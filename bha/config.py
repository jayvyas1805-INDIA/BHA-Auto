"""
config.py — single place for paths and tunables.

Everything resolves from PROJECT_ROOT (the repo directory), not from
whatever module happens to be importing it. Before this existed, db.py
used os.path.dirname(__file__) for the SQLite path, which after the
package restructure would have quietly created bha/bha.db *inside the
package directory* instead of at the project root -- the kind of bug
that only shows up as "why is my data gone" after someone reinstalls.

Every value is overridable by environment variable so the same code runs
in dev, in tests (tmp dirs), and in a container without edits.
"""
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Storage
DB_PATH = os.getenv("BHA_DB_PATH", os.path.join(PROJECT_ROOT, "bha.db"))
UPLOAD_DIR = os.getenv("BHA_UPLOAD_DIR", os.path.join(PROJECT_ROOT, "upload"))
OUTPUT_DIR = os.getenv("BHA_OUTPUT_DIR", os.path.join(PROJECT_ROOT, "output"))
FRONTEND_DIR = os.getenv("BHA_FRONTEND_DIR", os.path.join(PROJECT_ROOT, "frontend"))

# LLM normalization (off by default -- the deterministic parser runs either way)
USE_LLM_NORMALIZATION = os.getenv("USE_LLM_NORMALIZATION", "false").lower() == "true"
MISTRAL_API_URL = os.getenv("MISTRAL_API_URL", "https://api.mistral.ai/v1/chat/completions")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-2506")

# CORS origins allowed to call the API. Comma-separated env var.
# Default covers the usual local dev ports; the bundled frontend is served
# same-origin by the API itself, so it needs no CORS entry at all.
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("BHA_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
    if o.strip()
]


def ensure_dirs():
    for d in (UPLOAD_DIR, OUTPUT_DIR):
        os.makedirs(d, exist_ok=True)
