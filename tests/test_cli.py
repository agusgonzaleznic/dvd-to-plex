import contextlib
import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from dvdtoplex import __version__
from dvdtoplex import cli as cli_module
from dvdtoplex.cli import _names, main
from disc import private_cache

private_cache()


class Cli(unittest.TestCase):
    def test_version(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as exit_:
            main(["--version"])
        self.assertEqual(exit_.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f"dvd-to-plex {__version__}")

    def test_files_take_names_with_or_without_mkv(self):
        self.assertEqual(_names(" title_01.mkv, title_02 ,,TITLE_03.MKV"), ["title_01", "title_02", "TITLE_03"])
        self.assertEqual(_names(None), [])

    def test_no_arguments_prints_help(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main([]), 0)
        self.assertIn("usage: dvd-to-plex", out.getvalue())

    def test_sample_writes_a_disc_and_refuses_to_overwrite_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "disc"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["sample", str(dest)]), 0)
            self.assertTrue((dest / "VIDEO_TS" / "VIDEO_TS.IFO").is_file())
            truth = json.loads((dest / "sample-truth.json").read_text(encoding="utf-8"))
            self.assertEqual(len(truth["titles"]), 3)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(main(["sample", str(dest)]), 1)
            self.assertIn("already exists", err.getvalue())

    def test_sample_refuses_an_insecure_helper_cache_in_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = pathlib.Path(tmp) / "cache"
            cache.mkdir(mode=0o700)
            cache.chmod(0o777)
            err = io.StringIO()
            with mock.patch.dict(os.environ, {"DVDTOPLEX_CACHE": str(cache)}), \
                    contextlib.redirect_stderr(err):
                code = main(["sample", str(pathlib.Path(tmp) / "disc")])
            self.assertEqual(code, 1)
            self.assertEqual(len(err.getvalue().strip().splitlines()), 1, err.getvalue())
            self.assertIn("writable by group or other", err.getvalue())
            self.assertEqual(list(cache.iterdir()), [], "nothing was built into it")

    def test_a_tool_that_times_out_is_a_one_line_error_in_every_handler(self):
        timeout = subprocess.TimeoutExpired(["ffmpeg", "-i", "x"], 7200)
        with tempfile.TemporaryDirectory() as tmp:
            work = pathlib.Path(tmp)
            (work / "out").mkdir()
            (work / "out" / "title_01.mkv").write_bytes(b"")
            (work / "names.json").write_text("{}")
            for name, target, args in (
                    ("inspect", "survey.survey", ["inspect", tmp]),
                    ("copy", "copier.copy", ["copy", tmp, str(work / "copy")]),
                    ("remux", "survey.survey", ["remux", tmp]),
                    ("ocr", "ocr.ocr_file", ["ocr", tmp]),
                    ("name", "namer.place", ["name", tmp, tmp])):
                err = io.StringIO()
                with self.subTest(command=name), \
                        mock.patch.object(*self.owner(target), side_effect=timeout), \
                        mock.patch("dvdtoplex.cli.source.opened", return_value=contextlib.nullcontext(work)), \
                        contextlib.redirect_stderr(err):
                    self.assertEqual(main(args), 1)
                    self.assertEqual(len(err.getvalue().strip().splitlines()), 1, err.getvalue())
                    self.assertIn("timed out after 7200", err.getvalue())

    @staticmethod
    def owner(dotted: str):
        module, attr = dotted.split(".")
        return getattr(cli_module, module), attr

    def test_the_suite_never_points_at_the_real_cache(self):
        cache = pathlib.Path(os.environ["DVDTOPLEX_CACHE"])
        self.assertEqual(cache.parent, pathlib.Path(tempfile.gettempdir()))
        self.assertNotEqual(cache, pathlib.Path.home() / "Library" / "Caches" / "dvd-to-plex" / "bin")


if __name__ == "__main__":
    unittest.main()
