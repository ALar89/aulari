"""Unit tests for aulari.downloader, using temporary folders and a fake client."""

import hashlib
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from aulari import downloader
from aulari.downloader import DownloadError
from aulari.moodle_items import KIND_FILE, KIND_URL, SectionInfo, SyncItem
from aulari.registry import FileKey

ITEM_SIZE = 5  # bytes Moodle reports for every test item
SECTION = SectionInfo(section_id=901, number=1, name="Tema 1")
NOV_6 = int(datetime(2026, 11, 6, 17, 5, tzinfo=UTC).timestamp())  # 18:05 in Madrid


def make_item(
    filename: str = "a.pdf",
    filepath: str = "/",
    subfolder: str = "",
    kind: str = KIND_FILE,
    url: str = "https://x.test/webservice/pluginfile.php/1/mod_resource/content/1/a.pdf",
) -> SyncItem:
    """Return a SyncItem with sensible defaults."""
    key = FileKey(cmid=10, filearea="content", filepath=filepath, filename=filename)
    return SyncItem(key, SECTION, kind, subfolder, url, ITEM_SIZE, NOV_6)


class FakeClient:
    """Stand-in for MoodleClient.download() that writes canned bytes."""

    def __init__(self, body: bytes) -> None:
        """Store the bytes every download will write."""
        self.body = body

    def download(self, url: str, destination: Path) -> tuple[int, str]:
        """Write the body to destination and return (size, SHA-256)."""
        destination.write_bytes(self.body)
        return len(self.body), hashlib.sha256(self.body).hexdigest()


class FolderTestCase(unittest.TestCase):
    """Base class with a temporary destination folder."""

    def setUp(self) -> None:
        """Create the destination."""
        self.folder = tempfile.TemporaryDirectory()
        self.destination = Path(self.folder.name) / "dest"
        self.destination.mkdir()

    def tearDown(self) -> None:
        """Delete the destination."""
        self.folder.cleanup()

    def temp_files(self) -> list[Path]:
        """Return the files left in the temporary download folder."""
        temp_dir = self.destination / downloader.TEMP_DIR_NAME
        return list(temp_dir.iterdir()) if temp_dir.is_dir() else []


class PathTests(FolderTestCase):
    """Tests for item_relative_dir(), item_filename() and plan_target()."""

    def test_relative_dir_with_subfolder_and_filepath(self) -> None:
        """Subfolder and filepath parts are sanitized one by one."""
        item = make_item(subfolder="Exemples: C?", filepath="/extra/x*y/")
        result = downloader.item_relative_dir("2026-27/SO", "01 - Tema 1", item)
        self.assertEqual(result, PurePosixPath("2026-27/SO/01 - Tema 1/Exemples_ C_/extra/x_y"))

    def test_dotdot_part_is_rejected(self) -> None:
        """A '..' filepath part has no usable characters and raises."""
        item = make_item(filepath="/../")
        with self.assertRaises(ValueError):
            downloader.item_relative_dir("2026-27/SO", "01 - Tema 1", item)

    def test_link_filename(self) -> None:
        """Links get a .url suffix."""
        self.assertEqual(downloader.item_filename(make_item("Docs", kind=KIND_URL)), "Docs.url")

    def test_target_is_inside_and_short(self) -> None:
        """Long names are shortened so that the full path fits the limit."""
        target = downloader.plan_target(
            self.destination, PurePosixPath("SO/01"), "n" * 300 + ".pdf"
        )
        self.assertTrue(target.is_relative_to(self.destination.resolve()))
        self.assertLessEqual(len(str(target)), downloader.MAX_PATH_LENGTH)
        self.assertTrue(target.name.endswith(".pdf"))

    def test_too_long_folder_is_rejected(self) -> None:
        """If the folder alone is too long, there is no room for a file name."""
        with self.assertRaises(ValueError):
            downloader.plan_target(self.destination, PurePosixPath("x" * 250), "a.pdf")


class FreeNameTests(FolderTestCase):
    """Tests for free_unique_name() and free_versioned_name()."""

    def test_unique_name(self) -> None:
        """A taken name gets ' (2)'."""
        (self.destination / "a.pdf").write_bytes(b"x")
        result = downloader.free_unique_name(self.destination, "a.pdf")
        self.assertEqual(result, "a (2).pdf")

    def test_versioned_name_progression(self) -> None:
        """Date first, then date and time, then a counter (RF7, Madrid time)."""
        first = downloader.free_versioned_name(self.destination, "a.pdf", NOV_6)
        self.assertEqual(first, "a (2026-11-06).pdf")
        (self.destination / first).write_bytes(b"x")
        second = downloader.free_versioned_name(self.destination, "a.pdf", NOV_6)
        self.assertEqual(second, "a (2026-11-06 1805).pdf")
        (self.destination / second).write_bytes(b"x")
        third = downloader.free_versioned_name(self.destination, "a.pdf", NOV_6)
        self.assertEqual(third, "a (2026-11-06 1805) (2).pdf")

    def test_styled_names_are_checked_on_disk(self) -> None:
        """With a space style the disk holds 'Tema_1...', but the original form is returned."""
        (self.destination / "Tema_1.pdf").write_bytes(b"x")
        result = downloader.free_unique_name(self.destination, "Tema 1.pdf", "_")
        self.assertEqual(result, "Tema 1 (2).pdf")
        (self.destination / "Tema_1_(2026-11-06).pdf").write_bytes(b"x")
        version = downloader.free_versioned_name(self.destination, "Tema 1.pdf", NOV_6, "_")
        self.assertEqual(version, "Tema 1 (2026-11-06 1805).pdf")

    def test_adoptable(self) -> None:
        """A file is adoptable only if it exists with the same size (RF11)."""
        path = self.destination / "a.pdf"
        self.assertFalse(downloader.adoptable(path, 3))
        path.write_bytes(b"abc")
        self.assertTrue(downloader.adoptable(path, 3))
        self.assertFalse(downloader.adoptable(path, 4))


class AtomicDownloadTests(FolderTestCase):
    """Tests for download_atomic() and clean_temp_dir()."""

    def test_successful_download(self) -> None:
        """The file reaches its target and the temporary folder is left empty."""
        target = self.destination / "SO" / "01" / "a.pdf"
        size, sha256, moved = downloader.download_atomic(
            FakeClient(b"hello"), make_item(), self.destination, target
        )
        self.assertEqual(target.read_bytes(), b"hello")
        self.assertEqual((size, sha256, moved), (5, hashlib.sha256(b"hello").hexdigest(), True))
        self.assertEqual(self.temp_files(), [])

    def test_same_content_is_not_moved(self) -> None:
        """If the SHA-256 equals the previous version, nothing is written (design §6)."""
        target = self.destination / "a (2026-11-06).pdf"
        previous = hashlib.sha256(b"hello").hexdigest()
        result = downloader.download_atomic(
            FakeClient(b"hello"), make_item(), self.destination, target, skip_if_sha256=previous
        )
        self.assertEqual(result, (5, previous, False))
        self.assertFalse(target.exists())
        self.assertEqual(self.temp_files(), [])

    def test_size_mismatch(self) -> None:
        """A wrong size raises DownloadError and leaves neither target nor temp file."""
        target = self.destination / "a.pdf"
        with self.assertRaises(DownloadError):
            downloader.download_atomic(FakeClient(b"hi"), make_item(), self.destination, target)
        self.assertFalse(target.exists())
        self.assertEqual(self.temp_files(), [])

    def test_existing_target_is_never_overwritten(self) -> None:
        """If the target exists, the download fails and the old file is intact."""
        target = self.destination / "a.pdf"
        target.write_bytes(b"mine!")
        with self.assertRaises(FileExistsError):
            downloader.download_atomic(FakeClient(b"hello"), make_item(), self.destination, target)
        self.assertEqual(target.read_bytes(), b"mine!")
        self.assertEqual(self.temp_files(), [])

    def test_clean_temp_dir(self) -> None:
        """Leftover .part files from an interrupted sync are removed."""
        temp_dir = self.destination / downloader.TEMP_DIR_NAME
        temp_dir.mkdir()
        (temp_dir / "abc.part").write_bytes(b"x")
        self.assertEqual(downloader.clean_temp_dir(self.destination), 1)
        self.assertEqual(self.temp_files(), [])


class UrlShortcutTests(FolderTestCase):
    """Tests for write_url_shortcut()."""

    def test_exact_content(self) -> None:
        """The .url file has the exact format, CRLF and ASCII (non-ASCII encoded)."""
        target = self.destination / "Docs.url"
        downloader.write_url_shortcut(
            make_item(kind=KIND_URL, url="https://x.org/càlcul?a=1"), target
        )
        self.assertEqual(
            target.read_bytes(), b"[InternetShortcut]\r\nURL=https://x.org/c%C3%A0lcul?a=1\r\n"
        )

    def test_unsafe_url_is_not_written(self) -> None:
        """javascript: and file: links raise and create no file."""
        target = self.destination / "bad.url"
        for url in ["javascript:alert(1)", "file:///C:/Windows/system32/calc.exe"]:
            with self.assertRaises(ValueError, msg=url):
                downloader.write_url_shortcut(make_item(kind=KIND_URL, url=url), target)
        self.assertFalse(target.exists())


@unittest.skipUnless(sys.platform == "win32", "Mark-of-the-Web is Windows only")
class MarkOfTheWebTests(FolderTestCase):
    """Tests for mark_downloaded_from_internet()."""

    def test_zone_identifier_is_written(self) -> None:
        """The Zone.Identifier stream says ZoneId=3 (Internet)."""
        path = self.destination / "a.pdf"
        path.write_bytes(b"x")
        downloader.mark_downloaded_from_internet(path)
        try:
            with open(f"{path}:Zone.Identifier", encoding="ascii") as stream:
                content = stream.read()
        except OSError:
            self.skipTest("File system without alternate data streams")
        self.assertIn("ZoneId=3", content)
        self.assertEqual(path.read_bytes(), b"x")
        self.assertEqual(os.path.getsize(path), 1)


if __name__ == "__main__":
    unittest.main()
