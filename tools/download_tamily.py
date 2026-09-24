"""
Fast, resumable downloader for Tamily-1 shards (sasicodes/tamily-1 on Hugging Face, MIT licence).

Each shard is ~3 GB / 5,000 rendered ancient-Tamil text images. A single HTTP stream from the
Hugging Face CDN is slow on this connection, so the file is fetched as fixed-size byte ranges over
several parallel connections, written in place, and verified against the official SHA-256.

Resumable: finished ranges are recorded in <file>.progress.json. A partial file left by a plain
sequential download (no progress file) is kept and only the missing tail is fetched.

Usage:
  python tools/download_tamily.py 2                  # shard 002 -> storage/datasets/tamily-1/10000-15000/
  python tools/download_tamily.py 2 3 4              # several shards, one after another
  python tools/download_tamily.py 1 --out storage/datasets/tamily-1/0-10000/train_shard_001.parquet
"""
import argparse
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

REPO = "sasicodes/tamily-1"
TREE_URL = f"https://huggingface.co/api/datasets/{REPO}/tree/main/data"
FILE_URL = f"https://huggingface.co/datasets/{REPO}/resolve/main/data/{{name}}"
ROWS_PER_SHARD = 5000
DEFAULT_DEST = Path("storage/datasets/tamily-1")
CHUNK = 4 * 1024 * 1024  # small ranges finish before this connection tends to drop


def shard_metadata():
    """{shard_index: {'name', 'size', 'sha256'}} from the Hugging Face file tree."""
    files = requests.get(TREE_URL, timeout=60).json()
    meta = {}
    for f in files:
        name = f["path"].split("/")[-1]
        if name.startswith("train_shard_"):
            meta[int(name[len("train_shard_"):len("train_shard_") + 3])] = {
                "name": name, "size": f["size"], "sha256": f["lfs"]["oid"],
            }
    return meta


def default_path(index: int, dest: Path) -> Path:
    start = index * ROWS_PER_SHARD
    return dest / f"{start}-{start + ROWS_PER_SHARD}" / f"train_shard_{index:03d}.parquet"


def fetch_range(url: str, path: Path, start: int, end: int, attempts: int = 20) -> int:
    """
    Download bytes [start, end] into the same offsets of `path`. Returns bytes written.
    A dropped connection resumes from the last byte received rather than restarting the range.
    """
    expected = end - start + 1
    written = 0
    for attempt in range(1, attempts + 1):
        try:
            pos = start + written
            with requests.get(url, headers={"Range": f"bytes={pos}-{end}"}, stream=True, timeout=(15, 60)) as r:
                if r.status_code != 206:
                    raise IOError(f"expected 206 Partial Content, got {r.status_code}")
                with open(path, "r+b") as fh:
                    fh.seek(pos)
                    for block in r.iter_content(256 * 1024):
                        fh.write(block)
                        written += len(block)
            if written != expected:
                raise IOError(f"short read {written}/{expected}")
            return written
        except Exception as e:
            if attempt == attempts:
                raise
            if attempt >= 3:  # occasional drops are normal; only report persistent trouble
                print(f"  range {start}-{end}: attempt {attempt} broke at {written}/{expected} bytes ({type(e).__name__}); resuming",
                      flush=True)
            time.sleep(min(20, attempt))
    return written


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def download_shard(index: int, meta: dict, out: Path, connections: int) -> None:
    name, size, sha = meta["name"], meta["size"], meta["sha256"]
    out.parent.mkdir(parents=True, exist_ok=True)
    state_path = out.with_name(out.name + ".progress.json")
    print(f"\n=== shard {index:03d} ({size / 1e9:.2f} GB) -> {out}", flush=True)

    existing = out.stat().st_size if out.exists() else 0
    if existing == size and not state_path.exists():
        print("file already complete; verifying hash", flush=True)
    else:
        if state_path.exists():
            state = json.loads(state_path.read_text())
            done, prefix = set(state["done"]), state["prefix"]
        else:
            # A sequential partial download: its bytes [0, existing) are valid.
            done, prefix = set(), min(existing, size)
            if prefix:
                print(f"keeping {prefix / 1e9:.2f} GB already downloaded", flush=True)
        with open(out, "ab") as fh:
            fh.truncate(size)  # pre-size so every range can be written in place

        ranges = [(s, min(s + CHUNK, size) - 1) for s in range(prefix, size, CHUNK)]
        todo = [r for r in ranges if r[0] not in done]
        have = size - sum(e - s + 1 for s, e in todo)
        lock = threading.Lock()
        progress = {"bytes": have, "last_print": 0.0, "t0": time.time(), "start_bytes": have}

        def save_state():
            state_path.write_text(json.dumps({"size": size, "prefix": prefix, "done": sorted(done)}))

        url = FILE_URL.format(name=name)
        with ThreadPoolExecutor(max_workers=connections) as pool:
            futures = {pool.submit(fetch_range, url, out, s, e): (s, e) for s, e in todo}
            for fut in as_completed(futures):
                s, e = futures[fut]
                n = fut.result()
                with lock:
                    done.add(s)
                    progress["bytes"] += n
                    save_state()
                    now = time.time()
                    if now - progress["last_print"] >= 5 or progress["bytes"] >= size:
                        rate = (progress["bytes"] - progress["start_bytes"]) / max(now - progress["t0"], 1e-6)
                        left = (size - progress["bytes"]) / rate if rate > 0 else 0
                        print(f"  {progress['bytes'] / 1e6:,.0f} / {size / 1e6:,.0f} MB "
                              f"({100 * progress['bytes'] / size:.1f}%)  {rate / 1e6:.1f} MB/s  ~{left / 60:.1f} min left",
                              flush=True)
                        progress["last_print"] = now

    print("verifying sha256...", flush=True)
    digest = sha256_of(out)
    if digest != sha:
        raise SystemExit(f"HASH MISMATCH for shard {index:03d}: {digest} != {sha}. "
                         f"Delete {state_path.name} to re-download.")
    state_path.unlink(missing_ok=True)
    print(f"shard {index:03d} OK (sha256 verified)", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("shards", type=int, nargs="+", help="shard indices, e.g. 2 for rows 10000-15000")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--out", type=Path, help="explicit output file (single shard only)")
    parser.add_argument("--connections", type=int, default=8)
    args = parser.parse_args()
    if args.out and len(args.shards) != 1:
        parser.error("--out works with exactly one shard")

    meta = shard_metadata()
    for index in args.shards:
        if index not in meta:
            raise SystemExit(f"shard {index} does not exist (0-{max(meta)})")
        out = args.out or default_path(index, args.dest)
        download_shard(index, meta[index], out, args.connections)
    print("\nALL DONE", flush=True)


if __name__ == "__main__":
    sys.exit(main())
