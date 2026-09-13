# merger.py
"""
llm_extractor.py sends the chapter a few pages at a time (see
_chunk_pages) and gets back one JSON result per chunk, each shaped like:
    {"date": "...", "bha_list": [{"wellbore": ..., "bha_no": ..., "table": [...]}, ...]}

This file stitches those chunk results into one document-level result in
the same shape parser.parse_bha() returns, so main.py can treat either
extraction path identically:
    {"date": "...", "wellbores": [{"wellbore": ..., "bha_list": [...]}]}

The one real merge problem: a BHA's component table can be split across a
chunk boundary (the table just kept going onto the next page, which landed
in the next chunk). When that happens, the LLM sees the continuation rows
with no repeated "BHA NO:" line above them and has to guess -- it may
either (a) attach them to a same-bha_no entry again, or (b) drop the
metadata fields and return an entry with the same bha_no/wellbore but only
a table. Either way, the fix is the same: if the first entry of a chunk
has the same (wellbore, bha_no) as the last entry of the previous chunk,
treat it as a continuation and concatenate the tables rather than creating
a duplicate BHA run.
"""


def _is_continuation(prev, curr):
    return (
        prev is not None
        and prev.get("bha_no") == curr.get("bha_no")
        and prev.get("wellbore") == curr.get("wellbore")
    )


def _flatten(chunk_results):
    flat = []
    date = None
    for chunk in chunk_results:
        if not date and chunk.get("date"):
            date = chunk["date"]
        for entry in chunk.get("bha_list", []):
            if flat and _is_continuation(flat[-1], entry):
                flat[-1]["table"].extend(entry.get("table", []))
                # Fill in anything the first fragment was missing.
                for field in ("kind", "description", "name"):
                    if not flat[-1].get(field) and entry.get(field):
                        flat[-1][field] = entry[field]
            else:
                flat.append(dict(entry, table=list(entry.get("table", []))))
    return date, flat


def merge_llm_chunks(chunk_results, fallback=None):
    """
    chunk_results: list of per-chunk dicts from llm_extractor.extract_bha_with_llm.
    fallback: the deterministic parser's result, used as-is if the LLM
    returned nothing usable (empty chunks, or every chunk failed).
    """
    date, flat_entries = _flatten(chunk_results)
    if not flat_entries:
        return fallback if fallback is not None else {"date": date, "wellbores": []}

    wellbore_order = []
    grouped = {}
    for entry in flat_entries:
        wb = entry.get("wellbore")
        if wb not in grouped:
            grouped[wb] = []
            wellbore_order.append(wb)
        entry = dict(entry)
        entry.pop("wellbore", None)
        grouped[wb].append(entry)

    if not date and fallback:
        date = fallback.get("date")

    return {
        "date": date,
        "wellbores": [{"wellbore": wb, "bha_list": grouped[wb]} for wb in wellbore_order],
    }
