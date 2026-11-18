"""Find scrambled packs. dvd-to-plex reads unscrambled DVD-Video only, and stops on anything else.

Each 2048-byte pack of a VOB starts with an MPEG-2 pack header; the first PES packet follows at
14 + the pack's stuffing length. For video, audio and private stream 1, bits 5-4 of that PES
header's sixth byte are its scrambling control; any nonzero value means the payload is scrambled.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass

SECTOR = 2048
REFUSAL = ("This disc's video is scrambled. dvd-to-plex reads only unscrambled DVD-Video, "
           "so it stops here and changes nothing.")
_PACK = b"\x00\x00\x01\xba"


def _flagged(stream_id: int) -> bool:
    return stream_id == 0xBD or 0xC0 <= stream_id <= 0xEF


def pack_scrambled(pack: bytes) -> bool | None:
    """True or False for a pack whose first PES carries scrambling bits; None for other packs."""
    if len(pack) < SECTOR or pack[:4] != _PACK:
        return None
    pes = 14 + (pack[13] & 7)
    if pack[pes:pes + 3] != b"\x00\x00\x01" or not _flagged(pack[pes + 3]):
        return None
    return bool((pack[pes + 6] >> 4) & 3)


@dataclass
class Count:
    checked: int = 0
    scrambled: int = 0

    def add(self, other: "Count") -> None:
        self.checked += other.checked
        self.scrambled += other.scrambled


def count_file(path: pathlib.Path, *, most: int | None = None) -> Count:
    """Check every pack of PATH, or at most MOST packs spread evenly across it."""
    packs = path.stat().st_size // SECTOR
    step = 1 if most is None or packs <= most else packs // most
    out = Count()
    with open(path, "rb") as f:
        for k in range(0, packs, step):
            f.seek(k * SECTOR)
            flag = pack_scrambled(f.read(SECTOR))
            if flag is not None:
                out.checked += 1
                out.scrambled += flag
    return out


def count_disc(video_ts: pathlib.Path, *, most_per_file: int | None = 4096) -> Count:
    """Sample every VOB of the disc (or read all of them with most_per_file=None)."""
    total = Count()
    for vob in sorted(p for p in video_ts.iterdir() if p.suffix.upper() == ".VOB"):
        total.add(count_file(vob, most=most_per_file))
    return total
