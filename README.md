# BHA Extraction Pipeline

Extracts Bottom Hole Assembly (BHA) tables from 90–100 page oil & gas Final
Well Reports, normalizes them to JSON, stores them in a database, and serves
a web console to review the results.

---

## Rotate your Mistral API key first

An earlier version of this project had a live `MISTRAL_API_KEY` committed in
`.env`. Treat that key as exposed and rotate it. `.env` is now gitignored and
`.env.example` is the template to copy.

---

## Project structure

```
bha-extraction/
├── run.py                  CLI entrypoint
├── requirements.txt
├── .env.example            copy to .env (gitignored)
├── .gitignore
│
├── bha/                    the pipeline package
│   ├── config.py           all paths + settings, env-overridable
│   ├── pipeline.py         orchestrator: dedup → locate → parse → validate → store
│   ├── reader.py           PDF text/word/table extraction (pdfplumber)
│   ├── toc.py              finds the BHA chapter via the document's own TOC
│   ├── detector.py         keyword-scan fallback when there's no usable TOC
│   ├── parser.py           deterministic metadata + table extraction
│   ├── llm_extractor.py    optional Mistral label-normalization pass
│   ├── merger.py           stitches per-chunk LLM results, handles split tables
│   ├── validate.py         confidence scoring + warnings
│   ├── db.py               SQLite schema, dedup, persistence
│   └── writer.py           JSON output
│
├── api/
│   └── app.py              FastAPI: /api routes + serves the frontend
│
├── frontend/               no build step — plain HTML/CSS/JS
│   ├── index.html
│   ├── styles.css
│   └── app.js
│
├── tests/                  33 tests, runnable with stdlib unittest
├── upload/                 PDFs land here
└── output/                 JSON results
```

---

## How it works

1. **Dedup** — `db.compute_file_hash` hashes the PDF's bytes and checks
   SQLite. Already processed → returns the stored result and stops. Content-
   based, not filename-based, so a renamed copy is still recognized.
2. **Locate the chapter** — `toc.py` reads the document's own Table of
   Contents and fuzzy-matches the chapter title. This matters because the
   title differs per template: your two samples use *"7.6 BHA list"* and
   *"8.6 Bha list"*. A fixed regex would miss one; TOC matching gets both.
3. **Fallback** — `detector.py` scores pages by keyword/table signals if
   there's no machine-readable TOC.
4. **Parse** — `parser.py` pulls the metadata block above each table
   (`WELLBORE`, `BHA NO`, `RUN TYPE`/`BHA KIND`, `DESCRIPTION`,
   `RUN NAME`/`BHA NAME`) plus the component table under it. Handles both
   bordered tables and borderless ones (aligned by word x-position).
5. **Normalize (optional)** — `llm_extractor.py` sends the chapter to Mistral
   to reconcile label variants across templates. Retries transient 429/5xx
   only (a bad key fails fast); validates response shape; a failed chunk is
   isolated and falls back to the deterministic result instead of killing the
   document.
6. **Validate** — `validate.py` scores `high`/`medium`/`low` and lists
   specific warnings (empty tables, non-numeric values in numeric columns,
   duplicate BHA numbers, chapter found via fallback). This is what separates
   "trust this" from "a human should check this one".
7. **Store** — `db.py` writes to SQLite and drops JSON in `output/`.

---

## A real bug the validation layer caught

Worth calling out, because it's the whole reason `validate.py` exists.

Running the pipeline on `COMPLETION_REPORT_1.pdf` produced 13 BHA entries with
**zero table rows each** — technically successful, no exception, silently
wrong. Cause: `parser.py` had

```python
if is_label_line(stripped) is not None:   # bug
    break
```

`is_label_line` returns a plain bool, which is never `None`, so the condition
was always true and borderless-table extraction stopped right after the header
before reading a single row. It only surfaced on PDFs whose tables have no
per-row ruling lines — the other sample used bordered tables and never hit the
path.

Fixed (`if is_label_line(stripped):`) and covered by a regression test in
`tests/test_parser.py`.

---

## Why table columns are stored as JSON, not fixed DB columns

Both sample PDFs contain exactly **one** table shape (`String component / OD in
/ ID in / Length m / Acc length m`), repeated per BHA run — so "10-12 tables"
in a report means 10-12 BHA *entries*, not 10-12 different schemas.

Since a new operator's template can add or drop a column at any time,
`bha_components.properties_json` stores whatever columns were actually found
for that row. No migration when a new column appears.

**Untested:** a genuinely different table type (torque & drag, hydraulics).
If you have one, add a sample page and verify the column detection against it.

---

## Database (SQLite)

One file, no server, inspectable with `sqlite3 bha.db`. Plain SQL throughout,
so moving to Postgres is a driver swap.

```
documents       one row per PDF. file_hash UNIQUE = dedup key.
                status: processing | done | failed
                confidence / needs_review / warnings_json from validate.py
                raw_json = full extracted result, for audit
wellbores       one per WELLBORE in a document
bha_runs        one per BHA NO under a wellbore
bha_components  one per table row; properties_json holds its actual columns
```

---

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env          # add your key if using LLM normalization
```

**CLI:**
```bash
python run.py upload/REPORT.pdf
USE_LLM_NORMALIZATION=true python run.py upload/REPORT.pdf
```

**Web console + API:**
```bash
uvicorn api.app:app --reload
```
- Dashboard: http://127.0.0.1:8000/
- API docs: http://127.0.0.1:8000/docs

Upload a PDF from the sidebar; the row appears as `processing` and the page
polls until extraction finishes, then renders the tables and a stack diagram
of each BHA string.

**Tests:**
```bash
python -m unittest discover -s tests -t .    # no install needed
pytest tests/                                 # if you have pytest
```

---

## API

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/documents` | upload a PDF, processed in background |
| GET | `/api/documents` | list documents + status/confidence |
| GET | `/api/documents/{id}` | full result incl. extracted data + warnings |
| GET | `/api/documents/{id}/status` | lightweight polling |

The frontend is served same-origin by the same app, so CORS isn't involved for
the bundled dashboard. `BHA_CORS_ORIGINS` covers the separate-dev-server case.

---

## What's verified, and what isn't

**Verified against the real PDFs and a live browser:**
- TOC chapter detection on both full reports (94 and 99 pages) — found pages
  62–66 and 65–71 from each document's own TOC.
- Dedup: re-running a file is skipped; a renamed byte-identical copy is caught.
- Parsing + DB fan-out, including the borderless-table bug fix above.
- Confidence scoring against real results and in unit tests.
- LLM retry/failure-isolation/response-validation and the chunk-boundary
  merge — tested with a fake HTTP layer (no real network call).
- **The frontend, rendered in headless Chromium against a server exposing the
  same routes and response shapes as `api/app.py`, reading the real `bha.db`.**
  Confirmed: document list, wellbore tabs, 17 run cards, 75 table rows, 75
  diagram segments, tab switching, and document switching — with zero JS
  errors.
- 33 tests passing.

**Not verified — test these yourself:**
- **The actual Mistral API call.** `api.mistral.ai` is unreachable from the
  environment this was built in, so `USE_LLM_NORMALIZATION=true` has never
  round-tripped to a real model.
- **`api/app.py` under real uvicorn.** `fastapi`/`uvicorn` couldn't be
  installed there either. The routes were verified via a stdlib server
  mirroring the same shapes, and `app.py` is syntax-checked and reviewed —
  but it has never actually been started. Run it before trusting it.
- **Upload flow end-to-end.** The frontend's upload handler is wired against
  the documented response shapes but was not exercised against a live
  `POST /api/documents`.
- Only two report templates, from one operator, both text-based PDFs. Scanned
  or image-only PDFs, and documents with no TOC, are untested paths.

---

## Known limits before calling this production

- `BackgroundTasks` runs in-process: a restart mid-extraction loses the job and
  leaves the row stuck at `processing`. A real queue (Celery/RQ/arq) fixes this.
- SQLite is single-writer — don't run `uvicorn --workers > 1` against it.
- No authentication on any endpoint.
- No OCR path for scanned PDFs.
- No CI running the test suite.
