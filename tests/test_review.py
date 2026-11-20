"""review-prep and assemble on the sample's OCR text: the plants, and canned proofreader outputs
for each way a chunk must pass or fail."""
import contextlib
import io
import json
import os
import pathlib
import random
import re
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from disc import ocr_work, sample_disc
from dvdtoplex import assemble, review
from dvdtoplex.cli import main

TRACKS = {"title_01.s0": 1, "title_02.s0": 2}
BREAKS = "\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"     # what str.splitlines() breaks on besides "\n"
WRITTEN = ("1\n00:00:01,000 --> 00:00:02,604\nGuten\nMorgen, wie war die Reise?\n\n"
           "2\n00:00:03,400 --> 00:00:05,402\nLang,\naber die Aussicht war schön.\n\n"
           "3\n00:00:06,500 --> 00:00:08,502\nUm\nneun fährt der letzte Bus über die Brücke.\n\n"
           "4\n00:00:09,200 --> 00:00:11,396\nDann\nmüssen wir uns beeilen!\n").encode()


def cli(*args) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in args])
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        self.work, self.keys = root / "work", root / "keys"
        for sub in ("out", "ocr"):
            shutil.copytree(ocr_work() / sub, self.work / sub)
        env = mock.patch.dict(os.environ, {"DVDTOPLEX_KEYS": str(self.keys)})
        env.start()
        self.addCleanup(env.stop)
        self.truth = {t["title"]: [line["text"] for line in t["subs"][0]["lines"]]
                      for t in sample_disc()[1]["titles"] if t["subs"]}
        code, self.prep_out, err = cli("review-prep", self.work)
        self.assertEqual((code, err), (0, ""), err)

    def chunk(self, track: str) -> pathlib.Path:
        return self.work / "review" / track / "chunk_01"

    def cues(self, track: str) -> list[dict]:
        return json.loads((self.chunk(track) / "cues.json").read_text(encoding="utf-8"))["cues"]

    def ocr_first(self, track: str) -> dict[int, str]:
        stem, s = track.split(".")
        ocr = json.loads((self.work / "ocr" / stem / f"{s}.json").read_text(encoding="utf-8"))
        return {c["n"]: c["first"] for c in ocr["cues"]}

    def planted(self, track: str) -> list[int]:
        first = self.ocr_first(track)
        return [c["n"] for c in self.cues(track) if c["draft"] != first[c["n"]]]

    def answer(self, track: str, text=None, note=None) -> None:
        """Answer every cue: the authored line, unless TEXT(cue, truth) says otherwise."""
        truth = self.truth[TRACKS[track]]
        rows = [{"n": c["n"], "text": text(c, truth[c["n"] - 1]) if text else truth[c["n"] - 1],
                 "note": note(c) if note else ""} for c in self.cues(track)]
        (self.chunk(track) / "answers.json").write_text(json.dumps({"cues": rows}, ensure_ascii=False),
                                                        encoding="utf-8")

    def srt(self, track: str, lang: str = "de") -> list[str]:
        blocks = (self.work / "subs" / f"{track}.{lang}.srt").read_text(encoding="utf-8").strip().split("\n\n")
        return ["\n".join(b.split("\n")[2:]) for b in blocks]


class Prep(Base):
    def test_controls_sit_only_where_the_images_can_expose_them(self):
        self.assertIn("title_01.s0: 1 chunks", self.prep_out)
        self.assertEqual(len(self.planted("title_01.s0")), 3)
        self.assertEqual(len(self.planted("title_02.s0")), 2)
        for track in TRACKS:
            for c in self.cues(track):
                self.assertEqual(set(c), {"n", "draft"}, "both readings agree, so no second reading is shown")
            self.assertEqual((self.chunk(track) / "sheet_01.png").read_bytes()[:4], b"\x89PNG")

    def test_the_key_is_outside_the_work_folder_hashed_and_private(self):
        keys = list(self.keys.glob("*.json"))
        self.assertEqual(len(keys), 2)
        words = {w for lines in self.truth.values() for line in lines for w in line.split()}
        for k in keys:
            self.assertEqual(stat.S_IMODE(k.stat().st_mode), 0o600)
            text = k.read_text(encoding="utf-8")
            self.assertFalse([w for w in words if w in text], "the key holds no word in plain text")
        with mock.patch.dict(os.environ, {"DVDTOPLEX_KEYS": str(self.work / "keys")}):
            code, _, err = cli("review-prep", self.work)
        self.assertEqual(code, 1)
        self.assertIn("outside the work folder", err)

    def test_counts_below_one_are_refused(self):
        for args in (("--chunk", "0"), ("--chunk", "-1"), ("--plants", "0"),
                     ("--redo", "title_01.s0/1", "--plants", "0")):
            with self.subTest(args=args):
                code, _, err = cli("review-prep", self.work, *args)
                self.assertEqual(code, 1)
                self.assertIn("at least 1", err)

    def test_a_track_without_chunks_never_passes(self):
        for track in TRACKS:
            self.answer(track)
        path = self.work / "review" / "title_01.s0" / "review.json"
        summary = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**summary, "chunks": []}), encoding="utf-8")
        self.assertEqual(cli("assemble", self.work)[0], 1)
        self.assertFalse((self.work / "subs" / "title_01.s0.de.srt").exists())

    def test_preparing_twice_is_refused(self):
        code, _, err = cli("review-prep", self.work)
        self.assertEqual(code, 1)
        self.assertIn("prepared already", err)

    def test_each_plant_changes_exactly_one_token(self):
        rng = random.Random(7)
        for text in ("'Hallo Lisa, komm bitte her.", "Lang, aber schön.", "Guten Morgen!",
                     "Hallo Lisa,\nkomm bitte her."):
            for _ in range(50):
                planted, old, new, kind = review.plant(text, rng)
                a, b = text.split(), planted.split()
                self.assertEqual(sum(x != y for x, y in zip(a, b)), 1)
                self.assertNotEqual(old, new)
                self.assertEqual(re.sub(r"\S+", "x", planted), re.sub(r"\S+", "x", text),
                                 "the line breaks and spaces stay where the image has them")
                if kind == "quote":
                    self.assertEqual(planted, text[1:])
                if kind == "l":
                    self.assertEqual(new, old.replace("L", "l", 1) if old.count("L") == 1 else new)
        self.assertIsNone(review.plant("Ja, gut.", rng))


class Assemble(Base):
    def test_restoring_every_control_writes_the_srt(self):
        for track in TRACKS:
            self.answer(track)
        code, out, err = cli("assemble", self.work)
        self.assertEqual((code, err), (0, ""), err)
        self.assertIn("title_01.s0: every control restored; 4 cues", out)
        self.assertEqual(self.srt("title_01.s0"), self.truth[1])
        self.assertEqual(self.srt("title_02.s0"), self.truth[2])
        first = (self.work / "subs" / "title_01.s0.de.srt").read_text(encoding="utf-8").split("\n")[1]
        self.assertEqual(first, "00:00:01,000 --> 00:00:02,604")

    def test_kept_plants_fail_the_chunk_without_naming_them(self):
        self.answer("title_01.s0", text=lambda c, truth: c["draft"])
        self.answer("title_02.s0")
        code, out, err = cli("assemble", self.work)
        self.assertEqual(code, 1)
        self.assertIn("title_01.s0 chunk 1: 3 of 3 controls failed; redo it one model up", err)
        self.assertFalse((self.work / "subs" / "title_01.s0.de.srt").exists())
        self.assertTrue((self.work / "subs" / "title_02.s0.de.srt").exists())
        words = {w.strip(".,!?") for c in self.cues("title_01.s0") for w in c["draft"].split()}
        said = set(re.findall(r"\w+", out + err))         # whole words: "der" is in /var/folders
        self.assertFalse([w for w in words if len(w) > 2 and w in said], "no cue text in the report")
        self.assertIn("title_01", said, "control: the report's own words are found")

    def test_a_silent_cut_and_an_unsure_keep_both_fail(self):
        planted = self.planted("title_01.s0")
        first = self.ocr_first("title_01.s0")

        def cut(c, truth):
            if c["n"] != planted[0]:
                return truth
            gone = next(t for t in c["draft"].split() if t not in first[c["n"]].split())
            return " ".join(t for t in c["draft"].split() if t != gone)

        self.answer("title_01.s0", text=cut)
        self.answer("title_02.s0")
        code, _, err = cli("assemble", self.work)
        self.assertEqual(code, 1)
        self.assertIn("1 of 3 controls failed", err)
        self.answer("title_01.s0", text=lambda c, truth: c["draft"] if c["n"] == planted[0] else truth,
                    note=lambda c: "unsure" if c["n"] == planted[0] else "")
        code, _, err = cli("assemble", self.work)
        self.assertEqual(code, 1)
        self.assertIn("1 of 3 controls failed", err)
        result = assemble.score(self.work, "title_01.s0", self.keys)
        self.assertEqual([c["failed"] for c in result["chunks"]], [1])

    def test_overrides_never_touch_a_verdict(self):
        overrides = self.work / "review" / "title_01.s0" / "overrides.json"
        truth = self.truth[1]
        overrides.write_text(json.dumps({str(n): truth[n - 1] for n in self.planted("title_01.s0")}),
                             encoding="utf-8")
        self.answer("title_01.s0", text=lambda c, t: c["draft"])
        self.answer("title_02.s0")
        self.assertEqual(cli("assemble", self.work)[0], 1)
        overrides.write_text(json.dumps({"2": "Anders gesagt."}), encoding="utf-8")
        self.answer("title_01.s0")
        self.assertEqual(cli("assemble", self.work)[0], 0)
        self.assertEqual(self.srt("title_01.s0")[1], "Anders gesagt.")

    def test_a_redo_gives_fresh_controls_and_keeps_the_old_answers(self):
        self.answer("title_01.s0", text=lambda c, t: c["draft"])
        self.answer("title_02.s0")
        self.assertEqual(cli("assemble", self.work)[0], 1)
        code, out, err = cli("review-prep", self.work, "--redo", "title_01.s0/1")
        self.assertEqual((code, err), (0, ""), err)
        self.assertIn("attempt 2", out)
        self.assertTrue((self.chunk("title_01.s0") / "answers.attempt1.json").is_file())
        self.assertFalse((self.chunk("title_01.s0") / "answers.json").exists())
        self.answer("title_01.s0")
        self.assertEqual(cli("assemble", self.work)[0], 0)

    def test_malformed_answers_are_refused(self):
        self.answer("title_02.s0")
        code, _, err = cli("assemble", self.work)
        self.assertEqual(code, 1)
        self.assertIn("title_01.s0/chunk_01: no readable answers.json", err)
        self.answer("title_01.s0")
        path = self.chunk("title_01.s0") / "answers.json"
        rows = json.loads(path.read_text(encoding="utf-8"))["cues"]
        for broken in (rows[:-1], rows + rows[:1]):
            path.write_text(json.dumps({"cues": broken}), encoding="utf-8")
            code, _, err = cli("assemble", self.work)
            self.assertEqual(code, 1)
            self.assertIn("exactly once", err)


class CueShape(Base):
    """An answer is model output: nothing in its text may add, split or end an SRT cue."""

    def refused(self, inject) -> str:
        self.answer("title_01.s0", text=lambda c, t: inject(t) if c["n"] == 2 else t)
        self.answer("title_02.s0")
        code, out, err = cli("assemble", self.work)
        self.assertEqual(code, 1)
        self.assertFalse((self.work / "subs").exists(), "no SRT is written")
        self.assertNotIn("Eingeschmuggelt", out + err, "the report never quotes the answer")
        return err

    def test_a_blank_line_and_a_timing_line_are_refused(self):
        err = self.refused(lambda t: t + "\n\n5\n00:00:09,000 --> 00:00:10,000\nEingeschmuggelt")
        self.assertIn("title_01.s0/chunk_01: cue 2's text has a blank line, which could change the SRT's cues", err)

    def test_each_form_that_could_change_the_cues_is_refused(self):
        forms = {**{f"break {ord(b):#x}": (b, "a line break other than a newline") for b in BREAKS},
                 "crlf": ("\r\n", "a line break other than a newline"),
                 "crlf twice": ("\r\n\r\n", "a line break other than a newline"),
                 "empty line": ("\n\n", "a blank line"),
                 "spaces and a tab": ("\n \t \n", "a blank line"),
                 "no-break space": ("\n\u00a0\n", "a blank line"),
                 "arrow": (" --> ", "an SRT timing arrow")}
        for name, (form, problem) in forms.items():
            with self.subTest(form=name):
                err = self.refused(lambda t: t + form + "Eingeschmuggelt")
                self.assertIn(f"cue 2's text has {problem}", err)

    def test_multi_line_text_is_written_as_before(self):
        self.answer("title_01.s0", text=lambda c, t: "  " + t.replace(" ", "\n", 1) + " \n")
        self.answer("title_02.s0")
        code, _, err = cli("assemble", self.work)
        self.assertEqual((code, err), (0, ""), err)
        self.assertEqual((self.work / "subs" / "title_01.s0.de.srt").read_bytes(), WRITTEN)

    def test_a_whitespace_only_text_still_counts_as_empty(self):
        kept = next(c["n"] for c in self.cues("title_01.s0") if c["n"] not in self.planted("title_01.s0"))
        self.answer("title_01.s0", text=lambda c, t: "\r\n \u2028\n" if c["n"] == kept else t)
        self.answer("title_02.s0")
        code, _, err = cli("assemble", self.work)
        self.assertEqual((code, err), (0, ""), err)
        report = json.loads((self.work / "review" / "assemble.json").read_text(encoding="utf-8"))
        self.assertEqual((report[0]["cues"], report[0]["empty"]), (3, 1))


class Breaks(unittest.TestCase):
    def test_breaks_are_every_line_break_but_the_newline(self):
        found = {chr(c) for c in range(0x110000) if len(f"a{chr(c)}a".splitlines()) > 1}
        self.assertEqual(found - {"\n"}, set(BREAKS))
        self.assertEqual(assemble.BREAKS, set(BREAKS))


if __name__ == "__main__":
    unittest.main()
