"""Copy a disc's VIDEO_TS into a work folder with plain file reads, and record what was copied.

A read error stops the copy: a disc that cannot be read cleanly is not copied partially or with
gaps filled in. Each file's SHA-256 and size go into WORK/copy.json, which remux checks again.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil

from dvdtoplex import scramble

CHUNK = 1 << 20


class CopyError(RuntimeError):
    """The copy stopped: a read error, a short read, or a work folder already in use."""


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fin:
        for block in iter(lambda: fin.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_file(src: pathlib.Path, dst: pathlib.Path) -> dict:
    """Copy SRC to DST through DST.part, stopping on a read error; returns its name, size and SHA-256."""
    size = src.stat().st_size
    digest, done = hashlib.sha256(), 0
    tmp = dst.with_name(dst.name + ".part")
    try:
        with open(src, "rb", buffering=0) as fin, open(tmp, "wb") as fout:
            while True:
                try:
                    block = fin.read(CHUNK)
                except OSError as e:
                    raise CopyError(f"read error in {src.name} at byte {done}: {e.strerror or e}") from e
                if not block:
                    break
                digest.update(block)
                fout.write(block)
                done += len(block)
            fout.flush()
            os.fsync(fout.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if done != size:
        tmp.unlink(missing_ok=True)
        raise CopyError(f"{src.name}: read {done} bytes of {size}")
    tmp.rename(dst)
    return {"name": src.name, "bytes": size, "sha256": digest.hexdigest()}


def copy(video_ts: pathlib.Path, work: pathlib.Path) -> dict:
    """Copy VIDEO_TS's IFO, BUP and VOB files to WORK/VIDEO_TS; returns the manifest written to
    WORK/copy.json. Refuses scrambled video before reading anything else."""
    count = scramble.count_disc(video_ts)
    if count.scrambled:
        raise CopyError(scramble.REFUSAL)
    dest = work / "VIDEO_TS"
    if dest.exists():
        raise CopyError(f"{dest} already exists; use an empty work folder")
    files = sorted(p for p in video_ts.iterdir()
                   if p.is_file() and p.suffix.upper() in (".IFO", ".BUP", ".VOB"))
    need = sum(p.stat().st_size for p in files)
    work.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(work).free
    if free < need + (1 << 30):
        raise CopyError(f"{work} has {free >> 20} MiB free; the copy needs {need >> 20} MiB plus 1 GiB")
    dest.mkdir()
    manifest = {"source": str(video_ts), "files": []}
    for src in files:
        manifest["files"].append(copy_file(src, dest / src.name.upper()))
    (work / "copy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify(work: pathlib.Path) -> list[str]:
    """Problems found re-hashing WORK/VIDEO_TS against WORK/copy.json; empty when it matches."""
    manifest = json.loads((work / "copy.json").read_text(encoding="utf-8"))
    problems = []
    for f in manifest["files"]:
        p = work / "VIDEO_TS" / f["name"].upper()
        if not p.is_file():
            problems.append(f"{f['name']}: missing")
            continue
        if p.stat().st_size != f["bytes"] or sha256_file(p) != f["sha256"]:
            problems.append(f"{f['name']}: changed since the copy")
    return problems
