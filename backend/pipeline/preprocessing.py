import os
from typing import Tuple, Dict, Any
from PIL import Image, ImageOps, ImageEnhance, ImageFilter
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

def reduce_specular_glare(np_img: np.ndarray) -> Tuple[np.ndarray, float]:
    """
    Detects and reduces specular glare / reflection on shiny, glossy plastic or metallic foil pouches.
    Uses adaptive thresholding on HSV luminance + Telea inpainting to recover underlying text edges.

    Returns (image, glare_ratio) -- glare_ratio is the fraction of the panel covered by specular
    highlight, useful downstream (e.g. root-cause diagnosis of a missing declaration) even on the
    majority of frames where it falls outside the inpainting band.
    """
    hsv = cv2.cvtColor(np_img, cv2.COLOR_RGB2HSV)
    # Highlight mask for washed-out specular glare (high brightness V, low saturation S)
    glare_mask = cv2.inRange(hsv, np.array([0, 0, 238]), np.array([180, 45, 255]))

    # Check if specular reflections cover between 0.1% and 15% of packaging
    total_pixels = np_img.shape[0] * np_img.shape[1]
    glare_count = np.count_nonzero(glare_mask)
    glare_ratio = glare_count / total_pixels if total_pixels else 0.0

    if 50 < glare_count < (total_pixels * 0.15):
        # Slightly dilate mask to include overexposed boundary halo
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        dilated_mask = cv2.dilate(glare_mask, kernel, iterations=1)
        # Fast Telea inpainting to recover local gradient continuity
        inpainted = cv2.inpaint(np_img, dilated_mask, inpaintRadius=3, flags=cv2.INPAINT_TELEA)
        return inpainted, glare_ratio
    return np_img, glare_ratio

def enhance_text_clarity(img: Image.Image) -> Image.Image:
    """
    Enhances contrast, suppresses glare, and sharpens text to maximize legibility.
    1. Reduces specular reflection on glossy laminate packaging.
    2. Applies CLAHE on L-channel in LAB space.
    3. Unsharp masking.
    """
    np_img = np.array(img)
    
    # 1. Specular Glare Reduction
    np_img, _glare_ratio = reduce_specular_glare(np_img)

    # 2. Convert RGB to LAB for CLAHE contrast enhancement
    lab = cv2.cvtColor(np_img, cv2.COLOR_RGB2LAB)
    l, a, b = cv2.split(lab)
    
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    cl = clahe.apply(l)
    
    limg = cv2.merge((cl, a, b))
    enhanced_np = cv2.cvtColor(limg, cv2.COLOR_LAB2RGB)
    enhanced_pil = Image.fromarray(enhanced_np)
    
    # 3. Subtle unsharp mask sharpening
    enhanced_pil = enhanced_pil.filter(ImageFilter.UnsharpMask(radius=1.5, percent=120, threshold=3))
    return enhanced_pil

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
    is_sharp = blur_score > 60.0 # Standard threshold for readable packaging
    
    # Contrast calculation (RMS contrast = standard deviation of pixel intensities)
    contrast_score = float(round(float(np.std(np_img)), 2))
    is_contrast_sufficient = contrast_score > 35.0
    
    warnings = []
    if not is_sharp:
        warnings.append(f"Image may have slight motion or focus blur (score: {blur_score}). Important small numerals might be degraded.")
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
