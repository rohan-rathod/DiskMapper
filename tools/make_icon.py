"""Generate the DiskMapper application icon.

Draws a blueprint-style treemap at several resolutions and packs them into a
multi-size .ico. Uses only the standard library (zlib + struct), so there is no
Pillow dependency.

    python tools\\make_icon.py
"""

from __future__ import annotations

import os
import struct
import zlib

BG = (0x0B, 0x2A, 0x4A)
GRID = (0x14, 0x40, 0x6B)
ACCENT = (0x59, 0xE3, 0xFF)

# (x0, y0, x1, y1, colour) in 0..1 space - mirrors the real treemap layout
BLOCKS = (
    (0.07, 0.07, 0.55, 0.60, (0x1D, 0x6F, 0xA5)),
    (0.58, 0.07, 0.93, 0.33, (0x1F, 0x8A, 0x70)),
    (0.58, 0.36, 0.93, 0.60, (0x8C, 0x5B, 0xB0)),
    (0.07, 0.63, 0.40, 0.93, (0xB5, 0x65, 0x2F)),
    (0.43, 0.63, 0.93, 0.93, (0x2E, 0x86, 0xAB)),
)

# Nested detail inside the largest block
INNER = (
    (0.11, 0.11, 0.33, 0.36),
    (0.36, 0.11, 0.51, 0.36),
    (0.11, 0.39, 0.51, 0.56),
)

SIZES = (16, 24, 32, 48, 64, 128, 256)


class Canvas:
    def __init__(self, size: int, colour) -> None:
        self.size = size
        self.buf = bytearray()
        for _ in range(size * size):
            self.buf += bytes((colour[0], colour[1], colour[2], 255))

    def _set(self, x: int, y: int, colour) -> None:
        if 0 <= x < self.size and 0 <= y < self.size:
            offset = (y * self.size + x) * 4
            self.buf[offset:offset + 3] = bytes(colour)

    def fill(self, x0, y0, x1, y1, colour) -> None:
        for y in range(max(int(y0), 0), min(int(y1), self.size)):
            for x in range(max(int(x0), 0), min(int(x1), self.size)):
                self._set(x, y, colour)

    def outline(self, x0, y0, x1, y1, colour, thickness=1) -> None:
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        for t in range(max(int(thickness), 1)):
            for x in range(x0, x1):
                self._set(x, y0 + t, colour)
                self._set(x, y1 - 1 - t, colour)
            for y in range(y0, y1):
                self._set(x0 + t, y, colour)
                self._set(x1 - 1 - t, y, colour)

    def to_png(self) -> bytes:
        raw = bytearray()
        stride = self.size * 4
        for y in range(self.size):
            raw.append(0)  # filter type: none
            raw += self.buf[y * stride:(y + 1) * stride]

        def chunk(tag: bytes, data: bytes) -> bytes:
            body = tag + data
            return (struct.pack(">I", len(data)) + body
                    + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

        header = struct.pack(">2I5B", self.size, self.size, 8, 6, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", header)
                + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
                + chunk(b"IEND", b""))


def render(size: int) -> bytes:
    canvas = Canvas(size, BG)
    step = max(size // 8, 2)

    for i in range(0, size, step):
        canvas.fill(i, 0, i + 1, size, GRID)
        canvas.fill(0, i, size, i + 1, GRID)

    thick = max(size // 32, 1)
    for x0, y0, x1, y1, colour in BLOCKS:
        left, top = x0 * size, y0 * size
        right, bottom = x1 * size, y1 * size
        canvas.fill(left, top, right, bottom, colour)
        canvas.outline(left, top, right, bottom, ACCENT, thick)

    if size >= 48:
        for x0, y0, x1, y1 in INNER:
            canvas.outline(x0 * size, y0 * size, x1 * size, y1 * size,
                           (0x8F, 0xD6, 0xF5), 1)

    canvas.outline(0, 0, size, size, ACCENT, thick)
    return canvas.to_png()


def build_ico(path: str) -> None:
    images = [(size, render(size)) for size in SIZES]

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        dimension = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32,
                               len(data), offset)
        blobs += data
        offset += len(data)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(header + entries + blobs)


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target = os.path.join(root, "assets", "diskmapper.ico")
    build_ico(target)
    print(f"Wrote {target} ({os.path.getsize(target):,} bytes, "
          f"sizes: {', '.join(str(s) for s in SIZES)})")
