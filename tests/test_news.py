"""Unit tests for aulari.news (Novedades.txt, RF14)."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from aulari import news
from aulari.sync import ChangedFile
from aulari.texts import text

FIRST = [ChangedFile("2026-27/SO/02 - TEORIA/Tema 5.pdf", False)]
SECOND = [ChangedFile("2026-27/EC/01 - General/P2 (2026-11-20).pdf", True)]


class NewsTests(unittest.TestCase):
    """write_news() and its lines."""

    def setUp(self) -> None:
        """Create a temporary destination."""
        self.folder = tempfile.TemporaryDirectory()
        self.path = news.news_path(Path(self.folder.name), "2026-27")

    def tearDown(self) -> None:
        """Delete it."""
        self.folder.cleanup()

    def test_lines(self) -> None:
        """Course alias in brackets, path inside the course, tag for versions."""
        self.assertEqual(news.change_line(FIRST[0]), "[SO] 02 - TEORIA/Tema 5.pdf")
        expected = f"[EC] 01 - General/P2 (2026-11-20).pdf {text('change_new_version_tag')}"
        self.assertEqual(news.change_line(SECOND[0]), expected)

    def test_newest_block_first(self) -> None:
        """Each sync adds a dated block on top and keeps the older ones."""
        self.assertTrue(news.write_news(self.path, FIRST, datetime(2026, 11, 6, 21, 0)))
        self.assertTrue(news.write_news(self.path, SECOND, datetime(2026, 11, 20, 9, 30)))
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], text("news_header", date="2026-11-20 09:30", count=1))
        self.assertIn("  [SO] 02 - TEORIA/Tema 5.pdf", lines)
        self.assertLess(
            lines.index(
                "  [EC] 01 - General/P2 (2026-11-20).pdf " + text("change_new_version_tag")
            ),
            lines.index("  [SO] 02 - TEORIA/Tema 5.pdf"),
        )

    def test_no_changes_no_file(self) -> None:
        """A sync without changes does not create or touch the file."""
        self.assertFalse(news.write_news(self.path, [], datetime(2026, 11, 6)))
        self.assertFalse(self.path.exists())

    def test_size_is_limited(self) -> None:
        """The file never grows beyond MAX_LINES lines."""
        many = [ChangedFile(f"2026-27/SO/f{index}.pdf", False) for index in range(news.MAX_LINES)]
        news.write_news(self.path, many, datetime(2026, 11, 6))
        news.write_news(self.path, FIRST, datetime(2026, 11, 7))
        self.assertEqual(len(self.path.read_text(encoding="utf-8").splitlines()), news.MAX_LINES)


if __name__ == "__main__":
    unittest.main()
