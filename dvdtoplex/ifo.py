"""Read the DVD-Video information files (IFO): titles, title sets, PGCs, cells and stream attributes.

Plain file reads of VIDEO_TS.IFO and VTS_nn_0.IFO; nothing here touches the VOBs. Offsets follow
the DVD-Video layout as documented by the dvdread project's ifo_types.h and the community
references derived from it.
"""
from __future__ import annotations

import pathlib
import struct
from dataclasses import dataclass, field

SECTOR = 2048

AUDIO_CODING = {0: "ac3", 2: "mpeg1", 3: "mpeg2", 4: "lpcm", 6: "dts"}
# Audio code extension: what the track is for.
AUDIO_CONTENT = {0: "unspecified", 1: "normal", 2: "visually impaired", 3: "commentary",
                 4: "commentary 2"}
# Subpicture code extension.
SUB_CONTENT = {0: "unspecified", 1: "normal", 2: "large", 3: "children", 5: "captions",
               6: "large captions", 7: "children's captions", 9: "forced", 13: "commentary",
               14: "large commentary", 15: "children's commentary"}


class IfoError(ValueError):
    """The file is not a DVD-Video IFO, or holds something this tool does not handle."""


def _bcd(b: int) -> int:
    return (b >> 4) * 10 + (b & 0x0F)


def playback_time(b: bytes) -> tuple[float, float]:
    """(seconds, frames per second) of a 4-byte BCD playback time; frame rate in the top 2 bits.
    An all-zero time (an empty PGC or cell) carries no rate and reads as (0.0, 0.0)."""
    if not any(b[:4]):
        return 0.0, 0.0
    rate = {1: 25.0, 3: 30000 / 1001}.get(b[3] >> 6)
    if rate is None:
        raise IfoError(f"playback time with an unknown frame rate code {b[3] >> 6}")
    frames = _bcd(b[3] & 0x3F)
    return _bcd(b[0]) * 3600 + _bcd(b[1]) * 60 + _bcd(b[2]) + frames / rate, rate


def _lang(b: bytes, code_type: int) -> str:
    return b.decode("latin-1") if code_type == 1 and b.isalpha() else ""


@dataclass
class Audio:
    index: int
    coding: str
    channels: int
    lang: str
    content: str


@dataclass
class Subpicture:
    index: int
    lang: str
    content: str


@dataclass
class Cell:
    seconds: float
    first_sector: int
    last_sector: int
    angle_block: bool
    interleaved: bool
    stc_discontinuity: bool


@dataclass
class Pgc:
    number: int
    seconds: float
    fps: float
    # Disc stream number of each audio attribute index (AC-3 0x80+n, LPCM 0xA0+n, ...), or None.
    audio_streams: list[int | None]
    # Per subpicture attribute index: disc substream numbers for 4:3, wide, letterbox, pan-scan.
    sub_streams: list[tuple[int, int, int, int] | None]
    palette_ycrcb: list[tuple[int, int, int]]
    program_cells: list[int]
    cells: list[Cell]

    def chapter_starts(self) -> list[float]:
        """Start of each program (chapter) in seconds from the PGC's start."""
        return [round(sum(c.seconds for c in self.cells[:first - 1]), 3) for first in self.program_cells]

    def palette_rgb(self) -> list[tuple[int, int, int]]:
        """The palette as RGB. DVD palettes are BT.601 studio range (Y 16-235, Cr and Cb 16-240),
        the convention of ffmpeg's DVD subtitle decoder, which maps Y 235 to white and Y 16 to black."""
        out = []
        for y, cr, cb in self.palette_ycrcb:
            luma = 1.164383 * (y - 16)
            r = luma + 1.596027 * (cr - 128)
            g = luma - 0.391762 * (cb - 128) - 0.812968 * (cr - 128)
            b = luma + 2.017232 * (cb - 128)
            out.append(tuple(max(0, min(255, round(v))) for v in (r, g, b)))
        return out


@dataclass
class TitleSet:
    number: int
    standard: str
    aspect: str
    audio: list[Audio]
    subpictures: list[Subpicture]
    pgcs: list[Pgc]
    # For each title of this set (1-based), the PGC number of its first chapter.
    title_pgcs: list[int] = field(default_factory=list)


@dataclass
class Title:
    number: int
    title_set: int
    title_in_set: int
    angles: int
    chapters: int


def _u16(d: bytes, o: int) -> int:
    return struct.unpack_from(">H", d, o)[0]


def _u32(d: bytes, o: int) -> int:
    return struct.unpack_from(">I", d, o)[0]


def _malformed(name: str):
    """Turn a read past the end of a truncated or malformed IFO into an IfoError."""
    def wrap(fn):
        def inner(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (struct.error, IndexError) as e:
                raise IfoError(f"{name} is truncated or malformed ({e})") from e
        inner.__doc__, inner.__name__ = fn.__doc__, fn.__name__
        return inner
    return wrap


@_malformed("VIDEO_TS.IFO")
def read_vmg(video_ts: pathlib.Path) -> list[Title]:
    """The disc's title table (TT_SRPT) from VIDEO_TS.IFO."""
    d = (video_ts / "VIDEO_TS.IFO").read_bytes()
    if d[:12] != b"DVDVIDEO-VMG":
        raise IfoError("VIDEO_TS.IFO does not start with DVDVIDEO-VMG")
    base = _u32(d, 0xC4) * SECTOR
    count = _u16(d, base)
    titles = []
    for k in range(count):
        e = base + 8 + 12 * k
        titles.append(Title(number=k + 1, angles=d[e + 1], chapters=_u16(d, e + 2),
                            title_set=d[e + 6], title_in_set=d[e + 7]))
    return titles


def _pgc(d: bytes, base: int, number: int) -> Pgc:
    programs, cells_n = d[base + 2], d[base + 3]
    seconds, fps = playback_time(d[base + 4:base + 8])
    audio = []
    for k in range(8):
        v = _u16(d, base + 0x0C + 2 * k)
        audio.append((v >> 8) & 7 if v & 0x8000 else None)
    subs = []
    for k in range(32):
        v = _u32(d, base + 0x1C + 4 * k)
        subs.append(((v >> 24) & 0x1F, (v >> 16) & 0x1F, (v >> 8) & 0x1F, v & 0x1F)
                    if v & 0x80000000 else None)
    palette = [tuple(d[base + 0xA4 + 4 * k + 1:base + 0xA4 + 4 * k + 4]) for k in range(16)]
    pmap, cpb = _u16(d, base + 0xE6), _u16(d, base + 0xE8)
    program_cells = list(d[base + pmap:base + pmap + programs]) if pmap else []
    cells = []
    for c in range(cells_n if cpb else 0):
        cb = base + cpb + 24 * c
        flags = d[cb]
        cells.append(Cell(seconds=playback_time(d[cb + 4:cb + 8])[0],
                          first_sector=_u32(d, cb + 8), last_sector=_u32(d, cb + 20),
                          angle_block=(flags >> 4) & 3 == 1, interleaved=bool(flags & 0x04),
                          stc_discontinuity=bool(flags & 0x02)))
    return Pgc(number=number, seconds=seconds, fps=fps, audio_streams=audio, sub_streams=subs,
               palette_ycrcb=palette, program_cells=program_cells, cells=cells)


@_malformed("a VTS IFO")
def read_vts(video_ts: pathlib.Path, number: int) -> TitleSet:
    d = (video_ts / f"VTS_{number:02d}_0.IFO").read_bytes()
    if d[:12] != b"DVDVIDEO-VTS":
        raise IfoError(f"VTS_{number:02d}_0.IFO does not start with DVDVIDEO-VTS")
    va = _u16(d, 0x200)
    standard = ("NTSC", "PAL")[(va >> 12) & 1]
    aspect = {0: "4:3", 3: "16:9"}.get((va >> 10) & 3, "unknown")
    audio = []
    for i in range(_u16(d, 0x202)):
        a = d[0x204 + 8 * i:0x20C + 8 * i]
        audio.append(Audio(index=i, coding=AUDIO_CODING.get(a[0] >> 5, f"code {a[0] >> 5}"),
                           channels=(a[1] & 7) + 1, lang=_lang(a[2:4], (a[0] >> 2) & 3),
                           content=AUDIO_CONTENT.get(a[5], f"code {a[5]}")))
    subs = []
    for i in range(_u16(d, 0x254)):
        s = d[0x256 + 6 * i:0x25C + 6 * i]
        subs.append(Subpicture(index=i, lang=_lang(s[2:4], s[0] & 3),
                               content=SUB_CONTENT.get(s[5], f"code {s[5]}")))
    pgcit = _u32(d, 0xCC) * SECTOR
    pgcs = []
    for p in range(_u16(d, pgcit)):
        start = _u32(d, pgcit + 8 + 8 * p + 4)
        pgcs.append(_pgc(d, pgcit + start, p + 1))
    ptt = _u32(d, 0xC8) * SECTOR
    title_pgcs = []
    for t in range(_u16(d, ptt)):
        first = ptt + _u32(d, ptt + 8 + 4 * t)
        title_pgcs.append(_u16(d, first))
    return TitleSet(number=number, standard=standard, aspect=aspect, audio=audio,
                    subpictures=subs, pgcs=pgcs, title_pgcs=title_pgcs)
