from pathlib import Path

from PIL import Image

from backend import app as app_module
from backend.database import db
from backend.pipeline.preprocessing import enhance_engraved_script


def test_embedding_storage_accepts_numpy_values(temp_db):
    import numpy as np

    doc = temp_db.create_document("page.jpg", "storage/originals/page.jpg", "camera")
    page_id = temp_db.upsert_page(doc["id"], 1, status="ready")
    temp_db.save_page_embedding(page_id, np.array([np.float32(0.25), np.float32(0.75)]), "clip")
    assert temp_db.list_page_embeddings()[0]["embedding"] == [0.25, 0.75]


def test_engraved_preprocessing_preserves_dimensions():
    image = Image.new("RGB", (120, 40), "#9b5b2c")
    enhanced = enhance_engraved_script(image)
    assert enhanced.size == image.size


def test_process_photo_marks_review_after_recognition(temp_db, monkeypatch, tmp_path):
    image_path = tmp_path / "page.jpg"
    Image.new("RGB", (40, 40), "white").save(image_path)
    document = temp_db.create_document("page.jpg", str(image_path), "camera")

    class FakeClip:
        def classify_source_type(self, image):
            return [{"source_type": "modern_print", "confidence": 1.0}]

        def generate_embedding(self, image):
            return [0.1, 0.9]

    class FakeOCR:
        def read_page(self, image, language, rec_model=None):
            return [{"text": "தமிழ்", "confidence": 0.99, "bbox": [1, 1, 30, 10], "engine": "paddle"}]

        def read_line(self, crop, language):
            return {"text": "தமிழ்", "confidence": 0.8, "engine": "tesseract"}

    monkeypatch.setattr(app_module, "ORIGINALS_DIR", tmp_path)
    monkeypatch.setattr(app_module, "PAGES_DIR", tmp_path / "pages")
    monkeypatch.setattr(app_module, "THUMBNAIL_DIR", tmp_path / "thumbs")
    monkeypatch.setattr(app_module, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr("backend.pipeline.clip_engine.get_clip_engine", lambda: FakeClip())
    monkeypatch.setattr("backend.pipeline.ocr_engine.get_ocr_engine", lambda: FakeOCR())

    app_module._process_photo(document["id"], image_path)
    processed = temp_db.get_document(document["id"])
    assert processed["status"] == "review"
    assert processed["source_type"] == "modern_print"
    assert temp_db.list_page_embeddings()[0]["embedding"] == [0.1, 0.9]