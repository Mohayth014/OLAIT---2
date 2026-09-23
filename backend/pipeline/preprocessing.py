import os
from typing import Tuple, Dict, Any
from PIL import Image, ImageOps
import cv2
import numpy as np

# Safely enable HEIC support
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

def load_and_orient_image(image_input) -> Image.Image:
    """
    Loads an image from file path, bytes, or file-like object,
    applies EXIF orientation normalization, and converts to RGB.
    """
    if isinstance(image_input, Image.Image):
        img = image_input
    elif isinstance(image_input, (str, os.PathLike)):
        img = Image.open(image_input)
    else:
        # Assuming bytes or file stream
        img = Image.open(image_input)

    # Automatically correct orientation from EXIF metadata (crucial for smartphone photos)
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    return img.convert("RGB")

def resize_for_ocr(img: Image.Image, max_dim: int = 1600) -> Tuple[Image.Image, float]:
    """
    Resizes image maintaining aspect ratio so the largest dimension does not exceed max_dim.
    Returns the resized PIL image and the scaling factor (scale = resized / original).
    """
    w, h = img.size
    scale = min(1.0, max_dim / max(w, h))
    if scale < 1.0:
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        return resized, scale
    return img, 1.0

def analyze_image_quality(img: Image.Image) -> Dict[str, Any]:
    """
    Computes objective image quality metrics:
    - Blur Score: Laplacian variance (standard CV blur check)
    - Contrast Score: Standard deviation of luminance
    - Legibility assessment
    """
    np_img = np.array(img.convert("L"))
    
    # Blur detection via Laplacian variance
    laplacian_var = cv2.Laplacian(np_img, cv2.CV_64F).var()
    blur_score = float(round(laplacian_var, 2))
    is_sharp = blur_score > 60.0  # provisional; re-tuned for document pages in Phase 3
    
    # Contrast calculation (RMS contrast = standard deviation of pixel intensities)
    contrast_score = float(round(float(np.std(np_img)), 2))
    is_contrast_sufficient = contrast_score > 35.0
    
    warnings = []
    if not is_sharp:
        warnings.append(f"Image may have slight motion or focus blur (score: {blur_score}). Small characters and vowel signs may be misread.")
    if not is_contrast_sufficient:
        warnings.append(f"Low lighting or poor contrast detected (score: {contrast_score}).")

    return {
        "blur_score": blur_score,
        "contrast_score": contrast_score,
        "is_sharp": is_sharp,
        "is_contrast_sufficient": is_contrast_sufficient,
        "warnings": warnings
    }

def create_thumbnail(img: Image.Image, max_dim: int = 320) -> Image.Image:
    """Creates a thumbnail preview preserving aspect ratio."""
    thumb = img.copy()
    thumb.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    return thumb


def enhance_engraved_script(img: Image.Image) -> Image.Image:
    """Improve local contrast for engraved palm-leaf and inscription strokes.

    This preserves the original dimensions so OCR boxes remain valid. It is a
    recognition input only; the untouched normalized page remains the provenance image.
    """
    gray = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    return Image.fromarray(cv2.cvtColor(enhanced, cv2.COLOR_GRAY2RGB))
