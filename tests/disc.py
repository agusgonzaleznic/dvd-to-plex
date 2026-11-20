"""The synthetic sample disc, built once per test run and shared by every test module."""
import atexit
import collections
import contextlib
import io
import os
import pathlib
import shutil
import subprocess
import tempfile

from dvdtoplex import sample
from dvdtoplex.cli import main

_built: tuple[pathlib.Path, dict] | None = None
_remuxed: pathlib.Path | None = None
_ocred: pathlib.Path | None = None
_cache: tempfile.TemporaryDirectory | None = None


def private_cache() -> pathlib.Path:
    """Point DVDTOPLEX_CACHE at a private temp folder for this whole test run, so no test builds a
    Swift helper into the real ~/Library/Caches. Every test module that can reach swiftc.build
    calls this when it is imported, and this module does too, so every module that builds the sample
    disc is covered whatever order the modules load in."""
    global _cache
    if _cache is None:
        _cache = tempfile.TemporaryDirectory(prefix="dvdtoplex-cache-")      # mkdtemp: mode 0700
        atexit.register(_cache.cleanup)
    os.environ["DVDTOPLEX_CACHE"] = _cache.name
    return pathlib.Path(_cache.name)


private_cache()


def sample_disc() -> tuple[pathlib.Path, dict]:
    """(VIDEO_TS folder, truth). A missing tool raises, so the tests fail rather than skip."""
    global _built
    if _built is None:
        tmp = tempfile.TemporaryDirectory(prefix="dvdtoplex-test-")
        atexit.register(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        truth = sample.build(root)
        _built = (root / "VIDEO_TS", truth)
    return _built


def remuxed_work() -> pathlib.Path:
    """A work folder holding the sample disc's copy and its three titles remuxed, built once per
    run. Tests copy what they change out of it."""
    global _remuxed
    if _remuxed is None:
        video_ts, _ = sample_disc()
        tmp = tempfile.TemporaryDirectory(prefix="dvdtoplex-remuxed-")
        atexit.register(tmp.cleanup)
        work = pathlib.Path(tmp.name) / "work"
        for args in (["copy", video_ts, work], ["remux", work, "--titles", "1,2,3"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = main([str(a) for a in args])
            if code != 0:
                raise RuntimeError(f"{args[0]} failed on the sample:\n{out.getvalue()}")
        _remuxed = work
    return _remuxed


def ocr_work() -> pathlib.Path:
    """A work folder holding the remuxed sample's out/ and its ocr/, built once per run."""
    global _ocred
    if _ocred is None:
        tmp = tempfile.TemporaryDirectory(prefix="dvdtoplex-ocr-")
        atexit.register(tmp.cleanup)
        work = pathlib.Path(tmp.name) / "work"
        shutil.copytree(remuxed_work() / "out", work / "out")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            if main(["ocr", str(work)]) != 0:
                raise RuntimeError(f"ocr failed on the sample:\n{out.getvalue()}")
        _ocred = work
    return _ocred


def rendered_colours(ifo_file: pathlib.Path, vob: pathlib.Path, seconds: float) -> collections.Counter:
    """Pixel colours (hex) of a title's subtitle stream 0 as rendered by ffmpeg's dvdsub decoder
    from the IFO's palette, over a red background, sampled twice a second."""
    w, h = sample.WIDTH, sample.HEIGHT
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-ifo_palette", str(ifo_file), "-i", str(vob),
         "-f", "lavfi", "-i", f"color=c=red:s={w}x{h}:r=25:d={seconds + 1}",
         "-filter_complex", "[1:v][0:s:0]overlay=eof_action=pass:format=rgb,fps=2[o]", "-map", "[o]",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True, timeout=300).stdout
    frame = w * h * 3
    seen: collections.Counter = collections.Counter()
    for k in range(len(raw) // frame):
        f = raw[k * frame:(k + 1) * frame]
        seen.update(f[i:i + 3].hex() for i in range(0, frame, 3))
    return seen
