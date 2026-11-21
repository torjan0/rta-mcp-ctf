"""Compare local editor feedback with the server's form validation."""

import json
import subprocess
import unittest
from pathlib import Path

import a6


class EditorValidation(unittest.TestCase):
    def test_browser_and_server_agree(self):
        def schema(properties, **extra):
            return json.dumps({"type": "object", "properties": properties, **extra}, ensure_ascii=False)

        field = {"type": "string"}
        message = a6.STARTER_MESSAGE
        starter = schema({"format": field})
        cases = [(msg, starter) for msg in (
            "", "short", "\u0085" + "x" * 9, "a" * 10, "📝" * 1200, "x" * 1201)]
        cases += [(message, raw) for raw in (
            "{", "[]", schema({}), schema({"format": field}, extra=True),
            schema({str(i): field for i in range(5)}), schema({"bad-key": field}),
            schema({"format": field}, required=["other"]),
            schema({"format": field}, required=["format", "format"]),
            schema({"format": field}, required=None), schema({"format": {"type": "integer"}}),
            starter.ljust(3000), starter.ljust(3001))]
        for attrs in ({"title": "📝" * 200}, {"title": "x" * 201}, {"description": None},
                      {"enum": []}, {"enum": ["PDF", "PDF"]}, {"enum": ["PDF", "CSV"]},
                      {"default": ""}, {"default": "PDF"}, {"default": "PDF", "enum": ["PDF"]}):
            cases.append((message, schema({"format": {**field, **attrs}})))

        js = """
            const {messageError, checkSchema} = require('./static/desktop/a6.js');
            const cases = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
            console.log(JSON.stringify(cases.map(([msg, raw]) =>
              messageError(msg) || checkSchema(raw).error || '')));
        """
        output = subprocess.run(["node", "-e", js], cwd=Path(__file__).parent, text=True,
                                input=json.dumps(cases), capture_output=True, check=True)
        for (msg, raw), browser in zip(cases, json.loads(output.stdout), strict=True):
            with self.subTest(message=msg[:20], schema=raw[:80]):
                try:
                    a6.validate(msg, raw)
                except ValueError as error:
                    self.assertEqual(browser, str(error))
                else:
                    self.assertEqual(browser, "")
