"""Prepare OCR text for proofreading, with planted controls only the images can expose.

Each track's cues are cut into chunks. In every chunk a few cues where both Vision readings agree
get one planted error in their draft: a changed word, a dropped leading quote, or `l` for `L`
(two misreads Vision makes). Those cues show no second reading, so a plant can only be found by
comparing the text with the image. The proofreader answers every cue of its chunk from the
images; assemble then scores each plant, and a chunk with any plant not restored fails as a whole.

The key (which cues hold plants, and the original and planted words) is kept as salted hashes in
a key folder outside the work folder, so an agent that reads its chunk cannot read the answers.

Layout: WORK/review/<file>.s<N>/review.json and chunk_NN/{cues.json, sheet_NN.png}; the
proofreader writes chunk_NN/answers.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import secrets
import shutil
import uuid

from dvdtoplex import sheets

CHUNK = 50
PLANTS = 3
ROWS = 10                       # cues per contact sheet
QUOTES = "'\"‚„»«‹›"
WORD = re.compile(r"[^\W\d_]{4,}")
VOWELS = "aeiou"


class ReviewError(RuntimeError):
    """Nothing to review, a chunk cannot hold a control, or the key is missing."""


def key_dir() -> pathlib.Path:
    if env := os.environ.get("DVDTOPLEX_KEYS"):
        return pathlib.Path(env)
    return pathlib.Path.home() / "Library" / "Application Support" / "dvd-to-plex" / "keys"


def digest(salt: str, text: str) -> str:
    return hashlib.sha256(f"{salt}\0{text}".encode()).hexdigest()


def _change(kind: str, old: str, rng) -> str | None:
    if kind == "quote":
        return old[1:]
    if kind == "l":
        at = rng.choice([i for i, ch in enumerate(old) if ch == "L"])
        return old[:at] + "l" + old[at + 1:]
    m = WORD.search(old)
    word = m.group()
    vowels = [i for i in range(1, len(word) - 1) if word[i].lower() in VOWELS]
    if vowels:
        i = rng.choice(vowels)
        swap = rng.choice([v for v in VOWELS if v != word[i].lower()])
        changed = word[:i] + (swap.upper() if word[i].isupper() else swap) + word[i + 1:]
    else:
        pairs = [i for i in range(1, len(word) - 2) if word[i] != word[i + 1]]
        if not pairs:
            return None
        i = rng.choice(pairs)
        changed = word[:i] + word[i + 1] + word[i] + word[i + 2:]
    return old[:m.start()] + changed + old[m.end():]


def plant(text: str, rng) -> tuple[str, str, str, str] | None:
    """(planted text, original token, planted token, kind) for one error in TEXT, or None when no
    token can take one. Tokens are whitespace-separated words with their punctuation; only a
    token that occurs once in the cue is changed, so scoring can tell it apart. The whitespace
    around it, line breaks included, is kept, so a planted cue keeps the shape of its image."""
    spans = [m.span() for m in re.finditer(r"\S+", text)]
    tokens = [text[a:b] for a, b in spans]
    once = [k for k, t in enumerate(tokens) if tokens.count(t) == 1]
    choices = [("quote", 0)] if 0 in once and tokens[0][0] in QUOTES and len(tokens[0]) > 1 else []
    choices += [("l", k) for k in once if "L" in tokens[k]]
    choices += [("word", k) for k in once if WORD.search(tokens[k])]
    rng.shuffle(choices)
    for kind, k in choices:
        old = tokens[k]
        new = _change(kind, old, rng)
        if new and new != old and new not in tokens:
            a, b = spans[k]
            return text[:a] + new + text[b:], old, new, kind
    return None


def _write_chunk(folder: pathlib.Path, ocr_dir: pathlib.Path, number: int, cues: list[dict],
                 key: dict, attempt: int, plants: int, language: str) -> int:
    rng = secrets.SystemRandom()
    chunk = folder / f"chunk_{number:02d}"
    if chunk.exists():
        if (chunk / "answers.json").exists():
            (chunk / "answers.json").rename(chunk / f"answers.attempt{attempt - 1}.json")
        for old in [*chunk.glob("sheet_*.png"), chunk / "cues.json"]:
            old.unlink(missing_ok=True)
    chunk.mkdir(parents=True, exist_ok=True)
    eligible = [c for c in cues if c["agree"]]
    rng.shuffle(eligible)
    placed: dict[int, tuple] = {}
    for c in eligible:
        if len(placed) == plants:
            break
        if made := plant(c["first"], rng):
            placed[c["n"]] = made
    if not placed:
        raise ReviewError(f"{folder.name} chunk {number}: no cue where both readings agree can hold a control")
    rows = []
    for c in cues:
        row = {"n": c["n"], "draft": placed[c["n"]][0] if c["n"] in placed else c["first"]}
        if not c["agree"]:
            row["second_reading"] = c["second"]
        rows.append(row)
    names = []
    for s in range(0, len(cues), ROWS):
        name = f"sheet_{s // ROWS + 1:02d}.png"
        sheets.write_sheet(chunk / name, [(c["n"], ocr_dir / c["image"]) for c in cues[s:s + ROWS]])
        names.append(name)
    (chunk / "cues.json").write_text(json.dumps(
        {"chunk": number, "attempt": attempt, "language": language, "sheets": names, "cues": rows},
        indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    salt = key["salt"]
    key["chunks"][str(number)] = {"attempt": attempt, "plants": [
        {"cue": digest(salt, str(n)), "original": digest(salt, old), "planted": digest(salt, new), "kind": kind}
        for n, (_, old, new, kind) in placed.items()]}
    return len(placed)


def _split(cues: list[dict], size: int) -> list[list[dict]]:
    """Chunks of about SIZE cues; one with no cue where the readings agree joins its neighbour."""
    chunks = [cues[k:k + size] for k in range(0, len(cues), size)]
    out: list[list[dict]] = []
    for c in chunks:
        if out and not any(x["agree"] for x in out[-1]):
            out[-1] += c
        elif out and not any(x["agree"] for x in c):
            out[-1] += c
        else:
            out.append(c)
    return out


def _save_key(key: dict, keys: pathlib.Path) -> None:
    keys.mkdir(parents=True, exist_ok=True)
    path = keys / f"{key['run']}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(key, f, indent=2)


def load_key(run: str, keys: pathlib.Path | None = None) -> dict:
    path = (keys or key_dir()) / f"{run}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ReviewError(f"the key for this review is not at {path} ({e})") from e


def prepare(work: pathlib.Path, *, size: int = CHUNK, plants: int = PLANTS,
            keys: pathlib.Path | None = None) -> list[dict]:
    keys = keys or key_dir()
    if size < 1 or plants < 1:
        raise ReviewError("the chunk size and the controls per chunk must each be at least 1")
    if keys.resolve().is_relative_to(work.resolve()):
        raise ReviewError("the key folder must be outside the work folder")
    tracks = sorted((work / "ocr").glob("*/s*.json"))
    if not tracks:
        raise ReviewError(f"nothing to review: run ocr first (no {work / 'ocr'}/*/s*.json)")
    done = []
    for path in tracks:
        ocr = json.loads(path.read_text(encoding="utf-8"))
        track = f"{path.parent.name}.{path.stem}"
        folder = work / "review" / track
        if (folder / "review.json").exists():
            raise ReviewError(f"{track} is prepared already; redo one chunk with --redo {track}/N")
        cues = [c for c in ocr["cues"] if "first" in c]
        if not any(c["agree"] for c in cues):
            raise ReviewError(f"{track}: no cue where both readings agree, so no control can be planted")
        key = {"run": uuid.uuid4().hex, "salt": secrets.token_hex(16), "track": track, "chunks": {}}
        chunks = _split(cues, size)
        planted = [_write_chunk(folder, path.parent, k, c, key, 1, plants, ocr["language"])
                   for k, c in enumerate(chunks, 1)]
        _save_key(key, keys)
        summary = {"track": track, "file": ocr["file"], "stream": ocr["track"], "language": ocr["language"],
                   "run": key["run"], "not_decoded": len(ocr["cues"]) - len(cues),
                   "chunks": [{"chunk": k, "cues": [c["n"] for c in ch], "attempt": 1, "plants": p}
                              for k, (ch, p) in enumerate(zip(chunks, planted), 1)]}
        (folder / "review.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        done.append(summary)
    return done


def redo(work: pathlib.Path, target: str, *, plants: int = PLANTS, keys: pathlib.Path | None = None) -> dict:
    """Fresh plants for one chunk (TRACK/N), its earlier answers kept beside it."""
    keys = keys or key_dir()
    if plants < 1:
        raise ReviewError("the controls per chunk must be at least 1")
    track, _, number = target.partition("/")
    folder = work / "review" / track
    try:
        summary = json.loads((folder / "review.json").read_text(encoding="utf-8"))
        entry = next(c for c in summary["chunks"] if str(c["chunk"]) == number)
    except (OSError, ValueError, StopIteration) as e:
        raise ReviewError(f"no prepared chunk {target}; give TRACK/N as review-prep printed it") from e
    key = load_key(summary["run"], keys)
    ocr_path = work / "ocr" / summary["file"].removesuffix(".mkv") / f"s{summary['stream']}.json"
    by_n = {c["n"]: c for c in json.loads(ocr_path.read_text(encoding="utf-8"))["cues"]}
    entry["attempt"] += 1
    entry["plants"] = _write_chunk(folder, ocr_path.parent, entry["chunk"], [by_n[n] for n in entry["cues"]],
                                   key, entry["attempt"], plants, summary["language"])
    _save_key(key, keys)
    (folder / "review.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return entry


def clean(work: pathlib.Path) -> None:
    """Remove the review folder (for tests and a fresh start); keys are left alone."""
    shutil.rmtree(work / "review", ignore_errors=True)
