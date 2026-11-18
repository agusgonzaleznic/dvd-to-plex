"""embed on the remuxed sample: text tracks added with every original stream unchanged, the
original retired, name and verify taking the new file, and a control for each check."""
import contextlib
import datetime
import hashlib
import io
import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from disc import remuxed_work, sample_disc
from dvdtoplex import assemble, embed, media
from dvdtoplex.cli import main

NAMES = {"category": "Movies", "title": "Example Film", "year": 2024,
         "files": {"title_01": {"role": "movie"},
                   "title_02": {"role": "extra", "folder": "Featurettes", "label": "Making Of"},
                   "title_03": {"role": "skip"}}}


def cli(*args) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in args])
    return code, out.getvalue(), err.getvalue()


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_back(mkv: pathlib.Path, index: int) -> list[str]:
    """The text of one track, decoded by ffmpeg (embed's own check copies it instead)."""
    srt = media.run([*media.FFMPEG, "-i", str(mkv), "-map", f"0:{index}", "-f", "srt", "-"])
    return ["\n".join(b.split("\n")[2:]) for b in srt.strip().split("\n\n")]


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        self.work, self.lib = root / "work", root / "lib"
        for sub in ("out", "remux"):
            shutil.copytree(remuxed_work() / sub, self.work / sub)
        self.lib.mkdir()
        (self.work / "subs").mkdir()
        self.lines = {}
        for t in sample_disc()[1]["titles"]:
            if t["subs"]:
                stem, lines = f"title_{t['title']:02d}", t["subs"][0]["lines"]
                assemble.write_srt(self.work / "subs" / f"{stem}.s0.de.srt",
                                   [(x["start"], x["end"], x["text"]) for x in lines])
                self.lines[stem] = [x["text"] for x in lines]
        self.before = {p.stem: sha(p) for p in (self.work / "out").glob("*.mkv")}

    def retired(self) -> dict[str, pathlib.Path]:
        return {p.stem: p for p in self.work.glob("retired-*/*.mkv")}

    def assert_unchanged(self, stems=("title_01", "title_02", "title_03")) -> None:
        for stem in stems:
            self.assertEqual(sha(self.work / "out" / f"{stem}.mkv"), self.before[stem], stem)
        self.assertEqual(self.retired(), {})


class Embed(Base):
    def test_text_tracks_are_added_and_the_originals_retired(self):
        code, out, err = cli("embed", self.work)
        self.assertEqual((code, err), (0, ""), err)
        self.assertIn("title_01.mkv: 1 text track added (ger); the original is in", out)
        retired = self.retired()
        self.assertEqual(sorted(retired), ["title_01", "title_02"], "title_03 has no SRT")
        self.assertEqual(sha(self.work / "out" / "title_03.mkv"), self.before["title_03"])
        for stem, old in retired.items():
            new = self.work / "out" / f"{stem}.mkv"
            self.assertEqual(sha(old), self.before[stem], "the original is kept byte for byte")
            was, now = media.streams(old), media.streams(new)
            self.assertEqual(len(now), len(was) + 1)
            self.assertEqual([s["disposition"] for s in now[:-1]], [s["disposition"] for s in was])
            self.assertEqual((now[-1]["codec_name"], now[-1]["tags"]["language"], now[-1]["disposition"]["default"]),
                             ("subrip", "ger", 0))
            self.assertEqual(read_back(new, len(was)), self.lines[stem])
            for spec in ("v:0", "a:0", "s:0"):
                self.assertEqual(media.packet_hashes(new, spec), media.packet_hashes(old, spec), spec)
            report = json.loads((self.work / "embed" / stem / "report.json").read_text(encoding="utf-8"))
            self.assertEqual((report["sha256"], report["bytes"], report["replaced"]),
                             (sha(new), new.stat().st_size, True))
            self.assertEqual(report["original"]["sha256"], self.before[stem])

    def test_name_and_verify_take_the_embedded_files(self):
        self.assertEqual(cli("embed", self.work)[0], 0)
        (self.work / "names.json").write_text(json.dumps(NAMES), encoding="utf-8")
        code, _, err = cli("name", self.work, self.lib)
        self.assertEqual((code, err), (0, ""), err)
        code, out, err = cli("verify", self.work)
        self.assertEqual((code, err), (0, ""), err)
        self.assertIn("ok, every file as it was checked", out)

    def test_name_refuses_an_embedded_file_that_does_not_match(self):
        self.assertEqual(cli("embed", self.work)[0], 0)
        (self.work / "names.json").write_text(json.dumps(NAMES), encoding="utf-8")
        new = self.work / "out" / "title_02.mkv"
        keep = new.read_bytes()
        new.write_bytes(keep + b"\0")
        code, _, err = cli("name", self.work, self.lib)
        self.assertEqual(code, 1)
        self.assertIn("title_02.mkv changed since it was checked", err)
        new.write_bytes(keep)
        path = self.work / "embed" / "title_02" / "report.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**report, "original": {**report["original"], "sha256": "0" * 64}}),
                        encoding="utf-8")
        code, _, err = cli("name", self.work, self.lib)
        self.assertEqual(code, 1)
        self.assertIn("its embed report does not follow it", err)
        self.assertEqual(list(self.lib.iterdir()), [], "nothing was moved")


class Checks(Base):
    """Each check on the new file, shown to fail when what it guards is broken."""

    def refused(self, want: str) -> None:
        code, _, err = cli("embed", self.work, "--files", "title_01")
        self.assertEqual(code, 1)
        self.assertIn(want, err)
        self.assert_unchanged()
        self.assertFalse((self.work / "embed" / "title_01" / "title_01.mkv").exists(), "the failed file is gone")

    def test_a_cut_or_retagged_stream_is_caught(self):
        for extra, want in ((("-t", "5"), "stream 0 (mpeg2video): its packets changed"),
                            (("-metadata:s:a:0", "language=fre"), "stream 1 (ac3): tags changed"),
                            (("-t", "0.5"), "its text read back differs from the SRT")):   # before the first cue
            with self.subTest(extra=extra), mock.patch.object(embed, "COPY", embed.COPY + extra):
                self.refused(want)
        self.assertEqual(cli("embed", self.work, "--files", "title_01")[0], 0, "a failed run can be redone")

    def test_without_explicit_flags_ffmpeg_makes_a_subtitle_default(self):
        with mock.patch.object(embed, "dispositions", lambda probe, added: {}):
            self.refused("stream 3 (dvd_subtitle): disposition changed")

    def test_text_that_does_not_read_back_is_caught(self):
        real = embed._mux

        def other_text(src, subs, n, flags, dst):
            srt, line = subs[0]["srt"], self.lines["title_01"][0]
            keep = srt.read_text(encoding="utf-8")
            srt.write_text(keep.replace(line, line + " Nein.", 1), encoding="utf-8")
            try:
                real(src, subs, n, flags, dst)
            finally:
                srt.write_text(keep, encoding="utf-8")

        with mock.patch.object(embed, "_mux", other_text):
            self.refused("its text read back differs from the SRT")


class Refusals(Base):
    def refused(self, *args, want: str) -> None:
        code, _, err = cli("embed", self.work, *args)
        self.assertEqual(code, 1)
        self.assertIn(want, err)

    def test_bad_input_changes_nothing(self):
        srt = self.work / "subs" / "title_02.s0.de.srt"
        good = srt.read_text(encoding="utf-8")
        for text, want in (("", "no cues"), ("1\nsoon\nHallo.\n", "block 1 is not an SRT cue")):
            srt.write_text(text, encoding="utf-8")
            self.refused(want=want)
        srt.write_text(good, encoding="utf-8")
        srt.rename(srt.with_name("title_02.s0.xx.srt"))
        self.refused(want="'xx' is not a language code")
        srt.with_name("title_02.s0.xx.srt").rename(srt)
        self.refused("--files", "title_03", want="title_03: no SRT")
        self.refused("--files", "title_09", want="title_09 not in")
        self.assert_unchanged()

    def test_a_changed_original_or_a_taken_retired_name_changes_nothing(self):
        mkv = self.work / "out" / "title_02.mkv"
        keep = mkv.read_bytes()
        mkv.write_bytes(keep + b"\0")
        self.refused(want="title_02.mkv changed since it was checked")
        mkv.write_bytes(keep)
        taken = self.work / f"retired-{datetime.date.today().isoformat()}" / "title_02.mkv"
        taken.parent.mkdir()
        taken.write_bytes(b"")
        self.refused(want="exists; nothing was changed")
        taken.unlink()
        taken.parent.rmdir()
        self.assert_unchanged()

    def test_embedding_twice_or_after_a_stop_is_refused(self):
        self.assertEqual(cli("embed", self.work, "--files", "title_02")[0], 0)
        self.refused("--files", "title_02", want="title_02.mkv has its text tracks already")
        path = self.work / "embed" / "title_02" / "report.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**report, "replaced": False}), encoding="utf-8")
        self.refused("--files", "title_02", want="an earlier embed stopped part way")


if __name__ == "__main__":
    unittest.main()
