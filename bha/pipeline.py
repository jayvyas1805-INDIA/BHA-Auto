import os
import json

from .reader import read_pdf
from .toc import find_bha_chapter
from .detector import detect_section
from .parser import parse_bha
from .writer import save_json
from .validate import validate_result
from . import db
from .config import OUTPUT_DIR, USE_LLM_NORMALIZATION, ensure_dirs


def get_chapter_pages(pages):
    """
    Locates the BHA chapter two ways, in order of trust:
      1. toc.py — reads the document's own Table of Contents and computes
         the exact page range from the printed page numbers. Works even
         though the chapter title text ('BHA list', 'Bottom Hole Assembly',
         'BHA', etc.) differs between templates, because it fuzzy-matches
         the title rather than assuming one exact string.
      2. detector.py — keyword/table scoring fallback for documents with no
         machine-readable TOC (or a TOC that doesn't list this chapter).

    Returns (chapter_pages, method, (start, end)) where method is "toc" or
    "keyword_scan" for logging/debugging -- if TOC detection is ever wrong,
    method tells you which path was taken without re-running anything.
    """
    toc_range = find_bha_chapter(pages)
    if toc_range:
        start, end = toc_range
        return pages[start:end + 1], "toc", (start, end)

    scanned = detect_section(pages)
    if scanned:
        page_numbers = [p["page"] for p in scanned]
        return scanned, "keyword_scan", (min(page_numbers) - 1, max(page_numbers) - 1)

    # Last resort: nothing matched, hand back everything rather than fail
    # silently. This should be rare and worth noticing in logs.
    return pages, "none_matched", (0, len(pages) - 1)


def process(document_id, pdf_path, json_output=None):
    """
    Runs extraction for a document row that already exists (created via
    db.create_placeholder, typically right at upload time so the caller has
    an id to poll immediately). Moves the row through db.STAGES as it goes:

        queued -> reading_pdf -> locating_chapter -> parsing
               -> [normalizing, only if USE_LLM_NORMALIZATION] -> validating
               -> saving -> done   (or -> failed, with `error` set)

    This is what api/app.py's background task calls, and what the live
    progress page (frontend/upload.js) is polling GET /status to watch move
    through. run() below wraps this for the CLI, where there's no separate
    "give me an id first" step needed.
    """
    filename = os.path.basename(pdf_path)
    json_output = json_output or os.path.join(OUTPUT_DIR, os.path.splitext(filename)[0] + ".json")
    llm_errors = None

    try:
        db.set_stage(document_id, "reading_pdf")
        print("reading pdf..")
        pages = read_pdf(pdf_path)

        db.set_stage(document_id, "locating_chapter")
        print("locating BHA chapter..")
        chapter_pages, method, (start, end) = get_chapter_pages(pages)
        print(f"  -> found via {method}, pages {start + 1}-{end + 1} of {len(pages)}")
        db.update_chapter_pages(document_id, start + 1, end + 1)

        db.set_stage(document_id, "parsing")
        print("parsing chapter..")
        data = parse_bha(chapter_pages)

        if USE_LLM_NORMALIZATION:
            db.set_stage(document_id, "normalizing")
            print("normalizing with LLM..")
            from .llm_extractor import extract_bha_with_llm
            from .merger import merge_llm_chunks
            chunk_results, llm_errors = extract_bha_with_llm(chapter_pages)
            if llm_errors:
                for pages_failed, err in llm_errors:
                    print(f"  warning: LLM call failed for pages {pages_failed} after retries: {err}")
            data = merge_llm_chunks(chunk_results, fallback=data)

        db.set_stage(document_id, "validating")
        validation = validate_result(data, method, llm_errors=llm_errors)
        if validation["warnings"]:
            print("validation warnings:")
            for w in validation["warnings"]:
                print(f"  - {w}")
        print(f"confidence: {validation['confidence']}"
              + ("  (FLAGGED FOR REVIEW)" if validation["needs_review"] else ""))

        db.set_stage(document_id, "saving")
        print("saving output..")
        save_json(data, json_output)
        db.save_result(document_id, data, validation=validation)  # also sets stage='done'

    except Exception as e:
        db.mark_failed(document_id, e)  # also sets stage='failed'
        raise

    print(f"done \u2714  (document id={document_id})")
    return data


def run(pdf_path, json_output=None):
    """CLI convenience wrapper: handles the dedup check and placeholder-row
    creation that api/app.py does itself (so it can hand back an id at
    upload time), then delegates to process()."""
    ensure_dirs()
    db.init_db()

    print("checking if this file was already processed..")
    file_hash = db.compute_file_hash(pdf_path)
    existing = db.find_existing(file_hash)
    if existing and existing["status"] == "done":
        print(f"already processed (document id={existing['id']}, processed {existing['updated_at']}). skipping.")
        return json.loads(existing["raw_json"])
    if existing and existing["status"] == "processing":
        print("warning: a previous run on this file didn't finish cleanly (status=processing). re-running.")

    filename = os.path.basename(pdf_path)
    if existing:
        document_id = existing["id"]
        db.requeue(document_id)
    else:
        document_id = db.create_placeholder(filename, file_hash)
    return process(document_id, pdf_path, json_output)
