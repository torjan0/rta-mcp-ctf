"""Shared handler parser cases for the package editor and server."""

import json
import unittest
from pathlib import Path

import a5


CASES = json.loads((Path(__file__).parent / "static" / "desktop" / "handler_cases.json").read_text())


class HandlerCases(unittest.TestCase):
    def test_python_grammar(self):
        for case in CASES:
            with self.subTest(case=case["name"]):
                try:
                    a5.JsHandler(case["source"])
                    result = {"ok": True}
                except a5.Invalid as error:
                    result = {"ok": False, "error": str(error)}
                self.assertEqual(result, {key: case[key] for key in result})

