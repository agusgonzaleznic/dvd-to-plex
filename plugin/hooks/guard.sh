#!/bin/sh
# Claude Code runs this before every tool call while the dvd-to-plex plugin is enabled, with the
# call as JSON on stdin. The guard itself is proofreader_guard.py on the system python3.
#
# Without the Xcode command line tools, /usr/bin/python3 is a stub that asks to install them, and
# it would ask on every tool call. So without them, the proofreader's calls are refused (it has no
# chunks to read anyway, since the subtitle reader needs those tools too) and every other call
# passes. The pattern matches the real agent_type key only, not an escaped copy inside a tool input.
if /usr/bin/xcode-select -p >/dev/null 2>&1; then
  exec /usr/bin/python3 -I "$(dirname "$0")/proofreader_guard.py"
fi
if grep -E '"agent_type"[[:space:]]*:[[:space:]]*"dvd-to-plex:subtitle-proofreader"' >/dev/null; then
  echo "dvd-to-plex guard: the proofreader needs the Xcode command line tools" >&2
  exit 2
fi
exit 0
