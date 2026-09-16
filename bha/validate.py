"""
validate.py — sanity-checks an extraction result before it's trusted.

The pipeline can succeed "technically" (no exception thrown) while still
being wrong: chapter detection fell back to keyword scanning and grabbed
the wrong pages, a table's numeric columns didn't actually parse as
numbers, a BHA entry has metadata but an empty table, etc. None of those
raise an error on their own -- they just produce quietly bad data. This
module turns those conditions into an explicit list of warnings and an
overall confidence tier, so a caller (main.py, or a human reviewing the
DB) can tell "trust this" from "check this by hand" without re-reading
the source PDF.
"""

import re

NUMERIC_RE = re.compile(r"^-?\d+([.,]\d+)?$")


def _is_numeric_like(value):
    if value is None:
        return True  # missing is fine, a garbled value is not
    return bool(NUMERIC_RE.match(str(value).strip()))


def validate_result(result, chapter_detection_method, llm_errors=None):
    """
    result: the {"date":..., "wellbores": [...]} dict from parser.parse_bha
            or merger.merge_llm_chunks.
    chapter_detection_method: "toc" | "keyword_scan" | "none_matched"
    llm_errors: list of (page_numbers, error) from llm_extractor, if the
                LLM normalization path was used.

    Returns {"confidence": "high"|"medium"|"low", "warnings": [str, ...],
             "needs_review": bool}
    """
    warnings = []

    if chapter_detection_method == "none_matched":
        warnings.append("chapter could not be located via TOC or keyword scan -- "
                         "the whole document was scanned, which likely includes irrelevant pages")
    elif chapter_detection_method == "keyword_scan":
        warnings.append("chapter located via keyword fallback, not the document's own TOC -- "
                         "page range is a best guess")

    if llm_errors:
        pages = [p for pages, _ in llm_errors for p in pages]
        warnings.append(f"LLM normalization failed for pages {pages} after retries -- "
                         f"those pages fell back to deterministic parsing only")

    if not result.get("date"):
        warnings.append("no report date could be found on the chapter pages")

    wellbores = result.get("wellbores", [])
    if not wellbores:
        warnings.append("no wellbore/BHA data extracted at all")

    total_runs = 0
    empty_table_runs = 0
    bad_numeric_cells = 0
    duplicate_bha_no = 0

    for wb in wellbores:
        seen_bha_no = set()
        for run in wb.get("bha_list", []):
            total_runs += 1
            bha_no = run.get("bha_no")
            if bha_no in seen_bha_no:
                duplicate_bha_no += 1
            seen_bha_no.add(bha_no)

            table = run.get("table", [])
            if not table:
                empty_table_runs += 1
                continue

            for row in table:
                for key, val in row.items():
                    if key in ("component", "string_component"):
                        continue
                    if not _is_numeric_like(val):
                        bad_numeric_cells += 1

    if total_runs and empty_table_runs:
        warnings.append(f"{empty_table_runs}/{total_runs} BHA entries have no component table rows")
    if duplicate_bha_no:
        warnings.append(f"{duplicate_bha_no} duplicate BHA NO values within the same wellbore "
                         f"(possible table split across a page/chunk boundary that wasn't merged)")
    if bad_numeric_cells:
        warnings.append(f"{bad_numeric_cells} table cells that should be numeric don't parse as a number "
                         f"(possible column misalignment)")

    # Confidence tiering: keep this simple and legible rather than a
    # weighted score -- anyone reading this function should be able to
    # predict the tier from the warnings without running it.
    if chapter_detection_method == "none_matched" or not wellbores:
        confidence = "low"
    elif chapter_detection_method == "keyword_scan" or bad_numeric_cells or (llm_errors and len(llm_errors) > 0):
        confidence = "medium"
    else:
        confidence = "high"

    return {
        "confidence": confidence,
        "warnings": warnings,
        "needs_review": confidence != "high",
    }
