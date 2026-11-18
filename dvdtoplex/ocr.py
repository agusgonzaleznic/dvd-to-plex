"""Read a remuxed file's disc subtitles as text.

Each subtitle unit of each VobSub track is taken from the MKV as stored, decoded to a PNG (dark
text on white) and read twice by macOS Vision: as it is with language correction, and at twice
the size without it. Two independent readings are what the proofreading stage plants its checks
on. Times are seconds from the video's first frame, as the MKV plays them.

Output per file and track: WORK/ocr/<stem>/s<N>/NNNN.png and WORK/ocr/<stem>/s<N>.json.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

from dvdtoplex import lang, media, spu, swiftc

READINGS = (("first", "1", "on"), ("second", "2", "off"))
LAST_CUE = 5.0          # seconds shown for a final unit that sets no stop time


class OcrError(RuntimeError):
    """Vision or a tool failed, or a track's language is unknown."""


def subtitle_tracks(mkv: pathlib.Path) -> list[dict]:
    return [s for s in media.streams(mkv) if s["codec_type"] == "subtitle"
            and s.get("codec_name") == "dvd_subtitle"]


def units(mkv: pathlib.Path, track: int) -> list[tuple[float, bytes]]:
    """(time from the video's first frame, unit bytes) for each packet of subtitle track TRACK."""
    rows = json.loads(media.run(["ffprobe", "-v", "error", "-select_streams", f"s:{track}",
                                 "-show_entries", "packet=pts_time,size", "-of", "json", str(mkv)]))["packets"]
    data = subprocess.run([*media.FFMPEG, "-i", str(mkv), "-map", f"0:s:{track}", "-c", "copy",
                           "-f", "data", "-"], capture_output=True, check=True, timeout=3600).stdout
    sizes = [int(r["size"]) for r in rows]
    if sum(sizes) != len(data):
        raise OcrError(f"{mkv.name} track s:{track}: packets add up to {sum(sizes)} bytes, data is {len(data)}")
    t0 = media.first_time(mkv, "v:0")
    out, pos = [], 0
    for r, n in zip(rows, sizes):
        out.append((float(r["pts_time"]) - t0, data[pos:pos + n]))
        pos += n
    return out


def read(binary: pathlib.Path, language: str, scale: str, correction: str,
         images: list[pathlib.Path]) -> list[str]:
    """Vision's text for each image, its lines joined with newlines."""
    if not images:
        return []
    p = subprocess.run([str(binary), language, scale, correction, *map(str, images)],
                       capture_output=True, text=True, timeout=3600)
    if p.returncode != 0:
        raise OcrError(p.stderr.strip() or f"read_text exited {p.returncode}")
    rows = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
    if [r["image"] for r in rows] != list(map(str, images)):
        raise OcrError("Vision's output does not line up with the images it was given")
    return ["\n".join(r["lines"]) for r in rows]


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def ocr_track(mkv: pathlib.Path, track: int, language: str, out: pathlib.Path, binary: pathlib.Path) -> dict:
    folder = out / f"s{track}"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    raw = units(mkv, track)
    cues, images = [], []
    for n, (start, data) in enumerate(raw, 1):
        cue: dict = {"n": n, "start": round(start, 3)}
        try:
            unit = spu.parse(data)
        except spu.SpuError as e:
            cue["error"] = str(e)
            cues.append(cue)
            continue
        following = raw[n][0] if n < len(raw) else None
        end = start + unit.stop if unit.stop is not None else following or start + LAST_CUE
        image = folder / f"{n:04d}.png"
        spu.write_png(image, *spu.gray_for_ocr(unit))
        cue.update(start=round(start + unit.start, 3), end=round(end, 3), forced=unit.forced,
                   image=image.relative_to(out).as_posix())
        cues.append(cue)
        images.append((cue, image))
    for key, scale, correction in READINGS:
        for (cue, _), text in zip(images, read(binary, language, scale, correction, [i for _, i in images])):
            cue[key] = text
    for cue, _ in images:
        cue["agree"] = _same(cue["first"], cue["second"])
    result = {"file": mkv.name, "track": track, "language": language, "cues": cues}
    (out / f"s{track}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    return result


def ocr_file(mkv: pathlib.Path, work: pathlib.Path, *, language: str | None = None) -> list[dict]:
    """Read every VobSub track of MKV; LANGUAGE (two letters) overrides the tracks' own tags."""
    tracks = subtitle_tracks(mkv)
    out = work / "ocr" / mkv.stem
    binary = swiftc.build("read_text", work / "ocr" / "bin")
    results = []
    for k, s in enumerate(tracks):
        code = language or lang.two_letter((s.get("tags") or {}).get("language", ""))
        if not code:
            raise OcrError(f"{mkv.name} track s:{k} has no known language; give one with --lang")
        results.append(ocr_track(mkv, k, code, out, binary))
    return results
