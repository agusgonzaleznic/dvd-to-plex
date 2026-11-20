"""Disc files: links and special files are refused, an IFO is capped, every pack is checked on copy."""
import contextlib
import hashlib
import io
import os
import pathlib
import plistlib
import signal
import subprocess
import tempfile
import unittest
from unittest import mock

from disc import sample_disc
from dvdtoplex import copier, ifo, scramble, source, vob
from dvdtoplex.cli import REFUSED, main

PACKS = 10001     # above the 4096-pack sample limit, so the sample steps over every other pack


def pack(scrambled: bool = False, fill: int = 0) -> bytes:
    """One 2048-byte pack: an MPEG-2 pack header, then a video PES packet with the scramble bits."""
    b = bytearray([fill]) * scramble.SECTOR
    b[0:4] = b"\x00\x00\x01\xba"
    b[13] = 0xF8
    b[14:18] = b"\x00\x00\x01\xe0"
    b[18:20] = b"\x07\xec"
    b[20] = 0x80 | (0x10 if scrambled else 0)
    return bytes(b)


def vob_bytes(packs: int, bad: tuple[int, ...] = (), tail: int = 0) -> bytes:
    good, evil = pack(), pack(True)
    return b"".join(evil if k in bad else good for k in range(packs)) + b"\x07" * tail


def run_cli(*args) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in args])
    return code, out.getvalue(), err.getvalue()


class Folder(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)
        self.disc = self.tmp / "VIDEO_TS"
        self.disc.mkdir()
        self.work = self.tmp / "work"

    def plain_disc(self, vob_data: bytes | None = None) -> None:
        (self.disc / "VIDEO_TS.IFO").write_bytes(b"DVDVIDEO-VMG" + bytes(100))
        (self.disc / "VTS_01_0.IFO").write_bytes(b"DVDVIDEO-VTS" + bytes(100))
        (self.disc / "VTS_01_0.BUP").write_bytes(b"DVDVIDEO-VTS" + bytes(100))
        (self.disc / "VTS_01_1.VOB").write_bytes(vob_bytes(20) if vob_data is None else vob_data)

    def outside(self, data: bytes = b"SECRET outside the disc folder") -> pathlib.Path:
        p = self.tmp / "outside.bin"
        p.write_bytes(data)
        return p


class Links(Folder):
    def test_copy_refuses_a_symlink_and_a_regular_disc_still_copies(self):
        self.plain_disc()
        manifest = copier.copy(self.disc, self.work)          # control
        self.assertEqual(len(manifest["files"]), 4)
        self.assertEqual((self.work / "VIDEO_TS" / "VTS_01_0.BUP").read_bytes(),
                         (self.disc / "VTS_01_0.BUP").read_bytes())
        (self.disc / "VTS_01_0.BUP").unlink()
        (self.disc / "VTS_01_0.BUP").symlink_to(self.outside())
        work2 = self.tmp / "work2"
        with self.assertRaises(copier.CopyError) as e:
            copier.copy(self.disc, work2)
        self.assertIn("VTS_01_0.BUP is not a regular file", str(e.exception))
        self.assertFalse((work2 / "VIDEO_TS").exists())
        self.assertFalse((work2 / "copy.json").exists())

    def test_copy_refuses_a_special_file_without_waiting_on_it(self):
        self.plain_disc()
        os.mkfifo(self.disc / "VTS_01_9.BUP")
        with self.assertRaises(copier.CopyError):
            copier.copy(self.disc, self.work)
        self.assertFalse((self.work / "VIDEO_TS").exists())

    def test_readers_refuse_a_fifo_without_waiting_on_it(self):
        self.plain_disc()
        video_ts, _ = sample_disc()             # built before the alarm: it can take a while
        (self.disc / "VTS_01_0.IFO").write_bytes((video_ts / "VTS_01_0.IFO").read_bytes())
        os.mkfifo(self.disc / "VTS_02_0.IFO")
        os.mkfifo(self.disc / "VTS_01_2.VOB")

        def stuck(*_):
            raise TimeoutError("a reader waited on a FIFO")

        old = signal.signal(signal.SIGALRM, stuck)
        signal.alarm(10)            # a reader that blocks on the FIFO fails here instead of hanging
        try:
            self.assertTrue(ifo.read_vts(self.disc, 1).pgcs)                   # control: a regular IFO
            with self.assertRaisesRegex(ifo.IfoError, "not a regular file"):
                ifo.read_vts(self.disc, 2)
            self.assertEqual(scramble.count_file(self.disc / "VTS_01_1.VOB").checked, 20)  # control
            with self.assertRaisesRegex(source.SourceError, "not a regular file"):
                scramble.count_file(self.disc / "VTS_01_2.VOB")
            with self.assertRaisesRegex(vob.VobError, "not a regular file"):
                vob.title_vobs(self.disc, 1)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)

    def test_copy_does_not_follow_a_symlinked_vob(self):
        self.plain_disc()
        (self.disc / "VTS_01_1.VOB").unlink()
        (self.disc / "VTS_01_1.VOB").symlink_to(self.outside(vob_bytes(20)))
        code, out, err = run_cli("copy", self.disc, self.work)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("not a regular file", err)
        self.assertFalse((self.work / "VIDEO_TS").exists())

    def test_error_does_not_print_a_name_with_terminal_escapes(self):
        self.plain_disc()
        name = "VTS_09_0\x1b[31m.BUP"
        (self.disc / name).symlink_to(self.outside())
        with self.assertRaises(copier.CopyError) as e:
            copier.copy(self.disc, self.work)
        self.assertNotIn("\x1b", str(e.exception))
        self.assertIn("\\x1b", str(e.exception))

    def test_ifo_readers_refuse_a_symlink_and_read_a_regular_file(self):
        video_ts, _ = sample_disc()
        for name in ("VIDEO_TS.IFO", "VTS_01_0.IFO"):
            (self.disc / name).write_bytes((video_ts / name).read_bytes())
        self.assertTrue(ifo.read_vmg(self.disc))              # control
        self.assertTrue(ifo.read_vts(self.disc, 1).pgcs)
        for name in ("VIDEO_TS.IFO", "VTS_01_0.IFO"):
            (self.disc / name).unlink()
            (self.disc / name).symlink_to(video_ts / name)    # a perfectly valid IFO, but a link
        with self.assertRaisesRegex(ifo.IfoError, "symbolic link"):
            ifo.read_vmg(self.disc)
        with self.assertRaisesRegex(ifo.IfoError, "symbolic link"):
            ifo.read_vts(self.disc, 1)

    def test_ifo_reader_refuses_a_folder_named_like_an_ifo(self):
        (self.disc / "VTS_01_0.IFO").mkdir()
        with self.assertRaisesRegex(ifo.IfoError, "not a regular file"):
            ifo.read_vts(self.disc, 1)

    def test_vob_set_refuses_a_symlink_and_reads_a_regular_file(self):
        self.plain_disc()
        parts = vob.title_vobs(self.disc, 1)                  # control
        self.assertEqual(len(b"".join(vob.read_sectors(parts, 0, 4))), 5 * 2048)
        (self.disc / "VTS_01_2.VOB").symlink_to(self.outside(vob_bytes(4)))
        with self.assertRaisesRegex(vob.VobError, "not a regular file"):
            vob.title_vobs(self.disc, 1)
        with self.assertRaisesRegex(vob.VobError, "symbolic link"):
            list(vob.read_sectors([self.disc / "VTS_01_2.VOB"], 0, 1))

    def test_scramble_reader_refuses_a_symlink(self):
        self.plain_disc()
        self.assertEqual(scramble.count_file(self.disc / "VTS_01_1.VOB").checked, 20)   # control
        link = self.disc / "VTS_01_2.VOB"
        link.symlink_to(self.outside(vob_bytes(4)))
        with self.assertRaisesRegex(source.SourceError, "symbolic link"):
            scramble.count_file(link)
        with self.assertRaises(source.SourceError):
            scramble.count_disc(self.disc)

    def test_a_video_ts_link_inside_the_given_folder_is_not_followed(self):
        self.plain_disc()
        parent = self.tmp / "parent"
        parent.mkdir()
        (parent / "VIDEO_TS").symlink_to(self.disc)
        with self.assertRaisesRegex(source.SourceError, "no VIDEO_TS folder"):
            source.find_video_ts(parent)
        real = self.tmp / "parent2"
        real.mkdir()
        (real / "VIDEO_TS").mkdir()
        (real / "VIDEO_TS" / "VIDEO_TS.IFO").write_bytes(b"x")
        self.assertEqual(source.find_video_ts(real), real / "VIDEO_TS")                  # control

    def test_the_path_given_on_the_command_line_may_be_a_link(self):
        self.plain_disc()
        alias = self.tmp / "alias"
        alias.symlink_to(self.tmp)                            # the given path itself is trusted
        with source.opened(alias) as found:
            self.assertEqual(found, alias / "VIDEO_TS")
            self.assertEqual(len(copier.copy(found, self.work)["files"]), 4)
        named = self.tmp / "elsewhere" / "VIDEO_TS"
        named.parent.mkdir()
        named.symlink_to(self.disc)                           # trusted too, when it is the given path
        with source.opened(named) as found:
            self.assertEqual(found, named)


class IfoSize(Folder):
    def test_oversized_ifo_is_refused_before_it_is_read(self):
        big = self.disc / "VIDEO_TS.IFO"
        with open(big, "wb") as f:
            f.write(b"DVDVIDEO-VMG")
            f.truncate((16 << 20) + 1)                       # sparse: no disk used
        with self.assertRaisesRegex(ifo.IfoError, "refused"):
            ifo.read_vmg(self.disc)
        small = self.disc / "VIDEO_TS.IFO"
        small.write_bytes(b"DVDVIDEO-VMG" + bytes(5000))      # control: under the cap, read normally
        with self.assertRaises(ifo.IfoError) as e:
            ifo.read_vmg(self.disc)
        self.assertNotIn("refused", str(e.exception))         # it parses as far as its tables allow

    def test_signature_is_checked_before_the_rest_is_read(self):
        (self.disc / "VTS_01_0.IFO").write_bytes(b"NOT-A-DVD-IFO" + bytes(1 << 20))
        real, reads = getattr(source, "open_regular", None), []

        class Spy:
            def __init__(self, f):
                self.f = f

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.f.close()

            def fileno(self):
                return self.f.fileno()

            def read(self, n=-1):
                reads.append(n)
                return self.f.read(n)

        with mock.patch.object(source, "open_regular", lambda *a, **k: Spy(real(*a, **k)), create=True):
            with self.assertRaisesRegex(ifo.IfoError, "does not start with DVDVIDEO-VTS"):
                ifo.read_vts(self.disc, 1)
        self.assertEqual(reads, [12])


class EveryPack(Folder):
    def test_scrambled_pack_off_the_sample_grid_is_refused_and_removed(self):
        self.plain_disc(vob_bytes(PACKS, bad=(3,)))
        self.assertEqual(scramble.count_disc(self.disc).scrambled, 0, "the sample misses pack 3")
        self.work.mkdir()
        (self.work / "notes.txt").write_text("mine")
        (self.work / "copy.json").write_text("old manifest")
        code, out, err = run_cli("copy", self.disc, self.work)
        self.assertEqual((code, out, err.strip()), (REFUSED, "", scramble.REFUSAL))
        self.assertFalse((self.work / "VIDEO_TS").exists())
        self.assertEqual((self.work / "notes.txt").read_text(), "mine")
        self.assertEqual((self.work / "copy.json").read_text(), "old manifest")
        self.assertEqual(sorted(p.name for p in self.work.iterdir()), ["copy.json", "notes.txt"])

    def test_refusal_removes_work_folders_this_run_made_and_only_those(self):
        self.plain_disc(vob_bytes(PACKS, bad=(1,)))
        work = self.tmp / "a" / "b" / "work"
        with self.assertRaises(copier.CopyError):
            copier.copy(self.disc, work)
        self.assertFalse((self.tmp / "a").exists())
        (self.tmp / "keep").mkdir()
        (self.tmp / "keep" / "other.txt").write_text("x")
        with self.assertRaises(copier.CopyError):
            copier.copy(self.disc, self.tmp / "keep" / "work")
        self.assertEqual([p.name for p in (self.tmp / "keep").iterdir()], ["other.txt"])

    def test_an_unscrambled_vob_with_a_partial_last_pack_is_copied(self):
        data = vob_bytes(PACKS, tail=700)
        self.plain_disc(data)
        manifest = copier.copy(self.disc, self.work)
        got = {f["name"]: f for f in manifest["files"]}["VTS_01_1.VOB"]
        self.assertEqual(got["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual((self.work / "VIDEO_TS" / "VTS_01_1.VOB").read_bytes(), data)

    def test_a_scrambled_pack_across_a_chunk_boundary_is_found(self):
        data = vob_bytes(10, bad=(4,))
        src = self.tmp / "x.VOB"
        src.write_bytes(data)
        for chunk in (3000, 2048, 5000, 1 << 20):
            with mock.patch.object(copier, "CHUNK", chunk):
                with self.assertRaises(copier.CopyError, msg=f"chunk {chunk}") as e:
                    copier.copy_file(src, self.tmp / "y.VOB", check_scramble=True)
            self.assertEqual(str(e.exception), scramble.REFUSAL)
            self.assertFalse((self.tmp / "y.VOB.part").exists())
        with mock.patch.object(copier, "CHUNK", 3000):         # control: nothing scrambled
            src.write_bytes(vob_bytes(10))
            copier.copy_file(src, self.tmp / "y.VOB", check_scramble=True)
        self.assertEqual((self.tmp / "y.VOB").read_bytes(), vob_bytes(10))

    def test_packs_that_are_not_video_audio_or_private_are_not_counted(self):
        self.assertEqual(scramble.count_packs(bytes(2048 * 3)), scramble.Count())
        self.assertEqual(scramble.count_packs(pack(True) + b"abc"), scramble.Count(1, 1))


if __name__ == "__main__":
    unittest.main()


class Attach(unittest.TestCase):
    def run_attach(self, answer: bytes) -> pathlib.Path:
        done = subprocess.CompletedProcess(["hdiutil"], 0, answer, b"")
        with mock.patch.object(source.subprocess, "run", return_value=done):
            return source._attach(pathlib.Path("disc.iso"))

    def test_an_hdiutil_answer_that_cannot_be_read_is_one_line(self):
        ok = plistlib.dumps({"system-entities": [{"mount-point": "/Volumes/DISC"}]})
        self.assertEqual(self.run_attach(ok), pathlib.Path("/Volumes/DISC"))      # control
        for answer in (b"not a property list", b"<?xml version='1.0'?><plist><dict>",
                       plistlib.dumps(["a list, not a dict"]),
                       plistlib.dumps({"system-entities": ["not a dict"]})):
            with self.subTest(answer=answer[:30]):
                with self.assertRaises(source.SourceError) as e:
                    self.run_attach(answer)
                self.assertEqual(len(str(e.exception).splitlines()), 1)
                self.assertIn("could not be read", str(e.exception))
