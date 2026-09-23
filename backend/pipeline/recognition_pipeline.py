"""Phase 4 recognition orchestration and confidence routing."""

import asyncio
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from PIL import Image

from backend.config import (
    CLIP_SAMPLE_PAGES,
    CROPS_DIR,
    PROCESSING_ROUTES,
    TESSERACT_AUDIT_RATE,
    TESSERACT_GATE_CONFIDENCE,
)
from backend.pipeline.layout_service import group_lines_into_regions


def sample_page_numbers(page_count: int, sample_count: int = CLIP_SAMPLE_PAGES) -> List[int]:
    """Return unique zero-based page indexes spread across the document."""
    if page_count <= 0:
        return []
    if sample_count >= page_count:
        return list(range(page_count))
    return sorted({round(position * (page_count - 1) / (sample_count - 1))
                   for position in range(sample_count)})


def weighted_source_vote(classifications: Sequence[Sequence[Dict[str, Any]]]) -> Dict[str, Any]:
    """Choose the source type by summing each page's top-class confidence."""
    totals: Dict[str, float] = {}
    for page_results in classifications:
        if not page_results:
            continue
        top = max(page_results, key=lambda item: float(item.get("confidence", 0)))
        source_type = top["source_type"]
        totals[source_type] = totals.get(source_type, 0.0) + float(top.get("confidence", 0))
    if not totals:
        return {"source_type": None, "confidence": 0.0, "votes": {}}
    winner, score = max(totals.items(), key=lambda item: item[1])
    total = sum(totals.values())
    return {"source_type": winner, "confidence": round(score / total, 4), "votes": totals}


def should_reread_line(confidence: float, audit_rate: float = TESSERACT_AUDIT_RATE, rng: Optional[random.Random] = None) -> bool:
    """Apply the low-confidence gate plus a reproducible random audit sample."""
    if confidence < TESSERACT_GATE_CONFIDENCE:
        return True
    return (rng or random).random() < audit_rate


def crop_line(image: Image.Image, bbox: Sequence[float], padding: int = 4) -> Image.Image:
    width, height = image.size
    x0, y0, x1, y1 = (round(value) for value in bbox)
    return image.crop((max(0, x0 - padding), max(0, y0 - padding),
                       min(width, x1 + padding), min(height, y1 + padding)))


def _read_line_task(ocr_engine: Any, image: Image.Image, line: Dict[str, Any], language: str,
                    crop_path: Path) -> Dict[str, Any]:
    crop = crop_line(image, line["bbox"])
    crop.save(crop_path)
    return ocr_engine.read_line(crop, language=language)


def apply_tesseract_gate(
    image: Image.Image,
    lines: List[Dict[str, Any]],
    ocr_engine: Any,
    language: str,
    document_id: str,
    page_number: int,
    rng: Optional[random.Random] = None,
) -> List[Dict[str, Any]]:
    """Re-read selected lines concurrently while preserving Paddle readings."""
    selected = [line for line in lines if should_reread_line(float(line.get("confidence", 0)), rng=rng)]
    if not selected:
        return lines
    crop_dir = CROPS_DIR / document_id
    crop_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor() as executor:
        futures = [executor.submit(_read_line_task, ocr_engine, image, line, language,
                                   crop_dir / f"page-{page_number:04d}-line-{index:04d}.png")
                   for index, line in enumerate(selected)]
        for line, future in zip(selected, futures):
            line["tesseract"] = future.result()
    return lines


async def classify_samples_and_ocr_first_pages(
    images: Sequence[Image.Image],
    sample_indexes: Sequence[int],
    clip_engine: Any,
    ocr_engine: Any,
    language: str,
    rec_model: Optional[str],
) -> Dict[str, Any]:
    """Run CLIP sampling alongside OCR of the initial pages."""
    clip_task = asyncio.gather(*[
        asyncio.to_thread(clip_engine.classify_source_type, images[index])
        for index in sample_indexes
    ])
    first_page_indexes = list(range(min(2, len(images))))
    ocr_task = asyncio.gather(*[
        asyncio.to_thread(ocr_engine.read_page, images[index], language, rec_model)
        for index in first_page_indexes
    ])
    classifications, first_pages = await asyncio.gather(clip_task, ocr_task)
    return {
        "classifications": classifications,
        "source_vote": weighted_source_vote(classifications),
        "first_pages": dict(zip(first_page_indexes, first_pages)),
    }


async def recognize_pages(
    images: Sequence[Image.Image],
    document_id: str,
    language: str,
    source_type: str,
    clip_engine: Any,
    ocr_engine: Any,
    rng: Optional[random.Random] = None,
) -> List[Dict[str, Any]]:
    """Recognize pages with one Paddle reader and pipelined CPU second opinions."""
    route = PROCESSING_ROUTES.get(source_type, PROCESSING_ROUTES["modern_print"])
    sample_indexes = sample_page_numbers(len(images))
    initial = await classify_samples_and_ocr_first_pages(
        images, sample_indexes, clip_engine, ocr_engine, language, route.get("paddle_rec_model"),
    )
    paddle_pages = initial["first_pages"]
    results: List[Dict[str, Any]] = []
    for page_number, image in enumerate(images):
        if page_number not in paddle_pages:
            paddle_pages[page_number] = await asyncio.to_thread(
                ocr_engine.read_page, image, language, route.get("paddle_rec_model"),
            )
        lines = group_lines_into_regions(paddle_pages[page_number])
        lines = await asyncio.to_thread(
            apply_tesseract_gate, image, lines, ocr_engine, language, document_id,
            page_number + 1, rng,
        )
        results.append({"page_number": page_number + 1, "lines": lines})
    return results