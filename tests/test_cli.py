import contextlib
import io
import json
import pathlib
import tempfile
import unittest

from dvdtoplex import __version__
from dvdtoplex.cli import main


class Cli(unittest.TestCase):
    def test_version(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as exit_:
            main(["--version"])
        self.assertEqual(exit_.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f"dvd-to-plex {__version__}")

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


if __name__ == "__main__":
    unittest.main()
