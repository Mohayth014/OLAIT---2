import pytest
import numpy as np
from PIL import Image
from backend.pipeline.preprocessing import reduce_specular_glare, enhance_text_clarity
from backend.pipeline.spell_checker import get_spell_checker
from backend.pipeline.spatial_layout import get_spatial_layout_extractor

def test_glare_reduction_filter():
    # Synthetic image with specular glare spot (pure white blown-out patch)
    np_img = np.full((300, 300, 3), 100, dtype=np.uint8)
    # Add a white glare patch
    np_img[50:100, 50:100] = [255, 255, 255]
    
    cleaned, glare_ratio = reduce_specular_glare(np_img)
    assert cleaned.shape == np_img.shape
    # Glare should be attenuated
    assert cleaned[75, 75, 0] < 255
    # The 50x50 pure-white patch is 1/36 of the 300x300 frame
    assert 0.02 < glare_ratio < 0.04

def test_packaging_spell_checker():
    checker = get_spell_checker()
    
    # 1. OCR numeric confusion: O -> 0 in price
    assert "₹ 50.00" in checker.correct_packaging_text("MRP Rs. 5O.OO (incl. of all taxes)")
    
    # 2. Statutory phrase normalization
    corrected = checker.correct_packaging_text("cust care: 1800-10-22-221")
    assert "consumer care" in corrected.lower()

    # 3. Brand name fuzzy correction
    assert checker.correct_word("Dov") == "Dove"
    assert checker.correct_word("Britania") == "Britannia"

def test_spatial_layout_extractor():
    extractor = get_spatial_layout_extractor()
    boxes = [
        {"text": "Manufactured by:", "confidence": 0.95, "normalized_bbox": {"x_min": 0.1, "y_min": 0.2, "x_max": 0.4, "y_max": 0.24}},
        {"text": "Hindustan Unilever Limited", "confidence": 0.94, "normalized_bbox": {"x_min": 0.1, "y_min": 0.25, "x_max": 0.5, "y_max": 0.29}},
        {"text": "Mumbai, Maharashtra 400099", "confidence": 0.92, "normalized_bbox": {"x_min": 0.1, "y_min": 0.30, "x_max": 0.5, "y_max": 0.34}},
        {"text": "MRP Rs. 65.00", "confidence": 0.96, "normalized_bbox": {"x_min": 0.6, "y_min": 0.2, "x_max": 0.8, "y_max": 0.24}},
        {"text": "inclusive of all taxes", "confidence": 0.93, "normalized_bbox": {"x_min": 0.6, "y_min": 0.25, "x_max": 0.8, "y_max": 0.28}}
    ]

    spatial_data = extractor.extract_spatial_entities(boxes, 1000, 1000)
    assert "mfg_spatial_block" in spatial_data
    assert len(spatial_data["mfg_spatial_block"]) >= 2
    assert "mrp_spatial_block" in spatial_data
    assert len(spatial_data["mrp_spatial_block"]) >= 2
