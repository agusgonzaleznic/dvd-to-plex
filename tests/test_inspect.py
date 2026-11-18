"""inspect: the survey of the sample disc, and its refusals, each with a patched copy as the control."""
import contextlib
import errno
import io
import json
import pathlib
import shutil
import struct
import subprocess
import tempfile
import unittest

from disc import sample_disc
from dvdtoplex import scramble, source, survey
from dvdtoplex.cli import REFUSED, main

SECTOR = 2048


def run_cli(*args) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in args])
    return code, out.getvalue(), err.getvalue()


def cell_offset(ifo_bytes: bytes, pgc: int, cell: int = 1) -> int:
    """Byte offset of a cell's playback entry in a VTS IFO."""
    pgcit = struct.unpack_from(">I", ifo_bytes, 0xCC)[0] * SECTOR
    base = pgcit + struct.unpack_from(">I", ifo_bytes, pgcit + 8 + 8 * (pgc - 1) + 4)[0]
    return base + struct.unpack_from(">H", ifo_bytes, base + 0xE8)[0] + 24 * (cell - 1)


class Copy(unittest.TestCase):
    """Each test gets its own copy of the sample to patch."""

    def setUp(self):
        self.video_ts_orig, self.truth = sample_disc()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.video_ts = self.root / "VIDEO_TS"
        shutil.copytree(self.video_ts_orig, self.video_ts)

    def patch_ifo(self, name: str, fn) -> None:
        for suffix in (".IFO", ".BUP"):
            p = self.video_ts / (name + suffix)
            d = bytearray(p.read_bytes())
            fn(d)
            p.write_bytes(bytes(d))


class Survey(Copy):
    def test_sample(self):
        s = survey.survey(self.video_ts)
        self.assertEqual(s.standard, "PAL")
        self.assertEqual(s.scramble.scrambled, 0)
        self.assertGreater(s.scramble.checked, 100)
        self.assertEqual([t.number for t in s.titles], [1, 2, 3])
        t1, t2, t3 = s.titles
        self.assertEqual(t1.audio_ids, [0x80, 0x81])
        self.assertEqual([a.content for a in t1.audio], ["normal", "commentary"])
        self.assertEqual(t1.sub_ids, [0x20])
        self.assertEqual((t2.sub_ids, t3.sub_ids), ([0x20], []))
        self.assertEqual(s.main.number, 1)
        self.assertFalse(any(t.problems or t.duplicate_of for t in s.titles))

    def test_cli_lists_and_writes_json(self):
        code, out, err = run_cli("inspect", self.root)
        self.assertEqual((code, err), (0, ""))
        line1 = next(x for x in out.splitlines() if x.startswith("1 "))
        self.assertIn("de (commentary)", line1)
        self.assertIn("main (longest)", line1)
        code, out, _ = run_cli("inspect", "--json", self.video_ts)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["main"], 1)

    def test_angle_blocks_and_interleaving_get_flagged(self):
        def mark(d):
            d[cell_offset(d, 1)] |= 0x10      # title 2: angle block
            d[cell_offset(d, 2)] |= 0x04      # title 3: interleaved
        self.patch_ifo("VTS_02_0", mark)
        s = survey.survey(self.video_ts)
        self.assertEqual(s.titles[1].problems, ["multi-angle"])
        self.assertEqual(s.titles[2].problems, ["interleaved cells (seamless branching)"])
        self.assertEqual(s.titles[0].problems, [])

    def test_titles_sharing_cells_are_marked_duplicate(self):
        def same(d):
            a, b = cell_offset(d, 1), cell_offset(d, 2)
            d[b + 8:b + 24] = d[a + 8:a + 24]
        self.patch_ifo("VTS_02_0", same)
        s = survey.survey(self.video_ts)
        self.assertEqual([t.duplicate_of for t in s.titles], [None, None, 2])


class Scrambled(Copy):
    def scramble_video(self) -> int:
        vob = self.video_ts / "VTS_01_1.VOB"
        d = bytearray(vob.read_bytes())
        hit = 0
        for k in range(0, len(d), SECTOR):
            pes = k + 14 + (d[k + 13] & 7)
            if d[k:k + 4] == b"\x00\x00\x01\xba" and d[pes:pes + 4] == b"\x00\x00\x01\xe0":
                d[pes + 6] |= 0x10
                hit += 1
        vob.write_bytes(bytes(d))
        return hit

    def test_clean_sample_is_not_refused(self):
        self.assertEqual(run_cli("inspect", self.root)[0], 0)

    def test_scrambled_copy_is_refused_with_the_fixed_sentence(self):
        self.assertGreater(self.scramble_video(), 0)
        self.assertGreater(scramble.count_disc(self.video_ts).scrambled, 0)
        code, out, err = run_cli("inspect", self.root)
        self.assertEqual((code, out, err.strip()), (REFUSED, "", scramble.REFUSAL))

    def test_pack_check_reads_only_flagged_streams(self):
        pack = bytearray(SECTOR)
        pack[:4] = b"\x00\x00\x01\xba"
        pack[14:18] = b"\x00\x00\x01\xbf"           # private stream 2 (navigation): no flags byte
        self.assertIsNone(scramble.pack_scrambled(bytes(pack)))
        pack[17] = 0xE0
        self.assertFalse(scramble.pack_scrambled(bytes(pack)))
        pack[20] = 0x10
        self.assertTrue(scramble.pack_scrambled(bytes(pack)))


class Sources(Copy):
    def test_iso_is_attached_read_only_and_detached(self):
        iso = self.root / "sample.iso"
        subprocess.run(["hdiutil", "makehybrid", "-udf", "-udf-volume-name", "SAMPLE", "-o", str(iso),
                        str(self.root)], check=True, capture_output=True, timeout=300)
        with source.opened(iso) as video_ts:
            mount = video_ts.parent
            self.assertTrue((video_ts / "VIDEO_TS.IFO").is_file())
            with self.assertRaises(OSError) as e:
                (mount / "write-test").write_bytes(b"x")
            self.assertEqual(e.exception.errno, errno.EROFS)
            self.assertEqual(len(survey.survey(video_ts).titles), 3)
        self.assertFalse(mount.exists() and any(mount.iterdir()))
        self.assertEqual(run_cli("inspect", iso)[0], 0)

    def test_bad_sources_are_errors(self):
        (self.root / "notes.txt").write_text("x")
        (self.root / "empty").mkdir()
        for bad in (self.root / "missing", self.root / "notes.txt", self.root / "empty"):
            code, out, err = run_cli("inspect", bad)
            self.assertEqual((code, out), (1, ""), bad)
            self.assertTrue(err.startswith("dvd-to-plex: "), err)

    def test_truncated_ifo_is_an_error_not_a_crash(self):
        p = self.video_ts / "VTS_01_0.IFO"
        p.write_bytes(p.read_bytes()[:0x300])
        code, _, err = run_cli("inspect", self.root)
        self.assertEqual(code, 1)
        self.assertIn("truncated or malformed", err)


if __name__ == "__main__":
    unittest.main()
