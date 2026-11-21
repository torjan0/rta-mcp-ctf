"""Run the local challenge tools through Claude Code's MCP client.

The CLI owns the model conversation. The HTTP server is loopback only and its
handlers stay in the process that called engine.run_agent.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from engine import Handler, MAX_TOKENS, Run, ToolCall

MODEL = "claude-haiku-4-5-20251001"
SERVER_NAME = "agentlab"


class MCPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, tools: list[dict], handlers: dict[str, Handler]):
        self.tools = tools
        self.handlers = handlers
        self.calls: list[ToolCall] = []
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), MCPHandler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/mcp"


class MCPHandler(BaseHTTPRequestHandler):
    server: MCPServer

    def log_message(self, _format: str, *_args: Any) -> None:
        pass

    def do_POST(self) -> None:
        if self.path != "/mcp":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1024 * 1024 or length < 0:
                self.send_error(413)
                return
            request = json.loads(self.rfile.read(length))
            response = self.dispatch(request)
        except (ValueError, TypeError, json.JSONDecodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid request"}}
        if response is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self, request: dict) -> dict | None:
        if not isinstance(request, dict):
            raise ValueError("Request must be an object")
        method = request.get("method")
        if "id" not in request:
            return None
        answer: dict[str, Any] = {"jsonrpc": "2.0", "id": request["id"]}
        if method == "initialize":
            answer["result"] = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "1.0"},
            }
        elif method == "ping":
            answer["result"] = {}
        elif method == "tools/list":
            answer["result"] = {"tools": [
                {"name": t["name"], "description": t.get("description", ""),
                 "inputSchema": t["input_schema"]} for t in self.server.tools
            ]}
        elif method == "tools/call":
            params = request.get("params") or {}
            if not isinstance(params, dict):
                params = {}
            name = params.get("name", "")
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}
            handler = self.server.handlers.get(name)
            if handler is None:
                result, is_error = f"Unknown tool {name}", True
            else:
                try:
                    result, is_error = str(handler(args)), False
                except Exception as exc:  # noqa: BLE001, returned to the model
                    result, is_error = f"Error: {exc}", True
            with self.server.lock:
                self.server.calls.append(ToolCall(name, args, result))
            answer["result"] = {"content": [{"type": "text", "text": result}], "isError": is_error}
        else:
            answer["error"] = {"code": -32601, "message": "Method not found"}
        return answer


def parse_stream(stream: str, run: Run) -> None:
    """Apply the CLI's completed stream-json events to one Run."""
    last_message_id = None
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            message = event.get("message") or {}
            message_id = message.get("id")
            if message_id and message_id != last_message_id:
                run.turns += 1
                last_message_id = message_id
                usage = message.get("usage") or {}
                write = usage.get("cache_creation_input_tokens") or 0
                read = usage.get("cache_read_input_tokens") or 0
                run.cache_write += write
                run.cache_read += read
                run.input_tokens += (usage.get("input_tokens") or 0) + round(1.25 * write + 0.1 * read)
                run.output_tokens += usage.get("output_tokens") or 0
            parts = [part.get("text", "") for part in message.get("content", [])
                     if part.get("type") == "text"]
            if parts:
                run.final_text = "\n".join(parts)
            if message.get("stop_reason"):
                run.stop = message["stop_reason"]
                run.refused = run.stop == "refusal"
        elif event.get("type") == "result":
            if event.get("subtype") == "error_max_turns":
                run.stop = "max_turns"
            elif event.get("is_error"):
                run.error = str(event.get("subtype") or "Claude Code failed")[:200]
                run.stop = "error"
            elif event.get("result") and not run.final_text:
                run.final_text = str(event["result"])
            usage = event.get("usage") or {}
            if run.turns == 0:
                run.input_tokens = usage.get("input_tokens") or 0
                run.output_tokens = usage.get("output_tokens") or 0


def run_claude_code(system: str, user: str, tools: list[dict],
                    handlers: dict[str, Handler], max_turns: int) -> Run:
    run = Run()
    server = MCPServer(tools, handlers)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="agentlab-cc-") as working_dir:
            config = json.dumps({"mcpServers": {SERVER_NAME: {"type": "http", "url": server.url}}})
            command = [
                "claude", "-p", "--model", MODEL, "--system-prompt", system,
                "--tools", "", "--strict-mcp-config", "--mcp-config", config,
                "--allowedTools", *[f"mcp__{SERVER_NAME}__{t['name']}" for t in tools],
                "--max-turns", str(max_turns), "--output-format", "stream-json",
                "--verbose", "--no-session-persistence", "--disable-slash-commands",
                "--setting-sources", "", "--settings", '{"disableAllHooks":true}',
                "--restricted", "--prompt-suggestions", "false",
                "--permission-mode", "dontAsk", "--permission-prompts", "none",
                user,
            ]
            env = os.environ.copy()
            for key in list(env):
                if key.startswith("AWS_") or key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                        "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
                    env.pop(key, None)
            env["MAX_THINKING_TOKENS"] = "0"
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(MAX_TOKENS)
            env["CLAUDE_CODE_DISABLE_TELEMETRY"] = "1"
            try:
                completed = subprocess.run(command, cwd=working_dir, env=env,
                                           capture_output=True, text=True, timeout=240, check=False)
                parse_stream(completed.stdout, run)
                if completed.returncode and not run.error:
                    if run.stop != "max_turns":
                        run.error = f"Claude Code exited {completed.returncode}"
                        run.stop = "error"
                if not run.stop:
                    run.stop = "end_turn" if completed.returncode == 0 else "error"
            except (OSError, subprocess.TimeoutExpired) as exc:
                run.error = f"{type(exc).__name__}: {str(exc)[:160]}"
                run.stop = "error"
        run.calls = server.calls.copy()
        if run.turns >= max_turns and run.stop == "tool_use":
            run.stop = "max_turns"
        return run
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
