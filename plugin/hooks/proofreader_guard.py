"""PreToolUse guard: the subtitle proofreader reads and writes only inside a proofreading chunk.

Claude Code runs this, through guard.sh, before every tool call while the plugin is enabled and
passes the call as JSON on stdin. For the plugin's subtitle-proofreader agent it allows only Read of a chunk's
cues.json, contact sheets and answers.json, and Write of its answers.json; anything else is
refused with exit status 2, so the agent cannot reach the key that scores it or any other file.
Calls from every other agent, and from the main session, pass untouched.

Runs on the system python3 (3.9 on macOS), so it uses nothing newer.
"""
from __future__ import annotations

import json
import os
import re
import sys

AGENT = "dvd-to-plex:subtitle-proofreader"
CHUNK = r"/review/[^/]+/chunk_\d{2}/"
READABLE = re.compile(CHUNK + r"(cues\.json|sheet_\d{2}\.png|answers\.json)$")
WRITABLE = re.compile(CHUNK + r"answers\.json$")


def decide(call: dict) -> str | None:
    """Why CALL is refused, or None when it may run."""
    if call.get("agent_type") != AGENT:
        return None
    tool = call.get("tool_name")
    if tool not in ("Read", "Write"):
        return f"the proofreader uses only Read and Write, not {tool}"
    raw = (call.get("tool_input") or {}).get("file_path")
    if not isinstance(raw, str) or not os.path.isabs(raw):
        return "the proofreader needs an absolute file path"
    path = os.path.normpath(raw)
    if path != raw.rstrip("/") or os.path.islink(path):
        return f"{raw} is not a plain path inside a chunk folder"
    if not (WRITABLE if tool == "Write" else READABLE).search(path):
        return (f"{raw} is outside the chunk; the proofreader reads cues.json and the sheets and "
                "writes answers.json, all in its own chunk folder")
    return None


def main() -> int:
    text = sys.stdin.read()
    try:
        call = json.loads(text)
        if not isinstance(call, dict):
            raise ValueError("not an object")
    except ValueError:
        if AGENT in text:                         # cannot read the call, but it is the proofreader's
            print("dvd-to-plex guard: unreadable tool call refused", file=sys.stderr)
            return 2
        return 0
    reason = decide(call)
    if reason:
        print(f"dvd-to-plex guard: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
