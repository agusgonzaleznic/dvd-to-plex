"""What is on a disc: its titles, their streams, chapters and cells, and what dvd-to-plex cannot handle."""
from __future__ import annotations

import dataclasses
import pathlib
from dataclasses import dataclass, field

from dvdtoplex import ifo, scramble


@dataclass
class TitleInfo:
    number: int
    title_set: int
    title_in_set: int
    pgc: int
    seconds: float
    fps: float
    chapters: list[float]
    audio: list[ifo.Audio]
    # Disc stream of each audio entry, parallel to audio: 0x80 + n for AC-3, 0xA0 + n for LPCM, ...
    audio_ids: list[int]
    subs: list[ifo.Subpicture]
    # Disc substream (0x20 + n) of each subtitle entry, parallel to subs; 4:3 or wide stream.
    sub_ids: list[int]
    palette: list[tuple[int, int, int]]
    cells: list[ifo.Cell]
    duplicate_of: int | None = None
    problems: list[str] = field(default_factory=list)


@dataclass
class Survey:
    video_ts: pathlib.Path
    standard: str
    scramble: scramble.Count
    titles: list[TitleInfo]

    @property
    def main(self) -> TitleInfo | None:
        usable = [t for t in self.titles if not t.problems and t.duplicate_of is None]
        return max(usable, key=lambda t: t.seconds, default=None)

    def to_json(self) -> dict:
        d = dataclasses.asdict(self)
        d["video_ts"] = str(self.video_ts)
        d["main"] = self.main.number if self.main else None
        return d


_AUDIO_BASE = {"ac3": 0x80, "dts": 0x88, "lpcm": 0xA0, "mpeg1": 0xC0, "mpeg2": 0xC0}


def _title(t: ifo.Title, ts: ifo.TitleSet) -> TitleInfo:
    where = f"title {t.number} (VTS_{t.title_set:02d})"
    if not 1 <= t.title_in_set <= len(ts.title_pgcs):
        raise ifo.IfoError(f"{where} is entry {t.title_in_set} of the title set's title table, "
                           f"which has {len(ts.title_pgcs)} entries")
    number = ts.title_pgcs[t.title_in_set - 1]
    if not 1 <= number <= len(ts.pgcs):
        raise ifo.IfoError(f"{where} points at program chain {number}, but the title set has "
                           f"{len(ts.pgcs)}")
    pgc = ts.pgcs[number - 1]
    audio, audio_ids = [], []
    for a in ts.audio:
        stream = pgc.audio_streams[a.index] if a.index < len(pgc.audio_streams) else None
        if stream is not None:
            audio.append(a)
            audio_ids.append(_AUDIO_BASE.get(a.coding, 0x80) + stream)
    subs, sub_ids = [], []
    for s in ts.subpictures:
        streams = pgc.sub_streams[s.index] if s.index < len(pgc.sub_streams) else None
        if streams is not None:
            subs.append(s)
            sub_ids.append(0x20 + (streams[1] if ts.aspect == "16:9" else streams[0]))
    info = TitleInfo(number=t.number, title_set=t.title_set, title_in_set=t.title_in_set,
                     pgc=number, seconds=pgc.seconds, fps=pgc.fps, chapters=pgc.chapter_starts(),
                     audio=audio, audio_ids=audio_ids, subs=subs, sub_ids=sub_ids,
                     palette=pgc.palette_rgb(), cells=pgc.cells)
    if t.angles > 1 or any(c.angle_block for c in pgc.cells):
        info.problems.append("multi-angle")
    if any(c.interleaved for c in pgc.cells):
        info.problems.append("interleaved cells (seamless branching)")
    if not pgc.cells:
        info.problems.append("no cells")
    return info


def survey(video_ts: pathlib.Path, *, most_per_file: int | None = 4096) -> Survey:
    titles = ifo.read_vmg(video_ts)
    sets = {n: ifo.read_vts(video_ts, n) for n in sorted({t.title_set for t in titles})}
    infos, seen = [], {}
    for t in titles:
        info = _title(t, sets[t.title_set])
        if not (video_ts / f"VTS_{t.title_set:02d}_1.VOB").is_file():
            info.problems.append("VOB files missing")
        key = (t.title_set, tuple((c.first_sector, c.last_sector) for c in info.cells))
        info.duplicate_of = seen.setdefault(key, t.number) if info.cells else None
        if info.duplicate_of == t.number:
            info.duplicate_of = None
        infos.append(info)
    standard = sets[min(sets)].standard if sets else "unknown"
    return Survey(video_ts=video_ts, standard=standard,
                  scramble=scramble.count_disc(video_ts, most_per_file=most_per_file), titles=infos)


def _clock(seconds: float) -> str:
    s = round(seconds)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def _stream(lang: str, content: str) -> str:
    label = lang or "unlabelled"
    return label if content in ("normal", "unspecified") else f"{label} ({content})"


def describe(s: Survey) -> str:
    rows = [("Title", "Set", "Length", "Ch", "Audio", "Subtitles", "Notes")]
    main = s.main
    for t in s.titles:
        notes = list(t.problems)
        if t.duplicate_of:
            notes.append(f"same cells as title {t.duplicate_of}")
        if main and t.number == main.number:
            notes.append("main (longest)")
        rows.append((str(t.number), str(t.title_set), _clock(t.seconds), str(len(t.chapters)),
                     ", ".join(_stream(a.lang, a.content) for a in t.audio) or "-",
                     ", ".join(_stream(x.lang, x.content) for x in t.subs) or "-", "; ".join(notes)))
    widths = [max(len(r[k]) for r in rows) for k in range(len(rows[0]) - 1)]
    table = ["  ".join(c.ljust(w) for c, w in zip(r, widths)) + "  " + r[-1] for r in rows]
    return "\n".join([f"VIDEO_TS: {s.video_ts}", f"Standard: {s.standard}",
                      f"Scrambled packs: {s.scramble.scrambled} of {s.scramble.checked} checked", ""]
                     + [line.rstrip() for line in table])
