"""Offline checks for the public Docker mode and Anthropic adapter."""

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import engine
from selfhost_mode import scripted_notice

ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backend = load("selfhost_backend_test", ROOT / "selfhost/backend.py")
seed = load("selfhost_seed_test", ROOT / "deploy/aws/ctfd_seed.py")


class AnthropicBackendTest(unittest.TestCase):
    def test_tool_loop_uses_haiku_api_and_same_limits(self):
        use = SimpleNamespace(type="tool_use", id="tool-1", name="check", input={"value": "hello"})
        first = SimpleNamespace(content=[use], stop_reason="tool_use",
                                usage=SimpleNamespace(input_tokens=10, output_tokens=3,
                                                      cache_creation_input_tokens=4, cache_read_input_tokens=2))
        second = SimpleNamespace(content=[SimpleNamespace(type="text", text="Done")], stop_reason="end_turn",
                                 usage=SimpleNamespace(input_tokens=7, output_tokens=2))
        fake = SimpleNamespace(messages=SimpleNamespace(create=mock.Mock(side_effect=[first, second])))
        tool = {"name": "check", "input_schema": {"type": "object", "properties": {}}}
        with mock.patch.dict(os.environ, {"AGENTLAB_BACKEND": "anthropic"}), \
             mock.patch.object(engine, "Anthropic", return_value=fake) as constructor, \
             mock.patch.object(engine, "AnthropicBedrock") as bedrock, \
             mock.patch.object(engine, "_client", None):
            result = engine.run_agent("System", "User", [tool], {"check": lambda args: args["value"]}, max_turns=2)
        constructor.assert_called_once_with()
        bedrock.assert_not_called()
        self.assertEqual(result.final_text, "Done")
        self.assertEqual(result.calls[0].result, "hello")
        self.assertEqual((result.turns, result.input_tokens, result.output_tokens), (2, 22, 5))
        first_call, second_call = fake.messages.create.call_args_list
        self.assertEqual(first_call.kwargs["model"], engine.ANTHROPIC_MODEL)
        self.assertEqual(first_call.kwargs["max_tokens"], engine.MAX_TOKENS)
        self.assertEqual(first_call.kwargs["system"][0]["text"], "System")
        self.assertEqual(first_call.kwargs["tools"], [tool])
        self.assertEqual(second_call.kwargs["messages"][-1]["content"][0]["content"], "hello")

    def test_error_stops_without_another_call(self):
        fake = SimpleNamespace(messages=SimpleNamespace(create=mock.Mock(side_effect=RuntimeError("offline test"))))
        with mock.patch.dict(os.environ, {"AGENTLAB_BACKEND": "anthropic"}), \
             mock.patch.object(engine, "Anthropic", return_value=fake), \
             mock.patch.object(engine, "_client", None):
            result = engine.run_agent("s", "u", [], {}, max_turns=6)
        self.assertEqual(result.stop, "error")
        self.assertEqual(result.turns, 1)
        fake.messages.create.assert_called_once()


class SelfhostSwitchTest(unittest.TestCase):
    def test_backend_precedence(self):
        self.assertEqual(backend.select_backend({}), "scripted")
        self.assertEqual(backend.select_backend({"AWS_ACCESS_KEY_ID": "a", "AWS_SECRET_ACCESS_KEY": "b"}), "bedrock")
        self.assertEqual(backend.select_backend({"ANTHROPIC_API_KEY": "key", "AWS_ACCESS_KEY_ID": "a",
                                                 "AWS_SECRET_ACCESS_KEY": "b"}), "anthropic")
        self.assertEqual(backend.select_backend({"AGENTLAB_BACKEND": "scripted", "ANTHROPIC_API_KEY": "key"}), "scripted")

    def test_fake_issue_text_and_hints_do_not_require_github(self):
        live = {c["key"]: c for c in seed.challenges("http://lab.local/")}
        local = {c["key"]: c for c in seed.challenges("http://lab.local/", selfhost=True)}
        for key in ("a2", "a3"):
            self.assertIn("Save your GitHub username", live[key]["description"])
            self.assertNotIn("Save your GitHub username", local[key]["description"])
            self.assertNotIn("GitHub account page", " ".join(local[key]["hints"]))
            self.assertIn("Write your issue in the lab", local[key]["description"])

    def test_scripted_notice_on_hub(self):
        with mock.patch.dict(os.environ, {"AGENTLAB_BACKEND": "scripted"}):
            self.assertIn("not a real AI model", scripted_notice())
        with mock.patch.dict(os.environ, {"AGENTLAB_BACKEND": "anthropic"}):
            self.assertEqual(scripted_notice(), "")


if __name__ == "__main__":
    unittest.main()
