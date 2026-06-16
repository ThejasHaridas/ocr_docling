"""Command-line interface for the bill extractor.

Usage:
    python -m bill_extractor <image_or_folder> [-o OUT] [--pretty/--compact]

A single image prints JSON to stdout (or ``-o file.json``). A folder is
processed in batch, writing one ``<name>.json`` next to each image (or into the
directory given by ``-o``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .converter import ModelDownloadError
from .extractor import extract_bill_json

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".pdf"}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bill_extractor",
        description="Parse bill/invoice images into structured JSON using Docling.",
    )
    p.add_argument("source", help="Path to an image/PDF or a folder of them.")
    p.add_argument(
        "-o",
        "--output",
        help="Output JSON file (single input) or output directory (folder input).",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--pretty", dest="pretty", action="store_true", default=True,
                   help="Pretty-print JSON (default).")
    g.add_argument("--compact", dest="pretty", action="store_false",
                   help="Emit compact single-line JSON.")
    return p


def _process_one(path: Path, pretty: bool) -> str:
    return extract_bill_json(path, pretty=pretty)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    source = Path(args.source)

    if not source.exists():
        print(f"error: no such file or directory: {source}", file=sys.stderr)
        return 1

    if source.is_dir():
        images = sorted(p for p in source.iterdir() if p.suffix.lower() in IMAGE_EXTS)
        if not images:
            print(f"error: no images found in {source}", file=sys.stderr)
            return 1
        out_dir = Path(args.output) if args.output else source
        out_dir.mkdir(parents=True, exist_ok=True)
        for img in images:
            try:
                payload = _process_one(img, args.pretty)
            except Exception as exc:  # keep batch going on a single failure
                print(f"warn: failed on {img.name}: {exc}", file=sys.stderr)
                continue
            out_file = out_dir / f"{img.stem}.json"
            out_file.write_text(payload, encoding="utf-8")
            print(f"wrote {out_file}")
        return 0

    try:
        payload = _process_one(source, args.pretty)
    except ModelDownloadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
