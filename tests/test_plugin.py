"""The Claude Code plugin: its manifests agree with each other, the agent and skill say what the
CLI enforces, and the proofreader guard lets that agent touch only its chunk files."""
import json
import pathlib
import re
import shutil
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"
GUARD = PLUGIN / "hooks" / "proofreader_guard.py"
WRAPPER = PLUGIN / "hooks" / "guard.sh"
AGENT = "dvd-to-plex:subtitle-proofreader"
WORK = "/Volumes/Media/dvd-to-plex/Film"
CHUNK = f"{WORK}/review/title_01.s0/chunk_03"
KEY = "/Volumes/Media/Library/Application Support/dvd-to-plex/keys/run.json"
PYTHONS = [sys.executable] + [p for p in ("/usr/bin/python3",) if shutil.which(p)]


def frontmatter(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, f"{path.name} has no frontmatter"
    return dict(line.split(": ", 1) for line in m[1].splitlines())


def guard(call, python: str = sys.executable, command: list[str] | None = None) -> tuple[int, str]:
    stdin = call if isinstance(call, str) else json.dumps(call)
    command = command or [python, "-I", str(GUARD)]
    p = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=30)
    return p.returncode, p.stderr


def has_command_line_tools() -> bool:
    return subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True).returncode == 0 \
        if pathlib.Path("/usr/bin/xcode-select").exists() else False


def call(tool: str, path: str | None = None, agent: str | None = AGENT) -> dict:
    c = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {}}
    if path is not None:
        c["tool_input"]["file_path"] = path
    if agent is not None:
        c["agent_type"] = agent
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

    def test_the_hook_runs_the_guard_on_every_tool(self):
        hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        (entry,) = hooks["PreToolUse"]
        self.assertEqual(entry["matcher"], "*")
        command = entry["hooks"][0]["command"]
        self.assertEqual(command, '/bin/sh "${CLAUDE_PLUGIN_ROOT}/hooks/guard.sh"')
        self.assertIn('exec /usr/bin/python3 -I "$(dirname "$0")/proofreader_guard.py"', WRAPPER.read_text())

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


class Guard(unittest.TestCase):
    allowed = [call("Read", f"{CHUNK}/cues.json"), call("Read", f"{CHUNK}/sheet_02.png"),
               call("Read", f"{CHUNK}/answers.json"), call("Write", f"{CHUNK}/answers.json")]

    def test_the_proofreader_reaches_only_its_chunk_files(self):
        allowed = self.allowed
        refused = [call("Write", f"{CHUNK}/cues.json"), call("Write", f"{CHUNK}/sheet_02.png"),
                   call("Read", KEY),
                   call("Read", f"{CHUNK}/../../../../keys/run.json"),
                   call("Read", f"{CHUNK}/../chunk_04/../chunk_03/cues.json"),
                   call("Read", f"{WORK}/review/title_01.s0/review.json"),
                   call("Read", "review/title_01.s0/chunk_03/cues.json"), call("Read"),
                   call("Bash"), call("Glob"), call("Grep"), call("Edit", f"{CHUNK}/answers.json"),
                   f'{{"agent_type": "{AGENT}", broken']
        for python in PYTHONS:
            for c in allowed:
                with self.subTest(python=python, call=c):
                    self.assertEqual(guard(c, python), (0, ""))
            for c in refused:
                with self.subTest(python=python, call=c):
                    code, err = guard(c, python)
                    self.assertEqual(code, 2)
                    self.assertTrue(err.startswith("dvd-to-plex guard: "), err)

    def test_every_other_caller_passes(self):
        key = KEY
        for python in PYTHONS:
            for c in (call("Read", key, agent=None), call("Bash", agent=None), call("Read", key, agent="reviewer"),
                      call("Read", key, agent="other-plugin:subtitle-proofreader"), "not json"):
                with self.subTest(python=python, call=c):
                    self.assertEqual(guard(c, python), (0, ""))

    @unittest.skipUnless(has_command_line_tools(), "the Xcode command line tools are not installed")
    def test_the_wrapper_runs_the_guard(self):
        sh = ["/bin/sh", str(WRAPPER)]
        for c in self.allowed + [call("Read", "/etc/hosts", agent=None)]:
            with self.subTest(call=c):
                self.assertEqual(guard(c, command=sh), (0, ""))
        for c in (call("Bash"), call("Read", f"{CHUNK}/../../../../keys/run.json")):
            with self.subTest(call=c):
                code, err = guard(c, command=sh)
                self.assertEqual(code, 2)
                self.assertTrue(err.startswith("dvd-to-plex guard: "), err)

    def test_without_the_command_line_tools_only_the_proofreader_is_refused(self):
        import tempfile
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertEqual(text.count("/usr/bin/xcode-select -p"), 1)
        with tempfile.TemporaryDirectory() as tmp:          # no proofreader_guard.py beside it
            bare = pathlib.Path(tmp) / "guard.sh"
            bare.write_text(text.replace("/usr/bin/xcode-select -p", "/usr/bin/false"), encoding="utf-8")
            sh = ["/bin/sh", str(bare)]
            code, err = guard(call("Read", f"{CHUNK}/cues.json"), command=sh)
            self.assertEqual(code, 2)
            self.assertIn("command line tools", err)
            compact = json.dumps(call("Read", f"{CHUNK}/cues.json"), separators=(",", ":"))
            self.assertEqual(guard(compact, command=sh)[0], 2, "the key matches without spaces too")
            mention = call("Write", "/tmp/notes.json", agent=None)
            mention["tool_input"]["content"] = json.dumps(call("Read", "/x"))
            for c in (call("Read", "/etc/hosts", agent=None), call("Bash", agent="reviewer"), mention, "not json"):
                with self.subTest(call=c):
                    self.assertEqual(guard(c, command=sh), (0, ""))


if __name__ == "__main__":
    unittest.main()
