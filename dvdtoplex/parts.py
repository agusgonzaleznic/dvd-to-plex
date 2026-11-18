"""Which cells of a title go into which output file, and where its chapters fall.

Two disc habits shape this. Some discs end every extra with the same short return-to-menu cell;
a cell under one second whose sectors two or more PGCs share is that filler, and is left out.
And a cell flagged as an STC discontinuity may restart the timestamps; where the video PTS really
jumps (backwards, or forwards by more than a second), the title is split there into parts, each
remuxed to its own file, because one Matroska timeline cannot hold a reset.
"""
from __future__ import annotations

import pathlib
from collections import Counter
from dataclasses import dataclass, field

from dvdtoplex import ifo, vob

FILLER_SECONDS = 1.0
JUMP_TICKS = 90000          # one second at 90 kHz
WRAP = 1 << 33


@dataclass
class Part:
    title: int
    vts: int
    number: int                 # 1-based
    cells: list[ifo.Cell]
    chapters: list[float]       # chapter starts, seconds from the part's start
    of: int = 1                 # parts in the title
    dropped: list[str] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return sum(c.seconds for c in self.cells)

    @property
    def ranges(self) -> list[tuple[int, int]]:
        return [(c.first_sector, c.last_sector) for c in self.cells]


def filler(ts: ifo.TitleSet) -> set[tuple[int, int]]:
    """Sector ranges of sub-second cells used by two or more PGCs of the title set."""
    use: Counter = Counter()
    for p in ts.pgcs:
        use.update({(c.first_sector, c.last_sector) for c in p.cells if c.seconds < FILLER_SECONDS})
    return {k for k, n in use.items() if n >= 2}


def _video_pts(parts: list[pathlib.Path], first: int, last: int) -> list[int]:
    out = []
    for block in vob.read_sectors(parts, first, last):
        for k in range(0, len(block) - vob.SECTOR + 1, vob.SECTOR):
            for sid, body in vob.pes(block[k:k + vob.SECTOR]):
                if sid == 0xE0 and (t := vob.pts(body)) is not None:
                    out.append(t)
    return out


def _jumps(parts: list[pathlib.Path], before: ifo.Cell, after: ifo.Cell) -> bool:
    """True when the video PTS restarts between two adjacent cells."""
    tail = _video_pts(parts, max(before.first_sector, before.last_sector - 255), before.last_sector)
    head = _video_pts(parts, after.first_sector, min(after.last_sector, after.first_sector + 255))
    if not tail or not head:
        return True
    gap = (min(head) - max(tail)) % WRAP
    return gap > JUMP_TICKS


def plan(video_ts: pathlib.Path, title: int, vts: int, ts: ifo.TitleSet, pgc: ifo.Pgc) -> list[Part]:
    fill = filler(ts)
    parts = vob.title_vobs(video_ts, vts)
    starts = {p - 1 for p in pgc.program_cells}
    groups: list[list[tuple[int, ifo.Cell]]] = []
    dropped = []
    for i, c in enumerate(pgc.cells):
        if (c.first_sector, c.last_sector) in fill:
            dropped.append(f"cell {i + 1}: {c.seconds:.2f} s filler shared with other PGCs")
            continue
        if not groups or (c.stc_discontinuity and _jumps(parts, groups[-1][-1][1], c)):
            groups.append([])
        groups[-1].append((i, c))
    out = []
    for n, group in enumerate(groups, 1):
        chapters, t = [], 0.0
        for i, c in group:
            if i in starts:
                chapters.append(round(t, 3))
            t += c.seconds
        if not chapters or chapters[0] != 0.0:
            chapters.insert(0, 0.0)
        out.append(Part(title=title, vts=vts, number=n, cells=[c for _, c in group],
                        chapters=chapters, of=len(groups), dropped=dropped if n == 1 else []))
    return out
