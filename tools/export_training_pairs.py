"""Export reviewer-corrected line pairs for recognition-model fine-tuning.

The output is PaddleOCR recognition format: one image path and its verified
transcription per line. It intentionally refuses to invent labels from OCR.
"""

import argparse
from pathlib import Path

from backend.config import STORAGE_DIR
from backend.database import db


def export_pairs(source_type: str, output_dir: Path) -> int:
    conn = db.get_connection()
    rows = conn.execute(
        """SELECT crop_path, text FROM training_pairs
           WHERE source_type = ? AND text <> '' ORDER BY id""",
        (source_type,),
    ).fetchall()
    conn.close()
    output_dir.mkdir(parents=True, exist_ok=True)
    label_path = output_dir / "rec_gt.txt"
    with label_path.open("w", encoding="utf-8", newline="\n") as labels:
        for row in rows:
            crop = STORAGE_DIR / row["crop_path"]
            if crop.exists():
                labels.write(f"{crop.resolve()}\t{row['text'].replace(chr(9), ' ').strip()}\n")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export verified OCR crops for model training")
    parser.add_argument("--source-type", default="palm_leaf")
    parser.add_argument("--output", type=Path, default=STORAGE_DIR / "exports" / "training")
    args = parser.parse_args()
    count = export_pairs(args.source_type, args.output / args.source_type)
    print(f"Exported {count} verified {args.source_type} training pairs to {args.output / args.source_type}")


if __name__ == "__main__":
    main()