import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath("."))
import time
import json
from pathlib import Path
import numpy as np
from PIL import Image

from backend.config import DATASET_DIR
from backend.pipeline.preprocessing import load_and_orient_image
from backend.pipeline.clip_engine import get_clip_engine
from backend.pipeline.ocr_engine import get_ocr_engine
from backend.pipeline.readability import evaluate_font_and_readability
from backend.pipeline.extraction import extract_structured_information
from backend.pipeline.rules_engine import LegalMetrologyRuleEngine
from backend.database.db import get_all_rules

def evaluate_clip_accuracy():
    print("=" * 70)
    print("1. CLIP ZERO-SHOT CLASSIFIER ACCURACY & CONFIDENCE (52 DATASET IMAGES)")
    print("=" * 70)
    
    clip_engine = get_clip_engine()
    dataset_path = Path(DATASET_DIR)
    files = [f for f in os.listdir(dataset_path) if f.lower().endswith(('.jpg', '.jpeg', '.heic', '.png'))]
    
    confidences = []
    category_counts = {}
    top3_confidences = []
    category_confidence_map = {}
    
    t0 = time.time()
    for idx, f in enumerate(files):
        img = load_and_orient_image(dataset_path / f)
        cats = clip_engine.classify_category(img)
        if cats:
            top_cat = cats[0]["label"]
            top_conf = cats[0]["confidence"]
            confidences.append(top_conf)
            category_counts[top_cat] = category_counts.get(top_cat, 0) + 1
            if top_cat not in category_confidence_map:
                category_confidence_map[top_cat] = []
            category_confidence_map[top_cat].append(top_conf)
            top3_sum = sum(c["confidence"] for c in cats[:3])
            top3_confidences.append(top3_sum)
    
    total_time = time.time() - t0
    avg_conf = float(np.mean(confidences) * 100)
    std_conf = float(np.std(confidences) * 100)
    median_conf = float(np.median(confidences) * 100)
    top3_avg = float(np.mean(top3_confidences) * 100)
    high_conf_pct = float((sum(1 for c in confidences if c >= 0.50) / len(confidences)) * 100)
    very_high_conf_pct = float((sum(1 for c in confidences if c >= 0.70) / len(confidences)) * 100)
    
    print(f"Total Dataset Images Evaluated: {len(files)}")
    print(f"Total CLIP Inference Time: {total_time:.2f}s ({total_time/len(files)*1000:.1f}ms / image)")
    print(f"Mean Top-1 Classification Confidence: {avg_conf:.2f}% (±{std_conf:.2f}%)")
    print(f"Median Top-1 Confidence: {median_conf:.2f}%")
    print(f"Top-3 Cumulative Confidence: {top3_avg:.2f}%")
    print(f"High-Confidence Classifications (>=50%): {high_conf_pct:.1f}% ({sum(1 for c in confidences if c >= 0.50)}/{len(files)})")
    print(f"Very High-Confidence Classifications (>=70%): {very_high_conf_pct:.1f}% ({sum(1 for c in confidences if c >= 0.70)}/{len(files)})")
    
    print("\nCategory Distribution & Mean Confidence:")
    cat_summary = {}
    for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
        m_conf = float(np.mean(category_confidence_map[cat]) * 100)
        cat_summary[cat] = {"count": count, "pct": round(count/len(files)*100, 1), "mean_conf": round(m_conf, 1)}
        print(f"  - {cat:35s}: {count:2d} ({count/len(files)*100:4.1f}%) | Avg Conf: {m_conf:5.1f}%")
    
    return {
        "dataset_size": len(files),
        "mean_top1_conf": round(avg_conf, 2),
        "std_top1_conf": round(std_conf, 2),
        "median_top1_conf": round(median_conf, 2),
        "top3_avg_conf": round(top3_avg, 2),
        "high_conf_pct": round(high_conf_pct, 1),
        "very_high_conf_pct": round(very_high_conf_pct, 1),
        "latency_per_image_ms": round(total_time/len(files)*1000, 1),
        "categories": cat_summary
    }

def evaluate_pipeline_accuracy(sample_size: int = 10):
    print("\n" + "=" * 70)
    print(f"2. DUAL-OCR & LEGAL METROLOGY EXTRACTION ACCURACY ({sample_size} REPRESENTATIVE SAMPLES)")
    print("=" * 70)
    
    clip_engine = get_clip_engine()
    ocr_engine = get_ocr_engine()
    configured_rules = [r for r in get_all_rules() if r.get("enabled", 1) == 1]
    rule_engine = LegalMetrologyRuleEngine(configured_rules)
    
    dataset_path = Path(DATASET_DIR)
    files = [f for f in os.listdir(dataset_path) if f.lower().endswith(('.jpg', '.jpeg', '.heic', '.png'))]
    
    # Stratified selection across the dataset
    step = max(1, len(files) // sample_size)
    samples = [files[i * step] for i in range(sample_size)]
    
    results = []
    extraction_hits = {
        "Rule 6(1)(a) - Manufacturer / Packer": 0,
        "Rule 6(1)(b) - Generic Name / Commodity": 0,
        "Rule 6(1)(c) - Net Quantity": 0,
        "Rule 6(1)(d) - Date of Mfg / Expiry": 0,
        "Rule 6(1)(e) - MRP & Tax Declaration": 0,
        "Rule 6(1)(f) - Consumer Care Helpline/Email": 0,
        "FSSAI License (14-Digit)": 0,
        "Unit Sale Price (Rule 6(10))": 0
    }
    
    ocr_confidences = []
    total_boxes = 0
    total_ocr_latency = 0.0
    decisions = {"COMPLIANT": 0, "NON_COMPLIANT": 0, "REVIEW_REQUIRED": 0}
    
    for idx, f in enumerate(samples):
        img_path = dataset_path / f
        print(f"[{idx+1:2d}/{sample_size}] Inspecting: {f[:28]:28s} ...", end=" ", flush=True)
        t_start = time.time()
        img = load_and_orient_image(img_path)
        
        # 1. CLIP Classification
        clip_categories = clip_engine.classify_category(img)
        
        # 2. Dual-OCR
        boxes, full_text = ocr_engine.run_ocr(img)
        readability_eval = evaluate_font_and_readability(img, boxes)
        t_sample = time.time() - t_start
        total_ocr_latency += t_sample
        
        total_boxes += len(boxes)
        box_confs = [b["confidence"] for b in boxes]
        if box_confs:
            ocr_confidences.extend(box_confs)
            
        # 3. Information Extraction
        extracted = extract_structured_information(boxes, full_text, clip_categories)
        
        # 4. Statutory Rule Engine
        overall_status, overall_conf, rule_results, violations, summary = rule_engine.evaluate(
            extracted, readability_eval
        )
        decisions[overall_status] = decisions.get(overall_status, 0) + 1
        
        # Check extraction recall
        if extracted.get("manufacturer", {}).get("value"):
            extraction_hits["Rule 6(1)(a) - Manufacturer / Packer"] += 1
        if extracted.get("product_name", {}).get("value"):
            extraction_hits["Rule 6(1)(b) - Generic Name / Commodity"] += 1
        if extracted.get("net_quantity", {}).get("value"):
            extraction_hits["Rule 6(1)(c) - Net Quantity"] += 1
        if extracted.get("mfg_date", {}).get("value") or extracted.get("best_before", {}).get("value"):
            extraction_hits["Rule 6(1)(d) - Date of Mfg / Expiry"] += 1
        if extracted.get("mrp", {}).get("value"):
            extraction_hits["Rule 6(1)(e) - MRP & Tax Declaration"] += 1
        if extracted.get("consumer_care", {}).get("value"):
            extraction_hits["Rule 6(1)(f) - Consumer Care Helpline/Email"] += 1
        if extracted.get("fssai_license", {}).get("value"):
            extraction_hits["FSSAI License (14-Digit)"] += 1
        if extracted.get("unit_sale_price", {}).get("value"):
            extraction_hits["Unit Sale Price (Rule 6(10))"] += 1
            
        avg_sample_conf = np.mean(box_confs)*100 if box_confs else 0.0
        print(f"{t_sample:5.2f}s | Boxes: {len(boxes):3d} | OCR Conf: {avg_sample_conf:5.1f}% | {overall_status}")
        
        results.append({
            "filename": f,
            "latency_s": round(t_sample, 2),
            "text_boxes": len(boxes),
            "avg_ocr_confidence": round(avg_sample_conf, 1),
            "status": overall_status,
            "violations_count": len(violations),
            "violations": violations[:2] # sample first 2
        })
        
    avg_ocr_conf = float(np.mean(ocr_confidences) * 100) if ocr_confidences else 0.0
    avg_latency = float(total_ocr_latency / sample_size)
    
    print("\n--- Statutory Declaration Extraction Recall Rates ---")
    hit_summary = {}
    for declaration, hits in extraction_hits.items():
        recall = (hits / sample_size) * 100
        hit_summary[declaration] = {"hits": hits, "total": sample_size, "recall_pct": round(recall, 1)}
        print(f"  * {declaration:45s}: {hits:2d}/{sample_size:2d} ({recall:5.1f}%)")
        
    print(f"\n--- System Summary Metrics ---")
    print(f"Average Pipeline Latency: {avg_latency:.2f}s per image")
    print(f"Total Text Regions Detected: {total_boxes} boxes (avg {total_boxes/sample_size:.1f} boxes/image)")
    print(f"Mean Character Recognition Confidence: {avg_ocr_conf:.2f}%")
    print(f"Statutory Enforcement Decisions: {decisions}")
    
    return {
        "sample_size": sample_size,
        "avg_pipeline_latency_s": round(avg_latency, 2),
        "total_boxes_detected": total_boxes,
        "mean_ocr_confidence": round(avg_ocr_conf, 2),
        "declaration_recalls": hit_summary,
        "enforcement_decisions": decisions,
        "samples": results
    }

if __name__ == "__main__":
    clip_eval = evaluate_clip_accuracy()
    pipeline_eval = evaluate_pipeline_accuracy(sample_size=10)
    
    full_report = {
        "clip_accuracy": clip_eval,
        "pipeline_accuracy": pipeline_eval,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    
    report_path = Path("scratch/accuracy_evaluation_report.json")
    with open(report_path, "w") as f:
        json.dump(full_report, f, indent=2)
        
    print(f"\n[Done] Complete benchmark evaluation saved to {report_path.resolve()}")
