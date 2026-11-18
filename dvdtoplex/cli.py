"""Command line entry point."""
import argparse
import json
import pathlib
import sys

from dvdtoplex import (__version__, assemble, copier, embed, ifo, media, namer, ocr, parts, remux,
                       review, sample, scramble, source, survey, swiftc, verifier)

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


def _names(text: str | None) -> list[str]:
    """The --files list: names of files in WORK/out, comma-separated, with or without .mkv."""
    names = [s.strip() for s in (text or "").split(",") if s.strip()]
    return [n[:-4] if n.lower().endswith(".mkv") else n for n in names]


def _ocr(args: argparse.Namespace) -> int:
    files = sorted((args.work / "out").glob("*.mkv"))
    if args.files:
        wanted = set(_names(args.files))
        if unknown := wanted - {f.stem for f in files}:
            print(f"dvd-to-plex: not in {args.work / 'out'}: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 1
        files = [f for f in files if f.stem in wanted]
    try:
        for mkv in files:
            results = ocr.ocr_file(mkv, args.work, language=args.lang)
            if not results:
                print(f"{mkv.stem}: no disc subtitles")
            for r in results:
                read = [c for c in r["cues"] if "first" in c]
                print(f"{mkv.stem} s{r['track']} ({r['language']}): {len(read)} cues read, "
                      f"{sum(c['agree'] for c in read)} where both readings agree"
                      + (f", {len(r['cues']) - len(read)} units not decoded" if len(read) < len(r["cues"]) else ""))
    except (ocr.OcrError, swiftc.SwiftError, media.MediaError, OSError, ValueError) as e:
        print(f"dvd-to-plex: {e}", file=sys.stderr)
        return 1
    return 0


def _review_prep(args: argparse.Namespace) -> int:
    try:
        if args.redo:
            entry = review.redo(args.work, args.redo, plants=args.plants)
            print(f"{args.redo}: attempt {entry['attempt']}, {entry['plants']} controls; earlier answers kept "
                  f"as answers.attempt{entry['attempt'] - 1}.json")
            return 0
        for s in review.prepare(args.work, size=args.chunk, plants=args.plants):
            print(f"{s['track']}: {len(s['chunks'])} chunks in {args.work / 'review' / s['track']}, "
                  f"{sum(c['plants'] for c in s['chunks'])} controls")
    except (review.ReviewError, OSError, ValueError) as e:
        print(f"dvd-to-plex: {e}", file=sys.stderr)
        return 1
    return 0


def _assemble(args: argparse.Namespace) -> int:
    try:
        reports = assemble.assemble(args.work)
    except (assemble.AssembleError, review.ReviewError, OSError, ValueError, KeyError) as e:
        print(f"dvd-to-plex: {e}", file=sys.stderr)
        return 1
    for r in reports:
        for c in r["chunks"]:
            if not c["ok"]:
                print(f"{r['track']} chunk {c['chunk']}: {c['failed']} of {c['controls']} controls failed; "
                      f"redo it one model up (review-prep --redo {r['track']}/{c['chunk']})", file=sys.stderr)
        if r["ok"]:
            print(f"{r['track']}: every control restored; {r['cues']} cues in {args.work / r['srt']}")
    return 0 if all(r["ok"] for r in reports) else 1


def _embed(args: argparse.Namespace) -> int:
    files = _names(args.files) or None
    try:
        reports = embed.embed(args.work, files)
    except (embed.EmbedError, namer.PlaceError, media.MediaError, OSError, ValueError) as e:
        print(f"dvd-to-plex: {e}", file=sys.stderr)
        return 1
    for r in reports:
        if r["ok"]:
            print(f"{r['file']}: {len(r['tracks'])} text track{'s' if len(r['tracks']) != 1 else ''} added "
                  f"({', '.join(t['language'] for t in r['tracks'])}); the original is in "
                  f"{args.work / r['original']['retired']}")
        else:
            print(f"{r['file']}: not changed, the new file failed its checks: " + "; ".join(r["problems"]),
                  file=sys.stderr)
    return 0 if all(r["ok"] for r in reports) else 1


def _name(args: argparse.Namespace) -> int:
    try:
        record = namer.place(args.work, args.library, args.names or args.work / "names.json",
                             dry_run=args.dry_run)
    except (namer.PlaceError, copier.CopyError, OSError, ValueError) as e:
        print(f"dvd-to-plex: {e}", file=sys.stderr)
        return 1
    for f in record["files"]:
        print(f"{f['stem']} -> {pathlib.Path(record['library']) / f['dst']}")
    print("Dry run: nothing moved." if args.dry_run else f"Moves recorded in {args.work / 'name.json'}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    try:
        result = verifier.verify(args.work)
    except (verifier.VerifyError, OSError, ValueError) as e:
        print(f"dvd-to-plex: {e}", file=sys.stderr)
        return 1
    for problem in result["problems"]:
        print(problem, file=sys.stderr)
    print(f"{result['folder']}: " + ("ok, every file as it was checked" if result["ok"] else
                                     f"{len(result['problems'])} problems; see {args.work / 'verify.json'}"))
    return 0 if result["ok"] else 1


NAMES_HELP = """The names file (default WORK/names.json) is JSON:
  {"category": "Movies", "title": "Example Film", "year": 2024, "imdb": "tt0000000",
   "edition": "Special Edition",
   "files": {"title_01": {"role": "movie"},
             "title_02": {"role": "extra", "folder": "Featurettes", "label": "Making Of"},
             "title_03": {"role": "skip"}}}
year, imdb (or tmdb) and edition are optional. Every file in WORK/out needs a role. Parts of one
title (title_01_part1, title_01_part2) can all be the movie. Extras folders:
  """ + ", ".join(namer.FOLDERS) + """.
The film's SRTs in WORK/subs go beside it as <film>.<lang>.srt (.forced when the disc track was);
an extra's must be embedded first (embed), because Plex ignores sidecars next to a local extra.
Nothing moves until the names read back through plex-renamer and every file matches its remux (or
embed) report; WORK/name.json records each move. Exit status: 0 named, 1 an error or a failed check."""


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
    p = commands.add_parser("ocr", help="read the disc subtitles of WORK/out's files as text with macOS Vision",
                            description="Decodes each subtitle of each VobSub track to WORK/ocr/<file>/s<N>/NNNN.png "
                                        "and reads it twice (as is; twice the size without language correction) "
                                        "into WORK/ocr/<file>/s<N>.json. Exit status: 0 read, 1 an error.")
    p.add_argument("work", type=pathlib.Path, help="work folder holding out/ (as written by remux)")
    p.add_argument("--files", help="file names in WORK/out to read, e.g. title_01,title_02 (.mkv optional; default: all)")
    p.add_argument("--lang", metavar="LANG", help="two-letter language for every track, instead of its tag")
    p.set_defaults(run=_ocr)
    p = commands.add_parser("review-prep", help="cut the OCR text into proofreading chunks with planted controls",
                            description="Writes WORK/review/<file>.s<N>/chunk_NN/ with cues.json and contact "
                                        "sheets; the proofreader answers in chunk_NN/answers.json as "
                                        '{"cues": [{"n": 1, "text": "...", "note": ""}]}. The key goes to '
                                        "DVDTOPLEX_KEYS or ~/Library/Application Support/dvd-to-plex/keys, "
                                        "outside the work folder. Exit status: 0 prepared, 1 an error.")
    p.add_argument("work", type=pathlib.Path, help="work folder holding ocr/ (as written by ocr)")
    p.add_argument("--chunk", type=int, default=review.CHUNK, help=f"cues per chunk (default {review.CHUNK})")
    p.add_argument("--plants", type=int, default=review.PLANTS,
                   help=f"controls planted per chunk (default {review.PLANTS})")
    p.add_argument("--redo", metavar="TRACK/N", help="fresh controls for one chunk, e.g. title_01.s0/3")
    p.set_defaults(run=_review_prep)
    p = commands.add_parser("assemble", help="score the proofreading against its controls and write the SRTs",
                            description="Only a chunk whose every control was restored passes; a track whose "
                                        "chunks all pass is written to WORK/subs/<file>.s<N>.<lang>.srt. "
                                        "Exit status: 0 every chunk passed, 1 a chunk failed or an error.")
    p.add_argument("work", type=pathlib.Path, help="work folder holding review/ (as written by review-prep)")
    p.set_defaults(run=_assemble)
    p = commands.add_parser("embed", help="add the SRTs to their MKV as text tracks, the original kept",
                            description="Adds WORK/subs/<file>.s<N>.<lang>.srt to WORK/out/<file>.mkv as SubRip "
                                        "tracks, every other stream copied as it is. The new file must keep every "
                                        "original stream's packets and properties and read back as its SRTs before "
                                        "it replaces the original, which moves to WORK/retired-YYYY-MM-DD/. "
                                        "Exit status: 0 every file done, 1 a file failed its checks or an error.")
    p.add_argument("work", type=pathlib.Path, help="work folder holding out/, remux/ and subs/")
    p.add_argument("--files", help="file names in WORK/out, e.g. title_02,title_03 (.mkv optional; default: every file with an SRT)")
    p.set_defaults(run=_embed)
    p = commands.add_parser("name", help="move WORK/out's files into a Plex library under plex-renamer's names",
                            description=NAMES_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("work", type=pathlib.Path, help="work folder holding out/ and remux/ (as written by remux)")
    p.add_argument("library", type=pathlib.Path, help="library root; files go to LIBRARY/CATEGORY/<title folder>")
    p.add_argument("--names", type=pathlib.Path, help="names file (default WORK/names.json)")
    p.add_argument("--dry-run", action="store_true", help="check and print the names without moving anything")
    p.set_defaults(run=_name)
    p = commands.add_parser("verify", help="check the named files against their remux or embed reports and plex-renamer",
                            description="Re-hashes every file WORK/name.json lists at its place in the library "
                                        "and reads the title folder back through plex-renamer; writes "
                                        "WORK/verify.json. Exit status: 0 all as checked, 1 a problem.")
    p.add_argument("work", type=pathlib.Path, help="work folder holding name.json (as written by name)")
    p.set_defaults(run=_verify)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return args.run(args)
