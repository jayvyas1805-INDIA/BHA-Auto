import re

# Titles that count as "the BHA chapter", matched case-insensitively.
BHA_TITLE_PATTERNS = [
    r"\bbha\s*list\b",
    r"\bbottom\s*hole\s*assembl(y|ies)\b",
    r"\bb\.?h\.?a\.?\b",
]

TOC_HEADING_RE = re.compile(r"table\s+of\s+contents", re.IGNORECASE)

# A TOC line looks like: "7.6 BHA list.......................62" or
# "7.6   BHA list   62" (dot leaders are common but not guaranteed).
TOC_ENTRY_RE = re.compile(
    r"^\s*(?P<number>\d+(?:\.\d+)*)\s+(?P<title>.+?)\s*[.\s]{2,}\s*(?P<page>\d+)\s*$"
)
# Fallback for entries with no dot leader, just trailing whitespace + number.
TOC_ENTRY_RE_LOOSE = re.compile(
    r"^\s*(?P<number>\d+(?:\.\d+)*)\s+(?P<title>.+?)\s+(?P<page>\d+)\s*$"
)


def _is_bha_title(title):
    t = title.lower()
    return any(re.search(p, t) for p in BHA_TITLE_PATTERNS)


def _parse_toc_entries(text):
    entries = []
    for line in text.splitlines():
        m = TOC_ENTRY_RE.match(line) or TOC_ENTRY_RE_LOOSE.match(line)
        if m:
            entries.append({
                "number": m.group("number"),
                "title": m.group("title").strip(" ."),
                "page": int(m.group("page")),
            })
    return entries


def find_toc_pages(pages, max_scan=15):
    """Return indices of pages that look like they contain the TOC."""
    found = []
    for i, page in enumerate(pages[:max_scan]):
        text = page.get("text", "") or ""
        if TOC_HEADING_RE.search(text) or (found and len(found) < 3):
            found.append(i)
        elif found:
            break
    return found


def find_bha_chapter(pages, printed_to_pdf_offset=None):
    """
    Locates the BHA chapter using the document's own table of contents.

    Returns (start_pdf_index, end_pdf_index) — both 0-based, inclusive —
    or None if no TOC entry could be matched (caller should fall back to
    keyword scanning in that case).

    printed_to_pdf_offset: printed_page_number - pdf_index, if known.
    If not given, it's inferred from the printed page number found on the
    TOC page itself (most reports print "N of TOTAL" on every page).
    """
    toc_page_indices = find_toc_pages(pages)
    if not toc_page_indices:
        return None

    toc_text = "\n".join(pages[i].get("text", "") or "" for i in toc_page_indices)
    entries = _parse_toc_entries(toc_text)
    if not entries:
        return None

    bha_idx = next((i for i, e in enumerate(entries) if _is_bha_title(e["title"])), None)
    if bha_idx is None:
        return None

    bha_entry = entries[bha_idx]
    next_entry = next(
        (e for e in entries[bha_idx + 1:] if e["page"] > bha_entry["page"]),
        None,
    )

    # Work out printed-page -> pdf-index offset.
    if printed_to_pdf_offset is None:
        printed_to_pdf_offset = _infer_offset(pages, bha_entry["page"])

    start_pdf = bha_entry["page"] - printed_to_pdf_offset
    if next_entry:
        end_pdf = next_entry["page"] - printed_to_pdf_offset - 1
    else:
        end_pdf = start_pdf + 10  # safety cap if this is the last chapter

    start_pdf = max(0, start_pdf)
    end_pdf = min(len(pages) - 1, max(end_pdf, start_pdf))

    return start_pdf, end_pdf


PRINTED_PAGE_RE = re.compile(r"\b(\d+)\s+of\s+\d+\b", re.IGNORECASE)


def _infer_offset(pages, target_printed_page, sample_size=20):
    """
    Reads the 'N of TOTAL' footer/header printed on each page and figures
    out the constant offset between printed page numbers and 0-based pdf
    indices, by sampling early pages (offset is constant throughout).
    """
    for i, page in enumerate(pages[:sample_size]):
        text = page.get("text", "") or ""
        m = PRINTED_PAGE_RE.search(text)
        if m:
            printed = int(m.group(1))
            return printed - i
    return 0  # assume no offset if we can't find printed numbers
