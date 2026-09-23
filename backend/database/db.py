import sqlite3
import uuid
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from backend.config import DATABASE_PATH, DEFAULT_LANGUAGE

# Document lifecycle: uploaded -> processing -> review -> verified  (or failed)
DOCUMENT_STATUSES = {"uploaded", "processing", "review", "verified", "failed"}

SCHEMA = """
-- One uploaded file (PDF, image or camera photo).
CREATE TABLE IF NOT EXISTS documents (
    id                  TEXT PRIMARY KEY,
    filename            TEXT NOT NULL,
    original_path       TEXT NOT NULL,
    file_type           TEXT NOT NULL,              -- pdf | image | camera
    language            TEXT NOT NULL,              -- language pack key, e.g. 'ta'
    source_type         TEXT,                       -- modern_print | historical_print | ... (CLIP, once per document)
    source_confidence   REAL,
    source_manual       INTEGER NOT NULL DEFAULT 0, -- 1 = user overrode CLIP
    status              TEXT NOT NULL DEFAULT 'uploaded',
    page_count          INTEGER NOT NULL DEFAULT 0,
    error               TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

-- One page of a document, normalized to an image.
CREATE TABLE IF NOT EXISTS pages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id         TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number         INTEGER NOT NULL,
    image_path          TEXT,
    thumbnail_path      TEXT,
    width               INTEGER,
    height              INTEGER,
    extraction_method   TEXT NOT NULL DEFAULT 'pending', -- pending | text_layer | ocr
    text_layer_score    REAL,                       -- structural validity of the PDF text layer
    quality_json        TEXT,                       -- blur / contrast / skew metrics
    status              TEXT NOT NULL DEFAULT 'pending', -- pending | processing | ready | failed
    review_status       TEXT NOT NULL DEFAULT 'unreviewed', -- unreviewed | in_review | verified
    processed_at        TEXT,
    UNIQUE (document_id, page_number)
);

-- A layout region on a page (text block, column, title, marginal note).
CREATE TABLE IF NOT EXISTS regions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id             INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    region_type         TEXT NOT NULL DEFAULT 'text',
    x0 REAL, y0 REAL, x1 REAL, y1 REAL,             -- pixel coordinates on the page image
    reading_order       INTEGER NOT NULL DEFAULT 0
);

-- A text line: the unit of OCR, confidence, review and provenance.
-- ocr_text is what the machine read and is NEVER overwritten; verified_text is the human decision.
CREATE TABLE IF NOT EXISTS lines (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id             INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    region_id           INTEGER REFERENCES regions(id) ON DELETE SET NULL,
    line_order          INTEGER NOT NULL,           -- reading order within the page
    ocr_text            TEXT NOT NULL,
    confidence          REAL,
    engine              TEXT,                       -- paddle | tesseract | consensus | text_layer
    x0 REAL, y0 REAL, x1 REAL, y1 REAL,
    review_status       TEXT NOT NULL DEFAULT 'accepted', -- accepted | needs_review | verified | corrected
    verified_text       TEXT,
    created_at          TEXT NOT NULL
);

-- Alternative readings offered to the reviewer for an uncertain line.
CREATE TABLE IF NOT EXISTS candidates (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id             INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
    candidate_text      TEXT NOT NULL,
    score               REAL,
    rank                INTEGER NOT NULL,
    source              TEXT                        -- paddle | tesseract | lexicon | context
);

-- Audit trail: every human decision on a line.
CREATE TABLE IF NOT EXISTS verifications (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id             INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
    action              TEXT NOT NULL,              -- accept | choose | edit | reject
    previous_text       TEXT,
    new_text            TEXT,
    reviewer            TEXT,
    created_at          TEXT NOT NULL
);

-- Derived outputs from verified text: romanized (line level) and English (page level).
CREATE TABLE IF NOT EXISTS outputs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id             INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    line_id             INTEGER REFERENCES lines(id) ON DELETE CASCADE,
    kind                TEXT NOT NULL,              -- romanized | english
    text                TEXT NOT NULL,
    model               TEXT,
    created_at          TEXT NOT NULL
);

-- Background processing jobs (page-by-page progress, resumable).
CREATE TABLE IF NOT EXISTS jobs (
    id                  TEXT PRIMARY KEY,
    document_id         TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    status              TEXT NOT NULL DEFAULT 'queued', -- queued | running | done | failed
    total_pages         INTEGER NOT NULL DEFAULT 0,
    done_pages          INTEGER NOT NULL DEFAULT 0,
    current_step        TEXT,
    error               TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

-- Human-corrected line crops, collected as training data for future models (e.g. palm-leaf).
CREATE TABLE IF NOT EXISTS training_pairs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id             INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
    crop_path           TEXT NOT NULL,
    text                TEXT NOT NULL,
    source_type         TEXT,
    language            TEXT,
    created_at          TEXT NOT NULL
);

-- CLIP image embeddings are kept separately so the page schema remains
-- compatible with existing installations and future vector backends.
CREATE TABLE IF NOT EXISTS page_embeddings (
    page_id             INTEGER PRIMARY KEY REFERENCES pages(id) ON DELETE CASCADE,
    embedding_json      TEXT NOT NULL,
    model               TEXT,
    created_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pages_document ON pages(document_id, page_number);
CREATE INDEX IF NOT EXISTS idx_lines_page ON lines(page_id, line_order);
CREATE INDEX IF NOT EXISTS idx_lines_review ON lines(review_status);
CREATE INDEX IF NOT EXISTS idx_candidates_line ON candidates(line_id, rank);
CREATE INDEX IF NOT EXISTS idx_outputs_page ON outputs(page_id, kind);
CREATE INDEX IF NOT EXISTS idx_jobs_document ON jobs(document_id);
CREATE INDEX IF NOT EXISTS idx_page_embeddings_page ON page_embeddings(page_id);
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_connection():
    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
    # WAL lets the background pipeline write while the UI reads.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def list_tables() -> List[str]:
    conn = get_connection()
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall()
    conn.close()
    return sorted(r["name"] for r in rows)


# Documents

def new_document_id() -> str:
    return f"DOC-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"


def create_document(
    filename: str,
    original_path: str,
    file_type: str,
    language: str = DEFAULT_LANGUAGE,
    document_id: Optional[str] = None,
) -> Dict[str, Any]:
    doc_id = document_id or new_document_id()
    now = _now()
    conn = get_connection()
    conn.execute(
        """INSERT INTO documents (id, filename, original_path, file_type, language, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'uploaded', ?, ?)""",
        (doc_id, filename, original_path, file_type, language, now, now),
    )
    conn.commit()
    conn.close()
    return get_document(doc_id)


def get_document(document_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_documents(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT d.*,
                  (SELECT COUNT(*) FROM pages p WHERE p.document_id = d.id AND p.review_status = 'verified') AS verified_pages
           FROM documents d ORDER BY d.created_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_document_status(document_id: str, status: str, error: Optional[str] = None) -> None:
    if status not in DOCUMENT_STATUSES:
        raise ValueError(f"Unknown document status: {status}")
    conn = get_connection()
    conn.execute(
        "UPDATE documents SET status = ?, error = ?, updated_at = ? WHERE id = ?",
        (status, error, _now(), document_id),
    )
    conn.commit()
    conn.close()


def update_document_page_count(document_id: str, page_count: int) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE documents SET page_count = ?, updated_at = ? WHERE id = ?",
        (page_count, _now(), document_id),
    )
    conn.commit()
    conn.close()


def set_document_source_type(document_id: str, source_type: str, confidence: Optional[float], manual: bool = False) -> None:
    conn = get_connection()
    conn.execute(
        """UPDATE documents
           SET source_type = ?, source_confidence = ?, source_manual = ?, updated_at = ?
           WHERE id = ?""",
        (source_type, confidence, int(manual), _now(), document_id),
    )
    conn.commit()
    conn.close()


def delete_document(document_id: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    conn.commit()
    conn.close()


def upsert_page(document_id: str, page_number: int, **values: Any) -> int:
    allowed = {"image_path", "thumbnail_path", "width", "height", "extraction_method",
               "text_layer_score", "quality_json", "status", "review_status", "processed_at"}
    fields = {key: value for key, value in values.items() if key in allowed}
    conn = get_connection()
    existing = conn.execute(
        "SELECT id FROM pages WHERE document_id = ? AND page_number = ?",
        (document_id, page_number),
    ).fetchone()
    if existing:
        assignments = ", ".join(f"{key} = ?" for key in fields)
        if assignments:
            conn.execute(
                f"UPDATE pages SET {assignments} WHERE id = ?",
                (*fields.values(), existing["id"]),
            )
        page_id = existing["id"]
    else:
        columns = ["document_id", "page_number", *fields.keys()]
        placeholders = ", ".join("?" for _ in columns)
        cursor = conn.execute(
            f"INSERT INTO pages ({', '.join(columns)}) VALUES ({placeholders})",
            (document_id, page_number, *fields.values()),
        )
        page_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return int(page_id)


def save_page_embedding(page_id: int, embedding: Any, model: Optional[str] = None) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO page_embeddings (page_id, embedding_json, model, created_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(page_id) DO UPDATE SET embedding_json = excluded.embedding_json,
           model = excluded.model, created_at = excluded.created_at""",
        (page_id, json.dumps(list(embedding)), model, _now()),
    )
    conn.commit()
    conn.close()


def list_page_embeddings() -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT e.page_id, e.embedding_json, p.document_id, p.page_number
           FROM page_embeddings e JOIN pages p ON p.id = e.page_id"""
    ).fetchall()
    conn.close()
    return [
        {"page_id": row["page_id"], "document_id": row["document_id"],
         "page_number": row["page_number"], "embedding": json.loads(row["embedding_json"])}
        for row in rows
    ]


def save_recognition_page(document_id: str, page_number: int, lines: List[Dict[str, Any]], **page_values: Any) -> int:
    """Atomically replace one page's generated layout and OCR rows."""
    page_id = upsert_page(document_id, page_number, status="ready", processed_at=_now(), **page_values)
    conn = get_connection()
    conn.execute("DELETE FROM lines WHERE page_id = ?", (page_id,))
    for region_order in sorted({line.get("region_order", 1) for line in lines}):
        region_lines = [line for line in lines if line.get("region_order", 1) == region_order]
        bbox = region_lines[0].get("region", {}).get("bbox", [None, None, None, None])
        region = conn.execute(
            """INSERT INTO regions (page_id, region_type, x0, y0, x1, y1, reading_order)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (page_id, "text", *bbox, region_order),
        )
        region_id = region.lastrowid
        for line in region_lines:
            conn.execute(
                """INSERT INTO lines
                   (page_id, region_id, line_order, ocr_text, confidence, engine, x0, y0, x1, y1, review_status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (page_id, region_id, line.get("line_order", 0), line.get("text", ""),
                 line.get("confidence"), line.get("engine", "paddle"), *line["bbox"],
                 "needs_review" if float(line.get("confidence", 0)) < 0.8 else "accepted", _now()),
            )
    conn.commit()
    conn.close()
    return page_id


def processed_page_numbers(document_id: str) -> List[int]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT page_number FROM pages WHERE document_id = ? AND status = 'ready' ORDER BY page_number",
        (document_id,),
    ).fetchall()
    conn.close()
    return [int(row["page_number"]) for row in rows]


# Dashboard

def get_stats() -> Dict[str, Any]:
    conn = get_connection()
    one = lambda sql: conn.execute(sql).fetchone()[0]
    stats = {
        "documents": one("SELECT COUNT(*) FROM documents"),
        "pages": one("SELECT COUNT(*) FROM pages"),
        "pages_verified": one("SELECT COUNT(*) FROM pages WHERE review_status = 'verified'"),
        "lines": one("SELECT COUNT(*) FROM lines"),
        "lines_needing_review": one("SELECT COUNT(*) FROM lines WHERE review_status = 'needs_review'"),
        "avg_confidence": one("SELECT AVG(confidence) FROM lines WHERE engine != 'text_layer'"),
    }
    conn.close()
    return stats
