"""Compile the package's Swift helpers (dvdtoplex/swift/NAME.swift) with the system swiftc.

The compiled helpers are cached in a private per-user folder (DVDTOPLEX_CACHE, default
~/Library/Caches/dvd-to-plex/bin), never in a work folder that may be shared. A cached helper is
run later, so the folder and the file are refused unless they are the user's own, not symlinks and
not writable by group or other.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import secrets
import shutil
import stat
import subprocess
from importlib import resources


class SwiftError(RuntimeError):
    """swiftc is missing or failed, or the helper cache is not private."""


def cache_dir() -> pathlib.Path:
    if env := os.environ.get("DVDTOPLEX_CACHE"):
        return pathlib.Path(env)
    return pathlib.Path.home() / "Library" / "Caches" / "dvd-to-plex" / "bin"


def _check(p: pathlib.Path, kind: str) -> None:
    """Refuse P (a folder or a file) unless it is the user's own, not a symlink, and not writable
    by group or other; read with lstat so a link is never followed."""
    st = p.lstat()
    wanted = stat.S_ISDIR if kind == "folder" else stat.S_ISREG
    if stat.S_ISLNK(st.st_mode):
        why = "is a symlink"
    elif not wanted(st.st_mode):
        why = f"is not a {kind}"
    elif st.st_uid != os.getuid():
        why = "is not owned by you"
    elif st.st_mode & 0o022:
        why = f"is writable by group or other (mode {stat.S_IMODE(st.st_mode):04o})"
    else:
        return
    raise SwiftError(f"refusing the helper cache {kind} {p}: it {why}; "
                     f"use a private folder (DVDTOPLEX_CACHE)")


def _private_folder(dest: pathlib.Path) -> None:
    """Create DEST and any missing parent with mode 0700, then check DEST."""
    missing = []
    q = dest
    while not os.path.lexists(q):
        missing.append(q)
        q = q.parent
    for q in reversed(missing):
        try:
            os.mkdir(q, 0o700)
        except FileExistsError:
            pass
    _check(dest, "folder")


def build(name: str, dest: pathlib.Path | None = None) -> pathlib.Path:
    """The compiled helper in DEST (the private cache by default), named after its source's hash so
    a changed source is rebuilt."""
    dest = dest or cache_dir()
    source = resources.files("dvdtoplex") / "swift" / f"{name}.swift"
    binary = dest / f"{name}-{hashlib.sha256(source.read_bytes()).hexdigest()[:12]}"
    _private_folder(dest)
    if os.path.lexists(binary):
        _check(binary, "file")
        return binary
    if shutil.which("swiftc") is None:
        raise SwiftError("swiftc is not on PATH; install the Xcode Command Line Tools")
    partial = dest / f".{binary.name}.{secrets.token_hex(4)}.partial"
    try:
        with resources.as_file(source) as path:
            p = subprocess.run(["swiftc", "-O", str(path), "-o", str(partial)], capture_output=True,
                               text=True, timeout=600)
        if p.returncode != 0:
            tail = "\n".join(p.stderr.strip().splitlines()[-8:])
            raise SwiftError(f"swiftc failed on {name}.swift ({p.returncode}):\n{tail}")
        os.chmod(partial, 0o700)
        os.replace(partial, binary)
    finally:
        partial.unlink(missing_ok=True)
    return binary
