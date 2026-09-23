import os
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
from PIL import Image
from backend.config import LANGUAGE_PACKS, DEFAULT_LANGUAGE, OCR_DEVICE, TESSERACT_CMD
from backend.pipeline.tamil_processor import normalize_text

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


class OCREngine:
    """
    Dual-OCR engine:
      - PaddleOCR (GPU): primary reader, runs on every page, returns lines + boxes + confidence.
      - Tesseract (CPU): second opinion, re-reads individual line crops only when asked
        (the confidence gate that decides which lines is part of the pipeline, not this class).
    Paddle readers are created lazily, one per (language, recognition model) pair, so a
    route-specific model (e.g. a future palm-leaf model) can be used without reloading the rest.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(OCREngine, cls).__new__(cls)
            cls._instance._paddle_readers = {}
        return cls._instance

    # PaddleOCR (primary)

    def _get_paddle(self, language: str, rec_model: Optional[str] = None):
        key = (language, rec_model)
        if key not in self._paddle_readers:
            from paddleocr import PaddleOCR
            pack = LANGUAGE_PACKS[language]
            kwargs = dict(
                lang=pack["paddle_lang"],
                device=OCR_DEVICE,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
            if rec_model:
                kwargs["text_recognition_model_dir"] = rec_model
            print(f"[OCREngine] Loading PaddleOCR ({pack['name']}, model={rec_model or 'default'}, device={OCR_DEVICE})...")
            self._paddle_readers[key] = PaddleOCR(**kwargs)
        return self._paddle_readers[key]

    def read_page(self, img: Image.Image, language: str = DEFAULT_LANGUAGE, rec_model: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Runs PaddleOCR on a full page image.
        Returns [{'text', 'confidence', 'bbox': [x0, y0, x1, y1], 'engine': 'paddle'}] in pixel coordinates.
        """
        reader = self._get_paddle(language, rec_model)
        result = reader.predict(np.array(img.convert("RGB")))[0]
        texts = result["rec_texts"]
        scores = result["rec_scores"]
        boxes = result.get("rec_boxes")
        if boxes is None or len(boxes) != len(texts):
            boxes = [self._poly_to_box(p) for p in result["rec_polys"]]

        lines = []
        for text, score, box in zip(texts, scores, boxes):
            clean = normalize_text(text)
            if not clean:
                continue
            x0, y0, x1, y1 = (float(v) for v in box)
            lines.append({
                "text": clean,
                "confidence": round(float(score), 4),
                "bbox": [x0, y0, x1, y1],
                "engine": "paddle",
            })
        return lines

    @staticmethod
    def _poly_to_box(poly) -> Tuple[float, float, float, float]:
        pts = np.asarray(poly, dtype=float)
        return pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()

    # Tesseract (second opinion)

    def read_line(self, crop: Image.Image, language: str = DEFAULT_LANGUAGE) -> Dict[str, Any]:
        """
        Re-reads a single text-line crop with Tesseract.
        Returns {'text', 'confidence' (0..1), 'words': [(word, conf)], 'engine': 'tesseract'}.
        """
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
        data = pytesseract.image_to_data(
            crop,
            lang=LANGUAGE_PACKS[language]["tesseract_lang"],
            config="--psm 7",  # treat the image as a single text line
            output_type=pytesseract.Output.DICT,
        )
        words = [
            (normalize_text(w), float(c) / 100.0)
            for w, c in zip(data["text"], data["conf"])
            if w.strip() and float(c) >= 0
        ]
        words = [(w, c) for w, c in words if w]
        confidence = round(sum(c for _, c in words) / len(words), 4) if words else 0.0
        return {
            "text": " ".join(w for w, _ in words),
            "confidence": confidence,
            "words": words,
            "engine": "tesseract",
        }


# Global singleton
_ocr_engine_instance = None

def get_ocr_engine() -> OCREngine:
    global _ocr_engine_instance
    if _ocr_engine_instance is None:
        _ocr_engine_instance = OCREngine()
    return _ocr_engine_instance
