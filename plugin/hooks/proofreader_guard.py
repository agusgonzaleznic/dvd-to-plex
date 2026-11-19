"""PreToolUse guard: the subtitle proofreader reads and writes only inside its own chunk folder.

Claude Code runs this, through guard.sh, before every tool call while the plugin is enabled and
passes the call as JSON on stdin. For the plugin's subtitle-proofreader agent it allows only Read of
cues.json, the contact sheets and answers.json in one chunk folder, and Write of answers.json
there; anything else is refused with exit status 2, so the agent cannot reach the key that scores
it, the OCR output, another chunk or any other file. Calls from every other agent, and from the main
session, pass untouched.

The chunk is the folder of the agent run's first allowed Read. That Read is decided before any
file has reached the agent, so no text from the disc can choose the chunk. Claude Code gives each
agent run its own agent_id, so a redo of a chunk is a new run with a new binding. The binding is
kept in a private folder under the user's temporary folder.

Anything unexpected in a proofreader's call is refused, an error in this script included.

Runs on the system python3 (3.9 on macOS), so it uses nothing newer.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import tempfile

AGENT = "dvd-to-plex:subtitle-proofreader"
FILE = re.compile(r"(?P<chunk>/.+/review/[^/]+/chunk_[0-9]{2})/(?P<name>[^/]+)")
READABLE = re.compile(r"cues\.json|sheet_[0-9]{2}\.png|answers\.json")
WRITABLE = "answers.json"
STATE = "dvd-to-plex-guard"


class Refused(Exception):
    """A proofreader call the guard does not allow; the message says why."""


def state_folder() -> str:
    """This user's private folder of bindings, made with mode 0700 when missing."""
    path = os.path.join(tempfile.gettempdir(), STATE)
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise Refused(f"{path} is not a private folder of this user")
    return path


def bind(call: dict, chunk: str, reading: bool) -> None:
    """Refuse unless CHUNK is the chunk this agent run is bound to; a first Read binds it."""
    session, agent = call.get("session_id"), call.get("agent_id", "main")
    if not (isinstance(session, str) and session and isinstance(agent, str) and agent):
        raise Refused("the call does not say which agent run made it")
    real = os.path.realpath(chunk)
    folder = state_folder()
    name = hashlib.sha256(f"{session}\0{agent}".encode()).hexdigest()
    path = os.path.join(folder, name)
    try:
        with open(path, encoding="utf-8") as f:
            bound = f.read()
    except FileNotFoundError:
        if not reading:
            raise Refused("read cues.json in your chunk folder before writing") from None
        draft = os.path.join(folder, f"{name}.{os.getpid()}")
        with open(draft, "x", encoding="utf-8") as f:
            f.write(real)
        try:
            os.link(draft, path)                  # atomic: one of two parallel first calls wins
            bound = real
        except FileExistsError:
            with open(path, encoding="utf-8") as f:
                bound = f.read()
        finally:
            os.unlink(draft)
    if bound != real:
        raise Refused(f"{chunk} is not this agent's chunk folder; it works only in {bound}")


def check(call: dict) -> None:
    """Refuse a proofreader CALL that is not a Read or Write of its own chunk's files."""
    tool = call.get("tool_name")
    if tool not in ("Read", "Write"):
        raise Refused(f"the proofreader uses only Read and Write, not {tool}")
    tool_input = call.get("tool_input")
    raw = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(raw, str) or not raw.startswith("/"):
        raise Refused("the proofreader needs an absolute file path")
    if raw.startswith("//") or os.path.normpath(raw) != raw:
        raise Refused(f"{raw} is not a plain path: no '.', '..', doubled or trailing '/'")
    m = FILE.fullmatch(raw)
    if not m or not (READABLE.fullmatch(m["name"]) if tool == "Read" else m["name"] == WRITABLE):
        raise Refused(f"{raw} is outside the chunk; the proofreader reads cues.json and the sheets "
                      "and writes answers.json, all in its own chunk folder")
    chunk = m["chunk"]
    review = chunk.rsplit("/", 2)[0]
    for part in (review, os.path.dirname(chunk), chunk, raw):
        if os.path.islink(part):
            raise Refused(f"{part} is a symbolic link")
    bind(call, chunk, reading=tool == "Read")


def decide(text: str) -> str | None:
    """Why the call in TEXT is refused, or None when it may run."""
    try:
        call = json.loads(text)
    except ValueError:
        call = None
    if not isinstance(call, dict):
        return "unreadable tool call refused" if AGENT in text else None
    if call.get("agent_type") != AGENT:
        return None
    try:
        check(call)
    except Refused as e:
        return str(e)
    except Exception as e:                        # a fault here must not let the proofreader through
        return f"the guard failed ({type(e).__name__}), so the call is refused"
    return None


def main() -> int:
    reason = decide(sys.stdin.read())
    if reason:
        print(f"dvd-to-plex guard: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
