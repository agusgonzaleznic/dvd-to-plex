"""DVD subtitle units (SPUs): parse one, decode its bitmap, and write it as a PNG for OCR.

A unit holds two run-length coded fields (even and odd lines) of 2-bit pixels and a control
sequence: the display area, which 4 of the 16 palette colours and which transparency each pixel
value takes, and the delays at which the unit starts and stops showing.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

TICK = 1024 / 90000          # control-sequence delays count in units of 1024 90 kHz ticks


class SpuError(ValueError):
    """The unit is truncated or malformed."""


@dataclass
class Spu:
    x: int
    y: int
    width: int
    height: int
    colours: tuple[int, int, int, int]      # palette index per pixel value 0-3
    alphas: tuple[int, int, int, int]       # 0 transparent to 15 opaque, per pixel value
    start: float                            # seconds after the unit's PTS
    stop: float | None
    forced: bool
    pixels: bytes                           # one pixel value (0-3) per byte, row by row


def _nibbles(data: bytes, pos: int):
    while True:
        if pos // 2 >= len(data):
            raise SpuError("run-length data runs past the end of the unit")
        b = data[pos // 2]
        yield (b >> 4) if pos % 2 == 0 else (b & 0x0F)
        pos += 1


def _field(data: bytes, offset: int, width: int, lines: int) -> list[bytes]:
    """Decode LINES lines of run-length data starting at byte OFFSET."""
    rows, pos = [], offset * 2
    for _ in range(lines):
        row, x = bytearray(), 0
        nib = _nibbles(data, pos)
        used = 0
        while x < width:
            v = next(nib)
            used += 1
            for limit in (0x4, 0x10, 0x40):             # codes are 4, 8, 12 or 16 bits long
                if v >= limit:
                    break
                v = (v << 4) | next(nib)
                used += 1
            run = v >> 2 or width - x                    # a zero run fills to the end of the line
            run = min(run, width - x)
            row += bytes([v & 3]) * run
            x += run
        pos += used
        pos += pos % 2                                  # each line starts on a whole byte
        rows.append(bytes(row))
    return rows


def parse(data: bytes) -> Spu:
    """One unit, as stored in a Matroska VobSub packet or reassembled from the PES packets."""
    try:
        size, ctrl = struct.unpack_from(">HH", data, 0)
        if size > len(data) or not 4 <= ctrl < size:
            raise SpuError(f"unit size {size} or control offset {ctrl} does not fit {len(data)} bytes")
        x = y = x2 = y2 = None
        colours, alphas, fields = (0, 1, 2, 3), (0, 15, 15, 15), None
        start, stop, forced = 0.0, None, False
        seq, seen = ctrl, set()             # a sequence ends the chain by pointing to itself or an earlier one
        while seq not in seen:
            seen.add(seq)
            delay, following = struct.unpack_from(">HH", data, seq)
            pos = seq + 4
            while (cmd := data[pos]) != 0xFF:
                pos += 1
                if cmd in (0x00, 0x01):
                    start, forced = delay * TICK, forced or cmd == 0x00
                elif cmd == 0x02:
                    stop = delay * TICK
                elif cmd in (0x03, 0x04):
                    a, b = data[pos], data[pos + 1]
                    values = (b & 0x0F, b >> 4, a & 0x0F, a >> 4)
                    colours, alphas = (values, alphas) if cmd == 0x03 else (colours, values)
                    pos += 2
                elif cmd == 0x05:
                    d = data[pos:pos + 6]
                    x, x2 = (d[0] << 4) | (d[1] >> 4), ((d[1] & 0x0F) << 8) | d[2]
                    y, y2 = (d[3] << 4) | (d[4] >> 4), ((d[4] & 0x0F) << 8) | d[5]
                    pos += 6
                elif cmd == 0x06:
                    fields = struct.unpack_from(">HH", data, pos)
                    pos += 4
                elif cmd == 0x07:
                    pos += struct.unpack_from(">H", data, pos)[0]
                else:
                    raise SpuError(f"unknown control command {cmd:#x}")
            if following not in seen and following <= pos:     # each sequence after the last: linear
                raise SpuError(f"control sequence at {seq} points to {following}, which overlaps or goes back")
            seq = following
        if None in (x, y, x2, y2) or fields is None:
            raise SpuError("the control sequence sets no display area or no bitmap")
        width, height = x2 - x + 1, y2 - y + 1
        if width <= 0 or height <= 0:
            raise SpuError(f"empty display area {width}x{height}")
        top = _field(data[:ctrl], fields[0], width, (height + 1) // 2)
        bottom = _field(data[:ctrl], fields[1], width, height // 2)
    except (struct.error, IndexError) as e:
        raise SpuError(f"the unit is truncated ({e})") from e
    rows = [top[k // 2] if k % 2 == 0 else bottom[k // 2] for k in range(height)]
    return Spu(x=x, y=y, width=width, height=height, colours=colours, alphas=alphas, start=start,
               stop=stop, forced=forced, pixels=b"".join(rows))


def background(spu: Spu) -> int:
    """The pixel value along the bitmap's border: what surrounds the text, box or no box."""
    w, h, px = spu.width, spu.height, spu.pixels
    edge = px[:w] + px[(h - 1) * w:] + bytes(px[k * w] for k in range(h)) + bytes(px[k * w + w - 1] for k in range(h))
    return max(range(4), key=edge.count)


def gray_for_ocr(spu: Spu, margin: int = 16) -> tuple[int, int, bytes]:
    """The unit as dark text on white, with a white margin: the border's pixel value and any
    transparent value white, every other value dark by its opacity. A light fill and its dark
    outline both come out dark, so the letters read as bold; a boxed subtitle's box comes out
    white."""
    bg = background(spu)
    level = bytes(255 if v == bg else 255 - round(255 * spu.alphas[v] / 15) for v in range(4))
    lut = level + bytes(252)
    w, h = spu.width + 2 * margin, spu.height + 2 * margin
    blank = b"\xff" * w
    rows = [blank] * margin
    for k in range(spu.height):
        line = spu.pixels[k * spu.width:(k + 1) * spu.width].translate(lut)
        rows.append(b"\xff" * margin + line + b"\xff" * margin)
    rows += [blank] * margin
    return w, h, b"".join(rows)


def write_png(path, width: int, height: int, gray: bytes) -> None:
    """An 8-bit grayscale PNG."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    raw = b"".join(b"\x00" + gray[k * width:(k + 1) * width] for k in range(height))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
