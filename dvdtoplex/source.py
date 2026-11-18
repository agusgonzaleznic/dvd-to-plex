"""Where the disc is: a mounted disc, a folder that contains VIDEO_TS, VIDEO_TS itself, or an ISO image.

An ISO is attached read-only with hdiutil for as long as it is in use and detached afterwards.
"""
from __future__ import annotations

import contextlib
import pathlib
import plistlib
import subprocess
from collections.abc import Iterator


class SourceError(ValueError):
    """The path is not something dvd-to-plex can read DVD-Video from."""


def find_video_ts(folder: pathlib.Path) -> pathlib.Path:
    candidates = [folder] if folder.name.upper() == "VIDEO_TS" else []
    candidates += [c for c in sorted(folder.iterdir()) if c.is_dir() and c.name.upper() == "VIDEO_TS"]
    for c in candidates:
        if (c / "VIDEO_TS.IFO").is_file():
            return c
    raise SourceError(f"no VIDEO_TS folder with a VIDEO_TS.IFO in {folder}")


def _attach(iso: pathlib.Path) -> pathlib.Path:
    p = subprocess.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen", "-noverify",
                        "-plist", str(iso)], capture_output=True, timeout=300)
    if p.returncode != 0:
        raise SourceError(f"hdiutil could not attach {iso}: "
                          + p.stderr.decode("utf-8", "replace").strip())
    mounts = [e["mount-point"] for e in plistlib.loads(p.stdout).get("system-entities", [])
              if e.get("mount-point")]
    if len(mounts) != 1:
        for m in mounts:
            _detach(pathlib.Path(m))
        raise SourceError(f"{iso} attached with {len(mounts)} mount points; expected one")
    return pathlib.Path(mounts[0])


def _detach(mount: pathlib.Path) -> None:
    for extra in ([], ["-force"]):
        if subprocess.run(["hdiutil", "detach", *extra, str(mount)], capture_output=True,
                          timeout=120).returncode == 0:
            return
    raise SourceError(f"could not detach {mount}; detach it with: hdiutil detach -force {mount}")


@contextlib.contextmanager
def opened(source: pathlib.Path) -> Iterator[pathlib.Path]:
    """Yield the VIDEO_TS folder of SOURCE; an ISO stays attached until the block ends."""
    source = source.expanduser()
    if source.is_dir():
        yield find_video_ts(source)
    elif source.is_file() and source.suffix.lower() == ".iso":
        mount = _attach(source)
        try:
            yield find_video_ts(mount)
        finally:
            _detach(mount)
    elif not source.exists():
        raise SourceError(f"{source} does not exist")
    else:
        raise SourceError(f"{source} is neither a folder nor an .iso image")
