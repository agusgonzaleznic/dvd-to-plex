"""Where the disc is: a mounted disc, a folder that contains VIDEO_TS, VIDEO_TS itself, or an ISO image.

An ISO is attached read-only with hdiutil for as long as it is in use and detached afterwards.

The path the user gives is trusted, even when it is a link. Everything found beneath it is disc
content and is not: a link or a special file there is refused, never followed (see open_regular).
"""
from __future__ import annotations

import contextlib
import errno
import os
import pathlib
import plistlib
import stat
import subprocess
import xml.parsers.expat
from collections.abc import Iterator
from typing import BinaryIO


class SourceError(ValueError):
    """The path is not something dvd-to-plex can read DVD-Video from."""


def safe_name(name: str) -> str:
    """A disc file name for an error message: as is when printable, else repr() (names are disc
    content and can hold terminal escape codes)."""
    return name if name.isprintable() else repr(name)


def is_regular(path: pathlib.Path) -> bool:
    """True when PATH is a regular file itself, not a link to one (lstat; a missing path is False)."""
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def open_regular(path: pathlib.Path, error: type[Exception] = SourceError, *,
                 buffering: int = -1) -> BinaryIO:
    """Open disc file PATH for reading without following a symlink, as a binary file.

    Raises ERROR for a link or for anything that is not a regular file (a folder, a FIFO, a device).
    The kind is read from the open descriptor, so nothing can be swapped in after the check. Other
    OSErrors, such as a missing file, propagate as they are."""
    label = safe_name(path.name)
    try:
        # O_NONBLOCK: opening a FIFO for reading must not wait for a writer.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise error(f"{label} is a symbolic link; links inside a disc folder are not followed") from e
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise error(f"{label} is not a regular file; special files in a disc folder are refused")
        os.set_blocking(fd, True)       # a regular file from here on: plain blocking reads
        return os.fdopen(fd, "rb", buffering=buffering)
    except BaseException:
        os.close(fd)
        raise


def find_video_ts(folder: pathlib.Path) -> pathlib.Path:
    """The VIDEO_TS folder of FOLDER. FOLDER is trusted, even as a link, and so is FOLDER itself
    named VIDEO_TS. A VIDEO_TS found inside it is disc content: it must be a real folder, not a link."""
    candidates = [folder] if folder.name.upper() == "VIDEO_TS" else []
    candidates += [c for c in sorted(folder.iterdir())
                   if c.name.upper() == "VIDEO_TS" and _is_real_dir(c)]
    for c in candidates:
        if is_regular(c / "VIDEO_TS.IFO"):
            return c
    raise SourceError(f"no VIDEO_TS folder with a VIDEO_TS.IFO in {folder} "
                      "(links inside a disc folder are not followed)")


def _is_real_dir(path: pathlib.Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _attach(iso: pathlib.Path) -> pathlib.Path:
    p = subprocess.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen", "-noverify",
                        "-plist", str(iso)], capture_output=True, timeout=300)
    if p.returncode != 0:
        raise SourceError(f"hdiutil could not attach {iso}: "
                          + p.stderr.decode("utf-8", "replace").strip())
    try:
        mounts = [e["mount-point"] for e in plistlib.loads(p.stdout).get("system-entities", [])
                  if e.get("mount-point")]
    except (ValueError, AttributeError, TypeError, xml.parsers.expat.ExpatError) as e:
        raise SourceError(f"hdiutil's answer for {iso.name} could not be read ({type(e).__name__}); "
                          "check `hdiutil info` for an image left attached") from e
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
