"""The agent loop shared by the agent challenges.

One run is one short conversation between a fixed user request and Claude on
Amazon Bedrock, with tools that exist only inside this process. The loop is
written out by hand, rather than with the SDK's tool runner, so every limit sits
in one place: the number of model turns, the reply length, and what happens on a
refusal. Nothing a tool does touches a real file, shell or network.

Haiku 4.5 is older than Claude 4.7, so the Anthropic SDK stays below 2.0, whose
Bedrock clients send an API version Bedrock accepts only for 4.7 and later. See
requirements.txt.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from anthropic import Anthropic, AnthropicBedrock

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
MODEL = os.environ.get("AGENTLAB_MODEL", "Scripted stand-in" if os.environ.get("AGENTLAB_BACKEND") == "scripted"
                       else ANTHROPIC_MODEL if os.environ.get("AGENTLAB_BACKEND") == "anthropic"
                       else "us.anthropic.claude-haiku-4-5-20251001-v1:0")
REGION = os.environ.get("AGENTLAB_REGION", "us-west-2")
MAX_TURNS = int(os.environ.get("AGENTLAB_MAX_TURNS", "6"))
MAX_TOKENS = int(os.environ.get("AGENTLAB_MAX_TOKENS", "1024"))

_client: AnthropicBedrock | Anthropic | None = None


def client() -> AnthropicBedrock | Anthropic:
    global _client
    if _client is None:
        _client = Anthropic() if os.environ.get("AGENTLAB_BACKEND") == "anthropic" else AnthropicBedrock(aws_region=REGION)
    return _client


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    result: str


@dataclass
class Run:
    calls: list[ToolCall] = field(default_factory=list)
    final_text: str = ""
    stop: str = ""
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    refused: bool = False
    error: str = ""
    cache_write: int = 0
    cache_read: int = 0
    view: Any = None  # what a challenge shows the player, filled in by the challenge


Handler = Callable[[dict[str, Any]], str]


def run_agent(
    system: str, user: str, tools: list[dict], handlers: dict[str, Handler], max_turns: int = MAX_TURNS
) -> Run:
    """Run one conversation and return every tool call it made."""
    if os.environ.get("AGENTLAB_BACKEND") == "claude-code":
        from cc_backend import run_claude_code

        return run_claude_code(system, user, tools, handlers, max_turns)
    backend = os.environ.get("AGENTLAB_BACKEND", "bedrock")
    run = Run()
    messages: list[dict] = [{"role": "user", "content": user}]
    # One cache marker after the system prompt caches the tools and the system
    # prompt, which repeat on every turn and in every run of a challenge. Haiku 4.5
    # caches only a prefix of 4,096 tokens or more, so the small in-memory tool
    # sets are not cached and the GitHub MCP tool set is. Bedrock's integration
    # for Haiku 4.5 takes explicit markers only.
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    for _ in range(max_turns):
        run.turns += 1
        try:
            resp = client().messages.create(
                model=ANTHROPIC_MODEL if backend == "anthropic" else MODEL,
                max_tokens=MAX_TOKENS,
                system=system_blocks,
                tools=tools,
                messages=messages,
            )
        except Exception as e:  # noqa: BLE001, a failed call ends the run, it never crashes the app
            run.error = f"{type(e).__name__}: {str(e)[:200]}"
            run.stop = "error"
            return run
        # input_tokens is kept in full-price tokens, so the spend cap stays right:
        # a cache write costs 1.25 times the base input price and a read 0.1 times.
        write = getattr(resp.usage, "cache_creation_input_tokens", 0) or 0
        read = getattr(resp.usage, "cache_read_input_tokens", 0) or 0
        run.cache_write += write
        run.cache_read += read
        run.input_tokens += resp.usage.input_tokens + round(1.25 * write + 0.1 * read)
        run.output_tokens += resp.usage.output_tokens
        run.stop = resp.stop_reason or ""
        if resp.stop_reason == "refusal":
            run.refused = True
            return run
        text = [b.text for b in resp.content if b.type == "text"]
        if text:
            run.final_text = "\n".join(text)
        uses = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not uses:
            return run
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for u in uses:
            handler = handlers.get(u.name)
            args = u.input if isinstance(u.input, dict) else {}
            if handler is None:
                out, is_error = f"Unknown tool {u.name}", True
            else:
                try:
                    out, is_error = handler(args), False
                except Exception as e:  # noqa: BLE001, a tool error goes back to the model as text
                    out, is_error = f"Error: {e}", True
            run.calls.append(ToolCall(u.name, args, out))
            results.append(
                {"type": "tool_result", "tool_use_id": u.id, "content": out, "is_error": is_error}
            )
        # Every result goes back in one user message.
        messages.append({"role": "user", "content": results})
    run.stop = "max_turns"
    return run


def to_json(run: Run) -> str:
    return json.dumps(
        {
            "calls": [c.__dict__ for c in run.calls],
            "final_text": run.final_text,
            "stop": run.stop,
            "turns": run.turns,
            "tokens": [run.input_tokens, run.output_tokens],
            "cache": [run.cache_write, run.cache_read],
            "refused": run.refused,
            "error": run.error,
        },
        indent=2,
    )
