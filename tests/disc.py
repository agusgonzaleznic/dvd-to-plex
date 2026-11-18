"""The synthetic sample disc, built once per test run and shared by every test module."""
import atexit
import collections
import pathlib
import subprocess
import tempfile

from dvdtoplex import sample

_built: tuple[pathlib.Path, dict] | None = None


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
