"""
db.py — persistence + dedup for the BHA extraction pipeline.

Why SQLite:
- Zero setup (single file, ships with Python) -- "best/optimal/easy" for a
  mini-project. No server to run, easy to inspect (`sqlite3 bha.db`), and
  still gives us real relational queries + a UNIQUE constraint for dedup.
  Swapping to Postgres later is a small change (same SQL, different driver)
  because nothing here uses SQLite-only syntax beyond AUTOINCREMENT.

Why this schema shape:
- We don't actually know all 10-12 table variants that will show up across
  every operator's report template. Rather than hardcoding columns per
  table type (which breaks the moment a new template adds/removes a
  column -- the exact bug the old regex parser had), every component row
  is stored as (component_name, properties_json) where properties_json is
  whatever key/value pairs were found for that row (od_in, id_in,
  torque_ft_lb, whatever). No migration needed when a new column shows up.
- Same idea one level up: a BHA run's own metadata (kind/description/name
  and any label we didn't recognize) is stored as extra_json so nothing
  is silently dropped.

Tables:
  documents      one row per uploaded PDF (dedup key = file_hash)
  wellbores      one row per WELLBORE found inside a document
  bha_runs       one row per BHA NO entry under a wellbore
  bha_components one row per string-component table row under a BHA run
"""
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    filename      TEXT NOT NULL,
    file_hash     TEXT NOT NULL UNIQUE,
    report_date   TEXT,
    chapter_start_page INTEGER,
    chapter_end_page   INTEGER,
    status        TEXT NOT NULL DEFAULT 'processing',  -- processing | done | failed
    stage         TEXT NOT NULL DEFAULT 'queued',       -- see STAGES below
    raw_json      TEXT,           -- full extracted JSON, for audit / re-export
    confidence    TEXT,           -- high | medium | low, see validate.py
    needs_review  INTEGER DEFAULT 0,
    warnings_json TEXT,           -- list of human-readable warning strings
    error         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wellbores (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id   INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    wellbore_name TEXT
);

CREATE TABLE IF NOT EXISTS bha_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    wellbore_id   INTEGER NOT NULL REFERENCES wellbores(id) ON DELETE CASCADE,
    bha_no        TEXT,
    kind          TEXT,
    description   TEXT,
    name          TEXT,
    extra_json    TEXT           -- any label we saw but didn't map to a known field
);

CREATE TABLE IF NOT EXISTS bha_components (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    bha_run_id    INTEGER NOT NULL REFERENCES bha_runs(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,   -- position within the table, top to bottom
    component     TEXT,
    properties_json TEXT            -- {"od_in": "...", "id_in": "...", ...} -- whatever columns this table had
);

CREATE INDEX IF NOT EXISTS idx_wellbores_document ON wellbores(document_id);
CREATE INDEX IF NOT EXISTS idx_bha_runs_wellbore ON bha_runs(wellbore_id);
CREATE INDEX IF NOT EXISTS idx_bha_components_run ON bha_components(bha_run_id);
"""

# Ordered pipeline stages -- the frontend's progress page (frontend/upload.js)
# hardcodes this same list to render a step tracker. Keep the two in sync if
# you add a stage. 'done' is the terminal success value; there is
# deliberately no terminal 'failed' stage value -- see mark_failed's
# docstring for why.
STAGES = [
    "queued",
    "reading_pdf",
    "locating_chapter",
    "parsing",
    "normalizing",   # only visited when USE_LLM_NORMALIZATION=true
    "validating",
    "saving",
    "done",
]


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn):
    """
    CREATE TABLE IF NOT EXISTS only helps for a brand-new database -- it
    does nothing to a documents table that already exists from an older
    version of this schema (e.g. before confidence/needs_review/
    warnings_json were added), which is exactly what produced:
        sqlite3.OperationalError: no such column: confidence

    This adds any column that SCHEMA declares but the existing table is
    missing, so an old bha.db from a previous run of this project upgrades
    in place instead of requiring "just delete the db file" forever.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    wanted = {
        "confidence": "TEXT",
        "needs_review": "INTEGER DEFAULT 0",
        "warnings_json": "TEXT",
        "stage": "TEXT NOT NULL DEFAULT 'queued'",
    }
    for col, decl in wanted.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {decl}")


def compute_file_hash(pdf_path, chunk_size=1024 * 1024):
    """Content hash, not filename -- so a re-uploaded copy with a different
    name is still recognized as the same document, and an edited/reissued
    PDF with the same name is correctly treated as new."""
    h = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def find_existing(file_hash):
    """Returns the existing documents row (as a dict) if this file was
    already processed, else None. Caller uses this for the
    'is it processed or not' check before doing any extraction work."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return dict(row) if row else None


def create_placeholder(filename, file_hash):
    """Inserts a 'queued' row and returns its id, before anything about the
    PDF (page count, chapter location) is known yet. This runs at upload
    time, not after extraction starts, specifically so the API can hand the
    caller a document_id immediately -- that id is what the live progress
    page (frontend/upload.js) polls via GET /api/documents/{id}/status
    while pipeline.process() moves it through STAGES."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO documents (filename, file_hash, status, stage, created_at, updated_at)
               VALUES (?, ?, 'processing', 'queued', ?, ?)""",
            (filename, file_hash, now, now),
        )
        return cur.lastrowid


def set_stage(document_id, stage):
    """Updates just the stage marker -- called at each step of
    pipeline.process() so a client polling /status sees granular progress
    instead of a single 'processing' blob for the whole run."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE documents SET stage = ?, updated_at = ? WHERE id = ?",
            (stage, now, document_id),
        )


def update_chapter_pages(document_id, start_page, end_page):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE documents SET chapter_start_page = ?, chapter_end_page = ?, updated_at = ? WHERE id = ?",
            (start_page, end_page, now, document_id),
        )


def mark_failed(document_id, error):
    """Marks the document failed WITHOUT touching `stage` -- stage keeps
    whatever value the last successful set_stage() call left it at, so a
    client can tell exactly which step it died on. Overwriting stage to a
    literal 'failed' here would erase that information; status='failed' is
    what signals failure, stage says where."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE documents SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
            (str(error), now, document_id),
        )


def requeue(document_id):
    """Resets a stuck/failed row (e.g. a previous run that never finished
    cleanly) back to 'queued' so it can be reprocessed under the same id,
    instead of leaving stale error/stage values visible while it reruns."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE documents SET status = 'processing', stage = 'queued', error = NULL, updated_at = ? WHERE id = ?",
            (now, document_id),
        )


def save_result(document_id, result, validation=None):
    """
    Writes the extracted structure for a document:
      - updates the documents row to 'done' + stores the raw JSON for audit
      - stores the confidence tier / warnings from validate.validate_result,
        if given, so a low-confidence row is visible with a plain SQL query
        instead of requiring someone to re-parse raw_json to notice it
      - fans wellbores -> bha_runs -> bha_components out into their tables

    `result` is the dict produced by parser.parse_bha (or the LLM-normalized
    equivalent): {"date": ..., "wellbores": [{"wellbore": ..., "bha_list": [...]}]}
    """
    now = datetime.now(timezone.utc).isoformat()
    validation = validation or {}
    with get_conn() as conn:
        conn.execute(
            """UPDATE documents
               SET status = 'done', stage = 'done', report_date = ?, raw_json = ?, updated_at = ?,
                   confidence = ?, needs_review = ?, warnings_json = ?, error = NULL
               WHERE id = ?""",
            (
                result.get("date"),
                json.dumps(result, ensure_ascii=False),
                now,
                validation.get("confidence"),
                1 if validation.get("needs_review") else 0,
                json.dumps(validation.get("warnings", []), ensure_ascii=False),
                document_id,
            ),
        )

        for wb in result.get("wellbores", []):
            wb_cur = conn.execute(
                "INSERT INTO wellbores (document_id, wellbore_name) VALUES (?, ?)",
                (document_id, wb.get("wellbore")),
            )
            wellbore_id = wb_cur.lastrowid

            for run in wb.get("bha_list", []):
                extra = run.get("attributes", {})
                run_cur = conn.execute(
                    """INSERT INTO bha_runs
                       (wellbore_id, bha_no, kind, description, name, extra_json)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        wellbore_id,
                        run.get("bha_no"),
                        run.get("kind"),
                        run.get("description"),
                        run.get("name"),
                        json.dumps(extra, ensure_ascii=False) if extra else None,
                    ),
                )
                bha_run_id = run_cur.lastrowid

                for seq, row in enumerate(run.get("table", [])):
                    row = dict(row)  # don't mutate caller's data
                    component = row.pop("string_component", None) or row.pop("component", None)
                    conn.execute(
                        """INSERT INTO bha_components (bha_run_id, seq, component, properties_json)
                           VALUES (?, ?, ?, ?)""",
                        (bha_run_id, seq, component, json.dumps(row, ensure_ascii=False)),
                    )

    return document_id
