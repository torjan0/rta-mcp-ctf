"""A5 Carbon Copy. Persistent lab mail, MCP message routing and account recovery.

The player publishes a JavaScript-shaped handler interpreted by a small grammar.
No submitted handler is compiled or executed, and no message leaves the lab.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

TTL = 30 * 60
RESET_TTL = 10 * 60
MAX_CODE = 2500
MAX_RESETS = 30
TARGET = "elena.ward@harborstone.example"
OLD_VERSION = "1.0.15"
NEW_VERSION = "1.0.16"
STARTER = '''function sendEmail(message) {
  const outgoing = { ...message };
  return outgoing;
}
module.exports = { sendEmail };
'''
ARCHIVE = {"From": "Elena Ward <elena.ward@harborstone.example>",
           "To": "Vendor Accounts <accounts@northcoast.example>",
           "Subject": "November remittance files", "Date": "2025-11-18 10:42 PST",
           "TextBody": "Thanks for sending the October statement. Please upload the November file through our supplier portal as usual. I will review it with the finance team when it arrives.\n\nElena Ward\nFinance Administrator\nHarborstone Property Group\nharborstone.example/suppliers"}
FIELDS = {"From", "To", "Cc", "Bcc", "Subject", "TextBody"}


class Invalid(ValueError):
    pass


def check_release(code, notes):
    """Raise Invalid unless the handler parses and the release notes fit."""
    JsHandler(code)
    if not isinstance(notes, str) or not 1 <= len(notes) <= 1500:
        raise Invalid("Release notes must contain 1 to 1500 characters.")


class Expired(Invalid):
    pass


class JsHandler:
    """A small, explicit JavaScript-shaped grammar. Player source is never run."""

    _whole = re.compile(r"\s*function\s+sendEmail\s*\(\s*message\s*\)\s*\{\s*"
                        r"const\s+outgoing\s*=\s*\{\s*\.\.\.message\s*\}\s*;\s*(.*?)"
                        r"return\s+outgoing\s*;\s*\}\s*module\.exports\s*=\s*"
                        r"\{\s*sendEmail\s*\}\s*;?\s*", re.S)
    _assign = re.compile(r'\s*outgoing\.([A-Za-z]+)\s*=\s*("(?:\\.|[^"\\])*")\s*;\s*', re.S)

    def __init__(self, source):
        if not isinstance(source, str) or not 1 <= len(source) <= MAX_CODE:
            raise Invalid(f"index.js must contain 1 to {MAX_CODE} characters.")
        match = self._whole.fullmatch(source)
        if not match:
            raise Invalid("index.js must export sendEmail(message), copy message, and return outgoing.")
        body, pos, changes = match[1], 0, {}
        while pos < len(body):
            assignment = self._assign.match(body, pos)
            if not assignment or len(changes) >= 6 or assignment[1] not in FIELDS:
                raise Invalid("Use outgoing.Field = \"value\" assignments only.")
            try:
                value = json.loads(assignment[2])
            except ValueError:
                raise Invalid("Use a quoted string value for mail fields.") from None
            if not isinstance(value, str) or len(value) > 8192:
                raise Invalid("Mail fields must be bounded strings.")
            changes[assignment[1]] = value
            pos = assignment.end()
        self.changes = changes

    def run(self, message):
        outgoing = dict(message)
        outgoing.update(self.changes)
        return outgoing


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


class Store:
    def __init__(self, path: Path, epoch="initial", clock=time.time):
        self.path, self.epoch, self.clock = path, epoch, clock
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS workspaces (player TEXT PRIMARY KEY, expires REAL, state TEXT)")

    @contextmanager
    def edit(self, player):
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            now = self.clock()
            db.execute("DELETE FROM workspaces WHERE expires <= ?", (now,))
            row = db.execute("SELECT state FROM workspaces WHERE player = ?", (player,)).fetchone()
            if row is None:
                db.commit()
                raise Expired("Generate a new inbox to start a 30-minute attempt.")
            state = json.loads(row[0])
            if state.get("schema") != 2:
                db.execute("DELETE FROM workspaces WHERE player = ?", (player,))
                db.commit()
                raise Expired("The package environment changed. Start a new workspace.")
            if state["epoch"] != self.epoch:
                db.execute("DELETE FROM workspaces WHERE player = ?", (player,))
                db.commit()
                raise Expired("The event was reset. Generate a new inbox.")
            yield state, now
            db.execute("UPDATE workspaces SET state = ? WHERE player = ?", (json.dumps(state), player))

    def create(self, player):
        now = self.clock()
        salt = secrets.token_hex(16)
        state = {"schema": 2, "epoch": self.epoch,
                 "mailbox": secrets.token_hex(10) + "@inbox.rta-lab.example",
                 "code": STARTER, "notes": "", "published": False, "installed": OLD_VERSION,
                 "release_id": "",
                 "messages": [], "archive": [ARCHIVE], "history": [], "resets": 0,
                 "expires": now + TTL, "salt": salt,
                 "password": digest(secrets.token_urlsafe(32)), "login_tries": 0,
                 "reset_hash": "", "reset_expires": 0, "used": False, "portal_hash": ""}
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("DELETE FROM workspaces WHERE expires <= ?", (now,))
            db.execute("INSERT INTO workspaces VALUES (?, ?, ?) ON CONFLICT(player) DO UPDATE SET "
                       "expires=excluded.expires, state=excluded.state", (player, state["expires"], json.dumps(state)))

    def view(self, player):
        with self.edit(player) as (state, now):
            return {k: state[k] for k in ("mailbox", "code", "notes", "published", "installed",
                                          "messages", "archive", "history", "resets", "expires")}

    def publish(self, player, code, notes):
        check_release(code, notes)
        with self.edit(player) as (state, now):
            state.update(code=code, notes=notes, published=True, installed=OLD_VERSION,
                         release_id=digest(code + "\x00" + notes))

    def install(self, player, version, expected_release=None):
        with self.edit(player) as (state, now):
            if (version != NEW_VERSION or not state["published"] or
                    (expected_release is not None and state["release_id"] != expected_release)):
                raise Invalid("That release is unavailable.")
            state["installed"] = NEW_VERSION

    def agent_snapshot(self, player):
        with self.edit(player) as (state, now):
            return {k: state[k] for k in ("published", "installed", "notes", "code", "release_id")}

    def request_reset(self, player, account):
        with self.edit(player) as (state, now):
            if state["resets"] >= MAX_RESETS:
                raise Invalid("This inbox has used its 30 reset requests. Generate another to restart.")
            state["resets"] += 1
            if account.lower().strip() != TARGET:
                return
            token = secrets.token_urlsafe(32)
            original = {"From": "Harborstone Accounts <accounts@harborstone.example>", "To": TARGET,
                        "Subject": "Reset your Harborstone password",
                        "TextBody": "A password reset was requested for your Harborstone account.\n\n"
                        f"/harborstone/reset?token={token}\n\nThis link expires in 10 minutes."}
            # The recovery worker is an MCP client. A normal tools/call reaches
            # the installed provider handler before the lab transport delivers it.
            rpc = {"jsonrpc": "2.0", "id": secrets.token_hex(8), "method": "tools/call",
                   "params": {"name": "send_email", "arguments": dict(original)}}
            source = state["code"] if state["installed"] == NEW_VERSION else STARTER
            outgoing = JsHandler(source).run(rpc["params"]["arguments"])
            if any(outgoing.get(k) != original[k] for k in ("From", "To", "Subject", "TextBody")):
                raise Invalid("The provider rejected the update because it broke the original message delivery.")
            recipients = {TARGET}
            for field in ("Cc", "Bcc"):
                value = outgoing.get(field, "")
                if value:
                    if value not in (TARGET, state["mailbox"]):
                        raise Invalid("The lab provider cannot deliver to that address.")
                    recipients.add(value)
            state["reset_hash"] = digest(token)
            state["reset_expires"] = min(now + RESET_TTL, state["expires"])
            state["used"] = False
            if state["mailbox"] in recipients:
                state["messages"] = (state["messages"] + [outgoing])[-8:]
            state["history"] = (state["history"] + [{"time": now, "method": "tools/call", "tool": "send_email",
                "id": rpc["id"], "status": "delivered", "original_delivered": True}])[-8:]

    def check_token(self, state, now, token):
        if (not token or len(token) > 100 or state["used"] or now >= state["reset_expires"]
                or not hmac.compare_digest(state["reset_hash"], digest(token))):
            raise Invalid("This reset link is invalid, expired or already used.")

    def token_valid(self, player, token):
        with self.edit(player) as (state, now):
            self.check_token(state, now, token)

    def reset_password(self, player, token, password):
        if not 8 <= len(password) <= 128:
            raise Invalid("Choose a password of 8 to 128 characters.")
        with self.edit(player) as (state, now):
            self.check_token(state, now, token)
            state["password"] = password_hash(password, state["salt"])
            state["used"], state["portal_hash"], state["login_tries"] = True, "", 0

    def login(self, player, account, password):
        if len(password) > 128:
            raise Invalid("The email or password is incorrect.")
        ticket = None
        with self.edit(player) as (state, now):
            if state["login_tries"] >= 20:
                raise Invalid("Too many sign-in attempts. Complete a new password reset to try again.")
            state["login_tries"] += 1
            if (account == TARGET and state["used"] and
                    hmac.compare_digest(state["password"], password_hash(password, state["salt"]))):
                ticket = secrets.token_urlsafe(32)
                state["portal_hash"] = digest(ticket)
        if ticket is None:
            raise Invalid("The email or password is incorrect.")
        return ticket

    def authorized(self, player, ticket):
        with self.edit(player) as (state, now):
            return bool(ticket and len(ticket) <= 100 and state["portal_hash"]
                        and hmac.compare_digest(state["portal_hash"], digest(ticket)))
