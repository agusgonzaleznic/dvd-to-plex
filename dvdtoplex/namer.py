"""Move remuxed files into a Plex library under the names plex-renamer gives them.

A names file says what each file in WORK/out is: the film (or one part of it), an extra with its
Plex extras folder and label, or skip. Every file in WORK/out must be listed, so none is left
behind unnoticed. Before anything moves, each file is hashed against its remux report (or its
embed report, once embed added text tracks), and the
planned names are laid out as sparse files and run through plex-renamer's own scan, classify and
plan: it must read back the same title, year, id and edition and want to rename nothing.
WORK/name.json records every move before it happens and marks each one once it is done.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile

from plexrenamer import classify, naming, scan, util
from plexrenamer.planner import Planner

from dvdtoplex import copier

STEM = re.compile(r"^title_(\d{2})(?:_part(\d+))?$")
IDS = {"imdb": re.compile(r"^tt\d{7,10}$"), "tmdb": re.compile(r"^\d{1,10}$")}
ROLES = ("movie", "extra", "skip")
FOLDERS = sorted(set(util.EXTRAS_DIRS.values()))


class PlaceError(RuntimeError):
    """The names file is wrong, a check failed, or a file is already at its destination."""


def _text(d: dict, key: str, where: str, *, required: bool = True) -> str | None:
    v = d.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v.strip():
        raise PlaceError(f"{where}: {key!r} must be a non-empty string")
    return v.strip()


def title_info(names: dict) -> naming.TitleInfo:
    title = _text(names, "title", "names")
    year = names.get("year")
    if year is not None and not (isinstance(year, int) and 1880 <= year <= 2100):
        raise PlaceError(f"names: 'year' must be a whole number from 1880 to 2100, not {year!r}")
    ids = {}
    for key, pattern in IDS.items():
        if (v := names.get(key)) is not None:
            if not isinstance(v, str) or not pattern.match(v):
                raise PlaceError(f"names: {v!r} is not a {key} id")
            ids[key] = v
    if len(ids) > 1:
        raise PlaceError("names: give an imdb or a tmdb id, not both; Plex reads only one")
    edition = _text(names, "edition", "names", required=False)
    return naming.TitleInfo(kind="movie", title=title, year=year, ids=ids,
                            editions=[edition] if edition else [])


def _category(names: dict) -> str:
    category = _text(names, "category", "names")
    if util.sanitize(category) != category or category.startswith("."):
        raise PlaceError(f"names: category {category!r} must be a plain folder name")
    return category


def _json(path: pathlib.Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def checked(work: pathlib.Path, stem: str) -> dict | None:
    """The report holding the size and SHA-256 WORK/out/<stem>.mkv was last checked at: its embed
    report once embed replaced the file, if that embed started from the file its remux report
    checked; otherwise the remux report. None when neither passes."""
    remux = _json(work / "remux" / stem / "report.json")
    if not remux.get("ok") or not remux.get("sha256") or "bytes" not in remux:
        return None
    embedded = _json(work / "embed" / stem / "report.json")
    if not embedded.get("ok"):
        return remux                          # never embedded, or its checks failed and nothing moved
    if not embedded.get("replaced") or embedded.get("original", {}).get("sha256") != remux["sha256"]:
        return None
    return embedded


def plan(work: pathlib.Path, library: pathlib.Path, names: dict) -> dict:
    """The record of what goes where, checked against the remux (or embed) reports and plex-renamer."""
    if not isinstance(names, dict) or not isinstance(names.get("files"), dict):
        raise PlaceError("names: expected an object with a 'files' object")
    info, category = title_info(names), _category(names)
    folder = info.folder()
    stems = sorted(p.stem for p in (work / "out").glob("*.mkv"))
    mapping = names["files"]
    if unknown := sorted(set(mapping) - set(stems)):
        raise PlaceError(f"names lists {', '.join(unknown)}, not in {work / 'out'}")
    if missing := [s for s in stems if s not in mapping]:
        raise PlaceError(f"{', '.join(missing)} in {work / 'out'} not in names; give each a role: "
                         + ", ".join(ROLES))
    roles = {}
    for stem, entry in mapping.items():
        role = entry.get("role") if isinstance(entry, dict) else None
        if role not in ROLES:
            raise PlaceError(f"names: {stem}: 'role' must be one of {', '.join(ROLES)}")
        roles[stem] = role
    movies = [s for s in stems if roles[s] == "movie"]
    if not movies:
        raise PlaceError("names: no file has the role movie")
    parts = {s: STEM.match(s) for s in movies}
    if len(movies) > 1 and (len({m[1] for m in parts.values() if m}) != 1 or not all(
            m and m[2] for m in parts.values())):
        raise PlaceError("names: only the parts of one title can share the role movie, "
                         f"not {', '.join(movies)}")

    files, taken = [], set()
    for stem in stems:
        entry = mapping[stem]
        if roles[stem] == "skip":
            continue
        if roles[stem] == "movie":
            part = int(parts[stem][2]) if len(movies) > 1 else None
            rel = f"{category}/{folder}/{naming.movie_file(info, '.mkv', part=part)}"
        else:
            kind = util.EXTRAS_DIRS.get(_text(entry, "folder", f"names: {stem}").lower())
            if kind is None:
                raise PlaceError(f"names: {stem}: 'folder' must be one of {', '.join(FOLDERS)}")
            label = _text(entry, "label", f"names: {stem}")
            rel = f"{category}/{folder}/{kind}/{naming.extras_file(kind, label, '.mkv')}"
        if rel.lower() in taken:
            raise PlaceError(f"names: two files would be named {rel}")
        taken.add(rel.lower())
        report = checked(work, stem)
        if report is None:
            raise PlaceError(f"{stem}: no passing remux report with a SHA-256 at "
                             f"{work / 'remux' / stem / 'report.json'}, or its embed report does not follow it")
        if (library / rel).exists():
            raise PlaceError(f"{library / rel} already exists; nothing was moved")
        files.append({"stem": stem, "role": roles[stem], "dst": rel, "bytes": report["bytes"],
                      "sha256": report["sha256"], "placed": False})
    record = {"library": str(library.resolve()), "category": category, "folder": folder,
              "info": {"title": info.title, "year": info.year, "ids": info.ids, "editions": info.editions},
              "files": files}
    if problems := roundtrip(record, [(f["dst"], f["bytes"]) for f in files]):
        raise PlaceError("plex-renamer reads these names differently: " + "; ".join(problems))
    return record


def roundtrip(record: dict, files: list[tuple[str, int]]) -> list[str]:
    """What plex-renamer disagrees with, reading FILES (paths under the library, with sizes) laid
    out as sparse files: a different title, year, id or edition, or any move it would make."""
    i = record["info"]
    want = (util.sanitize(i["title"]), i["year"], i["ids"], i["editions"])
    category = record["category"]
    with tempfile.TemporaryDirectory(prefix="dvdtoplex-names-") as tmp:
        for rel, size in files:
            p = pathlib.Path(tmp, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "wb") as f:
                f.truncate(size)
        tree = scan.scan(tmp, [category]).get(category)
        units = classify.classify_category(category, tree) if tree else []
        if len(units) != 1:
            return [f"it finds {len(units)} titles in {category}, not 1: "
                    + ", ".join(repr(u.info.title) for u in units)]
        got = units[0].info
        problems = []
        if (got.title, got.year, got.ids, got.editions) != want:
            problems.append(f"it reads title {got.title!r}, year {got.year}, ids {got.ids}, "
                            f"editions {got.editions}")
        problems += [f"it would move {os.path.relpath(m.src, tmp)} to {os.path.relpath(m.dst, tmp)}"
                     for m in Planner(tmp).plan_unit(units[0]).moves]
        return problems


def _same_device(src: pathlib.Path, folder: pathlib.Path) -> bool:
    return os.stat(src).st_dev == os.stat(folder).st_dev


def _move(src: pathlib.Path, dst: pathlib.Path, sha256: str) -> None:
    """Rename within one file system; across file systems copy, check the copy's SHA-256, and
    only then remove the source."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        raise PlaceError(f"{dst} appeared while naming; nothing was overwritten")
    if _same_device(src, dst.parent):
        os.rename(src, dst)
        return
    partial = dst.with_name(dst.name + ".partial")
    read = copier.copy_file(src, partial)["sha256"]       # what was read from the source
    if read != sha256 or copier.sha256_file(partial) != sha256:   # and what landed
        partial.unlink(missing_ok=True)
        raise PlaceError(f"the copy of {src.name} to {dst.parent} does not match; the source is kept")
    os.rename(partial, dst)
    src.unlink()


def place(work: pathlib.Path, library: pathlib.Path, names_file: pathlib.Path, *,
          dry_run: bool = False) -> dict:
    try:
        names = json.loads(names_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise PlaceError(f"cannot read the names file {names_file}: {e}") from e
    if not library.is_dir():
        raise PlaceError(f"{library} is not a folder")
    record = plan(work, library, names)
    if dry_run:
        return record
    log = work / "name.json"
    if log.exists():
        raise PlaceError(f"{log} exists: these files were named already, or a run stopped part "
                         "way; it lists every move")
    for f in record["files"]:
        if copier.sha256_file(work / "out" / f"{f['stem']}.mkv") != f["sha256"]:
            raise PlaceError(f"{f['stem']}.mkv changed since it was checked; nothing was moved")

    def save() -> None:
        log.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    save()
    for f in record["files"]:
        _move(work / "out" / f"{f['stem']}.mkv", library / f["dst"], f["sha256"])
        f["placed"] = True
        save()
    return record
