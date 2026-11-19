# Security posture

How to report a vulnerability is set out in the shared [security
policy](https://github.com/agusgonzaleznic/.github/blob/main/SECURITY.md). This page covers what is
specific to dvd-to-plex: what helps in a report, what is in and out of scope, what the project
already does, and the positions it has accepted, so that nobody spends time reporting something
already known. The latest audit is in [`security.md`](security.md).

dvd-to-plex is a command-line tool that runs on one user's Mac, plus a Claude Code plugin that
drives it. It has no server, no accounts and no network code of its own. Its untrusted input is the
disc: a mounted disc, a folder holding `VIDEO_TS`, or an ISO image.

## What helps in a report

- The version (`dvd-to-plex --version`) and whether it still happens on the latest release. The
  plugin is installed from the default branch, so for the plugin say when you installed or updated
  it.
- The command and stage (`inspect`, `copy`, `remux`, `ocr`, `review-prep`, `assemble`, `embed`,
  `name`, `verify`), and its exit status.
- macOS version, Python version, and the ffmpeg version where a remux or probe is involved.
- A minimal reproduction on synthetic input. `dvd-to-plex sample` writes a small disc folder you can
  alter. Please never send a commercial disc image or any part of one.
- For the plugin: the tool call the proofreader made and what the guard answered.
- What an attacker gains. Severity follows impact on the user's machine and files.

## In scope

- **Reading disc data**: IFO tables, VOB packs, subtitle units and file names (`dvdtoplex/ifo.py`,
  `vob.py`, `spu.py`, `scramble.py`, `source.py`, `survey.py`, `parts.py`). Crashes, hangs,
  unbounded memory, or reads outside the disc that a crafted disc can cause.
- **Paths**: anything that makes the tool write outside the work folder or the library folder,
  overwrite a file it should not, or follow a link it should not.
- **Subprocesses**: argument handling for `ffmpeg`, `ffprobe`, `hdiutil`, `swiftc` and the compiled
  OCR helper.
- **The compiled OCR helper and its cache** (`dvdtoplex/swiftc.py`, `dvdtoplex/ocr.py`).
- **The planted-error key** (`dvdtoplex/review.py`, `dvdtoplex/assemble.py`): ways for the
  proofreader to learn which cues hold a control, or to pass a chunk without doing the work.
- **The plugin's guard** (`plugin/hooks/`): any way for the `subtitle-proofreader` agent to read or
  write outside its own chunk's files, or to use a tool other than Read and Write.
- **Release and supply chain**: the workflows in `.github/`, the hash-locked requirement files in
  `requirements/`, and the path from a merge to PyPI.

## Out of scope

- **Scrambled discs, and anything that reads past them.** dvd-to-plex reads unscrambled DVD-Video
  only. When it finds a scrambled pack it stops with exit status 3 and a message that names no tool.
  That refusal is by design. Reports asking to read, work around or soften it are closed, and so are
  reports about what happens on such a disc after the refusal.
- **The user's own arguments and files.** The user runs the tool as themselves. A work folder, names
  file or `--lang` value that the user chose is not a trust boundary.
- **Code outside this repository**: ffmpeg, macOS (including Vision and the disk image and file
  system code `hdiutil` uses), Python, and Claude Code itself. Report those upstream. A report that
  this project calls them in an unsafe way is in scope.
- **plex-renamer**, a separate project with its own repository. How dvd-to-plex uses it is in scope.
- **Model quality**: OCR mistakes, or a proofreader that is simply wrong. The planted controls exist
  to catch that, and a failed chunk is the intended outcome.
- **Denial of service by the user against their own machine**, such as a huge work folder or a disc
  the user built to be slow, unless a disc from someone else can cause it.

## What the project already does

- **Plain file reads only.** The disc is read with ordinary file reads; nothing in the package opens
  a network connection. Text recognition runs on the device with Apple's Vision framework.
- **Refuses scrambled video.** `copy` checks a sample of packs per VOB file before it copies
  anything. `remux` checks every pack of the copy, then every pack of each cut, before ffmpeg runs.
  `inspect --full-scan` checks every pack.
- **Refuses truncated disc tables** with a one-line error, records a truncated subtitle unit and
  leaves it out, and stops on the first read error rather than skipping or padding.
- **No shell.** Every external program gets an argument list.
- **ISO images attach read-only**, hidden from Finder, and are detached when the command ends.
- **Refuses to replace an existing output.** `copy`, `remux`, `name` and `embed` each stop when
  their output exists, and `embed` keeps the file it replaces in a dated `retired-` folder.
- **Integrity from disc to library.** `copy` records a SHA-256 per file and `remux` checks them
  again when that record is there; each MKV's hash is recorded once its checks pass; `name`
  re-hashes before every move, copies across file systems through a checked partial file, and
  records every move; `verify` re-hashes everything in the library.
- **Names stay inside the library.** Titles and labels go through plex-renamer's sanitiser, the
  category must be a plain folder name, extras folders come from a fixed list, and plex-renamer must
  read every planned name back the same way before anything moves.
- **Keeps the key away from the work folder.** The key lives in `~/Library/Application
  Support/dvd-to-plex/keys` (or `DVDTOPLEX_KEYS`), never inside the work folder, with owner-only
  permissions and a fresh random salt and run id per track. A redo plants fresh errors.
- **Confines the proofreader with a guard.** A `PreToolUse` hook refuses the `subtitle-proofreader`
  agent every tool except Read and Write, every path that is not absolute and plain, a symbolic link
  at the review, track or chunk folder or the file, and any file that is not a chunk's `cues.json`,
  contact sheet or `answers.json` (Read) or `answers.json` (Write). It holds each agent run to the
  chunk folder it first read from. Any failure refuses the agent: an error in the guard, a missing
  interpreter, or no Xcode command line tools. The hook sets `"onFailure": "block"`, so a hook
  that cannot start or times out blocks the call too.
- **Release from CI only.** Workflows run on `pull_request`, never `pull_request_target`, with
  read-only default permissions. Every action and shared workflow is pinned to a full commit SHA.
  The build and runtime requirement files are hash-locked. PyPI uploads use Trusted Publishing from
  the `pypi` environment, with no stored token. Dependabot proposes action updates after a 7-day
  cooldown.

## Known and accepted positions

Most of these come from the 18 November 2026 audit. They are open, not forgotten; the maintainer
decides whether and when each changes.

- **A hook that hangs blocks every tool call.** `onFailure` applies to the whole hook, so if the
  shim cannot start or runs past its 30-second timeout, Claude Code blocks the call whoever made
  it, not only the proofreader's. It needs Claude Code 2.1.295 or later; older versions were not
  tested.
- **The guard trusts the agent's first Read to name its chunk.** The main session's prompt names
  the folder, and that Read is decided before any file has reached the agent. Whether the folder
  exists, or is the one the prompt named, the guard leaves unchecked. The binding lives in the
  temporary folder: if that is cleared while an agent run goes on, its next Read binds afresh.
- **The main session is not guarded.** The skill tells it never to open the key folder or write
  answers, but only the proofreader agent is technically confined.
- **The key is safe by location, not by its hashes.** The salt sits in the same file as the hashes,
  so whoever reads the key can recover the controls.
- **The work folder keeps the OCR text before planting.** Comparing `WORK/ocr` with a chunk's
  `cues.json` shows the plants; only the guard keeps the proofreader out of it.
- **Proofreader answers are trusted beyond the controls.** The controls check a few planted words
  per chunk. The rest of the text, including any extra cue an answer adds, is taken as written.
- **Subtitle images can carry text aimed at the model.** The proofreader reads them by design.
- **The OCR helper is cached in the work folder** and reused by name, without a content check. Keep
  the work folder where only you can write.
- **Symlinks in a disc folder are followed.** Inspect a disc folder from someone else before copying
  it.
- **`copy` and plain `inspect` sample the scrambling check.** `remux` always scans in full, so
  nothing is remuxed from a disc with any scrambled pack.
- **External programs are found on `PATH`** and inherit the user's environment.
- **Installs are not hash-locked for users.** `uv tool install dvd-to-plex` or `pipx install
  dvd-to-plex` resolve `plex-renamer>=1.0.2,<2` at install time. The hash locks cover CI and the
  release build. The README describes a `SHA256SUMS` file on each GitHub release for checking the
  files first; the shared workflow that makes it was not reviewed.
- **The plugin follows the default branch.** It has no version of its own, so an update brings
  whatever is on the branch, guard included.
- **Python requirement files are refreshed by hand.** Dependabot watches GitHub Actions only.
