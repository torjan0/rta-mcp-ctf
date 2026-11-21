"""Account admission over a real loopback HTTP connection to a scripted CTFd."""

import contextlib
import html
import importlib.util
import io
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

from starlette.testclient import TestClient

import ctfd_auth
from end_sessions import end_sessions
from unlink_github import unlink_github

HERE = Path(__file__).resolve().parent


class Scoreboard(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body=b"", **headers):
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def record(self):
        ip = self.headers.get("X-Forwarded-For")
        self.server.requests.append((self.command, self.path, ip))
        return ip

    def do_GET(self):
        self.record()
        server = self.server
        if self.path == "/login":
            ticket, nonce = secrets.token_hex(12), secrets.token_hex(12)
            server.nonces["session=" + ticket] = nonce
            body = ('<script>window.init = {"csrfNonce": "%s"};</script>' % nonce).encode()
            self.reply(200, body if server.send_nonce else b"maintenance", **{"Set-Cookie": f"session={ticket}; Path=/"})
            return
        cookie = self.headers.get("Cookie")
        user_id = server.sessions.get(cookie)
        if not user_id:
            self.reply(403)
        elif self.path == "/logout":
            del server.sessions[cookie]
            self.reply(server.logout_status, Location="/")
        elif self.path == "/api/v1/users/me":
            if server.barrier:
                server.barrier.wait(timeout=3)
            if user_id in server.banned:
                self.reply(403)
            else:
                name = server.users[user_id]
                profile = {"success": True, "data": {"id": user_id, "name": name}}
                self.reply(200, server.profile_body if server.profile_body is not None else json.dumps(profile).encode())
        else:
            self.reply(404)

    def do_POST(self):
        ip = self.record()
        server = self.server
        fields = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        if self.path != "/login":
            self.reply(404)
            return
        nonce = server.nonces.pop(self.headers.get("Cookie"), None)
        if nonce is None or fields.get("nonce") != [nonce]:
            self.reply(403)
            return
        now = server.clock()
        with server.lock:
            attempts = server.attempts[ip]
            while attempts and now - attempts[0] >= 5:
                attempts.popleft()
            if len(attempts) >= 10:
                self.reply(429)
                return
            attempts.append(now)
        if server.login_reply:
            self.reply(*server.login_reply[:2], **server.login_reply[2])
            return
        for user_id, name in server.users.items():
            if fields.get("name") in ([name], [f"player{user_id}@example.test"]) and fields.get("password") == [server.password]:
                cookie = "session=" + secrets.token_hex(12)
                server.sessions[cookie] = user_id
                self.reply(302, **{"Location": "/challenges", "Set-Cookie": cookie + "; Path=/"})
                return
        self.reply(200, b"<p>Your username or password is incorrect</p>")


class Accounts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "flags.json").write_text("{}")
        self.server = s = ThreadingHTTPServer(("127.0.0.1", 0), Scoreboard)
        self.addCleanup(s.server_close)
        s.clock = time.monotonic
        s.password = secrets.token_urlsafe(22)
        s.users = {17: "Player <17>", 18: "Next player", 19: "Third player"}
        s.nonces, s.sessions, s.banned = {}, {}, set()
        s.attempts, s.requests = defaultdict(deque), []
        s.lock, s.barrier = threading.Lock(), None
        s.login_reply, s.profile_body, s.send_nonce, s.logout_status = None, None, True, 302
        thread = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.02})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(s.shutdown)
        self.url = f"http://127.0.0.1:{s.server_port}"
        with patch.dict(os.environ, {"AGENTLAB_STATE": tmp.name, "AGENTLAB_CTFD_URL": self.url,
                                     "AGENTLAB_CTFD_PUBLIC_URL": "https://scores.example/",
                                     "AGENTLAB_MAX_ACCOUNTS": "70"}):
            spec = importlib.util.spec_from_file_location("accounts_test_app", HERE / "app.py")
            self.lab = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.lab)
        self.browser = self.device()

    def device(self, peer="127.0.0.1"):
        client = TestClient(self.lab.app, client=(peer, 4000), follow_redirects=False)
        self.addCleanup(client.close)
        return client

    def login(self, name=None, *, browser=None, password=None, ip="192.0.2.1"):
        return (browser or self.browser).post("/login", headers={"X-Forwarded-For": "198.51.100.1, " + ip},
            data={"name": name if name is not None else self.server.users[17],
                  "password": password if password is not None else self.server.password})

    def test_login_by_name_and_email_rename_and_forwarding(self):
        form = self.browser.get("/").text
        for text in ("Scoreboard name or email", "type=password", "There are no password resets",
                     "https://scores.example/register"):
            self.assertIn(text, form)
        self.assertEqual(self.login().headers["location"], "/")
        self.assertEqual(self.server.requests, [
            (method, path, "192.0.2.1") for method, path in
            (("GET", "/login"), ("POST", "/login"), ("GET", "/api/v1/users/me"), ("GET", "/logout"))])
        self.assertFalse(self.server.sessions)
        self.assertEqual(set(self.browser.cookies), {"agentlab"})
        page = self.browser.get("/").text
        self.assertIn(html.escape(self.server.users[17]), page)
        self.assertNotIn(self.server.users[17], page)
        self.server.users[17] = "Renamed"
        other = self.device("203.0.113.17")
        self.assertEqual(self.login("player17@example.test", browser=other).status_code, 303)
        self.assertEqual(self.server.requests[-1][-1], "203.0.113.17")
        self.assertIn("Logged in as Renamed", self.browser.get("/").text)
        with self.lab.db() as con:
            self.assertEqual(con.execute("SELECT player, ctfd_id, name, first_login < last_login FROM accounts").fetchall(),
                             [("u17", 17, "Renamed", 1)])

    def test_invalid_credentials_local_and_scoreboard_limits(self):
        self.server.clock = lambda: 100
        invalid = self.login(password="incorrect")
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(invalid.content, self.login("missing").content)
        self.lab.LOGIN_FAILS.limit = 2
        self.assertEqual(self.login("missing").status_code, 429)
        self.assertEqual(self.login().status_code, 303)
        for _ in range(6):
            self.login("missing")
        throttled = self.login()
        self.assertEqual(throttled.status_code, 429)
        self.assertIn("Too many login attempts from this network. Wait a few seconds.", throttled.text)
        self.server.clock = lambda: 106
        self.assertEqual(self.login().status_code, 303)
        self.lab.SESSION_LIMIT.limit = 1
        self.assertEqual(self.browser.get("/").status_code, 200)
        self.assertEqual(self.browser.get("/").status_code, 429)
        self.assertEqual(self.login(ip="192.0.2.2").status_code, 303)

    def test_banned_and_failed_logout(self):
        self.server.banned.add(17)
        result = self.login()
        self.assertEqual(result.status_code, 403)
        self.assertIn("banned", result.text)
        self.assertFalse(self.server.sessions)
        self.assertFalse(self.browser.cookies)
        self.server.banned.clear()
        self.server.logout_status = 500
        self.assertEqual(self.login().status_code, 303)

    def test_down_and_unexpected_responses_fail_closed(self):
        def unavailable(result):
            self.assertEqual(result.status_code, 503)
            self.assertIn("The scoreboard is not answering. Ask an organizer.", result.text)
            self.assertFalse(self.browser.cookies)

        # Bind without listening, so no other server can claim the port.
        with socket.socket() as down:
            down.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{down.getsockname()[1]}"
            with patch.object(self.lab, "authenticate", partial(ctfd_auth.authenticate, url)):
                unavailable(self.login())
        self.server.send_nonce = False
        unavailable(self.login())
        self.server.send_nonce = True
        for reply in [(403, b"", {}), (200, b"<p>Something else</p>", {}),
                      (302, b"", {}), (303, b"", {"Location": "/login?next=%2F"})]:
            self.server.login_reply = reply
            unavailable(self.login())
        self.server.login_reply = None
        for profile in [b"{}", b"bad json", b'{"success":false,"data":{"id":17,"name":"test"}}',
                        br'{"success":true,"data":{"id":17,"name":"\ud800"}}',
                        b'{"success":true,"data":{"id":"invalid","name":"test"}}']:
            self.server.profile_body = profile
            unavailable(self.login())
            self.assertFalse(self.server.sessions)

    def test_fake_requires_the_session_that_issued_the_nonce(self):
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        from urllib.parse import urlencode
        with urlopen(self.url + "/login") as first, urlopen(self.url + "/login") as second:
            first_cookie = first.headers["Set-Cookie"].split(";")[0]
            second_cookie = second.headers["Set-Cookie"].split(";")[0]
            nonce = self.server.nonces[first_cookie]
        request = Request(self.url + "/login", data=urlencode({"nonce": nonce}).encode(),
                          headers={"Cookie": second_cookie})
        with self.assertRaises(HTTPError) as failure:
            urlopen(request)
        self.assertEqual(failure.exception.code, 403)
        failure.exception.close()

    def test_password_stays_out_of_responses_logs_and_database(self):
        stream = io.StringIO()
        with self.assertLogs(level=logging.DEBUG) as logs, contextlib.redirect_stderr(stream), contextlib.redirect_stdout(stream):
            responses = [self.login(), self.browser.get("/"), self.login("unknown"), self.browser.post("/logout")]
        secret = self.server.password
        self.assertFalse(secret in "\n".join(logs.output) or secret in stream.getvalue())
        for response in responses:
            self.assertFalse(secret.encode() in response.content or secret in str(response.headers))
        with self.lab.db() as con:
            self.assertFalse(any(secret in line for line in con.iterdump()))
        self.assertFalse(secret.encode() in (self.root / "state.db").read_bytes())

    def test_last_place_is_atomic_and_returning_accounts_can_always_login(self):
        with self.lab.db() as con:
            con.executemany("INSERT INTO accounts VALUES (?, ?, ?, 0, 0)",
                            [(f"u{i}", i, f"player{i}") for i in range(100, 169)])
        self.server.barrier = threading.Barrier(2)
        devices = [self.device(), self.device()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.login, self.server.users[i], browser=device)
                       for i, device in zip((17, 18), devices)]
            results = [job.result() for job in futures]
        self.server.barrier = None
        self.assertCountEqual([r.status_code for r in results], [303, 403])
        self.assertTrue(any("The lab is full" in r.text for r in results))
        with self.lab.db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 70)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)
        winner = [r.status_code for r in results].index(303)
        returning_id = (17, 18)[winner]
        self.assertEqual(self.login(self.server.users[returning_id]).status_code, 303)
        self.assertEqual(self.login(self.server.users[19]).status_code, 403)

    def test_session_limit_logout_and_expiration(self):
        devices = [self.device() for _ in range(6)]
        for device in devices:
            response = self.login(browser=device)
            self.assertIn("Max-Age=43200", response.headers["set-cookie"])
        self.assertNotIn("Log out", devices[0].get("/").text)
        self.assertTrue(all("Log out" in d.get("/").text for d in devices[1:]))
        calls = len(self.server.requests)
        devices[-1].post("/logout")
        self.assertNotIn("Log out", devices[-1].get("/").text)
        self.assertIn("Log out", devices[-2].get("/").text)
        self.assertEqual(len(self.server.requests), calls)
        with self.lab.db() as con:
            con.execute("UPDATE sessions SET created = ?", (time.time() - 43201,))
        self.assertNotIn("Log out", devices[-2].get("/").text)

    def test_end_sessions_command_by_name_or_key(self):
        for name in [self.server.users[17], self.server.users[17], self.server.users[18]]:
            self.login(name)
        name = self.server.users[17]
        def command(*args):
            return subprocess.run([sys.executable, str(HERE / "end_sessions.py"), *args],
                                  capture_output=True, env={**os.environ, "AGENTLAB_STATE": str(self.root)})
        closed = command(name)
        self.assertEqual(closed.returncode, 0)
        self.assertIn(b"Ended 2 lab session(s) for u17", closed.stdout)
        self.assertNotEqual(command("missing").returncode, 0)
        self.assertIn("Log out", self.browser.get("/").text)
        self.assertEqual(command("u18").returncode, 0)
        self.assertNotIn("Log out", self.browser.get("/").text)
        self.login()
        self.server.users[18] = "u17"
        self.login("player18@example.test")
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            end_sessions(self.root, "u17")
        self.assertEqual(command("--key", "u17").returncode, 0)
        self.assertIn("Log out", self.browser.get("/").text)

    def test_legacy_sessions_and_links_expire_on_upgrade(self):
        with self.lab.db() as con:
            con.execute("INSERT INTO sessions VALUES ('old-cookie', 'player01', ?)", (time.time(),))
            con.execute("INSERT INTO github_users VALUES ('player01', 'old-link')")
        self.lab._migrate()
        self.lab._migrate()
        self.browser.cookies.set("agentlab", "old-cookie")
        self.assertNotIn("Log out", self.browser.get("/").text)
        with self.lab.db() as con:
            for table in ("sessions", "github_users"):
                self.assertEqual(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_github_username_save_validation_change_and_conflict(self):
        self.lab.GH_OWNER = "fixture"
        self.login()
        second = self.device()
        self.login(self.server.users[18], browser=second)
        page = self.browser.get("/github").text
        self.assertIn("name=login", page)
        import re
        csrf = re.search(r"name=csrf value='([a-f0-9]+)'", page).group(1)
        self.assertEqual(self.browser.post("/github", data={"login": "Octo-Cat"}).status_code, 403)
        self.assertIsNone(self.lab.github_login("u17"))
        for bad in ("", "-leading", "bad_name", "a" * 40, "<script>"):
            response = self.browser.post("/github", data={"login": bad, "csrf": csrf})
            self.assertIn("valid GitHub username", self.browser.get(response.headers["location"]).text)
        self.assertEqual(self.browser.post("/github", data={"login": "Octo-Cat", "csrf": csrf}).status_code, 303)
        self.assertEqual(self.lab.github_login("u17"), "octo-cat")
        self.assertIn("octo-cat", self.browser.get("/github").text)
        other_csrf = re.search(r"name=csrf value='([a-f0-9]+)'", second.get("/github").text).group(1)
        conflict = second.post("/github", data={"login": "OCTO-CAT", "csrf": other_csrf})
        self.assertEqual(conflict.status_code, 303)
        self.assertIn("Ask an organizer", second.get(conflict.headers["location"]).text)
        self.assertIsNone(self.lab.github_login("u18"))
        self.browser.post("/github", data={"login": "New-Name", "csrf": csrf})
        self.assertEqual(self.lab.github_login("u17"), "new-name")
        self.assertEqual(second.post("/github", data={"login": "octo-cat", "csrf": other_csrf}).status_code, 303)
        self.assertEqual(self.lab.github_login("u18"), "octo-cat")
        with self.lab.db() as con:
            con.execute("UPDATE github_users SET login = ? WHERE player = ?", ("<unsafe>", "u17"))
        escaped = self.browser.get("/github").text
        self.assertIn("&lt;unsafe&gt;", escaped)
        self.assertNotIn("<unsafe>", escaped)

    def test_organizer_unlink_github_by_name_or_key(self):
        self.login()
        second = self.device()
        self.login(self.server.users[18], browser=second)
        self.lab.save_github_login("u17", "squatted")
        player, count = unlink_github(self.root, self.server.users[17])
        self.assertEqual((player, count), ("u17", 1))
        self.assertIsNone(self.lab.github_login("u17"))
        self.lab.save_github_login("u18", "squatted")
        self.server.users[18] = "u17"
        self.login("player18@example.test", browser=second)
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            unlink_github(self.root, "u17")
        command = subprocess.run([sys.executable, str(HERE / "unlink_github.py"), "--key"],
                                 input=b"u18", capture_output=True,
                                 env={**os.environ, "AGENTLAB_STATE": str(self.root)})
        self.assertEqual(command.returncode, 0)
        self.assertIn(b"Cleared 1 GitHub username(s) for u18", command.stdout)
        self.assertIsNone(self.lab.github_login("u18"))

    def test_public_forensics_and_every_attack_route(self):
        for path in ("/f1", "/f2", "/f3"):
            self.assertEqual(self.browser.get(path).status_code, 200)
        for route in self.lab.app.routes:
            if not route.path.startswith(("/a", "/github", "/harborstone")):
                continue
            for method in route.methods - {"HEAD"}:
                with self.subTest(path=route.path, method=method):
                    response = self.browser.request(method, route.path)
                    if response.status_code == 303:
                        self.assertEqual(response.headers["location"], "/a1" if route.path == "/a1/classic" else "/")
                    else:
                        self.assertEqual(response.status_code, 401)
        self.lab.GH_OWNER = "fixture"
        for path in ("/a2", "/a3"):
            self.assertEqual(self.browser.post(path).headers["location"], "/")
