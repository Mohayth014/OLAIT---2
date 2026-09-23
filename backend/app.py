import importlib.util
import asyncio
import json
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from backend.config import (
    ALLOWED_EXTENSIONS, BASE_DIR, CROPS_DIR, FRONTEND_DIR, MAX_UPLOAD_MB, MOBILE_DIR, ORIGINALS_DIR,
    PAGES_DIR, STORAGE_DIR, THUMBNAIL_DIR, DEFAULT_LANGUAGE, LANGUAGE_PACKS, TESSERACT_CMD,
    PROCESSING_ROUTES,
)
from backend.models import DashboardMetrics, DocumentList, DocumentSummary, Stats, HealthResponse, EngineStatus
from backend.database.db import (
    create_document, get_dashboard_metrics, get_document, get_stats, init_db, list_documents, new_document_id, save_page_embedding,
    get_document_review, get_line_training_context, save_recognition_page, save_training_pair,
    set_document_source_type, update_document_page_count, update_document_status, verify_line,
)
from backend.config import SOURCE_TYPES
from backend.models import LineVerification, SourceTypeUpdate
from backend.pipeline.preprocessing import analyze_image_quality, create_thumbnail, load_and_orient_image, resize_for_ocr
from backend.pipeline.tamil_processor import transliterate_tamil


def _process_photo(document_id: str, image_path: Path) -> None:
    """Run one uploaded photo through the async recognition pipeline."""
    from backend.pipeline.clip_engine import get_clip_engine
    from backend.pipeline.ocr_engine import get_ocr_engine
    from backend.pipeline.recognition_pipeline import recognize_pages

    async def run() -> None:
        try:
            update_document_status(document_id, "processing")
            image = load_and_orient_image(image_path)
            normalized, _ = resize_for_ocr(image)
            page_dir = PAGES_DIR / document_id
            thumbnail_dir = THUMBNAIL_DIR / document_id
            page_dir.mkdir(parents=True, exist_ok=True)
            thumbnail_dir.mkdir(parents=True, exist_ok=True)
            page_path = page_dir / "page-0001.png"
            thumbnail_path = thumbnail_dir / "page-0001.jpg"
            normalized.save(page_path, format="PNG")
            create_thumbnail(normalized).save(thumbnail_path, format="JPEG", quality=88)

            update_document_page_count(document_id, 1)
            page_values = {
                "image_path": str(page_path.relative_to(STORAGE_DIR)).replace("\\", "/"),
                "thumbnail_path": str(thumbnail_path.relative_to(STORAGE_DIR)).replace("\\", "/"),
                "width": normalized.width,
                "height": normalized.height,
                "quality_json": json.dumps(analyze_image_quality(normalized)),
                "extraction_method": "ocr",
            }
            clip_engine = get_clip_engine()
            results = await recognize_pages(
                [normalized], document_id, DEFAULT_LANGUAGE, "modern_print",
                clip_engine, get_ocr_engine(),
            )
            page_result = results[0]
            source_vote = page_result.get("source_vote", {})
            set_document_source_type(
                document_id, source_vote.get("source_type") or "modern_print",
                source_vote.get("confidence", 0.0), manual=False,
            )
            page_id = save_recognition_page(document_id, 1, page_result["lines"], **page_values)
            save_page_embedding(page_id, clip_engine.generate_embedding(normalized), "clip")
            update_document_status(document_id, "review")
        except Exception as exc:
            update_document_status(document_id, "failed", str(exc)[:1000])
            print(f"[OLAI] Document {document_id} failed: {exc}")

    asyncio.run(run())

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


@app.get("/api/dashboard", response_model=DashboardMetrics)
async def dashboard():
    return get_dashboard_metrics()


# API: Documents
@app.get("/api/documents", response_model=DocumentList)
async def documents(limit: int = Query(100, ge=1, le=1000)):
    return {"documents": list_documents(limit=limit)}


@app.delete("/api/documents/{document_id}", status_code=204)
async def delete_document_endpoint(document_id: str):
    document = get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    original = Path(document["original_path"])
    if not original.is_absolute():
        original = BASE_DIR / original
    original.unlink(missing_ok=True)
    for directory in (PAGES_DIR, THUMBNAIL_DIR, CROPS_DIR):
        shutil.rmtree(directory / document_id, ignore_errors=True)
    from backend.database.db import delete_document
    delete_document(document_id)


@app.post("/api/documents/upload", response_model=DocumentSummary, status_code=202)
async def upload_photo(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    filename = Path(file.filename or "photo.jpg").name
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS or extension == ".pdf":
        raise HTTPException(status_code=415, detail="Upload a JPG, PNG, TIFF, WEBP, HEIC, or JPEG photo.")
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded photo is empty.")
    if len(contents) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"The photo exceeds the {MAX_UPLOAD_MB} MB limit.")

    document_id = new_document_id()
    original_path = ORIGINALS_DIR / f"{document_id}{extension}"
    original_path.write_bytes(contents)
    stored_path = str(original_path.relative_to(BASE_DIR)) if original_path.is_relative_to(BASE_DIR) else str(original_path)
    document = create_document(filename, stored_path, "camera", document_id=document_id)
    background_tasks.add_task(_process_photo, document_id, original_path)
    return document


@app.get("/api/documents/{document_id}", response_model=DocumentSummary)
async def document_detail(document_id: str):
    doc = get_document(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    return doc


@app.get("/api/documents/{document_id}/review")
async def document_review(document_id: str):
    review = get_document_review(document_id)
    if not review:
        raise HTTPException(status_code=404, detail="Document not found.")
    for page in review["pages"]:
        for line in page["lines"]:
            line["tanglish"] = transliterate_tamil(line.get("verified_text") or line.get("ocr_text", ""))
    return review


@app.put("/api/lines/{line_id}/verify")
async def verify_document_line(line_id: int, verification: LineVerification):
    if not verification.reviewer.strip():
        raise HTTPException(status_code=422, detail="Reviewer name is required.")
    line = verify_line(line_id, verification.reviewer, verification.text, verification.action)
    if not line:
        raise HTTPException(status_code=404, detail="Line not found.")
    line["tanglish"] = transliterate_tamil(line.get("verified_text") or line.get("ocr_text", ""))
    context = get_line_training_context(line_id)
    if context and context["image_path"] and verification.text.strip():
        image_path = STORAGE_DIR / context["image_path"]
        if image_path.exists():
            from PIL import Image
            crop_dir = CROPS_DIR / "training_pairs" / (context["source_type"] or "unknown")
            crop_dir.mkdir(parents=True, exist_ok=True)
            crop_path = crop_dir / f"line-{line_id}.png"
            with Image.open(image_path) as image:
                image.crop((
                    max(0, round(context["x0"] - 4)), max(0, round(context["y0"] - 4)),
                    min(image.width, round(context["x1"] + 4)), min(image.height, round(context["y1"] + 4)),
                )).save(crop_path)
            save_training_pair(
                line_id, str(crop_path.relative_to(STORAGE_DIR)).replace("\\", "/"),
                verification.text.strip(), context["source_type"], context["language"],
            )
    return line


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
