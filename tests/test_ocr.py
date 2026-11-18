"""ocr on the sample disc's remuxed titles: the subtitle decoder against ffmpeg's, the times and
the text against what was authored, and the controls that show each check can fail."""
import contextlib
import dataclasses
import difflib
import io
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from disc import remuxed_work, sample_disc
from dvdtoplex import ocr, spu, swiftc
from dvdtoplex.cli import main

LINE_FLOOR = 0.9        # each line, each reading: similarity to the authored text
MEAN_FLOOR = 0.97       # over all lines and both readings


def cli(*args) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in args])
    return code, out.getvalue(), err.getvalue()


def track_palette(mkv: pathlib.Path) -> list[tuple[int, int, int]]:
    """The palette line of the VobSub track's codec private data, read through ffprobe's hex dump."""
    dump = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "s:0", "-show_streams",
                                      "-show_data", "-of", "json", str(mkv)], capture_output=True, text=True,
                                     check=True).stdout)["streams"][0]["extradata"]
    text = bytes.fromhex("".join("".join(line[10:49].split()) for line in dump.splitlines())).decode()
    line = next(x for x in text.splitlines() if x.startswith("palette:"))
    return [tuple(bytes.fromhex(h.strip())) for h in line.split(":", 1)[1].split(",")]


def ffmpeg_frames(mkv: pathlib.Path, seconds: float) -> list[bytes]:
    """RGB frames, twice a second, of ffmpeg's dvdsub decoder drawing track 0 over red."""
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(mkv), "-f", "lavfi",
         "-i", f"color=c=red:s=720x576:r=25:d={seconds + 1}", "-filter_complex",
         "[1:v][0:s:0]overlay=eof_action=pass:format=rgb,fps=2[o]", "-map", "[o]",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True, timeout=300).stdout
    n = 720 * 576 * 3
    return [raw[k * n:(k + 1) * n] for k in range(len(raw) // n)]


def drawn(unit: spu.Spu, palette) -> bytes:
    """The unit drawn the way ffmpeg overlays it: opaque pixels in their palette colour over red."""
    frame = bytearray(b"\xfe\x00\x00" * (720 * 576))
    for y in range(unit.height):
        for x in range(unit.width):
            v = unit.pixels[y * unit.width + x]
            if unit.alphas[v]:
                i = ((unit.y + y) * 720 + unit.x + x) * 3
                frame[i:i + 3] = bytes(palette[unit.colours[v]])
    return bytes(frame)


class Decoder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = remuxed_work()
        cls.truth = sample_disc()[1]

    def test_every_unit_draws_what_ffmpeg_draws(self):
        for title in ("title_01", "title_02"):
            mkv = self.work / "out" / f"{title}.mkv"
            palette, frames = track_palette(mkv), ffmpeg_frames(mkv, 12)
            for start, data in ocr.units(mkv, 0):
                unit = spu.parse(data)
                ffmpeg = frames[round((start + 0.5) * 2)]
                with self.subTest(title=title, start=start):
                    self.assertEqual(drawn(unit, palette), ffmpeg)
                    w = unit.width
                    rows = [unit.pixels[k * w:(k + 1) * w] for k in range(unit.height)]
                    swapped = b"".join(rows[k + 1 if k % 2 == 0 else k - 1] for k in range(len(rows) - 1)) + rows[-1]
                    self.assertNotEqual(drawn(dataclasses.replace(unit, pixels=swapped), palette), ffmpeg,
                                        "control: the two fields swapped must not match")

    def test_times_are_the_authored_ones(self):
        for t in self.truth["titles"]:
            if not t["subs"]:
                continue
            got = [(start + u.start, start + u.stop) for start, d in
                   ocr.units(self.work / "out" / f"title_{t['title']:02d}.mkv", 0) for u in [spu.parse(d)]]
            want = [(line["start"], line["end"]) for line in t["subs"][0]["lines"]]
            self.assertEqual(len(got), len(want))
            for (a, b), (c, d) in zip(got, want):
                self.assertAlmostEqual(a, c, delta=0.001)
                self.assertAlmostEqual(b, d, delta=spu.TICK)       # stop times count in 1024-tick steps

    def test_a_truncated_unit_is_an_error(self):
        _, data = ocr.units(self.work / "out" / "title_01.mkv", 0)[0]
        for cut in (b"", data[:3], data[:40], data[:len(data) // 2]):
            with self.subTest(length=len(cut)), self.assertRaises(spu.SpuError):
                spu.parse(cut)


class Reading(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.work = pathlib.Path(tmp.name) / "work"
        shutil.copytree(remuxed_work() / "out", cls.work / "out")
        cls.code, cls.out, cls.err = cli("ocr", cls.work)
        cls.truth = sample_disc()[1]

    def readings(self) -> list[tuple[str, str, str]]:
        """(authored, first reading, second reading) for every subtitle line on the disc."""
        rows = []
        for t in self.truth["titles"]:
            if t["subs"]:
                got = json.loads((self.work / "ocr" / f"title_{t['title']:02d}" / "s0.json").read_text(encoding="utf-8"))
                rows += [(line["text"], c["first"], c["second"]) for line, c in zip(t["subs"][0]["lines"], got["cues"])]
        return rows

    def test_command_reads_every_track(self):
        self.assertEqual((self.code, self.err), (0, ""), self.err)
        self.assertIn("title_01 s0 (de): 4 cues read", self.out)
        self.assertIn("title_02 s0 (de): 2 cues read", self.out)
        self.assertIn("title_03: no disc subtitles", self.out)
        got = json.loads((self.work / "ocr" / "title_01" / "s0.json").read_text(encoding="utf-8"))
        self.assertEqual([c["n"] for c in got["cues"]], [1, 2, 3, 4])
        for c in got["cues"]:
            self.assertEqual((self.work / "ocr" / "title_01" / c["image"]).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_accuracy_floor(self):
        rows = self.readings()
        self.assertEqual(len(rows), 6)
        scores = [difflib.SequenceMatcher(None, got, want).ratio()
                  for want, *readings in rows for got in readings]
        self.assertGreaterEqual(min(scores), LINE_FLOOR, rows)
        self.assertGreaterEqual(sum(scores) / len(scores), MEAN_FLOOR, rows)
        shifted = [difflib.SequenceMatcher(None, rows[k - 1][1], rows[k][0]).ratio() for k in range(len(rows))]
        self.assertLess(sum(shifted) / len(shifted), MEAN_FLOOR, "control: readings against the wrong lines")

    def test_a_blank_image_reads_nothing(self):
        image = self.work / "blank.png"
        spu.write_png(image, 400, 80, b"\xff" * (400 * 80))
        binary = swiftc.build("read_text", self.work / "ocr" / "bin")
        for _, scale, correction in ocr.READINGS:
            self.assertEqual(ocr.read(binary, "de", scale, correction, [image]), [""])

    def test_a_language_vision_lacks_is_refused(self):
        code, _, err = cli("ocr", self.work, "--files", "title_02", "--lang", "xx")
        self.assertEqual(code, 1)
        self.assertIn("Vision reads no language for xx", err)

    def test_an_unknown_file_is_refused(self):
        code, _, err = cli("ocr", self.work, "--files", "title_09")
        self.assertEqual(code, 1)
        self.assertIn("not in", err)


if __name__ == "__main__":
    unittest.main()
