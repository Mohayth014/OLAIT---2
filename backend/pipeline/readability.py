from typing import List, Dict, Any
from PIL import Image
import numpy as np
import cv2

def evaluate_font_and_readability(
    img: Image.Image,
    ocr_boxes: List[Dict[str, Any]],
    net_quantity_str: str = ""
) -> Dict[str, Any]:
    """
    Evaluates visual properties under Legal Metrology Rule 7 & Fifth Schedule:
    - Minimum numeral height relative to packaging area
    - Contrast ratio of declarations against packaging background
    - Visual clarity / sharpness
    """
    orig_w, orig_h = img.size
    total_area = orig_w * orig_h

    # Blur score
    gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    blur_score = float(round(cv2.Laplacian(gray, cv2.CV_64F).var(), 2))
    is_sharp = blur_score >= 50.0

    # Contrast score
    contrast_score = float(round(float(np.std(gray)), 2))
    is_contrast_sufficient = contrast_score >= 30.0

    # Numeral / text box height analysis
    box_heights = []
    box_areas = []
    for b in ocr_boxes:
        norm = b.get("normalized_bbox", {})
        h_norm = norm.get("y_max", 0) - norm.get("y_min", 0)
        w_norm = norm.get("x_max", 0) - norm.get("x_min", 0)
        box_heights.append(h_norm * orig_h)
        box_areas.append(w_norm * h_norm * total_area)

    avg_height = float(np.mean(box_heights)) if box_heights else 0.0
    min_height = float(np.min(box_heights)) if box_heights else 0.0
    max_height = float(np.max(box_heights)) if box_heights else 0.0
    total_text_area = float(np.sum(box_areas)) if box_areas else 0.0
    text_coverage_ratio = float(round(total_text_area / total_area if total_area > 0 else 0.0, 4))

    # Determine compliance under Rule 7
    warnings = []
    is_legible = True

    if not is_sharp:
        warnings.append(f"Blur detected (Laplacian score {blur_score} < 50.0). Small declarations may be ambiguous.")
        is_legible = False

    if not is_contrast_sufficient:
        warnings.append(f"Insufficient visual contrast (RMS contrast {contrast_score} < 30.0). Text may blend into background.")
        is_legible = False

    if box_heights and min_height < 12.0:
        warnings.append(f"Certain detected declaration numerals are under 12px in height, which may fail minimum height standards under Fifth Schedule.")

    return {
        "blur_score": blur_score,
        "contrast_score": contrast_score,
        "is_sharp": is_sharp,
        "is_contrast_sufficient": is_contrast_sufficient,
        "avg_font_height_px": round(avg_height, 1),
        "min_font_height_px": round(min_height, 1),
        "max_font_height_px": round(max_height, 1),
        "estimated_text_coverage": text_coverage_ratio,
        "is_legible": is_legible,
        "warnings": warnings
    }
