"""The chain of a subtitle unit's control sequences, on units built here: a normal chain parses, a
chain that overlaps or turns back to a new sequence is refused."""
import dataclasses
import struct
import unittest

from dvdtoplex import spu

BITMAP = bytes([0x9A, 0x13])        # a 4x2 bitmap: top line 1 1 2 2, bottom line 3 3 3 3
CTRL = 4 + len(BITMAP)
SETUP = (bytes([0x03, 0x48, 0x0D])                          # colours 13 0 8 4
         + bytes([0x04, 0xFF, 0xF0])                        # alphas 0 15 15 15
         + bytes([0x05, 0x00, 0xA0, 0x0D, 0x01, 0x40, 0x15])   # x 10-13, y 20-21
         + bytes([0x06]) + struct.pack(">HH", 4, 5)         # top field at 4, bottom at 5
         + bytes([0x01, 0xFF]))                             # start showing
EXPECTED = spu.Spu(x=10, y=20, width=4, height=2, colours=(13, 0, 8, 4), alphas=(0, 15, 15, 15),
                   start=0.0, stop=50 * spu.TICK, forced=False, pixels=bytes([1, 1, 2, 2, 3, 3, 3, 3]))


def unit(body: bytes) -> bytes:
    """The unit's size and control offset, the bitmap, then BODY at CTRL."""
    return struct.pack(">HH", 4 + len(BITMAP) + len(body), CTRL) + BITMAP + body


def seq(delay: int, following: int, commands: bytes) -> bytes:
    return struct.pack(">HH", delay, following) + commands


def overlapping(count: int) -> bytes:
    """COUNT sequences, each one's header hidden in the previous one's 0x06 command, so each
    sequence's commands run over every later sequence to the one 0xFF at the end."""
    body = bytearray()
    for k in range(count - 1):
        body += seq(0, CTRL + 5 * (k + 1), b"\x06")
    body += seq(0, CTRL + 5 * (count - 1), SETUP)           # the last one points to itself
    return unit(bytes(body))


class Chain(unittest.TestCase):
    def test_forward_pointers_ending_on_itself_parse(self):
        second = CTRL + 4 + len(SETUP)
        data = unit(seq(0, second, SETUP) + seq(50, second, b"\x02\xff"))
        self.assertEqual(spu.parse(data), EXPECTED)

    def test_a_gap_and_a_last_pointer_back_to_the_first_parse(self):
        second = CTRL + 4 + len(SETUP) + 3
        data = unit(seq(0, second, SETUP) + b"\x00\x00\x00" + seq(50, CTRL, b"\x02\xff"))
        self.assertEqual(spu.parse(data), EXPECTED)

    def test_a_single_sequence_parses(self):
        data = unit(seq(0, CTRL, SETUP))
        self.assertEqual(spu.parse(data), dataclasses.replace(EXPECTED, stop=None))

    def test_overlapping_sequences_are_refused(self):
        for count in (2, 3, 50):
            with self.subTest(count=count), self.assertRaisesRegex(spu.SpuError, "overlaps or goes back"):
                spu.parse(overlapping(count))

    def test_a_pointer_back_to_a_new_sequence_is_refused(self):
        middle = CTRL + 4 + len(SETUP)                     # a whole sequence, reached only from the last
        last = middle + 6
        body = seq(0, last, SETUP) + seq(50, middle, b"\x02\xff") + seq(50, middle, b"\x02\xff")
        with self.assertRaisesRegex(spu.SpuError, "overlaps or goes back"):
            spu.parse(unit(body))


if __name__ == "__main__":
    unittest.main()
