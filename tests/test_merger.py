import unittest

from bha.merger import merge_llm_chunks


class TestMerger(unittest.TestCase):

    def test_single_chunk_groups_by_wellbore(self):
        chunks = [{
            "date": "2014-01-08",
            "bha_list": [
                {"wellbore": "A", "bha_no": "1", "kind": "k1", "table": []},
                {"wellbore": "A", "bha_no": "2", "kind": "k2", "table": []},
                {"wellbore": "B", "bha_no": "1", "kind": "k3", "table": []},
            ],
        }]
        result = merge_llm_chunks(chunks)
        self.assertEqual(result["date"], "2014-01-08")
        self.assertEqual([w["wellbore"] for w in result["wellbores"]], ["A", "B"])
        self.assertEqual(len(result["wellbores"][0]["bha_list"]), 2)
        self.assertEqual(len(result["wellbores"][1]["bha_list"]), 1)

    def test_table_split_across_chunk_boundary_is_stitched(self):
        """The classic failure mode: a table runs off the end of one chunk
        and the LLM sees the continuation rows on the next chunk with no
        repeated 'BHA NO:' line, so it reports the same (wellbore, bha_no)
        again with just more table rows. Those two fragments must merge
        into ONE bha entry with a concatenated table, not become two
        duplicate entries."""
        chunks = [
            {"date": "d", "bha_list": [
                {"wellbore": "A", "bha_no": "5", "kind": "drilling", "description": "desc",
                 "table": [{"component": "BIT", "od_in": "8.5"}]},
            ]},
            {"date": None, "bha_list": [
                {"wellbore": "A", "bha_no": "5", "kind": None, "description": None,
                 "table": [{"component": "STABILIZER", "od_in": "8.0"}]},
            ]},
        ]
        result = merge_llm_chunks(chunks)
        wb = result["wellbores"][0]
        self.assertEqual(len(wb["bha_list"]), 1)  # not 2 -- continuation merged, not duplicated
        run = wb["bha_list"][0]
        self.assertEqual(len(run["table"]), 2)
        self.assertEqual(run["kind"], "drilling")   # kept from the first fragment
        self.assertEqual(run["description"], "desc")

    def test_different_bha_no_same_wellbore_not_merged(self):
        chunks = [
            {"date": "d", "bha_list": [{"wellbore": "A", "bha_no": "1", "table": [{"component": "X"}]}]},
            {"date": None, "bha_list": [{"wellbore": "A", "bha_no": "2", "table": [{"component": "Y"}]}]},
        ]
        result = merge_llm_chunks(chunks)
        self.assertEqual(len(result["wellbores"][0]["bha_list"]), 2)

    def test_same_bha_no_different_wellbore_not_merged(self):
        """bha_no resets per wellbore, so bha_no=1 under wellbore B right
        after bha_no=1 under wellbore A is a coincidence, not a continuation."""
        chunks = [
            {"date": "d", "bha_list": [{"wellbore": "A", "bha_no": "1", "table": [{"component": "X"}]}]},
            {"date": None, "bha_list": [{"wellbore": "B", "bha_no": "1", "table": [{"component": "Y"}]}]},
        ]
        result = merge_llm_chunks(chunks)
        names = [w["wellbore"] for w in result["wellbores"]]
        self.assertEqual(names, ["A", "B"])
        self.assertEqual(len(result["wellbores"][0]["bha_list"]), 1)
        self.assertEqual(len(result["wellbores"][1]["bha_list"]), 1)

    def test_empty_chunks_falls_back(self):
        fallback = {"date": "fallback-date", "wellbores": [{"wellbore": "X", "bha_list": []}]}
        result = merge_llm_chunks([], fallback=fallback)
        self.assertEqual(result, fallback)

    def test_no_fallback_and_no_data_returns_empty_shape(self):
        result = merge_llm_chunks([{"date": None, "bha_list": []}])
        self.assertEqual(result["wellbores"], [])

    def test_date_pulled_from_first_chunk_that_has_one(self):
        chunks = [
            {"date": None, "bha_list": [{"wellbore": "A", "bha_no": "1", "table": []}]},
            {"date": "2020-01-01", "bha_list": [{"wellbore": "A", "bha_no": "2", "table": []}]},
        ]
        result = merge_llm_chunks(chunks)
        self.assertEqual(result["date"], "2020-01-01")


if __name__ == "__main__":
    unittest.main()
