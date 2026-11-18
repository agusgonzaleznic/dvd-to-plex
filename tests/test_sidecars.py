"""name with subtitles: the film's SRTs as Plex sidecars read back by plex-renamer, an extra's
only once embedded, and verify over both; each check with a control that breaks it."""
import contextlib
import hashlib
import io
import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from disc import remuxed_work, sample_disc
from dvdtoplex import assemble, media, namer
from dvdtoplex.cli import main

FOLDER = "Movies/Example Film (2024) {imdb-tt0000001}"
FILM = f"{FOLDER}/Example Film (2024) {{imdb-tt0000001}}"
NAMES = {"category": "Movies", "title": "Example Film", "year": 2024, "imdb": "tt0000001",
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
        (self.work / "names.json").write_text(json.dumps(NAMES), encoding="utf-8")
        self.lines = {t["title"]: t["subs"][0]["lines"] for t in sample_disc()[1]["titles"] if t["subs"]}

    def srt(self, title: int, name: str) -> pathlib.Path:
        path = self.work / "subs" / name
        assemble.write_srt(path, [(x["start"], x["end"], x["text"]) for x in self.lines[title]])
        return path

    def placed(self) -> list[str]:
        return sorted(p.relative_to(self.lib).as_posix() for p in self.lib.rglob("*") if p.is_file())


class Sidecars(Base):
    def test_the_films_srt_goes_beside_it_and_verifies(self):
        srt = self.srt(1, "title_01.s0.de.srt")
        want = sha(srt)
        code, _, err = cli("name", self.work, self.lib)
        self.assertEqual((code, err), (0, ""), err)
        self.assertIn(f"{FILM}.de.srt", self.placed())
        self.assertEqual(sha(self.lib / f"{FILM}.de.srt"), want)
        self.assertFalse(srt.exists(), "moved, not copied")
        record = json.loads((self.work / "name.json").read_text(encoding="utf-8"))
        self.assertEqual([(f["role"], f["src"]) for f in record["files"] if f["role"] == "subtitle"],
                         [("subtitle", "subs/title_01.s0.de.srt")])
        code, out, err = cli("verify", self.work)
        self.assertEqual((code, err), (0, ""), err)

    def test_a_forced_disc_track_names_its_srt_forced(self):
        self.srt(1, "title_01.s0.de.srt")
        real = media.streams

        def forced(path):
            return [{**s, "disposition": {**s["disposition"], "forced": 1}} if s["codec_type"] == "subtitle" else s
                    for s in real(path)]

        with mock.patch.object(namer.media, "streams", forced):
            code, _, err = cli("name", self.work, self.lib)
        self.assertEqual((code, err), (0, ""), err)
        self.assertIn(f"{FILM}.de.forced.srt", self.placed())

    def test_names_for_a_repeated_language(self):
        names = namer.sidecar_names("Film (2024)", [("de", False), ("de", True), ("de", False)])
        self.assertEqual(names, ["Film (2024).de.srt", "Film (2024).de.forced.srt", "Film (2024).ger.srt"])
        with self.assertRaisesRegex(namer.PlaceError, "more than two de subtitles"):
            namer.sidecar_names("Film (2024)", [("de", False)] * 3)

    def test_an_extras_srt_must_be_embedded_first(self):
        self.srt(2, "title_02.s0.de.srt")
        code, _, err = cli("name", self.work, self.lib)
        self.assertEqual(code, 1)
        self.assertIn("Plex ignores sidecar subtitles next to a local extra", err)
        self.assertIn("embed WORK --files title_02", err)
        self.assertEqual(self.placed(), [])
        self.srt(1, "title_01.s0.de.srt")
        self.assertEqual(cli("embed", self.work)[0], 0)
        code, _, err = cli("name", self.work, self.lib)
        self.assertEqual((code, err), (0, ""), err)
        self.assertFalse([p for p in self.placed() if p.endswith(".srt")], "embedded, so no sidecar")
        self.assertEqual(cli("verify", self.work)[0], 0)

    def test_an_srt_for_a_track_the_file_lacks_is_refused(self):
        self.srt(1, "title_01.s1.de.srt")
        code, _, err = cli("name", self.work, self.lib)
        self.assertEqual(code, 1)
        self.assertIn("title_01.s1.de.srt: title_01.mkv has no subtitle stream 1", err)


class Controls(Base):
    def test_a_sidecar_plex_renamer_would_rename_is_refused(self):
        self.srt(1, "title_01.s0.de.srt")
        with mock.patch.object(namer, "sidecar_names", lambda stem, tracks: ["Example Film.de.srt"]):
            code, _, err = cli("name", self.work, self.lib)
        self.assertEqual(code, 1)
        self.assertIn("plex-renamer reads these names differently", err)
        self.assertIn("would move", err)
        self.assertEqual(self.placed(), [])

    def test_an_srt_changed_before_the_move_stops_everything(self):
        srt = self.srt(1, "title_01.s0.de.srt")
        real = namer.plan

        def then_edit(*args):
            record = real(*args)
            srt.write_text(srt.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            return record

        with mock.patch.object(namer, "plan", then_edit):
            code, _, err = cli("name", self.work, self.lib)
        self.assertEqual(code, 1)
        self.assertIn("subs/title_01.s0.de.srt changed since it was checked; nothing was moved", err)
        self.assertEqual(self.placed(), [])

    def test_verify_catches_a_changed_or_missing_sidecar(self):
        self.srt(1, "title_01.s0.de.srt")
        self.assertEqual(cli("name", self.work, self.lib)[0], 0)
        sidecar = self.lib / f"{FILM}.de.srt"
        keep = sidecar.read_bytes()
        sidecar.write_bytes(keep.replace(b"\n", b"\r\n", 1))
        code, _, err = cli("verify", self.work)
        self.assertEqual(code, 1)
        self.assertIn(f"{FILM}.de.srt: changed since it was checked", err)
        sidecar.unlink()
        code, _, err = cli("verify", self.work)
        self.assertEqual(code, 1)
        self.assertIn(f"{FILM}.de.srt: missing", err)


if __name__ == "__main__":
    unittest.main()
