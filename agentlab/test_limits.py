"""Checks the challenge site's limits with a fake model, so nothing calls Bedrock.

  .venv/bin/python test_limits.py

Each case starts the app in a fresh temporary state folder with its own limits,
in a child process, because app.py reads its limits when it is imported.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = 8199
FORM = {"server_json": json.dumps({"name": "s", "tools": [{"name": "t", "description": "d", "inputSchema": {"type": "object", "properties": {}}}]})}

CASES = {
    "login": ({"AGENTLAB_LOGIN_FAILS": "3"}, "ok"),
    "attempts": ({"AGENTLAB_COOLDOWN": "0", "AGENTLAB_ATTEMPTS_PER_CHALLENGE": "4"}, "refused"),
    "error_cooldown": ({"AGENTLAB_COOLDOWN": "30"}, "error"),
    "per_minute": ({"AGENTLAB_COOLDOWN": "0", "AGENTLAB_RUNS_PER_MINUTE": "2"}, "ok"),
    "spend": ({"AGENTLAB_COOLDOWN": "0", "AGENTLAB_SPEND_CAP_USD": "0.01"}, "ok"),
    "requests": ({"AGENTLAB_SESSION_RPM": "5", "AGENTLAB_IP_RPM": "8"}, "ok"),
    "pages_fake": ({}, "ok"),
    "pages_github": ({"AGENTLAB_GITHUB_OWNER": "acme-test"}, "ok"),
    "worker_usernames": ({"AGENTLAB_GITHUB_OWNER": "acme-test", "AGENTLAB_COOLDOWN": "0"}, "ok"),
    "f1_page": ({}, "ok"),
    "f2_page": ({}, "ok"),
    "f3_page": ({}, "ok"),
    "a4_page": ({"AGENTLAB_COOLDOWN": "0"}, "ok"),
    "a4_error": ({"AGENTLAB_COOLDOWN": "0"}, "error"),
    "a4_limits": ({"AGENTLAB_COOLDOWN": "0", "AGENTLAB_ATTEMPTS_PER_CHALLENGE": "2"}, "refused"),
}


class Client:
    def __init__(self, ip: str):
        self.ip = ip
        self.jar = http.cookiejar.CookieJar()
        jar = urllib.request.HTTPCookieProcessor(self.jar)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        self.opener = urllib.request.build_opener(jar, NoRedirect)

    def go(self, method: str, path: str, form: dict | None = None) -> tuple[int, str]:
        json_action = method == "POST" and path == "/a1"
        data = (json.dumps(form).encode() if json_action else urllib.parse.urlencode(form).encode()) if form is not None else None
        headers = {"X-Forwarded-For": self.ip}
        if json_action:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method,
                                     headers=headers)
        try:
            with self.opener.open(req, timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location", "") or e.read().decode()

    def login(self, name: str, password: str = "local-fixture") -> int:
        return self.go("POST", "/login", {"name": name, "password": password})[0]

    def run(self) -> str:
        code, where = self.go("POST", "/a1", FORM)
        return urllib.parse.unquote(where.split("err=", 1)[1]) if "err=" in where else str(code)


def child(case: str) -> None:
    mode = CASES[case][1]
    sys.path.insert(0, str(HERE))
    import uvicorn

    import app as m
    from engine import Run, ToolCall
    from functools import partial
    from dev_nospend import create_account, local_auth
    for name in ("one", "two"):
        create_account(m.ROOT, name, f"{name}@example.test", "local-fixture")
    m.authenticate = partial(local_auth, m.ROOT)

    def fake(tool, flag, player=None):
        if mode == "refused":
            return Run(stop="refusal", refused=True, input_tokens=500)
        if mode == "error":
            return Run(stop="error", error="ThrottlingException: fake", input_tokens=500)
        return Run(calls=[ToolCall(tool["tools"][0]["name"], {"x": "1"}, "Done.")], stop="end_turn", input_tokens=20000, output_tokens=100)

    m.play_a1 = fake

    def fake_a4(attempt, flag, player):
        return Run(view={"replies": ["<i>reply</i>"], "closed": True, "installs": 1,
                         "requests": [{"time": "12:00:00", "method": "POST", "path": "/h/x",
                                       "query": "a=<tag>", "body": "<b>key</b>"}]},
                   refused=mode == "refused", error="ThrottlingException: fake" if mode == "error" else "",
                   input_tokens=500)

    m.play_a4 = fake_a4
    server = uvicorn.Server(uvicorn.Config(m.app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    ok = True

    def check(label, got, want):
        nonlocal ok
        good = want(got) if callable(want) else got == want
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'} {label}")

    a, b = Client("10.0.0.1"), Client("10.0.0.2")
    if case == "login":
        for i in range(3):
            check(f"bad password {i + 1} is refused normally", a.login("one", "incorrect"), 401)
        check("a 4th wrong password from that address is slowed down", a.login("one", "incorrect"), 429)
        check("a correct password from the same address still logs in", Client("10.0.0.1").login("one"), 303)
    elif case == "attempts":
        b.login("one")
        for i in range(4):
            check(f"refused attempt {i + 1} goes through", b.run(), "303")
        check("5th attempt is stopped", b.run(), lambda s: "no attempts left" in s)
        check("refusals did not use counted runs", json.loads(b.go("GET", "/a1/state")[1])["runs_left"] == 20, True)
    elif case == "error_cooldown":
        b.login("one")
        check("an errored run reports the error", b.run(), lambda s: "hit an error" in s)
        check("the cooldown still applies after an error", b.run(), lambda s: s.startswith("Wait"))
        check("the error did not use a counted run", json.loads(b.go("GET", "/a1/state")[1])["runs_left"] == 20, True)
    elif case == "per_minute":
        a.login("one"); b.login("two")
        check("run 1 this minute", a.run(), "303")
        check("run 2 this minute", a.run(), "303")
        check("run 3 by another player is held back", b.run(), lambda s: "busy" in s)
    elif case == "spend":
        b.login("one")
        check("first run fits the budget", b.run(), "303")
        check("spend cap reached stops the next run", b.run(), lambda s: "budget is used up" in s)
    elif case == "requests":
        b.login("one")  # one request on this session's address
        codes = [b.go("GET", "/")[0] for _ in range(6)]
        check("a player's 6th page load in a minute is 429", codes, lambda c: c[:5] == [200] * 5 and c[5] == 429)
        c = Client("10.0.0.3")
        codes = [c.go("GET", "/")[0] for _ in range(9)]
        check("a logged-out address's 9th request in a minute is 429", codes, lambda c: c[:8] == [200] * 8 and c[8] == 429)
        d = Client("10.0.0.3")
        d.login("two")
        check("a player on that flooded address still gets in", d.go("GET", "/a1")[0], 200)
        f = Client("10.0.0.4")
        check("another device can log in", f.login("one"), 303)
        check("logging in again does not reset that player's limit", f.go("GET", "/")[0], 429)
    elif case == "pages_fake":
        b.login("one")
        for path in ("/", "/a1", "/a2", "/a3"):
            check(f"{path} loads in fake mode", b.go("GET", path)[0], 200)
        code, where = b.go("POST", "/a3", {"title": "x" * 101})
        check("a 101-character A3 title is refused", urllib.parse.unquote(where), lambda w: "limited to 100" in w)
    elif case == "pages_github":
        import re
        b.login("one"); a.login("two")
        check("A2 asks for a saved GitHub username first", "GitHub account" in b.go("GET", "/a2")[1], True)
        page_html = b.go("GET", "/github")[1]
        csrf = re.search(r"name=csrf value='([a-f0-9]+)'", page_html).group(1)
        check("GitHub form has a username field and save button", "name=login" in page_html and "Save username" in page_html, True)
        check("GitHub form rejects missing CSRF", b.go("POST", "/github", {"login": "Octo-Cat"})[0], 403)
        check("GitHub form saves username", b.go("POST", "/github", {"login": "Octo-Cat", "csrf": csrf})[0], 303)
        check("A2 names the saved login and links the repo", b.go("GET", "/a2")[1],
              lambda h: "octo-cat" in h and "github.com/acme-test/website/issues" in h)
        check("A3 links the docs repo", "github.com/acme-test/docs/issues" in b.go("GET", "/a3")[1], True)
        check("posting the old A2 form does not run anything", b.go("POST", "/a2", FORM)[1], lambda w: w.endswith("/a2"))
    elif case == "worker_usernames":
        import datetime as dt
        tok = Path(os.environ["AGENTLAB_STATE"], "tok"); tok.write_text("x")
        os.environ["AGENTLAB_GITHUB_TOKEN_FILE"] = str(tok)
        import gh_worker as w
        repos = {"website": [], "docs": []}
        w.newest_issues = lambda repo, after=0: ([i for i in repos[repo] if i["number"] > after], False)
        w.POOL.submit = lambda *a, **k: None
        stamp = lambda s: dt.datetime.fromtimestamp(time.time() + s, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        def issue(repo, n, login, title, at):
            repos[repo].append({"number": n, "user": {"login": login}, "title": title, "created_at": stamp(at),
                                "updated_at": stamp(at), "html_url": f"https://github.com/acme-test/{repo}/issues/{n}"})
        b.login("one")
        m.save_github_login("u1", "victim")
        issue("website", 9, "stranger", "link ABCD-EFGH", 1)
        issue("website", 10, "victim", "link ABCD-EFGH", 2)
        issue("docs", 5, "victim", "A3 title", 3)
        w.tick(1)
        with m.db() as con:
            rows = con.execute("SELECT challenge, number, player, status FROM gh_seen ORDER BY challenge, number").fetchall()
            runs = con.execute("SELECT challenge, player FROM runs ORDER BY challenge").fetchall()
        check("worker ignores unlinked and handles linked issues including link title", rows,
              [("a2", 9, None, "not a linked player"), ("a2", 10, "u1", "running"), ("a3", 5, "u1", "running")])
        check("worker attributes only linked issues to player", runs, [("a2", "u1"), ("a3", "u1")])
        check("A2 shows only the player's issue", b.go("GET", "/a2")[1],
              lambda h: "Issue #10" in h and "Issue #9" not in h)
    elif case.startswith("a4_"):
        import re
        b.login("one")
        classic = "/a4/classic"
        form = {"package": "@quick/helper-mcp", "script": "echo hi", "name": "<u>Priya</u>",
                "email": "p@vendor.example", "subject": "<em>Hello</em>", "message": "Hi"}
        check("A4 requires login", Client("10.0.0.8").go("GET", "/a4")[0], 303)
        code, body = b.go("GET", classic)
        check("A4 loads", code, 200)
        hooks = re.findall(r"https://hooks\.rta-lab\.example/h/[a-f0-9]{16}", body)
        check("A4 shows a hook", bool(hooks), True)
        if hooks:
            check("A4 keeps the same hook", hooks[0] in b.go("GET", classic)[1], True)
            a.login("two")
            check("another player gets a different hook", hooks[0] not in a.go("GET", classic)[1], True)
        code, where = b.go("POST", "/a4", form)
        check("A4 submit redirects", code, 303)
        if case == "a4_error":
            check("A4 reports an engine error", "hit an error" in urllib.parse.unquote(where), True)
        body = b.go("GET", classic)[1]
        for text in ("installed 1 time", "&lt;b&gt;key&lt;/b&gt;", "&lt;i&gt;reply&lt;/i&gt;", "was closed",
                     "&lt;u&gt;Priya&lt;/u&gt;", "&lt;em&gt;Hello&lt;/em&gt;", "a=&lt;tag&gt;"):
            check(f"A4 shows {text}", text in body, True)
        check("A4 escapes markup", "<b>key</b>" not in body and "<u>Priya</u>" not in body, True)
        check("another player sees no history", "installed 1 time" not in a.go("GET", classic)[1], True)
        if mode != "ok":
            check("failed or refused A4 does not count", "did not count" in body and "20 runs left" in body, True)
        if case == "a4_limits":
            b.go("POST", "/a4", form)
            where = b.go("POST", "/a4", form)[1]
            check("A4 obeys attempt cap after refusals", "no attempts left" in urllib.parse.unquote(where), True)
        if case == "a4_page":
            where = b.go("POST", "/a4", {**form, "package": "Bad"})[1]
            check("A4 validates packages", "package" in urllib.parse.unquote(where) and "err=" in where, True)
            check("home lists A4", "A4, ticket to shell" in b.go("GET", "/")[1], True)
            for _ in range(5):
                b.go("POST", "/a4", form)
            check("A4 shows only the last five runs", b.go("GET", classic)[1].count("Run at "), 5)
    elif case == "f2_page":
        import base64
        import re
        c = Client("10.0.0.9")
        code, body = c.go("GET", "/f2")
        check("F2 loads without logging in", code, 200)
        check("and links the download", "/f2/session.jsonl" in body, True)
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/f2/session.jsonl")
        with urllib.request.urlopen(req, timeout=10) as r:
            disposition, text = r.headers.get("Content-Disposition", ""), r.read().decode()
        check("the download is an attachment named session.jsonl", disposition, lambda d: "attachment" in d and "session.jsonl" in d)
        blob = re.search(r"v2/ping\?d=([A-Za-z0-9+/=]+)", text).group(1)
        check("and it hides this server's F2 flag", "RTA\x7bf2-test}" in base64.b64decode(blob).decode(), True)
    elif case == "f3_page":
        import io
        import zipfile
        c = Client("10.0.0.9")
        code, body = c.go("GET", "/f3")
        check("F3 loads without logging in", code, 200)
        check("and links the offline download", "/f3/siem-export.zip" in body, True)
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/f3/siem-export.zip")
        with urllib.request.urlopen(req, timeout=60) as r:
            disposition, archive = r.headers.get("Content-Disposition", ""), r.read()
            check("the ZIP is not stored by intermediate caches", r.headers.get("Cache-Control"), "no-store")
        check("the download is a ZIP attachment", disposition, lambda d: "attachment" in d and "siem-export.zip" in d)
        z = zipfile.ZipFile(io.BytesIO(archive))
        check("the export contains exactly the brief and five log sources", len(z.namelist()), 6)
        sys.path.insert(0, str(HERE.parent / "solutions"))
        from solve_f3 import investigate
        recovered = investigate(io.BytesIO(archive))
        check("the downloaded evidence recovers this server's F3 flag", recovered["flag"], "RTA\x7bf3-test}")
        cached = list(Path(os.environ["AGENTLAB_STATE"], "f3-cache").glob("*.zip"))
        check("one completed export is cached", len(cached), 1)
        before = cached[0].stat().st_mtime_ns
        with urllib.request.urlopen(req, timeout=10) as r:
            check("repeat downloads are byte identical", r.read() == archive, True)
        check("repeat downloads do not rebuild the export", cached[0].stat().st_mtime_ns, before)
        check("no model runs were consumed", m.db().execute("SELECT count(*) FROM runs").fetchone()[0], 0)
    elif case == "f1_page":
        code, body = Client("10.0.0.9").go("GET", "/f1")
        check("F1 loads without logging in", code, 200)
        check("and it is the terminal page", "<title>Claude Code</title>" in body, True)
        alias_code, alias_body = Client("10.0.0.9").go("GET", "/w1")
        check("the old /w1 link still works", (alias_code, alias_body == body), (200, True))
    server.should_exit = True
    sys.exit(0 if ok else 1)


def main() -> None:
    failed = []
    for case, (env, _) in CASES.items():
        with tempfile.TemporaryDirectory() as state:
            Path(state, "flags.json").write_text(json.dumps({"a1": "RTA\x7bt}", "a2": "RTA\x7bt}", "a3": "RTA\x7bt}", "a4": "RTA\x7bt}", "f2": "RTA\x7bf2-test}", "f3": "RTA\x7bf3-test}"}))
            print(case, flush=True)
            r = subprocess.run([sys.executable, __file__, case], env={**os.environ, **env, "AGENTLAB_STATE": state})
            if r.returncode:
                failed.append(case)
    print("ALL PASS" if not failed else f"FAILED: {', '.join(failed)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    child(sys.argv[1]) if len(sys.argv) > 1 else main()
