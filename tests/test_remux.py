"""remux on the sample disc: the output against the authored truth, and every check against a
control built to break it."""
import collections
import contextlib
import dataclasses
import hashlib
import io
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from disc import sample_disc
from dvdtoplex import ifo, media, parts, remux, scramble, survey, vob
from dvdtoplex.cli import main


def colours_drawn(mkv: pathlib.Path, seconds: float) -> set[str]:
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(mkv), "-f", "lavfi",
         "-i", f"color=c=red:s=720x576:r=25:d={seconds + 1}", "-filter_complex",
         "[1:v][0:s:0]overlay=eof_action=pass:format=rgb,fps=2[o]", "-map", "[o]",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True, timeout=300).stdout
    n, seen = 720 * 576 * 3, collections.Counter()
    for k in range(len(raw) // n):
        f = raw[k * n:(k + 1) * n]
        seen.update(f[i:i + 3].hex() for i in range(0, n, 3))
    return set(seen) - {"fe0000"}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.video_ts, cls.truth = sample_disc()
        cls.survey = survey.survey(cls.video_ts)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)

    def plan(self, title: int) -> list[parts.Part]:
        info = self.survey.titles[title - 1]
        ts = ifo.read_vts(self.video_ts, info.title_set)
        return parts.plan(self.video_ts, info.number, info.title_set, ts, ts.pgcs[info.pgc - 1])

    def run_title(self, title: int, **kw) -> tuple[dict, pathlib.Path, pathlib.Path]:
        (part,) = self.plan(title)
        work, out = self.tmp / f"work{title}", self.tmp / f"t{title}.mkv"
        return remux.remux(self.video_ts, self.survey.titles[title - 1], part, work, out, **kw), work, out


class Output(Base):
    def test_every_title_passes_and_matches_the_truth(self):
        for t in self.truth["titles"]:
            report, work, out = self.run_title(t["title"])
            self.assertTrue(report["ok"], report)
            self.assertTrue(out.is_file())
            self.assertEqual((report["bytes"], report["sha256"]),
                             (out.stat().st_size, hashlib.sha256(out.read_bytes()).hexdigest()))
            self.assertFalse((work / "cut.vob").exists() or (work / "stage1.mkv").exists())
            got = media.streams(out)
            audio = [s for s in got if s["codec_type"] == "audio"]
            self.assertEqual([s["tags"]["language"] for s in audio],
                             [{"de": "ger", "en": "eng"}[a["lang"]] for a in t["audio"]])
            self.assertEqual([s["disposition"]["comment"] for s in audio],
                             [int(a["content"] == "commentary") for a in t["audio"]])
            self.assertEqual(audio[0]["disposition"]["default"], 1)
            tv = media.first_time(out, "v:0")
            subs = [round(x - tv, 3) for x, _ in media.packets(out, "s:0")] if t["subs"] else []
            self.assertEqual(subs, [line["start"] for s in t["subs"] for line in s["lines"]])
            if t["subs"]:
                self.assertEqual(colours_drawn(out, t["seconds"]), {t["subs"][0]["colour"], "000000"})
            self.assertAlmostEqual(media.video_seconds(out), t["seconds"], delta=0.08)

    def test_chapters_follow_the_ifo(self):
        _, _, out = self.run_title(1)
        rows = media.run(["ffprobe", "-v", "error", "-show_chapters", "-of", "csv=p=0", str(out)]).split()
        self.assertEqual([float(r.split(",")[3]) for r in rows], self.truth["titles"][0]["chapters"])


class Controls(Base):
    def test_shifted_subtitle_index_fails_the_time_check(self):
        real = vob.write_idx

        def late(path, **kw):
            kw["times"] = [x + 0.04 for x in kw["times"]]
            return real(path, **kw)

        with mock.patch.object(vob, "write_idx", late):
            report, work, out = self.run_title(1)
        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"]["subtitles"][0]["times_ok"])
        self.assertTrue(report["checks"]["subtitles"][0]["data_ok"])
        self.assertFalse(out.exists())
        self.assertTrue((work / "cut.vob").exists(), "a failed part keeps its work files")

    def test_wrong_anchor_fails_anchor_and_cross_check(self):
        real = remux._first_pts

        def off(cut, wanted, n=64):
            got = real(cut, wanted, n)
            got[0xE0] += 9000          # 0.1 s
            return got

        with mock.patch.object(remux, "_first_pts", off):
            report, _, out = self.run_title(1)
        self.assertFalse(report["checks"]["anchor"]["ok"])
        self.assertFalse(report["checks"]["subtitles"][0]["cross_check"]["ok"])
        self.assertFalse(out.exists())

    def test_packet_hashes_notice_a_missing_second(self):
        _, _, out = self.run_title(2)
        (part,) = self.plan(2)
        cut = self.tmp / "cut.vob"
        vob.cut(vob.title_vobs(self.video_ts, 2), part.ranges, cut)
        full = media.packet_hashes(cut, "0:i:0x1e0", genpts=True)
        self.assertEqual(full, media.packet_hashes(out, "0:v:0"))
        self.assertNotEqual(full, media.packet_hashes(out, "0:v:0", seconds=4.0))

    def test_decode_check_notices_corruption(self):
        _, _, out = self.run_title(2)
        self.assertEqual(media.decode_errors(out), [])
        bad = self.tmp / "bad.mkv"
        d = bytearray(out.read_bytes())
        for k in range(len(d) // 3, len(d) // 3 + 4096):
            d[k] ^= 0x5A
        bad.write_bytes(bytes(d))
        self.assertTrue(media.decode_errors(bad))

    def test_scrambled_cut_is_refused(self):
        copy = self.tmp / "VIDEO_TS"
        shutil.copytree(self.video_ts, copy)
        vobf = copy / "VTS_02_1.VOB"
        d = bytearray(vobf.read_bytes())
        for k in range(0, len(d), 2048):
            p = k + 14 + (d[k + 13] & 7)
            if d[k:k + 4] == b"\x00\x00\x01\xba" and d[p:p + 4] == b"\x00\x00\x01\xe0":
                d[p + 6] |= 0x10
        vobf.write_bytes(bytes(d))
        (part,) = self.plan(2)
        with self.assertRaises(remux.RemuxError) as e:
            remux.remux(copy, self.survey.titles[1], part, self.tmp / "w", self.tmp / "o.mkv")
        self.assertEqual(str(e.exception), scramble.REFUSAL)


class Parts(Base):
    def test_a_real_timestamp_reset_splits_the_title(self):
        ts = ifo.read_vts(self.video_ts, 2)
        a, b = ts.pgcs[0].cells[0], ts.pgcs[1].cells[0]      # two titles, separate timelines
        joined = dataclasses.replace(ts.pgcs[0], cells=[a, dataclasses.replace(b, stc_discontinuity=True)],
                                     program_cells=[1, 2])
        got = parts.plan(self.video_ts, 2, 2, ts, joined)
        self.assertEqual([len(p.cells) for p in got], [1, 1])
        self.assertEqual([p.of for p in got], [2, 2])
        info = self.survey.titles[1]
        for p in got:
            report = remux.remux(self.video_ts, info, p, self.tmp / f"w{p.number}", self.tmp / f"p{p.number}.mkv")
            self.assertTrue(report["ok"], report)
        self.assertEqual(len(media.packets(self.tmp / "p1.mkv", "s:0")), 2)
        self.assertEqual([s["codec_type"] for s in media.streams(self.tmp / "p2.mkv")], ["video", "audio"])

    def test_a_flag_without_a_reset_keeps_one_part(self):
        ts = ifo.read_vts(self.video_ts, 1)
        pgc = ts.pgcs[0]
        flagged = dataclasses.replace(pgc, cells=[pgc.cells[0]] + [dataclasses.replace(c, stc_discontinuity=True)
                                                                  for c in pgc.cells[1:]])
        (only,) = parts.plan(self.video_ts, 1, 1, ts, flagged)
        self.assertEqual(only.chapters, [0.0, 3.0, 6.0, 9.0])

    def test_shared_short_cells_are_filler(self):
        cell = ifo.Cell(seconds=0.48, first_sector=900, last_sector=910, angle_block=False,
                        interleaved=False, stc_discontinuity=True)
        long = dataclasses.replace(cell, seconds=0.48, first_sector=920, last_sector=930)
        pgc = ifo.Pgc(number=1, seconds=0, fps=25, audio_streams=[], sub_streams=[], palette_ycrcb=[],
                      program_cells=[1], cells=[cell])
        ts = ifo.TitleSet(number=2, standard="PAL", aspect="4:3", audio=[], subpictures=[],
                          pgcs=[pgc, dataclasses.replace(pgc, number=2),
                                dataclasses.replace(pgc, number=3, cells=[long])])
        self.assertEqual(parts.filler(ts), {(900, 910)})



class Command(Base):
    def cli(self, *args) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([str(a) for a in args])
        return code, out.getvalue(), err.getvalue()

    def test_titles_flag_rerun_and_changed_copy(self):
        work = self.tmp / "work"
        self.assertEqual(self.cli("copy", self.video_ts, work)[0], 0)
        code, out, err = self.cli("remux", work, "--titles", "3")
        self.assertEqual((code, err), (0, ""), out + err)
        self.assertTrue((work / "out" / "title_03.mkv").is_file())
        self.assertEqual(len(json.loads((work / "remux.json").read_text(encoding="utf-8"))), 1)
        code, _, err = self.cli("remux", work, "--titles", "3")
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        vobf = work / "VIDEO_TS" / "VTS_01_1.VOB"
        d = bytearray(vobf.read_bytes())
        d[10000] ^= 1
        vobf.write_bytes(bytes(d))
        code, _, err = self.cli("remux", work, "--titles", "1")
        self.assertEqual(code, 1)
        self.assertIn("VTS_01_1.VOB: changed since the copy", err)
        self.assertFalse((work / "out" / "title_01.mkv").exists())

if __name__ == "__main__":
    unittest.main()
