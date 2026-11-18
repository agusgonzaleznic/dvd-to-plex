---
name: dvd-to-plex
description: Turns an unscrambled DVD-Video (a mounted disc, a folder holding VIDEO_TS, or an ISO image) into lossless MKV files under Plex names, with the disc subtitles read into proofread SRT text, by driving the dvd-to-plex command stage by stage. Use when the user wants to rip, remux, archive or add a DVD to a Plex library.
---

# DVD to Plex

You drive the `dvd-to-plex` command through its stages. The command does every check itself; your
job is to run it in order, ask the user what only they know, hand subtitle chunks to the
proofreader agent, and report what happened.

## Before you start

- Run `dvd-to-plex --version`. If it is missing, tell the user to install it
  (`uv tool install dvd-to-plex` or `pipx install dvd-to-plex`) and stop.
- It runs on macOS on Apple Silicon, with `ffmpeg` and `ffprobe` on the PATH and the Xcode
  command line tools (`swiftc`) for the subtitle reader. If one is missing, say which and stop.
- Agree on a work folder with the user (default `~/Movies/dvd-to-plex/<disc name>`). It needs free
  space for about twice the disc.

## Rules for the whole run

- Read every command's exit status: 0 done, 1 an error or a failed check, 3 refused.
- **Exit 3:** show the command's message exactly as printed, stop the whole run, and add nothing:
  no other tool, setting or route around it.
- **Exit 1:** show the message and stop that stage. Never work around a failed check by editing a
  report, a key, an answers file or anything else the command wrote.
- Never open the key folder (`~/Library/Application Support/dvd-to-plex/keys`, or
  `DVDTOPLEX_KEYS` when set), and never give its path to an agent.
- Never write a chunk's `answers.json` yourself and never proofread a chunk yourself. Only the
  proofreader agent does that.

## Steps

1. **Inspect.** `dvd-to-plex inspect SOURCE --json`. Summarise for the user: each title's number,
   length, audio and subtitle languages and chapter count, and which title looks like the film.
2. **Ask, in one message:** which title is the film (or which parts are), which titles are extras
   (with a Plex extras folder and a label for each), and which to skip; the film's title, year,
   IMDb or TMDb id and edition if any; the library root and category folder (for example
   `Movies`); the default audio language; and whether they want the disc subtitles as text.
3. **Copy.** `dvd-to-plex copy SOURCE WORK`.
4. **Remux.** `dvd-to-plex remux WORK --titles N,M` with the titles the user kept, and
   `--prefer-audio LANG` if they named a default audio language. A title whose timestamps restart
   comes out as `title_NN_partK.mkv`; each part needs its own entry in the names file.
5. **Subtitles as text** (skip if the user said no, or no file has disc subtitles):
   1. `dvd-to-plex ocr WORK` (add `--lang xx` only if a track's language tag is wrong).
   2. `dvd-to-plex review-prep WORK`. It prints the chunks per track.
   3. For each folder `WORK/review/<track>/chunk_NN/`, launch one `dvd-to-plex:subtitle-proofreader`
      agent on model `sonnet` at effort `medium`. Start several in one message. The prompt is
      only: `Proofread the chunk in <absolute path of the chunk folder>.` Give no subtitle text,
      hints or expectations.
   4. `dvd-to-plex assemble WORK`. For each chunk it reports as failed, run the redo command it
      prints (`dvd-to-plex review-prep WORK --redo TRACK/N`) and launch the proofreader again for
      that chunk on model `opus` at effort `high`, then run `assemble` again. If a chunk fails on
      Opus too, stop retrying and tell the user which track has no SRT; everything else goes on.
   5. **Extras' subtitles go into the file**, because Plex ignores sidecar subtitles next to a
      local extra: `dvd-to-plex embed WORK --files <the extras that have an SRT>`. The film's SRTs
      stay in `WORK/subs`; the next step places them beside the film.
6. **Names.** Write `WORK/names.json` from the user's answers, in the format
   `dvd-to-plex name --help` shows; every file in `WORK/out` needs a role, `skip` included. Run
   `dvd-to-plex name WORK LIBRARY --dry-run`, show the user the planned paths, and run it without
   `--dry-run` once they agree.
7. **Verify.** `dvd-to-plex verify WORK`.

## Report

Tell the user, briefly: the film's folder in the library, how many files and subtitle tracks were
placed, any chunk that needed a redo or failed, and any stage that stopped and why. If embed ran,
say that `WORK/retired-<date>/` holds the files it replaced, and that deleting it is their call.
