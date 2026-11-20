"""ocr on the sample disc's remuxed titles: the subtitle decoder against ffmpeg's, the times and
the text against what was authored, and the controls that show each check can fail."""
import contextlib
import dataclasses
import difflib
import io
import json
import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from disc import remuxed_work, sample_disc
from dvdtoplex import ocr, spu, swiftc
from dvdtoplex.cli import main
from disc import private_cache

private_cache()

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
        binary = swiftc.build("read_text")
        for _, scale, correction in ocr.READINGS:
            self.assertEqual(ocr.read(binary, "de", scale, correction, [image]), [""])

    def test_a_language_vision_lacks_is_refused(self):
        code, _, err = cli("ocr", self.work, "--files", "title_02.mkv", "--lang", "xx")   # .mkv accepted
        self.assertEqual(code, 1)
        self.assertIn("Vision reads no language for xx", err)

    def test_an_unknown_file_is_refused(self):
        code, _, err = cli("ocr", self.work, "--files", "title_09")
        self.assertEqual(code, 1)
        self.assertIn("not in", err)


def one_line(text: str) -> bool:
    return len(text.strip().splitlines()) == 1 and "Traceback" not in text


class Failures(unittest.TestCase):
    """What ocr does when ffmpeg or the Vision helper misbehaves: one line, exit 1, no traceback."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)
        self.work = self.tmp / "work"
        (self.work / "out").mkdir(parents=True)
        self.mkv = self.work / "out" / "title_01.mkv"
        shutil.copy(remuxed_work() / "out" / "title_01.mkv", self.mkv)

    def failing_ffmpeg(self, stderr: bytes):
        real = subprocess.run

        def run(args, *a, **kw):
            if args[0] == "ffmpeg":
                if kw.get("check"):             # as subprocess.run itself does
                    raise subprocess.CalledProcessError(7, args, b"", stderr)
                return subprocess.CompletedProcess(args, 7, b"", stderr)
            return real(args, *a, **kw)

        return mock.patch.object(ocr.subprocess, "run", run)

    def test_a_failing_ffmpeg_is_a_clean_refusal_with_its_exit_status(self):
        with self.failing_ffmpeg(b"first\nboom: no such stream\n"), \
                mock.patch.object(ocr.swiftc, "build", return_value=pathlib.Path("/nonexistent")):
            code, out, err = cli("ocr", self.work, "--files", "title_01")
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(one_line(err), err)
        self.assertIn("ffmpeg failed (7)", err)
        self.assertIn("title_01.mkv", err)
        self.assertIn("boom: no such stream", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse((self.work / "ocr" / "title_01.json").exists())

    def test_ffmpegs_output_is_cut_down(self):
        with self.failing_ffmpeg(b"x" * 100_000), \
                mock.patch.object(ocr.swiftc, "build", return_value=pathlib.Path("/nonexistent")):
            code, _, err = cli("ocr", self.work, "--files", "title_01")
        self.assertEqual(code, 1)
        self.assertLess(len(err), 600)

    def test_control_the_same_run_without_the_failure_reads_the_track(self):
        code, out, err = cli("ocr", self.work, "--files", "title_01")
        self.assertEqual((code, err), (0, ""), err)
        self.assertIn("title_01 s0 (de): 4 cues read", out)

    def test_a_packet_without_a_time_is_an_ocr_error(self):
        real = ocr.media.run

        def run(args, **kw):
            out = real(args, **kw)
            if "packet=pts_time,size" not in args:
                return out
            found = json.loads(out)
            for row in found["packets"]:
                del row["pts_time"]
            return json.dumps(found)

        self.assertTrue(ocr.units(self.mkv, 0), "control: the unaltered track reads")
        with mock.patch.object(ocr.media, "run", run), self.assertRaises(ocr.OcrError) as e:
            ocr.units(self.mkv, 0)
        self.assertIn("no usable size or time", str(e.exception))

    def test_output_the_helper_cannot_be_read_from_is_an_ocr_error(self):
        helper = self.tmp / "helper"
        helper.write_text("#!/bin/sh\necho '{\"unexpected\": 1}'\n")
        helper.chmod(0o700)
        with self.assertRaises(ocr.OcrError):
            ocr.read(helper, "de", "1", "on", [self.tmp / "a.png"])


class Cache(unittest.TestCase):
    """The helper cache: a private per-user folder, checked before anything in it is run."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.cache = self.root / "cache"
        self.compiles: list[list[str]] = []

    def build(self, dest=None, *, fail=False, env=False):
        """swiftc.build with swiftc replaced by a fake that writes its output file; ENV: no folder given."""
        def run(args, **kw):
            self.compiles.append(args)
            if fail:
                raise subprocess.TimeoutExpired(args, 1)
            pathlib.Path(args[-1]).write_bytes(b"#!/bin/sh\n")
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch.object(swiftc.subprocess, "run", run), \
                mock.patch.object(swiftc.shutil, "which", return_value="/usr/bin/swiftc"):
            return swiftc.build("read_text") if env else swiftc.build("read_text", dest or self.cache)

    def refused(self, dest=None, *, words: str):
        with self.assertRaises(swiftc.SwiftError) as e:
            self.build(dest)
        self.assertTrue(one_line(str(e.exception)), str(e.exception))
        self.assertIn(words, str(e.exception))

    def test_missing_folders_are_created_private_and_the_binary_lands_whole(self):
        dest = self.root / "a" / "b"
        old = os.umask(0)
        try:
            binary = self.build(dest)
        finally:
            os.umask(old)
        for folder in (dest, dest.parent):
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700, folder)
        self.assertEqual(stat.S_IMODE(binary.stat().st_mode), 0o700)
        self.assertEqual([p.name for p in dest.iterdir()], [binary.name], "no partial file left")
        self.assertTrue(self.compiles[0][-1].endswith(".partial"), "built under another name, then moved")

    def test_control_a_correct_private_cache_is_used_without_rebuilding(self):
        first = self.build()
        again = self.build()        # swiftc is called again only if the cache is not used
        self.assertEqual((first, again, len(self.compiles)), (first, first, 1))
        first.chmod(0o755)          # readable and runnable by others is fine; writable is not
        self.assertEqual(self.build(), first)
        self.assertEqual(len(self.compiles), 1)

    def test_an_interrupted_build_leaves_nothing_under_any_name(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.build(fail=True)
        self.assertEqual(list(self.cache.iterdir()), [])
        done = self.build()
        self.assertTrue(done.is_file())

    def test_a_cached_binary_that_is_a_symlink_is_refused(self):
        binary = self.build()
        binary.unlink()
        target = self.root / "evil"
        target.write_text("#!/bin/sh\n")
        target.chmod(0o700)
        binary.symlink_to(target)
        self.refused(words="symlink")
        binary.unlink()
        binary.symlink_to(self.root / "nowhere")        # a dangling link too
        self.refused(words="symlink")
        self.assertEqual(len(self.compiles), 1, "nothing was rebuilt over it")

    def test_a_cached_binary_writable_by_group_or_other_is_refused(self):
        binary = self.build()
        for mode in (0o720, 0o702, 0o777):
            binary.chmod(mode)
            with self.subTest(mode=oct(mode)):
                self.refused(words="writable by group or other")

    def test_a_cached_binary_owned_by_someone_else_is_refused(self):
        self.build()
        # the folder would be refused first; skip its check so the file's own check is reached
        with mock.patch.object(swiftc, "_private_folder"), \
                mock.patch.object(swiftc.os, "getuid", return_value=os.getuid() + 1):
            self.refused(words="helper cache file")
        with mock.patch.object(swiftc, "_private_folder"), \
                mock.patch.object(swiftc.os, "getuid", return_value=os.getuid() + 1):
            self.refused(words="not owned by you")

    def test_a_cached_binary_that_is_not_a_file_is_refused(self):
        binary = self.build()
        binary.unlink()
        binary.mkdir(mode=0o700)
        self.refused(words="not a file")

    def test_a_cache_folder_that_is_a_symlink_is_refused(self):
        self.build()
        link = self.root / "link"
        link.symlink_to(self.cache)
        self.refused(link, words="symlink")

    def test_a_cache_folder_writable_by_group_or_other_is_refused(self):
        self.build()
        for mode in (0o770, 0o707, 0o777):
            self.cache.chmod(mode)
            with self.subTest(mode=oct(mode)):
                self.refused(words="writable by group or other")
        self.cache.chmod(0o700)

    def test_a_cache_folder_owned_by_someone_else_or_a_file_is_refused(self):
        self.build()
        with mock.patch.object(swiftc.os, "getuid", return_value=os.getuid() + 1):
            self.refused(words="not owned by you")
        plain = self.root / "plain"
        plain.write_text("x")
        self.refused(plain, words="not a folder")

    def test_the_cache_is_the_env_folder_or_the_private_home_default(self):
        with mock.patch.dict(os.environ, {"DVDTOPLEX_CACHE": str(self.cache)}):
            self.assertEqual(swiftc.cache_dir(), self.cache)
        env = {k: v for k, v in os.environ.items() if k != "DVDTOPLEX_CACHE"} | {"HOME": str(self.root)}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(swiftc.cache_dir(), self.root / "Library" / "Caches" / "dvd-to-plex" / "bin")

    def test_build_without_a_folder_uses_the_env_folder(self):
        with mock.patch.dict(os.environ, {"DVDTOPLEX_CACHE": str(self.cache)}):
            self.assertEqual(self.build(env=True).parent, self.cache)

    def test_the_ocr_command_refuses_an_insecure_cache_in_one_line(self):
        work = self.root / "work"
        (work / "out").mkdir(parents=True)
        shutil.copy(remuxed_work() / "out" / "title_01.mkv", work / "out" / "title_01.mkv")
        self.cache.mkdir(mode=0o700)
        self.cache.chmod(0o777)
        with mock.patch.dict(os.environ, {"DVDTOPLEX_CACHE": str(self.cache)}):
            code, out, err = cli("ocr", work, "--files", "title_01")
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(one_line(err), err)
        self.assertIn("writable by group or other", err)
        self.assertEqual(list(self.cache.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
