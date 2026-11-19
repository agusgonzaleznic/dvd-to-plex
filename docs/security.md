# Security audit

**Audited:** 18 November 2026, at commit `f0e3198` (branch `release`). **Scope:** the Python package
`dvdtoplex/` (with its two Swift helpers), the Claude Code plugin (`plugin/`, `.claude-plugin/`),
the GitHub workflows and Dependabot settings (`.github/`), and the packaging (`pyproject.toml`,
`requirements/`). The tests were read for intended behaviour.

**Method.** One automated reviewer read every file in scope, then proved or dropped each candidate
with a `file:line` and, where it was cheap and safe, a reproduction on synthetic files in a scratch
folder. Nothing ran against a real disc or real user files. Nothing in the repository was changed.
**No second, independent reviewer has checked this audit.** The maintainer re-checked finding 2
against the Claude Code hooks reference. The output of every reproduction, and the scripts of most,
are kept outside the repository.

**Status.** Findings 1 and 2 were fixed on 19 November 2026, after the 0.1.0 release, in the plugin,
which installs from `main`, so no package release was needed. The rest are **open**. The maintainer
decides what to fix and when. Each finding describes the code at the audited commit; findings 1
and 2 end with a note on their fix.

## Threat model

dvd-to-plex is a local command run by one user on their own Mac, plus a Claude Code plugin that
drives it. It has no server, no accounts and no network code of its own.

| Vector | Realistic? |
|---|---|
| A crafted disc, disc folder or ISO image (IFO tables, VOB packs, subtitle units, file names, symlinks) | **Yes.** The disc is the main untrusted input, and an image can come from anywhere |
| Subtitle images that carry text aimed at the proofreading model | **Yes.** The proofreader reads the images by design |
| The proofreader agent reaching beyond its chunk: the key, the OCR output, other files, other tools | **Yes.** The plugin's guard exists for this |
| Someone who can write the work folder (a shared drive, a work folder handed over) | Yes, but only where the folder is shared |
| A compromised dependency, workflow or release step | Yes, through `plex-renamer`, the shared reusable workflows, or the PyPI upload |
| The user's own command-line arguments and names file | **No.** The user runs the tool as themselves; arguments are not a trust boundary |
| Discs with scrambled sectors | **Out of scope by design.** The tool refuses them |

## Findings

Severity is judged for a single-user local tool: what an attacker gains, and what it takes.

| # | Finding | Severity | Status | Reproduced |
|---|---|---|---|---|
| 1 | The guard allows any chunk-shaped path, not only the agent's own chunk | Low | fixed | yes (R5) |
| 2 | The guard fails open when it cannot run or crashes | Medium | fixed | yes (R5, R6) |
| 3 | A cached OCR helper in the work folder runs without any check | Low | open | yes (R4) |
| 4 | Symlinks in the disc folder are followed; copy brings their targets in | Low | open | yes (R2) |
| 5 | Some malformed disc tables crash with a traceback instead of a clean refusal | Low | open | yes (R1) |
| 6 | Disc data can force large reads and quadratic parsing time | Low | open | yes (R7, R11) |
| 7 | `copy` and default `inspect` check a sample of packs for scrambling | Informational | open | yes (R3) |
| 8 | A proofreader answer can add SRT cues that no control checks | Low | open | yes (R9) |
| 9 | Untrusted subtitle images reach a model whose reply goes to the main session | Informational | open | no (design reading) |

### 1. The guard allows any chunk-shaped path, not only the agent's own chunk

`plugin/hooks/proofreader_guard.py:19-21` and `:37` search the path for `/review/<name>/chunk_NN/`
followed by an allowed file name. The match can sit anywhere in the path, and nothing ties it to the
chunk the agent was launched on. In R5 the guard allowed:

- a Write of a sibling chunk's `answers.json` in the same work folder;
- a Read of another work folder's `cues.json`;
- a Write of `answers.json` under any folder that ends in `review/<name>/chunk_NN/`.

The key, the OCR output, reports and traversal with `..` were still refused (R5 controls). A
symbolic link earlier in the path is followed: in R10 a Read through a symlinked chunk folder
passed, while a symlinked final file was refused (`plugin/hooks/proofreader_guard.py:35` checks only
the last part). The agent cannot create a link with Read and Write, so the key stays out of reach
unless a link is already there. What the agent gains is the ability to overwrite another chunk's
answers and to write a JSON file into folders of that shape anywhere it can write. The agent is told
to write only its own chunk's `answers.json`; the guard does not enforce that. Whether the Write
tool creates missing parent folders was not tested.

**Fixed on 19 November 2026.** The guard now binds each agent run to the chunk folder of its first
allowed Read, and refuses every other chunk folder and a Write before any Read. It keys the binding
on the session id and the `agent_id` Claude Code gives each agent run, so proofreaders running in
parallel, and a redo, each get their own. It also refuses a symbolic link at the review, track or
chunk folder, not only at the file, and any path with a `.` segment, a doubled or trailing slash,
or a leading `//`. `tests/test_plugin.py` covers each case. Inside Claude Code 2.1.328, two
proofreaders launched in parallel each read their own chunk and were refused the other's. Whether
the bound folder exists, or is the one the prompt named, the guard leaves unchecked: the agent's
first Read, decided before any file has reached it, picks it.

### 2. The guard fails open when it cannot run or crashes

Claude Code blocks a `PreToolUse` call when the hook exits with status 2 or returns a deny decision.
Any other exit status, a hook that cannot start, or a timeout lets the call go ahead, unless the
hook sets `"onFailure": "block"` (Claude Code hooks reference; that setting needs Claude Code
v2.1.295 or later). `plugin/hooks/hooks.json:5-11` does not set it. Reproduced:

- With the interpreter missing after `xcode-select -p` succeeds (`plugin/hooks/guard.sh:9-10`), the
  wrapper exits 126 for a proofreader `Bash` call (R6). The real wrapper exits 2.
- A `tool_input` that is not an object makes `plugin/hooks/proofreader_guard.py:31` raise, and the
  guard exits 1 (R5).

The agent itself does not control the shape of `tool_input` or the interpreter, so it cannot trigger
this on purpose as far as this audit found. The risk is a silent failure: if the guard stops
working, the proofreader's Read and Write calls are no longer confined, and it reads untrusted
subtitle images (finding 9). It could also pass its chunk without reading the images: `WORK/ocr`
keeps every cue's text before planting (`dvdtoplex/ocr.py:91-98`), so comparing it with `cues.json`
shows each plant, as `tests/test_review.py:52-59` does. The user's own permission settings then
remain the only limit.

**Fixed on 19 November 2026.** `guard.sh` now refuses the proofreader's call when the guard exits
with anything but 0, or 2 with the guard's own message, a missing interpreter or script included,
and lets every other call pass. The guard refuses the proofreader's call on any error of its own, a
`tool_input` that is not an object included. `hooks.json` sets `"onFailure": "block"` and a
30-second timeout for what the shim cannot cover, the shim itself not starting or timing out; Claude
Code then blocks the call, whoever made it. Inside Claude Code 2.1.328, a hook that timed out
blocked a Read, and the same hook without `onFailure` let it through. Claude Code older than 2.1.295
was not tested.

### 3. A cached OCR helper in the work folder runs without any check

`dvdtoplex/swiftc.py:18-20` returns any existing file named `read_text-<12 hex digits of the
source's SHA-256>` in the cache folder, and `dvdtoplex/ocr.py:106` puts that folder inside the work
folder (`WORK/ocr/bin`). `dvdtoplex/ocr.py:54` then runs it. Owner, mode and content are not
checked. In R4 a planted shell script was returned by `build` and run by `ocr.read`, which accepted
its output.

Anyone who can write the work folder gets code run as the user at the next `ocr`. That matters when
the work folder sits on a shared volume or is handed from one machine to another. On a private home
folder it adds nothing an attacker does not already have.

### 4. Symlinks in the disc folder are followed; copy brings their targets in

`dvdtoplex/copier.py:69-70` selects files with `is_file()`, and `dvdtoplex/copier.py:37` opens them
with `open()`. Both follow symlinks. In R2, a symlink named `VTS_01_0.BUP` in a source folder
pointed at a file outside it; `copy` exited 0, wrote the target's content into
`WORK/VIDEO_TS/VTS_01_0.BUP` and listed it in `copy.json`. The IFO, VOB and scrambling readers
follow symlinks the same way (`dvdtoplex/ifo.py:149`, `:191`; `dvdtoplex/vob.py:23`;
`dvdtoplex/scramble.py:60`).

The target must be readable by the user, and the copy stays in the user's own work folder, so the
gain is small unless the work folder is shared later. A disc folder from someone else can also point
a link at a special file or a very large file (see finding 6). Whether an attached ISO image can
carry such links was not tested.

### 5. Some malformed disc tables crash with a traceback instead of a clean refusal

`dvdtoplex/survey.py:55-56` index the title set's tables with numbers taken from the disc,
unchecked. `IndexError` is not among the exceptions `dvdtoplex/cli.py:28` (`inspect`) or
`dvdtoplex/cli.py:98` (`remux`) catch. In R1, a title entry whose `title_in_set` has no matching
entry made `inspect` print a Python traceback (exit 1). A `title_in_set` of 0 becomes index -1,
which selects the last entry when the table is not empty. A truncated IFO, by contrast, is refused
cleanly with one line (R1 control, `dvdtoplex/ifo.py:133-143`).

Found by reading, same class, not reproduced: `dvdtoplex/remux.py:113` and `:116` index the
first-PTS table, which leaves out a stream with no PTS in the packs it read (`KeyError`);
`dvdtoplex/ocr.py:36-37` raises `CalledProcessError`, which `dvdtoplex/cli.py:128` does not catch;
and `dvdtoplex/namer.py:119` reads stream data through `dvdtoplex/media.py:15-19`, whose
`MediaError` `dvdtoplex/cli.py:188` does not catch. The impact is an untidy exit, not a wrong
output: no stage writes its result after such an error.

### 6. Disc data can force large reads and quadratic parsing time

- `dvdtoplex/ifo.py:149-151` and `:191-193` read a whole IFO into memory before checking its 12-byte
  signature. In R11 a 512 MiB file raised peak memory from 16 MiB to 529 MiB before the refusal. A
  real IFO is a few hundred KiB at most.
- `dvdtoplex/spu.py:77-105` follows a subtitle unit's chain of control sequences. Cycles are
  stopped, but a unit can hide each sequence's header inside the previous sequence's commands, so
  every sequence rescans to the end. In R7 the time grew with the square of the size: 0.6 s at 16 KB
  and 9.6 s at 64 KB, the largest unit the format allows. `ocr` parses every unit
  (`dvdtoplex/ocr.py:75-82`), so a disc with many such units could keep it busy for hours. Whether
  such a unit survives remux's full decode check (`dvdtoplex/remux.py:181-182`) and reaches `ocr`
  was not verified.

Both are local denial of service. The user can stop the command.

### 7. `copy` and default `inspect` check a sample of packs for scrambling

`dvdtoplex/scramble.py:42-57` checks a sample of about 4096 packs per VOB (fewer than 8192) unless
told otherwise. `dvdtoplex/copier.py:63` and `dvdtoplex/cli.py:27` (without `--full-scan`) use that
default. In R3, one flagged pack off the sampling grid passed both, and `copy` copied the VOB into
the work folder. The control, with the pack on the grid, was refused.

The refusal still holds where it matters: `remux` scans every pack of the copy before it remuxes
anything (`dvdtoplex/cli.py:70-72`) and every pack of each cut before ffmpeg runs
(`dvdtoplex/remux.py:93-95`), so nothing is remuxed. What may surprise is that `copy` can finish on
such a disc; `README.md:95` and `README.md:96` state the sample for both.

### 8. A proofreader answer can add SRT cues that no control checks

`dvdtoplex/assemble.py:28` accepts any string as a cue's text, and `dvdtoplex/assemble.py:72-74`
writes it into the SRT unchanged. An answer holding a blank line and a timing line therefore becomes
an extra cue: in R9, two cues went in and `embed`'s reader (`dvdtoplex/embed.py:41-45`) read back
three, one at a time no image showed. The planted controls score only the planted tokens
(`dvdtoplex/assemble.py:37-43`). The result is wrong subtitle text in the user's library, not code
execution.

### 9. Untrusted subtitle images reach a model whose reply goes to the main session

The proofreader reads images decoded from the disc (`plugin/agents/subtitle-proofreader.md`). Text
in those images can be written to steer a model. The guard limits what the agent can touch (findings
1 and 2), and the controls catch an agent that skips the work. Two outputs are not confined: its
answers (finding 8), and its one-line reply to the main session, which runs with the user's full
tool set (`plugin/skills/dvd-to-plex/SKILL.md:49-53`). The agent is told to reply with counts only
(`plugin/agents/subtitle-proofreader.md:46-47`), but nothing enforces that. Not reproduced; this is
a reading of the design.

## What holds, and how it was checked

- **No shell anywhere.** Every subprocess call passes an argument list; a grep found no `shell=`,
  `os.system`, `os.popen`, `eval` or `pickle` (evidence: read-only checks).
- **No network code.** `dvdtoplex/` imports no network module, and text recognition uses Vision on
  the device (`dvdtoplex/swift/read_text.swift`). Nothing is fetched at run time; no `uvx` call
  exists in the repository.
- **Truncated input is caught.** IFO reads past the end become `IfoError`
  (`dvdtoplex/ifo.py:133-143`, R1 control) and stop the command; subtitle units that run short
  become `SpuError` (`dvdtoplex/spu.py:113-114`), which `ocr` records on the cue and leaves out of
  the SRT; sector ranges past the VOB set become `VobError` (`dvdtoplex/vob.py:51-52`).
- **Names stay inside the library.** Titles and labels pass through plex-renamer's sanitiser, which
  replaces `/` and strips trailing dots; the category must be a plain folder name; extras folders
  come from a fixed table. R8 tried `..`, absolute paths and `/` inside names: every result stayed
  one component (`dvdtoplex/namer.py:64-68`, `:168`).
- **Existing outputs are refused.** `copy` refuses an existing `VIDEO_TS`, `remux` an existing
  output, `name` an existing destination or an earlier run, `embed` an existing retired file
  (`dvdtoplex/copier.py:66-68`, `dvdtoplex/remux.py:82-83`, `dvdtoplex/namer.py:189-190`,
  `:235-236`, `:261-263`, `dvdtoplex/embed.py:154-155`).
- **ISO images attach read-only**, hidden and without auto-open, and detach afterwards
  (`dvdtoplex/source.py:28-29`, `:57-61`).
- **The key is out of the agent's reach while the guard runs.** It lives outside the work folder
  (`dvdtoplex/review.py:169-170`), with mode 0600 (`dvdtoplex/review.py:151`), a random run id and
  salt (`dvdtoplex/review.py:184`). The guard refused the key path, `..` traversal, a symlinked
  final file, and every tool other than Read and Write (R5, R10). Note that the salt is stored in
  the same file as its hashes, so the hashing keeps plain words out of the file but does not protect
  a key someone has read.
- **The scrambling refusal holds before any remux.** `remux` scans every pack, then every cut
  (finding 7). The refusal text names no tool (`dvdtoplex/scramble.py:13-14`).
- **CI and release.** Workflows run on `pull_request`, never `pull_request_target`; default
  permissions are `contents: read`, and `permissions: {}` in the release workflow; only the publish
  job holds `id-token: write`, inside the `pypi` environment, with no stored token; every action and
  reusable workflow is pinned to a full commit SHA; the build and runtime requirement files are
  hash-locked; Dependabot proposes action updates after a 7-day cooldown (evidence: read-only
  checks).

## Not verified

- The contents of the shared reusable workflows the CI and release jobs call. They live in another
  repository.
- Settings outside the repository were read from the GitHub API by the maintainer instead: the
  `pypi` environment deploys only from `v*` tags; the `main` ruleset requires signed commits, linear
  history, a pull request with one approving review from the code owner and the six required checks,
  with an admin bypass; a second ruleset protects `v*` tags from deletion and moves. The PyPI
  trusted publisher entry was confirmed field by field on PyPI by the maintainer.
- Symlinks inside an attached ISO image (finding 4), whether Claude Code's Write tool creates
  missing folders (finding 1), and whether a slow subtitle unit survives remux (finding 6).
- The plugin was not run inside Claude Code for this audit. The guard was exercised directly, with
  the input shape the Claude Code hooks reference describes. The fixes for findings 1 and 2 were
  checked inside Claude Code.

## Verification

Each reproduction is a short script in a scratch folder outside the repository, run with the
project's virtual environment Python in isolated mode and a stripped environment, on files the
script made itself. The guard runs used `/usr/bin/python3 -I`, as the wrapper does. Five
reproductions have a control that shows the check can pass or fail as intended: a truncated IFO
(R1), a flagged pack on the sampling grid (R3), the own chunk, the key path, traversal and `Bash`
(R5), the real wrapper (R6), and a symlinked final file (R10). R7 and R11 compare with a baseline;
R2, R4 and R9 have no control. The `file:line` references above are the evidence that can be checked
from this repository.
