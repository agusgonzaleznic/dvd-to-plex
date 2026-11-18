import contextlib
import io
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


if __name__ == "__main__":
    unittest.main()
