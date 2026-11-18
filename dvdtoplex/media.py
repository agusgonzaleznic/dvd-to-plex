"""ffmpeg and ffprobe, wrapped for the checks remux runs on its own output."""
from __future__ import annotations

import json
import pathlib
import subprocess

FFMPEG = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error"]


class MediaError(RuntimeError):
    """ffmpeg or ffprobe failed."""


def run(args: list[str], *, timeout: float = 7200) -> str:
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        tail = "\n".join(p.stderr.strip().splitlines()[-6:])
        raise MediaError(f"{pathlib.Path(args[0]).name} failed ({p.returncode}): {tail}")
    return p.stdout


def packet_hashes(path: pathlib.Path, spec: str, *, genpts: bool = False,
                  seconds: float | None = None) -> list[tuple[int, str]]:
    """(size, SHA-256) per packet of one stream, in order, read with -c copy."""
    args = [*FFMPEG, *(["-fflags", "+genpts"] if genpts else []), "-i", str(path)]
    args += ["-t", str(seconds)] if seconds is not None else []
    args += ["-map", spec, "-c", "copy", "-f", "framehash", "-hash", "sha256", "-"]
    out = []
    for line in run(args).splitlines():
        if line and not line.startswith("#"):
            f = [x.strip() for x in line.split(",")]
            out.append((int(f[4]), f[5].split("=", 1)[-1]))
    return out


def packets(path: pathlib.Path, spec: str, *, limit: int | None = None) -> list[tuple[float, str]]:
    """(pts in seconds, SHA-256 of the data) of each packet of one stream, via ffprobe."""
    args = ["ffprobe", "-v", "error", "-select_streams", spec, "-show_data_hash", "sha256",
            "-show_entries", "packet=pts_time,data_hash", "-of", "json"]
    args += ["-read_intervals", f"%+#{limit}"] if limit else []
    rows = json.loads(run([*args, str(path)]))["packets"]
    return [(float(r["pts_time"]), r["data_hash"].split(":", 1)[-1])
            for r in rows if r.get("pts_time") not in (None, "N/A")]


def first_time(path: pathlib.Path, spec: str, *, n: int = 48) -> float:
    """Earliest pts among a stream's first N packets (B-frames put the first PTS later)."""
    times = [t for t, _ in packets(path, spec, limit=n)]
    if not times:
        raise MediaError(f"no packets with a timestamp in {spec} of {path.name}")
    return min(times)


def streams(path: pathlib.Path) -> list[dict]:
    return json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)]))["streams"]


def video_seconds(path: pathlib.Path) -> float:
    """Shown length of the video stream: from its first frame's start to its last frame's end."""
    rows = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "packet=pts_time,duration_time", "-of", "json", str(path)]))["packets"]
    starts = [float(r["pts_time"]) for r in rows if r.get("pts_time") not in (None, "N/A")]
    ends = [float(r["pts_time"]) + float(r.get("duration_time") or 0) for r in rows
            if r.get("pts_time") not in (None, "N/A")]
    if not starts:
        raise MediaError(f"no video packets in {path.name}")
    return max(ends) - min(starts)


def decode_errors(path: pathlib.Path) -> list[str]:
    """Error lines from a full decode of every stream; subtitles are re-encoded to dvdsub because
    the null muxer needs an encoder per stream."""
    p = subprocess.run([*FFMPEG, "-i", str(path), "-map", "0", "-c:s", "dvdsub", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=7200)
    lines = p.stderr.strip().splitlines()
    if p.returncode != 0 and not lines:
        lines = [f"ffmpeg exited {p.returncode}"]
    return lines
