EXPECTED_TABLES = {
    "documents", "pages", "regions", "lines", "candidates",
    "verifications", "outputs", "jobs", "training_pairs",
    "page_embeddings",
}


def test_schema_has_all_tables(temp_db):
    assert EXPECTED_TABLES <= set(temp_db.list_tables())


def test_health_reports_engines(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["app"] == "OLAI"
    assert body["language"] == "Tamil"
    names = {e["name"] for e in body["engines"]}
    assert {"PaddleOCR", "Tesseract", "CLIP", "PyMuPDF"} <= names


def test_empty_stats(client):
    res = client.get("/api/stats")
    assert res.status_code == 200
    body = res.json()
    assert body["documents"] == 0 and body["pages"] == 0 and body["lines_needing_review"] == 0


def test_dashboard_metrics_are_available(client):
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert body["documents"] == 0
    assert body["auto_accepted_percent"] == 0.0
    assert body["tesseract_percent"] == 0.0
    assert "confidence_buckets" in body


def test_documents_list_and_detail(client, temp_db):
    assert client.get("/api/documents").json() == {"documents": []}

    doc = temp_db.create_document("book.pdf", "storage/originals/book.pdf", "pdf")
    assert doc["id"].startswith("DOC-")
    assert doc["language"] == "ta" and doc["status"] == "uploaded"

    listed = client.get("/api/documents").json()["documents"]
    assert [d["id"] for d in listed] == [doc["id"]]
    assert listed[0]["verified_pages"] == 0

    detail = client.get(f"/api/documents/{doc['id']}")
    assert detail.status_code == 200 and detail.json()["filename"] == "book.pdf"


def test_unknown_document_is_404(client):
    assert client.get("/api/documents/DOC-NOPE").status_code == 404


def test_delete_document_removes_database_record(client, temp_db, tmp_path, monkeypatch):
    from backend import app as app_module

    original = tmp_path / "original.jpg"
    original.write_bytes(b"photo")
    monkeypatch.setattr(app_module, "BASE_DIR", tmp_path)
    doc = temp_db.create_document("original.jpg", "original.jpg", "camera")
    response = client.delete(f"/api/documents/{doc['id']}")
    assert response.status_code == 204
    assert temp_db.get_document(doc["id"]) is None
    assert not original.exists()


def test_delete_unknown_document_is_404(client):
    assert client.delete("/api/documents/DOC-NOPE").status_code == 404


def test_manual_source_override(client, temp_db):
    doc = temp_db.create_document("scan.jpg", "storage/originals/scan.jpg", "image")
    response = client.put(f"/api/documents/{doc['id']}/source-type", json={"source_type": "palm_leaf"})
    assert response.status_code == 200
    assert response.json()["source_type"] == "palm_leaf"
    assert response.json()["source_manual"] == 1


def test_photo_upload_creates_processing_document(client, temp_db, monkeypatch, tmp_path):
    from backend import app as app_module

    monkeypatch.setattr(app_module, "ORIGINALS_DIR", tmp_path)
    monkeypatch.setattr(app_module, "_process_photo", lambda document_id, image_path: None)
    response = client.post(
        "/api/documents/upload",
        files={"file": ("page.jpg", b"fake image bytes", "image/jpeg")},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["filename"] == "page.jpg"
    assert body["file_type"] == "camera"
    assert temp_db.get_document(body["id"])["status"] == "uploaded"
    assert list(tmp_path.glob("*.jpg"))


def test_photo_upload_rejects_pdf(client):
    response = client.post(
        "/api/documents/upload",
        files={"file": ("document.pdf", b"pdf", "application/pdf")},
    )
    assert response.status_code == 415


def test_review_and_line_verification_are_audited(client, temp_db):
    doc = temp_db.create_document("scan.jpg", "storage/originals/scan.jpg", "camera")
    conn = temp_db.get_connection()
    conn.execute("INSERT INTO pages (document_id, page_number, status) VALUES (?, 1, 'ready')", (doc["id"],))
    page_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
          """INSERT INTO lines (page_id, line_order, ocr_text, confidence, x0, y0, x1, y1, review_status, created_at)
              VALUES (?, 0, ?, ?, 1, 2, 30, 12, 'needs_review', ?)""",
          (page_id, "மொழி", 0.4, "2026-09-24 00:00:00"),
    )
    line_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()

    review = client.get(f"/api/documents/{doc['id']}/review")
    assert review.status_code == 200
    assert review.json()["pages"][0]["lines"][0]["id"] == line_id
    assert review.json()["pages"][0]["lines"][0]["x0"] == 1
    saved = client.put(f"/api/lines/{line_id}/verify", json={
        "reviewer": "Arun", "text": "மொழி சரி", "action": "edit",
    })
    assert saved.status_code == 200
    assert saved.json()["review_status"] == "verified"
    audit = temp_db.get_connection().execute("SELECT reviewer FROM verifications").fetchone()[0]
    assert audit == "Arun"


def test_deleting_document_cascades_to_pages(temp_db):
    doc = temp_db.create_document("scan.jpg", "storage/originals/scan.jpg", "image")
    conn = temp_db.get_connection()
    conn.execute("INSERT INTO pages (document_id, page_number) VALUES (?, 1)", (doc["id"],))
    conn.commit()
    conn.close()

    temp_db.delete_document(doc["id"])

    conn = temp_db.get_connection()
    remaining = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    conn.close()
    assert remaining == 0


def test_invalid_status_rejected(temp_db):
    doc = temp_db.create_document("scan.jpg", "storage/originals/scan.jpg", "image")
    try:
        temp_db.update_document_status(doc["id"], "COMPLIANT")
    except ValueError:
        return
    raise AssertionError("legacy compliance status should be rejected")


def test_frontends_are_served(client):
    home = client.get("/")
    assert home.status_code == 200 and "OLAI" in home.text
    mobile = client.get("/m/")
    assert mobile.status_code == 200 and "OLAI" in mobile.text
