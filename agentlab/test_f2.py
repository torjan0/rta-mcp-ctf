"""Tests for F2, the black box transcript.

  cd agentlab && .venv/bin/python -m unittest test_f2 -v
"""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = json.loads((HERE / "f2_schema.json").read_text())


def sample_lines():
    import f2
    t = f2.Transcript(seed=1, cwd="/home/sam/invoice-sync", branch="main",
                      start=datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc))
    t.meta("permission-mode", permissionMode="bypassPermissions")
    t.prompt("upgrade boto3")
    t.think("Let me look at the requirements first.")
    call = t.tool("Read", {"file_path": "/home/sam/invoice-sync/requirements.txt"})
    t.result(call, "boto3==1.28.0\n")
    t.say("Done.")
    return t.lines()


class Writer(unittest.TestCase):
    def test_one_record_per_line(self):
        for line in sample_lines():
            self.assertNotIn("\n", line)
            self.assertIsInstance(json.loads(line), dict)

    def test_fields_known(self):
        for rec in map(json.loads, sample_lines()):
            t = rec["type"]
            self.assertLessEqual(set(rec), set(SCHEMA[t]), t)
            msg = rec.get("message")
            if isinstance(msg, dict):
                self.assertLessEqual(set(msg), set(SCHEMA[f"{t}.message"]), t)
                if "usage" in msg:
                    self.assertLessEqual(set(msg["usage"]), set(SCHEMA[f"{t}.message.usage"]))
                for block in msg["content"] if isinstance(msg["content"], list) else []:
                    self.assertLessEqual(set(block), set(SCHEMA[f"block.{block['type']}"]), block["type"])
            if isinstance(rec.get("toolUseResult"), dict) and "stdout" in rec["toolUseResult"]:
                self.assertLessEqual(set(rec["toolUseResult"]), set(SCHEMA["toolUseResult.bash"]))

    def test_parent_chain(self):
        chained = [r for r in map(json.loads, sample_lines()) if "uuid" in r]
        self.assertIsNone(chained[0]["parentUuid"])
        for prev, cur in zip(chained, chained[1:]):
            self.assertEqual(cur["parentUuid"], prev["uuid"])

    def test_timestamps_rise(self):
        stamps = [r["timestamp"] for r in map(json.loads, sample_lines()) if "timestamp" in r]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(len(stamps), len(set(stamps)))

    def test_results_match_calls(self):
        seen = set()
        for rec in map(json.loads, sample_lines()):
            content = (rec.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if block["type"] == "tool_use":
                    seen.add(block["id"])
                if block["type"] == "tool_result":
                    self.assertIn(block["tool_use_id"], seen)


FLAG = "RTA\x7bblack-box-test-flag}"
LOOKALIKE = "boto3-docs.com"


def story(flag=FLAG):
    import f2
    return f2.session_jsonl(flag)


def bash_commands(text):
    out = []
    for rec in map(json.loads, text.splitlines()):
        content = (rec.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_use" and block["name"] == "Bash":
                out.append(block["input"]["command"])
    return out


def exfil_value(text):
    import re
    cmd = next(c for c in bash_commands(text) if LOOKALIKE in c and "curl" in c)
    return re.search(r"[?&]d=([A-Za-z0-9+/=]+)", cmd).group(1)


class Story(unittest.TestCase):
    def test_flag_not_plain(self):
        self.assertNotIn("RTA\x7b", story())
        self.assertNotIn("black-box-test-flag", story())

    def test_exfil_decodes_to_env_with_flag(self):
        import base64
        env = base64.b64decode(exfil_value(story())).decode()
        self.assertIn(f"DEPLOY_TOKEN={FLAG}", env)

    def test_red_herrings(self):
        import base64
        import re
        text = story()
        self.assertGreaterEqual(sum("curl" in c for c in bash_commands(text)), 3)
        blobs = set(re.findall(r"[A-Za-z0-9+/]{16,}={0,2}", text))
        hits = []
        for b in blobs:
            try:
                if FLAG in base64.b64decode(b + "=" * (-len(b) % 4)).decode(errors="ignore"):
                    hits.append(b)
            except ValueError:
                pass
        self.assertGreaterEqual(len(blobs), 2)
        self.assertEqual(len(set(hits)), 1)

    def test_deterministic(self):
        self.assertEqual(story(), story())

    def test_odd_flag(self):
        import base64
        odd = 'RTA\x7bq"uo\\te%41}'
        env = base64.b64decode(exfil_value(story(odd))).decode()
        self.assertIn(f"DEPLOY_TOKEN={odd}", env)

    def test_size(self):
        text = story()
        lines = text.splitlines()
        calls = sum('"type":"tool_use"' in line for line in lines)
        self.assertTrue(100 <= len(lines) <= 260, len(lines))
        self.assertTrue(45 <= calls <= 80, calls)

    def test_clean(self):
        text = story()
        for word in ("organizer-handle", "OrganizerOne", "OrganizerTwo", "\u2014", "\u2013"):
            self.assertNotIn(word, text)


class Setup(unittest.TestCase):
    def test_setup_adds_missing_flag(self):
        import os
        import subprocess
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as state:
            old = {"a1": "RTA\x7bone}", "a2": "RTA\x7btwo}", "a3": "RTA\x7bthree}"}
            Path(state, "flags.json").write_text(json.dumps(old))
            def setup(*options):
                return subprocess.run([sys.executable, str(HERE / "setup_agentlab.py"), *options],
                                      env={**os.environ, "AGENTLAB_STATE": state}, capture_output=True, check=True)

            first = setup()
            flags_file = Path(state, "flags.json")
            new = json.loads(flags_file.read_text())
            self.assertEqual({k: new[k] for k in old}, old)
            self.assertRegex(new.get("f2", ""), r"^RTA\{black-box-[0-9a-f]{12}\}$")
            self.assertEqual(list(Path(state).iterdir()), [flags_file])
            self.assertNotIn(b"RTA\x7b", first.stdout + first.stderr)
            for option in ("--new-flags", "--force"):
                setup(option)
                rotated = json.loads(flags_file.read_text())
                self.assertEqual(new.keys(), rotated.keys())
                self.assertTrue(all(value != rotated[key] for key, value in new.items()))
                new = rotated


if __name__ == "__main__":
    unittest.main()
