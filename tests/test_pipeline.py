import os
from pathlib import Path
from PIL import Image
from backend.config import DATASET_DIR
from backend.pipeline.preprocessing import load_and_orient_image, analyze_image_quality
from backend.pipeline.clip_engine import get_clip_engine
from backend.pipeline.vector_search import get_vector_search_engine
from backend.pipeline.ocr_engine import get_ocr_engine
from backend.pipeline.readability import evaluate_font_and_readability
from backend.pipeline.extraction import extract_structured_information
from backend.pipeline.rules_engine import LegalMetrologyRuleEngine
from backend.config import DEFAULT_RULES

def test_pipeline_on_dataset_sample():
    dataset_path = Path(DATASET_DIR)
    files = [f for f in os.listdir(dataset_path) if f.lower().endswith(('.jpg', '.jpeg', '.heic'))]
    assert len(files) > 0, "Dataset contains no image files"

    test_file = dataset_path / files[0]
    print(f"\n[Test Pipeline] Testing sample image: {test_file.name}")

    # 1. Preprocessing
    img = load_and_orient_image(test_file)
    assert isinstance(img, Image.Image)
    assert img.size[0] > 0 and img.size[1] > 0
    quality = analyze_image_quality(img)
    assert "blur_score" in quality

    # 2. CLIP
    clip_engine = get_clip_engine()
    categories = clip_engine.classify_category(img)
    assert len(categories) > 0
    embedding = clip_engine.generate_embedding(img)
    assert len(embedding) == 512

    # 3. Vector search
    vector_search = get_vector_search_engine()
    vector_search.load_index([{"filename": files[0], "name": "Sample Product", "category": "Food", "embedding": embedding.tolist()}])
    similar = vector_search.search_similar(embedding, top_k=1)
    assert len(similar) > 0

    # 4. OCR
    ocr_engine = get_ocr_engine()
    boxes, full_text = ocr_engine.run_ocr(img)
    assert isinstance(boxes, list)

    # 5. Readability
    readability = evaluate_font_and_readability(img, boxes)
    assert "is_legible" in readability

    # 6. Extraction
    extracted = extract_structured_information(boxes, full_text, categories)
    assert "product_name" in extracted
    assert "mrp" in extracted
    assert "net_quantity" in extracted

    # 7. Rules Engine
    engine = LegalMetrologyRuleEngine(DEFAULT_RULES)
    status, conf, rule_results, violations, summary = engine.evaluate(extracted, readability)
    assert status in ["COMPLIANT", "NON_COMPLIANT", "REVIEW_REQUIRED"]
    assert 0.0 <= conf <= 1.0
    print(f"[Test Pipeline] Result: status={status}, confidence={conf*100:.1f}%, violations={len(violations)}")

if __name__ == "__main__":
    test_pipeline_on_dataset_sample()
