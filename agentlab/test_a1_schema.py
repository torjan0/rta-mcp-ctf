import json
import unittest
from pathlib import Path

import a1_schema


class ServerDocuments(unittest.TestCase):
    def test_shared_cases(self):
        for case in json.loads(Path(__file__).with_name("a1_cases.json").read_text()):
            with self.subTest(case=case["label"]):
                if case["error"] is None:
                    self.assertEqual(a1_schema.validate(case["source"]), json.loads(case["source"]))
                else:
                    with self.assertRaises(ValueError) as raised:
                        a1_schema.validate(case["source"])
                    self.assertEqual(str(raised.exception), case["error"])
