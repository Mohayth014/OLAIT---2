import sys
import os
sys.path.insert(0, os.path.abspath("."))
import time
from PIL import Image
import numpy as np
import easyocr
import torch
from backend.config import DATASET_DIR
from backend.pipeline.preprocessing import load_and_orient_image, enhance_text_clarity

dataset_files = [f for f in os.listdir(DATASET_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.heic'))]
sample_path = os.path.join(DATASET_DIR, dataset_files[0])
print(f"Benchmarking on sample: {dataset_files[0]}")

img = load_and_orient_image(sample_path)
reader = easyocr.Reader(['en'], gpu=False, verbose=False)

# 1. Baseline: 1600px, batch_size=1 (default)
t0 = time.time()
scale = min(1.0, 1600 / max(img.size))
res_base = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
out_base = reader.readtext(np.array(res_base), batch_size=1)
t_base = time.time() - t0
print(f"\n[Baseline] 1600px, batch_size=1: {t_base:.2f}s, detected {len(out_base)} boxes")

# 2. Optimized: 1280px + CLAHE enhancement + batch_size=16
t1 = time.time()
scale_opt = min(1.0, 1280 / max(img.size))
res_opt = img.resize((int(img.width * scale_opt), int(img.height * scale_opt)), Image.Resampling.LANCZOS)
res_opt = enhance_text_clarity(res_opt)
out_opt = reader.readtext(np.array(res_opt), batch_size=16, contrast_ths=0.2, adjust_contrast=0.7)
t_opt = time.time() - t1
print(f"[Optimized] 1280px + CLAHE + batch_size=16: {t_opt:.2f}s, detected {len(out_opt)} boxes")

speedup = ((t_base - t_opt) / t_base) * 100
print(f"\n>>> Speedup: {t_base/t_opt:.2f}x faster ({speedup:.1f}% latency reduction)!")
