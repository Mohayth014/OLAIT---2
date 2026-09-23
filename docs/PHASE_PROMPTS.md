# OLAI: Phase-wise Build Prompts

Each phase has one prompt. To run a phase, paste the **Shared context** block followed by that
phase's prompt into Claude Code, opened in this project folder.

Every phase ends the same way: run the tests, show the results, commit, and **stop for approval**
before starting the next phase.

---

## Shared context (paste before every phase prompt)

```
Project: OLAI (ஓலை), an AI-assisted Tamil document digitization system, built by converting the
TRACE AI compliance scanner in this folder. Keep the template's technical architecture: FastAPI
backend, singleton AI engines, parallel branches via asyncio.to_thread + gather, confidence-based
routing, human-in-the-loop review with audit trail, vanilla-JS desktop app (frontend/) + mobile app
(frontend-mobile/, served at /m), existing design system in frontend/styles.css.

Spec: the OLAI project document (upload/camera → source identification → preprocessing → Tamil OCR
→ confidence + suggestions → human verification → verified Tamil → Tanglish + English → provenance
→ search → separate Tamil/Tanglish/English PDF export → reuse).

Locked decisions:
- No login/roles in the prototype. Reviewers type a name once; it is stored on each verification.
- Database: SQLite (backend/database/db.py), schema kept PostgreSQL-compatible.
- OCR: PaddleOCR Tamil on GPU (primary) + Tesseract 5 `tam` on CPU (second opinion). No EasyOCR.
- Source type: CLIP zero-shot (backend/pipeline/clip_engine.py).
- Tanglish: our own deterministic romanizer. English: IndicTrans2 distilled indic→en via
  CTranslate2, behind a swappable translation_service interface. Translate VERIFIED Tamil only.
- Tamil only for now. Hindi is on hold until the user says so, but keep code language-pack ready
  (LANGUAGE_PACKS in config.py, `language` column in DB, no Tamil hard-coding in shared code).
- Palm-leaf / old scripts: build the route now with standard models; each route in
  PROCESSING_ROUTES can take its own recognition model later. Save corrected line crops as
  training pairs.
- ocr_text is NEVER overwritten; verified_text and the verifications table hold human decisions.

Priority speed rules (must be honoured):
 ① Tesseract only re-reads lines PaddleOCR is not highly confident about (TESSERACT_GATE_CONFIDENCE),
   plus a small random audit sample (TESSERACT_AUDIT_RATE).
 ② CLIP classifies each document ONCE from CLIP_SAMPLE_PAGES sampled pages (not every page).
 ③ Digital PDFs whose text layer passes tamil_processor.validate_text_layer skip OCR entirely.

Environment: Python 3.12 venv at C:\olai-env (outside OneDrive). RTX 4050 6 GB.
Keep nvidia-cudnn-cu12==9.10.2.21 (matches torch) and opencv-contrib-python==4.10.0.84 only.
Tesseract at C:\Program Files\Tesseract-OCR. Run with C:\olai-env\Scripts\python.exe.
Known gotchas: Tesseract emits U+200C after pulli (normalize_text strips it); PDF text layers
can look Tamil but be structurally garbled (validate structure, not character ratio).

Working rules: read the existing code before changing it; match its style; write pytest tests
for new logic; run long jobs with unbuffered logs and give live progress updates; report real
measured numbers, never guesses presented as results; commit at the end of the phase
(message ends with the Co-Authored-By line); then stop and summarise what was built, what was
verified, what is still unverified, and what the user needs to check.
```

---

## Phase 0: Setup and safety net  ✅ DONE (commit 8b8a0ce)

```
Phase 0. Prepare the machine and a restore point.
1. git init, add a .gitignore (venv, *.db, storage/, datasets, OLAI_samples/, _trace_backup/),
   commit the untouched TRACE AI code as the baseline.
2. Create a Python 3.12 venv at C:\olai-env. Install GPU PyTorch (cu126), PaddlePaddle-GPU 3.3.1
   (cu126; use the bcebos CDN wheel with resumable download if the mirror is slow), PaddleOCR,
   pytesseract, PyMuPDF, OpenCV contrib 4.10, transformers<5, FastAPI, ReportLab.
3. Align cuDNN so torch and paddle load in the same process.
4. Write tools/phase0_smoke_test.py checking: torch GPU, paddle GPU, generating a Tamil test PDF,
   rejecting a garbled PDF text layer, PaddleOCR Tamil on GPU (text + confidence + timing),
   Tesseract `tam` (text + confidence + timing), CLIP on GPU.
Done when all checks pass and the timings are reported.
```

## Phase 1: Clean slate  ✅ DONE (commit b39e822)

```
Phase 1. Turn TRACE AI into an empty OLAI skeleton without losing anything.
1. Move FOOD/, the old database, thumbnails and generated reports into _trace_backup/ (git-ignored).
2. Delete legal-metrology code: auth, rules engine, extraction, readability, violation
   diagnostics, spatial layout, dispatcher, violation/compliance reports, dataset indexer, old tests.
3. New config.py: storage folders, LANGUAGE_PACKS (ta), SOURCE_TYPES, PROCESSING_ROUTES with
   per-route rec model, speed-rule settings, provisional thresholds.
4. New SQLite schema: documents, pages, regions, lines, candidates, verifications, outputs, jobs,
   training_pairs (foreign keys + cascade, WAL).
5. Rewrite ocr_engine.py (PaddleOCR read_page + Tesseract read_line), repurpose clip_engine.py
   for source types, generalise vector_search.py, turn spell_checker.py into tamil_processor.py
   (normalize_text, is_valid_tamil_word, validate_text_layer).
6. New app.py: /api/health, /api/stats, /api/documents, static mounts (/storage, /m, /).
7. Minimal OLAI home page (desktop + mobile) reusing styles.css / m.css.
8. New tests, requirements.txt with install order, README.
Done when tests pass, the server starts, and the engines still read the Phase 0 test page.
```

---

## Phase 2: Input and the digital-PDF fast path ③

```
Phase 2. Input: every file becomes pages, and digital PDFs skip OCR.

Build:
1. backend/services/upload_service.py: validate extension (ALLOWED_EXTENSIONS) and size
   (MAX_UPLOAD_MB), stream the upload to storage/originals/<doc_id>/<original filename>, create the
   documents row. Reject empty, corrupt and unsupported files with clear messages.
2. backend/services/document_service.py: normalise any input into page records.
   - PDF: PyMuPDF. For each page, extract the text layer with word positions and run
     tamil_processor.validate_text_layer. If trusted → extraction_method='text_layer', store lines
     from the text layer (confidence 1.0, engine 'text_layer', bbox scaled to the page-image pixel
     space, text normalised). If not → render the page at PDF_RENDER_DPI to storage/pages/ and mark
     it for OCR. Always render a page image + thumbnail (needed for provenance highlighting).
   - Image (JPG/PNG/TIFF/WEBP/HEIC, including multi-page TIFF): load_and_orient_image → page image.
3. Camera input: POST /api/documents/camera accepts a captured photo (file_type='camera'); it goes
   through exactly the same pipeline as uploads. Frontend camera capture (getUserMedia with a
   file-input fallback on mobile) with capture / retake / confirm. Perspective correction is
   Phase 3; here just store the photo.
4. Background jobs: backend/services/job_service.py. One job per document, pages processed in
   order, jobs row updated per page (done_pages, current_step). Resumable: on startup, find jobs
   in 'running'/'queued' state and continue from the first page not yet 'ready'. Processing must
   not block API requests (worker thread or FastAPI background task + thread).
5. API: POST /api/documents/upload, POST /api/documents/camera, GET /api/documents/{id}/pages,
   GET /api/jobs/{id} (progress), DELETE /api/documents/{id} (also deletes its storage files).
6. Frontend: enable the Upload and Camera cards on desktop and mobile. Drag-and-drop, progress
   bar ("page 12 / 300"), polling job progress, the document appears in Recent/Library with its
   status. Allow the server to be reached from the phone on the same Wi-Fi (--host 0.0.0.0,
   document it; camera on a phone needs HTTPS or localhost, so the mobile file-input with
   capture="environment" is the reliable path).
7. Tests: upload validation, PDF with a valid Tamil text layer → text_layer pages with lines and
   bboxes, garbled-layer PDF → OCR route, image upload → 1 page, multi-page PDF page count, job
   resume after a simulated crash, delete cascades files + rows.

Measure and report: time for a 100-page digital Tamil PDF (should be seconds), and time to
render pages of a scanned PDF. Use the user's samples in OLAI_samples/ if present.
```

## Phase 3: Preprocessing

```
Phase 3. Clean page images before OCR (only for pages routed to OCR).

Build in backend/pipeline/preprocessing.py (keep existing helpers):
1. assess_image_quality(): blur (Laplacian variance), brightness, contrast, noise estimate,
   skew angle, resolution. Store as pages.quality_json. Re-tune the provisional thresholds for
   document pages (not packaging).
2. Enhancement steps, applied only when the quality metrics call for them (good pages skip them,
   which saves time): grayscale, contrast normalisation (CLAHE), denoising, gentle sharpening,
   optional adaptive binarisation.
3. Deskew: estimate the text angle (projection profile or Hough on text lines) and rotate; never
   rotate when confidence in the angle is low.
4. Camera photos: detect the page boundary (largest 4-point contour) and apply a perspective
   warp; fall back to the original if no reliable quadrilateral is found.
5. Keep the original page image untouched for display/provenance; save the processed image
   separately and record which steps were applied. OCR bboxes must map back to the ORIGINAL page
   image coordinates (store the transform, or run OCR on an image with the same geometry).
6. Tests with synthetic images: rotated text is deskewed to within ±1°, a skewed photo of a page
   is rectified, a clean page is left unchanged, quality metrics move in the right direction.

Show before/after images for a tilted phone photo and a faded scan from OLAI_samples/.
```

## Phase 4: Parallel AI analysis ① ②

```
Phase 4. The core recognition engine.

Build:
1. ② Source identification once per document: sample CLIP_SAMPLE_PAGES pages (spread across
   start/middle/end), classify each with clip_engine.classify_source_type, majority vote weighted
   by confidence → documents.source_type/source_confidence. Re-classify an individual page only if
   its OCR confidence drops sharply below the document average. Manual override endpoint:
   PUT /api/documents/{id}/source-type (sets source_manual=1). Store page embeddings for
   vector_search (similar pages).
2. Run CLIP sampling in parallel with OCR of the first pages (asyncio.to_thread + gather, as in
   the template), so identification adds no wall-clock time.
3. PaddleOCR per page using the route's rec model (PROCESSING_ROUTES), batched on the GPU where
   the API allows. Store lines with text, confidence, bbox (original-image pixels), engine.
4. backend/pipeline/layout_service.py: group lines into regions (columns, blocks, titles,
   marginal notes) and assign reading order (column detection + top-to-bottom within a column).
   PP-Structure / PP-DocLayout may be used if it helps; keep a simple geometric fallback.
5. ① Tesseract gate: for each line with confidence < TESSERACT_GATE_CONFIDENCE, crop the line
   (small padding) and call ocr_engine.read_line; also re-read a TESSERACT_AUDIT_RATE random
   sample of confident lines and log disagreements. Save crops under storage/crops/.
6. Pipelining: while Paddle processes page n+1 on the GPU, Tesseract re-reads page n's doubtful
   lines on CPU threads (thread pool sized to CPU cores). One GPU model instance only.
7. Benchmark script tools/benchmark_pipeline.py: seconds per page split by stage
   (preprocess / paddle / layout / tesseract), share of lines sent to Tesseract, pages per minute,
   GPU memory peak. Run it on the user's samples by source type.
8. Tests: gate sends only low-confidence lines (mock OCR), reading order on a synthetic
   two-column page, CLIP vote logic, resume mid-document.

Report measured speed for: digital PDF, clean modern scan, old/degraded scan, phone photo.
```

## Phase 5: Tamil intelligence

```
Phase 5. Confidence, suggestions and context.

Build:
1. Lexicon: build a Tamil word-frequency list from an openly licensed corpus (document the source
   and licence in the README), normalised with normalize_text; load into SymSpell. Keep it in
   backend/data/ (small enough for git, or a download script if not).
2. backend/pipeline/candidate_engine.py: for an uncertain line, generate candidate readings from
   (a) Paddle vs Tesseract disagreement (word-level alignment), (b) Tesseract per-word
   alternatives where available, (c) confusable-letter substitutions (ள/ழ/ல, ண/ன/ந, ர/ற, and
   vowel-sign confusions such as ி/ீ, ு/ூ, ெ/ே) filtered by the lexicon, (d) SymSpell lookups.
   Drop structurally invalid words (is_valid_tamil_word).
3. backend/pipeline/context_engine.py: score each candidate against neighbouring words (word
   frequency + bigram counts from the same corpus); combine with OCR confidence into a ranked top 3.
   Scores are suggestions, not truth.
4. backend/pipeline/confidence_engine.py: line status 'accepted' or 'needs_review' using
   REVIEW_CONFIDENCE, engine agreement, and lexicon coverage; mark the specific uncertain words
   (character offsets) so the UI can highlight them.
5. Historical spelling is never "corrected" automatically: suggestions are only offered for review,
   and accepted lines keep the OCR text as read.
6. Store candidates (rank, score, source). Tests: known confusable errors produce the correct
   word in the top 3; valid archaic words are not flagged as invalid; ZWNJ-free text throughout.
```

## Phase 6: Human verification

```
Phase 6. A human decides; nothing is lost.

Build:
1. backend/services/verification_service.py + API:
   GET  /api/documents/{id}/review-queue (lines needing review, page order)
   GET  /api/pages/{id} (page image URL, lines with bboxes, statuses, candidates)
   POST /api/lines/{id}/verify   {action: accept|choose|edit|reject, text?, candidate_id?, reviewer}
   POST /api/pages/{id}/verify   (accept all remaining lines on the page)
   GET  /api/lines/{id}/history  (audit trail)
2. Every action writes a verifications row (previous_text, new_text, reviewer, time), sets
   lines.verified_text and review_status; ocr_text is never modified. Undo = a new verification
   restoring the previous text.
3. When a correction changes the text, save a training pair: the line crop + verified text +
   source_type + language (storage/crops/, training_pairs table). Export script
   tools/export_training_pairs.py (PaddleOCR rec-training format).
4. Page and document status roll up: page verified when all lines are verified/accepted-and-
   confirmed; document 'verified' when all pages are.
5. Reviewing works while the rest of the document is still processing.
6. Tests: each action, audit trail order, ocr_text unchanged, training pair written only on real
   corrections, status roll-up.
```

## Phase 7: The three outputs (+ Tanglish, English)

```
Phase 7. Tamil, Tanglish and English from verified text.

Build:
1. backend/services/romanization_service.py: deterministic Tamil → Tanglish. Handle vowels, long
   vowels, consonants, uyirmei, pulli, ஃ, Grantha letters (ஜ ஷ ஸ ஹ க்ஷ ஸ்ரீ), and common
   conventions (ழ→zh, ற→r/tr in ற்ற, ந/ன→n, ஞ→nj/gn, word-initial vs medial க/ச/ட/த/ப
   voicing: e.g. medial க→g, ச→s, ட→d, த→dh, ப→b; doubled consonants stay unvoiced).
   Line-level output (keeps provenance). Unit tests with a table of known words
   (தமிழ்→tamizh, மொழி→mozhi, பழமையானது→pazhamaiyaanadhu, அழகானது→azhagaanadhu, etc.).
2. backend/services/translation_service.py: interface translate(texts, src, tgt) with an
   IndicTrans2 + CTranslate2 (int8, GPU) implementation. Input is page-level verified Tamil
   assembled in reading order and split into sentences (not OCR lines). Download/convert the model
   with a script in tools/. Cache results in outputs keyed by a hash of the source text;
   re-translate only when the verified text changes. Runs automatically after a page is verified,
   in the background.
3. API: GET /api/pages/{id}/outputs → {tamil, tanglish (per line), english (per page)}.
4. The translation provider must be swappable by config (so Claude/Google can be added later).
5. Tests: romanizer table, translation service with a mocked model, cache invalidation.
Report translation speed (sentences/sec) on the GPU and show 5 sample translations for the user
to judge.
```

## Phase 8: Provenance and search

```
Phase 8. Every word traceable back to its source.

Build:
1. Provenance endpoint: GET /api/lines/{id}/source → document, page number, page image URL,
   bbox (+ region). Tanglish lines share the Tamil line's provenance; English points to the page
   and to the Tamil lines it was translated from.
2. Search index: SQLite FTS5 table(s) over verified Tamil, Tanglish and English with the trigram
   tokenizer (works for Tamil substrings); keep it in sync on verification and output changes.
   Only verified text is searchable by default; an option includes unverified text, clearly labelled.
3. GET /api/search?q=&lang=tamil|tanglish|english|all&document_id= → results with document, page,
   highlighted snippet, line id. Rank by match quality then document order. Normalise the query
   with normalize_text.
4. Frontend: Search view (query box, language filter, result list); clicking a result opens the
   page viewer scrolled/zoomed to the line with the bbox highlighted.
5. Tests: Tamil substring search, Tanglish search, English search, a query with ZWNJ still
   matches, results point to the correct bbox, index updates after a correction.
```

## Phase 9: Export

```
Phase 9. Three independent PDFs.

Build backend/services/export_service.py:
1. generate_tamil_pdf, generate_tanglish_pdf, generate_english_pdf, generate_all (zip).
2. Fonts: Noto Serif Tamil / Noto Sans Tamil (OFL) in backend/assets/fonts/. Tamil needs complex
   text shaping; verify ReportLab renders conjuncts and vowel signs correctly (compare a rendered
   PDF page against the text). If it does not, render the PDFs from HTML with a headless browser
   instead, and say which approach was used and why.
3. Each PDF: title page (document name, date, source type, verification status), page-by-page
   content with the original page number, paragraph structure preserved, a footer with the
   source reference. Unverified pages are excluded unless draft mode is chosen, in which case they
   are watermarked "DRAFT - NOT VERIFIED".
4. API: GET /api/documents/{id}/export?format=tamil|tanglish|english|all&draft=false.
5. Tests: each PDF is generated, contains the expected text (extract with PyMuPDF and compare),
   drafts are watermarked, verified-only excludes unverified pages.
Open the Tamil PDF and show a rendered image of page 1 so the user can check the letters.
```

## Phase 10: Frontend (desktop + mobile)

```
Phase 10. The full OLAI experience on the existing design system.

Desktop screens (frontend/):
- Home: upload / camera, recent documents, stats.
- Processing: live per-document progress (source type → preprocessing → OCR → confidence),
  pages appear as they finish.
- Library: all documents, status, % verified, source type (editable), delete.
- Review (the key screen): page image on the left (zoom/pan, line boxes coloured by status,
  uncertain words highlighted) and text on the right. For each flagged line: top-3 suggestions,
  edit box, accept/choose/edit/reject. Keyboard shortcuts (next flagged line, 1/2/3 to choose,
  Enter accept, E edit). Reviewer name remembered locally.
- Content: Tamil / Tanglish / English tabs; clicking any line highlights its source region.
- Search: from Phase 8.
- Export: the four download buttons with draft toggle.
- Dashboard: documents, pages, % auto-accepted, % reviewed, average confidence, pages/minute,
  lines sent to Tesseract (%), using the dataviz approach for charts.
Mobile (frontend-mobile/): camera scan, upload, processing progress, simple review of flagged
lines, search.
All Tamil text uses the Noto Sans Tamil font. Works at phone width without horizontal scroll.
Walk through the complete flow in the browser with a real sample and report anything broken.
```

## Phase 11: Accuracy, tests and documentation

```
Phase 11. Proof that it works, with numbers.

1. tools/evaluate.py: for OLAI_samples/ground_truth/ pages, compute CER and WER per source type
   (Paddle alone, Paddle+Tesseract gate, after suggestions), confusion pairs, and the review rate.
2. Calibrate TESSERACT_GATE_CONFIDENCE and REVIEW_CONFIDENCE from the data (choose the values
   that catch at least ~95% of real errors while minimising review load); update config.py and
   report the trade-off curve.
3. Speed report: seconds/page and pages/minute for digital PDF, clean scan, old scan, photo.
4. Full pytest suite green; add an end-to-end test (upload → process → verify → search → export)
   on a small generated Tamil PDF.
5. README: architecture, setup, usage, API reference, measured accuracy and speed, known
   limitations (palm-leaf, handwriting), roadmap (palm-leaf model training from training_pairs,
   Hindi pack, optional cloud fast mode, PostgreSQL).
6. docs/DEMO_SCRIPT.md: a 5-minute jury demo path with the exact files to use.
```

---

## Later (after the prototype, only when the user asks)

```
Hindi pack: add LANGUAGE_PACKS["hi"] (Paddle Devanagari model, Tesseract hin, Devanagari
structural validator, Hindi lexicon, Hinglish romanizer with schwa deletion, Noto Serif Devanagari
for PDFs), language auto-detection (Unicode for digital PDFs, Tesseract OSD on sampled pages for
scans) with manual override, and optional Tamil↔Hindi translation (IndicTrans2 indic-indic) +
Tamil→Devanagari transliteration as extra outputs.

Palm-leaf model: fine-tune a PaddleOCR recognition model on exported training_pairs for the
palm_leaf route and plug it into PROCESSING_ROUTES["palm_leaf"].

Optional: cloud OCR fast mode, PostgreSQL migration, stronger literary translation provider.
```
