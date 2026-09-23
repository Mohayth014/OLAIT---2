# AI-Based Packaged Commodity Compliance Detection System
### Legal Metrology (Packaged Commodities) Rules, 2011 Enforcement Platform

**Problem Statement ID:** 26034  
**Title:** Software System to Check Compliance of Packaged Commodities under Legal Metrology (Packaged Commodities) Rules, 2011 by Scanning Products, Images and Labels.

---

## 1. System Architecture & High-Accuracy Pipeline

The platform combines **Computer Vision**, **CLIP Visual Intelligence**, **Dual-OCR Ensemble (EasyOCR + PaddleOCR)**, **2D Spatial LayoutLM Clustering**, **Packaging Domain Spell-Checking**, and a **Statutory Rule Engine** to verify mandatory declarations under the **Legal Metrology (Packaged Commodities) Rules, 2011** (Rules 6, 7, and 10).

```
                        PACKAGED COMMODITY IMAGE(S)
                       [Front (PDP) + Back / Crimp Panel]
                                    |
                                    v
                       +--------------------------+
                       |   Image Preprocessing    |
                       |   - EXIF Auto-Orientation|
                       |   - Glare Reduction Filter|
                       |   - CLAHE Adaptive Contrast|
                       |   - Laplacian Blur Score |
                       +------------+-------------+
                                    |
             +----------------------+----------------------+
             |                                             |
             v                                             v
   +--------------------+                        +--------------------+
   |   CLIP Visual AI   |                        |  Dual-OCR Ensemble |
   |  - Zero-Shot Cat   |                        |  - EasyOCR Engine  |
   |  - 512-dim Embed   |                        |  - PaddleOCR v4/v6 |
   |  - Vector Search   |                        |  - Consensus Voting|
   +---------+----------+                        +---------+----------+
             |                                             |
             |                                             v
             |                                   +--------------------+
             |                                   | Dot-Matrix Optical |
             |                                   | Normalizer & Spell |
             |                                   | Checker (SymSpell) |
             |                                   +---------+----------+
             |                                             |
             +----------------------+----------------------+
                                    |
                                    v
                       +--------------------------+
                       | 2D Spatial Layout Binder |
                       | - Coordinate Grid [0,1000|
                       | - "See Below" Linker     |
                       | - Spaced PIN Code Parser |
                       +------------+-------------+
                                    |
                                    v
                       +--------------------------+
                       |  Information Extraction  |
                       |  - Generic Product Name  |
                       |  - Net Quantity & Units  |
                       |  - MRP & Tax Declaration |
                       |  - Statutory USP Rule 610|
                       |  - Mfg & Expiry Dates    |
                       |  - Manufacturer & PIN    |
                       |  - Consumer Care Contact |
                       |  - Country of Origin     |
                       |  - FSSAI License Number  |
                       +------------+-------------+
                                    |
                                    v
                       +--------------------------+
                       | Statutory Rule Engine    |
                       |  - Rules 6(1)(a)-(f)     |
                       |  - Rule 6(10) Statutory  |
                       |  - Rule 7 Readability    |
                       +------------+-------------+
                                    |
                                    v
                       +--------------------------+
                       |   Compliance Decision    |
                       |  COMPLIANT / NON-COMPLIANT|
                       |  / MANUAL REVIEW QUEUE   |
                       +------------+-------------+
                                    |
             +----------------------+----------------------+
             |                                             |
             v                                             v
   +--------------------+                        +--------------------+
   | Official Printable |                        | Officer Human-in-  |
   | Inspection Cert.   |                        | the-Loop Review    |
   +--------------------+                        +--------------------+
```

---

## 2. Advanced Accuracy Features Implemented

### 1. Dual-OCR Ensemble with Consensus Voting (`ocr_engine.py`)
- Fuses **EasyOCR** and **PaddleOCR (PP-OCRv4/v6)** in parallel.
- Merges bounding boxes based on Intersection over Union (IoU $\ge 0.35$).
- Prioritizes higher confidence text transcriptions and recovers characters that single-engine OCR misses on low-contrast plastic films.

### 2. Dot-Matrix Optical Normalizer (`spell_checker.py`)
- Packaging ink-jet printers frequently generate dot-matrix artifacts:
  - Artifacts `{`, `?`, `*` $\rightarrow$ `₹` (e.g., `* {523 /-,3 1.05/9` $\rightarrow$ `₹ 523 /-, 1.05/g`).
  - Weight digits: `(\d+)\s+9` $\rightarrow$ `\1 g` (preserving survey numbers like `159/B`).
  - Stamped date abbreviations: `# MM/YY` $\rightarrow$ `MFD: MM/YY`, `@ MM/YY` $\rightarrow$ `USE BEFORE: MM/YY`.
- Sub-millisecond **SymSpell** fuzzy lookup corrects OCR typos in brand names and statutory terms (`cust care` $\rightarrow$ `consumer care`).

### 3. Cross-Line "See Below / See Crimp" Spatial Linker (`extraction.py`)
- Real-world Indian packaged goods separate pre-printed declaration headers (`MRP (inclusive of all taxes) SEE BELOW`) from the actual ink-jet stamped numerals positioned directly underneath.
- The spatial linker clusters lines vertically within a calibrated bounding box window, binding pre-printed statutory headers to factory stamped numerals.

### 4. Spaced Indian PIN Code Parser (`extraction.py`)
- Real-world manufacturer labels format postal codes with internal spaces (e.g., `370 240`, `400 099`).
- Standard `\d{6}` regexes miss these; the spaced PIN parser extracts and validates any 6-digit postal code format and pairs it with the state dictionary.

### 5. Statutory Unit Sale Price (USP) Engine (Rule 6(10))
- Automatically extracts printed unit sale prices (`₹ 1.05 / g` or `1.05/g`).
- Automatically computes the statutory rate under Rule 6(10):
  $$\text{USP} = \frac{\text{MRP}}{\text{Net Quantity}}$$
  (e.g., ₹ 523.00 / 125 g = ₹ 4.18 / g).

### 6. Dual-Panel (Front + Back) Multi-Angle Inspection
- Solves single-panel omissions where brand & net quantity appear on the Front (Principal Display Panel), but MRP, Manufacturer, and Dates reside on the Back or crimp.
- API and Web UI support uploading both panels simultaneously, fusing OCR bounding boxes and text streams for a synchronized compliance audit.

### 7. Specular Glare Reduction Filter (`preprocessing.py`)
- Eliminates specular blown-out glare patches on glossy laminate pouches using adaptive luminance thresholding and Telea inpainting.

---

## 3. Statutory Legal Metrology Rules Enforced

| Rule Citation | Statutory Requirement | Implementation / Validation |
| :--- | :--- | :--- |
| **Rule 6(1)(a)** | Name & Complete Address of Manufacturer / Packer / Importer | Multi-candidate address scoring with Spaced PIN verification |
| **Rule 6(1)(b)** | Generic Name or Commodity Identity | Dynamic Trademark extraction + CLIP zero-shot classification |
| **Rule 6(1)(c)** | Net Quantity in Standard SI Units (`g`, `kg`, `ml`, `l`, `N`) | Normalizes units; flags prohibited symbols (`gms`, `kilos`) |
| **Rule 6(1)(d)** | Month & Year of Manufacture, Packing, or Expiry | Parses both printed and dot-matrix `#` / `@` date stamps |
| **Rule 6(1)(e)** | Maximum Retail Price (MRP) & Tax Inclusivity | Verifies price in `₹` and checks for *"inclusive of all taxes"* |
| **Rule 6(1)(f)** | Consumer Care Grievance Redressal Mechanism | Detects toll-free telephone number and official email address |
| **Rule 6(10)** | Statutory Unit Sale Price (USP) & Country of Origin | Validates printed USP or calculates rate; extracts Country of Origin |
| **Rule 7** | Legibility, Minimum Font Height & Visual Contrast | Laplacian sharpness, RMS contrast, and box height audit |

---

## 4. Empirical Accuracy Benchmark Results

Benchmarked across the packaged commodity dataset (`FOOD/FOOD`):

| Metric | Measured Performance |
| :--- | :--- |
| **CLIP Zero-Shot Top-1 Accuracy** | **55.49%** (±20.65%) |
| **CLIP Zero-Shot Top-3 Accuracy** | **86.67%** |
| **Rule 6(1)(a) Manufacturer Recall** | **90.0%** (9/10) |
| **Rule 6(1)(b) Commodity Identity Recall** | **100.0%** (10/10) |
| **Rule 6(1)(c) Net Quantity Recall** | **80.0%** (8/10) |
| **Rule 6(1)(d) Date (Mfg/Expiry) Recall** | **40.0%** (4/10) |
| **Rule 6(1)(e) MRP & Tax Declaration Recall** | **50.0%** (5/10) |
| **Rule 6(10) Unit Sale Price Recall** | **40.0%** (4/10) |
| **Character Recognition Confidence** | **37.53% - 58.3%** |
| **Average End-to-End Pipeline Latency** | **8.92s** per image (CPU inference) |

---

## 5. Project Directory Structure

```
CLIP/
├── backend/
│   ├── app.py                      # FastAPI REST API & static mount points
│   ├── config.py                   # Centralized configuration & legal constants
│   ├── models.py                   # Pydantic schemas (supporting dual-panel & USP)
│   ├── pipeline/
│   │   ├── preprocessing.py        # Orientation, glare reduction & CLAHE contrast
│   │   ├── clip_engine.py          # CLIP classification & 512-dim embedding
│   │   ├── vector_search.py        # Vector similarity search over indexed catalog
│   │   ├── ocr_engine.py           # Dual-OCR Ensemble (EasyOCR + PaddleOCR)
│   │   ├── spell_checker.py        # Dot-matrix optical normalizer & SymSpell
│   │   ├── spatial_layout.py       # 2D LayoutLM coordinate clustering & binder
│   │   ├── extraction.py           # NLP extraction, spaced PIN parser & USP engine
│   │   ├── rules_engine.py         # Statutory Legal Metrology (Rules 2011) engine
│   │   └── readability.py          # Rule 7 font size, contrast & blur assessment
│   ├── database/
│   │   ├── db.py                   # SQLite database (inspections, catalog, rules)
│   │   ├── indexer.py              # Automated dataset indexer & embedding generator
│   │   └── thumbnails/             # Web thumbnails for dataset catalog
│   └── reporting/
│       └── report_generator.py     # Formal printable HTML compliance certificates
├── frontend/
│   ├── index.html                  # Dark-mode dashboard with dual-panel scanner
│   ├── styles.css                  # Custom styling & glassmorphic design system
│   └── app.js                      # Client logic, dual-panel canvas visualizer & scanner
├── FOOD/
│   └── FOOD/                       # Packaged commodity dataset images
├── tests/
│   ├── test_rules.py               # Unit tests for Legal Metrology compliance rules
│   ├── test_extraction.py          # Unit tests for text normalization, PIN & USP
│   ├── test_enhanced_features.py   # Unit tests for glare, spell check & spatial layout
│   └── test_pipeline.py            # End-to-end integration test
├── scratch/
│   ├── evaluate_accuracy.py        # Empirical accuracy benchmark script
│   └── accuracy_evaluation_report.json # Benchmark evaluation output
├── requirements.txt                # Python dependencies
└── README.md                       # System documentation
```

---

## 6. Installation & Setup

### 6.1 Prerequisites
- Python 3.10, 3.11, or 3.12 (Tested on Python 3.12 64-bit on Windows)
- Pip package manager

### 6.2 Install Dependencies
```powershell
pip install -r requirements.txt
```

### 6.3 Index the Dataset Catalog
To generate CLIP embeddings and thumbnails for all packaged products in `FOOD/FOOD`:
```powershell
python -m backend.database.indexer
```

### 6.4 Start the FastAPI Application
```powershell
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```
Open your browser to:
```
http://localhost:8000
```

---

## 7. REST API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/scan` | Execute AI inspection on uploaded or dataset images (supports `file`, `dataset_filename`, `back_file`, `back_dataset_filename`) |
| `GET` | `/api/catalog` | Retrieve list of pre-indexed catalog commodities with thumbnails |
| `GET` | `/api/inspections` | Retrieve history of past inspections. Optional query params: `search` (text filter over id / filename / product / brand / manufacturer), `status` (`COMPLIANT` \| `NON_COMPLIANT` \| `REVIEW_REQUIRED`), `limit` |
| `GET` | `/api/inspections/{id}` | Retrieve specific inspection record and metadata |
| `POST` | `/api/inspections/{id}/review` | Submit official human-in-the-loop review / override |
| `GET` | `/api/dashboard/metrics` | Bento-dashboard payload: KPIs, `compliance_rate` / `hold_rate` / `avg_confidence`, `wow_compliance_delta`, `category_distribution`, `field_detection` (per-field detection rate), `violation_types` (failed-rule tally), and a 14-day `trend_14d` series |
| `GET` | `/api/rules` | Retrieve statutory rule configurations |
| `PUT` | `/api/rules/{id}` | Update statutory rule severity, requirement, or enabled state |
| `GET` | `/api/reports/{id}/html` | Generate printable Legal Metrology digital compliance certificate (HTML) |
| `GET` | `/api/reports/{id}/pdf` | Download the same certificate as a PDF (requires `reportlab`) |
| `GET` | `/api/reports/{id}/export?format=json\|csv` | Editable / machine-readable export of the full inspection record |

---

## 8. Running Automated Tests

Run the complete test suite:
```powershell
python -m pytest tests/ -v
```

Run accuracy evaluation benchmark:
```powershell
python scratch/evaluate_accuracy.py
```
