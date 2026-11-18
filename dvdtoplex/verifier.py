"""Check the end state in the library against what was checked and what name recorded.

Every file name.json lists must be at its destination with the size and SHA-256 its remux report
gave (or its embed report, once embed added text tracks), and plex-renamer, reading the title folder as it is now (laid out again as sparse files),
must agree with the names and want to move nothing. The result goes to WORK/verify.json.
"""
from __future__ import annotations

import json
import pathlib

from dvdtoplex import copier, namer


class VerifyError(RuntimeError):
    """There is nothing to verify: no name.json, or one that cannot be read."""


def verify(work: pathlib.Path) -> dict:
    log = work / "name.json"
    try:
        record = json.loads(log.read_text(encoding="utf-8"))
        library = pathlib.Path(record["library"])
        folder = library / record["category"] / record["folder"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise VerifyError(f"cannot read {log}; run name first ({e})") from e
    problems = []
    for f in record["files"]:
        dst = library / f["dst"]
        report = namer.checked(work, f["stem"])
        if report is None or report.get("sha256") != f["sha256"]:
            problems.append(f"{f['stem']}: name.json does not match its passing remux or embed report")
        if not f.get("placed"):
            problems.append(f"{f['dst']}: never moved (the name run stopped before it)")
        elif not dst.is_file():
            problems.append(f"{f['dst']}: missing")
        elif dst.stat().st_size != f["bytes"] or copier.sha256_file(dst) != f["sha256"]:
            problems.append(f"{f['dst']}: changed since it was checked")
    if folder.is_dir():
        found = [(p.relative_to(library).as_posix(), p.stat().st_size) for p in sorted(folder.rglob("*"))
                 if p.is_file() and p.name != ".DS_Store" and not p.name.startswith("._")]
        problems += [f"plex-renamer: {p}" for p in namer.roundtrip(record, found)]
    else:
        problems.append(f"{folder} is missing")
    result = {"folder": str(folder), "files": len(record["files"]), "problems": problems,
              "ok": not problems}
    (work / "verify.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
    return result
