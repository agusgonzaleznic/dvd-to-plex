"""ifo.py read against the synthetic sample disc's truth, and on hand-made bytes."""
import pathlib
import tempfile
import unittest

from dvdtoplex import ifo
from disc import rendered_colours, sample_disc


def hexes(colours) -> set[str]:
    return {"%02x%02x%02x" % c for c in colours}


class SampleDisc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.video_ts, cls.truth = sample_disc()
        cls.titles = ifo.read_vmg(cls.video_ts)
        cls.sets = {t.title_set: ifo.read_vts(cls.video_ts, t.title_set) for t in cls.titles}

    def pgc(self, n: int) -> ifo.Pgc:
        t = self.titles[n - 1]
        ts = self.sets[t.title_set]
        return ts.pgcs[ts.title_pgcs[t.title_in_set - 1] - 1]

    def test_title_table(self):
        got = [(t.title_set, t.chapters, t.angles) for t in self.titles]
        want = [(t["title_set"], len(t["chapters"]), 1) for t in self.truth["titles"]]
        self.assertEqual(got, want)

    def test_stream_attributes(self):
        for t in self.truth["titles"]:
            ts = self.sets[t["title_set"]]
            self.assertEqual(ts.standard, "PAL")
            self.assertEqual([(a.lang, a.content) for a in ts.audio][:len(t["audio"])],
                             [(a["lang"], a["content"]) for a in t["audio"]], f"title {t['title']}")
            for s in t["subs"]:
                self.assertIn(s["lang"], [p.lang for p in ts.subpictures])

    def test_durations_and_chapters(self):
        for t in self.truth["titles"]:
            pgc = self.pgc(t["title"])
            self.assertAlmostEqual(pgc.seconds, t["seconds"], delta=0.04, msg=f"title {t['title']}")
            self.assertEqual(pgc.fps, 25.0)
            starts = pgc.chapter_starts()
            self.assertEqual(len(starts), len(t["chapters"]))
            for got, want in zip(starts, t["chapters"]):
                self.assertAlmostEqual(got, want, delta=0.04, msg=f"title {t['title']} chapters {starts}")

    def test_cells_are_plain_and_contiguous(self):
        for t in self.truth["titles"]:
            cells = self.pgc(t["title"]).cells
            self.assertTrue(cells)
            for c in cells:
                self.assertFalse(c.angle_block or c.interleaved)
                self.assertLessEqual(c.first_sector, c.last_sector)
            for a, b in zip(cells, cells[1:]):
                self.assertEqual(b.first_sector, a.last_sector + 1)

    def test_subtitle_stream_map(self):
        for t in self.truth["titles"]:
            first = self.pgc(t["title"]).sub_streams[0]
            if t["subs"]:
                self.assertEqual(first, (0, 0, 0, 0), f"title {t['title']}")
            else:
                self.assertIsNone(first, f"title {t['title']}")

    def test_palette_holds_the_rendered_colours(self):
        for t in self.truth["titles"]:
            pal = hexes(self.pgc(t["title"]).palette_rgb())
            for s in t["subs"]:
                self.assertLessEqual({s["colour"], "000000"}, pal, f"title {t['title']}")

    def test_palette_matches_ffmpeg_decoder(self):
        """Independent reference: what ffmpeg draws from the same IFO palette. Every colour it
        draws, apart from the red background, must be one palette_rgb gives."""
        for t in self.truth["titles"]:
            if not t["subs"]:
                continue
            ts = t["title_set"]
            seen = rendered_colours(self.video_ts / f"VTS_{ts:02d}_0.IFO",
                                    self.video_ts / f"VTS_{ts:02d}_1.VOB", t["seconds"])
            drawn = set(seen) - {"fe0000"}
            self.assertIn(t["subs"][0]["colour"], drawn, f"title {t['title']} drew {sorted(drawn)}")
            self.assertLessEqual(drawn, hexes(self.pgc(t["title"]).palette_rgb()))


class Bytes(unittest.TestCase):
    def test_playback_time_pal_and_ntsc(self):
        self.assertEqual(ifo.playback_time(bytes([0x01, 0x02, 0x03, 0x40 | 0x12])), (3723 + 12 / 25, 25.0))
        secs, fps = ifo.playback_time(bytes([0x00, 0x00, 0x01, 0xC0 | 0x15]))
        self.assertAlmostEqual(fps, 29.97, places=2)
        self.assertAlmostEqual(secs, 1 + 15 / fps)

    def test_empty_time_and_unknown_rate(self):
        self.assertEqual(ifo.playback_time(b"\0\0\0\0"), (0.0, 0.0))
        with self.assertRaises(ifo.IfoError):
            ifo.playback_time(bytes([0x00, 0x00, 0x05, 0x00]))

    def test_studio_range_palette(self):
        pgc = ifo.Pgc(number=1, seconds=0, fps=25, audio_streams=[], sub_streams=[], program_cells=[],
                      cells=[], palette_ycrcb=[(235, 128, 128), (16, 128, 128), (176, 128, 128)])
        self.assertEqual(pgc.palette_rgb(), [(255, 255, 255), (0, 0, 0), (186, 186, 186)])

    def test_rejects_a_file_that_is_not_an_ifo(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "VIDEO_TS.IFO").write_bytes(b"NOT-A-DVD-IFO" + bytes(4096))
            (d / "VTS_01_0.IFO").write_bytes(b"NOT-A-DVD-IFO" + bytes(4096))
            with self.assertRaises(ifo.IfoError):
                ifo.read_vmg(d)
            with self.assertRaises(ifo.IfoError):
                ifo.read_vts(d, 1)


if __name__ == "__main__":
    unittest.main()
