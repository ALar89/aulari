"""Unit tests for aulari.config and aulari.texts."""

import json
import string
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from aulari import config, texts
from aulari.config import Settings


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
        self.app_dir = Path(self.folder.name) / "aulari"

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
    """Tests for aulari.texts and the Spanish strings file."""

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


class OtherSitesTests(unittest.TestCase):
    """other_sites in the config file (RF19)."""

    def test_old_files_and_invalid_entries(self) -> None:
        """A missing list gives []; invalid entries are dropped, valid ones cleaned."""
        self.assertEqual(config.settings_from_dict({}).other_sites, [])
        data = {
            "other_sites": [
                {
                    "site_url": "https://x.test/2025-26",
                    "user_id": 42,
                    "selected_courses": [1],
                    "year_folder": "2n Curs",
                },
                {"site_url": ""},
                "not a dict",
                {"site_url": "https://x.test/2024-25", "user_id": "bad"},
            ]
        }
        sites = config.settings_from_dict(data).other_sites
        self.assertEqual(
            sites,
            [
                {
                    "site_url": "https://x.test/2025-26",
                    "user_id": 42,
                    "selected_courses": [1],
                    "known_courses": [],
                    "year_folder": "2n Curs",
                },
                {
                    "site_url": "https://x.test/2024-25",
                    "user_id": 0,
                    "selected_courses": [],
                    "known_courses": [],
                    "year_folder": "",
                },
            ],
        )


class FolderSettingsTests(unittest.TestCase):
    """year_folder and campus_subfolder in the config file (RF20)."""

    def test_defaults_and_values(self) -> None:
        """Missing or wrong values mean 'no custom name'; strings are kept."""
        empty = config.settings_from_dict({"year_folder": 3, "campus_subfolder": None})
        self.assertEqual((empty.year_folder, empty.campus_subfolder), ("", ""))
        data = {"year_folder": "2n Curs", "campus_subfolder": "Campus"}
        settings = config.settings_from_dict(data)
        self.assertEqual((settings.year_folder, settings.campus_subfolder), ("2n Curs", "Campus"))


class LanguageTests(unittest.TestCase):
    """Catalan, Spanish and English texts, detection and switching."""

    def tearDown(self) -> None:
        """Go back to the default language for the other tests."""
        texts.set_language(texts.DEFAULT_LANGUAGE)

    def placeholders(self, value: str) -> set[str]:
        """Return the {names} used in a text."""
        return {name for _, name, _, _ in string.Formatter().parse(value) if name}

    def test_same_keys_and_placeholders_in_every_language(self) -> None:
        """No language misses a text or a {placeholder}."""
        reference = texts.load_strings("es")
        for code in texts.LANGUAGES:
            strings = texts.load_strings(code)
            self.assertEqual(set(strings), set(reference), code)
            for key, value in strings.items():
                self.assertTrue(value.strip(), (code, key))
                self.assertEqual(
                    self.placeholders(value), self.placeholders(reference[key]), (code, key)
                )
            self.assertEqual(len(strings["weekdays"].split(",")), 7, code)

    def test_detect_language(self) -> None:
        """Locale names from Windows and POSIX map to the three languages."""
        cases = {"ca_ES": "ca", "Catalan_Spain": "ca", "es_ES": "es", "Spanish_Spain": "es"}
        cases.update({"en_US": "en", "French_France": "en", "": "en"})
        for name, expected in cases.items():
            self.assertEqual(texts.detect_language(name), expected, name)

    def test_switching_language(self) -> None:
        """text() follows the current language; unknown codes fall back to Spanish."""
        texts.set_language("ca")
        self.assertEqual(texts.text("sync_button"), "Sincronitza")
        self.assertEqual(texts.text("sync_button", language="en"), "Sync")
        self.assertEqual(texts.set_language("xx"), "es")
        self.assertEqual(texts.text("sync_button"), "Sincronizar")

    def test_language_setting_is_saved(self) -> None:
        """The chosen language survives in the settings file; missing means system."""
        self.assertEqual(config.settings_from_dict({}).language, "")
        self.assertEqual(config.settings_from_dict({"language": "ca"}).language, "ca")


if __name__ == "__main__":
    unittest.main()
