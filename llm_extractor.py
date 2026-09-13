import os
import json
import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - fallback if python-dotenv is not installed
    def load_dotenv():
        return False

load_dotenv()  # Load environment variables from .env file

MISTRAL_API_URL = os.getenv(
    "MISTRAL_API_URL", "https://api.mistral.ai/v1/chat/completions"
)
MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-2506")

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


def _chunk_pages(pages, chunk_size=3):
    for i in range(0, len(pages), chunk_size):
        yield pages[i:i + chunk_size]


def _call_mistral(page_texts, api_key):
    user_content = "\n\n---PAGE BREAK---\n\n".join(page_texts)

    resp = requests.post(
        MISTRAL_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(content)


def extract_bha_with_llm(chapter_pages, api_key=None, chunk_size=3):
    """
    chapter_pages: list of page dicts (as returned by reader.read_pdf),
    already sliced down to just the BHA chapter.
    Returns a list of per-chunk result dicts: [{"date": ..., "bha_list": [...]}, ...]
    Merging across chunks is the caller's responsibility (see merger.py) so
    that page-boundary-split tables can be stitched correctly.
    """
    api_key = api_key or os.environ.get("MISTRAL_API_KEY") or os.environ.get("MISTRAL_KEY")
    if not api_key:
        raise RuntimeError(
            "MISTRAL_API_KEY not set. Export it or pass api_key= explicitly."
        )

    chunk_results = []
    for chunk in _chunk_pages(chapter_pages, chunk_size=chunk_size):
        page_texts = [p["text"] for p in chunk]
        result = _call_mistral(page_texts, api_key)
        chunk_results.append(result)

    return chunk_results
