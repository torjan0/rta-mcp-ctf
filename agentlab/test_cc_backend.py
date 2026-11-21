"""Local tests for the Claude Code adapter. No CLI or external service is used."""

import json
import unittest
from types import SimpleNamespace

from cc_backend import MCPHandler, parse_stream
from engine import Run


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        tool = {"name": "check", "description": "Check a value.",
                "input_schema": {"type": "object", "properties": {"value": {"type": "string"}}}}
        server = SimpleNamespace(tools=[tool], handlers={"check": lambda args: args["value"]},
                                 calls=self.calls, lock=__import__("threading").Lock())
        self.endpoint = object.__new__(MCPHandler)
        self.endpoint.server = server

    def rpc(self, method, params=None):
        return self.endpoint.dispatch({"jsonrpc": "2.0", "id": 1, "method": method,
                                       "params": params or {}})

    def test_initialize_and_list(self):
        self.assertEqual(self.rpc("initialize")["result"]["capabilities"], {"tools": {}})
        tools = self.rpc("tools/list")["result"]["tools"]
        self.assertEqual(tools[0]["inputSchema"]["properties"]["value"]["type"], "string")
        self.assertEqual(tools[0]["name"], "check")

    def test_call_and_errors(self):
        answer = self.rpc("tools/call", {"name": "check", "arguments": {"value": "ok"}})
        self.assertEqual(answer["result"], {"content": [{"type": "text", "text": "ok"}],
                                             "isError": False})
        self.assertEqual(self.calls[0].name, "check")
        self.assertEqual(self.calls[0].args, {"value": "ok"})
        self.assertTrue(self.rpc("tools/call", {"name": "missing"})["result"]["isError"])
        self.assertEqual(self.rpc("other")["error"]["code"], -32601)
        self.assertIsNone(self.endpoint.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}))


class StreamTests(unittest.TestCase):
    def test_turns_text_usage_and_refusal(self):
        events = [
            {"type": "assistant", "message": {"id": "one", "stop_reason": "tool_use",
                "content": [{"type": "tool_use", "name": "mcp__agentlab__check"}],
                "usage": {"input_tokens": 10, "output_tokens": 4,
                          "cache_creation_input_tokens": 8, "cache_read_input_tokens": 10}}},
            {"type": "assistant", "message": {"id": "two", "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "Done."}],
                "usage": {"input_tokens": 20, "output_tokens": 5}}},
            {"type": "result", "is_error": False, "result": "Done."},
        ]
        run = Run()
        parse_stream("\n".join(json.dumps(x) for x in events), run)
        self.assertEqual((run.turns, run.final_text, run.stop), (2, "Done.", "end_turn"))
        self.assertEqual((run.input_tokens, run.output_tokens), (41, 9))
        self.assertEqual((run.cache_write, run.cache_read), (8, 10))
        refused = Run()
        parse_stream(json.dumps({"type": "assistant", "message": {"id": "r", "stop_reason": "refusal",
                     "content": []}}), refused)
        self.assertTrue(refused.refused)

    def test_error_result(self):
        run = Run()
        parse_stream(json.dumps({"type": "result", "is_error": True,
                                 "subtype": "error_during_execution"}), run)
        self.assertEqual(run.stop, "error")
        self.assertEqual(run.error, "error_during_execution")
        capped = Run()
        parse_stream(json.dumps({"type": "result", "is_error": True,
                                 "subtype": "error_max_turns"}), capped)
        self.assertEqual(capped.stop, "max_turns")
        self.assertFalse(capped.error)


if __name__ == "__main__":
    unittest.main()
