import re

# -----------------------------
# LABEL HANDLING
# -----------------------------
# Different report templates use different labels for the same concept.
# Map every label variant seen so far (lowercased) to a canonical field name.
# This is just a convenience mapping -- labels NOT in here are no longer
# dropped, they get captured verbatim into current_bha["attributes"]
# (see parse_label_line / main loop below). So a brand new template that
# prints e.g. "MUD TYPE:" or "RIG:" above its tables still has that data
# preserved even though we've never seen that label before.
LABEL_SYNONYMS = {
    "wellbore": "wellbore",
    "bha no": "bha_no",
    "bha kind": "kind",
    "run type": "kind",
    "description": "description",
    "bha name": "name",
    "run name": "name",
}

# Boilerplate labels that show up in report headers/footers on every page
# (not BHA attributes) -- these must NOT be captured as generic attributes,
# or a header repeated on a later page would get attached to whatever BHA
# happens to still be open (e.g. a table continuing across a page break).
EXCLUDED_LABELS = {
    "license", "licence", "licence no", "license no", "well",
    "doc no", "doc. no", "valid from", "rev no", "rev. no",
    "classification", "status", "expiry date", "page",
}

LABEL_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 /]{1,25}?)\s*:\s*(.*)$")

DATE_PATTERNS = [
    r"\b(\d{4}-\d{1,2}-\d{1,2})\b",          # 2014-01-08
    r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{4})\b",  # 12.08.2009 / 12/08/2009
]

# Keep the header's own wording, "string_component", not a generic "component".
COLUMN_MAP = [
    (("acc", "length"), "acc_length_m"),   # check before plain "length"
    (("string", "component"), "string_component"),
    (("od",), "od_in"),
    (("id",), "id_in"),
    (("length",), "length_m"),
]


def _canonical_column(header_text):
    h = header_text.lower()
    for keywords, name in COLUMN_MAP:
        if all(k in h for k in keywords):
            return name
    return None


def clean_line(line: str) -> str:
    return line.strip()


def find_date(full_text):
    for pattern in DATE_PATTERNS:
        m = re.search(pattern, full_text)
        if m:
            return m.group(1)
    return None


def parse_label_line(stripped):
    """
    Returns (canonical_or_None, raw_label, attr_key, value) for any
    "Label: value" line, or None if the line isn't of that shape or is
    known page boilerplate. `canonical` is the mapped field name
    (wellbore/bha_no/kind/description/name) when the label is a known
    synonym, otherwise None -- callers should still keep the data by
    storing it under `attr_key` rather than discarding it.
    """
    m = LABEL_LINE_RE.match(stripped)
    if not m:
        return None

    raw_label = m.group(1).strip()
    value = m.group(2).strip()
    norm = raw_label.lower()

    if norm in EXCLUDED_LABELS:
        return None

    canonical = LABEL_SYNONYMS.get(norm)
    attr_key = norm.replace(" ", "_").replace(".", "")
    return canonical, raw_label, attr_key, value


def is_label_line(stripped):
    """True/False-style check used by the table-boundary detector to know
    when a new metadata block starts (so borderless table extraction stops
    consuming rows). Returns a bool -- callers must check truthiness
    (`if is_label_line(x):`), NOT `is not None` (a bool is never None, so
    that comparison is always True and silently breaks borderless
    extraction after zero rows -- this exact bug shipped once already,
    see tests/test_parser.py::test_borderless_table_actually_collects_rows)."""
    return parse_label_line(stripped) is not None


# -----------------------------
# BORDERED-TABLE EXTRACTION (deterministic, position-safe)
# -----------------------------
def extract_bordered_table(rows):
    """
    pdfplumber pads every cell of a bordered table into a fixed number of
    sub-slots per column. The real value always lands in the same relative
    slot for every row -- a genuinely blank cell comes through as '' in
    that slot, not as a missing slot. Filtering out only the `None`
    structural padding (never the '' values) and zipping the remainder
    against the header preserves exact column alignment for every row,
    even when a column is blank. This is what prevents values from
    shifting into the wrong column, and stops two equal-looking values in
    adjacent columns from being mistaken for a duplicate + a blank.
    """
    header_idx = None
    header_names = None

    for i, row in enumerate(rows):
        texts = [c.strip() for c in row if c and c.strip()]
        joined = " ".join(texts).lower()
        if "component" in joined and ("od" in joined or "id" in joined or "length" in joined):
            names = [_canonical_column(t) or t.lower().replace(" ", "_") for t in texts]
            if "string_component" in names:
                header_idx = i
                header_names = names
                break

    if header_idx is None:
        return []

    out_rows = []
    for row in rows[header_idx + 1:]:
        values = [c for c in row if c is not None]
        if not values or all(not v.strip() for v in values):
            continue
        if len(values) != len(header_names):
            continue  # different shape than this table's header; skip rather than guess
        entry = {}
        for name, val in zip(header_names, values):
            val = val.strip()
            # Keep the decimal separator exactly as printed (comma stays comma).
            entry[name] = val if val else None
        out_rows.append(entry)

    return out_rows


# -----------------------------
# BORDERLESS (TEXT-POSITION) TABLE EXTRACTION -- same guarantee, no ruling lines
# -----------------------------
def _find_header_anchors(line_words):
    anchors = []
    comp_x0 = None
    texts = [w["text"] for w in line_words]
    lower = [t.lower() for t in texts]

    for i, t in enumerate(lower):
        if t == "string" and comp_x0 is None:
            comp_x0 = line_words[i]["x0"]
        elif t == "od":
            anchors.append((line_words[i]["x0"], "od_in"))
        elif t == "id":
            anchors.append((line_words[i]["x0"], "id_in"))
        elif t == "acc":
            anchors.append((line_words[i]["x0"], "acc_length_m"))
        elif t == "length" and (i == 0 or lower[i - 1] != "acc"):
            anchors.append((line_words[i]["x0"], "length_m"))

    if comp_x0 is None and line_words:
        comp_x0 = line_words[0]["x0"]

    anchors.append((comp_x0, "string_component"))
    anchors.sort(key=lambda a: a[0])
    return anchors


def _assign_bucket(x0, anchors):
    bucket = anchors[0][1]
    for ax, name in anchors:
        if x0 >= ax - 1:
            bucket = name
        else:
            break
    return bucket


def extract_borderless_table(lines, start_idx):
    header_words = lines[start_idx][2]
    anchors = _find_header_anchors(header_words)

    rows = []
    i = start_idx + 1
    while i < len(lines):
        _, text, words = lines[i]
        stripped = clean_line(text)

        if not stripped:
            i += 1
            continue

        if is_label_line(stripped):
            break

        buckets = {"string_component": [], "od_in": [], "id_in": [], "length_m": [], "acc_length_m": []}
        for w in words:
            buckets[_assign_bucket(w["x0"], anchors)].append(w["text"])

        component = " ".join(buckets["string_component"]).strip()
        if not component:
            i += 1
            continue

        row = {"string_component": component}
        for col in ("od_in", "id_in", "length_m", "acc_length_m"):
            val = " ".join(buckets[col]).strip()
            row[col] = val if val else None
        rows.append(row)
        i += 1

    return rows, i


# -----------------------------
# MAIN PARSER
# -----------------------------
def parse_bha(pages):
    """
    Returns:
    {
      "date": "...",
      "wellbores": [
        {
          "wellbore": "NO 15/9-F-15 C",
          "bha_list": [
            {"bha_no": "1", "kind": "...", "description": "...", "name": "...", "table": [...]},
            ...
          ]
        },
        ...
      ]
    }
    wellbore is stored once per group, not repeated on every BHA entry.
    """
    wellbore_groups = []   # [{"wellbore": ..., "bha_list": [...]}]
    current_group = None
    current_bha = None
    full_text_for_date = []

    def get_group(wellbore_name):
        nonlocal current_group
        if current_group is not None and current_group["wellbore"] == wellbore_name:
            return current_group
        for g in wellbore_groups:
            if g["wellbore"] == wellbore_name:
                current_group = g
                return g
        g = {"wellbore": wellbore_name, "bha_list": []}
        wellbore_groups.append(g)
        current_group = g
        return g

    def push_current_bha():
        nonlocal current_bha
        if current_bha and current_bha.get("bha_no"):
            owner_group = current_bha.pop("_group")
            owner_group["bha_list"].append(current_bha)
        current_bha = None

    wellbore_name = None

    for page in pages:
        full_text_for_date.append(page.get("text", ""))

        bordered = []
        for t in page.get("tables", []):
            rows = extract_bordered_table(t["rows"])
            if rows:
                bordered.append({"top": t["bbox"][1], "bottom": t["bbox"][3], "rows": rows})
        bordered.sort(key=lambda b: b["top"])
        table_used = [False] * len(bordered)

        grouped = {}
        for w in page.get("words", []):
            y = round(w["top"], 1)
            grouped.setdefault(y, []).append(w)
        lines = []
        for y in sorted(grouped):
            ws = sorted(grouped[y], key=lambda w: w["x0"])
            lines.append((y, " ".join(w["text"] for w in ws), ws))

        i = 0
        while i < len(lines):
            y, raw_line, words = lines[i]

            in_table = None
            for idx, bt in enumerate(bordered):
                if bt["top"] - 2 <= y <= bt["bottom"] + 2:
                    in_table = idx
                    break

            if in_table is not None:
                if not table_used[in_table] and current_bha is not None:
                    current_bha["table"].extend(bordered[in_table]["rows"])
                    table_used[in_table] = True
                i += 1
                continue

            line = clean_line(raw_line)
            if not line:
                i += 1
                continue

            label = parse_label_line(line)
            if label is not None:
                canonical, raw_label, attr_key, value = label

                if canonical == "wellbore":
                    wellbore_name = value
                    get_group(wellbore_name)
                    i += 1
                    continue

                if canonical == "bha_no":
                    m = re.search(r"\d+", value)
                    if m:
                        push_current_bha()
                        group = get_group(wellbore_name)
                        current_bha = {
                            "bha_no": m.group(0),
                            "kind": "",
                            "description": "",
                            "name": "",
                            "attributes": {},
                            "table": [],
                            "_group": group,
                        }
                    i += 1
                    continue

                if current_bha is not None:
                    if canonical in ("kind", "description", "name"):
                        current_bha[canonical] = value
                    # Always also keep the raw label -> value, even for known
                    # ones. This means a label we haven't mapped yet (e.g.
                    # "MUD TYPE:", "RIG:") is still preserved instead of
                    # being silently dropped, so future templates don't lose
                    # data just because we haven't hardcoded that label.
                    current_bha["attributes"][attr_key] = value
                    i += 1
                    continue

            if line.lower().startswith("string component"):
                rows, i = extract_borderless_table(lines, i)
                if current_bha is not None and rows:
                    current_bha["table"].extend(rows)
                continue

            i += 1

        for idx, bt in enumerate(bordered):
            if not table_used[idx] and current_bha is not None:
                current_bha["table"].extend(bt["rows"])
                table_used[idx] = True

    push_current_bha()

    date = find_date("\n".join(full_text_for_date))

    return {
        "date": date,
        "wellbores": wellbore_groups,
    }