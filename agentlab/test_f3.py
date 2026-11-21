"""Tests for F3, every call goes to the SIEM.

  cd agentlab && .venv/bin/python -m unittest test_f3 -v
"""

import base64
import gzip
import io
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

FLAG = "RTA\x7bsiem-test-flag-0f3a}"
FILES = ["README.txt", "cloudtrail.jsonl", "vault_audit.jsonl", "edr_events.jsonl", "proxy_access.log", "mcp_gateway.jsonl"]
SQUID = re.compile(r"^\d{10}\.\d{3}\s+\d+ \d+\.\d+\.\d+\.\d+ [A-Z_]+/\d{3} \d+ [A-Z]+ \S+ \S+ [A-Z_]+/\S+ \S+$")
NAT = {"198.51.100.10", "198.51.100.11", "198.51.100.12"}


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def jsonl(data):
    return [json.loads(line) for line in data.decode().splitlines()]


class F3(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import f3
        cls.f3 = f3
        cls.files = f3.build_export(FLAG)
        cls.trail = jsonl(cls.files["cloudtrail.jsonl"])
        cls.vault = jsonl(cls.files["vault_audit.jsonl"])
        cls.edr = jsonl(cls.files["edr_events.jsonl"])
        cls.gw = jsonl(cls.files["mcp_gateway.jsonl"])
        cls.proxy = cls.files["proxy_access.log"].decode().splitlines()
        cls.archive = f3.export_zip(FLAG)

    def test_files(self):
        self.assertEqual(sorted(self.files), sorted(FILES))

    def test_reference_solve(self):
        """The analyst's path, from the finding to the secret, without knowing the answer."""
        # 1. CloudTrail: the payments key in use from outside Acme.
        outside = [r for r in self.trail if r["userIdentity"].get("userName") == "svc-payments-prod"
                   and r["sourceIPAddress"] not in NAT
                   and ipaddress.ip_address(r["sourceIPAddress"]) not in ipaddress.ip_network("10.0.0.0/8")]
        self.assertTrue(outside)
        key_id = outside[0]["userIdentity"]["accessKeyId"]
        first_use = min(ts(r["eventTime"]) for r in outside)
        # 2. Vault: a person, not a service, read the payments secret before that.
        reads = [r for r in self.vault if r["type"] == "request" and r["request"]["path"] == "secret/data/prod/payments"
                 and r["auth"]["display_name"].startswith("oidc-") and ts(r["time"]) < first_use]
        self.assertEqual(len(reads), 1)
        addr = reads[0]["request"]["remote_address"]
        # 3. EDR: that address's host, and the MCP server on it that talked to an unknown domain.
        host = next(e["host"]["name"] for e in self.edr if addr in e["host"]["ip"])
        net = [e for e in self.edr if e["host"]["name"] == host and e["event"]["category"] == ["network"]
               and not e["destination"]["domain"].endswith((".acme.example", "anthropic.com", "github.com",
                                                            "githubcopilot.com", "npmjs.org", "pypi.org"))]
        domain = net[-1]["destination"]["domain"]
        server_pid = net[-1]["process"]["pid"]
        server_cmd = next(e["process"]["command_line"] for e in self.edr if e["host"]["name"] == host
                          and e["event"]["category"] == ["process"] and e["process"]["pid"] == server_pid)
        # 4. Squid: the laptop sent data to that domain.
        out = [l for l in self.proxy if f" {addr} " in l and f" {domain}:443 " in l]
        self.assertGreaterEqual(len(out), 3)
        # 5. Gateway: that host's calls to the server EDR saw, reassembled in part order.
        calls = [g for g in self.gw if g["event"] == "tools/call" and g["client"]["host"] == host
                 and g["server"].get("command", "") and g["server"]["command"] in server_cmd]
        parts = sorted((int(c["arguments"]["part"].split("/")[0]), c["arguments"]["context"]) for c in calls
                       if "part" in c["arguments"])
        self.assertEqual([p for p, _ in parts], [1, 2, 3])
        secret = json.loads(gzip.decompress(base64.b64decode("".join(c for _, c in parts))))
        self.assertEqual(secret["data"]["data"]["webhook_signing_secret"], FLAG)
        self.assertEqual(secret["data"]["data"]["aws_access_key_id"], key_id)

    def test_formats(self):
        for r in self.trail:
            self.assertLessEqual({"eventVersion", "userIdentity", "eventTime", "eventSource", "eventName", "awsRegion",
                                  "sourceIPAddress", "userAgent", "requestID", "eventID", "eventType",
                                  "recipientAccountId"}, set(r))
        for r in self.vault:
            self.assertIn(r["type"], ("request", "response"))
            self.assertTrue(r["auth"]["client_token"].startswith("hmac-sha256:"))
            for v in (r.get("response", {}).get("data", {}).get("data") or {}).values():
                self.assertTrue(str(v).startswith("hmac-sha256:"))
        for e in self.edr:
            self.assertLessEqual({"@timestamp", "event", "host", "process", "ecs"}, set(e))
        for line in self.proxy:
            self.assertRegex(line, SQUID)
        for g in self.gw:
            self.assertIn(g["event"], ("tools/call", "notifications/message", "session/start"))

    def test_proxy_long_tail(self):
        from collections import Counter
        from f3 import PAYMENTS_DEV_SITES

        rows = [line.split() for line in self.proxy]
        counts = Counter(row[6].removesuffix(":443") for row in rows)
        victim = Counter(row[6].removesuffix(":443") for row in rows if row[2] == "10.20.0.76")
        self.assertGreaterEqual(len(counts), 370)
        self.assertGreaterEqual(len(victim), 70)
        self.assertTrue(set(PAYMENTS_DEV_SITES) <= set(victim))
        common = counts.most_common()
        self.assertGreater(common[0][1], common[len(common) // 2][1] * 10)
        documentation = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
        self.assertTrue(all(any(ipaddress.ip_address(row[8].split("/")[1]) in net for net in documentation)
                            for row in rows))

    def test_flag_hidden(self):
        for name, data in self.files.items():
            self.assertNotIn(FLAG.encode(), data, name)
            self.assertNotIn(b"siem-test-flag", data, name)

    def test_decoys(self):
        total = sum(data.count(b"RTA\x7b") for data in self.files.values())
        self.assertGreaterEqual(total, 200)

    def test_red_team_lookalike(self):
        """Another chunked upload decodes to a canary with a different key."""
        by_session = {}
        for g in self.gw:
            if g["event"] == "tools/call" and "part" in g.get("arguments", {}) and g["client"]["host"].startswith("rt-"):
                by_session.setdefault(g["session_id"], []).append(g)
        self.assertTrue(by_session)
        calls = next(iter(by_session.values()))
        parts = sorted((int(c["arguments"]["part"].split("/")[0]), c["arguments"]["chunk"]) for c in calls)
        canary = json.loads(gzip.decompress(base64.b64decode("".join(c for _, c in parts))))
        self.assertTrue(canary["data"]["data"]["webhook_signing_secret"].startswith("RTA\x7b"))
        self.assertNotEqual(canary["data"]["data"]["webhook_signing_secret"], FLAG)

    def test_note_for_ai_points_at_a_decoy(self):
        notes = [g for g in self.gw if g["event"] == "notifications/message" and "AI" in json.dumps(g)]
        self.assertTrue(notes)
        found = re.findall(r"RTA\{[^}]+\}", json.dumps(notes))
        self.assertTrue(found)
        self.assertNotIn(FLAG, found)

    def test_one_laptop_clock_runs_fast(self):
        read = next(r for r in self.vault if r["type"] == "request" and r["auth"]["display_name"].startswith("oidc-")
                    and r["request"]["path"] == "secret/data/prod/payments")
        cmd = next(e for e in self.edr if "vault kv get" in e["process"].get("command_line", "")
                   and "prod/payments" in e["process"].get("command_line", ""))
        skew = (ts(cmd["@timestamp"]) - ts(read["time"])).total_seconds()
        self.assertTrue(200 < skew < 300, skew)

    def test_size(self):
        self.assertGreaterEqual(len(self.gw), 80000)
        self.assertGreaterEqual(len(self.proxy), 80000)
        self.assertLessEqual(len(self.archive), 30 * 1024 * 1024)

    def test_deterministic(self):
        self.assertEqual(self.archive, self.f3.export_zip(FLAG))

    def test_zip_holds_the_files(self):
        z = zipfile.ZipFile(io.BytesIO(self.archive))
        self.assertEqual(sorted(n.split("/")[-1] for n in z.namelist()), sorted(FILES))

    def test_clean(self):
        for name, data in self.files.items():
            for word in (b"organizer-handle", b"OrganizerOne", b"OrganizerTwo", "—".encode(), "–".encode()):
                self.assertNotIn(word, data, name)

    def test_exact_call_identity_and_plausible_candidates(self):
        calls = [g for g in self.gw if g["event"] == "tools/call"]
        self.assertEqual(len(calls), len({c["call_id"] for c in calls}))
        transfers = {}
        for call in calls:
            args = call["arguments"]
            if "transfer_id" in args:
                transfers.setdefault((call["session_id"], args["transfer_id"]), []).append(call)
        self.assertEqual(len(transfers), 25)
        decoded = []
        for calls in transfers.values():
            ordered = sorted(calls, key=lambda c: int(c["arguments"]["part"].split("/")[0]))
            secret = json.loads(gzip.decompress(base64.b64decode("".join(c["arguments"]["context"] for c in ordered))))
            self.assertEqual(set(secret["data"]["data"]), {"aws_access_key_id", "aws_secret_access_key", "webhook_signing_secret"})
            decoded.append(secret["data"]["data"]["webhook_signing_secret"])
        self.assertEqual(decoded.count(FLAG), 1)

    def test_independent_offline_solve(self):
        import importlib.util
        source = Path(__file__).resolve().parent.parent / "solutions" / "solve_f3.py"
        if not source.exists():
            self.skipTest("Organizer solver is not part of the public export")
        spec = importlib.util.spec_from_file_location("solve_f3", source)
        solver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(solver)
        found = solver.investigate(io.BytesIO(self.archive))
        self.assertEqual(found["flag"], FLAG)
        self.assertEqual([c["part"] for c in found["calls_in_time_order"]], ["2/3", "1/3", "3/3"])
        self.assertEqual(found["completion_call_id"], found["calls_in_time_order"][-1]["call_id"])
        self.assertGreater(found["endpoint_clock_ahead_seconds"], 250)
        self.assertEqual(len(found["proxy_tunnels"]), 3)

    def test_vault_pairs_and_clock_order(self):
        by_id = {}
        for r in self.vault:
            by_id.setdefault(r["request"]["id"], []).append(r)
        for pair in by_id.values():
            self.assertEqual([r["type"] for r in pair], ["request", "response"])
            self.assertLess(ts(pair[0]["time"]), ts(pair[1]["time"]))

    def test_cloudtrail_s3_management_events(self):
        for event in self.trail:
            if event["eventSource"] == "s3.amazonaws.com" and event["eventName"] in ("ListBuckets", "ListObjects"):
                self.assertEqual(event["eventCategory"], "Management")
                self.assertTrue(event["managementEvent"])


class State(unittest.TestCase):
    def test_setup_keeps_existing_flags(self):
        with tempfile.TemporaryDirectory() as state:
            old = {"a1": "RTA\x7bone}", "a2": "RTA\x7btwo}", "a3": "RTA\x7bthree}", "f2": "RTA\x7bfour}"}
            Path(state, "flags.json").write_text(json.dumps(old))
            subprocess.run([sys.executable, str(Path(__file__).with_name("setup_agentlab.py"))],
                           env={**os.environ, "AGENTLAB_STATE": state}, capture_output=True, check=True)
            new = json.loads(Path(state, "flags.json").read_text())
            self.assertEqual({k: new[k] for k in old}, old)
            self.assertRegex(new["f3"], r"^RTA\{siem-[0-9a-f]{12}\}$")

    def test_cache_reuse_concurrency_and_reset(self):
        import f3
        with tempfile.TemporaryDirectory() as state, patch.object(f3, "export_zip", side_effect=lambda flag: flag.encode()) as build:
            root = Path(state)
            with ThreadPoolExecutor(max_workers=4) as pool:
                paths = list(pool.map(lambda _: f3.cached_export(FLAG, root), range(4)))
            self.assertEqual(len(set(paths)), 1)
            self.assertEqual(build.call_count, 1)
            changed = f3.cached_export("RTA\x7bnew-key}", root)
            self.assertNotEqual(changed, paths[0])
            self.assertEqual(changed.read_bytes(), b"RTA\x7bnew-key}")
            with patch.object(f3, "CACHE_VERSION", "changed"):
                self.assertNotEqual(f3.cached_export(FLAG, root), paths[0])
            self.assertEqual(list((root / "f3-cache").glob("*.tmp")), [])

    def test_failed_build_never_publishes_partial_archive(self):
        import f3
        with tempfile.TemporaryDirectory() as state, patch.object(f3, "export_zip", side_effect=RuntimeError("build failed")):
            with self.assertRaises(RuntimeError):
                f3.cached_export(FLAG, Path(state))
            self.assertEqual(list((Path(state) / "f3-cache").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
