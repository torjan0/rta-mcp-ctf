"""A small MCP client over stdio, enough to drive the official GitHub MCP server.

MCP over stdio is newline-delimited JSON-RPC: initialize, then tools/list and
tools/call. This client is synchronous, so the agent loop's tool handlers can
call it directly, and it has no dependencies.
"""

from __future__ import annotations

import base64
import json
import queue
import subprocess
import threading
from typing import Any


class MCPError(RuntimeError):
    pass


class StdioMCP:
    def __init__(self, cmd: list[str], env: dict[str, str], timeout: float = 30):
        self.timeout = timeout
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=env, text=True, bufsize=1)
        self.lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.next_id = 0
        self.lock = threading.Lock()
        self.request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "rta-agentlab", "version": "1"},
        })
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _read(self) -> None:
        for line in self.p.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _send(self, msg: dict) -> None:
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def request(self, method: str, params: dict) -> Any:
        with self.lock:
            self.next_id += 1
            rid = self.next_id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            while True:
                try:
                    line = self.lines.get(timeout=self.timeout)
                except queue.Empty:
                    raise MCPError(f"{method} timed out") from None
                if line is None:
                    raise MCPError("the MCP server exited")
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("method") == "ping" and "id" in msg:
                    self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
                    continue
                if msg.get("id") != rid:
                    continue  # a notification, or a request this client does not serve
                if "error" in msg:
                    raise MCPError(msg["error"].get("message", "error"))
                return msg.get("result", {})

    def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        while True:
            r = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += r.get("tools", [])
            cursor = r.get("nextCursor")
            if not cursor:
                return tools

    def call(self, name: str, args: dict) -> str:
        r = self.request("tools/call", {"name": name, "arguments": args})
        parts = []
        for c in r.get("content", []):
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif c.get("type") == "resource":
                res = c.get("resource", {})
                if "text" in res:
                    parts.append(res["text"])
                elif "blob" in res:
                    parts.append(base64.b64decode(res["blob"]).decode(errors="replace"))
        text = "\n".join(parts)
        if r.get("isError"):
            raise MCPError(text or "tool error")
        return text

    def close(self) -> None:
        try:
            self.p.stdin.close()
            self.p.terminate()
            self.p.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self.p.kill()
