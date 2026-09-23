import os
import re
import uuid
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Query, Response, Depends
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
import io
import numpy as np

from backend.config import (
    BASE_DIR, DATASET_DIR, THUMBNAIL_DIR, FRONTEND_DIR, MOBILE_DIR, REPORTS_DIR,
    CONFIDENCE_HIGH, CONFIDENCE_REVIEW
)
from backend.models import (
    InspectionResponse, ManualReviewPayload, RuleConfigUpdate,
    ExtractedData, RuleValidationResult, OCRDetectedBox, SimilarProduct, ReadabilityMetric,
    LoginPayload, TokenResponse, OverridePayload, RegisterPayload,
    JurisdictionPayload, CreateSessionPayload, AddProductPayload
)
from backend.auth import issue_token, verify_password, get_current_user, get_current_user_flexible, require_role
from backend.pipeline.preprocessing import (
    load_and_orient_image, create_thumbnail, analyze_image_quality, reduce_specular_glare
)
from backend.pipeline.clip_engine import get_clip_engine
from backend.pipeline.vector_search import get_vector_search_engine
from backend.pipeline.ocr_engine import get_ocr_engine
from backend.pipeline.readability import evaluate_font_and_readability
from backend.pipeline.extraction import extract_structured_information
from backend.pipeline.spatial_layout import get_spatial_layout_extractor
from backend.pipeline.spell_checker import get_spell_checker
from backend.pipeline.rules_engine import LegalMetrologyRuleEngine
from backend.database.db import (
    init_db, save_inspection, get_inspection_by_id, list_inspections,
    update_officer_review, get_dashboard_metrics, get_all_rules,
    update_rule, get_all_catalog_products, get_user_by_login, get_user_by_id, create_user, record_override,
    set_user_jurisdiction, get_officer_metrics,
    create_session, get_session_by_id, list_sessions, close_session,
    add_product, get_product_by_id, link_scan_to_product, get_session_summary,
    mark_session_seized, find_manufacturer_contact, list_manufacturer_contacts,
    save_violation_report, list_violation_reports, get_violation_report
)
from backend.database.indexer import index_dataset_products
from backend.reporting.report_generator import (
    generate_html_report, generate_pdf_report, generate_docx_report,
    generate_json_export, generate_csv_export
)
from backend.reporting.violation_report import (
    group_products_by_manufacturer, build_violation_report,
    render_violation_report_html, write_violation_report_html
)
from backend.reporting.dispatcher import dispatch_violation_report, dispatch_mode

@asynccontextmanager
async def lifespan(_app: FastAPI):
    print("[Server Startup] Initializing Database, Indexer & Engines...")
    init_db()
    # Pre-index dataset if not indexed
    try:
        index_dataset_products(force_reindex=False)
    except Exception as e:
        print(f"[Startup Warning] Indexer notice: {e}")
    yield

app = FastAPI(
    title="Legal Metrology Packaged Commodity Compliance AI",
    description="AI software system checking compliance of packaged commodities under Legal Metrology Rules, 2011.",
    version="2.1.0",
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Display titles for the two access levels. The stored role identifiers stay
# 'inspector' / 'manager' (every permission check, JWT claim and audit record
# keys off them); these are the Legal Metrology titles shown to the user.
ROLE_LABELS = {
    "inspector": "Legal Metrology Officer",
    "manager": "Controller of Legal Metrology",
}

# API: Health
@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "Legal Metrology Compliance Engine",
        "timestamp": datetime.now().isoformat(),
        "device": "cuda" if get_clip_engine().device == "cuda" else "cpu"
    }

# API: Login -- issues a JWT carrying {user_id, role, name}
@app.post("/api/auth/register", response_model=TokenResponse)
async def register(payload: RegisterPayload):
    """Officer self-registration: name, Officer ID, password, jurisdiction.

    The jurisdiction captured here is stored on the officer's profile and is
    auto-populated on every later session -- it is never asked for again at login.
    """
    officer_name = (payload.officer_name or "").strip()
    officer_id = (payload.officer_id or "").strip()
    jurisdiction = (payload.jurisdiction or "").strip()

    if not officer_name:
        raise HTTPException(status_code=400, detail="Officer name is required.")
    if not officer_id:
        raise HTTPException(status_code=400, detail="Officer ID is required.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-_/]{2,31}", officer_id):
        raise HTTPException(
            status_code=400,
            detail="Officer ID must be 3-32 characters using letters, numbers, hyphen, underscore or slash.",
        )
    if len(payload.password or "") < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    if not jurisdiction:
        raise HTTPException(status_code=400, detail="Jurisdiction is required.")

    try:
        user = create_user(officer_name, officer_id, payload.password, jurisdiction)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    token = issue_token(user["id"], user["role"], user["name"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user["id"],
        "name": user["name"],
        "role": user["role"],
        "role_label": ROLE_LABELS.get(user["role"], user["role"].title()),
        "officer_id": user.get("officer_id"),
        "jurisdiction": user.get("jurisdiction"),
    }

@app.post("/api/auth/login", response_model=TokenResponse)
async def login(payload: LoginPayload):
    identifier = payload.identifier or payload.email or ""
    user = get_user_by_login(identifier)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid Officer ID or password.")
    # When the client supplies the officer name too, it must match the registered
    # one. Deliberately the same error text as a bad password -- never reveal which
    # of the three credentials was wrong.
    if payload.officer_name is not None:
        if payload.officer_name.strip().casefold() != (user["name"] or "").strip().casefold():
            raise HTTPException(status_code=401, detail="Invalid Officer ID or password.")
    token = issue_token(user["id"], user["role"], user["name"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user["id"],
        "name": user["name"],
        "role": user["role"],
        "role_label": ROLE_LABELS.get(user["role"], user["role"].title()),
        "officer_id": user.get("officer_id") or user["id"],
        "jurisdiction": user.get("jurisdiction"),
    }

# API: Dashboard Metrics (manager only -- inspectors have no oversight view)
@app.get("/api/dashboard/metrics")
async def dashboard_metrics(user: dict = Depends(require_role("manager"))):
    return get_dashboard_metrics()

# API: Catalog items for 1-click dataset selection
@app.get("/api/catalog")
async def get_catalog():
    items = get_all_catalog_products()
    # Return without full embedding vector for lightweight payload
    cleaned = []
    for it in items:
        cleaned.append({
            "id": it["id"],
            "filename": it["filename"],
            "name": it["name"],
            "category": it["category"],
            "brand": it.get("brand", ""),
            "thumbnail_url": it["thumbnail_url"],
            "image_url": f"/dataset-images/{it['filename']}"
        })
    return cleaned

# API: Core Compliance Scanner
@app.post("/api/scan", response_model=InspectionResponse)
async def scan_product(
    file: Optional[UploadFile] = File(None),
    dataset_filename: Optional[str] = Form(None),
    back_file: Optional[UploadFile] = File(None),
    back_dataset_filename: Optional[str] = Form(None),
    session_id: Optional[str] = Form(None),
    product_id: Optional[str] = Form(None),
    user: dict = Depends(require_role("inspector")),
):
    """
    Scans a packaged commodity image from either an uploaded file or a selected dataset image.
    Supports Dual-Panel Multi-Angle Inspection (Front + Back panels scanned simultaneously).
    Executes the full hybrid pipeline:
    1. Preprocessing & Quality Check (Front + Back)
    2. CLIP Visual Understanding & Embedding
    3. Vector Similarity Search
    4. Text Region Detection & Concurrent Multi-Panel OCR
    5. Cross-Panel Information Extraction
    6. Legal Metrology Rule Validation
    """
    image_filename = ""
    img: Image.Image

    if file and file.filename:
        image_filename = file.filename
        content = await file.read()
        try:
            img = load_and_orient_image(io.BytesIO(content))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to read uploaded image: {str(e)}")
    elif dataset_filename:
        image_filename = dataset_filename
        dataset_path = Path(DATASET_DIR) / dataset_filename
        if not dataset_path.exists():
            raise HTTPException(status_code=404, detail=f"Dataset file {dataset_filename} not found.")
        try:
            img = load_and_orient_image(dataset_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to open dataset image: {str(e)}")
    else:
        raise HTTPException(status_code=400, detail="Either 'file' or 'dataset_filename' must be provided.")

    insp_id = f"INSP-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Optional inspection-session tagging: this sample belongs to Product `product_id`
    # inside Session `session_id`. Purely additive metadata -- validated up front, before
    # any ML compute runs, and never touches the pipeline logic below. Ad-hoc scans (no
    # active session) never send these fields, so this whole block is skipped for them.
    if bool(session_id) != bool(product_id):
        raise HTTPException(status_code=400, detail="session_id and product_id must be provided together.")
    if session_id and product_id:
        sess = get_session_by_id(session_id)
        if not sess or sess["officer_id"] != user["user_id"]:
            raise HTTPException(status_code=404, detail="Inspection session not found.")
        if sess["status"] != "ACTIVE":
            raise HTTPException(status_code=400, detail="Cannot add a sample scan to a closed inspection session.")
        prod = get_product_by_id(product_id)
        if not prod or prod["session_id"] != session_id:
            raise HTTPException(status_code=404, detail="Product not found in this inspection session.")

    # Handle Optional Back Panel Image
    back_img: Optional[Image.Image] = None
    back_image_url: Optional[str] = None
    is_dual = False

    if back_file and back_file.filename:
        try:
            back_content = await back_file.read()
            back_img = load_and_orient_image(io.BytesIO(back_content))
            is_dual = True
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to read uploaded back image: {str(e)}")
    elif back_dataset_filename:
        back_dataset_path = Path(DATASET_DIR) / back_dataset_filename
        if back_dataset_path.exists():
            try:
                back_img = load_and_orient_image(back_dataset_path)
                is_dual = True
                back_image_url = f"/dataset-images/{back_dataset_filename}"
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to open back dataset image: {str(e)}")

    # 1. Preprocessing & Thumbnail creation (Front)
    thumb_name = f"{insp_id}_thumb.jpg"
    thumb_path = Path(THUMBNAIL_DIR) / thumb_name
    thumb_img = create_thumbnail(img, max_dim=400)
    thumb_img.save(thumb_path, format="JPEG", quality=85)
    thumbnail_url = f"/thumbnails/{thumb_name}"
    image_url = f"/dataset-images/{image_filename}" if dataset_filename else thumbnail_url

    # Save Back Thumbnail / Web image if uploaded
    if back_img and not back_image_url:
        back_thumb_name = f"{insp_id}_back_thumb.jpg"
        back_thumb_path = Path(THUMBNAIL_DIR) / back_thumb_name
        back_thumb_img = create_thumbnail(back_img, max_dim=800)
        back_thumb_img.save(back_thumb_path, format="JPEG", quality=85)
        back_image_url = f"/thumbnails/{back_thumb_name}"

    # Image quality & readability
    quality_meta = analyze_image_quality(img)

    # 2 & 3. Concurrent Execution of CLIP and OCR for Minimum Latency
    clip_engine = get_clip_engine()
    ocr_engine = get_ocr_engine()
    vector_search = get_vector_search_engine()

    import asyncio

    def run_clip_pipeline():
        categories = clip_engine.classify_category(img)
        embedding = clip_engine.generate_embedding(img)
        sim_items = vector_search.search_similar(embedding, top_k=3, exclude_filename=dataset_filename)
        return categories, embedding, sim_items

    def run_front_ocr():
        boxes, raw_text = ocr_engine.run_ocr(img)
        for b in boxes:
            b["panel"] = "front"
        read_eval = evaluate_font_and_readability(img, boxes)
        return boxes, raw_text, read_eval

    def run_back_ocr():
        if not back_img:
            return [], "", None
        boxes, raw_text = ocr_engine.run_ocr(back_img)
        for b in boxes:
            b["panel"] = "back"
        read_eval = evaluate_font_and_readability(back_img, boxes)
        return boxes, raw_text, read_eval

    # Execute in parallel threads
    clip_task = asyncio.to_thread(run_clip_pipeline)
    front_ocr_task = asyncio.to_thread(run_front_ocr)
    tasks = [clip_task, front_ocr_task]

    if is_dual and back_img:
        back_ocr_task = asyncio.to_thread(run_back_ocr)
        tasks.append(back_ocr_task)

    results = await asyncio.gather(*tasks)

    (clip_categories, image_embedding, similar_items) = results[0]
    (front_boxes, front_text, readability_eval) = results[1]

    if is_dual and len(results) > 2:
        back_boxes, back_text, back_readability = results[2]
        all_ocr_boxes = front_boxes + back_boxes
        full_text = f"{front_text}\n--- BACK PANEL ---\n{back_text}"
        if back_readability and back_readability.get("warnings"):
            readability_eval["warnings"] = list(
                set(readability_eval.get("warnings", []) + [f"[Back Panel] {w}" for w in back_readability["warnings"]])
            )
    else:
        all_ocr_boxes = front_boxes
        full_text = front_text

    # 5. Information Extraction
    extracted_dict = extract_structured_information(all_ocr_boxes, full_text, clip_categories)

    # 5b. Root-cause diagnostic signal -- spatial trigger-keyword clusters, specular glare
    # coverage, and dot-matrix stamp detection. These are read-only side channels for
    # diagnosing *why* a rule failed; they do not change what OCR/extraction actually read,
    # so they carry no risk of shifting extraction accuracy on already-tuned packages.
    spatial_data = get_spatial_layout_extractor().extract_spatial_entities(all_ocr_boxes, img.width, img.height)
    dot_matrix_fired = get_spell_checker().has_dot_matrix_artifacts(full_text)
    _, glare_ratio = reduce_specular_glare(np.array(img))
    if back_img is not None:
        _, back_glare_ratio = reduce_specular_glare(np.array(back_img))
        glare_ratio = max(glare_ratio, back_glare_ratio)

    # 6. Legal Metrology Rule Engine
    configured_rules = [r for r in get_all_rules() if r.get("enabled", 1) == 1]
    rule_engine = LegalMetrologyRuleEngine(configured_rules)
    overall_status, overall_conf, rule_results, violations, summary = rule_engine.evaluate(
        extracted_dict, readability_eval,
        spatial_data=spatial_data, glare_ratio=glare_ratio, dot_matrix_fired=dot_matrix_fired,
    )

    # Build response payload
    response_data = {
        "inspection_id": insp_id,
        "timestamp": timestamp_str,
        "image_filename": image_filename,
        "image_url": image_url,
        "thumbnail_url": thumbnail_url,
        "overall_status": overall_status,
        "overall_confidence": overall_conf,
        "summary": summary,
        "extracted_data": extracted_dict,
        "rule_results": rule_results,
        "violations": violations,
        "readability": readability_eval,
        "ocr_boxes": all_ocr_boxes,
        "clip_categories": clip_categories[:5],
        "similar_products": similar_items,
        "officer_verification": None,
        "back_image_url": back_image_url,
        "is_dual_panel": is_dual,
        "inspector_id": user["user_id"],
        "override_history": []
    }

    # Save to persistent database
    save_inspection(response_data)

    if session_id and product_id:
        try:
            link_scan_to_product(session_id, product_id, insp_id)
        except Exception:
            pass  # scan already saved and valid; linking is best-effort metadata

    return response_data

def _enforce_record_access(insp: dict, user: dict, action: str = "view"):
    """Row-level authorization for a SINGLE inspection record.

    Deliberately re-checked here, independent of any list-level filtering:
    GET /api/inspections already forces inspector_id server-side for
    inspector-role callers, but that alone does not stop an inspector from
    reaching a colleague's record by guessing/incrementing an id and hitting
    this single-record endpoint directly (OWASP API1: Broken Object Level
    Authorization). Every route that returns one record's contents must call
    this -- never assume the list filter already covered it.
    """
    if user["role"] == "inspector" and insp.get("inspector_id") != user["user_id"]:
        raise HTTPException(status_code=403, detail=f"You can only {action} your own inspections.")


def _enforce_session_access(session: dict, user: dict, action: str = "view"):
    """Row-level authorization for a single inspection session -- same BOLA-defense
    rationale as _enforce_record_access above, applied to the session/product layer."""
    if user["role"] == "inspector" and session.get("officer_id") != user["user_id"]:
        raise HTTPException(status_code=403, detail=f"You can only {action} your own inspection sessions.")


VALID_INSPECTION_TYPES = {"Routine", "Special", "Complaint-based", "Enforcement"}

# API: Officer jurisdiction -- entered once, then auto-populated on every future login.
@app.put("/api/profile/jurisdiction")
async def set_jurisdiction(payload: JurisdictionPayload, user: dict = Depends(require_role("inspector"))):
    jurisdiction = (payload.jurisdiction or "").strip()
    if not jurisdiction:
        raise HTTPException(status_code=400, detail="Jurisdiction cannot be empty.")
    set_user_jurisdiction(user["user_id"], jurisdiction)
    return {"user_id": user["user_id"], "name": user["name"], "jurisdiction": jurisdiction}

# API: Officer dashboard stats (active/completed inspections, pending reviews, non-compliance findings)
@app.get("/api/officer/metrics")
async def officer_metrics(user: dict = Depends(require_role("inspector"))):
    return get_officer_metrics(user["user_id"])

# API: Start a new inspection session. Officer identity/jurisdiction are always
# server-derived from the JWT + users row -- never accepted from the client.
@app.post("/api/sessions")
async def create_inspection_session(payload: CreateSessionPayload, user: dict = Depends(require_role("inspector"))):
    officer = get_user_by_id(user["user_id"])
    jurisdiction = (officer or {}).get("jurisdiction")
    if not jurisdiction:
        raise HTTPException(status_code=400, detail="Set your jurisdiction before starting an inspection.")
    location = (payload.location or "").strip()
    if not location:
        raise HTTPException(status_code=400, detail="Inspection location is required.")
    inspection_type = (payload.inspection_type or "").strip()
    if inspection_type not in VALID_INSPECTION_TYPES:
        raise HTTPException(status_code=400, detail=f"inspection_type must be one of {', '.join(sorted(VALID_INSPECTION_TYPES))}.")

    session_id = f"SESSION-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    create_session(
        session_id=session_id,
        officer_id=user["user_id"],
        officer_name=user["name"],
        jurisdiction=jurisdiction,
        location=location,
        inspection_type=inspection_type,
        notes=(payload.notes or "").strip(),
    )
    return get_session_summary(session_id)

# API: List this officer's own inspection sessions (never a client-supplied officer id).
@app.get("/api/sessions")
async def get_sessions(
    status: Optional[str] = Query(None, description="Filter by session status: ACTIVE or CLOSED."),
    limit: int = 100,
    user: dict = Depends(require_role("inspector")),
):
    return list_sessions(officer_id=user["user_id"], status=status, limit=limit)

# API: Close an inspection session (marks it CLOSED; no further products/scans may be added).
@app.post("/api/sessions/{session_id}/close")
async def close_inspection_session(session_id: str, user: dict = Depends(require_role("inspector"))):
    sess = get_session_by_id(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Inspection session not found.")
    _enforce_session_access(sess, user, action="close")
    if sess["status"] != "ACTIVE":
        raise HTTPException(status_code=400, detail="This inspection session is already closed.")
    close_session(session_id)
    return get_session_summary(session_id)

# API: Add a product to an inspection session.
@app.post("/api/sessions/{session_id}/products")
async def add_session_product(session_id: str, payload: AddProductPayload, user: dict = Depends(require_role("inspector"))):
    sess = get_session_by_id(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Inspection session not found.")
    _enforce_session_access(sess, user, action="add a product to")
    if sess["status"] != "ACTIVE":
        raise HTTPException(status_code=400, detail="Cannot add a product to a closed inspection session.")
    product_name = (payload.product_name or "").strip()
    if not product_name:
        raise HTTPException(status_code=400, detail="Product name is required.")
    if payload.total_quantity <= 0:
        raise HTTPException(status_code=400, detail="Total quantity available must be greater than zero.")
    if payload.sample_target <= 0:
        raise HTTPException(status_code=400, detail="Number of samples to inspect must be greater than zero.")

    product_id = f"PROD-{uuid.uuid4().hex[:8].upper()}"
    add_product(
        product_id=product_id,
        session_id=session_id,
        product_name=product_name,
        manufacturer=(payload.manufacturer or "").strip(),
        total_quantity=payload.total_quantity,
        sample_target=payload.sample_target,
        category=(payload.category or "").strip(),
        location=(payload.location or "").strip(),
        notes=(payload.notes or "").strip(),
    )
    return get_session_summary(session_id)

# API: Confirm seizure of an inspection session.
# Seizure is the trigger for statutory reporting: the session's non-compliant
# products are grouped by manufacturer, one violation report is generated per
# manufacturer, dispatched to their registered contact, and the outcome recorded.
@app.post("/api/sessions/{session_id}/seize")
async def seize_session(session_id: str, user: dict = Depends(require_role("inspector"))):
    sess = get_session_by_id(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Inspection session not found.")
    _enforce_session_access(sess, user, action="record a seizure on")
    if sess.get("seized"):
        raise HTTPException(status_code=400, detail="This inspection has already been seized.")

    summary = get_session_summary(session_id)
    grouped = group_products_by_manufacturer(summary)
    if not grouped:
        raise HTTPException(
            status_code=400,
            detail="No non-compliant products in this inspection -- there is nothing to seize or report.",
        )

    seized_at = mark_session_seized(session_id)
    summary["seized_at"] = seized_at

    officer_row = get_user_by_id(user["user_id"]) or {}
    officer = {"name": officer_row.get("name") or user.get("name"), "officer_id": officer_row.get("officer_id")}

    generated = []
    for manufacturer, products in grouped.items():
        brand_hints = [manufacturer]
        for p in products:
            for scan in p.get("scans", []):
                ext = scan.get("extracted_data") or {}
                for key in ("brand", "manufacturer"):
                    val = (ext.get(key) or {}).get("value")
                    if val:
                        brand_hints.append(str(val))
        contact = find_manufacturer_contact(*brand_hints)

        report = build_violation_report(summary, manufacturer, products, officer, contact)
        report_id = f"VR-{uuid.uuid4().hex[:10].upper()}"
        report["report_id"] = report_id
        html_body = render_violation_report_html(report)
        report["report_html_path"] = write_violation_report_html(report, report_id)

        outcome = dispatch_violation_report(report, html_body, report.get("recipient_email"))
        report["dispatch_status"] = outcome["status"]
        report["dispatch_detail"] = outcome["detail"]
        report["sent_at"] = outcome["sent_at"]
        report["dispatch_mode"] = outcome["mode"]
        save_violation_report(report)
        generated.append({
            "report_id": report_id,
            "manufacturer": manufacturer,
            "brand": report.get("brand"),
            "recipient_email": report.get("recipient_email"),
            "dispatch_status": outcome["status"],
            "dispatch_detail": outcome["detail"],
            "sent_at": outcome["sent_at"],
            "violations": len(report.get("violations", [])),
            "products_inspected": report.get("products_inspected"),
        })

    return {
        "message": f"Seizure recorded. {len(generated)} manufacturer violation report(s) generated.",
        "seized_at": seized_at,
        "dispatch_mode": dispatch_mode(),
        "reports": generated,
        "session": get_session_summary(session_id),
    }

# API: Violation reports generated for a session
@app.get("/api/sessions/{session_id}/violation-reports")
async def session_violation_reports(session_id: str, user: dict = Depends(require_role("inspector"))):
    sess = get_session_by_id(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Inspection session not found.")
    _enforce_session_access(sess, user)
    return list_violation_reports(session_id)

# API: Rendered violation notice (opened via <a href>, hence the flexible auth)
@app.get("/api/violation-reports/{report_id}/html", response_class=HTMLResponse)
async def violation_report_html(report_id: str, user: dict = Depends(get_current_user_flexible)):
    record = get_violation_report(report_id)
    if not record:
        raise HTTPException(status_code=404, detail="Violation report not found.")
    sess = get_session_by_id(record["session_id"])
    if sess:
        _enforce_session_access(sess, user, action="view violation reports for")
    return HTMLResponse(content=render_violation_report_html(record["report"]))

# API: Registered manufacturer contact directory
@app.get("/api/manufacturer-contacts")
async def manufacturer_contacts(user: dict = Depends(get_current_user)):
    return list_manufacturer_contacts()

# API: Full aggregate summary for one inspection session (products, sample progress,
# pass/review/non-compliant tallies, every linked sample record). Powers both the
# in-progress workspace and the closed-session summary screen.
@app.get("/api/sessions/{session_id}/summary")
async def get_inspection_session_summary(session_id: str, user: dict = Depends(require_role("inspector"))):
    sess = get_session_by_id(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Inspection session not found.")
    _enforce_session_access(sess, user)
    return get_session_summary(session_id)

# API: List Inspections (with optional text search + status filter)
@app.get("/api/inspections")
async def get_inspections(
    limit: int = 50,
    search: Optional[str] = Query(None, description="Case-insensitive text filter over id, filename, product, brand, manufacturer."),
    status: Optional[str] = Query(None, description="Filter by compliance status: COMPLIANT, NON_COMPLIANT, REVIEW_REQUIRED."),
    inspector_id: Optional[str] = Query(None, description="Manager-only: filter the list to one inspector. Ignored for inspector-role callers."),
    user: dict = Depends(get_current_user),
):
    if user["role"] == "inspector":
        # Forced server-side to the caller's own id -- never trust a client-supplied
        # inspector_id here, even if one were passed.
        effective_inspector_id = user["user_id"]
    elif user["role"] == "manager":
        effective_inspector_id = inspector_id  # None = all inspectors
    else:
        raise HTTPException(status_code=403, detail="Unrecognized role.")
    return list_inspections(limit=limit, search=search, status=status, inspector_id=effective_inspector_id)

# API: Single Inspection Detail
@app.get("/api/inspections/{inspection_id}", response_model=InspectionResponse)
async def get_inspection(inspection_id: str, user: dict = Depends(get_current_user)):
    insp = get_inspection_by_id(inspection_id)
    if not insp:
        raise HTTPException(status_code=404, detail="Inspection not found.")
    _enforce_record_access(insp, user)
    return insp

# API: Officer Manual Verification -- the INSPECTOR's own routine post-scan
# decision (approve / rescan / hold) on their own scan, right after scanning.
# This is a different action from a manager's override below: different actor,
# different trigger, different audit trail. Do not merge the two code paths.
@app.post("/api/inspections/{inspection_id}/review")
async def submit_officer_review(
    inspection_id: str,
    payload: ManualReviewPayload,
    user: dict = Depends(require_role("inspector")),
):
    insp = get_inspection_by_id(inspection_id)
    if not insp:
        raise HTTPException(status_code=404, detail="Inspection not found.")
    _enforce_record_access(insp, user, action="record a decision on")

    review_data = {
        "officer_id": payload.officer_id,
        "officer_name": payload.officer_name,
        "decision": payload.decision,
        "officer_notes": payload.officer_notes,
        "reviewed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    update_officer_review(inspection_id, review_data)
    updated = get_inspection_by_id(inspection_id)
    return {"message": "Verification saved successfully.", "inspection": updated}

# API: Manager Verdict Override -- a MANAGER changing an ALREADY-RECORDED
# verdict. Requires a written reason and is logged as its own audit event
# (backend/database/db.py: record_override / the `overrides` table),
# distinct from the inspector's officer_review above.
@app.post("/api/inspections/{inspection_id}/override")
async def override_verdict(
    inspection_id: str,
    payload: OverridePayload,
    user: dict = Depends(require_role("manager")),
):
    insp = get_inspection_by_id(inspection_id)
    if not insp:
        raise HTTPException(status_code=404, detail="Inspection not found.")

    new_status = (payload.new_status or "").strip().upper()
    if new_status not in {"COMPLIANT", "NON_COMPLIANT", "REVIEW_REQUIRED"}:
        raise HTTPException(
            status_code=400,
            detail="new_status must be one of COMPLIANT, NON_COMPLIANT, REVIEW_REQUIRED."
        )
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="A written reason is required to override a verdict.")

    record_override(
        inspection_id=inspection_id,
        manager_id=user["user_id"],
        manager_name=user.get("name") or user["user_id"],
        previous_status=insp["overall_status"],
        new_status=new_status,
        reason=reason,
    )
    updated = get_inspection_by_id(inspection_id)
    return {"message": "Verdict overridden.", "inspection": updated}

# API: Rules Configuration (both roles may read; only managers may change enforcement)
@app.get("/api/rules")
async def list_rules(user: dict = Depends(get_current_user)):
    return get_all_rules()

@app.put("/api/rules/{rule_id}")
async def modify_rule(rule_id: str, update: RuleConfigUpdate, user: dict = Depends(require_role("manager"))):
    update_rule(rule_id, update.enabled, update.required, update.severity)
    return {"message": f"Rule {rule_id} updated successfully."}

# API: Printable Compliance Report
@app.get("/api/reports/{inspection_id}/html", response_class=HTMLResponse)
async def get_compliance_report_html(inspection_id: str, user: dict = Depends(get_current_user_flexible)):
    insp = get_inspection_by_id(inspection_id)
    if not insp:
        raise HTTPException(status_code=404, detail="Inspection not found.")
    _enforce_record_access(insp, user, action="export a report for")
    html = generate_html_report(insp)
    return HTMLResponse(content=html)

# API: Printable Compliance Report (PDF)
@app.get("/api/reports/{inspection_id}/pdf")
async def get_compliance_report_pdf(inspection_id: str, user: dict = Depends(get_current_user_flexible)):
    insp = get_inspection_by_id(inspection_id)
    if not insp:
        raise HTTPException(status_code=404, detail="Inspection not found.")
    _enforce_record_access(insp, user, action="export a report for")
    try:
        pdf_bytes = generate_pdf_report(insp)
    except RuntimeError as e:
        raise HTTPException(status_code=501, detail=str(e))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="report_{inspection_id}.pdf"'}
    )

# API: Editable Compliance Report (Word / .docx) -- unlike the PDF/HTML certificates,
# this is meant to be opened and edited (e.g. an officer annotating findings).
@app.get("/api/reports/{inspection_id}/docx")
async def get_compliance_report_docx(inspection_id: str, user: dict = Depends(get_current_user_flexible)):
    insp = get_inspection_by_id(inspection_id)
    if not insp:
        raise HTTPException(status_code=404, detail="Inspection not found.")
    _enforce_record_access(insp, user, action="export a report for")
    try:
        docx_bytes = generate_docx_report(insp)
    except RuntimeError as e:
        raise HTTPException(status_code=501, detail=str(e))
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="report_{inspection_id}.docx"'}
    )

# API: Editable / machine-readable export (JSON or CSV)
@app.get("/api/reports/{inspection_id}/export")
async def export_compliance_report(
    inspection_id: str,
    format: str = Query("json", pattern="^(json|csv)$", description="Export format: 'json' or 'csv'."),
    user: dict = Depends(get_current_user_flexible),
):
    insp = get_inspection_by_id(inspection_id)
    if not insp:
        raise HTTPException(status_code=404, detail="Inspection not found.")
    _enforce_record_access(insp, user, action="export a report for")

    if format == "csv":
        return Response(
            content=generate_csv_export(insp),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="inspection_{inspection_id}.csv"'}
        )
    return JSONResponse(
        content=generate_json_export(insp),
        headers={"Content-Disposition": f'attachment; filename="inspection_{inspection_id}.json"'}
    )

# Static file serving:

class NoCacheStaticFiles(StaticFiles):
    """StaticFiles that always forces browser revalidation (Cache-Control: no-cache).

    Plain StaticFiles sends only ETag/Last-Modified with no Cache-Control, which
    browsers are free to treat as heuristically cacheable -- a normal reload can
    then keep serving app.js/index.html from disk cache with zero request to the
    server, silently masking any edit to the frontend (e.g. auth/token bugs) until
    a hard refresh. This does not disable caching (ETag-based 304s still happen),
    it just forces a round-trip to check freshness on every load.
    """
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response

# 1. Dataset images
app.mount("/dataset-images", StaticFiles(directory=str(DATASET_DIR)), name="dataset-images")
# 2. Thumbnails
app.mount("/thumbnails", StaticFiles(directory=str(THUMBNAIL_DIR)), name="thumbnails")
# 3. Dedicated Mobile Web Application (phone-optimised UI at /m)
app.mount("/m", NoCacheStaticFiles(directory=str(MOBILE_DIR), html=True), name="mobile")
# 4. Desktop Frontend Web Application (catch-all, must be mounted last)
app.mount("/", NoCacheStaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
