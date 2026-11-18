"""Put accepted text subtitles into their MKV as text tracks, with the original kept.

For each chosen file in WORK/out, its SRTs in WORK/subs (<file>.s<N>.<lang>.srt, as assemble writes
them) are added as SubRip tracks tagged with their language, after every stream the file already
has, each copied as it is. The new file is written to WORK/embed/<file>/ and checked before it
takes the original's place:

- every original stream has the same packets (time base, time, size, SHA-256) and the same
  properties as ffprobe reports them: codec data, language, title, flags; the chapters and the
  file's own tags are the same;
- each new track, read back as SRT without re-encoding, has its SRT's cues: the same timings to the
  millisecond and the same text.

The original then moves to WORK/retired-YYYY-MM-DD/, never deleted, and the new file takes its
place. WORK/embed/<file>/report.json records the new size and SHA-256 and the original's, and name
and verify use it in place of the remux report.
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re

from dvdtoplex import copier, lang, media, namer

TIMING = re.compile(r"^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$")
COPY = ("-c", "copy", "-map_metadata", "0", "-map_chapters", "0")


class EmbedError(RuntimeError):
    """Nothing to embed, an SRT or a report is wrong, or an earlier embed stopped part way."""


def cues(text: str, where: str) -> list[tuple[str, str]]:
    """(timing line, text) of each cue of an SRT, none for empty text; anything that is not a cue
    is refused."""
    out: list[tuple[str, str]] = []
    text = text.replace("\r\n", "\n").strip()
    for block in re.split(r"\n[ \t]*\n", text) if text else []:
        lines = block.split("\n")
        if len(lines) < 3 or not lines[0].strip().isdigit() or not TIMING.match(lines[1].strip()):
            raise EmbedError(f"{where}: block {len(out) + 1} is not an SRT cue (number, timing, text)")
        out.append((lines[1].strip(), "\n".join(lines[2:])))
    return out


def subtitles(work: pathlib.Path, stem: str) -> list[dict]:
    found = []
    for p, _, two in namer.srts(work, stem):
        if (code := lang.matroska(two)) == "und":
            raise EmbedError(f"{p.name}: {two!r} is not a language code this tool knows")
        text = p.read_text(encoding="utf-8")
        if not text.strip():
            raise EmbedError(f"{p.name}: no cues")
        found.append({"srt": p, "language": two, "matroska": code, "cues": cues(text, p.name)})
    return found


def _probe(path: pathlib.Path) -> dict:
    return json.loads(media.run(["ffprobe", "-v", "error", "-show_streams", "-show_chapters", "-show_format",
                                 "-show_data_hash", "sha256", "-of", "json", str(path)]))


def _packets(path: pathlib.Path) -> dict[int, tuple[str, list[tuple[str, str, str]]]]:
    """Per stream index: its time base and the (pts, size, SHA-256) of each packet, read with -c copy."""
    bases, rows = {}, {}
    for line in media.run([*media.FFMPEG, "-i", str(path), "-map", "0", "-c", "copy", "-f", "framehash",
                           "-hash", "sha256", "-"]).splitlines():
        if m := re.match(r"^#tb (\d+): (\S+)$", line):
            bases[int(m[1])] = m[2]
        elif line and not line.startswith("#"):
            f = [x.strip() for x in line.split(",")]
            rows.setdefault(int(f[0]), []).append((f[2], f[4], f[5]))
    return {k: (bases.get(k, ""), rows.get(k, [])) for k in set(bases) | set(rows)}


def dispositions(probe: dict, added: int) -> dict[int, str]:
    """Every output stream's flags as ffmpeg's -disposition takes them, by index: each original
    stream's own, none for the ADDED tracks. Given explicitly, because when no stream's flags are
    given ffmpeg marks the first of two subtitle tracks default if neither was."""
    flags = {k: "+".join(f for f, on in s.get("disposition", {}).items() if on) or "0"
             for k, s in enumerate(probe["streams"])}
    return flags | {len(flags) + j: "0" for j in range(added)}


def _mux(src: pathlib.Path, subs: list[dict], n: int, flags: dict[int, str], dst: pathlib.Path) -> None:
    """SRC's N streams copied, then each SRT as a SubRip track; FLAGS by output stream index."""
    args = [*media.FFMPEG, "-i", str(src)]
    for s in subs:
        args += ["-i", str(s["srt"])]
    args += ["-map", "0"] + [x for k in range(len(subs)) for x in ("-map", str(k + 1))] + list(COPY)
    for k, f in flags.items():
        args += [f"-disposition:{k}", f]
    for j, s in enumerate(subs, n):
        args += [f"-metadata:s:{j}", f"language={s['matroska']}"]
    media.run([*args, str(dst)])


def check(src: pathlib.Path, new: pathlib.Path, subs: list[dict]) -> list[str]:
    a, b = _probe(src), _probe(new)
    n = len(a["streams"])
    if len(b["streams"]) != n + len(subs):
        return [f"the new file has {len(b['streams'])} streams, not {n + len(subs)}"]
    problems = []
    for k, (sa, sb) in enumerate(zip(a["streams"], b["streams"])):
        if changed := sorted(key for key in set(sa) | set(sb) if sa.get(key) != sb.get(key)):
            problems.append(f"stream {k} ({sa.get('codec_name')}): {', '.join(changed)} changed")
    if a.get("chapters") != b.get("chapters"):
        problems.append("the chapters changed")
    tags = lambda p: {k: v for k, v in p["format"].get("tags", {}).items() if k != "ENCODER"}
    if tags(a) != tags(b):
        problems.append("the file's tags changed")
    pa, pb = _packets(src), _packets(new)
    for k in range(n):
        if pa.get(k) != pb.get(k):
            problems.append(f"stream {k} ({a['streams'][k].get('codec_name')}): its packets changed")
    for j, s in enumerate(subs, n):
        st = b["streams"][j]
        if st.get("codec_name") != "subrip" or st.get("tags", {}).get("language") != s["matroska"]:
            problems.append(f"track {j} ({s['srt'].name}) is not a {s['matroska']} SubRip track")
            continue
        back = media.run([*media.FFMPEG, "-i", str(new), "-map", f"0:{j}", "-c", "copy", "-f", "srt", "-"])
        try:
            got = cues(back, f"track {j} read back")
        except EmbedError:
            got = None
        if got != s["cues"]:
            problems.append(f"track {j} ({s['srt'].name}): its text read back differs from the SRT")
    return problems


def _save(path: pathlib.Path, report: dict) -> None:
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _ready(work: pathlib.Path, stem: str, retired: pathlib.Path) -> tuple[dict, list[dict]]:
    """The report the file must still match, and its SRTs. Every chosen file passes this before
    anything is written."""
    earlier = work / "embed" / stem / "report.json"
    if earlier.is_file():
        old = json.loads(earlier.read_text(encoding="utf-8"))
        if old.get("replaced"):
            raise EmbedError(f"{stem}.mkv has its text tracks already ({earlier})")
        if old.get("ok"):
            raise EmbedError(f"{stem}: an earlier embed stopped part way; the original is at "
                             f"{work / old['original']['retired']}, the checked new file at {earlier.parent}")
    report = namer.checked(work, stem)
    if report is None:
        raise EmbedError(f"{stem}: no passing remux report with a SHA-256 in {work / 'remux' / stem}")
    if copier.sha256_file(work / "out" / f"{stem}.mkv") != report["sha256"]:
        raise EmbedError(f"{stem}.mkv changed since it was checked; nothing was changed")
    if (retired / f"{stem}.mkv").exists():
        raise EmbedError(f"{retired / f'{stem}.mkv'} exists; nothing was changed")
    subs = subtitles(work, stem)
    if not subs:
        raise EmbedError(f"{stem}: no SRT in {work / 'subs'} (<file>.s<N>.<lang>.srt); run assemble first")
    return report, subs


def embed(work: pathlib.Path, files: list[str] | None = None, *,
          today: datetime.date | None = None) -> list[dict]:
    stems = sorted(p.stem for p in (work / "out").glob("*.mkv"))
    if files:
        if unknown := [f for f in files if f not in stems]:
            raise EmbedError(f"{', '.join(unknown)} not in {work / 'out'}")
        chosen = files
    else:
        chosen = [s for s in stems if namer.srts(work, s)]
        if not chosen:
            raise EmbedError(f"nothing to embed: no SRT in {work / 'subs'} for a file in {work / 'out'}")
    retired = work / f"retired-{(today or datetime.date.today()).isoformat()}"
    ready = {stem: _ready(work, stem, retired) for stem in chosen}
    reports = []
    for stem, (checked, subs) in ready.items():
        src, folder = work / "out" / f"{stem}.mkv", work / "embed" / stem
        folder.mkdir(parents=True, exist_ok=True)
        new = folder / f"{stem}.mkv"
        new.unlink(missing_ok=True)                          # an unchecked file from a run that failed
        try:
            probe = _probe(src)
            _mux(src, subs, len(probe["streams"]), dispositions(probe, len(subs)), new)
            problems = check(src, new, subs)
        except BaseException:
            new.unlink(missing_ok=True)                      # never leave an unchecked file behind
            raise
        report = {"file": f"{stem}.mkv",
                  "tracks": [{"srt": s["srt"].name, "language": s["matroska"], "cues": len(s["cues"])} for s in subs],
                  "problems": problems}
        report["ok"] = not report["problems"]
        if not report["ok"]:
            new.unlink()
            _save(folder / "report.json", report)
            reports.append(report)
            continue
        report.update(bytes=new.stat().st_size, sha256=copier.sha256_file(new), replaced=False,
                      original={"bytes": checked["bytes"], "sha256": checked["sha256"],
                                "retired": f"{retired.name}/{stem}.mkv"})
        _save(folder / "report.json", report)                # where everything is, before anything moves
        retired.mkdir(exist_ok=True)
        os.rename(src, retired / f"{stem}.mkv")
        os.rename(new, src)
        report["replaced"] = True
        _save(folder / "report.json", report)
        reports.append(report)
    return reports
