# Developing dvd-to-plex

This page holds what is specific to this repository: how to set it up, run the tests, what the
sample disc is for, how a release happens, and the checklist to work through before you open a pull
request. The general rules live in the shared [contributing
guide](https://github.com/agusgonzaleznic/.github/blob/main/CONTRIBUTING.md), and participation is
governed by the shared [Code of
Conduct](https://github.com/agusgonzaleznic/.github/blob/main/CODE_OF_CONDUCT.md). How to report a
security problem is in the shared [security
policy](https://github.com/agusgonzaleznic/.github/blob/main/SECURITY.md); what this tool does and
does not defend against is in [`security-posture.md`](security-posture.md). Where the assets in this
repository come from is in [`assets-provenance.md`](assets-provenance.md).

Please read [the hard non-goals](#hard-non-goals) before you open an issue or a pull request that
touches what the tool reads.

## Quick start

The tool runs on macOS only, on Python 3.12 or newer. You need Homebrew packages and the Xcode
command line tools for the tests.

```bash
git clone https://github.com/agusgonzaleznic/dvd-to-plex.git
cd dvd-to-plex
brew install ffmpeg
xcode-select --install
python3 -m venv .venv
.venv/bin/pip install --require-hashes --no-deps -r requirements/runtime.txt
```

- The tests also need `dvdauthor` and `spumux` on your PATH, to build the sample disc. CI takes both
  from Homebrew's `dvdauthor` package, next to `ffmpeg`. Of the commands, only `sample` uses them.
- The Xcode command line tools bring `swiftc`, which builds the small Swift helpers. The CI workflow
  takes `swift` and `say` from the runner image.
- `requirements/runtime.txt` holds one package, `plex-renamer`, pinned with its hashes. Installing
  with `--require-hashes --no-deps` is how CI installs it, so a package outside the file cannot slip
  in.

From a clone, `python3 -m dvdtoplex` runs the code in place.

## Tests

CI runs this command, and it is also the local form from a clone:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The tests build the sample disc and run every stage on it. They also feed most checks a bad input
that they have to reject, so such a check that let everything through turns CI red; the
[architecture](architecture.md#12-not-in-this-version) lists the checks with no such test yet. A
tool the tests need fails them when it is missing, except in the plugin tests: the shim test is
skipped without the Xcode command line tools, and the guard runs on the system Python only when
`/usr/bin/python3` exists.

Some tests need more than the sample disc:

- `tests/test_runner.py` checks what the machine can do: `swift` and Vision reading German text,
  `say` writing German speech (it needs a German voice installed), and `dvdauthor` building a PAL
  title.
- `tests/test_plugin.py` checks that the plugin's manifests agree with each other, that the skill
  runs every command and no other, and that the proofreader guard lets the agent touch only its
  own chunk's files. One guard test is skipped when the Xcode command line tools are not installed.

### The synthetic sample disc

`dvd-to-plex sample DEST` writes a small PAL DVD-Video folder and a `sample-truth.json` next to it.
The tests build it once per run in `tests/disc.py` and share it between test modules. It has three
titles:

| Title | Length | Audio | Subtitles |
|---|---|---|---|
| 1 | 12 s, four chapters | German, and German commentary | German, white |
| 2 | 5 s | English | German, yellow (a different palette) |
| 3 | 3 s | English | none |

Titles 2 and 3 share a title set. The disc exists so that every stage has a known input and a known
answer: `sample-truth.json` holds every subtitle line as it was written, and the tests compare each
stage's output with it. It also lets you try the tool without a disc of your own, and lets a bug
report say whether the problem also happens on the sample.
[`assets-provenance.md`](assets-provenance.md) says how everything on the disc is made.

Nothing from a real disc belongs in the repository. `.gitignore` leaves out `VIDEO_TS/`, `*.iso`,
`*.vob`, `*.IFO`, `*.BUP`, `*.mkv`, `*.wav`, `*.aiff` and the `work/` folder.

## Continuous integration

Two workflows run on every pull request and every push to main, on macOS 15 for the tests. The jobs
live in shared reusable workflows, pinned to full commit SHAs with the release in a comment.

| Workflow | Job | Check name | What it does |
|---|---|---|---|
| `ci.yml` | `tests` | `Tests / Python 3.12`, and one per version in the matrix (3.12 and 3.13) | Installs `requirements/runtime.txt` and the Homebrew packages, then runs the test command above |
| `ci.yml` | `package` | `Package build / Build sdist and wheel` | Builds the package as a release builds it, with `requirements/build.txt` as constraints, so a stale file or a broken build fails a pull request instead of a release |
| `ci.yml` | `check-version` | `Release / Check the version` | Pull requests only: fails a change to what ships unless the version is raised |
| `ci.yml` | `release` | `Release` | Pushes to main only: see [Release on merge](#release-on-merge) |
| `lint.yml` | `lint` | `Lint / actionlint`, `Lint / Verify action pins` | Lints the workflows and proves every action pin |

The main ruleset requires six of these checks, with the branch up to date: `Tests / Python 3.12`,
`Tests / Python 3.13`, `Package build / Build sdist and wheel`, `Lint / actionlint`, `Lint / Verify
action pins` and `Release / Check the version`. Renaming a job breaks that list, as a comment in
`ci.yml` warns. A pull request cancels its older runs; CI runs on main never cancel a running one,
because it may be tagging a version.

`release.yml` runs on a version tag, never on a pull request.

## Release on merge

The merge of a pull request is the release. Nobody pushes a tag by hand, and nobody starts a release
by hand.

1. The version lives in `__version__` in `dvdtoplex/__init__.py`. `pyproject.toml` reads it from
   there.
2. A change to `dvdtoplex/`, `pyproject.toml`, `README.md` or `LICENSE` ships. Its pull request must
   raise `__version__` above every earlier release, and `Release / Check the version` fails until it
   does. The package includes the Swift source, so a change to a `.swift` file under `dvdtoplex/`
   counts.
3. Tests, docs and workflows do not ship. They go out with the next version. The plugin is installed
   from main, so it changes with every merge.
4. On the merge, once the tests and the package build pass on main, the `Release` job tags the
   version when it is new and runs `release.yml` on the tag. `release.yml` runs the tests and the
   build again, publishes a GitHub release with the wheel, the source archive and their
   `SHA256SUMS`, then uploads the same files to PyPI with Trusted Publishing. The workflow uses no
   PyPI token.
5. If the release fails, the run on main turns red.

The PyPI upload stays in `release.yml` because PyPI trusts only a job defined in the workflow file
registered with it.

## Regenerating the hash-locked requirements

Two files hold hashes: `requirements/runtime.txt` (from `requirements/runtime.in`) and
`requirements/build.txt` (from `requirements/build.in`, the build backend that `release.yml` builds
with). The header comments give the commands. Both leave out releases younger than a week, as
Dependabot's cooldown does for actions; replace `<the date a week ago>` with that date.

```bash
uv pip compile requirements/runtime.in --universal --generate-hashes --python-version 3.12 \
  --exclude-newer <the date a week ago> --no-header -o requirements/runtime.txt

uv pip compile requirements/build.in --universal --generate-hashes --python-version 3.12 \
  --exclude-newer <the date a week ago> --no-header -o requirements/build.txt
```

Regenerate `runtime.txt` after changing `runtime.in`, and `build.txt` after changing
`[build-system]` in `pyproject.toml`. `runtime.in` notes that `plex-renamer` is maintained alongside
this project, so its releases are taken without the wait.

## Pinned actions and lint

Every workflow uses shared reusable workflows or actions pinned to a full commit SHA, with the
release in a comment. `lint.yml` runs the shared `actions-lint.yml`, which lints the workflows and
proves every pin. Dependabot proposes updates to the pinned workflows weekly, and waits seven days
after a new release before it does.

## Developing the plugin

The plugin lives in `plugin/`, and `.claude-plugin/marketplace.json` at the repository root lists it
with the source `./plugin`:

- `plugin/skills/dvd-to-plex/SKILL.md`: the skill that runs the stages in order.
- `plugin/agents/subtitle-proofreader.md`: the proofreader agent, which has only the `Read` and
  `Write` tools.
- `plugin/hooks/hooks.json`, `guard.sh` and `proofreader_guard.py`: the hook that holds the
  proofreader to its own chunk's files, and refuses its calls when the guard fails.

`tests/test_plugin.py` keeps these in step with the command. It checks that the marketplace entry
names the plugin and points at `./plugin`, that the plugin has no version of its own to drift from
the package's, that the skill names every command the CLI has (except `sample`) and none that it has
not, and that the guard allows and refuses the calls it should. The guard runs on the system
`python3`, so it may use nothing newer than that version has.

The guard's tests hand it calls as Claude Code would; they do not run Claude Code. The binding
depends on the `agent_id` Claude Code puts in each call, so after a change to the hook, also run it
there: install the plugin from your checkout into a fresh `CLAUDE_CONFIG_DIR`, and have the main
session launch two proofreaders in parallel on two chunks, each reading its own chunk and then the
other's. Each must be refused the other's chunk only.

The checks live in the command, not in the plugin, so keep it that way: a stage the plugin runs must
not be able to skip one.

## Hard non-goals

These are by design and will stay. Issues and pull requests that ask to change them are closed.

- **Scrambled discs.** The tool reads only unscrambled DVD-Video. When it finds a scrambled pack it
  stops with exit status 3 and changes nothing. It reads the disc with plain file reads only, and
  the plugin stops there too and suggests nothing.
- **Re-encoding.** It never re-encodes.
- **Web lookups.** It never fetches subtitles or metadata from the web.
- **Deleting your files.** It never deletes a file you had. `embed` keeps the file it replaces in a
  `retired-` folder named by date.

The [known limitations](../README.md#-known-limitations) (macOS only, films only, no Blu-ray) are
gaps, not non-goals.

## Before you open a pull request

- [ ] The tests pass from a clone with the command above.
- [ ] A new check comes with a bad input that it must reject.
- [ ] If the change touches `dvdtoplex/`, `pyproject.toml`, `README.md` or `LICENSE`, you raised
  `__version__` in `dvdtoplex/__init__.py`.
- [ ] A change to `requirements/*.in` or `[build-system]` comes with the regenerated hash file.
- [ ] Workflow changes keep every action pinned to a full SHA with the release in a comment, and
  keep the job names listed above.
- [ ] A change to the plugin keeps `tests/test_plugin.py` green.
- [ ] No disc, copy or media file is in the commit.
- [ ] The README still matches what the tool does.
- [ ] You followed the [contributing
  guide](https://github.com/agusgonzaleznic/.github/blob/main/CONTRIBUTING.md).
- [ ] A bug report or pull request says whether the problem also happens on the sample disc.
