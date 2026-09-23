import re
from typing import List, Dict, Any, Tuple
import numpy as np

class SpatialLayoutEntityExtractor:
    """
    2D Spatial Layout & Entity Extractor based on LayoutLMv3 spatial coordinate architecture.
    Normalizes bounding boxes to standard LayoutLM [0, 1000] grid and clusters tokens
    using spatial graph adjacency (vertical column alignment & horizontal proximity).
    """
    def __init__(self):
        self.grid_scale = 1000.0

    def extract_spatial_entities(
        self,
        ocr_boxes: List[Dict[str, Any]],
        img_width: int,
        img_height: int
    ) -> Dict[str, Any]:
        """
        Processes OCR boxes with 2D spatial coordinates [x0, y0, x1, y1] normalized to [0, 1000].
        Builds spatial clusters for Legal Metrology entities:
        - MRP_BLOCK: 'MRP' trigger + price numeral + 'inclusive of all taxes'
        - QTY_BLOCK: 'Net Quantity' trigger + value + SI unit
        - MFG_BLOCK: 'Mfg by' trigger + company name + multi-line address + PIN
        - DATE_BLOCK: 'Mfg/Pkd' trigger + date token
        - CARE_BLOCK: Customer care trigger + 1800 toll-free + email
        """
        if not ocr_boxes or img_width <= 0 or img_height <= 0:
            return {}

        # 1. Convert to LayoutLM normalized 2D coordinates [x0, y0, x1, y1] in [0, 1000]
        layout_tokens = []
        for box in ocr_boxes:
            norm = box.get("normalized_bbox", {})
            x0 = int(norm.get("x_min", 0.0) * self.grid_scale)
            y0 = int(norm.get("y_min", 0.0) * self.grid_scale)
            x1 = int(norm.get("x_max", 1.0) * self.grid_scale)
            y1 = int(norm.get("y_max", 1.0) * self.grid_scale)
            
            layout_tokens.append({
                "text": box["text"],
                "confidence": box["confidence"],
                "box_1000": [x0, y0, x1, y1],
                "center_x": (x0 + x1) / 2.0,
                "center_y": (y0 + y1) / 2.0,
                "height": y1 - y0,
                "width": x1 - x0,
                "field_tag": box.get("field_tag", "general")
            })

        # Sort in natural reading order (top-to-bottom, left-to-right)
        layout_tokens.sort(key=lambda t: (t["center_y"] // 25, t["center_x"]))

        # 2. Spatial Clustering for Multi-line Address Blocks (Mfg/Packer).
        #    Trigger only on real attribution phrases ("... by", "manufactured &") so a
        #    stray heading like "Protein Packed" cannot anchor the address block.
        mfg_cluster = self._find_spatial_cluster(
            layout_tokens,
            trigger_keywords=[
                "manufactured by", "manufactured &", "manufactured and", "mfg by",
                "mfd by", "mfg.by", "packed by", "pkd by", "marketed by", "marketed &",
                "marketed and", "mktd by", "imported by", "mfg by:", "mktd. by",
            ],
            vertical_lookahead_px=180,
            max_lines=6
        )

        # 3. Spatial Clustering for Pricing (MRP + Taxes + Ink-jet Stamp)
        mrp_cluster = self._find_spatial_cluster(
            layout_tokens,
            trigger_keywords=["mrp", "maximum retail", "retail price", "see below", "₹", "rs."],
            vertical_lookahead_px=180,
            max_lines=4
        )

        # 4. Spatial Clustering for Dates (Mfg Date & Use Before / Expiry)
        date_cluster = self._find_spatial_cluster(
            layout_tokens,
            trigger_keywords=["mfd", "mfg", "date of", "use before", "best before", "expiry", "exp", "#"],
            vertical_lookahead_px=140,
            max_lines=3
        )

        # 5. Spatial Clustering for Consumer Care
        care_cluster = self._find_spatial_cluster(
            layout_tokens,
            trigger_keywords=["care", "toll free", "feedback", "helpline", "query", "consumer"],
            vertical_lookahead_px=120,
            max_lines=4
        )

        return {
            "mfg_spatial_block": mfg_cluster,
            "mrp_spatial_block": mrp_cluster,
            "date_spatial_block": date_cluster,
            "care_spatial_block": care_cluster,
            "total_tokens_mapped": len(layout_tokens)
        }

    def _find_spatial_cluster(
        self,
        tokens: List[Dict[str, Any]],
        trigger_keywords: List[str],
        vertical_lookahead_px: int,
        max_lines: int
    ) -> List[str]:
        """
        Finds a trigger token and clusters all spatially adjacent tokens directly beneath it
        or horizontally aligned with it within the specified LayoutLM coordinate budget.
        """
        trigger_token = None
        trigger_idx = -1
        for idx, t in enumerate(tokens):
            text_l = t["text"].lower()
            if any(kw in text_l for kw in trigger_keywords):
                trigger_token = t
                trigger_idx = idx
                break

        if not trigger_token:
            return []

        t_box = trigger_token["box_1000"]
        t_top, t_left, t_right = t_box[1], t_box[0], t_box[2]

        # The global sort (coarse y-bucket, then x) can place a short address line
        # ("KRBL Limited") just *before* its own long trigger line ("Manufactured &
        # Marketed by ...") on the same visual row. Re-admit those few immediate
        # predecessors that actually sit on / below the trigger's row.
        scan = [
            o for o in tokens[max(0, trigger_idx - 4):trigger_idx]
            if o["box_1000"][1] >= t_top - 10
        ] + tokens[trigger_idx + 1:]

        cluster = [trigger_token["text"]]
        t_bottom = t_box[3]
        for other in scan:
            o_box = other["box_1000"]
            dy = o_box[1] - t_bottom
            # Allow a small negative dy: consecutive label lines often overlap by a
            # few grid units, which must not break the vertical chain.
            if -20 <= dy <= vertical_lookahead_px:
                horiz_overlap = max(0, min(t_right, o_box[2]) - max(t_left, o_box[0]))
                if horiz_overlap > 0 or abs(o_box[0] - t_left) < 180:
                    cluster.append(other["text"])
                    t_bottom = max(t_bottom, o_box[3])
                    if len(cluster) >= max_lines:
                        break

        return cluster

# Singleton helper
_spatial_extractor_instance = None

def get_spatial_layout_extractor() -> SpatialLayoutEntityExtractor:
    global _spatial_extractor_instance
    if _spatial_extractor_instance is None:
        _spatial_extractor_instance = SpatialLayoutEntityExtractor()
    return _spatial_extractor_instance
