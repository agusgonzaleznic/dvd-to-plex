"""Copy a disc's VIDEO_TS into a work folder with plain file reads, and record what was copied.

A read error stops the copy: a disc that cannot be read cleanly is not copied partially or with
gaps filled in. Each file's SHA-256 and size go into WORK/copy.json, which remux checks again.
Every file is opened without following a link and must be a regular file. Every pack of every VOB
is checked for scrambling while it is copied.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil

from dvdtoplex import scramble, source

CHUNK = 1 << 20


class CopyError(RuntimeError):
    """The copy stopped: a read error, a short read, or a work folder already in use."""


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fin:
        for block in iter(lambda: fin.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_file(src: pathlib.Path, dst: pathlib.Path, *, check_scramble: bool = False) -> dict:
    """Copy SRC to DST through DST.part, stopping on a read error; returns its name, size and SHA-256.

    SRC is opened without following a link and must be a regular file. With CHECK_SCRAMBLE every
    2048-byte pack is tested as it is read, and a scrambled one raises CopyError(scramble.REFUSAL)."""
    name = source.safe_name(src.name)
    digest, done = hashlib.sha256(), 0
    tmp = dst.with_name(dst.name + ".part")
    carry = b""     # bytes of a pack that a chunk ended in the middle of
    try:
        with source.open_regular(src, CopyError, buffering=0) as fin, open(tmp, "wb") as fout:
            size = os.fstat(fin.fileno()).st_size
            while True:
                try:
                    block = fin.read(CHUNK)
                except OSError as e:
                    raise CopyError(f"read error in {name} at byte {done}: {e.strerror or e}") from e
                if not block:
                    break
                if check_scramble:
                    data = carry + block if carry else block
                    if scramble.count_packs(data).scrambled:
                        raise CopyError(scramble.REFUSAL)
                    carry = data[len(data) // scramble.SECTOR * scramble.SECTOR:]
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
        raise CopyError(f"{name}: read {done} bytes of {size}")
    tmp.rename(dst)
    return {"name": src.name, "bytes": size, "sha256": digest.hexdigest()}


def _select(video_ts: pathlib.Path) -> list[pathlib.Path]:
    """The IFO, BUP and VOB files of VIDEO_TS, sorted. A link, a folder or a special file with such
    a name is disc content that is not followed or copied: it stops the copy."""
    files = []
    for p in sorted(video_ts.iterdir()):
        if p.suffix.upper() not in (".IFO", ".BUP", ".VOB"):
            continue
        if not source.is_regular(p):
            raise CopyError(f"{source.safe_name(p.name)} is not a regular file; links and special files "
                            "in a disc folder are refused")
        files.append(p)
    return files


def _missing_dirs(folder: pathlib.Path) -> list[pathlib.Path]:
    """FOLDER and its parents that do not exist yet, deepest first."""
    out = []
    for d in (folder, *folder.parents):
        if d.exists():
            break
        out.append(d)
    return out


def copy(video_ts: pathlib.Path, work: pathlib.Path) -> dict:
    """Copy VIDEO_TS's IFO, BUP and VOB files to WORK/VIDEO_TS; returns the manifest written to
    WORK/copy.json. Refuses scrambled video before copying anything: a sample first, then every pack
    while each VOB is copied. A scramble found during the copy removes what this run created."""
    files = _select(video_ts)
    count = scramble.count_disc(video_ts)
    if count.scrambled:
        raise CopyError(scramble.REFUSAL)
    dest = work / "VIDEO_TS"
    if dest.exists():
        raise CopyError(f"{dest} already exists; use an empty work folder")
    need = sum(os.lstat(p).st_size for p in files)
    made = _missing_dirs(work)
    work.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(work).free
    if free < need + (1 << 30):
        raise CopyError(f"{work} has {free >> 20} MiB free; the copy needs {need >> 20} MiB plus 1 GiB")
    dest.mkdir()
    manifest = {"source": str(video_ts), "files": []}
    try:
        for src in files:
            manifest["files"].append(copy_file(src, dest / src.name.upper(),
                                               check_scramble=src.suffix.upper() == ".VOB"))
    except CopyError as e:
        if str(e) == scramble.REFUSAL:
            _undo(dest, made)
        raise
    (work / "copy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _undo(dest: pathlib.Path, made: list[pathlib.Path]) -> None:
    """Remove WORK/VIDEO_TS, which this run created (copy refuses an existing one), and the work
    folders this run made, only while empty. Nothing that existed before the run is touched."""
    shutil.rmtree(dest, ignore_errors=True)
    for d in made:
        try:
            d.rmdir()
        except OSError:
            break


def verify(work: pathlib.Path) -> list[str]:
    """Problems found re-hashing WORK/VIDEO_TS against WORK/copy.json; empty when it matches."""
    manifest = json.loads((work / "copy.json").read_text(encoding="utf-8"))
    problems = []
    for f in manifest["files"]:
        p, name = work / "VIDEO_TS" / f["name"].upper(), source.safe_name(f["name"])
        if not p.is_file():
            problems.append(f"{name}: missing")
            continue
        if p.stat().st_size != f["bytes"] or sha256_file(p) != f["sha256"]:
            problems.append(f"{name}: changed since the copy")
    return problems
