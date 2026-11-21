"""Verify one lab login using a private, short-lived CTFd form session."""

import http.client
import http.cookiejar
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import Enum

DEFAULT_URL = "http://127.0.0.1:8000"


@dataclass(frozen=True)
class Account:
    id: int
    name: str


class Refusal(Enum):
    INVALID = "Your scoreboard name or password is incorrect."
    LIMITED = "Too many login attempts from this network. Wait a few seconds."
    BANNED = "Your scoreboard account is banned. Ask an organizer."
    UNAVAILABLE = "The scoreboard is not answering. Ask an organizer."


class _ReturnResponse(urllib.request.HTTPErrorProcessor):
    """Read status codes directly, including redirects. Never replay a form."""

    def http_response(self, request, response):
        return response

    https_response = http_response


def authenticate(base: str, name: str, password: str, ip: str) -> Account | Refusal:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                         urllib.request.HTTPCookieProcessor(jar), _ReturnResponse())

    def call(path, data=None):
        req = urllib.request.Request(base.rstrip("/") + path, data=data, headers={"X-Forwarded-For": ip})
        return opener.open(req, timeout=15)

    try:
        with call("/login") as response:
            if response.status == 429:
                return Refusal.LIMITED
            if response.status != 200:
                return Refusal.UNAVAILABLE
            nonce = re.search(rb"""["']?csrfNonce["']?\s*:\s*["']([^"']+)["']""", response.read())
            if not nonce:
                return Refusal.UNAVAILABLE

        form = urllib.parse.urlencode({"name": name, "password": password, "nonce": nonce[1]}).encode()
        with call("/login", form) as response:
            if response.status == 429:
                return Refusal.LIMITED
            if response.status == 200 and b"Your username or password is incorrect" in response.read():
                return Refusal.INVALID
            location = response.headers.get("Location")
            if response.status not in (301, 302, 303, 307, 308) or not location:
                return Refusal.UNAVAILABLE
            target = urllib.parse.urljoin(base.rstrip("/") + "/login", location)
            if urllib.parse.urlsplit(target).path.rstrip("/") == "/login":
                return Refusal.UNAVAILABLE

        try:
            with call("/api/v1/users/me") as response:
                if response.status == 403:
                    return Refusal.BANNED
                if response.status == 429:
                    return Refusal.LIMITED
                if response.status != 200:
                    return Refusal.UNAVAILABLE
                result = json.load(response)
                data = result["data"]
                if (result["success"] is not True or type(data["id"]) is not int
                        or not 0 < data["id"] <= 2**63 - 1
                        or not isinstance(data["name"], str) or not data["name"].strip()):
                    return Refusal.UNAVAILABLE
                # SQLite text must be valid UTF-8, including after JSON escape decoding.
                data["name"].encode("utf-8")
                return Account(data["id"], data["name"])
        finally:
            try:
                with call("/logout"):
                    pass
            except (OSError, ValueError, http.client.HTTPException):
                pass
    except (OSError, ValueError, KeyError, TypeError, RecursionError, http.client.HTTPException):
        return Refusal.UNAVAILABLE
    finally:
        jar.clear()
