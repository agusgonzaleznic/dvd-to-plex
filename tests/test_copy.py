"""copy: a byte-identical copy with a manifest, and the ways it must stop."""
import contextlib
import hashlib
import io
import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from disc import sample_disc
from dvdtoplex import copier, scramble, source
from dvdtoplex.cli import REFUSED, main


def run_cli(*args) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in args])
    return code, out.getvalue(), err.getvalue()


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class Copy(unittest.TestCase):
    def setUp(self):
        self.video_ts, _ = sample_disc()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)
        self.work = self.tmp / "work"

    def test_copy_is_byte_identical_and_recorded(self):
        code, out, err = run_cli("copy", self.video_ts, self.work)
        self.assertEqual((code, err), (0, ""), err)
        manifest = json.loads((self.work / "copy.json").read_text(encoding="utf-8"))
        names = sorted(p.name for p in self.video_ts.iterdir() if p.suffix in (".IFO", ".BUP", ".VOB"))
        self.assertEqual([f["name"] for f in manifest["files"]], names)
        for f in manifest["files"]:
            self.assertEqual(sha(self.video_ts / f["name"]), f["sha256"])
            self.assertEqual(sha(self.work / "VIDEO_TS" / f["name"]), f["sha256"])
        self.assertEqual(copier.verify(self.work), [])
        self.assertFalse(list((self.work / "VIDEO_TS").glob("*.part")))

    def test_verify_notices_a_changed_or_missing_file(self):
        copier.copy(self.video_ts, self.work)
        vob = self.work / "VIDEO_TS" / "VTS_01_1.VOB"
        d = bytearray(vob.read_bytes())
        d[5000] ^= 1
        vob.write_bytes(bytes(d))
        (self.work / "VIDEO_TS" / "VTS_02_0.BUP").unlink()
        self.assertEqual(copier.verify(self.work),
                         ["VTS_01_1.VOB: changed since the copy", "VTS_02_0.BUP: missing"])

    def test_read_error_stops_and_leaves_no_partial_file(self):
        real_open = source.open_regular
        calls = {"n": 0}

        class Failing(io.RawIOBase):
            def __init__(self, f):
                self.f = f

            def fileno(self):
                return self.f.fileno()

            def read(self, n=-1):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise OSError(5, "Input/output error")
                return self.f.read(n)

            def close(self):
                self.f.close()
                super().close()

        def fake_open(path, *a, **k):
            f = real_open(path, *a, **k)
            # only the copy reads unbuffered; the up-front scramble sample must not be counted
            copying = k.get("buffering") == 0 and str(path).endswith("VTS_01_1.VOB")
            return Failing(f) if copying else f

        with mock.patch("dvdtoplex.source.open_regular", fake_open):
            with self.assertRaises(copier.CopyError) as e:
                copier.copy(self.video_ts, self.work)
        self.assertIn("read error in VTS_01_1.VOB at byte 1048576", str(e.exception))
        self.assertFalse((self.work / "VIDEO_TS" / "VTS_01_1.VOB").exists())
        self.assertFalse(list((self.work / "VIDEO_TS").glob("*.part")))
        self.assertFalse((self.work / "copy.json").exists())

    def test_scrambled_disc_is_refused_before_copying(self):
        bad = self.tmp / "disc" / "VIDEO_TS"
        shutil.copytree(self.video_ts, bad)
        vob = bad / "VTS_01_1.VOB"
        d = bytearray(vob.read_bytes())
        for k in range(0, len(d), 2048):
            pes = k + 14 + (d[k + 13] & 7)
            if d[k:k + 4] == b"\x00\x00\x01\xba" and d[pes:pes + 4] == b"\x00\x00\x01\xe0":
                d[pes + 6] |= 0x10
        vob.write_bytes(bytes(d))
        code, out, err = run_cli("copy", bad, self.work)
        self.assertEqual((code, out, err.strip()), (REFUSED, "", scramble.REFUSAL))
        self.assertFalse(self.work.exists())

    def test_existing_copy_is_not_overwritten(self):
        copier.copy(self.video_ts, self.work)
        before = sha(self.work / "copy.json")
        code, _, err = run_cli("copy", self.video_ts, self.work)
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        self.assertEqual(sha(self.work / "copy.json"), before)


if __name__ == "__main__":
    unittest.main()
