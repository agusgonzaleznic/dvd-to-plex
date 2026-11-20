"""Remux one part of a title to MKV without re-encoding a frame, then check the result.

Stage 1 copies the cut's video and audio into Matroska. Each subtitle stream becomes a VobSub:
its packs copied verbatim, the title's own palette, and each unit's time computed from its PTS
and anchored on the video's first PTS. The anchor is checked against where ffmpeg put the first
audio packet, and against ffmpeg's own subtitle timing whenever the first subtitle is near the
start. The final file adds languages, flags and chapters. The output only appears once every
check passes; otherwise the work folder keeps everything for a look.
"""
from __future__ import annotations

import json
import pathlib
import shutil

from dvdtoplex import copier, lang, media, scramble, vob
from dvdtoplex.parts import WRAP, Part
from dvdtoplex.survey import TitleInfo

TOLERANCE = 0.002           # seconds; idx times are whole milliseconds
CROSS_CHECK_BYTES = 64 << 20
VIDEO_STREAM = "0:i:0x1e0"         # MPEG-2 video, PES stream id 0xE0
AUDIO_TITLE = {"commentary": "Commentary", "commentary 2": "Commentary 2",
               "visually impaired": "Audio description"}


class RemuxError(RuntimeError):
    """The part could not be remuxed (scrambled, no video, an output already there)."""


def _signed(ticks: int) -> int:
    d = ticks % WRAP
    return d - WRAP if d >= WRAP // 2 else d


def _first_pts(cut: pathlib.Path, wanted: set[int], n: int = 64) -> dict[int, int]:
    """Smallest PTS among the first N PES packets of each stream: 0xE0 video, else substream ids."""
    seen: dict[int, list[int]] = {k: [] for k in wanted}
    for pack in vob.packs(cut):
        for sid, body in vob.pes(pack):
            key = vob.substream(body) if sid == 0xBD else sid
            if key in seen and len(seen[key]) < n and (t := vob.pts(body)) is not None:
                seen[key].append(t)
        if all(len(v) >= n for v in seen.values()):
            break
    return {k: min(v) for k, v in seen.items() if v}


def _default_audio(audio: list, prefer: str | None) -> int | None:
    plain = [k for k, (a, _) in enumerate(audio) if a.content in ("normal", "unspecified")]
    preferred = [k for k in plain if prefer and audio[k][0].lang == prefer]
    return (preferred or plain or [None])[0]


def _chapters(path: pathlib.Path, starts: list[float], end: float) -> None:
    lines = [";FFMETADATA1"]
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else end
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={round(s * 1000)}", f"END={round(e * 1000)}",
                  f"title=Chapter {k + 1}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _cross_check(cut: pathlib.Path, sid: int, v0: int, units: list[dict], tmp: pathlib.Path) -> dict:
    """ffmpeg's own timing of the subtitle stream against the formula, on a short test mux."""
    if not units:
        return {"ran": False, "why": "no subtitle units", "ok": True}
    first = units[0]["cut_offset"]
    if first > CROSS_CHECK_BYTES:
        return {"ran": False, "why": f"first subtitle {first >> 20} MiB into the cut", "ok": True}
    media.run([*media.FFMPEG, "-probesize", str(first + (8 << 20)), "-analyzeduration", "100000000000",
               "-fflags", "+genpts", "-i", str(cut), "-map", VIDEO_STREAM, "-map", f"0:i:{sid:#x}",
               "-c", "copy", "-y", str(tmp)])
    t_v = media.first_time(tmp, "v:0")
    got = [t for t, _ in media.packets(tmp, "s:0")]
    want = [t_v + _signed(u["pts"] - v0) / 90000 for u in units]
    ok = len(got) == len(want) and all(abs(a - b) <= TOLERANCE for a, b in zip(got, want))
    tmp.unlink(missing_ok=True)
    return {"ran": True, "units": len(want), "ffmpeg_units": len(got), "ok": ok}


def remux(video_ts: pathlib.Path, info: TitleInfo, part: Part, work: pathlib.Path,
          out: pathlib.Path, *, prefer_audio: str | None = None) -> dict:
    if out.exists():
        raise RemuxError(f"{out} already exists")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    report: dict = {"title": info.number, "part": part.number, "of": part.of,
                    "ifo_seconds": round(part.seconds, 3), "chapters": part.chapters,
                    "dropped_cells": part.dropped, "output": str(out), "checks": {}}
    checks = report["checks"]
    cut = work / "cut.vob"
    vob.cut(vob.title_vobs(video_ts, info.title_set), part.ranges, cut)
    count = scramble.count_file(cut)
    if count.scrambled:
        raise RemuxError(scramble.REFUSAL)
    checks["scrambled"] = {"packs_checked": count.checked, "scrambled": 0, "ok": True}

    found = {int(s["id"], 16) for s in media.streams(cut) if s.get("id")}
    if 0x1E0 not in found:
        raise RemuxError(f"title {info.number} part {part.number}: no video stream in the cut")
    audio = [(a, aid) for a, aid in zip(info.audio, info.audio_ids) if aid in found]
    report["audio_not_in_cut"] = [f"{aid:#x}" for aid in info.audio_ids if aid not in found]
    units_by_sub = vob.subpicture_counts(cut)
    subs = [(s, sid) for s, sid in zip(info.subs, info.sub_ids) if units_by_sub.get(sid)]

    stage1 = work / "stage1.mkv"
    args = [*media.FFMPEG, "-fflags", "+genpts", "-i", str(cut), "-map", VIDEO_STREAM]
    for _, aid in audio:
        args += ["-map", f"0:i:{aid:#x}"]
    media.run([*args, "-c", "copy", str(stage1)])

    needed = {0xE0} | {aid for _, aid in audio}
    first = _first_pts(cut, needed)
    if absent := sorted(needed - first.keys()):
        raise RemuxError(f"title {info.number} part {part.number}: no timestamp found for stream "
                         + ", ".join(f"{k:#x}" for k in absent) + " in the packs read")
    v0, t_v = first[0xE0], media.first_time(stage1, "v:0")
    anchor = []
    for k, (_, aid) in enumerate(audio):
        want = t_v + _signed(first[aid] - v0) / 90000
        got = media.first_time(stage1, f"a:{k}")
        anchor.append({"stream": f"{aid:#x}", "formula": round(want, 4), "ffmpeg": round(got, 4),
                       "ok": abs(want - got) <= TOLERANCE})
    checks["anchor"] = {"audio": anchor, "ok": all(a["ok"] for a in anchor) if subs else True}

    video = next((s for s in media.streams(stage1) if s.get("codec_type") == "video"), None)
    if video is None or not video.get("width") or not video.get("height"):
        raise RemuxError(f"title {info.number} part {part.number}: ffmpeg wrote no sized video stream")
    sub_files = []
    for j, (s, sid) in enumerate(subs):
        sub, idx = work / f"sub{j}.sub", work / f"sub{j}.idx"
        units = vob.extract_vobsub([cut], sid, sub)
        times = [t_v + _signed(u["pts"] - v0) / 90000 for u in units]
        vob.write_idx(idx, size=(video["width"], video["height"]), palette=info.palette, lang=s.lang,
                      times=times, filepos=[u["filepos"] for u in units])
        cross = _cross_check(cut, sid, v0, units, work / f"cross{j}.mkv")
        sub_files.append((s, sid, sub, idx, times, cross))

    final = work / "final.mkv"
    args = [*media.FFMPEG, "-i", str(stage1)]
    for _, _, _, idx, _, _ in sub_files:
        args += ["-i", str(idx)]
    if len(part.chapters) > 1:
        meta = work / "chapters.ffmeta"
        _chapters(meta, part.chapters, part.seconds)
        args += ["-i", str(meta), "-map_chapters", str(1 + len(sub_files))]
    else:
        args += ["-map_chapters", "-1"]
    args += ["-map", "0:v", "-map", "0:a?"]
    for j in range(len(sub_files)):
        args += ["-map", f"{1 + j}:s:0"]
    args += ["-map_metadata", "-1", "-c", "copy"]
    default = _default_audio(audio, prefer_audio)
    for k, (a, _) in enumerate(audio):
        flags = (["default"] if k == default else []) + (["comment"] if "commentary" in a.content else [])
        args += [f"-metadata:s:a:{k}", f"language={lang.matroska(a.lang)}",
                 f"-metadata:s:a:{k}", f"title={AUDIO_TITLE.get(a.content, lang.name(a.lang))}",
                 f"-disposition:a:{k}", "+".join(flags) or "0"]
    for j, (s, *_rest) in enumerate(sub_files):
        args += [f"-metadata:s:s:{j}", f"language={lang.matroska(s.lang)}",
                 f"-metadata:s:s:{j}", f"title={lang.name(s.lang)} (DVD)",
                 f"-disposition:s:{j}", "forced" if s.content == "forced" else "0"]
    media.run([*args, str(final)])

    for k, (_, aid) in enumerate([(None, 0x1E0)] + audio):
        spec = "0:v:0" if k == 0 else f"0:a:{k - 1}"
        same = media.packet_hashes(cut, f"0:i:{aid:#x}", genpts=True) == media.packet_hashes(final, spec)
        checks.setdefault("packets", []).append({"stream": f"{aid:#x}", "ok": same})
    for j, (s, sid, sub, _, times, cross) in enumerate(sub_files):
        src, got = media.packets(sub, "s:0"), media.packets(final, f"s:{j}")
        checks.setdefault("subtitles", []).append({
            "stream": f"{sid:#x}", "units": len(times), "in_mkv": len(got),
            "data_ok": [h for _, h in src] == [h for _, h in got],
            "times_ok": len(got) == len(times) and all(abs(a - b) <= TOLERANCE
                                                       for (a, _), b in zip(got, times)),
            "cross_check": cross})
        last = checks["subtitles"][-1]
        last["ok"] = last["data_ok"] and last["times_ok"] and cross["ok"]
    fps = info.fps or 25.0
    shown = media.video_seconds(final)
    checks["duration"] = {"mkv": round(shown, 3), "ifo": round(part.seconds, 3),
                          "ok": abs(shown - part.seconds) <= 2 / fps}
    if audio:
        offsets = [media.first_time(p, "a:0") - media.first_time(p, "v:0") for p in (cut, final)]
        checks["av_offset"] = {"cut_ms": round(offsets[0] * 1000, 1), "mkv_ms": round(offsets[1] * 1000, 1),
                               "ok": abs(offsets[0] - offsets[1]) <= 0.001}
    errors = media.decode_errors(final)
    checks["decode"] = {"error_lines": len(errors), "first": errors[:5], "ok": not errors}

    report["ok"] = all(c["ok"] for v in checks.values() for c in (v if isinstance(v, list) else [v]))
    if report["ok"]:
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(final), str(out))
        report["bytes"], report["sha256"] = out.stat().st_size, copier.sha256_file(out)
        for f in [cut, stage1, *[x for sf in sub_files for x in sf[2:4]]]:
            f.unlink(missing_ok=True)
    (work / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
    return report
