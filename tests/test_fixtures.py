"""Tests on anonymized real e-aules responses (tests/fixtures/).

The fixtures come from one real course, anonymized with poc/make_fixtures.py.
They check that moodle_items understands the real structure, and that the
fixtures contain no real names, hosts or secrets.
"""

import json
import re
import unittest
from collections import Counter
from pathlib import Path

from campus_sync import moodle_items

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
# Numbers verified with the real sync of this course (design.md section 10).
CONTENT_ITEMS = 26  # 15 folder + 10 resource + 1 file embedded in a page
ASSIGNMENT_ITEMS = 8
ALLOWED_STRING = re.compile(
    r"file_\d+\.\w+|index\.html|/(dir_\d+/)*|Section \d+|[a-z]+ \d+|\w+|\d+"
    r"|https://moodle\.example\.test/2026-27/webservice/pluginfile\.php/[\w/.]+\?forcedownload=1"
    r"|https://external\.example\.org/link\d+"
    r"|[a-z]+/[\w.+-]+"  # MIME types
    r"|No access rights in module context"
)


def load_fixture(name: str) -> object:
    """Return the parsed JSON of a fixture."""
    with (FIXTURES_DIR / name).open(encoding="utf-8") as in_file:
        result = json.load(in_file)
    return result


def all_strings(value: object) -> list[str]:
    """Return every string inside a JSON value (keys excluded), recursively."""
    result = []
    if isinstance(value, dict):
        for item in value.values():
            result.extend(all_strings(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(all_strings(item))
    elif isinstance(value, str):
        result.append(value)
    return result


class RealStructureTests(unittest.TestCase):
    """moodle_items on the real course structure."""

    def setUp(self) -> None:
        """Load both fixtures."""
        self.sections = load_fixture("course_contents.json")
        self.assignments = load_fixture("assignments.json")

    def test_contents_items(self) -> None:
        """Real contents give 26 files: no page HTML, no hidden modules."""
        items = moodle_items.items_from_contents(self.sections)
        self.assertEqual(len(items), CONTENT_ITEMS)
        self.assertNotIn("index.html", [item.key.filename for item in items])
        self.assertEqual(Counter(item.key.filearea for item in items), {"content": CONTENT_ITEMS})

    def test_assignment_items(self) -> None:
        """Real assignments give 8 attachments, all from visible modules."""
        items = moodle_items.items_from_assignments(self.assignments, self.sections)
        self.assertEqual(len(items), ASSIGNMENT_ITEMS)
        self.assertEqual({item.key.filearea for item in items}, {"introattachment"})
        self.assertTrue(all(item.subfolder for item in items))

    def test_keys_are_unique(self) -> None:
        """No two items share a registry key."""
        items = moodle_items.items_from_contents(self.sections)
        items = items + moodle_items.items_from_assignments(self.assignments, self.sections)
        keys = [item.key for item in items]
        self.assertEqual(len(keys), len(set(keys)))

    def test_inaccessible_assignments_are_warnings(self) -> None:
        """Assignments without access come as warnings with code 1 (design §10)."""
        codes = {warning["warningcode"] for warning in self.assignments["warnings"]}
        self.assertEqual(codes, {"1"})


class AnonymizationTests(unittest.TestCase):
    """The fixtures must never contain real data (CLAUDE.md section 4)."""

    def test_only_generic_strings(self) -> None:
        """Every string matches a known generic pattern."""
        for name in ["course_contents.json", "assignments.json"]:
            for text in all_strings(load_fixture(name)):
                self.assertRegex(text, f"^(?:{ALLOWED_STRING.pattern})$", f"{name}: {text!r}")

    def test_no_real_hosts_or_secrets(self) -> None:
        """No UAB host, e-mail address or token-like parameter in the raw text."""
        for path in FIXTURES_DIR.glob("*.json"):
            raw = path.read_text(encoding="utf-8").lower()
            for forbidden in ["uab", "e-aules", "@", "token", "wstoken"]:
                self.assertNotIn(forbidden, raw, path.name)


if __name__ == "__main__":
    unittest.main()
