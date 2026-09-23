import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from backend.config import (
    FRONTEND_DIR, MOBILE_DIR, STORAGE_DIR, DEFAULT_LANGUAGE, LANGUAGE_PACKS, TESSERACT_CMD
)
from backend.models import DocumentList, DocumentSummary, Stats, HealthResponse, EngineStatus
from backend.database.db import init_db, list_documents, get_document, get_stats, set_document_source_type
from backend.config import SOURCE_TYPES
from backend.models import SourceTypeUpdate

APP_NAME = "OLAI"
APP_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    print("[Server Startup] Initializing OLAI database...")
    init_db()
    yield


app = FastAPI(
    title="OLAI - Tamil Document Digitization",
    description="AI-assisted Tamil document digitization with human verification and evidence-linked outputs.",
    version=APP_VERSION,
    lifespan=lifespan,
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _engine_status() -> list:
    """Cheap availability checks; engines themselves load lazily on first use."""
    return [
        EngineStatus(name="PaddleOCR", available=importlib.util.find_spec("paddleocr") is not None,
                     detail="primary OCR (GPU)"),
        EngineStatus(name="Tesseract", available=Path(TESSERACT_CMD).exists(),
                     detail="second-opinion OCR (CPU)"),
        EngineStatus(name="CLIP", available=importlib.util.find_spec("transformers") is not None,
                     detail="source-type identification"),
        EngineStatus(name="PyMuPDF", available=importlib.util.find_spec("fitz") is not None,
                     detail="PDF pages + digital text layer"),
    ]


# API: Health
@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(
        status="healthy",
        app=APP_NAME,
        version=APP_VERSION,
        language=LANGUAGE_PACKS[DEFAULT_LANGUAGE]["name"],
        engines=_engine_status(),
    )


# API: Dashboard statistics
@app.get("/api/stats", response_model=Stats)
async def stats():
    return get_stats()


# API: Documents
@app.get("/api/documents", response_model=DocumentList)
async def documents(limit: int = Query(100, ge=1, le=1000)):
    return {"documents": list_documents(limit=limit)}


@app.get("/api/documents/{document_id}", response_model=DocumentSummary)
async def document_detail(document_id: str):
    doc = get_document(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    return doc


@app.put("/api/documents/{document_id}/source-type", response_model=DocumentSummary)
async def override_source_type(document_id: str, update: SourceTypeUpdate):
    if update.source_type not in SOURCE_TYPES:
        raise HTTPException(status_code=422, detail="Unknown source type.")
    if not get_document(document_id):
        raise HTTPException(status_code=404, detail="Document not found.")
    set_document_source_type(document_id, update.source_type, 1.0, manual=True)
    return get_document(document_id)


# Static file serving:

class NoCacheStaticFiles(StaticFiles):
    """StaticFiles that always forces browser revalidation (Cache-Control: no-cache).

    Plain StaticFiles sends only ETag/Last-Modified with no Cache-Control, which
    browsers are free to treat as heuristically cacheable -- a normal reload can
    then keep serving app.js/index.html from disk cache with zero request to the
    server, silently masking any edit to the frontend until a hard refresh. This
    does not disable caching (ETag-based 304s still happen), it just forces a
    round-trip to check freshness on every load.
    """
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response

# 1. Page images, thumbnails and exports
app.mount("/storage", StaticFiles(directory=str(STORAGE_DIR)), name="storage")
# 2. Dedicated Mobile Web Application (phone-optimised UI at /m)
app.mount("/m", NoCacheStaticFiles(directory=str(MOBILE_DIR), html=True), name="mobile")
# 3. Desktop Frontend Web Application (catch-all, must be mounted last)
app.mount("/", NoCacheStaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
