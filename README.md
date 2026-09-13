# BHA Extraction Pipeline

## First: rotate your Mistral API key
`.env` in this project has a live `MISTRAL_API_KEY` in it. That file was
inside the zip you uploaded to this chat, so treat the key as exposed and
rotate it in your Mistral dashboard before using this in production.

## What it does, end to end
1. **`db.compute_file_hash` + `db.find_existing`** — hashes the uploaded
   PDF's bytes and checks SQLite for that hash. Already processed →
   returns the stored result and stops. This is the "is it processed or
   not" check, and it's content-based, not filename-based, so a renamed
   copy of the same PDF is still recognized.
2. **`toc.py`** — reads the document's own Table of Contents, fuzzy-matches
   the chapter title ("BHA list" / "Bottom Hole Assembly" / "BHA" / etc —
   whatever this specific report calls it), and computes the exact page
   range from the printed page numbers. This is why we don't need a fixed
   regex for the chapter name.
3. **`detector.py`** — fallback if there's no machine-readable TOC, or the
   TOC doesn't list this chapter. Scores every page by keyword/table
   signals and grabs the contiguous run of pages that scores high.
4. **`reader.py`** — extracts text, word positions, and tables only for
   that page range (not the other ~90 pages).
5. **`parser.py`** — deterministic extraction: metadata lines above each
   table (`WELLBORE:`, `BHA NO:`, `RUN TYPE`/`BHA KIND`, `DESCRIPTION:`,
   `RUN NAME`/`BHA NAME`, and anything else in that pattern) plus the
   component table under each one.
6. **`llm_extractor.py` + `merger.py`** (optional, `USE_LLM_NORMALIZATION=true`)
   — sends the chapter to Mistral to normalize label variants across
   templates, and stitches per-chunk results back together, including
   tables that got split across a chunk boundary.
7. **`db.save_result`** — writes the result into SQLite (see schema below)
   and also drops a JSON file in `output/` for manual inspection.

## Why every table's columns are stored as JSON, not fixed DB columns
You said there are 10-12 table variants in this chapter across different
report templates, and you don't know all their columns. Both sample PDFs
in this project only actually contain **one** table shape (`String
component / OD in / ID in / Length m / Acc length m`, repeated once per
BHA run — that's the "10-12 tables" in your samples: 10-12 BHA *entries*,
each with an instance of the same table, not 10-12 different schemas).

Because a new template can add or drop a column at any time, `bha_components.properties_json`
stores whatever columns were actually found for that row (`{"od_in": ...,
"id_in": ..., "torque_ft_lb": ...}` — anything), instead of fixed DB
columns. If you do have a report with a genuinely different table (e.g. a
torque & drag table, a hydraulics table) send me a sample page and I'll
verify the column-detection logic against it — right now it's only been
tested against the string-component shape.

## Database (SQLite)
Chosen for a mini-project: no server, one file (`bha.db`), inspectable with
`sqlite3 bha.db`, and a straightforward move to Postgres later since the
schema uses plain SQL.

```
documents       — one row per uploaded PDF. file_hash UNIQUE = dedup key.
                  status: processing | done | failed. raw_json = full
                  extracted JSON for audit/re-export.
wellbores       — one row per WELLBORE found in a document
bha_runs        — one row per BHA NO entry under a wellbore
bha_components  — one row per table row under a BHA run.
                  properties_json holds that table's actual columns.
```

## Run it
```bash
pip install -r requirement.txt
export MISTRAL_API_KEY=your_new_key      # only needed if USE_LLM_NORMALIZATION=true
python main.py path/to/report.pdf                      # writes output/<name>.json + bha.db
USE_LLM_NORMALIZATION=true python main.py path/to/report.pdf
```

## Verified in this sandbox
- TOC-based chapter detection: tested against both full sample PDFs
  (94 and 99 pages) — correctly located pages 62-66 and 65-71 purely from
  each document's own TOC, matching the page ranges in your sample
  filenames.
- Dedup: re-running the same file is detected and skipped; a byte-identical
  re-upload under a different name is still recognized via content hash.
- Parsing + DB fan-out (wellbores -> bha_runs -> bha_components): verified
  against both samples, 128 component rows stored correctly for the
  workover report alone.
- **Not verified**: the LLM normalization call itself — `api.mistral.ai`
  isn't reachable from this sandbox's network egress allowlist. Test that
  path with your own key in your own environment, and rotate the leaked
  key first regardless.
