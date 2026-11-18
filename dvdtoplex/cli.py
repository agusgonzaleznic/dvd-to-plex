"""Command line entry point."""
import argparse
import json
import pathlib
import sys

from dvdtoplex import __version__, copier, ifo, sample, scramble, source, survey

# Exit codes: 0 done, 1 an error, 3 refused because the disc is scrambled.
REFUSED = 3


def _sample(args: argparse.Namespace) -> int:
    try:
        sample.build(args.dest)
    except (sample.SampleError, OSError) as e:
        print(f"dvd-to-plex: {e}", file=sys.stderr)
        return 1
    print(f"Wrote {args.dest / 'VIDEO_TS'} and {args.dest / 'sample-truth.json'}")
    return 0


def _inspect(args: argparse.Namespace) -> int:
    try:
        with source.opened(args.source) as video_ts:
            found = survey.survey(video_ts, most_per_file=None if args.full_scan else 4096)
    except (source.SourceError, ifo.IfoError, OSError) as e:
        print(f"dvd-to-plex: {e}", file=sys.stderr)
        return 1
    if found.scramble.scrambled:
        print(scramble.REFUSAL, file=sys.stderr)
        return REFUSED
    print(json.dumps(found.to_json(), indent=2, ensure_ascii=False) if args.json else survey.describe(found))
    return 0


def _copy(args: argparse.Namespace) -> int:
    try:
        with source.opened(args.source) as video_ts:
            manifest = copier.copy(video_ts, args.work)
    except copier.CopyError as e:
        refused = str(e) == scramble.REFUSAL
        print(e if refused else f"dvd-to-plex: {e}", file=sys.stderr)
        return REFUSED if refused else 1
    except (source.SourceError, OSError) as e:
        print(f"dvd-to-plex: {e}", file=sys.stderr)
        return 1
    total = sum(f["bytes"] for f in manifest["files"])
    print(f"Copied {len(manifest['files'])} files ({total >> 20} MiB) to {args.work / 'VIDEO_TS'}; "
          f"checksums in {args.work / 'copy.json'}")
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
    p = commands.add_parser("inspect", help="list a disc's titles, streams and chapters; refuse a scrambled one",
                            description="Exit status: 0 listed, 1 error, 3 refused (scrambled video).")
    p.add_argument("source", type=pathlib.Path, help="mounted disc, folder holding VIDEO_TS, or .iso image")
    p.add_argument("--json", action="store_true", help="print the survey as JSON")
    p.add_argument("--full-scan", action="store_true",
                   help="check every pack for scrambling instead of up to 4096 per VOB file")
    p.set_defaults(run=_inspect)
    p = commands.add_parser("copy", help="copy a disc's VIDEO_TS into a work folder, stopping on any read error",
                            description="Exit status: 0 copied, 1 error, 3 refused (scrambled video).")
    p.add_argument("source", type=pathlib.Path, help="mounted disc, folder holding VIDEO_TS, or .iso image")
    p.add_argument("work", type=pathlib.Path, help="work folder; VIDEO_TS and copy.json are written into it")
    p.set_defaults(run=_copy)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return args.run(args)
