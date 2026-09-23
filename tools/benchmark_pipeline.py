r"""Benchmark Phase 4 recognition stages on a directory of page images.

Example:
    C:\olai-env\Scripts\python.exe tools/benchmark_pipeline.py samples/ --source-type modern_print
"""

import argparse
import json
import time
from pathlib import Path
from statistics import mean

from PIL import Image

from backend.pipeline.layout_service import group_lines_into_regions
from backend.pipeline.ocr_engine import get_ocr_engine
from backend.pipeline.preprocessing import load_and_orient_image, resize_for_ocr
from backend.pipeline.recognition_pipeline import apply_tesseract_gate


def _gpu_peak_memory_mb() -> float | None:
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        return round(torch.cuda.max_memory_allocated() / 1024 / 1024, 2)
    except Exception:
        return None


def benchmark(image_paths: list[Path], source_type: str, language: str = "ta") -> dict:
    ocr_engine = get_ocr_engine()
    stage_times = {"preprocess": [], "paddle": [], "layout": [], "tesseract": []}
    tesseract_lines = 0
    total_lines = 0
    page_times = []
    for page_number, path in enumerate(image_paths, start=1):
        started = time.perf_counter()
        preprocess_started = time.perf_counter()
        image = load_and_orient_image(path)
        image, _ = resize_for_ocr(image)
        stage_times["preprocess"].append(time.perf_counter() - preprocess_started)

        paddle_started = time.perf_counter()
        lines = ocr_engine.read_page(image, language=language)
        stage_times["paddle"].append(time.perf_counter() - paddle_started)
        total_lines += len(lines)

        layout_started = time.perf_counter()
        lines = group_lines_into_regions(lines)
        stage_times["layout"].append(time.perf_counter() - layout_started)

        before = time.perf_counter()
        gated = [line for line in lines if line.get("confidence", 0) < 0.95]
        tesseract_lines += len(gated)
        apply_tesseract_gate(image, lines, ocr_engine, language, "benchmark", page_number)
        stage_times["tesseract"].append(time.perf_counter() - before)
        page_times.append(time.perf_counter() - started)

    pages_per_minute = len(image_paths) / (sum(page_times) / 60) if page_times else 0.0
    return {
        "pages": len(image_paths),
        "source_type": source_type,
        "seconds_per_page": round(mean(page_times), 3) if page_times else 0.0,
        "pages_per_minute": round(pages_per_minute, 2),
        "tesseract_share": round(tesseract_lines / total_lines, 4) if total_lines else 0.0,
        "gpu_memory_peak_mb": _gpu_peak_memory_mb(),
        "stage_seconds": {name: round(sum(values), 3) for name, values in stage_times.items()},
        "stage_seconds_per_page": {
            name: round(mean(values), 3) if values else 0.0 for name, values in stage_times.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark OLAI recognition stages")
    parser.add_argument("pages", type=Path, help="directory containing page images")
    parser.add_argument("--source-type", default="modern_print")
    parser.add_argument("--language", default="ta")
    args = parser.parse_args()
    extensions = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
    image_paths = sorted(path for path in args.pages.iterdir() if path.suffix.lower() in extensions)
    if not image_paths:
        parser.error(f"No page images found in {args.pages}")
    print(json.dumps(benchmark(image_paths, args.source_type, args.language), indent=2))


if __name__ == "__main__":
    main()