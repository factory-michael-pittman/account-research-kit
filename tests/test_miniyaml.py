import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from account_kit import miniyaml


class Scalars(unittest.TestCase):
    def test_types(self):
        data = miniyaml.loads("a: 1\nb: -2\nc: 50.0\nd: true\ne: null\nf: ~\ng: hello\n")
        self.assertEqual(data, {"a": 1, "b": -2, "c": 50.0, "d": True, "e": None, "f": None, "g": "hello"})

    def test_quoted_strings(self):
        data = miniyaml.loads("a: 'plain # not a comment'\nb: \"MM Prospect Pool\"\nc: 'it''s'\n")
        self.assertEqual(data["a"], "plain # not a comment")
        self.assertEqual(data["b"], "MM Prospect Pool")
        self.assertEqual(data["c"], "it's")

    def test_regexes_survive_single_quotes(self):
        data = miniyaml.loads("pat: '\\bjava\\b|spring boot'\n")
        self.assertEqual(data["pat"], r"\bjava\b|spring boot")

    def test_inline_list(self):
        data = miniyaml.loads("w: [-365, 0]\nx: [null, -180]\ny: ['a', \"b\"]\nz: []\n")
        self.assertEqual(data["w"], [-365, 0])
        self.assertEqual(data["x"], [None, -180])
        self.assertEqual(data["y"], ["a", "b"])
        self.assertEqual(data["z"], [])

    def test_block_lists(self):
        text = "owners:\n  - 'Jason Yu'\n  - Chris Campana\nlabels:\n- observed\n- unknown\n"
        data = miniyaml.loads(text)
        self.assertEqual(data["owners"], ["Jason Yu", "Chris Campana"])
        self.assertEqual(data["labels"], ["observed", "unknown"])  # same-indent list allowed

    def test_nested_maps_and_comments(self):
        text = """
# top comment
scoring:
  bucket_max: 50.0   # inline comment
  stack_step: 0.1
tiers:
  tier1:
    max_rank: 50
    min_score: 40
empty:
"""
        data = miniyaml.loads(text)
        self.assertEqual(data["scoring"], {"bucket_max": 50.0, "stack_step": 0.1})
        self.assertEqual(data["tiers"]["tier1"], {"max_rank": 50, "min_score": 40})
        self.assertIsNone(data["empty"])

    def test_shipped_configs_parse(self):
        base = Path(__file__).resolve().parent.parent / "config"
        signals = miniyaml.load(base / "signals.yaml")
        settings = miniyaml.load(base / "settings.yaml")
        self.assertIn("timing", signals)
        self.assertIn("sfdc", settings)
        self.assertEqual(len(signals["timing"]), 8)
        self.assertEqual(len(signals["study"]), 15)


class Errors(unittest.TestCase):
    def assert_error(self, text, fragment):
        with self.assertRaises(miniyaml.YamlError) as caught:
            miniyaml.loads(text)
        self.assertIn(fragment, str(caught.exception))

    def test_tab_indentation(self):
        self.assert_error("a:\n\tb: 1\n", "tab indentation")

    def test_duplicate_key(self):
        self.assert_error("a: 1\na: 2\n", "duplicate key")

    def test_unterminated_quote(self):
        self.assert_error("a: 'oops\n", "unterminated quoted string")

    def test_key_without_colon(self):
        self.assert_error("a: 1\nbork\n", "expected 'key: value'")

    def test_mapping_value_in_list(self):
        self.assert_error("a:\n  - b: 1\n", "only scalars are allowed here")

    def test_line_numbers(self):
        text = "a: 1\nb: 2\nc: 3\nbroken line here\n"
        with self.assertRaises(miniyaml.YamlError) as caught:
            miniyaml.loads(text)
        self.assertTrue(str(caught.exception).startswith("<string>:4:"), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
