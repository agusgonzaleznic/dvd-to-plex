"""The Claude Code plugin: its manifests agree with each other, the agent and skill say what the
CLI enforces, and the proofreader guard lets that agent touch only its own chunk's files."""
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"
GUARD = PLUGIN / "hooks" / "proofreader_guard.py"
WRAPPER = PLUGIN / "hooks" / "guard.sh"
AGENT = "dvd-to-plex:subtitle-proofreader"
WORK = "/Volumes/Media/dvd-to-plex/Film"
CHUNK = f"{WORK}/review/title_01.s0/chunk_03"
OTHER = f"{WORK}/review/title_01.s0/chunk_04"
ELSEWHERE = "/Volumes/Media/dvd-to-plex/Other/review/title_01.s0/chunk_03"
KEY = "/Volumes/Media/Library/Application Support/dvd-to-plex/keys/run.json"


def frontmatter(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, f"{path.name} has no frontmatter"
    return dict(line.split(": ", 1) for line in m[1].splitlines())


def guard(call, tmp: str, python: str = sys.executable, command: list[str] | None = None) -> tuple[int, str]:
    """Run the guard on CALL with TMP as the temporary folder that holds its bindings."""
    stdin = call if isinstance(call, str) else json.dumps(call)
    command = command or [python, "-I", str(GUARD)]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/"), "TMPDIR": tmp}
    p = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=30, env=env)
    return p.returncode, p.stderr


def has_command_line_tools() -> bool:
    return subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True).returncode == 0 \
        if pathlib.Path("/usr/bin/xcode-select").exists() else False


# without the command line tools /usr/bin/python3 is a stub that opens an install dialog
PYTHONS = [sys.executable] + (["/usr/bin/python3"] if has_command_line_tools() else [])


def call(tool: str, path: str | None = None, agent: str | None = AGENT, run: str | None = "agent-1",
         session: str | None = "session-1") -> dict:
    c = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {}}
    if session is not None:
        c["session_id"] = session
    if path is not None:
        c["tool_input"]["file_path"] = path
    if agent is not None:
        c["agent_type"] = agent
        if run is not None:
            c["agent_id"] = run
    return c


class Manifests(unittest.TestCase):
    def test_the_marketplace_lists_the_plugin_by_its_own_name(self):
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        (entry,) = market["plugins"]
        self.assertEqual((entry["name"], entry["source"]), (plugin["name"], "./plugin"))
        self.assertTrue((ROOT / entry["source"]).is_dir())
        self.assertTrue(market["owner"]["name"])
        self.assertNotIn("version", plugin, "no version to drift from the package's")

    def test_the_hook_runs_the_guard_on_every_tool_and_blocks_when_it_cannot_run(self):
        hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        (entry,) = hooks["PreToolUse"]
        self.assertEqual(entry["matcher"], "*")
        (hook,) = entry["hooks"]
        self.assertEqual(hook["command"], '/bin/sh "${CLAUDE_PLUGIN_ROOT}/hooks/guard.sh"')
        self.assertEqual(hook["onFailure"], "block")
        self.assertTrue(0 < hook["timeout"] <= 60)
        self.assertIn('| /usr/bin/python3 -I "$(/usr/bin/dirname "$0")/proofreader_guard.py" 2>&1 >/dev/null)',
                      WRAPPER.read_text())

    def test_the_agent_has_only_read_and_write_and_names_its_model(self):
        meta = frontmatter(PLUGIN / "agents" / "subtitle-proofreader.md")
        self.assertEqual(f"dvd-to-plex:{meta['name']}", AGENT)
        self.assertEqual([t.strip() for t in meta["tools"].split(",")], ["Read", "Write"])
        self.assertEqual((meta["model"], meta["effort"]), ("sonnet", "medium"))

    def test_the_skill_follows_the_cli(self):
        meta = frontmatter(PLUGIN / "skills" / "dvd-to-plex" / "SKILL.md")
        self.assertEqual(meta["name"], "dvd-to-plex")
        self.assertLess(len(meta["description"]), 1536)
        body = (PLUGIN / "skills" / "dvd-to-plex" / "SKILL.md").read_text(encoding="utf-8")
        from dvdtoplex.cli import main
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            main(["--help"])
        commands = set(re.findall(r"^    (\S+)\s", out.getvalue(), re.M)) - {"sample"}
        used = set(re.findall(r"`dvd-to-plex ([a-z][a-z-]*)", body))
        self.assertEqual(used, commands, "the skill runs every stage and no command that does not exist")
        self.assertIn(AGENT, body)


class GuardCase(unittest.TestCase):
    def fresh(self) -> str:
        """A new empty folder, removed after the test."""
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    def assertRefused(self, result: tuple[int, str], why: str = "") -> None:
        code, err = result
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("dvd-to-plex guard: "), err)
        self.assertIn(why, err)


class Guard(GuardCase):
    def test_the_proofreader_reaches_only_its_chunk_files(self):
        allowed = [call("Read", f"{CHUNK}/cues.json"), call("Read", f"{CHUNK}/sheet_02.png"),
                   call("Read", f"{CHUNK}/answers.json"), call("Write", f"{CHUNK}/answers.json")]
        refused = [call("Write", f"{CHUNK}/cues.json"), call("Write", f"{CHUNK}/sheet_02.png"),
                   call("Read", KEY),
                   call("Read", f"{CHUNK}/../../../../keys/run.json"),
                   call("Read", f"{CHUNK}/../chunk_04/../chunk_03/cues.json"),
                   call("Read", f"{WORK}/review/title_01.s0/review.json"),
                   call("Read", "review/title_01.s0/chunk_03/cues.json"), call("Read"),
                   call("Bash"), call("Glob"), call("Grep"), call("Edit", f"{CHUNK}/answers.json"),
                   f'{{"agent_type": "{AGENT}", broken',
                   call("Read", f"{CHUNK}/sheet_０２.png"),         # digits are ASCII only
                   call("Read", f"{WORK}/review/title_01.s0/chunk_٠٣/cues.json", run="digits")]
        for python in PYTHONS:
            tmp = self.fresh()
            for c in allowed:
                with self.subTest(python=python, call=c):
                    self.assertEqual(guard(c, tmp, python), (0, ""))
            for c in refused:
                with self.subTest(python=python, call=c):
                    self.assertRefused(guard(c, tmp, python))

    def test_the_proofreader_is_held_to_the_chunk_it_read_first(self):
        for python in PYTHONS:
            tmp = self.fresh()
            g = lambda c: guard(c, tmp, python)
            self.assertEqual(g(call("Read", f"{CHUNK}/cues.json")), (0, ""))
            for c in (call("Read", f"{OTHER}/cues.json"), call("Read", f"{OTHER}/sheet_01.png"),
                      call("Write", f"{OTHER}/answers.json"), call("Read", f"{ELSEWHERE}/cues.json")):
                with self.subTest(python=python, call=c):
                    self.assertRefused(g(c), "is not this agent's chunk folder")
            # another run binds its own chunk and the first stays held; a redo is a new run
            self.assertEqual(g(call("Read", f"{OTHER}/cues.json", run="agent-2")), (0, ""))
            self.assertEqual(g(call("Write", f"{OTHER}/answers.json", run="agent-2")), (0, ""))
            self.assertRefused(g(call("Write", f"{OTHER}/answers.json")))
            self.assertEqual(g(call("Read", f"{CHUNK}/cues.json", run="agent-3")), (0, ""))
            self.assertEqual(g(call("Read", f"{OTHER}/cues.json", session="session-2")), (0, ""))
            self.assertEqual(g(call("Write", f"{CHUNK}/answers.json")), (0, ""))

    def test_a_write_before_any_read_is_refused(self):
        tmp = self.fresh()
        self.assertRefused(guard(call("Write", f"{CHUNK}/answers.json"), tmp), "read cues.json")
        self.assertEqual(guard(call("Read", f"{CHUNK}/cues.json"), tmp), (0, ""))
        self.assertEqual(guard(call("Write", f"{CHUNK}/answers.json"), tmp), (0, ""))

    def test_a_call_that_names_no_session_is_refused(self):
        tmp = self.fresh()
        self.assertRefused(guard(call("Read", f"{CHUNK}/cues.json", session=None), tmp), "which agent run")
        self.assertRefused(guard(call("Read", f"{CHUNK}/cues.json", run=""), tmp), "which agent run")
        self.assertEqual(guard(call("Read", f"{CHUNK}/cues.json", run=None), tmp), (0, ""))

    def test_paths_must_be_plain(self):
        tmp = self.fresh()
        for i, path in enumerate((f"{CHUNK}/./cues.json", f"/{CHUNK}/cues.json", f"{CHUNK}//cues.json",
                                  f"{WORK}//review/title_01.s0/chunk_03/cues.json", f"{CHUNK}/cues.json/")):
            with self.subTest(path=path):
                self.assertRefused(guard(call("Read", path, run=f"r{i}"), tmp), "is not a plain path")
        self.assertEqual(guard(call("Read", f"{CHUNK}/cues.json", run="plain"), tmp), (0, ""))

    def test_symbolic_links_on_the_chunk_path_are_refused(self):
        tmp, base = self.fresh(), pathlib.Path(self.fresh())
        film = base / "Film"
        chunk = film / "review" / "t" / "chunk_01"
        chunk.mkdir(parents=True)
        (chunk / "cues.json").write_text("{}", encoding="utf-8")
        (chunk / "sheet_01.png").symlink_to("/etc/hosts")
        (chunk.parent / "chunk_02").symlink_to(chunk)
        (film / "review" / "u").symlink_to(film / "review" / "t")
        (base / "Film2").mkdir()
        (base / "Film2" / "review").symlink_to(film / "review")
        (base / "Library").symlink_to(film)
        links = [chunk / "sheet_01.png", chunk.parent / "chunk_02" / "cues.json",
                 film / "review" / "u" / "chunk_01" / "cues.json", base / "Film2" / "review" / "t" / "chunk_01" / "cues.json"]
        for i, path in enumerate(links):
            with self.subTest(path=path):
                self.assertRefused(guard(call("Read", str(path), run=f"r{i}"), tmp), "is a symbolic link")
        self.assertEqual(guard(call("Read", str(chunk / "cues.json"), run="plain"), tmp), (0, ""))
        above = base / "Library" / "review" / "t" / "chunk_01" / "cues.json"
        self.assertEqual(guard(call("Read", str(above), run="above"), tmp), (0, ""),
                         "a link above the work folder is the user's own, such as a library on another drive")

    def test_two_first_calls_at_once_bind_one_chunk(self):
        for attempt in range(3):
            tmp = self.fresh()
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "TMPDIR": tmp}
            procs = []
            for path in (f"{CHUNK}/cues.json", f"{OTHER}/cues.json"):
                p = subprocess.Popen([sys.executable, "-I", str(GUARD)], stdin=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env, text=True)
                procs.append((p, json.dumps(call("Read", path, run="same"))))
            for p, text in procs:
                p.stdin.write(text)
            for p, _ in procs:
                p.stdin.close()
            codes = sorted(p.wait(timeout=30) for p, _ in procs)
            self.assertEqual(codes, [0, 2], f"attempt {attempt}")
            self.assertEqual(len(os.listdir(pathlib.Path(tmp) / "dvd-to-plex-guard")), 1, "no draft left")

    def test_a_binding_folder_that_is_not_private_is_refused(self):
        tmp = self.fresh()
        state = pathlib.Path(tmp) / "dvd-to-plex-guard"
        state.mkdir(mode=0o700)
        state.chmod(0o777)
        self.assertRefused(guard(call("Read", f"{CHUNK}/cues.json"), tmp), "not a private folder")
        state.rmdir()
        elsewhere = pathlib.Path(self.fresh()) / "state"
        elsewhere.mkdir(mode=0o700)
        state.symlink_to(elsewhere)
        self.assertRefused(guard(call("Read", f"{CHUNK}/cues.json"), tmp), "not a private folder")
        state.unlink()
        self.assertEqual(guard(call("Read", f"{CHUNK}/cues.json"), tmp), (0, ""))

    def test_an_error_in_the_guard_refuses_only_the_proofreader(self):
        tmp = self.fresh()
        state = pathlib.Path(tmp) / "dvd-to-plex-guard"
        state.mkdir(mode=0o700)
        binding = state / hashlib.sha256("session-1\0agent-1".encode()).hexdigest()
        binding.mkdir()                                  # a folder where the binding file goes
        self.assertRefused(guard(call("Read", f"{CHUNK}/cues.json"), tmp), "the guard failed (IsADirectoryError)")
        self.assertEqual(guard(call("Read", KEY, agent=None), tmp), (0, ""))
        binding.rmdir()
        self.assertEqual(guard(call("Read", f"{CHUNK}/cues.json"), tmp), (0, ""))

    def test_a_call_it_cannot_read_is_refused_only_for_the_proofreader(self):
        tmp = self.fresh()
        for tool_input in (["not", "an object"], "a string", None):
            c = call("Read", f"{CHUNK}/cues.json")
            c["tool_input"] = tool_input
            with self.subTest(tool_input=tool_input):
                self.assertRefused(guard(c, tmp))
                del c["agent_type"]
                self.assertEqual(guard(c, tmp), (0, ""))

    def test_every_other_caller_passes(self):
        tmp = self.fresh()
        for python in PYTHONS:
            for c in (call("Read", KEY, agent=None), call("Bash", agent=None), call("Read", KEY, agent="reviewer"),
                      call("Read", KEY, agent="other-plugin:subtitle-proofreader"), "not json"):
                with self.subTest(python=python, call=c):
                    self.assertEqual(guard(c, tmp, python), (0, ""))


class Wrapper(GuardCase):
    def shim(self, python: str = sys.executable, script: str | None = None, missing: bool = False) -> list[str]:
        """A copy of guard.sh that finds the command line tools and runs PYTHON on SCRIPT (or the guard),
        or, if MISSING, on a script that is not there."""
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertEqual((text.count("/usr/bin/xcode-select -p"), text.count("/usr/bin/python3 -I")), (1, 1))
        folder = pathlib.Path(self.fresh())
        (folder / "guard.sh").write_text(text.replace("/usr/bin/xcode-select -p", "/usr/bin/true")
                                         .replace("/usr/bin/python3 -I", f'"{python}" -I'), encoding="utf-8")
        if not missing:
            (folder / "proofreader_guard.py").write_text(script or GUARD.read_text(encoding="utf-8"), encoding="utf-8")
        return ["/bin/sh", str(folder / "guard.sh")]

    @unittest.skipUnless(has_command_line_tools(), "the Xcode command line tools are not installed")
    def test_the_wrapper_runs_the_guard(self):
        tmp, sh = self.fresh(), ["/bin/sh", str(WRAPPER)]
        for c in (call("Read", f"{CHUNK}/cues.json"), call("Write", f"{CHUNK}/answers.json"),
                  call("Read", "/etc/hosts", agent=None)):
            with self.subTest(call=c):
                self.assertEqual(guard(c, tmp, command=sh), (0, ""))
        for c in (call("Bash"), call("Read", f"{CHUNK}/../../../../keys/run.json"), call("Read", f"{OTHER}/cues.json")):
            with self.subTest(call=c):
                self.assertRefused(guard(c, tmp, command=sh))

    def test_when_the_guard_fails_only_the_proofreader_is_refused(self):
        tmp = self.fresh()
        for sh, why in ((self.shim(script="raise SystemExit(3)\n"), "the guard failed (exit status 3)"),
                        (self.shim(script="this is not python\n"), "the guard failed (exit status 1)"),
                        (self.shim(script="raise SystemExit(2)\n"), "the guard failed (exit status 2)"),
                        (self.shim(missing=True), "the guard failed (exit status 2)"),
                        (self.shim(python="/nonexistent/python3"), "the guard failed (exit status 127)")):
            with self.subTest(why=why):
                self.assertRefused(guard(call("Read", f"{CHUNK}/cues.json"), tmp, command=sh), why)
                for c in (call("Read", "/etc/hosts", agent=None), call("Bash", agent="reviewer"), "not json"):
                    self.assertEqual(guard(c, tmp, command=sh), (0, ""))
        self.assertEqual(guard(call("Read", f"{CHUNK}/cues.json"), tmp, command=self.shim()), (0, ""),
                         "the same copy with the real guard allows the call")

    def test_without_the_command_line_tools_only_the_proofreader_is_refused(self):
        tmp = self.fresh()
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertEqual(text.count("/usr/bin/xcode-select -p"), 1)
        bare = pathlib.Path(self.fresh()) / "guard.sh"         # no proofreader_guard.py beside it
        bare.write_text(text.replace("/usr/bin/xcode-select -p", "/usr/bin/false"), encoding="utf-8")
        sh = ["/bin/sh", str(bare)]
        self.assertRefused(guard(call("Read", f"{CHUNK}/cues.json"), tmp, command=sh), "command line tools")
        compact = json.dumps(call("Read", f"{CHUNK}/cues.json"), separators=(",", ":"))
        self.assertEqual(guard(compact, tmp, command=sh)[0], 2, "the key matches without spaces too")
        nested = call("Write", "/tmp/notes.json", agent=None)
        nested["tool_input"]["meta"] = {"agent_type": AGENT}
        self.assertEqual(guard(nested, tmp, command=sh)[0], 2, "a nested key matches too, and is refused")
        mention = call("Write", "/tmp/notes.json", agent=None)
        mention["tool_input"]["content"] = json.dumps(call("Read", "/x"))
        for c in (call("Read", "/etc/hosts", agent=None), call("Bash", agent="reviewer"), mention, "not json"):
            with self.subTest(call=c):
                self.assertEqual(guard(c, tmp, command=sh), (0, ""))


if __name__ == "__main__":
    unittest.main()
