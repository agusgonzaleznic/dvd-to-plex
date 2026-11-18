"""Contact sheets: a few subtitle images stacked into one PNG, each row labelled with its cue
number, so a proofreading agent reads one picture per handful of cues.

Labels are drawn from a built-in 5x7 digit font, so no image library is needed. Reading PNGs back
covers only what spu.write_png writes: 8-bit grayscale, no interlacing.
"""
from __future__ import annotations

import pathlib
import struct
import zlib

from dvdtoplex import spu

DIGITS = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
}
SCALE = 4
LABEL_WIDTH = 6 * SCALE * 5 + 16          # room for five digits
BAR = 6                                   # gray separator between rows


def read_png(path: pathlib.Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    pos, idat, w, h = 8, b"", 0, 0
    while pos < len(data):
        n, kind = struct.unpack_from(">I4s", data, pos)
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            w, h, depth, colour = struct.unpack_from(">IIBB", body)
            if (depth, colour) != (8, 0):
                raise ValueError(f"{path.name}: only 8-bit grayscale PNGs are read here")
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    if any(raw[k * (w + 1)] for k in range(h)):
        raise ValueError(f"{path.name}: filtered rows are not read here")
    return w, h, b"".join(raw[k * (w + 1) + 1:(k + 1) * (w + 1)] for k in range(h))


def _label(number: int, height: int) -> list[bytearray]:
    if not 0 <= number < 100000:
        raise ValueError(f"cue number {number} does not fit the label (0 to 99999)")
    rows = [bytearray(b"\xff" * LABEL_WIDTH) for _ in range(height)]
    top = max(0, (height - 7 * SCALE) // 2)
    for d, ch in enumerate(str(number)):
        for gy, pattern in enumerate(DIGITS[ch]):
            for gx, bit in enumerate(pattern):
                if bit == "1":
                    for y in range(top + gy * SCALE, min(height, top + (gy + 1) * SCALE)):
                        x0 = 8 + d * 6 * SCALE + gx * SCALE
                        rows[y][x0:x0 + SCALE] = b"\x00" * SCALE
    return rows


def write_sheet(path: pathlib.Path, rows: list[tuple[int, pathlib.Path]]) -> None:
    """Stack (cue number, image) rows into one grayscale PNG."""
    images = [(n, *read_png(p)) for n, p in rows]
    width = LABEL_WIDTH + max(w for _, w, _, _ in images)
    lines: list[bytes] = []
    for k, (n, w, h, gray) in enumerate(images):
        if k:
            lines += [b"\x80" * width] * BAR
        label = _label(n, h)
        for y in range(h):
            lines.append(bytes(label[y]) + gray[y * w:(y + 1) * w] + b"\xff" * (width - LABEL_WIDTH - w))
    spu.write_png(path, width, len(lines), b"".join(lines))
