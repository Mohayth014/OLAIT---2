import random

from PIL import Image

from backend.pipeline.layout_service import group_lines_into_regions
from backend.pipeline.recognition_pipeline import (
    apply_tesseract_gate,
    recognize_pages,
    sample_page_numbers,
    weighted_source_vote,
)


def test_sample_pages_are_spread_and_unique():
    assert sample_page_numbers(10, 5) == [0, 2, 4, 7, 9]
    assert sample_page_numbers(2, 5) == [0, 1]


def test_weighted_source_vote_uses_each_page_top_confidence():
    result = weighted_source_vote([
        [{"source_type": "modern_print", "confidence": 0.8}],
        [{"source_type": "historical_print", "confidence": 0.95}],
        [{"source_type": "historical_print", "confidence": 0.9}],
    ])
    assert result["source_type"] == "historical_print"
    assert result["confidence"] == 0.6981


def test_tesseract_gate_reads_only_low_confidence_lines(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.pipeline.recognition_pipeline.CROPS_DIR", tmp_path)

    class FakeOCR:
        def __init__(self):
            self.calls = []

        def read_line(self, crop, language):
            self.calls.append((crop.size, language))
            return {"text": "தமிழ்", "confidence": 0.7, "engine": "tesseract"}

    engine = FakeOCR()
    lines = [
        {"text": "low", "confidence": 0.5, "bbox": [0, 0, 20, 10]},
        {"text": "high", "confidence": 0.99, "bbox": [0, 10, 20, 20]},
    ]
    result = apply_tesseract_gate(Image.new("RGB", (30, 30)), lines, engine, "ta", "DOC-1", 1,
                                  rng=random.Random(4))
    assert len(engine.calls) == 1
    assert "tesseract" in result[0]
    assert "tesseract" not in result[1]


def test_layout_orders_two_columns():
    lines = [
        {"text": "A", "bbox": [20, 10, 90, 20]},
        {"text": "B", "bbox": [200, 10, 270, 20]},
        {"text": "C", "bbox": [20, 30, 90, 40]},
        {"text": "D", "bbox": [200, 30, 270, 40]},
    ]
    assert [line["text"] for line in group_lines_into_regions(lines)] == ["A", "C", "B", "D"]


def test_clip_palm_leaf_vote_reprocesses_first_pages(monkeypatch):
    seen_images = []

    class FakeClip:
        def classify_source_type(self, image):
            return [{"source_type": "palm_leaf", "confidence": 1.0}]

    class FakeOCR:
        def read_page(self, image, language, rec_model=None):
            seen_images.append(image.getpixel((0, 0)))
            return [{"text": "தமிழ்", "confidence": 0.99, "bbox": [0, 0, 10, 10], "engine": "paddle"}]

        def read_line(self, crop, language):
            return {"text": "தமிழ்", "confidence": 0.9, "engine": "tesseract"}

    image = Image.new("RGB", (40, 40), (150, 80, 40))
    monkeypatch.setattr("backend.pipeline.recognition_pipeline.apply_tesseract_gate", lambda image, lines, *args, **kwargs: lines)
    result = __import__("asyncio").run(recognize_pages(
        [image], "DOC-ROUTE", "ta", "modern_print", FakeClip(), FakeOCR(),
    ))
    assert result[0]["source_vote"]["source_type"] == "palm_leaf"
    assert len(seen_images) == 2
    assert seen_images[0] != seen_images[1]