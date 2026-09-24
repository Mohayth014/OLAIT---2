"""Train and evaluate a compact Tamil line recognizer on Tamily-1.

This is a reproducible baseline for the cloned 0-5000 shard. It keeps the
Parquet images in place, trains a CRNN with CTC loss, and reports validation
exact-match accuracy and character error rate instead of treating loss as
accuracy. The resulting checkpoint is not silently wired into PaddleOCR; it
must beat the baseline before a production route is changed.
"""

import argparse
import io
import json
import random
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import numpy as np
import pyarrow.parquet as pq
import torch
from PIL import Image, ImageOps
from PIL import ImageEnhance, ImageFilter
from torch import nn
from torch.cuda.amp import GradScaler, autocast


IMAGE_HEIGHT = 64
IMAGE_WIDTH = 640
VAL_MODULUS = 10


def resolve_data_paths(data_path: Path) -> List[Path]:
    paths = sorted(data_path.glob("*.parquet")) if data_path.is_dir() else [data_path]
    if not paths:
        raise FileNotFoundError(f"No Parquet shards found under {data_path}")
    return paths


def load_labels(parquet_paths: Sequence[Path]) -> List[str]:
    labels = []
    for parquet_path in parquet_paths:
        table = pq.read_table(parquet_path, columns=["text"])
        labels.extend(str(value) for value in table.column("text").to_pylist())
    return labels


def build_charset(labels: Sequence[str]) -> List[str]:
    return sorted(set("".join(labels)))


def preprocess_image(image_bytes: bytes, augment: bool = False) -> torch.Tensor:
    image = Image.open(io.BytesIO(image_bytes)).convert("L")
    if augment:
        if random.random() < 0.35:
            image = ImageEnhance.Contrast(image).enhance(random.uniform(0.75, 1.3))
        if random.random() < 0.25:
            image = image.filter(ImageFilter.GaussianBlur(random.uniform(0.2, 0.7)))
    scale = IMAGE_HEIGHT / image.height
    width = min(IMAGE_WIDTH, max(1, round(image.width * scale)))
    image = image.resize((width, IMAGE_HEIGHT), Image.Resampling.LANCZOS)
    canvas = Image.new("L", (IMAGE_WIDTH, IMAGE_HEIGHT), 255)
    canvas.paste(image, (0, 0))
    array = np.asarray(canvas, dtype=np.float32) / 255.0
    return torch.from_numpy((array - 0.5) / 0.5).unsqueeze(0)


def iter_samples(parquet_paths: Sequence[Path], validation: bool) -> Iterable[Tuple[bytes, str]]:
    row_index = 0
    for parquet_path in parquet_paths:
        parquet = pq.ParquetFile(parquet_path)
        for group_index in range(parquet.num_row_groups):
            table = parquet.read_row_group(group_index, columns=["image", "text"])
            for image, text in zip(table.column("image").to_pylist(), table.column("text").to_pylist()):
                is_validation = row_index % VAL_MODULUS == 0
                if is_validation == validation:
                    yield image["bytes"], str(text)
                row_index += 1


def make_batches(samples: List[Tuple[bytes, str]], batch_size: int, shuffle: bool) -> Iterable[List[Tuple[bytes, str]]]:
    if shuffle:
        random.shuffle(samples)
    for start in range(0, len(samples), batch_size):
        yield samples[start:start + batch_size]


def encode_text(text: str, char_to_id: dict[str, int]) -> List[int]:
    return [char_to_id[char] for char in text]


def collate(samples: Sequence[Tuple[bytes, str]], char_to_id: dict[str, int], augment: bool = False):
    images = torch.stack([preprocess_image(image, augment=augment) for image, _ in samples])
    encoded = [encode_text(text, char_to_id) for _, text in samples]
    targets = torch.tensor([item for sequence in encoded for item in sequence], dtype=torch.long)
    target_lengths = torch.tensor([len(sequence) for sequence in encoded], dtype=torch.long)
    return images, targets, target_lengths


class TamilCRNN(nn.Module):
    def __init__(self, classes: int):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(64, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(128, 256, 3, padding=1), nn.BatchNorm2d(256), nn.ReLU(),
            nn.Conv2d(256, 256, 3, padding=1), nn.BatchNorm2d(256), nn.ReLU(),
            nn.MaxPool2d((2, 1)),
            nn.AdaptiveAvgPool2d((6, None)),
        )
        self.sequence = nn.LSTM(256 * 6, 256, num_layers=2, bidirectional=True, batch_first=True, dropout=0.1)
        self.classifier = nn.Linear(512, classes + 1)

    def forward(self, images):
        features = self.features(images)
        features = features.permute(0, 3, 1, 2).flatten(2)
        sequence, _ = self.sequence(features)
        return self.classifier(sequence).log_softmax(2)


def decode(logits: torch.Tensor, id_to_char: dict[int, str]) -> List[str]:
    # TamilCRNN returns batch-first logits: [batch, time, classes].
    # Decoding after transposing would mix samples across the batch.
    paths = logits.argmax(2).cpu().tolist()
    results = []
    for path in paths:
        previous = -1
        chars = []
        for token in path:
            if token != 0 and token != previous:
                chars.append(id_to_char[token])
            previous = token
        results.append("".join(chars))
    return results


def edit_distance(first: str, second: str) -> int:
    previous = list(range(len(second) + 1))
    for first_index, first_char in enumerate(first, 1):
        current = [first_index]
        for second_index, second_char in enumerate(second, 1):
            current.append(min(
                current[-1] + 1,
                previous[second_index] + 1,
                previous[second_index - 1] + (first_char != second_char),
            ))
        previous = current
    return previous[-1]


def evaluate(model, samples, char_to_id, id_to_char, device, batch_size):
    model.eval()
    exact = 0
    distance = 0
    characters = 0
    with torch.no_grad():
        for batch in make_batches(list(samples), batch_size, shuffle=False):
            images, _, _ = collate(batch, char_to_id)
            predictions = decode(model(images.to(device)), id_to_char)
            for prediction, (_, target) in zip(predictions, batch):
                exact += prediction == target
                distance += edit_distance(prediction, target)
                characters += len(target)
    return {
        "exact_match_percent": round(exact * 100 / len(samples), 2),
        "cer_percent": round(distance * 100 / max(characters, 1), 2),
        "samples": len(samples),
    }


def main():
    parser = argparse.ArgumentParser(description="Train a Tamil CRNN on Tamily-1")
    parser.add_argument("--data", type=Path, default=Path("storage/datasets/tamily-1/0-5000/train.parquet"))
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--output", type=Path, default=Path("storage/exports/tamily-1/tamil-crnn.pt"))
    parser.add_argument("--resume", action="store_true", help="resume model and optimizer state from --output")
    parser.add_argument("--target-exact", type=float, default=80.0)
    parser.add_argument("--patience", type=int, default=8)
    args = parser.parse_args()
    torch.manual_seed(7)
    random.seed(7)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    data_paths = resolve_data_paths(args.data)
    labels = load_labels(data_paths)
    charset = build_charset(labels)
    char_to_id = {char: index + 1 for index, char in enumerate(charset)}
    id_to_char = {index: char for char, index in char_to_id.items()}
    train_samples = list(iter_samples(data_paths, validation=False))
    validation_samples = list(iter_samples(data_paths, validation=True))
    model = TamilCRNN(len(charset)).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-4, weight_decay=1e-4)
    criterion = nn.CTCLoss(blank=0, zero_infinity=True)
    scaler = GradScaler(enabled=device.type == "cuda")
    history = []
    start_epoch = 1
    if args.resume and args.output.exists():
        checkpoint = torch.load(args.output, map_location=device)
        if checkpoint.get("charset") != charset:
            raise ValueError("Checkpoint charset does not match the dataset")
        model.load_state_dict(checkpoint["model"])
        if "optimizer" in checkpoint:
            optimizer.load_state_dict(checkpoint["optimizer"])
        if "scaler" in checkpoint:
            scaler.load_state_dict(checkpoint["scaler"])
        start_epoch = int(checkpoint.get("epoch", 0)) + 1
        history_path = args.output.with_suffix(".json")
        if not history_path.exists():
            history_path = args.output.with_name("tamil-crnn.json")
        history = json.loads(history_path.read_text(encoding="utf-8")).get("history", [])
    best_cer = min((record["cer_percent"] for record in history), default=float("inf"))
    best_exact = max((record["exact_match_percent"] for record in history), default=0.0)
    best_epoch = max((record["epoch"] for record in history if record["cer_percent"] == best_cer), default=0)
    stale_epochs = 0
    for epoch in range(start_epoch, args.epochs + 1):
        model.train()
        losses = []
        for batch in make_batches(train_samples, args.batch_size, shuffle=True):
            images, targets, target_lengths = collate(batch, char_to_id, augment=True)
            images = images.to(device)
            targets = targets.to(device)
            optimizer.zero_grad(set_to_none=True)
            with autocast(enabled=device.type == "cuda"):
                logits = model(images)
                input_lengths = torch.full((len(batch),), logits.shape[1], dtype=torch.long)
                loss = criterion(logits.transpose(0, 1), targets, input_lengths, target_lengths)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            scaler.step(optimizer)
            scaler.update()
            losses.append(float(loss.detach().cpu()))
        metrics = evaluate(model, validation_samples, char_to_id, id_to_char, device, args.batch_size)
        record = {"epoch": epoch, "loss": round(float(np.mean(losses)), 4), **metrics}
        history.append(record)
        improved = metrics["cer_percent"] < best_cer or (
            metrics["cer_percent"] == best_cer and metrics["exact_match_percent"] > best_exact
        )
        if improved:
            best_cer = metrics["cer_percent"]
            best_exact = metrics["exact_match_percent"]
            best_epoch = epoch
            stale_epochs = 0
            torch.save({"model": model.state_dict(), "optimizer": optimizer.state_dict(), "scaler": scaler.state_dict(),
                        "epoch": epoch, "charset": charset, "image_height": IMAGE_HEIGHT,
                        "image_width": IMAGE_WIDTH, "best_cer_percent": best_cer,
                        "best_exact_match_percent": best_exact}, args.output.with_name(args.output.stem + "-best.pt"))
        else:
            stale_epochs += 1
        print(json.dumps(record, ensure_ascii=False), flush=True)
        if metrics["exact_match_percent"] >= args.target_exact or stale_epochs >= args.patience:
            break
    args.output.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"model": model.state_dict(), "optimizer": optimizer.state_dict(), "scaler": scaler.state_dict(),
                "epoch": history[-1]["epoch"], "charset": charset, "image_height": IMAGE_HEIGHT,
                "image_width": IMAGE_WIDTH, "best_cer_percent": best_cer}, args.output)
    report = args.output.with_suffix(".json")
    report.write_text(json.dumps({"data": str(args.data), "device": str(device), "history": history}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checkpoint": str(args.output), "best_checkpoint": str(args.output.with_name(args.output.stem + "-best.pt")),
                      "best_epoch": best_epoch, "best_exact_match_percent": best_exact,
                      "best_cer_percent": best_cer, "final": history[-1]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()