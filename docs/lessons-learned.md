# Lessons learned

A retrospective on building **dvd-to-plex**, from the hand-run process it came from to the 0.1.0
release, written on 18 November 2026 and updated on 19 November, after the release.

**What was built:** a Python command-line tool with nine stages and a `sample` command, a Claude
Code plugin with a skill, a proofreading agent and a guard hook, a synthetic sample disc made from
code, and 103 tests that run on macOS for Python 3.12 and 3.13. It went from first commit to its
first release in thirteen pull requests.

**How:** with Claude Code, in one long session. The process was first run by hand, then designed,
reviewed by a second AI model before any code was written, built in stacked pull requests, reviewed
again as a whole, and checked for private material before the repository went public. The [design
review](design-review.md) and the [security audit](security.md) record the findings; this page
records what they taught.

## 1. A check you have never seen fail is not a check

The most useful habit of the project: for every check, feed it one input it must reject, and watch
it reject it. Without that, a check that cannot fail looks exactly like a check that passed.

Three times a check passed something wrong:

- A render of the subtitle colours looked right. The colours were wrong: DVD palettes use the studio
  range (luma 16 to 235), and a full-range conversion turns white into a light grey and black into a
  dark grey. A look at a rendered frame cannot tell. The synthetic sample disc, drawn in pure white
  and pure yellow, could, because its right answer is known.
- Subtitle times taken through an input offset moved every line by several seconds while each file
  on its own looked fine. Only a packet-by-packet comparison with a first-stage copy showed it. That
  shift is now a test that must fail.
- A control exited with an error, as it should, but because the parser crashed on the cut file, not
  because the check caught it. A control has to fail **at the check**.

The habit had gaps. When this documentation was written, a check-by-check reading of the code found
seventeen checks that no test had seen fail yet. The four on the guard have tests now, and the
[architecture](architecture.md#12-not-in-this-version) lists the other thirteen. A habit needs a list to be
checked against.

A related fact: copying a disc without a read error says nothing about whether its video is
scrambled. Only the flag in each pack does, so `inspect`, `copy` and `remux` look at the packs.

## 2. An AI proofreader has to prove it looked

The subtitle text comes from OCR, and an agent compares it with the subtitle images. When the
process was run by hand, the agents returned text that looked proofread and was largely the input
passed through. One planted wrong word in a chunk exposed it, and a second pass with a stronger
model found further misses that every structural check had passed.

So the tool plants errors itself: up to three per chunk of about 50 lines, a changed word, a dropped
leading quote or `l` for `L`, placed at random, with the key kept where the agent cannot read it. A
chunk with a missed plant is thrown away and proofread again with fresh plants. The planted errors
are not a test of the agent; they are the only evidence that it read the images.

A second lesson from the same run: when two readings of the same line disagree, an agent asked to
merge them tends to drop the word in dispute rather than pick one. Disagreements need a third
reading or a rule, not a merger.

## 3. Known answers beat real data for testing

Every stage is tested on a disc that the code draws itself: test patterns, tones, and subtitle lines
written for the project. Because every line and colour is known, a test can say "this is wrong"
instead of "this looks plausible". The colour bug above was found this way, by the second pull
request, after a visual check had passed it.

## 4. Check a claim against the code that enforces it

Before going public, the README was checked against the tests, and two claims were too strong. The
whole-repository review then found six more, and a claim-by-claim check of this documentation's
first drafts against the code found about twenty, among them how many packs the sampled scan reads.
Each came from reading a test's name or a docstring instead of the code path that enforces the
claim: the guard keeps the proofreader to **a** chunk, not to **its** chunk; the work folder needs
about four times the disc, not twice; a damaged subtitle is skipped, not refused.

The same review found that the source archive could not pass its own tests, although all 103 passed
from the checkout: the archive carried the plugin's tests but not the plugin. Test the thing you
ship, not the folder you build it from.

## 5. A hook is the only fence around a subagent, and by default it fails open

Claude Code cannot limit a plugin's subagent to a folder through its definition, so the plugin uses
a `PreToolUse` hook that refuses the proofreader every path outside a chunk folder. Two things about
that fence:

- When a hook cannot start or crashes, Claude Code lets the call through unless the hook sets
  `"onFailure": "block"`. The guard did not at 0.1.0, and it accepted any chunk folder rather
  than the one the agent was given. Both were fixed the day after the release (findings 1 and 2 of
  the [security audit](security.md)). The fix ties each agent run to its chunk through the
  `agent_id` Claude Code puts in every call a subagent makes, which only a run inside Claude Code
  could confirm: the guard's tests feed it the JSON they expect.
- On a Mac without the Xcode command line tools, `/usr/bin/python3` is a stub that opens an install
  dialog. A hook that calls it on every tool call would raise that dialog on every tool call, so the
  guard asks `xcode-select` first.

## 6. Know everything you install, CI included

A test-only Homebrew package, used to author the sample disc, turned out to bring in a library this
project does not use. The README told users to install that package. The check before going public
found it; the README no longer lists it, and only CI, which needs it for the sample disc, installs
it. Read the whole dependency tree of every package, in the docs and in CI, before saying what a
project installs.

## 7. A fresh setup finds what the tests do not

Before the release, the plugin was run end to end in a clean Claude Code setup on the sample disc.
It found a rough edge that 103 tests had not: a file name given with its `.mkv` ending was not
accepted. It also showed that a non-interactive Claude Code run with a fixed list of allowed tools
refuses shell variables in commands, so the skill's examples use plain paths.

## 8. A review answers the question it is asked

Both independent reviews were worth their cost. The design review found two blockers and cut the
first version to an honest size; the whole-repository review found three blocking problems and a
broken source archive. Neither was asked whether the project met its author's own standards, and
neither noticed that the repository had no documentation beyond the README. That was noticed by a
person, just before the release. The documentation in this folder was written then, and it should
have been written while each part was fresh. A review prompt should carry the project's standards as
well as its questions.

## 9. Going public is a release of everything at once

Making the repository public published every commit message, every pull request text and its edit
history, and the logs of every Actions run, not only the files. Each of those was checked for
private material before the switch. The release settings went in, in order, before the merge that
releases: the repository public, the `pypi` environment limited to version tags, the rules on
`main`, and the PyPI publisher confirmed field by field.

The first upload was refused all the same: PyPI found no trusted publisher matching the release
job, although every field had been checked. Saving the pending publisher again on PyPI and
re-running the release job published 0.1.0; the cause is not known. The job had been built so that
a re-run retries the release, and that made the failure cheap. Build a release so that it can be
retried.

## 10. Read the annotation before debugging

Just before the release, `main` turned red with no change to the code: GitHub had no macOS runner
free for fifteen minutes and cancelled the jobs, and a cancelled job reports as a failure. The run's
annotations said so in one line. A re-run passed.

## The one-line version

Give every check an input it must reject, give every AI step a planted error it must find, and write
the documentation while you build, not after someone asks for it.
