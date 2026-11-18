"""Command line entry point."""
import argparse
import pathlib
import sys

from dvdtoplex import __version__, sample


def _sample(args: argparse.Namespace) -> int:
    try:
        sample.build(args.dest)
    except (sample.SampleError, OSError) as e:
        print(f"dvd-to-plex: {e}", file=sys.stderr)
        return 1
    print(f"Wrote {args.dest / 'VIDEO_TS'} and {args.dest / 'sample-truth.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dvd-to-plex",
        description="Remux unscrambled DVD-Video to MKV losslessly, with Plex names and verified subtitles.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    p = commands.add_parser("sample", help="author a small synthetic DVD-Video folder to try the tool on")
    p.add_argument("dest", type=pathlib.Path,
                   help="folder to write VIDEO_TS and sample-truth.json into (no VIDEO_TS in it yet)")
    p.set_defaults(run=_sample)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return args.run(args)
