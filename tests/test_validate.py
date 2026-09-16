import unittest

from bha.validate import validate_result


def _good_result():
    return {
        "date": "2014-01-08",
        "wellbores": [{
            "wellbore": "A",
            "bha_list": [{
                "bha_no": "1", "kind": "x", "description": "y", "name": "1",
                "table": [{"string_component": "PLUG", "od_in": "5.720", "id_in": None,
                           "length_m": "1.95", "acc_length_m": "1.95"}],
            }],
        }],
    }


class TestValidate(unittest.TestCase):

    def test_clean_result_is_high_confidence(self):
        v = validate_result(_good_result(), "toc")
        self.assertEqual(v["confidence"], "high")
        self.assertFalse(v["needs_review"])
        self.assertEqual(v["warnings"], [])

    def test_none_matched_is_low_confidence(self):
        v = validate_result(_good_result(), "none_matched")
        self.assertEqual(v["confidence"], "low")
        self.assertTrue(v["needs_review"])
        self.assertTrue(any("could not be located" in w for w in v["warnings"]))

    def test_keyword_scan_is_medium_confidence(self):
        v = validate_result(_good_result(), "keyword_scan")
        self.assertEqual(v["confidence"], "medium")
        self.assertTrue(any("keyword fallback" in w for w in v["warnings"]))

    def test_no_wellbores_is_low_confidence(self):
        v = validate_result({"date": "d", "wellbores": []}, "toc")
        self.assertEqual(v["confidence"], "low")
        self.assertTrue(any("no wellbore" in w for w in v["warnings"]))

    def test_missing_date_flagged_but_not_fatal(self):
        result = _good_result()
        result["date"] = None
        v = validate_result(result, "toc")
        self.assertTrue(any("no report date" in w for w in v["warnings"]))
        self.assertEqual(v["confidence"], "high")

    def test_empty_table_flagged(self):
        result = _good_result()
        result["wellbores"][0]["bha_list"][0]["table"] = []
        v = validate_result(result, "toc")
        self.assertTrue(any("no component table rows" in w for w in v["warnings"]))

    def test_duplicate_bha_no_flagged(self):
        result = _good_result()
        result["wellbores"][0]["bha_list"].append(dict(result["wellbores"][0]["bha_list"][0]))
        v = validate_result(result, "toc")
        self.assertTrue(any("duplicate BHA NO" in w for w in v["warnings"]))

    def test_bad_numeric_cell_lowers_confidence(self):
        result = _good_result()
        result["wellbores"][0]["bha_list"][0]["table"][0]["od_in"] = "N/A garbled"
        v = validate_result(result, "toc")
        self.assertEqual(v["confidence"], "medium")
        self.assertTrue(any("don't parse as a number" in w for w in v["warnings"]))

    def test_comma_decimal_is_valid_numeric(self):
        result = _good_result()
        result["wellbores"][0]["bha_list"][0]["table"][0]["od_in"] = "5,720"
        v = validate_result(result, "toc")
        self.assertEqual(v["confidence"], "high")

    def test_llm_errors_flagged_as_medium(self):
        v = validate_result(_good_result(), "toc", llm_errors=[([1, 2, 3], Exception("boom"))])
        self.assertEqual(v["confidence"], "medium")
        self.assertTrue(any("LLM normalization failed" in w for w in v["warnings"]))


if __name__ == "__main__":
    unittest.main()
