# OLAI (ஓலை): AI-Assisted Tamil Document Digitization

OLAI accepts Tamil documents through upload or camera scanning, extracts the Tamil text with OCR,
flags uncertain text with ranked suggestions, lets a human verify and correct it, links every line
back to its exact region on the original page, and produces searchable **Tamil**, **Tanglish** and
**English** outputs, each exportable as a separate PDF.

> **AI extracts. AI identifies uncertainty. AI suggests. Human verifies. OLAI preserves the evidence.**

**Status:** prototype under construction. Phase 1 of 11 (clean slate) is complete.

## Pipeline

```
Upload / Camera
      ↓
Pages ──③ digital PDF with a valid Tamil text layer? ──YES──→ text + positions, no OCR ─┐
      │ NO                                                                             │
      ↓                                                                                │
Preprocessing (quality, denoise, deskew, perspective)                                  │
      ↓                                                                                │
②CLIP source type (once per document) ‖ PaddleOCR Tamil (GPU) → layout + reading order │
      ↓                                                                                │
①Confident line? ─YES─→ accept     NO ─→ Tesseract re-reads the line crop (CPU)        │
      ↓                                                                                │
Suggestions + context ranking → confidence → human verification ←──────────────────────┘
      ↓
Verified Tamil → Tanglish + English → provenance (line → region → page → document)
      ↓
Search → Export (Tamil PDF | Tanglish PDF | English PDF)
```

Priority speed rules: ① Tesseract only re-reads lines PaddleOCR is unsure of, ② CLIP classifies each
document once from a few sampled pages, ③ digital PDFs with a trustworthy text layer skip OCR.

## Project structure

```
backend/
  app.py                  FastAPI app, API routes, static mounts
  config.py               Paths, language packs, source types, routes, thresholds
  models.py               API response schemas
  database/db.py          SQLite schema: documents → pages → regions → lines → candidates / verifications / outputs
  pipeline/
    preprocessing.py      Image loading, EXIF orientation, quality metrics, thumbnails
    clip_engine.py        CLIP source-type classification + page embeddings
    vector_search.py      Similar-page search over CLIP embeddings
    ocr_engine.py         PaddleOCR (GPU, primary) + Tesseract (CPU, second opinion)
    tamil_processor.py    Tamil normalization + text-layer validation
frontend/                 Desktop web app
frontend-mobile/          Mobile web app (served at /m)
storage/                  Originals, page images, crops, exports (not in git)
tests/                    pytest suite
tools/phase0_smoke_test.py  Environment check (GPU, OCR engines, CLIP)
```

## Setup (Windows, NVIDIA GPU)

1. Install **Python 3.12** and **Tesseract 5** (UB-Mannheim build) with Tamil language + script data.
2. Create the environment outside OneDrive: `py -3.12 -m venv C:\olai-env`
3. Install packages in the order given at the top of `requirements.txt`.
4. Check the environment: `C:\olai-env\Scripts\python.exe tools\phase0_smoke_test.py`

## Run

```powershell
C:\olai-env\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Open http://localhost:8000 (desktop) or http://localhost:8000/m (mobile).

## Tests

```powershell
C:\olai-env\Scripts\python.exe -m pytest tests -v
```

## API (so far)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | App status and OCR/AI engine availability |
| `GET` | `/api/stats` | Documents, pages, verified pages, lines awaiting review |
| `GET` | `/api/documents` | List documents |
| `GET` | `/api/documents/{id}` | Document detail |
