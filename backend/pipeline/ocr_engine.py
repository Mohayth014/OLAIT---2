import re
import os
from typing import List, Dict, Any, Tuple
from PIL import Image
import numpy as np
import easyocr
import torch
from backend.config import OCR_LANGS, OCR_MAX_DIMENSION
from backend.pipeline.preprocessing import resize_for_ocr
from backend.pipeline.spell_checker import get_spell_checker

# Check PaddleOCR availability
HAS_PADDLEOCR = False
try:
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    from paddleocr import PaddleOCR
    HAS_PADDLEOCR = True
except Exception as e:
    print(f"[OCREngine] Notice: PaddleOCR optional integration note: {e}")

class OCREngine:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(OCREngine, cls).__new__(cls)
            cls._instance._init_engines()
        return cls._instance

    def _init_engines(self):
        use_gpu = torch.cuda.is_available()
        print(f"[OCREngine] Initializing Primary Engine (EasyOCR, gpu={use_gpu})...")
        self.easy_reader = easyocr.Reader(OCR_LANGS, gpu=use_gpu, verbose=False)
        self.spell_checker = get_spell_checker()

        self.has_paddle = False
        self.paddle_reader = None
        if HAS_PADDLEOCR:
            try:
                print("[OCREngine] Initializing Secondary Engine (PaddleOCR)...")
                self.paddle_reader = PaddleOCR(use_textline_orientation=True, lang='en')
                self.has_paddle = True
                print("[OCREngine] Dual-OCR Ensemble (EasyOCR + PaddleOCR) initialized successfully!")
            except Exception as e:
                print(f"[OCREngine] PaddleOCR fallback notice: {e}. Using EasyOCR as primary.")

    def run_ocr(self, img: Image.Image) -> Tuple[List[Dict[str, Any]], str]:
        """
        Dual-Engine OCR with Consensus Voting:
        1. Runs EasyOCR on contrast-enhanced image.
        2. Merges with secondary predictions when available using IoU consensus.
        3. Applies packaging spell-checker and confusion matrix correction.
        """
        orig_w, orig_h = img.size
        ocr_img, scale = resize_for_ocr(img, max_dim=OCR_MAX_DIMENSION)
        np_img = np.array(ocr_img)

        # 1. Primary Engine: EasyOCR (batched inference)
        raw_easy = self.easy_reader.readtext(
            np_img,
            detail=1,
            paragraph=False,
            batch_size=16,
            contrast_ths=0.15,
            adjust_contrast=0.7
        )

        detected_boxes = []
        inv_scale = 1.0 / scale if scale > 0 else 1.0

        for bbox, text, conf in raw_easy:
            clean_text = text.strip()
            if not clean_text or (len(clean_text) == 1 and not clean_text.isalnum()):
                continue

            # Apply domain-specific spell correction & confusion matrix
            corrected_text = self.spell_checker.correct_packaging_text(clean_text)

            scaled_bbox = [[float(pt[0] * inv_scale), float(pt[1] * inv_scale)] for pt in bbox]
            xs = [pt[0] for pt in scaled_bbox]
            ys = [pt[1] for pt in scaled_bbox]
            x_min = max(0.0, min(xs) / orig_w)
            y_min = max(0.0, min(ys) / orig_h)
            x_max = min(1.0, max(xs) / orig_w)
            y_max = min(1.0, max(ys) / orig_h)

            field_tag = self._infer_field_tag(corrected_text)

            detected_boxes.append({
                "text": corrected_text,
                "confidence": round(float(conf), 4),
                "bbox": scaled_bbox,
                "normalized_bbox": {
                    "x_min": round(x_min, 4),
                    "y_min": round(y_min, 4),
                    "x_max": round(x_max, 4),
                    "y_max": round(y_max, 4)
                },
                "field_tag": field_tag,
                "engine": "EasyOCR"
            })

        # 2. Secondary Engine: PaddleOCR consensus voting (if available and needed)
        if self.has_paddle and self.paddle_reader:
            try:
                paddle_results = self.paddle_reader.ocr(np_img)
                if paddle_results and paddle_results[0]:
                    for line in paddle_results[0]:
                        if not line or len(line) < 2:
                            continue
                        p_pts = line[0]
                        p_text, p_conf = line[1]
                        p_clean = self.spell_checker.correct_packaging_text(p_text.strip())
                        if not p_clean or len(p_clean) < 2:
                            continue

                        # Compute scaled bbox for paddle box
                        p_scaled = [[float(pt[0] * inv_scale), float(pt[1] * inv_scale)] for pt in p_pts]
                        p_xs = [pt[0] for pt in p_scaled]
                        p_ys = [pt[1] for pt in p_scaled]
                        px_min = max(0.0, min(p_xs) / orig_w)
                        py_min = max(0.0, min(p_ys) / orig_h)
                        px_max = min(1.0, max(p_xs) / orig_w)
                        py_max = min(1.0, max(p_ys) / orig_h)

                        # Consensus check: find overlapping EasyOCR box
                        matched_idx = -1
                        for idx, b in enumerate(detected_boxes):
                            nb = b["normalized_bbox"]
                            # Check IoU or center proximity
                            inter_x = max(0.0, min(px_max, nb["x_max"]) - max(px_min, nb["x_min"]))
                            inter_y = max(0.0, min(py_max, nb["y_max"]) - max(py_min, nb["y_min"]))
                            inter_area = inter_x * inter_y
                            if inter_area > 0.0001:
                                matched_idx = idx
                                break

                        if matched_idx >= 0:
                            # Consensus Voting: pick higher confidence
                            if p_conf > detected_boxes[matched_idx]["confidence"]:
                                detected_boxes[matched_idx]["text"] = p_clean
                                detected_boxes[matched_idx]["confidence"] = round(float(p_conf), 4)
                                detected_boxes[matched_idx]["engine"] = "PaddleOCR (Consensus)"
                        else:
                            # PaddleOCR caught a box that EasyOCR missed (e.g. faint date or code)
                            field_tag = self._infer_field_tag(p_clean)
                            detected_boxes.append({
                                "text": p_clean,
                                "confidence": round(float(p_conf), 4),
                                "bbox": p_scaled,
                                "normalized_bbox": {
                                    "x_min": round(px_min, 4),
                                    "y_min": round(py_min, 4),
                                    "x_max": round(px_max, 4),
                                    "y_max": round(py_max, 4)
                                },
                                "field_tag": field_tag,
                                "engine": "PaddleOCR"
                            })
            except Exception as e:
                # Silently fall back to primary engine
                pass

        # 3. Adaptive recovery: a net-quantity declaration is on the pack but its
        #    digits were lost to downscaling (e.g. "NET VOL." reads, "1 L" doesn't).
        try:
            detected_boxes = self._recover_net_quantity(img, detected_boxes)
        except Exception:
            pass

        full_extracted_text = "\n".join([b["text"] for b in detected_boxes])
        return detected_boxes, full_extracted_text

    # A net-quantity label ("NET WT / NET VOL / NET CONTENTS / NET QUANTITY").
    _DECL_TRIGGER = re.compile(r'net\s*(?:vol(?:ume)?|wt|weight|contents?|quantity|qty)', re.IGNORECASE)
    # A number already sitting next to a standard unit or count word -> nothing to recover.
    _QTY_NEAR = re.compile(
        r'\d\s*(?:kg|kgs|g|gm|gms|ml|l|ltr|litres?|nos?|numbers?|pcs?|pieces?|'
        r'patch(?:es)?|sheets?|tablets?|units?)\b',
        re.IGNORECASE,
    )
    _NETQTY_RANGES = {
        "l": (0.1, 25.0), "ml": (1.0, 5000.0),
        "kg": (0.02, 60.0), "g": (1.0, 5000.0),
    }
    _RECOVER_NUM_UNIT = re.compile(
        r'^\W*(\d{1,4}(?:\.\d{1,3})?)\s*([a-z]{0,3}l|kgs?|gms?|g|ml|ltr|litres?)\W*$',
        re.IGNORECASE,
    )

    def _recover_net_quantity(self, img: "Image.Image", boxes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Re-read a tight, native-resolution crop around a net-quantity declaration whose
        value was not captured in the primary pass, and inject one clean synthetic box
        ("NET QUANTITY <n> <unit>") so downstream parsing is unambiguous. No-op unless a
        declaration label is present without an adjacent quantity.
        """
        joined = " ".join(b.get("text", "") for b in boxes)
        if not self._DECL_TRIGGER.search(joined) or self._QTY_NEAR.search(joined):
            return boxes
        triggers = [b for b in boxes if self._DECL_TRIGGER.search(b.get("text", "")) and b.get("normalized_bbox")]
        if not triggers:
            return boxes

        trig_txt = " ".join(b["text"] for b in triggers).lower()
        if "vol" in trig_txt:
            family = {"l", "ml"}
        elif re.search(r'\bwt\b|weight', trig_txt):
            family = {"g", "kg"}
        else:  # NET CONTENTS / NET QUANTITY -> any standard unit of weight or measure
            family = set(self._NETQTY_RANGES)

        W, H = img.size

        def _centre(pts):
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            return sum(xs) / len(xs), sum(ys) / len(ys)

        for tb in triggers[:2]:
            nb = tb["normalized_bbox"]
            mx = my = 0.12
            x0 = max(0, int((nb["x_min"] - mx) * W)); x1 = min(W, int((nb["x_max"] + mx) * W))
            y0 = max(0, int((nb["y_min"] - my) * H)); y1 = min(H, int((nb["y_max"] + my) * H))
            if x1 - x0 < 24 or y1 - y0 < 24:
                continue

            crop = img.crop((x0, y0, x1, y1))
            cw, ch = crop.size
            up = max(1.0, 1500.0 / max(cw, ch))
            if up > 1.0:
                crop = crop.resize((int(cw * up), int(ch * up)), Image.Resampling.LANCZOS)
            try:
                sub = self.easy_reader.readtext(
                    np.array(crop), detail=1, paragraph=False,
                    contrast_ths=0.1, adjust_contrast=0.8,
                )
            except Exception:
                continue

            sub_trig = next((_centre(b) for b, t, c in sub if self._DECL_TRIGGER.search(t)), None)
            best = None  # (distance_to_trigger, value, unit)
            for b, t, c in sub:
                m = self._RECOVER_NUM_UNIT.match(t.strip())
                if not m:
                    continue
                val = float(m.group(1))
                unit = m.group(2).lower()
                unit = {"litres": "l", "litre": "l", "ltr": "l", "kgs": "kg", "gm": "g", "gms": "g"}.get(unit, unit)
                if unit.endswith("l") and unit != "ml":
                    unit = "l"  # OCR of "1 L" as "1l" / "il"
                if unit not in family or unit not in self._NETQTY_RANGES:
                    continue
                lo, hi = self._NETQTY_RANGES[unit]
                if not (lo <= val <= hi):
                    continue
                dist = 0.0
                if sub_trig:
                    cx, cy = _centre(b)
                    dist = ((cx - sub_trig[0]) ** 2 + (cy - sub_trig[1]) ** 2) ** 0.5
                if best is None or dist < best[0]:
                    best = (dist, val, unit)

            if best:
                _, val, unit = best
                boxes.append({
                    "text": f"NET QUANTITY {val:g} {unit}",
                    "confidence": 0.75,
                    "bbox": tb.get("bbox"),
                    "normalized_bbox": tb.get("normalized_bbox"),
                    "field_tag": "net_quantity",
                    "engine": "EasyOCR (hi-res recovery)",
                    "panel": tb.get("panel", "front"),
                })
                break
        return boxes

    def _infer_field_tag(self, text: str) -> str:
        """Heuristically tags detected text snippet for visual highlighting."""
        t = text.lower()
        if any(k in t for k in ["mrp", "₹", "rs.", "rs ", "incl", "taxes", "tax"]):
            return "mrp"
        if re.search(r'\b\d+\s*(g|kg|ml|l|ltr|gm|gms|n|units)\b', t, re.IGNORECASE) or "net qty" in t or "net weight" in t:
            return "net_quantity"
        if any(k in t for k in ["mfg", "mfd", "pkd", "packed", "date", "exp", "expiry", "best before"]):
            return "date"
        if any(k in t for k in ["mfd by", "mfg by", "marketed by", "packed by", "manufactured by", "pvt ltd", "ltd.", "llp", "corp", "unilever", "nestle", "itc"]):
            return "manufacturer"
        if any(k in t for k in ["care", "customer", "toll free", "1800", "@", "feedback", "helpline", "call"]):
            return "consumer_care"
        if "fssai" in t or re.search(r'\b100\d{11}\b', t):
            return "fssai"
        if any(k in t for k in ["made in", "origin", "country of"]):
            return "country_of_origin"
        return "general"

# Global singleton
_ocr_engine_instance = None

def get_ocr_engine() -> OCREngine:
    global _ocr_engine_instance
    if _ocr_engine_instance is None:
        _ocr_engine_instance = OCREngine()
    return _ocr_engine_instance
