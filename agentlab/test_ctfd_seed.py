"""CTFd metadata changes preserve the existing F1 challenge row and solves."""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parent.parent / "deploy" / "aws" / "ctfd_seed.py"
spec = importlib.util.spec_from_file_location("ctfd_seed_test", SOURCE)
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)


class FakeAPI:
    def __init__(self, rows):
        self.rows = {row["id"]: dict(row) for row in rows}
        self.patches = []
        self.posts = []

    def ok(self, method, path, js=None):
        if method == "GET" and path.endswith("/hints"):
            return {"data": []}
        if method == "GET" and path == "/challenges?view=admin":
            return {"data": list(self.rows.values())}
        if method == "PATCH":
            cid = int(path.rsplit("/", 1)[1])
            self.rows[cid].update(js)
            self.patches.append((cid, js))
            return {"data": self.rows[cid]}
        if method == "POST" and path == "/hints":
            return {"data": {"id": len(self.patches) + 900}}
        if method == "POST":
            self.posts.append((path, js))
            raise AssertionError("Rename must not create a new CTFd challenge or flag")
        raise AssertionError((method, path))


class AccountConfigAPI:
    def __init__(self, settings):
        self.settings = dict(settings)
        self.writes = []

    def ok(self, method, path, js=None):
        if method == "GET" and path.startswith("/configs?key="):
            key = path.split("=", 1)[1]
            return {"data": [{"key": key, "value": self.settings[key]}] if key in self.settings else []}
        if method == "POST" and path == "/configs":
            key = js["key"]
            assert key not in self.settings
        elif method == "PATCH" and path.startswith("/configs/"):
            key = path.rsplit("/", 1)[1]
            assert key in self.settings
        else:
            raise AssertionError((method, path))
        self.writes.append((method, key))
        self.settings[key] = js["value"]
        return {"success": True}


class AccountSettings(unittest.TestCase):
    def test_new_install_and_idempotent_reseed(self):
        api = AccountConfigAPI({})
        seed.sync_account_settings(api, 70)
        self.assertEqual(api.settings, {"num_users": "70", "password_min_length": "8", "theme_header": seed.RESET_CSS})
        self.assertIn("/reset_password", seed.RESET_CSS)
        api.writes.clear()
        seed.sync_account_settings(api, 70)
        self.assertEqual(api.writes, [])

    def test_existing_install_and_changed_limit(self):
        api = AccountConfigAPI({"num_users": "45", "password_min_length": 1, "theme_header": "<style>body {}</style>"})
        seed.sync_account_settings(api, 70)
        self.assertEqual([method for method, _ in api.writes], ["PATCH"] * 3)
        self.assertEqual(api.settings["theme_header"], "<style>body {}</style>\n" + seed.RESET_CSS)
        api.writes.clear()
        seed.sync_account_settings(api, 12)
        self.assertEqual(api.settings["num_users"], "12")
        self.assertEqual(api.writes, [("PATCH", "num_users")])


class SeedRename(unittest.TestCase):
    def test_existing_w1_renamed_in_place_without_touching_flag_or_solves(self):
        api = FakeAPI([{"id": 17, "name": "W1, read the menu", "category": "Warm-up",
                        "value": 50, "solves": 6, "flag_id": 52}])
        f1 = seed.challenges("https://lab.example/")[0]
        with contextlib.redirect_stdout(io.StringIO()):
            seed.seed_challenges(api, [f1], {}, sync_flags=False)
            seed.seed_challenges(api, [f1], {}, sync_flags=False)
        self.assertEqual(api.rows[17]["name"], "F1, read the menu")
        self.assertEqual(api.rows[17]["category"], "Forensics")
        self.assertEqual(api.rows[17]["solves"], 6)
        self.assertEqual(api.rows[17]["flag_id"], 52)
        self.assertEqual([cid for cid, _ in api.patches], [17, 17])
        self.assertEqual(api.posts, [])

    def test_optional_a6_renamed_in_place(self):
        a6 = next(c for c in seed.challenges("https://lab.example/") if c["key"] == "a6")
        api = FakeAPI([{"id": 21, "name": "A6, The Form That Asked Too Much", "solves": 2}])
        with contextlib.redirect_stdout(io.StringIO()):
            seed.seed_challenges(api, [a6], {})
        self.assertEqual(api.rows[21]["name"], "A6, The Form That Asked Too Much (optional)")
        self.assertEqual(api.posts, [])

    def test_duplicate_old_and_new_names_are_rejected(self):
        api = FakeAPI([{"id": 17, "name": "W1, read the menu"},
                       {"id": 18, "name": "F1, read the menu"}])
        with self.assertRaises(SystemExit):
            seed.seed_challenges(api, [seed.challenges("https://lab.example/")[0]], {})


class HintAPI:
    """Just enough of CTFd's hint endpoints to check the sync."""

    def __init__(self, hints=()):
        self.hints = {h["id"]: dict(h) for h in hints}
        self.next_id = 100
        self.calls = []

    def ok(self, method, path, js=None):
        self.calls.append((method, path))
        if method == "GET" and path.endswith("/hints"):
            cid = int(path.split("/")[2])
            return {"data": [h for h in self.hints.values() if h["challenge_id"] == cid]}
        if method == "POST" and path == "/hints":
            self.next_id += 1
            self.hints[self.next_id] = dict(js, id=self.next_id)
            return {"data": self.hints[self.next_id]}
        if method == "DELETE" and path.startswith("/hints/"):
            del self.hints[int(path.rsplit("/", 1)[1])]
            return {}
        raise AssertionError((method, path))


HINTS = ["Look at the archive.", "Publishing is not installing.", "Copy the reset email."]


class SeedHints(unittest.TestCase):
    def test_hints_created_in_order_and_chained(self):
        api = HintAPI()
        seed.sync_hints(api, 7, HINTS)
        made = sorted(api.hints.values(), key=lambda h: h["id"])
        self.assertEqual([h["content"] for h in made], HINTS)
        self.assertEqual([h["title"] for h in made], ["Hint 1", "Hint 2", "Hint 3"])
        self.assertEqual({h["cost"] for h in made}, {0})
        self.assertEqual({h["challenge_id"] for h in made}, {7})
        self.assertEqual([h["requirements"]["prerequisites"] for h in made], [[], [made[0]["id"]], [made[1]["id"]]])

    def test_unchanged_hints_are_left_alone(self):
        api = HintAPI()
        seed.sync_hints(api, 7, HINTS)
        api.calls.clear()
        seed.sync_hints(api, 7, HINTS)
        self.assertEqual(api.calls, [("GET", "/challenges/7/hints")])

    def test_changed_hints_are_replaced(self):
        api = HintAPI()
        seed.sync_hints(api, 7, HINTS)
        seed.sync_hints(api, 7, HINTS[:2])
        self.assertEqual(sorted(h["content"] for h in api.hints.values()), sorted(HINTS[:2]))

    def test_a_challenge_without_hints_keeps_none(self):
        api = HintAPI([{"id": 5, "challenge_id": 7, "title": "Hint 1", "content": "old", "cost": 0}])
        seed.sync_hints(api, 7, [])
        self.assertEqual(api.hints, {})

    def test_catalog_hints(self):
        catalog = {c["name"].split(",")[0]: c for c in seed.challenges("https://lab.example/")}
        with_hints = {k for k, c in catalog.items() if c.get("hints")}
        self.assertEqual(with_hints, {"F2", "F3", "A1", "A2", "A3", "A4", "A5", "A6"})
        shared = json.loads(Path(__file__).with_name("hints.json").read_text())
        self.assertEqual(catalog["A4"]["hints"], shared["a4"])
        self.assertEqual(catalog["A6"]["hints"], shared["a6"])
        for key, hints in shared.items():
            api = HintAPI()
            seed.sync_hints(api, 7, catalog[key.upper()].get("hints", []))
            self.assertEqual([row["content"] for row in api.hints.values()], hints)
        for key in with_hints:
            hints = catalog[key]["hints"]
            self.assertTrue(1 <= len(hints) <= 5, key)
            for h in hints:
                self.assertNotIn("RTA\x7b", h, key)
                for mark in ("—", "–", ";"):
                    self.assertNotIn(mark, h, key)


class SeedBoard(unittest.TestCase):
    OWASP = {"F1": "MCP03", "F2": "MCP06", "F3": "MCP04", "A1": "MCP03", "A2": "MCP06",
             "A3": "MCP06", "A4": "MCP05", "A5": "MCP04", "A6": "MCP10", "A7": "MCP07"}

    def setUp(self):
        self.catalog = seed.challenges("https://lab.example/")

    def test_board_order(self):
        self.assertEqual([c["name"].split(",")[0] for c in self.catalog],
                         ["F1", "F2", "F3", "A1", "A2", "A3", "A4", "A5", "A6", "A7"])
        self.assertEqual([c["position"] for c in self.catalog], list(range(1, 11)))

    def test_optional_titles(self):
        optional = [c["name"] for c in self.catalog if c["name"].endswith(" (optional)")]
        self.assertEqual([n.split(",")[0] for n in optional], ["A4", "A6", "A7"])

    def test_owasp_id_leads_each_description(self):
        for c in self.catalog:
            key = c["name"].split(",")[0]
            first = c["description"].split("\n\n")[0]
            self.assertTrue(first.startswith(f"<mark>**OWASP {self.OWASP[key]}, "), (key, first))

    def test_attack_descriptions_open_with_the_backstory(self):
        for c in self.catalog:
            if c["category"] != "Attack":
                continue
            story = c["description"].split("\n\n")[1]
            self.assertTrue(story.startswith(("You're a bad actor", "You're a contractor", "Same attacker")), story)
            for mark in (";", "\u2014", "\u2013"):
                self.assertNotIn(mark, story)


if __name__ == "__main__":
    unittest.main()
