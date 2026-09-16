"""
api/app.py — HTTP service + bundled frontend for the BHA extraction pipeline.

    pip install -r requirements.txt
    uvicorn api.app:app --reload
    # frontend:  http://127.0.0.1:8000/
    # API docs:  http://127.0.0.1:8000/docs

Routes:
    POST /api/documents              upload a PDF, processing runs in background
    GET  /api/documents              list documents + status/confidence
    GET  /api/documents/{id}         full result (extracted data, warnings)
    GET  /api/documents/{id}/status  lightweight polling endpoint
    GET  /                           the dashboard (static files from frontend/)

Why the API lives under /api and the frontend is served by this same app:
serving both from one origin means the browser makes same-origin requests,
so CORS never enters the picture for the bundled dashboard and there's one
process to run instead of two. CORS is still configured (see
config.CORS_ORIGINS) for the case where you run a separate dev server
(Vite on :5173, CRA on :3000) against this API.

Why background processing, not a synchronous response:
parsing + optional LLM normalization takes anywhere from under a second to
tens of seconds. Blocking the request ties up a worker and risks a
client-side timeout firing before extraction finishes. The client gets an
id immediately and polls /status.
"""
import json
import logging
import os
import shutil
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from bha import db
from bha.config import CORS_ORIGINS, FRONTEND_DIR, UPLOAD_DIR, ensure_dirs
from bha.pipeline import run as run_pipeline

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    db.init_db()
    yield


app = FastAPI(title="BHA Extraction Service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _process_in_background(pdf_path, filename):
    """Runs via BackgroundTasks. pipeline.run() already writes success or
    failure into the documents table (status + error columns), so a client
    polling /status sees the outcome either way -- this wrapper just makes
    sure an unexpected exception gets logged rather than vanishing, since
    BackgroundTasks doesn't surface exceptions to any caller."""
    try:
        run_pipeline(pdf_path)
    except Exception:
        logger.exception(f"background processing failed for {filename}")


def _row_to_dict(row, parse_json_fields=False):
    result = dict(row)
    if parse_json_fields:
        for field in ("raw_json", "warnings_json"):
            if result.get(field):
                result[field] = json.loads(result[field])
        if not result.get("warnings_json"):
            result["warnings_json"] = []
    return result


@app.post("/api/documents")
async def upload_document(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="only PDF files are accepted")

    ensure_dirs()
    dest_path = os.path.join(UPLOAD_DIR, os.path.basename(file.filename))
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Dedup up front: if this exact content was already processed, say so
    # immediately rather than starting a background task that would just
    # hit the same check inside pipeline.run() and skip.
    db.init_db()
    file_hash = db.compute_file_hash(dest_path)
    existing = db.find_existing(file_hash)
    if existing and existing["status"] == "done":
        return {"document_id": existing["id"], "status": "already_processed",
                "filename": existing["filename"]}

    background_tasks.add_task(_process_in_background, dest_path, file.filename)
    return {"status": "processing_started", "filename": file.filename}


@app.get("/api/documents")
def list_documents():
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT id, filename, status, confidence, needs_review, report_date,
                      chapter_start_page, chapter_end_page, created_at, updated_at
               FROM documents ORDER BY id DESC"""
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


@app.get("/api/documents/{document_id}")
def get_document(document_id: int):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="document not found")
        return _row_to_dict(row, parse_json_fields=True)


@app.get("/api/documents/{document_id}/status")
def get_status(document_id: int):
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, status, confidence, needs_review, error FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="document not found")
        return _row_to_dict(row)


# --- Frontend (mounted last so it never shadows the /api routes above) ---
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


# --- Known limits before calling this "production" ---
# - BackgroundTasks runs in-process: a restart mid-extraction loses that job
#   and leaves the row stuck at "processing" (pipeline.run() warns and
#   re-runs on the next upload of the same file, but nothing self-heals).
#   A real queue (Celery/RQ/arq + Redis) survives restarts and scales out.
# - SQLite is single-writer. Multiple uvicorn workers hitting the same file
#   will raise "database is locked" under concurrent writes -- move to
#   Postgres before running with --workers > 1.
# - No auth on any endpoint. Add an API key dependency before exposing this
#   beyond localhost.
