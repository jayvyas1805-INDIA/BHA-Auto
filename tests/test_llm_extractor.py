import json
import os
import unittest

from bha.llm_extractor import extract_bha_with_llm, ExtractionError, _validate_shape


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if not isinstance(body, str) else body

    def json(self):
        return self._body


def _ok_body(bha_no="1"):
    return {
        "choices": [{"message": {"content": json.dumps({
            "date": "2014-01-08",
            "bha_list": [{"wellbore": "A", "bha_no": bha_no, "kind": "x",
                          "description": "y", "name": "z", "table": []}],
        })}}]
    }


def _pages(n):
    return [{"page": i + 1, "text": f"page {i+1} text"} for i in range(n)]


class TestLLMExtractor(unittest.TestCase):

    def test_happy_path_single_chunk(self):
        calls = []

        def post_fn(url, headers, json_body, timeout):
            calls.append(json_body)
            return FakeResponse(200, _ok_body())

        results, errors = extract_bha_with_llm(_pages(3), api_key="k", chunk_size=3, post_fn=post_fn, sleep_fn=lambda s: None)
        self.assertEqual(len(results), 1)
        self.assertEqual(errors, [])
        self.assertEqual(results[0]["bha_list"][0]["bha_no"], "1")
        self.assertEqual(len(calls), 1)

    def test_chunking_multiple_calls(self):
        def post_fn(url, headers, json_body, timeout):
            return FakeResponse(200, _ok_body())

        results, errors = extract_bha_with_llm(_pages(7), api_key="k", chunk_size=3, post_fn=post_fn, sleep_fn=lambda s: None)
        self.assertEqual(len(results), 3)  # 7 pages / 3 -> chunks of 3,3,1
        self.assertEqual(errors, [])

    def test_retries_then_succeeds(self):
        attempts = {"n": 0}

        def post_fn(url, headers, json_body, timeout):
            attempts["n"] += 1
            if attempts["n"] < 3:
                return FakeResponse(503, {"error": "server busy"})
            return FakeResponse(200, _ok_body())

        results, errors = extract_bha_with_llm(_pages(3), api_key="k", post_fn=post_fn, sleep_fn=lambda s: None)
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(len(results), 1)
        self.assertEqual(errors, [])

    def test_permanent_failure_isolated_to_one_chunk(self):
        def post_fn(url, headers, json_body, timeout):
            if "page 1 " in json_body["messages"][1]["content"]:
                return FakeResponse(500, {"error": "down"})
            return FakeResponse(200, _ok_body())

        results, errors = extract_bha_with_llm(_pages(6), api_key="k", chunk_size=3, post_fn=post_fn, sleep_fn=lambda s: None)
        self.assertEqual(len(results), 1)   # second chunk (pages 4-6) succeeded
        self.assertEqual(len(errors), 1)    # first chunk (pages 1-3) failed after retries
        failed_pages, err = errors[0]
        self.assertEqual(failed_pages, [1, 2, 3])
        self.assertIsInstance(err, ExtractionError)

    def test_bad_api_key_fails_fast_no_retry(self):
        call_count = {"n": 0}

        def post_fn(url, headers, json_body, timeout):
            call_count["n"] += 1
            return FakeResponse(401, {"error": "unauthorized"})

        results, errors = extract_bha_with_llm(_pages(3), api_key="bad-key", post_fn=post_fn, sleep_fn=lambda s: None)
        self.assertEqual(call_count["n"], 1)  # no retries burned on a non-retryable error
        self.assertEqual(len(errors), 1)

    def test_malformed_json_response_is_caught(self):
        def post_fn(url, headers, json_body, timeout):
            return FakeResponse(200, {"choices": [{"message": {"content": "not valid json{{{"}}]})

        results, errors = extract_bha_with_llm(_pages(3), api_key="k", post_fn=post_fn, sleep_fn=lambda s: None)
        self.assertEqual(results, [])
        self.assertEqual(len(errors), 1)

    def test_validate_shape_rejects_missing_bha_list(self):
        with self.assertRaisesRegex(ExtractionError, "bha_list"):
            _validate_shape({"date": "x"})

    def test_validate_shape_rejects_non_list_table(self):
        with self.assertRaisesRegex(ExtractionError, "table"):
            _validate_shape({"bha_list": [{"bha_no": "1", "table": "oops"}]})

    def test_validate_shape_accepts_well_formed(self):
        obj = {"date": "d", "bha_list": [{"bha_no": "1", "table": [{"component": "x"}]}]}
        self.assertEqual(_validate_shape(obj), obj)

    def test_missing_api_key_raises_runtime_error(self):
        old_a, old_b = os.environ.pop("MISTRAL_API_KEY", None), os.environ.pop("MISTRAL_KEY", None)
        try:
            with self.assertRaises(RuntimeError):
                extract_bha_with_llm(_pages(1), api_key=None, post_fn=lambda *a, **k: None)
        finally:
            if old_a is not None:
                os.environ["MISTRAL_API_KEY"] = old_a
            if old_b is not None:
                os.environ["MISTRAL_KEY"] = old_b


if __name__ == "__main__":
    unittest.main()
