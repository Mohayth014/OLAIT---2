"""Geometric page layout and reading-order fallback for OCR lines."""

from typing import Any, Dict, List


def _overlap_ratio(first: Dict[str, Any], second: Dict[str, Any]) -> float:
    first_box = first["bbox"]
    second_box = second["bbox"]
    first_height = max(1.0, first_box[3] - first_box[1])
    second_height = max(1.0, second_box[3] - second_box[1])
    overlap = max(0.0, min(first_box[3], second_box[3]) - max(first_box[1], second_box[1]))
    return overlap / min(first_height, second_height)


def group_lines_into_regions(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group nearby OCR lines into geometric columns/blocks.

    The fallback intentionally uses only line boxes so it remains available when
    PP-Structure is not installed. Returned lines are copied and include a
    zero-based ``line_order`` and a one-based ``region_order``.
    """
    if not lines:
        return []

    ordered = sorted(lines, key=lambda line: (line["bbox"][1], line["bbox"][0]))
    regions: List[Dict[str, Any]] = []
    median_height = sorted(max(1.0, line["bbox"][3] - line["bbox"][1]) for line in ordered)[len(ordered) // 2]
    column_gap = median_height * 2.5

    for line in ordered:
        x0, y0, x1, y1 = line["bbox"]
        matching = None
        for region in regions:
            if abs(x0 - region["x0"]) <= column_gap:
                matching = region
                break
        if matching is None:
            matching = {
                "region_type": "text",
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "anchor": line,
                "lines": [],
            }
            regions.append(matching)
        matching["lines"].append(line)
        matching["x0"] = min(matching["x0"], x0)
        matching["y0"] = min(matching["y0"], y0)
        matching["x1"] = max(matching["x1"], x1)
        matching["y1"] = max(matching["y1"], y1)
        matching["anchor"] = line

    # Reading order is left column to right column, then top to bottom.
    regions.sort(key=lambda region: (region["x0"], region["y0"]))
    result: List[Dict[str, Any]] = []
    line_order = 0
    for region_order, region in enumerate(regions, start=1):
        region_lines = sorted(region["lines"], key=lambda line: (line["bbox"][1], line["bbox"][0]))
        for line in region_lines:
            item = dict(line)
            item["line_order"] = line_order
            item["region_order"] = region_order
            item["region"] = {
                "region_type": region["region_type"],
                "bbox": [region["x0"], region["y0"], region["x1"], region["y1"],],
            }
            result.append(item)
            line_order += 1
    return result