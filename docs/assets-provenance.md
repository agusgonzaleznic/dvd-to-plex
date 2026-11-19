# Asset provenance

This page says where every non-code asset in the repository comes from. Nothing third-party is
vendored: the repository holds no font file, no image from elsewhere and no media file, and the
command-line tool makes no network call when it runs. Apart from code and text, the only file in the
repository is the emblem.

## Emblem

- **File:** `assets/emblem.svg`, a 256 by 256 SVG.
- **What it holds:** a rounded square in `#1A1D23`, a circle outline in `#FAFAFA` (the disc), a
  short arc in `#DE3412` (the highlighted track) and a play triangle in `#FAFAFA`. It is four shapes
  written as SVG source. It embeds no bitmap and uses no font.
- **Used by:** the header of `README.md`, which loads it from the main branch on GitHub.
- **Source:** the repository records no source other than the file itself, and nothing in it comes
  from a library or an icon set.

## The synthetic sample disc

`dvd-to-plex sample DEST` writes the sample disc, and `tests/disc.py` builds it once per test run.
No disc, picture, sound or subtitle is stored in the repository: everything is made on your machine
when the command runs, from the code in `dvdtoplex/sample.py`. The module's docstring says the same:
everything on the disc is made there.

| Part | Where it comes from |
|---|---|
| Pictures | ffmpeg's `testsrc` test pattern, 720 by 576 at 25 frames a second, encoded with the `pal-dvd` target |
| Sound | ffmpeg's `sine` generator at four fixed tones, 330, 440, 550 and 660 Hz, encoded as AC-3 at 192 kb/s. There is no speech and no music |
| Subtitle text | Six German lines written for this repository, held in `MAIN_LINES` and `EXTRA_LINES` in `dvdtoplex/sample.py`. The lines are not quoted from any film or book |
| Subtitle pictures | One PNG per line, rendered by `dvdtoplex/swift/render_text.swift`, then muxed in as subpictures by `spumux` |
| Disc structure | Authored by `dvdauthor` from an XML file that `dvdtoplex/sample.py` writes: three titles, two title sets, the chapters and the language tags |
| `sample-truth.json` | Written by `dvdtoplex/sample.py` from the same tables. It lists every title, track and subtitle line as it was written, and the tests compare each stage with it |

`spumux` and `dvdauthor` are needed only to build the sample disc: `dvdtoplex/sample.py` calls them,
and no other command does. CI takes both from Homebrew's `dvdauthor` package.

### Subtitle pictures and fonts

`dvdtoplex/swift/render_text.swift` draws each line with Apple's AppKit: white text (or the colour
passed on the command line, yellow for title 2) with a black outline on a transparent background, in
the macOS system bold font at 34 points, with anti-aliasing and font smoothing off, so the image
holds at most three colours. The font is the one the operating system provides. The repository holds
no font file and does not name a typeface, so the look of the pictures depends on the macOS version
that renders them. The words the pictures carry do not.

`dvdtoplex/swiftc.py` compiles the helper with the system `swiftc`, into a file named after the
source's hash, so a changed source is rebuilt.

The contact sheets that the proofreading step builds (`dvdtoplex/sheets.py`) label each row with a
digit drawn from a 5 by 7 pixel font written in that module, so no image library or font file is
involved.

### Other test fixtures

- `tests/swift/render_and_read.swift` renders one line of text in the macOS system font at 40 points
  onto a black bitmap and reads it back with macOS Vision. It is run by `tests/test_runner.py`, with
  the line `Größere Straße, über die Brücke!` and with an empty line as the control.
- `tests/test_runner.py` also asks the macOS `say` command for German speech with the sentence
  `Guten Tag, wir fahren nach Hause.`, checks that the file is longer than a second, and throws it
  away with its temporary folder. The voice is one that is installed on the machine. The speech is
  never stored and is not part of the sample disc.
- `tests/test_plugin.py` uses made-up paths such as `/Volumes/Media/dvd-to-plex/Film`. Nothing there
  exists on disk.

### Regenerating

```bash
dvd-to-plex sample dvd-sample
```

From a clone, `python3 -m dvdtoplex sample dvd-sample` does the same. The folder must not already
hold a `VIDEO_TS`. The command needs `ffmpeg`, `spumux`, `dvdauthor` and `swiftc` on your PATH and
stops with a message that names the ones that are missing.

## Downloaded at run time or in CI

- **At run time:** nothing. A search of `dvdtoplex/` and `plugin/`, and of the `plexrenamer` package
  it names files with, finds no network call. The tool never fetches subtitles or metadata from the
  web. The plugin's proofreader is a Claude Code agent, so what it reads (its chunk's text and the
  contact sheets of subtitle images) goes to the model the way anything a Claude Code session reads
  does.
- **In CI:** `ffmpeg` and `dvdauthor` from Homebrew, the `swift` and `say` commands from the macOS
  runner image, `plex-renamer` installed from `requirements/runtime.txt` with its hashes checked,
  and `hatchling` and its dependencies from `requirements/build.txt` with theirs. The workflows call
  shared reusable workflows and two actions, each pinned to a full commit SHA. See
  [`development.md`](development.md#continuous-integration).
- **Not downloaded:** fonts, icons, images and media. Nothing is fetched into the repository.

## What is excluded from the repository

`.gitignore` keeps media out: `VIDEO_TS/`, `*.iso`, `*.vob`, `*.IFO`, `*.BUP`, `*.mkv`, `*.wav`,
`*.aiff` and the `work/` folder, so a copy of a disc or a remux cannot be committed by accident.
