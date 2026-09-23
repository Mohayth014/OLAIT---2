from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_DIR = BASE_DIR / "backend" / "database"
DATABASE_PATH = DATABASE_DIR / "olai.db"
FRONTEND_DIR = BASE_DIR / "frontend"
MOBILE_DIR = BASE_DIR / "frontend-mobile"

# File storage: everything OLAI writes to disk lives under storage/
STORAGE_DIR = BASE_DIR / "storage"
ORIGINALS_DIR = STORAGE_DIR / "originals"      # uploaded files, byte-for-byte
PAGES_DIR = STORAGE_DIR / "pages"              # one normalized image per page
THUMBNAIL_DIR = STORAGE_DIR / "thumbnails"     # page previews for the UI
CROPS_DIR = STORAGE_DIR / "crops"              # line crops (Tesseract input + training pairs)
EXPORTS_DIR = STORAGE_DIR / "exports"          # generated Tamil / Tanglish / English PDFs

# Ensure runtime directories exist
for _dir in (DATABASE_DIR, ORIGINALS_DIR, PAGES_DIR, THUMBNAIL_DIR, CROPS_DIR, EXPORTS_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# Input
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".heic"}
MAX_UPLOAD_MB = 500
PDF_RENDER_DPI = 300

# Languages
# One pack per language. Only Tamil is active in the prototype; Hindi is planned
# and will be added as another pack without touching the shared pipeline.
DEFAULT_LANGUAGE = "ta"
LANGUAGE_PACKS = {
    "ta": {
        "name": "Tamil",
        "unicode_range": ("\u0B80", "\u0BFF"),
        "paddle_lang": "ta",
        "tesseract_lang": "tam",
        "romanized_label": "Tanglish",
    },
}

# OCR Engines
OCR_DEVICE = "gpu"  # "gpu" or "cpu"
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Source types (identified once per document by CLIP zero-shot)
# Each source type maps to a processing route; each route can use its own OCR
# recognition model, so a model trained later (e.g. palm-leaf) plugs into one route.
SOURCE_TYPES = {
    "modern_print": "a page of a modern printed book or document",
    "historical_print": "an old yellowed page from a historical printed book",
    "handwritten": "a handwritten page or handwritten manuscript",
    "palm_leaf": "a palm-leaf manuscript with engraved script",
    "inscription": "a stone or copper-plate inscription",
}
PROCESSING_ROUTES = {
    # route: {"paddle_rec_model": None -> use the language pack's default model}
    "modern_print": {"paddle_rec_model": None},
    "historical_print": {"paddle_rec_model": None},
    "handwritten": {"paddle_rec_model": None},
    "palm_leaf": {"paddle_rec_model": None},  # plug the trained palm-leaf model in here later
    "inscription": {"paddle_rec_model": None},
}
CLIP_MODEL_NAME = "openai/clip-vit-base-patch32"

# Priority speed rules
# Rule 1: Tesseract only re-reads lines PaddleOCR is not highly confident about.
TESSERACT_GATE_CONFIDENCE = 0.95   # provisional; calibrated on ground-truth pages in Phase 11
TESSERACT_AUDIT_RATE = 0.02        # share of confident lines still re-read, to catch a bad threshold
# Rule 2: CLIP classifies the document once, from a few sampled pages.
CLIP_SAMPLE_PAGES = 5
# Rule 3: digital PDFs with a valid Unicode text layer skip OCR entirely.
TEXT_LAYER_MIN_VALID = 0.95        # share of structurally valid words required to trust a text layer

# Confidence / human review
REVIEW_CONFIDENCE = 0.80           # provisional; lines below this go to human review
