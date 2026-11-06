"""Unit tests for campus_sync.sync: whole sync runs with a fake Moodle."""

import hashlib
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path

import requests

from campus_sync import sync
from campus_sync.moodle_client import InvalidTokenError
from campus_sync.registry import STATE_MISSING, STATE_REMOVED, Registry
from campus_sync.sync import Course, NotOwnerError, SyncLockedError, SyncTarget

SITE = "https://moodle.example.test/2026-27"
BASE = SITE + "/webservice/pluginfile.php"
MACHINE = "machine-a"
T1 = int(datetime(2026, 10, 1, 10, 0, tzinfo=UTC).timestamp())
T2 = int(datetime(2026, 11, 6, 17, 5, tzinfo=UTC).timestamp())  # 18:05 in Madrid
GUIDE_CMID = 2
COURSE = Course(1072, "Sistemes Operatius - Grau Enginyeria Informàtica 26-27 [MO86824]")


class FakeMoodle:
    """Fake MoodleClient: one course with editable files, links and assignments."""

    def __init__(self) -> None:
        """Start with one resource, one folder file and one link."""
        self.bodies = {}
        self.modules = []
        self.assignments = []
        self.fail_urls = set()
        self.invalid_token = False
        self.downloads = 0
        self.add_resource(GUIDE_CMID, "guia.pdf", b"guide v1", T1)
        folder_url = f"{BASE}/78/mod_folder/content/1/extra/a.c"
        self.bodies[folder_url] = b"int main;"
        folder_file = self._content("a.c", "/extra/", folder_url, b"int main;", T1)
        self.modules.append(
            {"id": 4, "modname": "folder", "name": "Exemples", "contents": [folder_file]}
        )
        self.links = {5: "https://docs.example.org/"}

    def add_resource(self, cmid: int, filename: str, body: bytes, timemodified: int) -> None:
        """Add or replace a resource module with one file."""
        url = f"{BASE}/77/mod_resource/content/1/{filename}"
        self.bodies[url] = body
        self.modules = [module for module in self.modules if module["id"] != cmid]
        self.modules.append(
            {
                "id": cmid,
                "modname": "resource",
                "name": filename,
                "contents": [self._content(filename, "/", url, body, timemodified)],
            }
        )

    def _content(self, filename: str, filepath: str, url: str, body: bytes, tm: int) -> dict:
        """Return a 'file' content entry."""
        return {
            "type": "file",
            "filename": filename,
            "filepath": filepath,
            "filesize": len(body),
            "timemodified": tm,
            "fileurl": url,
        }

    def sections(self) -> list[dict]:
        """Return core_course_get_contents for the course."""
        modules = list(self.modules)
        for cmid, link in self.links.items():
            modules.append(
                {
                    "id": cmid,
                    "modname": "url",
                    "name": "Docs",
                    "contents": [{"type": "url", "fileurl": link, "timemodified": T1}],
                }
            )
        modules.append({"id": 7, "modname": "assign", "name": "Pràctica 1"})
        return [{"id": 901, "section": 1, "name": "Tema 1", "modules": modules}]

    def call(self, function: str, params: dict | None = None) -> dict | list:
        """Answer the two functions the sync uses."""
        if self.invalid_token:
            raise InvalidTokenError("invalidtoken", "expired")
        result = {"courses": [{"id": 1072, "assignments": self.assignments}]}
        if function == "core_course_get_contents":
            result = self.sections()
        return result

    def download(self, url: str, destination: Path) -> tuple[int, str]:
        """Write the body of url to destination."""
        if url in self.fail_urls:
            raise requests.ConnectionError("connection reset")
        body = self.bodies[url]
        destination.write_bytes(body)
        self.downloads = self.downloads + 1
        return len(body), hashlib.sha256(body).hexdigest()


class SyncTestCase(unittest.TestCase):
    """Base class: temporary destination, app folder, registry and fake Moodle."""

    def setUp(self) -> None:
        """Create folders, registry and Moodle."""
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.destination = root / "Campus"
        self.destination.mkdir()
        self.registry = Registry(root / "app" / "registry.sqlite3")
        self.moodle = FakeMoodle()
        self.target = SyncTarget(SITE, 42, "2026-27", self.destination)
        self.section_dir = self.destination / "2026-27" / "SO" / "01 - Tema 1"

    def tearDown(self) -> None:
        """Close the registry and delete the folders."""
        self.registry.close()
        self.folder.cleanup()

    def run_sync(self, cancel: threading.Event | None = None, progress: list | None = None):
        """Run one sync of COURSE and return the summary."""
        callback = None if progress is None else progress.append
        syncer = sync.Syncer(self.moodle, self.registry, self.target, callback, cancel)
        return syncer.run([COURSE], MACHINE)

    def files(self) -> list[str]:
        """Return every user-visible file under the destination, as relative paths."""
        return sorted(
            path.relative_to(self.destination).as_posix()
            for path in self.destination.rglob("*")
            if path.is_file() and ".campus-sync" not in path.parts
        )


class FirstSyncTests(SyncTestCase):
    """A first sync into an empty destination."""

    def test_downloads_everything_in_order(self) -> None:
        """Resources, folder files and links land in their folders."""
        summary = self.run_sync()
        self.assertEqual(summary.new, 3)
        self.assertEqual(summary.errors, [])
        self.assertEqual(
            self.files(),
            [
                "2026-27/SO/01 - Tema 1/Docs.url",
                "2026-27/SO/01 - Tema 1/Exemples/extra/a.c",
                "2026-27/SO/01 - Tema 1/guia.pdf",
            ],
        )
        self.assertEqual((self.section_dir / "guia.pdf").read_bytes(), b"guide v1")

    def test_progress_is_reported(self) -> None:
        """The callback gets one report per item."""
        progress = []
        self.run_sync(progress=progress)
        self.assertEqual([report.done for report in progress], [0, 1, 2])
        self.assertEqual(progress[0].total, 3)
        self.assertEqual(progress[0].course_folder, "2026-27/SO")

    def test_existing_file_with_same_size_is_adopted(self) -> None:
        """A file already downloaded by hand is adopted, not duplicated (RF11)."""
        self.section_dir.mkdir(parents=True)
        (self.section_dir / "guia.pdf").write_bytes(b"GUIDE V1")
        summary = self.run_sync()
        self.assertEqual(summary.adopted, 1)
        self.assertEqual((self.section_dir / "guia.pdf").read_bytes(), b"GUIDE V1")
        self.assertNotIn("2026-27/SO/01 - Tema 1/guia (2).pdf", self.files())

    def test_different_existing_file_is_kept(self) -> None:
        """A different file with the same name is never overwritten."""
        self.section_dir.mkdir(parents=True)
        (self.section_dir / "guia.pdf").write_bytes(b"my own notes, longer")
        self.run_sync()
        self.assertEqual((self.section_dir / "guia.pdf").read_bytes(), b"my own notes, longer")
        self.assertEqual((self.section_dir / "guia (2).pdf").read_bytes(), b"guide v1")


class SecondSyncTests(SyncTestCase):
    """Syncs after a first one."""

    def setUp(self) -> None:
        """Run a first sync."""
        super().setUp()
        self.run_sync()
        self.moodle.downloads = 0

    def test_nothing_changed(self) -> None:
        """A second sync downloads nothing."""
        summary = self.run_sync()
        self.assertEqual((summary.new, summary.unchanged), (0, 3))
        self.assertEqual(self.moodle.downloads, 0)

    def test_new_version_is_saved_next_to_old(self) -> None:
        """A changed file becomes 'name (date).ext' and the old one stays (RF7)."""
        self.moodle.add_resource(GUIDE_CMID, "guia.pdf", b"guide version 2", T2)
        summary = self.run_sync()
        self.assertEqual(summary.new_versions, 1)
        self.assertEqual((self.section_dir / "guia.pdf").read_bytes(), b"guide v1")
        self.assertEqual(
            (self.section_dir / "guia (2026-11-06).pdf").read_bytes(), b"guide version 2"
        )

    def test_same_content_new_date_writes_nothing(self) -> None:
        """A new timemodified with identical content adds no file (design §6)."""
        self.moodle.add_resource(GUIDE_CMID, "guia.pdf", b"guide v1", T2)
        summary = self.run_sync()
        self.assertEqual((summary.new_versions, summary.unchanged), (0, 3))
        self.assertNotIn("2026-27/SO/01 - Tema 1/guia (2026-11-06).pdf", self.files())
        self.assertEqual(self.run_sync().unchanged, 3)

    def test_changed_link_creates_new_shortcut(self) -> None:
        """A link whose URL changed gets a new .url version."""
        self.moodle.links[5] = "https://docs.example.org/v2"
        summary = self.run_sync()
        self.assertEqual(summary.new_versions, 1)
        self.assertIn("2026-27/SO/01 - Tema 1/Docs (2026-10-01).url", self.files())

    def test_deleted_locally_is_marked_missing(self) -> None:
        """A file the user deleted is marked missing, not downloaded again."""
        (self.section_dir / "guia.pdf").unlink()
        summary = self.run_sync()
        self.assertEqual(summary.missing_locally, 1)
        self.assertEqual(self.moodle.downloads, 0)
        states = {row["filename"]: row["state"] for row in self.registry.files_of_course(1, 1072)}
        self.assertEqual(states["guia.pdf"], STATE_MISSING)

    def test_removed_in_moodle_is_kept_locally(self) -> None:
        """A file gone from Moodle is marked, never deleted."""
        self.moodle.modules = [
            module for module in self.moodle.modules if module["id"] != GUIDE_CMID
        ]
        summary = self.run_sync()
        self.assertEqual(summary.removed_in_moodle, 1)
        self.assertTrue((self.section_dir / "guia.pdf").exists())
        states = {row["filename"]: row["state"] for row in self.registry.files_of_course(1, 1072)}
        self.assertEqual(states["guia.pdf"], STATE_REMOVED)


class RobustnessTests(SyncTestCase):
    """Errors, cancelling, lock and ownership."""

    def test_one_failing_file_does_not_stop_the_rest(self) -> None:
        """A network error on one file is reported and the others are synced."""
        self.moodle.fail_urls.add(f"{BASE}/77/mod_resource/content/1/guia.pdf")
        summary = self.run_sync()
        self.assertEqual(summary.new, 2)
        self.assertEqual(len(summary.errors), 1)
        self.assertIn("guia.pdf", summary.errors[0])
        self.assertEqual(list((self.destination / sync.downloader.TEMP_DIR_NAME).iterdir()), [])

    def test_invalid_token_stops_cleanly(self) -> None:
        """An expired token stops the run and is reported, and the lock is released."""
        self.moodle.invalid_token = True
        summary = self.run_sync()
        self.assertTrue(summary.invalid_token)
        self.assertEqual(self.files(), [])
        self.assertFalse((self.destination / ".campus-sync" / "sync.lock").exists())

    def test_cancel_stops_between_files(self) -> None:
        """A cancel request before the first file stops the run and skips removal marks."""
        cancel = threading.Event()
        cancel.set()
        summary = self.run_sync(cancel=cancel)
        self.assertTrue(summary.cancelled)
        self.assertEqual(summary.new, 0)

    def test_second_sync_at_once_is_refused(self) -> None:
        """An existing lock file refuses a second sync."""
        with sync.SyncLock(self.destination), self.assertRaises(SyncLockedError):
            self.run_sync()

    def test_other_machine_is_refused_until_take_over(self) -> None:
        """Another owner blocks the sync until this machine takes over (D2)."""
        sync.take_over_ownership(self.destination, "machine-b")
        with self.assertRaises(NotOwnerError):
            self.run_sync()
        self.assertFalse((self.destination / ".campus-sync" / "sync.lock").exists())
        sync.take_over_ownership(self.destination, MACHINE)
        self.assertEqual(self.run_sync().new, 3)

    def test_machine_id_is_stable(self) -> None:
        """The machine id is created once and then reused."""
        app_dir = Path(self.folder.name) / "app"
        first = sync.load_machine_id(app_dir)
        self.assertEqual(sync.load_machine_id(app_dir), first)


if __name__ == "__main__":
    unittest.main()
