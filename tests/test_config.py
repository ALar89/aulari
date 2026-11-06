"""Unit tests for campus_sync.config and campus_sync.texts."""

import json
import string
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from campus_sync import config, texts
from campus_sync.config import Settings


class DefaultSiteTests(unittest.TestCase):
    """Tests for default_site_url()."""

    def test_follows_academic_year(self) -> None:
        """Before 1 September it is the previous academic year (D6)."""
        july = datetime(2027, 7, 15, tzinfo=UTC)
        october = datetime(2026, 10, 1, tzinfo=UTC)
        self.assertEqual(config.default_site_url(july), "https://e-aules.uab.cat/2026-27")
        self.assertEqual(config.default_site_url(october), "https://e-aules.uab.cat/2026-27")


class SettingsFileTests(unittest.TestCase):
    """Tests for load_settings() and save_settings()."""

    def setUp(self) -> None:
        """Create an empty app folder."""
        self.folder = tempfile.TemporaryDirectory()
        self.app_dir = Path(self.folder.name) / "campus-sync"

    def tearDown(self) -> None:
        """Delete the folder."""
        self.folder.cleanup()

    def test_missing_file_gives_defaults(self) -> None:
        """Without a config file, defaults are returned."""
        settings = config.load_settings(self.app_dir)
        self.assertEqual(settings.user_id, 0)
        self.assertEqual(settings.selected_courses, [])
        self.assertFalse(settings.first_run_notice_seen)

    def test_round_trip(self) -> None:
        """Saved settings load back equal, with no temporary file left."""
        settings = Settings(
            site_url="https://moodle.example.test/2026-27",
            user_id=42,
            destination="C:/Campus",
            selected_courses=[1072, 472],
            known_courses=[1, 472, 1072],
            first_run_notice_seen=True,
        )
        config.save_settings(self.app_dir, settings)
        self.assertEqual(config.load_settings(self.app_dir), settings)
        self.assertEqual([path.name for path in self.app_dir.iterdir()], ["config.json"])

    def test_wrong_types_fall_back_to_defaults(self) -> None:
        """Hand-edited values of the wrong type are ignored, the rest is kept."""
        self.app_dir.mkdir()
        data = {"user_id": "42", "selected_courses": [1, "x"], "destination": "D:/C"}
        (self.app_dir / "config.json").write_text(json.dumps(data), encoding="utf-8")
        settings = config.load_settings(self.app_dir)
        self.assertEqual((settings.user_id, settings.selected_courses), (0, []))
        self.assertEqual(settings.destination, "D:/C")

    def test_corrupt_file_raises(self) -> None:
        """A corrupt file raises instead of silently losing the settings."""
        self.app_dir.mkdir()
        (self.app_dir / "config.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            config.load_settings(self.app_dir)

    def test_no_secret_fields(self) -> None:
        """The settings never hold a token (CLAUDE.md section 4)."""
        config.save_settings(self.app_dir, Settings())
        raw = (self.app_dir / "config.json").read_text(encoding="utf-8").lower()
        self.assertNotIn("token", raw)
        self.assertNotIn("password", raw)


class TextsTests(unittest.TestCase):
    """Tests for campus_sync.texts and the Spanish strings file."""

    def test_placeholders_are_filled(self) -> None:
        """text() fills {placeholders}."""
        self.assertEqual(texts.text("summary_new", count=3), "Nuevos: 3")

    def test_missing_key_raises(self) -> None:
        """An unknown key is a programming error and raises KeyError."""
        with self.assertRaises(KeyError):
            texts.text("does_not_exist")

    def test_all_strings_are_valid_templates(self) -> None:
        """Every text is non-empty and has well-formed placeholders."""
        for key, value in texts.load_strings().items():
            self.assertTrue(value.strip(), key)
            list(string.Formatter().parse(value))


if __name__ == "__main__":
    unittest.main()
