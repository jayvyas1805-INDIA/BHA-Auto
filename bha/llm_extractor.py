import os
import json
import time
import logging

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - fallback if python-dotenv is not installed
    def load_dotenv():
        return False

load_dotenv()  # Load environment variables from .env file

logger = logging.getLogger(__name__)

from .config import MISTRAL_API_URL, MISTRAL_MODEL as MODEL

# Status codes worth retrying: rate limit + transient server errors.
# 401/403 (bad key) and 400 (bad request) are NOT retried -- retrying those
# just burns time and quota for an error that will never go away on its own.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

SYSTEM_PROMPT = """You extract Bottom Hole Assembly (BHA) data from oil & gas \
Final Well Report pages. These reports come from many operators and templates, \
so field labels vary (e.g. "RUN TYPE" and "BHA KIND" mean the same thing; \
"RUN NAME" and "BHA NAME" mean the same thing). Numbers may use comma or dot \
as the decimal separator (e.g. "5,750" and "5.750" both mean 5.75) — always \
normalize to dot-decimal strings in your output.

For every BHA entry found on the given pages, extract:
- wellbore: the WELLBORE value in force for that entry (it may be stated \
once and apply to several BHA entries that follow, until a new WELLBORE line appears)
- bha_no: the BHA NO value (string)
- kind: RUN TYPE / BHA KIND value
- description: DESCRIPTION value
- name: RUN NAME / BHA NAME value
- table: list of string-component rows, each with component, od_in, id_in, \
length_m, acc_length_m (use null for any column not present for that row — \
do NOT shift values into the wrong column when one is missing)

Also extract the report's date if present on these pages (any date format, \
return as it literally appears in the text).

Return ONLY a JSON object with this exact shape, no prose, no markdown fences:
{
  "date": "<string or null>",
  "bha_list": [
    {
      "wellbore": "<string or null>",
      "bha_no": "<string>",
      "kind": "<string or null>",
      "description": "<string or null>",
      "name": "<string or null>",
      "table": [
        {"component": "<string>", "od_in": "<string or null>", "id_in": "<string or null>",
         "length_m": "<string or null>", "acc_length_m": "<string or null>"}
      ]
    }
  ]
}

If a BHA's table is empty or not shown on this chunk, return an empty list \
for "table" — do not invent rows.
"""


class ExtractionError(Exception):
    """Raised when a chunk could not be extracted after retries, or the
    model's response didn't match the expected shape. Callers (main.py)
    should catch this per-chunk rather than let one bad chunk abort the
    whole document."""


def _chunk_pages(pages, chunk_size=3):
    for i in range(0, len(pages), chunk_size):
        yield pages[i:i + chunk_size]


def _validate_shape(obj):
    """
    Raises ExtractionError with a specific message if `obj` doesn't match
    the schema we asked the model for. Catching a malformed response here
    (rather than downstream, e.g. a KeyError in merger.py) means the error
    message tells you exactly which field was wrong instead of an opaque
    stack trace three modules away.
    """
    if not isinstance(obj, dict):
        raise ExtractionError(f"expected a JSON object, got {type(obj).__name__}")
    if "bha_list" not in obj or not isinstance(obj["bha_list"], list):
        raise ExtractionError("response missing a 'bha_list' array")
    for i, entry in enumerate(obj["bha_list"]):
        if not isinstance(entry, dict):
            raise ExtractionError(f"bha_list[{i}] is not an object")
        if "table" in entry and not isinstance(entry["table"], list):
            raise ExtractionError(f"bha_list[{i}].table is not an array")
        for j, row in enumerate(entry.get("table", [])):
            if not isinstance(row, dict):
                raise ExtractionError(f"bha_list[{i}].table[{j}] is not an object")
    return obj


def _default_post(url, headers, json_body, timeout):
    """The real HTTP call. Split out from _call_mistral so tests can pass a
    fake `post_fn` and exercise retry/validation/merge logic without any
    network access."""
    return requests.post(url, headers=headers, json=json_body, timeout=timeout)


def _call_mistral(page_texts, api_key, post_fn=_default_post, max_retries=3, timeout=120, sleep_fn=time.sleep):
    user_content = "\n\n---PAGE BREAK---\n\n".join(page_texts)
    body = {
        "model": MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = post_fn(MISTRAL_API_URL, headers=headers, json_body=body, timeout=timeout)

            if resp.status_code in RETRYABLE_STATUS:
                last_error = ExtractionError(f"HTTP {resp.status_code} (attempt {attempt}/{max_retries})")
                logger.warning(str(last_error))
            elif not (200 <= resp.status_code < 300):
                # Non-retryable failure (bad key, bad request, etc) -- fail fast.
                raise ExtractionError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            else:
                content = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                return _validate_shape(parsed)

        except (requests.exceptions.RequestException, json.JSONDecodeError, KeyError) as e:
            last_error = ExtractionError(f"{type(e).__name__}: {e} (attempt {attempt}/{max_retries})")
            logger.warning(str(last_error))

        if attempt < max_retries:
            sleep_fn(min(2 ** attempt, 10))  # 2s, 4s, 8s... capped

    raise last_error or ExtractionError("failed with no specific error captured")


def extract_bha_with_llm(chapter_pages, api_key=None, chunk_size=3, post_fn=_default_post, sleep_fn=time.sleep):
    """
    chapter_pages: list of page dicts (as returned by reader.read_pdf),
    already sliced down to just the BHA chapter.

    Returns (chunk_results, errors):
      chunk_results -- list of successfully-parsed per-chunk dicts, in order
      errors        -- list of (chunk_page_numbers, ExtractionError) for any
                       chunk that failed after retries. A chunk failing does
                       NOT abort the rest of the document -- the caller
                       (main.py) falls back to the deterministic parser's
                       result for that chunk's pages when merging.
    """
    api_key = api_key or os.environ.get("MISTRAL_API_KEY") or os.environ.get("MISTRAL_KEY")
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY not set. Export it or pass api_key= explicitly.")

    chunk_results = []
    errors = []
    for chunk in _chunk_pages(chapter_pages, chunk_size=chunk_size):
        page_numbers = [p.get("page") for p in chunk]
        page_texts = [p["text"] for p in chunk]
        try:
            result = _call_mistral(page_texts, api_key, post_fn=post_fn, sleep_fn=sleep_fn)
            chunk_results.append(result)
        except ExtractionError as e:
            logger.error(f"chunk (pages {page_numbers}) failed permanently: {e}")
            errors.append((page_numbers, e))

    return chunk_results, errors
