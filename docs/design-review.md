# Design review

> ## Status: at the 0.1.0 release, November 2026
>
> dvd-to-plex had two independent reviews, both by a separate AI model that had not seen the
> conversation the work came from: one of the design before any code was written, and one of
> the whole repository before it went public. This page lists what each found and what became
> of it. Where this page and the code disagree, the code is the truth; please open an issue.
>
> **Open after 0.1.0:** five findings of the second review, listed under
> [What is still open](#what-is-still-open). The first, on the plugin guard, was fixed on
> 19 November 2026. The [security audit](security.md) of the same day covers the guard in more
> depth.

## Review 1: the design, before building

**Verdict: build with changes.** Two blockers, one gap in how the tool stays away from scrambled
discs, and a first version that was too big.

### Blockers

| # | Finding | What was done |
|---|---|---|
| 1 | The first scaffold would have published to PyPI on its first merge, before anyone had checked the repository's history for private material. | No release job and no release workflow until the pull request that releases (#13). The privacy check ran on the whole history first. |
| 2 | Planted errors were to be scored after a later step had changed the text, and a manual override could clear a missed plant. | Scoring happens on the proofreader's own answers. A missed plant voids the whole chunk, which is proofread again with fresh plants. An override changes the subtitle text after scoring and never a plant's verdict (`dvdtoplex/assemble.py`). |

### Staying away from scrambled discs

| Finding | What was done |
|---|---|
| Read the disc only with plain file reads, never through a library or demuxer that could unscramble sectors on its own, and say so as a design rule. | `inspect`, `copy` and `remux` read the files directly. The rule is in the README's "What it won't do". |
| Claim only what is really detected: the scrambling flag in each 2048-byte pack. | The README and the refusal say "scrambled" and nothing broader. |
| One fixed refusal, naming no tool; the skill stops there and suggests nothing. | Exit status 3 with one message (`dvdtoplex/scramble.py`). The skill forbids suggesting another tool after a refusal or a failed check. |
| `copy` stops on any read error rather than skipping or padding, and `remux` counts scrambled packs again itself. | Both done; `remux` scans every pack of the copy and of each cut. |

### Gaps in the stages

| Finding | What was done |
|---|---|
| Frame rate and picture size were written for PAL only. | The duration check works in both video standards. It has not been run on a real NTSC disc. |
| The palette and the audio and subtitle maps came from the first program chain. | Each title uses its own program chain's palette and stream maps (`dvdtoplex/ifo.py`). |
| Subtitle timing taken from the remuxed file can drift. | Times are taken from a first-stage file and compared packet by packet; an offset shift is kept as a test that must fail. |
| No check of the audio and video start offset, and timestamp resets at cell joins pass a hash check. | Both checked. A reset splits the title rather than failing it. |
| Titles with several angles or interleaved cells. | Flagged by `inspect` and skipped by `remux`, also when named by number. |
| Extras chosen by one disc's rules. | Read from the disc's own tables; a program chain that covers the same sectors as another is marked as a duplicate and left out of the default remux (`dvdtoplex/survey.py`). |
| No default audio track, no commentary labels, one fixed language. | Languages and commentary labels come from the IFO, per track. The default audio is the first ordinary track, or the one in the language given with `--prefer-audio`. |
| Why film subtitles are sidecar files and extras' subtitles are embedded. | Stated in the README. |

### Building it in CI, and the proofreading controls

| Finding | What was done |
|---|---|
| The shared test workflow could not install Homebrew packages and timed out too early. | Inputs for both, released in the shared workflows before this repository used them. |
| Text recognition and speech on a hosted macOS runner were assumptions. | The first pull request added probe tests for both, and they pass in CI. |
| Plants must sit only where the two readings agree, include character-level errors, and come with a key the agent cannot read. | Up to three plants per chunk of about 50 lines, a changed word, a dropped leading quote or `l` for `L`, placed at random on every run, the key as salted hashes outside the work folder, and an agent limited to Read and Write. |
| Everything a flip to public exposes must be checked, not only the files. | The privacy check covered every file in history, commit messages, pull request texts and their edits, and the Actions logs. |

### Size

The design was too big for a first version. 0.1.0 holds the lossless remux, the Plex names, the
end-state check and the disc subtitles with their proofreading. Speech to subtitles is planned for
0.2.0 and translation for 0.3.0.

Accepted as risks: NTSC discs and other subtitle fonts are untested on real discs; the only field
evidence is the hand-run process on one unscrambled disc, before the tool existed; a plugin user may
not have access to the model the proofreader is set to.

## Review 2: the whole repository, before going public

**Verdict: go with fixes.** The reviewer read every tracked file, every commit and pull request
text, a CI log and the built packages. It found no private material, and found that the code keeps
to the rule on scrambled discs. Three findings blocked the release; the rest did not.

| Finding | What was done |
|---|---|
| **Blocking.** The release needs its settings in order before the merge: the repository public, a `pypi` environment limited to `v*` tags, a ruleset on `main`, the PyPI publisher confirmed, private vulnerability reports on. | Done in that order before the release merge. |
| **Blocking.** The README and the agent's instructions said the guard keeps the proofreader inside its own chunk. The guard accepts any chunk folder. | The wording was corrected for the release. The guard was fixed after it, see below. |
| **Blocking.** The README asked users to install a Homebrew package that only the sample disc and the tests need, and whose dependencies include a library this project does not use. | The install table no longer lists it. CI keeps installing it to build the sample disc. |
| A source file's docstring named another project the design avoids. | Reworded. |
| The README's description of the scrambling check said more than the check does. | Reworded. |
| The skill did not forbid suggesting another tool after a failed check, and did not cover a follow-up question after a refusal. | Both covered. |
| The README said the work folder needs about twice the disc's size; the peak is about four times. | The README and the skill say four. |
| The README said the plugin ships with the next version. It is installed from `main`. | Reworded. |
| The checks table listed a damaged subtitle as a failing input. `ocr` skips it and exits 0. | Moved to the known limitations. |
| The `inspect` example lacked its first line, and exit status 2 was not listed. | Both fixed. |
| The source archive shipped the plugin tests but not the plugin, so its own tests could not pass. | The archive now includes `plugin/` and `.claude-plugin/`; an old archive fails and the new one passes. |
| Two checks have no failing control, and two inputs are tested against the check function rather than the stage. | Open, see below. |
| `copy`'s re-check, the guard, and the anchor check. | Open, see below; the guard was fixed after the release. |

## What is still open

These were found and not fixed before 0.1.0. They are listed here so nobody mistakes them for done.

1. **Fixed on 19 November 2026: the guard failed open, and accepted any chunk.** If the hook could
   not start or crashed, Claude Code let the call go ahead, and the guard matched any
   `review/<name>/chunk_NN/` path. It now blocks when it cannot run, holds each agent run to the
   chunk folder it first read from, and has tests that refuse another chunk. Findings 1 and 2 of
   the [security audit](security.md).
2. **The guard runs on every tool call.** Its matcher is `*`, so every session with the plugin
   enabled starts a shell and the system Python for each tool call, to let through every call that
   is not the proofreader's.
3. **`remux` skips its re-check of the copy** when `copy.json` is missing (`dvdtoplex/cli.py`), for
   example when the `VIDEO_TS` folder was put in the work folder by hand.
4. **The audio anchor check is enforced only when the title has subtitles** (`dvdtoplex/remux.py`).
5. **Several checks have no failing control.** The review named two: the duration check and the
   audio and video offset check pass on the sample disc, but no test feeds them an input they must
   reject. A claim-by-claim check of the architecture page against the code found fifteen more.
   The four of those on the guard have tests since 19 November 2026;
   [architecture.md](architecture.md#12-not-in-this-version) lists the thirteen left.

Suggested by the second review and not done: a prebuilt sample disc attached to each release, so
that trying the tool needs no authoring tools.
