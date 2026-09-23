import pytest
from backend.pipeline.extraction import extract_structured_information

def test_extraction_all_declarations():
    ocr_boxes = [
        {"text": "DOVE BATHING BAR", "confidence": 0.95},
        {"text": "Net Qty: 125 g", "confidence": 0.94},
        {"text": "MRP Rs. 65.00 (incl. of all taxes)", "confidence": 0.96},
        {"text": "Mfg: 06/2026", "confidence": 0.92},
        {"text": "Best before 24 months from mfg", "confidence": 0.90},
        {"text": "Manufactured by: Hindustan Unilever Limited", "confidence": 0.95},
        {"text": "Mumbai, Maharashtra 400099", "confidence": 0.91},
        {"text": "Toll Free: 1800-10-22-221", "confidence": 0.95},
        {"text": "care@unilever.com", "confidence": 0.97},
        {"text": "Made in India", "confidence": 0.98}
    ]
    full_text = "\n".join([b["text"] for b in ocr_boxes])
    clip_categories = [{"label": "soap shampoo cosmetics or personal care", "confidence": 0.92}]

    data = extract_structured_information(ocr_boxes, full_text, clip_categories)

    assert data["brand"]["value"] == "Dove"
    assert "125 g" in data["net_quantity"]["value"]
    assert "65.00" in data["mrp"]["value"]
    assert data["mrp"]["details"]["inclusive_of_taxes"] is True
    assert "06/2026" in data["mfg_date"]["value"]
    assert "Hindustan Unilever" in data["manufacturer"]["value"]
    assert "1800-10-22-221" in data["consumer_care"]["value"]
    assert data["country_of_origin"]["value"] == "Made in India"

def test_prohibited_quantity_detection():
    ocr_boxes = [
        {"text": "Potato Chips", "confidence": 0.90},
        {"text": "Net Weight: 50 gms", "confidence": 0.92}
    ]
    full_text = "Potato Chips\nNet Weight: 50 gms"
    data = extract_structured_information(ocr_boxes, full_text, [])

    assert data["net_quantity"]["details"]["is_prohibited_symbol"] is True
    assert data["net_quantity"]["details"]["raw_unit"].lower() == "gms"

def test_dot_matrix_optical_normalizer_and_cross_line_linking():
    # Real-world packaging scenario from empirical food/personal care dataset
    ocr_boxes = [
        {"text": "DOVE DAILY SHINE SHAMPOO", "confidence": 0.96},
        {"text": "DOVE IS A REGISTERED TRADEMARK", "confidence": 0.94},
        {"text": "Net Qty: 500 9", "confidence": 0.93}, # 9 -> g
        {"text": "MRP (incl. of all taxes) SEE BELOW", "confidence": 0.95}, # Pre-printed header
        {"text": "* {523.00", "confidence": 0.92}, # Stamped dot-matrix ink-jet price: { -> ₹
        {"text": "# 01/26", "confidence": 0.91}, # Dot-matrix stamp: # -> MFD
        {"text": "@ 05/28", "confidence": 0.90}, # Dot-matrix stamp: @ -> Best Before
        {"text": "Mfd. by: LAKME LEVER PVT. LTD.", "confidence": 0.96},
        {"text": "Survey No. 159/B, Gandhidham, Gujarat 370 240", "confidence": 0.94}, # Spaced PIN 370 240
        {"text": "Toll Free: 1800-10-22-221", "confidence": 0.95},
        {"text": "lever.care@unilever.com", "confidence": 0.96},
        {"text": "Made in India", "confidence": 0.97}
    ]
    full_text = "\n".join([b["text"] for b in ocr_boxes])
    clip_categories = [{"label": "shampoo soap cosmetics or personal care", "confidence": 0.95}]

    data = extract_structured_information(ocr_boxes, full_text, clip_categories)

    # 1. Dynamic Brand Detection
    assert data["brand"]["value"] == "Dove"
    assert data["brand"]["is_valid"] is True

    # 2. Dot-Matrix Weight Normalization
    assert "500 g" in data["net_quantity"]["value"]
    assert data["net_quantity"]["is_valid"] is True

    # 3. Cross-Line "See Below" Spatial Linker for MRP
    assert "523" in data["mrp"]["value"]
    assert data["mrp"]["is_valid"] is True
    assert data["mrp"]["details"]["inclusive_of_taxes"] is True

    # 4. Spaced Indian PIN Code Parser
    assert "370240" in data["manufacturer"]["details"]["pin_code"]
    assert "Gujarat" in data["manufacturer"]["details"]["state"]
    assert data["manufacturer"]["is_valid"] is True

    # 5. Statutory Unit Sale Price (USP) Engine (Rule 6(10))
    # 523 / 500 = 1.046 -> ₹ 1.05 / g
    assert data["unit_sale_price"]["is_valid"] is True
    assert "1.05" in data["unit_sale_price"]["value"]
    assert "/ g" in data["unit_sale_price"]["value"] or "/g" in data["unit_sale_price"]["value"]

    # 6. Dot-Matrix Dates
    assert "01/2026" in data["mfg_date"]["value"] or "01/26" in data["mfg_date"]["value"]
    assert "05/2028" in data["best_before"]["value"] or "05/28" in data["best_before"]["value"]

