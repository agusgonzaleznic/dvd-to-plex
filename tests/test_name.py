"""name and verify on the sample disc's remuxed titles: the Plex layout, plex-renamer reading it
back, and every check against a control built to break it."""
import contextlib
import copy
import hashlib
import io
import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from disc import remuxed_work
from dvdtoplex import copier, namer
from dvdtoplex.cli import main

FOLDER = "Movies/Example Film (2024) {imdb-tt0000001}"
NAMES = {"category": "Movies", "title": "Example Film", "year": 2024, "imdb": "tt0000001",
         "files": {"title_01": {"role": "movie"},
                   "title_02": {"role": "extra", "folder": "Featurettes", "label": "Making Of"},
                   "title_03": {"role": "extra", "folder": "trailer", "label": "Trailer: One"}}}
PLACED = {"title_01": f"{FOLDER}/Example Film (2024) {{imdb-tt0000001}}.mkv",
          "title_02": f"{FOLDER}/Featurettes/Making Of.mkv",
          "title_03": f"{FOLDER}/Trailers/Trailer - One.mkv"}


def cli(*args) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in args])
    return code, out.getvalue(), err.getvalue()


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def flip(p: pathlib.Path, at: int = 5000) -> None:
    d = bytearray(p.read_bytes())
    d[at] ^= 1
    p.write_bytes(bytes(d))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.remuxed = remuxed_work()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work, self.lib = pathlib.Path(tmp.name) / "work", pathlib.Path(tmp.name) / "lib"
        for sub in ("out", "remux"):
            shutil.copytree(self.remuxed / sub, self.work / sub)
        self.lib.mkdir()
        self.write_names(NAMES)

    def write_names(self, names: dict) -> None:
        (self.work / "names.json").write_text(json.dumps(names), encoding="utf-8")

    def report(self, stem: str) -> dict:
        return json.loads((self.work / "remux" / stem / "report.json").read_text(encoding="utf-8"))

    def lib_files(self) -> list[str]:
        return sorted(p.relative_to(self.lib).as_posix() for p in self.lib.rglob("*") if p.is_file())


class Name(Base):
    def test_dry_run_moves_nothing_then_name_and_verify(self):
        code, out, err = cli("name", self.work, self.lib, "--dry-run")
        self.assertEqual((code, err), (0, ""), err)
        self.assertIn("Dry run: nothing moved.", out)
        self.assertEqual(self.lib_files(), [])
        self.assertFalse((self.work / "name.json").exists())

        code, out, err = cli("name", self.work, self.lib)
        self.assertEqual((code, err), (0, ""), err)
        self.assertEqual(self.lib_files(), sorted(PLACED.values()))
        for stem, rel in PLACED.items():
            self.assertEqual(sha(self.lib / rel), self.report(stem)["sha256"])
        self.assertEqual(list((self.work / "out").iterdir()), [])
        record = json.loads((self.work / "name.json").read_text(encoding="utf-8"))
        self.assertEqual([(f["stem"], f["dst"], f["placed"]) for f in record["files"]],
                         [(s, d, True) for s, d in PLACED.items()])
        code, out, err = cli("verify", self.work)
        self.assertEqual((code, err), (0, ""), err)
        self.assertTrue(json.loads((self.work / "verify.json").read_text(encoding="utf-8"))["ok"])

    def test_parts_of_one_title_share_the_movie_role(self):
        for old, new in (("title_01", "title_01_part1"), ("title_02", "title_01_part2")):
            (self.work / "out" / f"{old}.mkv").rename(self.work / "out" / f"{new}.mkv")
            (self.work / "remux" / old).rename(self.work / "remux" / new)
        names = copy.deepcopy(NAMES)
        names["files"] = {"title_01_part1": {"role": "movie"}, "title_01_part2": {"role": "movie"},
                          "title_03": {"role": "skip"}}
        record = namer.plan(self.work, self.lib, names)
        self.assertEqual([f["dst"] for f in record["files"]],
                         [f"{FOLDER}/Example Film (2024) {{imdb-tt0000001}} - part{k}.mkv" for k in (1, 2)])

    def test_a_name_plex_renamer_reads_differently_is_refused(self):
        names = dict(NAMES, title="Example Film Extended")
        self.write_names(names)
        code, _, err = cli("name", self.work, self.lib)
        self.assertEqual(code, 1)
        self.assertIn("plex-renamer reads these names differently", err)
        self.assertIn("editions ['Extended']", err)
        self.assertEqual(self.lib_files(), [])
        self.assertFalse((self.work / "name.json").exists())
        names = dict(NAMES, edition="Extended")
        record = namer.plan(self.work, self.lib, names)
        self.assertTrue(record["folder"].endswith("{edition-Extended}"))

    def test_bad_names_are_refused(self):
        def changed(**kw):
            names = copy.deepcopy(NAMES)
            for path, value in kw.items():
                *keys, last = path.split("__")
                d = names
                for k in keys:
                    d = d[k]
                if value is None:
                    del d[last]
                else:
                    d[last] = value
            return names

        cases = {
            "a file left out": changed(files__title_03=None),
            "a file not in out": changed(files__title_09={"role": "skip"}),
            "an unknown role": changed(files__title_02__role="bonus"),
            "an unknown extras folder": changed(files__title_02__folder="Bloopers"),
            "an empty label": changed(files__title_02__label=" "),
            "two extras with one name": changed(files__title_03={"role": "extra", "folder": "featurette",
                                                                 "label": "making of"}),
            "two titles as the movie": changed(files__title_02={"role": "movie"}),
            "no movie": changed(files__title_01={"role": "skip"}),
            "a bad imdb id": changed(imdb="12345"),
            "two ids": changed(tmdb="603"),
            "a year as text": changed(year="2024"),
            "a category with a slash": changed(category="Movies/Kids"),
            "no title": changed(title=None),
        }
        for why, names in cases.items():
            with self.subTest(why):
                with self.assertRaises(namer.PlaceError):
                    namer.plan(self.work, self.lib, names)
        self.assertEqual(self.lib_files(), [])

    def test_an_existing_file_is_never_overwritten(self):
        there = self.lib / PLACED["title_02"]
        there.parent.mkdir(parents=True)
        there.write_bytes(b"keep")
        code, _, err = cli("name", self.work, self.lib)
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        self.assertEqual(there.read_bytes(), b"keep")
        self.assertEqual(self.lib_files(), [PLACED["title_02"]])
        self.assertEqual(len(list((self.work / "out").iterdir())), 3)

    def test_an_output_changed_after_remux_stops_everything(self):
        flip(self.work / "out" / "title_03.mkv")
        code, _, err = cli("name", self.work, self.lib)
        self.assertEqual(code, 1)
        self.assertIn("title_03.mkv changed since it was checked; nothing was moved", err)
        self.assertEqual(self.lib_files(), [])
        self.assertFalse((self.work / "name.json").exists())

    def test_a_second_run_is_refused(self):
        self.assertEqual(cli("name", self.work, self.lib)[0], 0)
        before = self.lib_files()
        self.assertEqual(cli("name", self.work, self.lib)[0], 1)
        self.assertEqual(self.lib_files(), before)


class AcrossFileSystems(Base):
    def test_the_copy_is_hashed_before_the_source_goes(self):
        with mock.patch.object(namer, "_same_device", return_value=False):
            code, _, err = cli("name", self.work, self.lib)
        self.assertEqual((code, err), (0, ""), err)
        self.assertEqual(self.lib_files(), sorted(PLACED.values()))
        self.assertEqual(list((self.work / "out").iterdir()), [])
        self.assertEqual(cli("verify", self.work)[0], 0)

    def test_a_copy_damaged_on_write_keeps_the_source(self):
        real = copier.copy_file

        def damaged(src, dst):
            result = real(src, dst)        # the hash of what was read is still right
            flip(dst)
            return result

        with mock.patch.object(namer, "_same_device", return_value=False), \
                mock.patch.object(namer.copier, "copy_file", damaged):
            code, _, err = cli("name", self.work, self.lib)
        self.assertEqual(code, 1)
        self.assertIn("does not match; the source is kept", err)
        self.assertTrue((self.work / "out" / "title_01.mkv").is_file())
        self.assertEqual(self.lib_files(), [])


class Verify(Base):
    def setUp(self):
        super().setUp()
        self.assertEqual(cli("name", self.work, self.lib)[0], 0)

    def test_one_altered_file_fails(self):
        placed = self.lib / PLACED["title_03"]
        flip(placed)
        code, _, err = cli("verify", self.work)
        self.assertEqual(code, 1)
        self.assertIn(f"{PLACED['title_03']}: changed since it was checked", err)
        flip(placed)
        self.assertEqual(cli("verify", self.work)[0], 0, "the same file flipped back passes")

    def test_a_missing_file_fails(self):
        (self.lib / PLACED["title_02"]).unlink()
        code, _, err = cli("verify", self.work)
        self.assertEqual(code, 1)
        self.assertIn(f"{PLACED['title_02']}: missing", err)

    def test_a_stray_video_in_the_folder_fails_the_read_back(self):
        stray = self.lib / FOLDER / "random video.mkv"
        stray.write_bytes(b"\0" * 1024)
        code, _, err = cli("verify", self.work)
        self.assertEqual(code, 1)
        self.assertIn("plex-renamer: it finds 2 titles", err)
        stray.unlink()
        self.assertEqual(cli("verify", self.work)[0], 0)

    def test_no_name_record(self):
        (self.work / "name.json").unlink()
        code, _, err = cli("verify", self.work)
        self.assertEqual(code, 1)
        self.assertIn("run name first", err)


if __name__ == "__main__":
    unittest.main()
