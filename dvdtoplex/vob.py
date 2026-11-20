"""Sector-level work on a title set's VOBs: cut cells out, walk PES packets, extract a VobSub.

Cell addresses in the IFO count 2048-byte sectors from the start of VTS_nn_1.VOB across the whole
set (VTS_nn_1.VOB to VTS_nn_9.VOB, each at most 1 GiB). Nothing here changes a byte of a pack.
"""
from __future__ import annotations

import os
import pathlib
import stat
import struct
from collections import Counter
from collections.abc import Iterator

from dvdtoplex import source

SECTOR = 2048
PACK = b"\x00\x00\x01\xba"


class VobError(ValueError):
    """The VOB set is not what the IFO describes."""


def title_vobs(video_ts: pathlib.Path, vts: int) -> list[pathlib.Path]:
    parts = [video_ts / f"VTS_{vts:02d}_{k}.VOB" for k in range(1, 10)]
    found = []
    for p in parts:
        try:
            st = os.lstat(p)          # lstat: a link named like a VOB is disc content, not followed
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(st.st_mode):
            raise VobError(f"{p.name} is not a regular file; links and special files in a disc folder "
                           "are refused")
        if st.st_size % SECTOR:
            raise VobError(f"{p.name} is not a whole number of 2048-byte sectors")
        found.append(p)
    if not found:
        raise VobError(f"no title VOBs for title set {vts}")
    return found


def read_sectors(parts: list[pathlib.Path], first: int, last: int) -> Iterator[bytes]:
    """The bytes of sectors FIRST..LAST (inclusive) across the VOB set, in chunks."""
    start, end, base = first * SECTOR, (last + 1) * SECTOR, 0
    got = 0
    for p in parts:
        size = os.lstat(p).st_size
        lo, hi = max(start, base), min(end, base + size)
        if lo < hi:
            with source.open_regular(p, VobError) as f:
                f.seek(lo - base)
                left = hi - lo
                while left:
                    block = f.read(min(left, 1 << 20))
                    if not block:
                        raise VobError(f"{p.name} ended early")
                    got += len(block)
                    left -= len(block)
                    yield block
        base += size
    if got != end - start:
        raise VobError(f"sectors {first}-{last} run past the end of the VOB set")


def cut(parts: list[pathlib.Path], ranges: list[tuple[int, int]], out: pathlib.Path) -> int:
    """Write the sector ranges, in order, to OUT; returns the number of sectors written."""
    n = 0
    with open(out, "wb") as f:
        for first, last in ranges:
            for block in read_sectors(parts, first, last):
                f.write(block)
            n += last - first + 1
    return n


def packs(path: pathlib.Path) -> Iterator[bytes]:
    with open(path, "rb") as f:
        while len(pack := f.read(SECTOR)) == SECTOR:
            yield pack


def pes(pack: bytes) -> Iterator[tuple[int, bytes]]:
    """(stream id, body after the 6-byte PES start) for each PES packet in an MPEG-2 pack."""
    if pack[:4] != PACK or pack[4] >> 6 != 1:
        return
    pos = 14 + (pack[13] & 7)
    while pos + 6 <= len(pack) and pack[pos:pos + 3] == b"\x00\x00\x01":
        sid = pack[pos + 3]
        if sid == 0xBB or sid >= 0xBC:
            length = struct.unpack_from(">H", pack, pos + 4)[0]
            yield sid, pack[pos + 6:pos + 6 + length]
            pos += 6 + length
        else:
            break


def pts(body: bytes) -> int | None:
    """PTS (90 kHz ticks) of an MPEG-2 PES body, or None when it carries none."""
    if len(body) < 8 or not body[1] & 0x80:
        return None
    b = body[3:8]
    return ((b[0] >> 1) & 7) << 30 | b[1] << 22 | (b[2] >> 1) << 15 | b[3] << 7 | b[4] >> 1


def substream(body: bytes) -> int | None:
    """Substream id of a private stream 1 body (0x20-0x3F subpicture, 0x80+ audio), or None."""
    if len(body) < 3:
        return None
    payload = body[3 + body[2]:]
    return payload[0] if payload else None


def subpicture_counts(path: pathlib.Path) -> Counter:
    """Subtitle units per subpicture substream: PES packets with a PTS start a unit."""
    counts: Counter = Counter()
    for pack in packs(path):
        for sid, body in pes(pack):
            if sid == 0xBD:
                sub = substream(body)
                if sub is not None and 0x20 <= sub <= 0x3F and pts(body) is not None:
                    counts[sub] += 1
    return counts


def extract_vobsub(cuts: list[pathlib.Path], sub_id: int, out_sub: pathlib.Path) -> list[dict]:
    """Copy every pack carrying subpicture SUB_ID, verbatim, into OUT_SUB. Returns one entry per
    subtitle unit (a pack whose PES has a PTS starts one): its byte offset in OUT_SUB, its PTS,
    and the byte offset of that pack across the cuts."""
    units, offset, read = [], 0, 0
    with open(out_sub, "wb") as f:
        for path in cuts:
            for pack in packs(path):
                keep, start = False, None
                for sid, body in pes(pack):
                    if sid == 0xBD and substream(body) == sub_id:
                        keep = True
                        start = pts(body) if start is None else start
                if keep:
                    if start is not None:
                        units.append({"filepos": offset, "pts": start, "cut_offset": read})
                    f.write(pack)
                    offset += SECTOR
                read += SECTOR
    return units


def write_idx(path: pathlib.Path, *, size: tuple[int, int], palette: list[tuple[int, int, int]],
              lang: str, times: list[float], filepos: list[int]) -> None:
    """A VobSub index: frame size, the 16-colour RGB palette, one timestamp per unit."""
    if len(times) != len(filepos):
        raise VobError(f"{len(times)} subtitle times for {len(filepos)} units")
    lines = ["# VobSub index file, v7 (do not modify this line!)", f"size: {size[0]}x{size[1]}",
             "palette: " + ", ".join("%02x%02x%02x" % c for c in palette), "",
             f"id: {lang or 'un'}, index: 0"]
    for t, pos in zip(times, filepos):
        ms = round(t * 1000)
        if ms < 0:
            raise VobError(f"negative subtitle time {t}")
        lines.append(f"timestamp: {ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}:"
                     f"{ms % 1000:03d}, filepos: {pos:09x}")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
