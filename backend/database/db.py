import sqlite3
import json
import re
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from backend.config import DATABASE_PATH, DEFAULT_RULES
from backend.auth import hash_password

VALID_STATUSES = {"COMPLIANT", "NON_COMPLIANT", "REVIEW_REQUIRED"}

def get_connection():
    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Inspections table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inspections (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            image_filename TEXT NOT NULL,
            image_url TEXT NOT NULL,
            thumbnail_url TEXT NOT NULL,
            overall_status TEXT NOT NULL,
            overall_confidence REAL NOT NULL,
            summary TEXT NOT NULL,
            extracted_json TEXT NOT NULL,
            rules_json TEXT NOT NULL,
            violations_json TEXT NOT NULL,
            readability_json TEXT NOT NULL,
            ocr_boxes_json TEXT NOT NULL,
            clip_categories_json TEXT NOT NULL,
            officer_review_json TEXT
        )
    """)

    # Catalog products table (pre-indexed dataset items)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS catalog_products (
            id TEXT PRIMARY KEY,
            filename TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            brand TEXT,
            thumbnail_url TEXT NOT NULL,
            embedding_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # Rule configuration table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rule_settings (
            rule_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            legal_reference TEXT NOT NULL,
            field TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            required INTEGER NOT NULL DEFAULT 1,
            severity TEXT NOT NULL,
            description TEXT
        )
    """)

    # Users table (two-portal RBAC: 'inspector' / 'manager')
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # Manager verdict-override audit log. Deliberately a separate table from
    # officer_review_json on `inspections`: an override is a distinct event
    # (different actor, different trigger, after-the-fact) from the
    # inspector's own post-scan decision, and must be independently auditable.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS overrides (
            id TEXT PRIMARY KEY,
            inspection_id TEXT NOT NULL,
            manager_id TEXT NOT NULL,
            manager_name TEXT,
            previous_status TEXT,
            new_status TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # Officer inspection-session workflow (additive, on top of the single-scan
    # `inspections` table -- an officer's one premises visit ("inspection session")
    # can contain multiple products, each with multiple scanned sample packages.
    # No FOREIGN KEY constraints, matching every other table in this file.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inspection_sessions (
            id TEXT PRIMARY KEY,
            officer_id TEXT NOT NULL,
            officer_name TEXT NOT NULL,
            jurisdiction TEXT NOT NULL,
            location TEXT NOT NULL,
            inspection_type TEXT NOT NULL,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL,
            closed_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inspection_products (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            manufacturer TEXT,
            total_quantity INTEGER NOT NULL DEFAULT 0,
            sample_target INTEGER NOT NULL DEFAULT 0,
            category TEXT,
            location TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_product_scans (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            inspection_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
    """)
    # Manufacturer / brand contact directory. The registered official address a
    # statutory violation report is dispatched to when an officer confirms seizure.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS manufacturer_contacts (
            id TEXT PRIMARY KEY,
            manufacturer TEXT NOT NULL,
            brand TEXT,
            contact_email TEXT NOT NULL,
            contact_name TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # Violation reports generated on seizure, one per manufacturer implicated in the
    # session, plus the dispatch audit trail (recipient + sent timestamp + status).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS violation_reports (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            manufacturer TEXT NOT NULL,
            brand TEXT,
            recipient_email TEXT,
            dispatch_status TEXT NOT NULL,
            dispatch_detail TEXT,
            sent_at TEXT,
            report_json TEXT NOT NULL,
            report_html_path TEXT,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vreports_session ON violation_reports(session_id)")

    # Seed the demo manufacturer directory (idempotent -- only when empty).
    cursor.execute("SELECT COUNT(*) AS cnt FROM manufacturer_contacts")
    if cursor.fetchone()["cnt"] == 0:
        now_seed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for mfr, brand, email, contact in [
            ("Britannia Industries Ltd", "Britannia", "legal.metrology@britannia.example.in", "Regulatory Affairs"),
            ("Hindustan Unilever Limited", "HUL", "compliance@hul.example.in", "Statutory Compliance Cell"),
            ("Nestle India Limited", "Nestle", "regulatory@nestle.example.in", "Regulatory Affairs"),
            ("ITC Limited", "Sunfeast", "lm.compliance@itc.example.in", "Legal Metrology Desk"),
            ("Parle Products Pvt Ltd", "Parle", "compliance@parle.example.in", "Compliance Officer"),
            ("Tata Consumer Products", "Tata", "regulatory@tataconsumer.example.in", "Regulatory Affairs"),
            ("Mondelez India Foods", "Cadbury", "compliance@mondelez.example.in", "Compliance Team"),
            ("Amul (GCMMF)", "Amul", "quality@amul.example.in", "Quality & Compliance"),
        ]:
            cursor.execute(
                """INSERT INTO manufacturer_contacts (id, manufacturer, brand, contact_email, contact_name, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (f"MFR-{uuid.uuid4().hex[:8].upper()}", mfr, brand, email, contact, now_seed),
            )
        print("[Database] Seeded manufacturer contact directory (8 entries).")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_session ON inspection_products(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_scans_product ON session_product_scans(product_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_scans_session ON session_product_scans(session_id)")

    # Seed demo accounts on first run (idempotent -- only if the table is empty).
    cursor.execute("SELECT COUNT(*) as cnt FROM users")
    if cursor.fetchone()["cnt"] == 0:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        seed_users = [
            ("u-insp-1", "Ananya Sharma", "inspector1@trace.gov", "Inspector@123", "inspector"),
            ("u-insp-2", "Rohit Verma", "inspector2@trace.gov", "Inspector@123", "inspector"),
            ("u-mgr-1", "Priya Nair", "manager1@trace.gov", "Manager@123", "manager"),
        ]
        for uid, name, email, pw, role in seed_users:
            cursor.execute(
                "INSERT INTO users (id, name, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, name, email, hash_password(pw), role, now),
            )
        print("[Database] Seeded demo accounts: inspector1@trace.gov / inspector2@trace.gov (Inspector@123), "
              "manager1@trace.gov (Manager@123)")

    # Populate default rules if empty
    cursor.execute("SELECT COUNT(*) as cnt FROM rule_settings")
    if cursor.fetchone()["cnt"] == 0:
        for r in DEFAULT_RULES:
            cursor.execute("""
                INSERT INTO rule_settings (rule_id, title, legal_reference, field, enabled, required, severity, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (r["rule_id"], r["title"], r["legal_reference"], r["field"], 1, 1 if r.get("required", True) else 0, r["severity"], r["description"]))
    else:
        # Keep statutory metadata (title / legal reference / description) in sync with the
        # code without disturbing officer-configurable enabled / required / severity.
        for r in DEFAULT_RULES:
            cursor.execute("""
                UPDATE rule_settings
                SET title = ?, legal_reference = ?, field = ?, description = ?
                WHERE rule_id = ?
            """, (r["title"], r["legal_reference"], r["field"], r["description"], r["rule_id"]))

    # Migration for inspections table (back_image_url, is_dual_panel)
    cursor.execute("PRAGMA table_info(inspections)")
    cols = [col[1] for col in cursor.fetchall()]
    if "back_image_url" not in cols:
        try:
            cursor.execute("ALTER TABLE inspections ADD COLUMN back_image_url TEXT")
        except Exception:
            pass
    if "is_dual_panel" not in cols:
        try:
            cursor.execute("ALTER TABLE inspections ADD COLUMN is_dual_panel INTEGER DEFAULT 0")
        except Exception:
            pass
    if "inspector_id" not in cols:
        try:
            cursor.execute("ALTER TABLE inspections ADD COLUMN inspector_id TEXT")
        except Exception:
            pass

    # Migration for users table: jurisdiction (set once by the officer, then
    # auto-populated every session) and officer_id (the human-readable service
    # number the officer signs in with, e.g. "LMO-001" -- distinct from the
    # internal primary key `id`).
    cursor.execute("PRAGMA table_info(users)")
    user_cols = [col[1] for col in cursor.fetchall()]
    if "jurisdiction" not in user_cols:
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN jurisdiction TEXT")
        except Exception:
            pass
    if "officer_id" not in user_cols:
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN officer_id TEXT")
        except Exception:
            pass

    # Migration for inspection_sessions: seizure state (set when an officer confirms
    # seizure, which triggers the manufacturer violation reports).
    cursor.execute("PRAGMA table_info(inspection_sessions)")
    sess_cols = [col[1] for col in cursor.fetchall()]
    for col_name, col_type in (("seized", "INTEGER DEFAULT 0"), ("seized_at", "TEXT")):
        if col_name not in sess_cols:
            try:
                cursor.execute(f"ALTER TABLE inspection_sessions ADD COLUMN {col_name} {col_type}")
            except Exception:
                pass

    # Backfill service numbers for the seeded demo accounts (idempotent: only
    # fills rows that don't have one yet).
    for uid, oid in (("u-insp-1", "LMO-001"), ("u-insp-2", "LMO-002"), ("u-mgr-1", "CLM-001")):
        cursor.execute(
            "UPDATE users SET officer_id = ? WHERE id = ? AND (officer_id IS NULL OR officer_id = '')",
            (oid, uid),
        )

    conn.commit()
    conn.close()
    print("[Database] Schema initialized at:", DATABASE_PATH)

def save_inspection(inspection_data: Dict[str, Any]):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO inspections (
            id, timestamp, image_filename, image_url, thumbnail_url,
            overall_status, overall_confidence, summary,
            extracted_json, rules_json, violations_json,
            readability_json, ocr_boxes_json, clip_categories_json, officer_review_json,
            back_image_url, is_dual_panel, inspector_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        inspection_data["inspection_id"],
        inspection_data["timestamp"],
        inspection_data["image_filename"],
        inspection_data["image_url"],
        inspection_data["thumbnail_url"],
        inspection_data["overall_status"],
        inspection_data["overall_confidence"],
        inspection_data["summary"],
        json.dumps(inspection_data["extracted_data"]),
        json.dumps(inspection_data["rule_results"]),
        json.dumps(inspection_data["violations"]),
        json.dumps(inspection_data["readability"]),
        json.dumps(inspection_data["ocr_boxes"]),
        json.dumps(inspection_data["clip_categories"]),
        json.dumps(inspection_data.get("officer_verification")) if inspection_data.get("officer_verification") else None,
        inspection_data.get("back_image_url"),
        1 if inspection_data.get("is_dual_panel") else 0,
        inspection_data.get("inspector_id")
    ))
    conn.commit()
    conn.close()

def get_inspection_by_id(inspection_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inspections WHERE id = ?", (inspection_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return _format_inspection_row(row, include_overrides=True)

def _inspection_matches(rec: Dict[str, Any], term: str) -> bool:
    """Case-insensitive substring match across identifying fields + key declarations.

    Product name / brand / manufacturer live inside the extracted_json blob, so the
    text search is applied in Python after the row is formatted rather than in SQL.
    """
    extracted = rec.get("extracted_data", {}) or {}

    def field_val(key: str) -> str:
        fld = extracted.get(key) or {}
        return str(fld.get("value") or "")

    haystack = " ".join([
        str(rec.get("inspection_id", "")),
        str(rec.get("image_filename", "")),
        str(rec.get("summary", "")),
        str(rec.get("overall_status", "")),
        field_val("product_name"),
        field_val("brand"),
        field_val("manufacturer"),
        field_val("category"),
    ]).lower()
    return term in haystack


def list_inspections(
    limit: int = 50,
    search: Optional[str] = None,
    status: Optional[str] = None,
    inspector_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List inspection history, newest first.

    Args:
        limit: maximum number of records to return.
        search: optional case-insensitive text filter (id, filename, product, brand...).
        status: optional exact compliance-status filter (COMPLIANT / NON_COMPLIANT / REVIEW_REQUIRED).
        inspector_id: optional owner filter. The API layer forces this to the caller's own
            user_id for inspector-role requests (server-side, not client-supplied) and lets
            managers pass any inspector_id (or none, for "all inspectors").
    """
    conn = get_connection()
    cursor = conn.cursor()

    params: List[Any] = []
    conditions: List[str] = []
    if status and status.upper() in VALID_STATUSES:
        conditions.append("overall_status = ?")
        params.append(status.upper())
    if inspector_id:
        conditions.append("inspector_id = ?")
        params.append(inspector_id)

    sql = "SELECT * FROM inspections"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY timestamp DESC"

    search_term = (search or "").strip().lower()
    if search_term:
        # Widen the SQL working set; narrowing happens in Python (see _inspection_matches).
        sql += " LIMIT 1000"
    else:
        sql += " LIMIT ?"
        params.append(limit)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    records = [_format_inspection_row(r) for r in rows]

    if search_term:
        records = [r for r in records if _inspection_matches(r, search_term)][:limit]

    return records

def update_officer_review(inspection_id: str, review_data: Dict[str, Any]):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE inspections
        SET officer_review_json = ?,
            overall_status = CASE 
                WHEN ? = 'APPROVED' THEN 'COMPLIANT'
                WHEN ? = 'REJECTED_NON_COMPLIANT' THEN 'NON_COMPLIANT'
                ELSE overall_status
            END
        WHERE id = ?
    """, (json.dumps(review_data), review_data.get("decision"), review_data.get("decision"), inspection_id))
    conn.commit()
    conn.close()

def record_override(
    inspection_id: str,
    manager_id: str,
    manager_name: str,
    previous_status: str,
    new_status: str,
    reason: str,
) -> str:
    """Logs a manager's verdict override as a distinct audit event (its own table,
    its own actor/timestamp/reason) and applies the new verdict to the inspection.
    This is intentionally NOT the same code path as update_officer_review()."""
    conn = get_connection()
    cursor = conn.cursor()
    override_id = f"OVR-{uuid.uuid4().hex[:10].upper()}"
    cursor.execute("""
        INSERT INTO overrides (id, inspection_id, manager_id, manager_name, previous_status, new_status, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        override_id, inspection_id, manager_id, manager_name,
        previous_status, new_status, reason,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))
    cursor.execute("UPDATE inspections SET overall_status = ? WHERE id = ?", (new_status, inspection_id))
    conn.commit()
    conn.close()
    return override_id


def get_overrides(inspection_id: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM overrides WHERE inspection_id = ? ORDER BY created_at ASC", (inspection_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def create_user(
    officer_name: str,
    officer_id: str,
    password: str,
    jurisdiction: str,
    role: str = "inspector",
) -> Dict[str, Any]:
    """Registers a new officer. Raises ValueError if the Officer ID is taken.

    `role` is NOT taken from user input by the API -- self-registration always
    creates a field officer. Controller access is provisioned separately, so a
    registrant can never grant themselves override/rule-config powers.
    """
    officer_id = officer_id.strip()
    if get_user_by_login(officer_id):
        raise ValueError("That Officer ID is already registered.")

    user_id = f"u-{uuid.uuid4().hex[:10]}"
    # `users.email` is NOT NULL UNIQUE in the existing schema but registration
    # doesn't collect one, so derive a stable internal address from the service number.
    synthetic_email = f"{officer_id.lower()}@trace.local"
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO users (id, name, email, password_hash, role, created_at, jurisdiction, officer_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id, officer_name.strip(), synthetic_email, hash_password(password), role,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), jurisdiction.strip(), officer_id,
        ),
    )
    conn.commit()
    conn.close()
    return get_user_by_id(user_id)


def get_user_by_login(identifier: str) -> Optional[Dict[str, Any]]:
    """Resolve a sign-in identifier to a user row.

    Officers sign in with their service number ("LMO-001"); the seeded demo
    accounts and older clients still use the e-mail address, so both are
    accepted. Matching is case-insensitive on either column.
    """
    ident = (identifier or "").strip()
    if not ident:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM users WHERE lower(officer_id) = lower(?) OR lower(email) = lower(?)",
        (ident, ident),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def set_user_jurisdiction(user_id: str, jurisdiction: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET jurisdiction = ? WHERE id = ?", (jurisdiction, user_id))
    conn.commit()
    conn.close()


# ================= Officer Inspection Sessions =================
# An "inspection session" is one officer's visit to a premises; it can contain
# multiple products, each with multiple scanned sample packages (each sample is
# an ordinary row in the existing `inspections` table, linked here). No new
# compliance logic lives in this section -- pass/review/non-compliant counts are
# pure tallies of each sample's already-computed `overall_status`.

def create_session(
    session_id: str, officer_id: str, officer_name: str, jurisdiction: str,
    location: str, inspection_type: str, notes: str,
) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO inspection_sessions (
            id, officer_id, officer_name, jurisdiction, location, inspection_type,
            notes, status, created_at, closed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, NULL)
    """, (
        session_id, officer_id, officer_name, jurisdiction, location, inspection_type,
        notes, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))
    conn.commit()
    conn.close()


def get_session_by_id(session_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inspection_sessions WHERE id = ?", (session_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def list_sessions(officer_id: Optional[str] = None, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    params: List[Any] = []
    conditions: List[str] = []
    if officer_id:
        conditions.append("s.officer_id = ?")
        params.append(officer_id)
    if status:
        conditions.append("s.status = ?")
        params.append(status.upper())
    sql = """
        SELECT s.*, (SELECT COUNT(*) FROM inspection_products p WHERE p.session_id = s.id) AS product_count
        FROM inspection_sessions s
    """
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY s.created_at DESC LIMIT ?"
    params.append(limit)
    cursor.execute(sql, params)
    sessions = [dict(r) for r in cursor.fetchall()]

    # Product-wise compliant / non-compliant tallies for every session in one pass
    # (rolling each product up from its samples' statuses), so the session-wise
    # history list doesn't have to call get_session_summary per row.
    cursor.execute(
        """
        SELECT p.session_id AS session_id,
               p.id         AS product_id,
               COUNT(i.id)  AS sample_count,
               SUM(CASE WHEN i.overall_status = 'NON_COMPLIANT'   THEN 1 ELSE 0 END) AS nc,
               SUM(CASE WHEN i.overall_status = 'REVIEW_REQUIRED' THEN 1 ELSE 0 END) AS rv
        FROM inspection_products p
        LEFT JOIN session_product_scans s ON s.product_id = p.id
        LEFT JOIN inspections i           ON i.id = s.inspection_id
        GROUP BY p.id
        """
    )
    tally: Dict[str, Dict[str, int]] = {}
    for r in cursor.fetchall():
        bucket = tally.setdefault(r["session_id"], {"compliant": 0, "non_compliant": 0, "review": 0, "not_inspected": 0})
        if (r["sample_count"] or 0) == 0:
            bucket["not_inspected"] += 1
        elif (r["nc"] or 0) > 0:
            bucket["non_compliant"] += 1
        elif (r["rv"] or 0) > 0:
            bucket["review"] += 1
        else:
            bucket["compliant"] += 1
    conn.close()

    for s in sessions:
        b = tally.get(s["id"], {})
        s["compliant_products"] = b.get("compliant", 0)
        s["non_compliant_products"] = b.get("non_compliant", 0)
        s["review_products"] = b.get("review", 0)
        s["not_inspected_products"] = b.get("not_inspected", 0)
    return sessions


def close_session(session_id: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE inspection_sessions SET status = 'CLOSED', closed_at = ? WHERE id = ?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), session_id),
    )
    conn.commit()
    conn.close()


def add_product(
    product_id: str, session_id: str, product_name: str, manufacturer: str,
    total_quantity: int, sample_target: int, category: str, location: str, notes: str,
) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO inspection_products (
            id, session_id, product_name, manufacturer, total_quantity, sample_target,
            category, location, notes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        product_id, session_id, product_name, manufacturer, total_quantity, sample_target,
        category, location, notes, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))
    conn.commit()
    conn.close()


def list_products(session_id: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inspection_products WHERE session_id = ? ORDER BY created_at ASC", (session_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_product_by_id(product_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inspection_products WHERE id = ?", (product_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def link_scan_to_product(session_id: str, product_id: str, inspection_id: str) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    link_id = f"LINK-{uuid.uuid4().hex[:10].upper()}"
    cursor.execute("""
        INSERT INTO session_product_scans (id, session_id, product_id, inspection_id, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (link_id, session_id, product_id, inspection_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    return link_id


def get_product_scans(product_id: str) -> List[Dict[str, Any]]:
    """Every scanned sample linked to this product, as full inspection records
    (reusing the existing, untouched get_inspection_by_id -- no duplicated
    JSON-parsing/shaping logic)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT inspection_id FROM session_product_scans WHERE product_id = ? ORDER BY created_at ASC",
        (product_id,),
    )
    ids = [r["inspection_id"] for r in cursor.fetchall()]
    conn.close()
    scans = []
    for insp_id in ids:
        rec = get_inspection_by_id(insp_id)
        if rec:
            scans.append(rec)
    return scans


def get_product_progress(product_id: str) -> Optional[Dict[str, Any]]:
    """Sample-count progress + pass/review/non-compliant tallies for one product.

    This is pure counting over each sample's already-computed `overall_status` --
    no new compliance decision is made here. Uninspected stock is never implied
    to be compliant; it is simply the stock not yet sampled.
    """
    product = get_product_by_id(product_id)
    if not product:
        return None
    scans = get_product_scans(product_id)
    samples_inspected = len(scans)
    pass_count = sum(1 for s in scans if s.get("overall_status") == "COMPLIANT")
    review_count = sum(1 for s in scans if s.get("overall_status") == "REVIEW_REQUIRED")
    non_compliant_count = sum(1 for s in scans if s.get("overall_status") == "NON_COMPLIANT")
    total_quantity = product.get("total_quantity") or 0
    sample_target = product.get("sample_target") or 0

    # Product-level verdict, rolled up from its samples' existing statuses -- a single
    # non-compliant sample makes the product non-compliant, since the officer has
    # physical proof of at least one violating package.
    if samples_inspected == 0:
        product_status = "NOT_INSPECTED"
    elif non_compliant_count > 0:
        product_status = "NON_COMPLIANT"
    elif review_count > 0:
        product_status = "REVIEW_REQUIRED"
    else:
        product_status = "COMPLIANT"

    return {
        **product,
        "samples_inspected": samples_inspected,
        "remaining_samples": max(0, sample_target - samples_inspected),
        "uninspected_stock": max(0, total_quantity - samples_inspected),
        "pass_count": pass_count,
        "review_count": review_count,
        "non_compliant_count": non_compliant_count,
        "product_status": product_status,
    }


def get_session_summary(session_id: str) -> Optional[Dict[str, Any]]:
    """Full aggregate for one inspection session: session header + every product's
    progress/tallies + every product's linked sample records + running totals."""
    session = get_session_by_id(session_id)
    if not session:
        return None
    products = []
    totals = {
        "total_quantity": 0, "sample_target": 0, "samples_inspected": 0,
        "remaining_samples": 0, "uninspected_stock": 0,
        "pass_count": 0, "review_count": 0, "non_compliant_count": 0,
    }
    for p in list_products(session_id):
        progress = get_product_progress(p["id"])
        if not progress:
            continue
        progress["scans"] = get_product_scans(p["id"])
        products.append(progress)
        for key in totals:
            totals[key] += progress.get(key, 0)

    # Product-wise verdict tallies for the session (distinct from the sample-wise
    # tallies in `totals`): how many PRODUCTS came out compliant vs non-compliant.
    status_counts = {"COMPLIANT": 0, "NON_COMPLIANT": 0, "REVIEW_REQUIRED": 0, "NOT_INSPECTED": 0}
    for p in products:
        status_counts[p["product_status"]] = status_counts.get(p["product_status"], 0) + 1

    return {
        **session,
        "products": products,
        "totals": totals,
        "product_count": len(products),
        "compliant_products": status_counts["COMPLIANT"],
        "non_compliant_products": status_counts["NON_COMPLIANT"],
        "review_products": status_counts["REVIEW_REQUIRED"],
        "not_inspected_products": status_counts["NOT_INSPECTED"],
    }


# ================= Seizure & manufacturer violation reporting =================

def mark_session_seized(session_id: str) -> str:
    seized_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    conn.execute(
        "UPDATE inspection_sessions SET seized = 1, seized_at = ? WHERE id = ?",
        (seized_at, session_id),
    )
    conn.commit()
    conn.close()
    return seized_at


def list_manufacturer_contacts() -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM manufacturer_contacts ORDER BY manufacturer ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _normalise_org(text: str) -> str:
    """Strip OCR noise and corporate suffixes so 'BY HINDUSTAN UNILEVER LIMITED, Gujarat'
    and 'Hindustan Unilever Ltd.' compare equal enough to match."""
    t = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    t = re.sub(
        r"\b(by|mfd|mfg|manufactured|marketed|packed|pvt|private|ltd|limited|llp|inc|co|company|india|"
        r"industries|products|foods|consumer|inr)\b",
        " ", t,
    )
    return re.sub(r"\s+", " ", t).strip()


def find_manufacturer_contact(*candidates: str) -> Optional[Dict[str, Any]]:
    """Resolve an extracted brand/manufacturer string to a registered contact.

    Tries each candidate (brand and manufacturer text from the scans) against the
    directory, matching on normalised token overlap in either direction so partial
    OCR text still resolves. Returns None when nothing matches -- the caller then
    records the report as undeliverable rather than guessing a recipient.
    """
    directory = list_manufacturer_contacts()
    for raw in candidates:
        needle = _normalise_org(raw)
        if not needle:
            continue
        for row in directory:
            for field in (row.get("brand"), row.get("manufacturer")):
                key = _normalise_org(field or "")
                if not key:
                    continue
                if key in needle or needle in key:
                    return row
            # token-level fallback: any distinctive (4+ char) token in common
            keys = set(_normalise_org(row.get("manufacturer") or "").split())
            keys |= set(_normalise_org(row.get("brand") or "").split())
            if {t for t in needle.split() if len(t) >= 4} & {t for t in keys if len(t) >= 4}:
                return row
    return None


def save_violation_report(report: Dict[str, Any]) -> str:
    report_id = report.get("report_id") or f"VR-{uuid.uuid4().hex[:10].upper()}"
    conn = get_connection()
    conn.execute(
        """
        INSERT OR REPLACE INTO violation_reports (
            id, session_id, manufacturer, brand, recipient_email,
            dispatch_status, dispatch_detail, sent_at, report_json, report_html_path, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report_id, report["session_id"], report["manufacturer"], report.get("brand"),
            report.get("recipient_email"), report.get("dispatch_status", "PENDING"),
            report.get("dispatch_detail"), report.get("sent_at"),
            json.dumps(report), report.get("report_html_path"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()
    return report_id


def list_violation_reports(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    if session_id:
        rows = conn.execute(
            "SELECT * FROM violation_reports WHERE session_id = ? ORDER BY created_at DESC", (session_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM violation_reports ORDER BY created_at DESC LIMIT 200").fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["report"] = json.loads(d.pop("report_json"))
        except Exception:
            d["report"] = {}
            d.pop("report_json", None)
        out.append(d)
    return out


def get_violation_report(report_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM violation_reports WHERE id = ?", (report_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["report"] = json.loads(d.pop("report_json"))
    except Exception:
        d["report"] = {}
        d.pop("report_json", None)
    return d


def get_officer_metrics(officer_id: str) -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM inspection_sessions WHERE officer_id = ? AND status = 'ACTIVE'",
        (officer_id,),
    )
    active_sessions = cursor.fetchone()["cnt"]

    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM inspection_sessions WHERE officer_id = ? AND status = 'CLOSED'",
        (officer_id,),
    )
    completed_inspections = cursor.fetchone()["cnt"]

    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM inspections WHERE inspector_id = ? AND officer_review_json IS NULL",
        (officer_id,),
    )
    pending_reviews = cursor.fetchone()["cnt"]

    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM inspections WHERE inspector_id = ? AND overall_status = 'NON_COMPLIANT'",
        (officer_id,),
    )
    non_compliance_findings = cursor.fetchone()["cnt"]

    conn.close()
    return {
        "active_sessions": active_sessions,
        "completed_inspections": completed_inspections,
        "pending_reviews": pending_reviews,
        "non_compliance_findings": non_compliance_findings,
    }


def get_dashboard_metrics() -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as total FROM inspections")
    total = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) as cnt FROM inspections WHERE overall_status = 'COMPLIANT'")
    compliant = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM inspections WHERE overall_status = 'NON_COMPLIANT'")
    non_compliant = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM inspections WHERE overall_status = 'REVIEW_REQUIRED'")
    review_required = cursor.fetchone()["cnt"]

    # Category distribution
    cursor.execute("SELECT clip_categories_json FROM inspections")
    rows = cursor.fetchall()
    cat_counts = {}
    for r in rows:
        try:
            cats = json.loads(r["clip_categories_json"])
            if cats:
                top_cat = cats[0]["label"].split(" or ")[0].title()
                cat_counts[top_cat] = cat_counts.get(top_cat, 0) + 1
        except Exception:
            pass

    # Total violations count
    cursor.execute("SELECT violations_json FROM inspections")
    v_rows = cursor.fetchall()
    total_violations = 0
    for vr in v_rows:
        try:
            viols = json.loads(vr["violations_json"])
            total_violations += len(viols)
        except Exception:
            pass

    # 14-day inspection trend (grouped on the stored 'YYYY-MM-DD ...' timestamp)
    start_date = (datetime.now().date() - timedelta(days=13))
    cursor.execute(
        """
        SELECT substr(timestamp, 1, 10) AS day,
               COUNT(*) AS total,
               SUM(CASE WHEN overall_status = 'COMPLIANT' THEN 1 ELSE 0 END) AS compliant,
               SUM(CASE WHEN overall_status = 'NON_COMPLIANT' THEN 1 ELSE 0 END) AS non_compliant,
               SUM(CASE WHEN overall_status = 'REVIEW_REQUIRED' THEN 1 ELSE 0 END) AS review_required
        FROM inspections
        WHERE substr(timestamp, 1, 10) >= ?
        GROUP BY day
        """,
        (start_date.isoformat(),),
    )
    by_day = {r["day"]: r for r in cursor.fetchall()}
    trend_14d = []
    for i in range(14):
        d = (start_date + timedelta(days=i)).isoformat()
        row = by_day.get(d)
        trend_14d.append({
            "date": d,
            "total": row["total"] if row else 0,
            "compliant": (row["compliant"] or 0) if row else 0,
            "non_compliant": (row["non_compliant"] or 0) if row else 0,
            "review_required": (row["review_required"] or 0) if row else 0,
        })

    # Week-over-week compliance-rate delta (older 7 days vs most recent 7 days)
    def _bucket_rate(bucket):
        t = sum(x["total"] for x in bucket)
        c = sum(x["compliant"] for x in bucket)
        return (c / t * 100) if t else None
    _r_prev, _r_curr = _bucket_rate(trend_14d[:7]), _bucket_rate(trend_14d[7:])
    wow_compliance_delta = (
        round(_r_curr - _r_prev, 1) if (_r_prev is not None and _r_curr is not None) else None
    )

    # Average AI pipeline confidence
    cursor.execute("SELECT AVG(overall_confidence) AS a FROM inspections")
    avg_confidence = round((cursor.fetchone()["a"] or 0.0) * 100, 1)

    # Per-field detection rate + violation tally by failed-rule title
    cursor.execute("SELECT extracted_json, rules_json FROM inspections")
    detail_rows = cursor.fetchall()

    field_specs = [
        ("manufacturer", "Manufacturer"),
        ("mrp", "MRP"),
        ("net_quantity", "Net quantity"),
        ("mfg_date", "Mfg / pack date"),
        ("consumer_care", "Consumer care"),
        ("fssai_license", "FSSAI licence"),
        ("country_of_origin", "Country of origin"),
    ]
    field_hits = {k: 0 for k, _ in field_specs}
    fail_counts: Dict[str, int] = {}
    for dr in detail_rows:
        try:
            ext = json.loads(dr["extracted_json"])
            for k, _ in field_specs:
                if (ext.get(k) or {}).get("value"):
                    field_hits[k] += 1
        except Exception:
            pass
        try:
            for rr in json.loads(dr["rules_json"]):
                if rr.get("status") == "FAIL":
                    label = rr.get("title", "Other statutory failure")
                    fail_counts[label] = fail_counts.get(label, 0) + 1
        except Exception:
            pass

    denom = len(detail_rows) or 1
    field_detection = [
        {"key": k, "label": lbl, "pct": round(field_hits[k] / denom * 100)}
        for k, lbl in field_specs
    ]
    violation_types = sorted(
        ({"label": k, "count": v} for k, v in fail_counts.items()),
        key=lambda x: x["count"], reverse=True,
    )

    conn.close()

    compliance_rate = round((compliant / total * 100) if total > 0 else 100.0, 1)
    hold_rate = round((review_required / total * 100) if total > 0 else 0.0, 1)

    return {
        "total_inspections": total,
        "compliant_count": compliant,
        "non_compliant_count": non_compliant,
        "review_required_count": review_required,
        "compliance_rate": compliance_rate,
        "hold_rate": hold_rate,
        "wow_compliance_delta": wow_compliance_delta,
        "avg_confidence": avg_confidence,
        "total_violations_found": total_violations,
        "category_distribution": cat_counts,
        "field_detection": field_detection,
        "violation_types": violation_types,
        "trend_14d": trend_14d
    }

def get_all_rules() -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rule_settings")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def update_rule(rule_id: str, enabled: bool, required: bool, severity: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE rule_settings
        SET enabled = ?, required = ?, severity = ?
        WHERE rule_id = ?
    """, (1 if enabled else 0, 1 if required else 0, severity, rule_id))
    conn.commit()
    conn.close()

def save_catalog_product(item: Dict[str, Any]):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO catalog_products (
            id, filename, name, category, brand, thumbnail_url, embedding_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        item["id"],
        item["filename"],
        item["name"],
        item["category"],
        item.get("brand", ""),
        item["thumbnail_url"],
        json.dumps(item["embedding"]),
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()

def get_all_catalog_products() -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, filename, name, category, brand, thumbnail_url, embedding_json FROM catalog_products")
    rows = cursor.fetchall()
    conn.close()
    items = []
    for r in rows:
        d = dict(r)
        d["embedding"] = json.loads(d["embedding_json"])
        del d["embedding_json"]
        items.append(d)
    return items

def _format_inspection_row(row: sqlite3.Row, include_overrides: bool = False) -> Dict[str, Any]:
    row_keys = row.keys()
    insp_id = row["id"]
    return {
        "inspection_id": insp_id,
        "timestamp": row["timestamp"],
        "image_filename": row["image_filename"],
        "image_url": row["image_url"],
        "thumbnail_url": row["thumbnail_url"],
        "overall_status": row["overall_status"],
        "overall_confidence": row["overall_confidence"],
        "summary": row["summary"],
        "extracted_data": json.loads(row["extracted_json"]),
        "rule_results": json.loads(row["rules_json"]),
        "violations": json.loads(row["violations_json"]),
        "readability": json.loads(row["readability_json"]),
        "ocr_boxes": json.loads(row["ocr_boxes_json"]),
        "clip_categories": json.loads(row["clip_categories_json"]),
        "officer_verification": json.loads(row["officer_review_json"]) if row["officer_review_json"] else None,
        "back_image_url": row["back_image_url"] if "back_image_url" in row_keys and row["back_image_url"] else None,
        "is_dual_panel": bool(row["is_dual_panel"]) if "is_dual_panel" in row_keys and row["is_dual_panel"] else False,
        "inspector_id": row["inspector_id"] if "inspector_id" in row_keys else None,
        "override_history": get_overrides(insp_id) if include_overrides else [],
        "similar_products": []
    }
