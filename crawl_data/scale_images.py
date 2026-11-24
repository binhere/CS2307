#!/usr/bin/env python3
"""Scale and compress listing images to max 1080px (JPEG quality=80).

Reads:
  crawl_data/data/images/<property_type>/<district>/<listing_id>/*.jpg

Writes:
  crawl_data/data/images_scaled/<property_type>/<district>/<listing_id>/*.jpg
"""
from __future__ import annotations

import argparse
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image, ImageOps

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_INPUT = DATA_DIR / "images"
DEFAULT_OUTPUT = DATA_DIR / "images_scaled"


def process_one(src_path: Path, dst_path: Path, max_side: int, quality: int) -> tuple[str, int, int]:
    src_size = src_path.stat().st_size
    if dst_path.exists() and dst_path.stat().st_size > 0:
        return "skipped", src_size, dst_path.stat().st_size

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src_path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        w, h = img.size
        if max(w, h) > max_side:
            img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

        tmp_path = dst_path.with_suffix(".tmp.jpg")
        img.save(tmp_path, format="JPEG", quality=quality, optimize=True)
        tmp_path.replace(dst_path)

    return "scaled", src_size, dst_path.stat().st_size


def main() -> None:
    parser = argparse.ArgumentParser(description="Scale listing images to max side 1080px.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-side", type=int, default=1080)
    parser.add_argument("--quality", type=int, default=80)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    files = [Path(r) / f for r, _, fs in os.walk(args.input) for f in fs if not f.endswith(".tmp.jpg")]
    print(f"Found {len(files)} images in {args.input} -> scaling to {args.output} (max={args.max_side}px, q={args.quality})")

    lock = threading.Lock()
    counts = {"scaled": 0, "skipped": 0, "failed": 0}
    src_bytes = 0
    dst_bytes = 0
    failures: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_one, p, args.output / p.relative_to(args.input), args.max_side, args.quality): p
            for p in files
        }
        for idx, fut in enumerate(as_completed(futures), 1):
            src = futures[fut]
            try:
                status, sb, db = fut.result()
                with lock:
                    counts[status] += 1
                    src_bytes += sb
                    dst_bytes += db
            except Exception as exc:
                with lock:
                    counts["failed"] += 1
                    failures.append((str(src), repr(exc)))
            if idx % 5000 == 0 or idx == len(files):
                print(
                    f"Progress {idx}/{len(files)} | {counts} | "
                    f"{src_bytes/(1024**2):.1f} MB -> {dst_bytes/(1024**2):.1f} MB",
                    flush=True,
                )

    if failures:
        print(f"Failed ({len(failures)}):")
        for path, err in failures[:10]:
            print(" ", path, err)


if __name__ == "__main__":
    main()
