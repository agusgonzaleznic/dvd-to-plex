#!/bin/sh
# Claude Code runs this before every tool call while the dvd-to-plex plugin is enabled, with the
# call as JSON on stdin. The guard itself is proofreader_guard.py on the system python3. This shim
# makes every failure refuse the proofreader's call and let every other call pass; hooks.json sets
# "onFailure": "block" for what the shim cannot cover, the shim itself not starting or timing out.
#
# Exit 2 counts as the guard's refusal only with the guard's own message, because python3 also
# exits 2 when it cannot open the script.
#
# Without the Xcode command line tools, /usr/bin/python3 is a stub that asks to install them, and
# it would ask on every tool call. So without them the guard does not run: the proofreader's calls
# are refused (it has no chunks to read anyway, since the subtitle reader needs those tools too) and
# every other call passes. The same happens when the guard fails or python3 is missing. The pattern
# does not match an escaped copy of the key inside a string value. It does match the key inside a
# nested object of a tool input, so such a call is refused as well, which errs on the safe side.
call=$(/bin/cat)
if /usr/bin/xcode-select -p >/dev/null 2>&1; then
  err=$(printf '%s\n' "$call" | /usr/bin/python3 -I "$(/usr/bin/dirname "$0")/proofreader_guard.py" 2>&1 >/dev/null)
  rc=$?
  case "$rc:$err" in
    0:*) exit 0 ;;
    "2:dvd-to-plex guard: "*) printf '%s\n' "$err" >&2; exit 2 ;;
  esac
  reason="the guard failed (exit status $rc)"
else
  reason="the proofreader needs the Xcode command line tools"
fi
if printf '%s\n' "$call" | /usr/bin/grep -E '"agent_type"[[:space:]]*:[[:space:]]*"dvd-to-plex:subtitle-proofreader"' >/dev/null; then
  echo "dvd-to-plex guard: $reason" >&2
  exit 2
fi
exit 0
