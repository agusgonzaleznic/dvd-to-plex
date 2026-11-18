"""Author a small synthetic DVD-Video folder, for the tests and for trying the tool without a disc.

Everything on it is made here: ffmpeg's test pattern and sine tones, newly written German lines that
the Swift helper renders to images, spumux to mux them in as subpictures, and dvdauthor to author
the disc. Next to VIDEO_TS it writes sample-truth.json, the facts each stage is checked against.

Layout:
  title 1 (title set 1): 12 s, four chapters, German audio plus a German commentary track,
                         German subtitles in white
  title 2 (title set 2): 5 s, English audio, German subtitles in yellow (a different palette)
  title 3 (title set 2): 3 s, English audio, no subtitles
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from importlib import resources
from xml.sax.saxutils import quoteattr

WIDTH, HEIGHT, FPS = 720, 576, 25

# (start, end, text) in seconds from the title's start.
MAIN_LINES = [
    (1.0, 2.6, "Guten Morgen, wie war die Reise?"),
    (3.4, 5.4, "Lang, aber die Aussicht war schön."),
    (6.5, 8.5, "Um neun fährt der letzte Bus über die Brücke."),
    (9.2, 11.4, "Dann müssen wir uns beeilen!"),
]
EXTRA_LINES = [
    (0.8, 2.6, "Hinter den Kulissen: Probe am Montag."),
    (3.0, 4.6, "Die Straße ist nass, bitte Ruhe."),
]

TITLES = [
    {"title": 1, "title_set": 1, "seconds": 12.0, "chapters": [0.0, 3.0, 6.0, 9.0],
     "audio": [{"lang": "de", "content": "normal", "tone": 440},
               {"lang": "de", "content": "commentary", "tone": 660}],
     "subs": [{"lang": "de", "colour": "ffffff", "lines": MAIN_LINES}]},
    {"title": 2, "title_set": 2, "seconds": 5.0, "chapters": [0.0],
     "audio": [{"lang": "en", "content": "normal", "tone": 550}],
     "subs": [{"lang": "de", "colour": "ffff00", "lines": EXTRA_LINES}]},
    {"title": 3, "title_set": 2, "seconds": 3.0, "chapters": [0.0],
     "audio": [{"lang": "en", "content": "normal", "tone": 330}],
     "subs": []},
]

TOOLS = ("ffmpeg", "spumux", "dvdauthor", "swiftc")
DVDAUTHOR_CONTENT = {"normal": "normal", "commentary": "comments1"}


class SampleError(RuntimeError):
    """A tool the sample needs is missing, or one of them failed."""


def missing_tools() -> list[str]:
    return [t for t in TOOLS if shutil.which(t) is None]


def _run(cmd: list[str], *, stdin: pathlib.Path | None = None, stdout: pathlib.Path | None = None,
         env: dict[str, str] | None = None) -> None:
    fin = open(stdin, "rb") if stdin else subprocess.DEVNULL
    fout = open(stdout, "wb") if stdout else subprocess.PIPE
    try:
        p = subprocess.run(cmd, stdin=fin, stdout=fout, stderr=subprocess.PIPE, env=env)
    finally:
        for f in (fin, fout):
            if hasattr(f, "close"):
                f.close()
    if p.returncode != 0:
        tail = p.stderr.decode("utf-8", "replace").strip().splitlines()[-8:]
        raise SampleError(f"{pathlib.Path(cmd[0]).name} failed ({p.returncode}):\n" + "\n".join(tail))


def _clock(seconds: float) -> str:
    """spumux time: HH:MM:SS.hundredths."""
    cs = round(seconds * 100)
    return f"{cs // 360000:02d}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _renderer(work: pathlib.Path) -> pathlib.Path:
    binary = work / "render_text"
    source = resources.files("dvdtoplex") / "swift" / "render_text.swift"
    with resources.as_file(source) as path:
        _run(["swiftc", "-O", str(path), "-o", str(binary)])
    return binary


def _encode(title: dict, out: pathlib.Path) -> None:
    seconds = title["seconds"]
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc=size={WIDTH}x{HEIGHT}:rate={FPS}:duration={seconds}"]
    for a in title["audio"]:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={a['tone']}:sample_rate=48000:duration={seconds}"]
    cmd += ["-map", "0:v"]
    for k in range(len(title["audio"])):
        cmd += ["-map", f"{k + 1}:a"]
    cmd += ["-target", "pal-dvd", "-aspect", "4:3", "-sc_threshold", "1000000000",
            "-c:a", "ac3", "-b:a", "192k", "-ac", "2", str(out)]
    _run(cmd)


def _subtitle(title: dict, renderer: pathlib.Path, src: pathlib.Path, work: pathlib.Path) -> pathlib.Path:
    """Render the title's lines and mux them in as subpicture stream 0; returns the new MPEG."""
    sub = title["subs"][0]
    spus = []
    for n, (start, end, text) in enumerate(sub["lines"], 1):
        png = work / f"t{title['title']}_{n:02d}.png"
        _run([str(renderer), str(png), str(WIDTH), str(HEIGHT), text, sub["colour"]])
        spus.append(f'    <spu start="{_clock(start)}" end="{_clock(end)}" image={quoteattr(str(png))}/>')
    xml = work / f"t{title['title']}_subs.xml"
    xml.write_text('<subpictures format="PAL">\n  <stream>\n' + "\n".join(spus)
                   + "\n  </stream>\n</subpictures>\n", encoding="utf-8")
    out = work / f"t{title['title']}_subbed.mpg"
    _run(["spumux", "-m", "dvd", "-s", "0", str(xml)], stdin=src, stdout=out, env=_env())
    return out


def _env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/"),
            "VIDEO_FORMAT": "PAL"}


def _titleset_xml(titles: list[tuple[dict, pathlib.Path]]) -> str:
    first = titles[0][0]
    lines = ["  <titleset>", "    <titles>", '      <video format="pal" aspect="4:3"/>']
    for a in first["audio"]:
        lines.append(f'      <audio lang="{a["lang"]}" content="{DVDAUTHOR_CONTENT[a["content"]]}"/>')
    langs = next((t["subs"] for t, _ in titles if t["subs"]), [])
    for s in langs:
        lines.append(f'      <subpicture lang="{s["lang"]}"/>')
    for title, mpg in titles:
        marks = ",".join(f"{c:g}" for c in title["chapters"])
        lines += ["      <pgc>", f"        <vob file={quoteattr(str(mpg))} chapters=\"{marks}\"/>",
                  "      </pgc>"]
    return "\n".join(lines + ["    </titles>", "  </titleset>"])


def build(dest: pathlib.Path) -> dict:
    """Author the sample into DEST/VIDEO_TS and write DEST/sample-truth.json; returns the truth."""
    missing = missing_tools()
    if missing:
        raise SampleError("the sample needs these tools on PATH: " + ", ".join(missing))
    dest.mkdir(parents=True, exist_ok=True)
    if (dest / "VIDEO_TS").exists():
        raise SampleError(f"{dest / 'VIDEO_TS'} already exists; pick an empty folder")
    with tempfile.TemporaryDirectory(prefix="dvdtoplex-sample-") as tmp:
        work = pathlib.Path(tmp)
        renderer = _renderer(work)
        mpegs = {}
        for title in TITLES:
            mpg = work / f"t{title['title']}.mpg"
            _encode(title, mpg)
            mpegs[title["title"]] = _subtitle(title, renderer, mpg, work) if title["subs"] else mpg
        sets: dict[int, list] = {}
        for title in TITLES:
            sets.setdefault(title["title_set"], []).append((title, mpegs[title["title"]]))
        xml = work / "dvd.xml"
        xml.write_text(f"<dvdauthor dest={quoteattr(str(dest))}>\n  <vmgm/>\n"
                       + "\n".join(_titleset_xml(sets[n]) for n in sorted(sets)) + "\n</dvdauthor>\n",
                       encoding="utf-8")
        _run(["dvdauthor", "-x", str(xml)], env=_env())
    truth = {"standard": "PAL", "titles": [
        {**{k: v for k, v in t.items() if k != "subs"},
         "subs": [{"lang": s["lang"], "colour": s["colour"],
                   "lines": [{"start": a, "end": b, "text": x} for a, b, x in s["lines"]]}
                  for s in t["subs"]]}
        for t in TITLES]}
    (dest / "sample-truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2) + "\n",
                                            encoding="utf-8")
    return truth
