import unittest

from bha.parser import (
    is_label_line,
    parse_label_line,
    extract_borderless_table,
    extract_bordered_table,
    parse_bha,
)


# Column x0 positions modeled on the real PDF's header word positions, so
# _find_header_anchors/_assign_bucket bucket words the same way they would
# for genuine report text (real columns are ~50-80pt apart, not a few pt).
COLUMN_X = {
    "component": 50,   # "String"/component name column starts here
    "od": 250,
    "id": 320,
    "length": 390,
    "acc_length": 460,
}


def _line(y, tokens):
    """Builds a (y, text, words) tuple like parser.py's internal `lines`
    structure. `tokens` is a list of (text, x0) pairs so column alignment
    matches real report spacing instead of guessed uniform spacing."""
    words = [{"text": t, "x0": x, "top": y} for t, x in tokens]
    text = " ".join(t for t, _ in tokens)
    return (y, text, words)


def _header_line(y):
    return _line(y, [
        ("String", COLUMN_X["component"]), ("component", COLUMN_X["component"] + 45),
        ("OD", COLUMN_X["od"]), ("in", COLUMN_X["od"] + 22),
        ("ID", COLUMN_X["id"]), ("in", COLUMN_X["id"] + 20),
        ("Length", COLUMN_X["length"]), ("m", COLUMN_X["length"] + 45),
        ("Acc", COLUMN_X["acc_length"]), ("length", COLUMN_X["acc_length"] + 25),
        ("m", COLUMN_X["acc_length"] + 65),
    ])


def _data_line(y, component_tokens, od=None, id_=None, length=None, acc_length=None):
    tokens = [(t, COLUMN_X["component"] + i * 55) for i, t in enumerate(component_tokens)]
    if od is not None:
        tokens.append((od, COLUMN_X["od"]))
    if id_ is not None:
        tokens.append((id_, COLUMN_X["id"]))
    if length is not None:
        tokens.append((length, COLUMN_X["length"]))
    if acc_length is not None:
        tokens.append((acc_length, COLUMN_X["acc_length"]))
    return _line(y, tokens)


class TestIsLabelLine(unittest.TestCase):
    def test_true_for_label_line(self):
        self.assertTrue(is_label_line("BHA NO: 1"))

    def test_false_for_non_label_line(self):
        self.assertFalse(is_label_line("NEAR BIT STAB 17.500 2.813 2.19 2.65"))

    def test_returns_actual_bool_not_none(self):
        # Regression guard for the exact bug: is_label_line must return a
        # real bool, not None, for a non-label line -- because callers
        # historically wrote `if is_label_line(x) is not None:` which is
        # ALWAYS True for a bool and silently broke table extraction.
        result = is_label_line("some plain text with no colon")
        self.assertIn(result, (True, False))
        self.assertIsNotNone(result)  # sanity: it's a bool, not None either way
        self.assertFalse(result)


class TestBorderlessTableExtraction(unittest.TestCase):
    def test_borderless_table_actually_collects_rows(self):
        """Regression test for the shipped bug: extract_borderless_table's
        stop condition (`if is_label_line(stripped) is not None: break`)
        was always true for a bool, so it broke immediately after the
        header line and returned zero rows for every borderless (no
        ruling-lines-per-row) table -- exactly the shape seen in
        COMPLETION_REPORT_1.pdf's BHA chapter."""
        header = _header_line(100)
        row1 = _data_line(112, ["NA~NA"], od="17.500", length="0.46", acc_length="0.46")
        row2 = _data_line(124, ["NEAR", "BIT", "STAB"], od="17.500", id_="2.813", length="2.19", acc_length="2.65")
        next_label = _line(136, [("BHA", 50), ("NO:", 85), ("2", 115)])
        lines = [header, row1, row2, next_label]

        rows, next_i = extract_borderless_table(lines, 0)

        self.assertEqual(len(rows), 2, "should collect both data rows, not stop at zero")
        self.assertEqual(rows[0]["string_component"], "NA~NA")
        self.assertEqual(rows[0]["od_in"], "17.500")
        self.assertIsNone(rows[0]["id_in"])  # genuinely blank cell, not a shifted column
        self.assertEqual(rows[1]["string_component"], "NEAR BIT STAB")
        self.assertEqual(next_i, 3)  # stopped at the next label line, not consumed it

    def test_stops_at_next_label_line(self):
        header = _header_line(100)
        row1 = _data_line(112, ["PLUG"], od="5.720", length="1.95", acc_length="1.95")
        next_label = _line(124, [("BHA", 50), ("NO:", 85), ("3", 115)])
        lines = [header, row1, next_label]

        rows, next_i = extract_borderless_table(lines, 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(next_i, 2)  # index of the label line, unconsumed


class TestParseBhaIntegration(unittest.TestCase):
    def test_full_page_with_borderless_table_extracts_components(self):
        """End-to-end through parse_bha with a page shaped like
        COMPLETION_REPORT_1.pdf: metadata lines + a borderless table with
        no ruling lines under the header (no pdfplumber 'tables' at all)."""
        meta_lines = [
            _line(0, [("WELLBORE:", 50), ("NO", 110), ("15/9-F-15", 140), ("A", 200)]),
            _line(10, [("BHA", 50), ("NO:", 85), ("1", 115)]),
            _line(20, [("BHA", 50), ("KIND:", 85)]),
            _line(30, [("DESCRIPTION:", 50), ("test", 150)]),
            _line(40, [("BHA", 50), ("NAME:", 85), ("3", 130)]),
            _header_line(50),
            _data_line(60, ["NA~NA"], od="17.500", length="0.46", acc_length="0.46"),
            _data_line(70, ["NEAR", "BIT", "STAB"], od="17.500", id_="2.813", length="2.19", acc_length="2.65"),
        ]
        words = [w for _, _, ws in meta_lines for w in ws]
        page = {"page": 1, "text": "", "tables": [], "words": words}

        result = parse_bha([page])
        wb = result["wellbores"][0]
        run = wb["bha_list"][0]
        self.assertEqual(len(run["table"]), 2)
        self.assertEqual(run["table"][0]["string_component"], "NA~NA")


if __name__ == "__main__":
    unittest.main()
