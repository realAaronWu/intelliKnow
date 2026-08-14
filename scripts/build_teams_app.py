#!/usr/bin/env python3
"""Build a sideloadable Microsoft Teams app package for IntelliKnow."""

from __future__ import annotations

import argparse
import json
import struct
import uuid
import zlib
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "teams-app" / "manifest.template.json"


def _png(size: int, *, outline: bool) -> bytes:
    teal = (14, 116, 144, 255)
    clear = (0, 0, 0, 0)
    white = (255, 255, 255, 255)
    pixels: list[bytes] = []
    margin = max(2, size // 7)
    stem = max(2, size // 9)
    center = size // 2
    for y in range(size):
        row = bytearray()
        for x in range(size):
            base = clear if outline else teal
            vertical = margin <= x < margin + stem and margin <= y < size - margin
            diagonal = abs((x - (margin + stem)) - abs(y - center)) <= stem
            mark = vertical or (
                x >= margin + stem and x < size - margin and diagonal
            )
            row.extend(white if mark else base)
        pixels.append(b"\x00" + bytes(row))
    raw = b"".join(pixels)

    def chunk(kind: bytes, value: bytes) -> bytes:
        return (
            struct.pack(">I", len(value))
            + kind
            + value
            + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(
        b"IDAT", zlib.compress(raw, 9)
    ) + chunk(b"IEND", b"")


def build(app_id: str, public_url: str, output: Path) -> None:
    try:
        uuid.UUID(app_id)
    except ValueError as exc:
        raise SystemExit("--app-id must be the Application (client) ID UUID") from exc
    parsed = urlparse(public_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise SystemExit("--public-url must be a public HTTPS base URL")

    raw = TEMPLATE.read_text()
    manifest = json.loads(
        raw.replace("{{APP_ID}}", app_id).replace("{{PUBLIC_HOST}}", parsed.hostname)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as package:
        package.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        package.writestr("color.png", _png(192, outline=False))
        package.writestr("outline.png", _png(32, outline=True))
    print(f"Teams app package: {output}")
    print(f"Azure Bot messaging endpoint: {public_url.rstrip('/')}/api/messages")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--public-url", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist" / "intelliknow-teams.zip",
    )
    args = parser.parse_args()
    build(args.app_id, args.public_url, args.output)


if __name__ == "__main__":
    main()
