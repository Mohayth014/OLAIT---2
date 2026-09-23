import sqlite3
import uuid
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

CREATE INDEX IF NOT EXISTS idx_pages_document ON pages(document_id, page_number);
CREATE INDEX IF NOT EXISTS idx_lines_page ON lines(page_id, line_order);
CREATE INDEX IF NOT EXISTS idx_lines_review ON lines(review_status);
CREATE INDEX IF NOT EXISTS idx_candidates_line ON candidates(line_id, rank);
CREATE INDEX IF NOT EXISTS idx_outputs_page ON outputs(page_id, kind);
CREATE INDEX IF NOT EXISTS idx_jobs_document ON jobs(document_id);
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


def delete_document(document_id: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    conn.commit()
    conn.close()


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
