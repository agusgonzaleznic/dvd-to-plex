"""Command line entry point."""
import argparse
import json
import pathlib
import sys

from dvdtoplex import __version__, copier, ifo, parts, remux, sample, scramble, source, survey

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


def _titles(arg: str | None) -> set[int] | None:
    if not arg:
        return None
    try:
        return {int(x) for x in arg.split(",") if x.strip()}
    except ValueError:
        raise SystemExit(f"dvd-to-plex: --titles takes numbers separated by commas, not {arg!r}")


def _remux(args: argparse.Namespace) -> int:
    work = args.work
    video_ts = work / "VIDEO_TS"
    try:
        if (work / "copy.json").is_file() and (problems := copier.verify(work)):
            raise remux.RemuxError("the copy changed since it was made: " + "; ".join(problems))
        found = survey.survey(video_ts, most_per_file=None)
        if found.scramble.scrambled:
            raise remux.RemuxError(scramble.REFUSAL)
        wanted = _titles(args.titles)
        picked = [t for t in found.titles if (t.number in wanted if wanted else
                  not t.problems and t.duplicate_of is None and t.seconds >= args.min_seconds)]
        reports, failed = [], 0
        for t in picked:
            if t.problems:
                print(f"title {t.number}: skipped ({'; '.join(t.problems)})")
                continue
            ts = ifo.read_vts(video_ts, t.title_set)
            for part in parts.plan(video_ts, t.number, t.title_set, ts, ts.pgcs[t.pgc - 1]):
                stem = f"title_{t.number:02d}" + (f"_part{part.number}" if part.of > 1 else "")
                r = remux.remux(video_ts, t, part, work / "remux" / stem, work / "out" / f"{stem}.mkv",
                                prefer_audio=args.prefer_audio)
                reports.append(r)
                bad = [k for k, v in r["checks"].items()
                       if not all(c["ok"] for c in (v if isinstance(v, list) else [v]))]
                failed += not r["ok"]
                print(f"{stem}: " + (f"ok -> {r['output']}" if r["ok"] else
                                     f"FAILED ({', '.join(bad)}); see {work / 'remux' / stem / 'report.json'}"))
        (work / "remux.json").write_text(json.dumps(reports, indent=2, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
    except remux.RemuxError as e:
        refused = str(e) == scramble.REFUSAL
        print(e if refused else f"dvd-to-plex: {e}", file=sys.stderr)
        return REFUSED if refused else 1
    except (ifo.IfoError, OSError, ValueError, RuntimeError) as e:
        print(f"dvd-to-plex: {e}", file=sys.stderr)
        return 1
    return 1 if failed else 0


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
    p = commands.add_parser("remux", help="remux titles of WORK/VIDEO_TS to MKV losslessly and check each",
                            description="Writes WORK/out/title_NN.mkv per title (title_NN_partK.mkv where the "
                                        "timestamps restart), a report per part under WORK/remux and WORK/remux.json. "
                                        "Exit status: 0 all checks passed, 1 a check failed or an error, "
                                        "3 refused (scrambled video).")
    p.add_argument("work", type=pathlib.Path, help="work folder holding VIDEO_TS (as written by copy)")
    p.add_argument("--titles", help="title numbers to remux, e.g. 1,3 (default: every usable title)")
    p.add_argument("--min-seconds", type=float, default=5.0,
                   help="without --titles, skip titles shorter than this (default 5)")
    p.add_argument("--prefer-audio", metavar="LANG", help="two-letter language to make the default audio")
    p.set_defaults(run=_remux)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return args.run(args)
