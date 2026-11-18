"""What the macOS machine running the tests can do: the facts later stages and their tests rely on.

Each test needs a tool on PATH (swift, say, ffmpeg, ffprobe, dvdauthor) and fails, never skips,
when it is missing, so a CI runner without it shows red instead of a quiet pass.
"""
import difflib
import pathlib
import shutil
import subprocess
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SWIFT_OCR = HERE / "swift" / "render_and_read.swift"


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


def need(tool):
    path = shutil.which(tool)
    if path is None:
        raise AssertionError(f"{tool} is not on PATH")
    return path


class VisionOcr(unittest.TestCase):
    """macOS Vision reads German text the way the subtitle stage will use it."""

    def read(self, text):
        need("swift")
        return run("swift", str(SWIFT_OCR), text, "de-DE", timeout=600).stdout.strip()

    def test_reads_rendered_german(self):
        text = "Größere Straße, über die Brücke!"
        got = self.read(text)
        ratio = difflib.SequenceMatcher(None, got, text).ratio()
        self.assertGreaterEqual(ratio, 0.9, f"read {got!r} for {text!r} (ratio {ratio:.2f})")

    def test_blank_image_reads_nothing(self):
        self.assertEqual(self.read(""), "")


class GermanSpeech(unittest.TestCase):
    """`say` can write German speech to a file, for the speech stage's fixtures."""

    def test_say_writes_german_audio(self):
        need("say")
        need("ffprobe")
        voices = [line.split()[0] for line in run("say", "-v", "?").stdout.splitlines() if " de_DE " in line]
        self.assertTrue(voices, "no de_DE voice installed")
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "speech.aiff"
            run("say", "-v", voices[0], "-o", str(out), "Guten Tag, wir fahren nach Hause.")
            seconds = float(run("ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(out)).stdout)
        self.assertGreater(seconds, 1.0)


class DvdAuthoring(unittest.TestCase):
    """dvdauthor builds a PAL disc from ffmpeg's test pattern: the base of the sample disc."""

    def test_builds_a_pal_title(self):
        need("ffmpeg")
        need("dvdauthor")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            mpg = tmp / "title.mpg"
            run("ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
                "-f", "lavfi", "-i", "testsrc=size=720x576:rate=25:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                "-target", "pal-dvd", "-aspect", "4:3", str(mpg))
            disc = tmp / "disc"
            env = {"PATH": shutil.os.environ["PATH"], "VIDEO_FORMAT": "PAL"}
            run("dvdauthor", "-o", str(disc), "-t", str(mpg), env=env)
            run("dvdauthor", "-o", str(disc), "-T", env=env)
            ts = disc / "VIDEO_TS"
            self.assertEqual((ts / "VTS_01_0.IFO").read_bytes()[:12], b"DVDVIDEO-VTS")
            self.assertEqual((ts / "VIDEO_TS.IFO").read_bytes()[:12], b"DVDVIDEO-VMG")
            pack = (ts / "VTS_01_1.VOB").read_bytes()[:4]
            self.assertEqual(pack, b"\x00\x00\x01\xba", "the VOB does not start with an MPEG-2 pack header")


if __name__ == "__main__":
    unittest.main()
