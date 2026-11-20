"""Score the proofreader's answers against the planted controls, and write the source SRT.

Each plant ends one of four ways: restored (the original word is back and the planted one gone),
flagged (the planted word kept but the cue marked unsure), kept, or changed some other way (cut or
rewritten). With the image as the source every word can be checked, so only restored passes. One
plant that does not pass fails its whole chunk: the chunk is redone with fresh plants, never
patched. The report says how many controls a chunk failed, never which cue or word.

overrides.json in a track's review folder ({"cue number": "text"}) changes the SRT's text after
scoring and never a plant's verdict.
"""
from __future__ import annotations

import json
import pathlib

from dvdtoplex import review


class AssembleError(RuntimeError):
    """A chunk's answers are missing or malformed."""


def _answers(chunk: pathlib.Path, numbers: list[int]) -> dict[int, dict]:
    path = chunk / "answers.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))["cues"]
        got = {int(r["n"]): {"text": str(r["text"]), "note": str(r.get("note") or "")} for r in rows}
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise AssembleError(f"{chunk.parent.name}/{chunk.name}: no readable answers.json ({type(e).__name__})") from e
    if len(rows) != len(got) or sorted(got) != sorted(numbers):
        raise AssembleError(f"{chunk.parent.name}/{chunk.name}: answers.json must answer each of its "
                            f"{len(numbers)} cues exactly once")
    for n, answer in sorted(got.items()):
        if problem := _shape(answer["text"]):
            raise AssembleError(f"{chunk.parent.name}/{chunk.name}: cue {n}'s text {problem}, "
                                "which could change the SRT's cues")
    return got


# Every character str.splitlines() breaks a line on, except "\n" (pinned by a test).
BREAKS = frozenset("\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")


def _shape(text: str) -> str | None:
    """What in a cue's text could add or split an SRT cue, or None. An empty text stays empty."""
    if not text.strip():
        return None
    if BREAKS.intersection(text):
        return "has a line break other than a newline"
    if any(not line.strip() for line in text.strip().split("\n")):
        return "has a blank line"
    if "-->" in text:
        return "has an SRT timing arrow"
    return None


def verdict(salt: str, plant: dict, answer: dict) -> str:
    tokens = {review.digest(salt, t) for t in answer["text"].split()}
    if plant["original"] in tokens and plant["planted"] not in tokens:
        return "restored"
    if plant["planted"] in tokens:
        return "flagged" if "unsure" in answer["note"].lower() else "kept"
    return "changed"


def score(work: pathlib.Path, track: str, keys: pathlib.Path | None = None) -> dict:
    folder = work / "review" / track
    summary = json.loads((folder / "review.json").read_text(encoding="utf-8"))
    key = review.load_key(summary["run"], keys)
    chunks, answers = [], {}
    for entry in summary["chunks"]:
        got = _answers(folder / f"chunk_{entry['chunk']:02d}", entry["cues"])
        kept = key["chunks"][str(entry["chunk"])]
        if kept["attempt"] != entry["attempt"]:
            raise AssembleError(f"{track} chunk {entry['chunk']}: the key is for attempt {kept['attempt']}, "
                                f"the chunk is attempt {entry['attempt']}")
        by_hash = {review.digest(key["salt"], str(n)): got[n] for n in got}
        outcomes = [verdict(key["salt"], p, by_hash[p["cue"]]) for p in kept["plants"]]
        failed = sum(o != "restored" for o in outcomes)
        chunks.append({"chunk": entry["chunk"], "attempt": entry["attempt"], "controls": len(outcomes),
                       "failed": failed, "ok": failed == 0})
        answers.update(got)
    return {"track": track, "summary": summary, "chunks": chunks, "answers": answers,
            "ok": bool(chunks) and all(c["ok"] for c in chunks)}


def _clock(t: float) -> str:
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(path: pathlib.Path, cues: list[tuple[float, float, str]]) -> None:
    blocks = [f"{k}\n{_clock(a)} --> {_clock(b)}\n{text}\n" for k, (a, b, text) in enumerate(cues, 1)]
    path.write_text("\n".join(blocks), encoding="utf-8")


def assemble(work: pathlib.Path, *, keys: pathlib.Path | None = None) -> list[dict]:
    reports = []
    for path in sorted((work / "review").glob("*/review.json")):
        result = score(work, path.parent.name, keys)
        s = result["summary"]
        report = {"track": result["track"], "chunks": result["chunks"], "ok": result["ok"]}
        if result["ok"]:
            overrides_path = path.parent / "overrides.json"
            overrides = (json.loads(overrides_path.read_text(encoding="utf-8"))
                         if overrides_path.is_file() else {})
            ocr = json.loads((work / "ocr" / s["file"].removesuffix(".mkv") / f"s{s['stream']}.json")
                             .read_text(encoding="utf-8"))
            cues, empty = [], 0
            for c in ocr["cues"]:
                if c["n"] not in result["answers"]:
                    continue
                text = str(overrides.get(str(c["n"]), result["answers"][c["n"]]["text"])).strip()
                if text:
                    cues.append((c["start"], c["end"], text))
                else:
                    empty += 1
            out = work / "subs" / f"{result['track']}.{s['language']}.srt"
            out.parent.mkdir(exist_ok=True)
            write_srt(out, cues)
            report.update(srt=str(out.relative_to(work)), cues=len(cues), empty=empty,
                          overrides=len(overrides), not_decoded=s["not_decoded"])
        reports.append(report)
    if not reports:
        raise AssembleError(f"nothing to assemble: run review-prep first (no {work / 'review'}/*/review.json)")
    (work / "review" / "assemble.json").write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
    return reports
