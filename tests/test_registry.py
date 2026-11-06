"""Unit tests for campus_sync.registry, using temporary SQLite files."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from campus_sync.registry import (
    STATE_MISSING,
    STATE_PRESENT,
    STATE_REMOVED,
    FileKey,
    FileStatus,
    Registry,
    RegistryError,
)

SITE_URL = "https://moodle.example.test/2026-27"
COURSE = 1072
KEY = FileKey(cmid=55132, filearea="content", filepath="/", filename="Tema 1.pdf")
OTHER_KEY = FileKey(cmid=55133, filearea="content", filepath="/practiques/", filename="P1.zip")


def version(path: str, timemodified: int = 1000, filesize: int = 50, sha256: str = "aa") -> dict:
    """Return the version data that record_download() expects."""
    return {
        "relative_path": path,
        "timemodified": timemodified,
        "filesize": filesize,
        "sha256": sha256,
    }


class RegistryTestCase(unittest.TestCase):
    """Base class: a fresh registry in a temporary folder, with one site and course."""

    def setUp(self) -> None:
        """Create the database, a site and a course."""
        self.folder = tempfile.TemporaryDirectory()
        self.db_path = Path(self.folder.name) / "data" / "registry.sqlite3"
        self.registry = Registry(self.db_path)
        self.site = self.registry.get_or_create_site(SITE_URL, 42, "2026-27")
        self.registry.ensure_course(self.site, COURSE, "SO", "2026-27/SO")

    def tearDown(self) -> None:
        """Close the database before deleting the folder (Windows locks open files)."""
        self.registry.close()
        self.folder.cleanup()


class SchemaTests(RegistryTestCase):
    """Tests for database creation and schema version."""

    def test_reopening_keeps_data(self) -> None:
        """Closing and opening again keeps the site and does not recreate tables."""
        self.registry.close()
        with Registry(self.db_path) as reopened:
            self.assertEqual(reopened.get_or_create_site(SITE_URL, 42, "2026-27"), self.site)

    def test_newer_schema_is_rejected(self) -> None:
        """A database from a newer app version raises RegistryError."""
        self.registry.close()
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA user_version = 99")
        conn.close()
        with self.assertRaises(RegistryError):
            Registry(self.db_path)

    def test_invalid_state_is_rejected_by_database(self) -> None:
        """The CHECK constraint protects the state column even from raw SQL."""
        self.registry.record_download(self.site, COURSE, KEY, version("2026-27/SO/a.pdf"))
        with self.assertRaises(sqlite3.IntegrityError), self.registry._conn:
            self.registry._conn.execute("UPDATE files SET state = 'deleted'")


class SiteCourseSectionTests(RegistryTestCase):
    """Tests for sites, courses and sections."""

    def test_site_is_created_once(self) -> None:
        """The same site and user give the same id; another user gets a new one."""
        self.assertEqual(self.registry.get_or_create_site(SITE_URL, 42, "2026-27"), self.site)
        self.assertNotEqual(self.registry.get_or_create_site(SITE_URL, 7, "2026-27"), self.site)

    def test_existing_course_keeps_alias(self) -> None:
        """ensure_course() never overwrites an alias chosen earlier."""
        course = self.registry.ensure_course(self.site, COURSE, "OTHER", "2026-27/OTHER")
        self.assertEqual(course["alias"], "SO")
        self.assertEqual(course["folder"], "2026-27/SO")

    def test_section_folder_is_fixed(self) -> None:
        """A renamed section in Moodle keeps its original folder."""
        first = self.registry.section_folder(self.site, COURSE, 900, "03 - Tema 3")
        second = self.registry.section_folder(self.site, COURSE, 900, "03 - Processos")
        self.assertEqual(first, "03 - Tema 3")
        self.assertEqual(second, "03 - Tema 3")


class VersionTests(RegistryTestCase):
    """Tests for classify() and record_download()."""

    def test_classify_new_unchanged_changed(self) -> None:
        """New until downloaded; changed if timemodified or filesize differ."""
        self.assertEqual(self.registry.classify(self.site, KEY, 1000, 50), FileStatus.NEW)
        self.registry.record_download(self.site, COURSE, KEY, version("2026-27/SO/a.pdf"))
        self.assertEqual(self.registry.classify(self.site, KEY, 1000, 50), FileStatus.UNCHANGED)
        self.assertEqual(self.registry.classify(self.site, KEY, 2000, 50), FileStatus.CHANGED)
        self.assertEqual(self.registry.classify(self.site, KEY, 1000, 51), FileStatus.CHANGED)

    def test_new_content_creates_version(self) -> None:
        """A different SHA-256 adds version 2 and keeps version 1."""
        first = self.registry.record_download(self.site, COURSE, KEY, version("2026-27/SO/a.pdf"))
        second = self.registry.record_download(
            self.site, COURSE, KEY, version("2026-27/SO/a (2026-11-06).pdf", 2000, 60, "bb")
        )
        self.assertTrue(first)
        self.assertTrue(second)
        latest = self.registry.latest_version(self.site, KEY)
        self.assertEqual(latest["version"], 2)
        self.assertEqual(latest["relative_path"], "2026-27/SO/a (2026-11-06).pdf")

    def test_same_content_only_updates_date(self) -> None:
        """Same SHA-256 with a new timemodified does not create a version (design §6)."""
        self.registry.record_download(self.site, COURSE, KEY, version("2026-27/SO/a.pdf"))
        created = self.registry.record_download(
            self.site, COURSE, KEY, version("ignored/path.pdf", 3000, 50, "aa")
        )
        self.assertFalse(created)
        latest = self.registry.latest_version(self.site, KEY)
        self.assertEqual(latest["version"], 1)
        self.assertEqual(latest["timemodified"], 3000)
        self.assertEqual(latest["relative_path"], "2026-27/SO/a.pdf")
        self.assertEqual(self.registry.classify(self.site, KEY, 3000, 50), FileStatus.UNCHANGED)


class StateTests(RegistryTestCase):
    """Tests for mark_removed(), mark_seen(), set_state() and files_of_course()."""

    def setUp(self) -> None:
        """Register two downloaded files."""
        super().setUp()
        self.registry.record_download(self.site, COURSE, KEY, version("2026-27/SO/a.pdf"))
        self.registry.record_download(self.site, COURSE, OTHER_KEY, version("2026-27/SO/P1.zip"))

    def states(self) -> dict:
        """Return {filename: state} for the course."""
        rows = self.registry.files_of_course(self.site, COURSE)
        return {row["filename"]: row["state"] for row in rows}

    def test_files_missing_from_moodle_are_marked(self) -> None:
        """Only files not seen in this sync become removed_in_moodle."""
        count = self.registry.mark_removed(self.site, COURSE, {KEY})
        self.assertEqual(count, 1)
        self.assertEqual(self.states(), {"Tema 1.pdf": STATE_PRESENT, "P1.zip": STATE_REMOVED})
        self.assertEqual(self.registry.mark_removed(self.site, COURSE, {KEY}), 0)

    def test_file_back_in_moodle_is_present_again(self) -> None:
        """mark_seen() undoes removed_in_moodle but not missing."""
        self.registry.mark_removed(self.site, COURSE, {KEY})
        self.registry.set_state(self.site, KEY, STATE_MISSING)
        self.registry.mark_seen(self.site, OTHER_KEY)
        self.registry.mark_seen(self.site, KEY)
        self.assertEqual(self.states(), {"Tema 1.pdf": STATE_MISSING, "P1.zip": STATE_PRESENT})

    def test_invalid_state_is_rejected(self) -> None:
        """set_state() only accepts the three valid states."""
        with self.assertRaises(ValueError):
            self.registry.set_state(self.site, KEY, "deleted")

    def test_files_of_course_shows_latest_version(self) -> None:
        """Each file appears once, with its newest version."""
        self.registry.record_download(
            self.site, COURSE, KEY, version("2026-27/SO/a (v2).pdf", 2000, 60, "bb")
        )
        rows = self.registry.files_of_course(self.site, COURSE)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["version"], 2)
        self.assertEqual(rows[0]["relative_path"], "2026-27/SO/a (v2).pdf")


class RenameTests(RegistryTestCase):
    """Tests for rename_course_folder() (RF4)."""

    def test_rename_updates_alias_and_paths(self) -> None:
        """Alias, folder and every version path change together."""
        self.registry.record_download(
            self.site, COURSE, KEY, version("2026-27/SO/00 - General/a.pdf")
        )
        self.registry.record_download(
            self.site, COURSE, KEY, version("2026-27/SO/00 - General/a (v2).pdf", 2, 2, "bb")
        )
        self.registry.rename_course_folder(self.site, COURSE, "SOP", "2026-27/SOP")
        course = self.registry.ensure_course(self.site, COURSE, "x", "x")
        self.assertEqual((course["alias"], course["folder"]), ("SOP", "2026-27/SOP"))
        paths = [
            row["relative_path"]
            for row in self.registry._conn.execute("SELECT relative_path FROM file_versions")
        ]
        self.assertEqual(
            sorted(paths), ["2026-27/SOP/00 - General/a (v2).pdf", "2026-27/SOP/00 - General/a.pdf"]
        )

    def test_rename_does_not_touch_similar_prefixes(self) -> None:
        """A folder named 'SO' must not rewrite paths of another course in 'SOP'."""
        self.registry.ensure_course(self.site, 2000, "SOP", "2026-27/SOP")
        self.registry.record_download(self.site, 2000, OTHER_KEY, version("2026-27/SOP/P1.zip"))
        self.registry.rename_course_folder(self.site, COURSE, "SO2", "2026-27/SO2")
        latest = self.registry.latest_version(self.site, OTHER_KEY)
        self.assertEqual(latest["relative_path"], "2026-27/SOP/P1.zip")

    def test_unknown_course_raises(self) -> None:
        """Renaming a course that is not registered raises KeyError."""
        with self.assertRaises(KeyError):
            self.registry.rename_course_folder(self.site, 999, "X", "2026-27/X")


if __name__ == "__main__":
    unittest.main()
