"""A7: a deliberately broken MCP Tasks service inside each player's lab.

The fictional service omits its task-owner check on tasks/get and tasks/update.
The surrounding CTF still isolates player workspaces: a task ID from a
different player never resolves in the current player's simulation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
import uuid
from pathlib import Path

TARGET = 7
COUNT = 13
INPUT_KEY = "approve_export"

def task_time(index: int) -> str:
    return f"2026-11-13T09:{40 + index:02d}:00Z"

OPERATIONS = [
    ("Nina Ford", "weekly-inventory", "completed"),
    ("Avery Lin", "vendor-summary", "working"),
    ("Mara Bell", "access-review", "completed"),
    ("Caleb Price", "quarterly-tax", "failed"),
    ("Ravi Shah", "incident-archive", "completed"),
    ("Leah Chen", "payroll-ledger", "input_required"),
    ("Owen Brooks", "warehouse-billing", "working"),
    ("Mara Bell", "board-packet-export", "input_required"),
    ("Zoe Park", "supplier-statements", "completed"),
    ("Noah Kim", "renewal-forecast", "input_required"),
    ("Jules Rivera", "legal-hold-report", "working"),
    ("Iris Patel", "field-expenses", "completed"),
    ("Avery Lin", "ops-rollup", "failed"),
]


class Store:
    def __init__(self, path: Path, flag: str, clock=time.time):
        self.path, self.flag, self.clock = path, flag, clock
        self.epoch = hashlib.sha256(flag.encode()).hexdigest()[:20]
        with self.connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS a7_state"
                        " (player TEXT PRIMARY KEY, epoch TEXT NOT NULL, completed INTEGER NOT NULL)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def _seed(self, player: str, purpose: str) -> bytes:
        return hmac.new(self.flag.encode(), f"a7:{player}:{purpose}".encode(), hashlib.sha256).digest()

    def task_id(self, player: str, index: int) -> str:
        return str(uuid.UUID(bytes=self._seed(player, f"task:{index}")[:16], version=4))

    def dropbox(self, player: str) -> str:
        return f"drop-{self._seed(player, 'dropbox')[:6].hex()}@inbox.rta-lab.example"

    def _row(self, con, player: str) -> bool:
        con.execute("INSERT OR IGNORE INTO a7_state (player, epoch, completed) VALUES (?, ?, 0)",
                    (player, self.epoch))
        row = con.execute("SELECT epoch, completed FROM a7_state WHERE player = ?", (player,)).fetchone()
        if row[0] != self.epoch:
            con.execute("UPDATE a7_state SET epoch = ?, completed = 0 WHERE player = ?", (self.epoch, player))
            return False
        return bool(row[1])

    def completed(self, player: str) -> bool:
        with self.connect() as con:
            return self._row(con, player)

    def operations(self, player: str) -> str:
        out = []
        for index, (owner, job, status) in enumerate(OPERATIONS):
            out.append(json.dumps({
                "timestamp": task_time(index),
                "source": "vendor-ops/task-receipts", "actor": owner, "job": job,
                "taskId": self.task_id(player, index), "status": status,
            }))
        return "\n".join(out) + "\n"

    def _index(self, player: str, task_id: object) -> int | None:
        if not isinstance(task_id, str):
            return None
        for index in range(COUNT):
            if hmac.compare_digest(self.task_id(player, index), task_id):
                return index
        return None

    def _task(self, player: str, index: int, completed: bool) -> dict:
        owner, job, base_status = OPERATIONS[index]
        status = "completed" if index == TARGET and completed else base_status
        task = {
            "resultType": "complete", "taskId": self.task_id(player, index),
            "status": status, "createdAt": task_time(index), "lastUpdatedAt": task_time(index),
            "pollIntervalMs": 5_000,
            "statusMessage": f"{job} · owner {owner}",
        }
        if index == TARGET and not completed:
            task["inputRequests"] = {INPUT_KEY: {
                "method": "elicitation/create",
                "params": {
                    "mode": "form",
                    "message": "Approve this board packet export and choose its delivery address.",
                    "requestedSchema": {"type": "object", "properties": {
                        "approved": {"type": "boolean", "title": "Approve export"},
                        "deliveryAddress": {"type": "string", "title": "Delivery address"},
                    }, "required": ["approved", "deliveryAddress"]},
                },
            }}
        elif status == "input_required":
            task["inputRequests"] = {"confirm_delivery": {
                "method": "elicitation/create", "params": {"mode": "form",
                    "message": f"Confirm delivery details for {job}.",
                    "requestedSchema": {"type": "object", "properties": {
                        "recipient": {"type": "string"}}, "required": ["recipient"]}},
            }}
        elif status == "completed":
            task["result"] = {"content": [{"type": "text", "text":
                f"{job} delivered to {self.dropbox(player)}" if index == TARGET else f"{job} finished."}],
                "isError": False}
        return task

    @staticmethod
    def _error(request_id, code: int, message: str, data=None) -> dict:
        error = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}

    def rpc(self, player: str, request: object) -> dict:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return self._error(None, -32600, "Invalid JSON-RPC request")
        request_id = request.get("id")
        if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
            return self._error(None, -32600, "A request id is required")
        method = request.get("method")
        if method not in ("tasks/get", "tasks/update"):
            return self._error(request_id, -32601, "Method not found")
        params = request.get("params")
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        capabilities = params.get("_meta", {})
        if not isinstance(capabilities, dict):
            capabilities = {}
        capabilities = capabilities.get("io.modelcontextprotocol/clientCapabilities", {})
        extensions = capabilities.get("extensions", {}) if isinstance(capabilities, dict) else {}
        if not isinstance(extensions, dict) or not isinstance(extensions.get("io.modelcontextprotocol/tasks"), dict):
            return self._error(request_id, -32021, "Missing required client capability", {
                "requiredCapabilities": {"extensions": {"io.modelcontextprotocol/tasks": {}}}})
        index = self._index(player, params.get("taskId"))
        if index is None:
            return self._error(request_id, -32602, "Unknown taskId")
        with self.connect() as con:
            completed = self._row(con, player)
            if method == "tasks/get":
                return {"jsonrpc": "2.0", "id": request_id,
                        "result": self._task(player, index, completed)}
            if index != TARGET or completed:
                return self._error(request_id, -32602, "Task has no outstanding input request")
            responses = params.get("inputResponses")
            answer = responses.get(INPUT_KEY) if isinstance(responses, dict) else None
            if not isinstance(answer, dict) or answer.get("action") != "accept":
                return self._error(request_id, -32602, "Approval form response required")
            content = answer.get("content")
            if not isinstance(content, dict) or content.get("approved") is not True or \
                    content.get("deliveryAddress") != self.dropbox(player):
                return self._error(request_id, -32602, "Approval or delivery address invalid")
            con.execute("UPDATE a7_state SET completed = 1 WHERE player = ? AND epoch = ?", (player, self.epoch))
            return {"jsonrpc": "2.0", "id": request_id, "result": {"resultType": "complete"}}

    def inbox(self, player: str) -> list[dict]:
        if not self.completed(player):
            return []
        return [{"subject": "Board packet export", "to": self.dropbox(player),
                 "body": f"Requested board packet export complete.\nAccess token: {self.flag}"}]
