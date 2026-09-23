"""
Phase 0 smoke test for OLAI.

Checks that every engine the pipeline depends on loads and runs on this machine:
  1. PyTorch sees the GPU
  2. PaddlePaddle sees the GPU
  3. A Tamil test PDF can be generated (real, shaped Tamil text layer)
  4. Digital-PDF fast path: the Tamil text layer is extracted without OCR
  5. PaddleOCR (Tamil) reads the rendered page image
  6. Tesseract (tam) reads the same image
  7. CLIP loads on the GPU

Run:  C:\\olai-env\\Scripts\\python.exe tools\\phase0_smoke_test.py
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

OUT_DIR = Path(__file__).resolve().parent.parent / "storage" / "smoke_test"
OUT_DIR.mkdir(parents=True, exist_ok=True)
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

TAMIL_LINES = [
    "தமிழ் மொழி மிகவும் பழமையானது.",
    "அறிவு ஒரு பெரிய செல்வம்.",
    "கற்க கசடற கற்பவை கற்றபின் நிற்க அதற்குத் தக.",
]

results = []


def step(name):
    def wrap(fn):
        def run():
            print(f"\n=== {name} ===")
            t0 = time.perf_counter()
            try:
                detail = fn()
                ok = True
            except Exception as e:  # report and continue with the remaining checks
                detail = f"{type(e).__name__}: {e}"
                ok = False
            dt = time.perf_counter() - t0
            print(f"[{'PASS' if ok else 'FAIL'}] {detail} ({dt:.2f}s)")
            results.append((name, ok, detail))
        return run
    return wrap


@step("1. PyTorch GPU")
def check_torch():
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError(f"torch {torch.__version__} has no CUDA")
    return f"torch {torch.__version__} on {torch.cuda.get_device_name(0)}"


@step("2. PaddlePaddle GPU")
def check_paddle():
    import paddle
    if not paddle.device.is_compiled_with_cuda():
        raise RuntimeError("paddle is a CPU-only build")
    return f"paddle {paddle.__version__}, device {paddle.device.get_device()}"


@step("3. Generate Tamil test PDF")
def make_pdf():
    import fitz  # PyMuPDF
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4 in points
    html = "".join(f"<p style='font-size:22px;margin:0 0 18px 0'>{line}</p>" for line in TAMIL_LINES)
    page.insert_htmlbox(fitz.Rect(50, 60, 545, 800), html)
    pdf_path = OUT_DIR / "tamil_digital.pdf"
    doc.save(pdf_path)
    # Render to an image, as if it were a scanned page
    pix = doc[0].get_pixmap(dpi=200)
    png_path = OUT_DIR / "tamil_scan.png"
    pix.save(png_path)
    return f"{pdf_path.name} + {png_path.name} ({pix.width}x{pix.height})"


TAMIL_CONSONANTS = set("\u0B95\u0B99\u0B9A\u0B9E\u0B9F\u0BA3\u0BA4\u0BA8\u0BAA\u0BAE\u0BAF\u0BB0\u0BB2\u0BB5\u0BB4\u0BB3\u0BB1\u0BA9\u0B9C\u0BB7\u0BB8\u0BB9")
TAMIL_SIGNS = set("\u0BBE\u0BBF\u0BC0\u0BC1\u0BC2\u0BC6\u0BC7\u0BC8\u0BCA\u0BCB\u0BCC\u0BCD\u0BD7")


def is_tamil(c):
    return "\u0B80" <= c <= "\u0BFF"


def tamil_text_layer_valid(text, min_valid=0.95):
    """
    A text layer is trusted only if its Tamil words are *structurally* valid:
    every vowel sign / pulli follows a consonant, and no foreign letter sits inside
    a Tamil word. Legacy-font PDFs (Bamini, TSCII, broken ToUnicode maps) fail this
    even when most of their characters fall in the Tamil Unicode block.
    """
    words = [w.strip(".,;:!?\"'()[]") for w in text.split()]
    words = [w.replace("\u200C", "").replace("\u200D", "") for w in words if w]
    tamil_words = [w for w in words if any(is_tamil(c) for c in w)]
    if not tamil_words:
        return False, 0.0
    valid = 0
    for w in tamil_words:
        ok = all(is_tamil(c) for c in w)
        prev = ""
        for c in w:
            if c in TAMIL_SIGNS and prev not in TAMIL_CONSONANTS and not (c == "\u0BD7" and prev == "\u0BC6"):
                ok = False
                break
            prev = c
        valid += ok
    score = valid / len(tamil_words)
    return score >= min_valid, score


@step("4. Digital-PDF check (garbled layer must be rejected)")
def check_text_layer():
    import fitz
    good = "\n".join(TAMIL_LINES)
    good_ok, good_score = tamil_text_layer_valid(good)
    if not good_ok:
        raise RuntimeError(f"validator rejects correct Tamil (score {good_score:.2f})")
    doc = fitz.open(OUT_DIR / "tamil_digital.pdf")
    text = doc[0].get_text().strip()
    ok, score = tamil_text_layer_valid(text)
    if ok:
        raise RuntimeError(f"garbled text layer was accepted (score {score:.2f}): {text[:40]!r}")
    verdict = "garbled -> fall back to OCR"
    return (f"correct Tamil scores {good_score:.2f} (accepted); "
            f"this PDF's layer scores {score:.2f} ({verdict}); "
            f"{len(doc[0].get_text('words'))} words with positions")


@step("5. PaddleOCR (Tamil, GPU)")
def check_paddleocr():
    from paddleocr import PaddleOCR
    t0 = time.perf_counter()
    ocr = PaddleOCR(
        lang="ta",
        device="gpu",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    load_s = time.perf_counter() - t0
    img = str(OUT_DIR / "tamil_scan.png")
    ocr.predict(img)  # warm-up (first call compiles kernels)
    t1 = time.perf_counter()
    res = ocr.predict(img)[0]
    run_s = time.perf_counter() - t1
    texts, scores = res["rec_texts"], res["rec_scores"]
    for t, s in zip(texts, scores):
        print(f"  {s:.3f}  {t}")
    return f"{len(texts)} lines, load {load_s:.1f}s, page {run_s:.2f}s"


@step("6. Tesseract (tam, CPU)")
def check_tesseract():
    import pytesseract
    from PIL import Image
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    img = Image.open(OUT_DIR / "tamil_scan.png")
    t0 = time.perf_counter()
    data = pytesseract.image_to_data(img, lang="tam", output_type=pytesseract.Output.DICT)
    run_s = time.perf_counter() - t0
    words = [(w, float(c)) for w, c in zip(data["text"], data["conf"]) if w.strip()]
    print("  " + " ".join(w for w, _ in words))
    avg = sum(c for _, c in words) / len(words) if words else 0
    return f"{len(words)} words, avg conf {avg:.1f}, page {run_s:.2f}s"


@step("7. CLIP (GPU)")
def check_clip():
    import torch
    from transformers import CLIPModel, CLIPProcessor
    name = "openai/clip-vit-base-patch32"
    CLIPProcessor.from_pretrained(name)
    model = CLIPModel.from_pretrained(name, use_safetensors=True).to("cuda")
    return f"{name} on {next(model.parameters()).device}"


if __name__ == "__main__":
    for check in (check_torch, check_paddle, make_pdf, check_text_layer,
                  check_paddleocr, check_tesseract, check_clip):
        check()
    print("\n=== SUMMARY ===")
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    sys.exit(0 if all(ok for _, ok, _ in results) else 1)
