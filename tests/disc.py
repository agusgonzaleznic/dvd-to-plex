"""The synthetic sample disc, built once per test run and shared by every test module."""
import atexit
import collections
import contextlib
import io
import pathlib
import subprocess
import tempfile

from dvdtoplex import sample
from dvdtoplex.cli import main

_built: tuple[pathlib.Path, dict] | None = None
_remuxed: pathlib.Path | None = None


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
