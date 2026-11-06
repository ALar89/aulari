"""Unit tests for campus_sync.app with a fake Moodle and an in-memory keyring."""

import base64
import hashlib
import tempfile
import unittest
from pathlib import Path

import keyring
from keyring.backend import KeyringBackend

from campus_sync import auth
from campus_sync.app import AppError, CampusApp
from campus_sync.sync import NotOwnerError, take_over_ownership

SITE = "https://moodle.example.test/2026-27"
TOKEN = "0123456789abcdef0123456789abcdef"
FILE_URL = SITE + "/webservice/pluginfile.php/77/mod_resource/content/1/guia.pdf"
COURSES = [
    {"id": 1072, "fullname": "Sistemes Operatius [MO86824]"},
    {"id": 472, "fullname": "Estructura de Computadors [MO86223]"},
    {"id": 402, "fullname": "VIU la UAB [MO86153]"},
]


class MemoryKeyring(KeyringBackend):
    """Keyring backend that keeps passwords in a dict."""

    priority = 1

    def __init__(self) -> None:
        """Start empty."""
        super().__init__()
        self.store = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        """Save a password."""
        self.store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        """Return a password or None."""
        return self.store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        """Remove a password."""
        del self.store[(service, username)]


class FakeClient:
    """Fake MoodleClient for every function CampusApp uses."""

    enrolled = list(COURSES)

    def __init__(self, site_url: str, token: str) -> None:
        """Remember the token used."""
        self.token = token

    def __enter__(self) -> FakeClient:
        """Support 'with'."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Nothing to close."""

    def call(self, function: str, params: dict | None = None) -> dict | list:
        """Return canned answers."""
        # Module ids are unique within a site in Moodle: derive them from the course.
        course_id = int((params or {}).get("courseid", 0))
        answers = {
            "core_webservice_get_site_info": {"userid": 42, "siteurl": SITE},
            "core_enrol_get_users_courses": FakeClient.enrolled,
            "core_course_get_contents": [
                {
                    "id": course_id * 10,
                    "section": 0,
                    "name": "General",
                    "modules": [
                        {
                            "id": course_id * 10 + 1,
                            "modname": "resource",
                            "name": "Guia",
                            "contents": [
                                {
                                    "type": "file",
                                    "filename": "guia.pdf",
                                    "filepath": "/",
                                    "filesize": 5,
                                    "timemodified": 1790000000,
                                    "fileurl": FILE_URL,
                                }
                            ],
                        }
                    ],
                }
            ],
            "mod_assign_get_assignments": {"courses": []},
            "core_calendar_get_calendar_events": {
                "events": [{"id": 9, "name": "Entrega", "courseid": 1072, "timestart": 1790000000}]
            },
        }
        return answers[function]

    def download(self, url: str, destination: Path) -> tuple[int, str]:
        """Write five bytes."""
        destination.write_bytes(b"hello")
        return 5, hashlib.sha256(b"hello").hexdigest()


def redirect_for(passport: str) -> str:
    """Return the moodlemobile:// redirect launch.php would give for this passport."""
    signature = hashlib.md5((SITE + passport).encode("utf-8")).hexdigest()
    payload = base64.b64encode(f"{signature}:::{TOKEN}".encode()).decode("ascii")
    return f"moodlemobile://token={payload}"


class AppTestCase(unittest.TestCase):
    """Base class: temporary folders, in-memory keyring and a CampusApp."""

    def setUp(self) -> None:
        """Create the app with the fake client."""
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.previous_keyring = keyring.get_keyring()
        keyring.set_keyring(MemoryKeyring())
        FakeClient.enrolled = list(COURSES)
        self.app = CampusApp(root / "app", client_factory=FakeClient)
        self.app.set_site_url(SITE)
        self.destination = root / "Campus"

    def tearDown(self) -> None:
        """Restore the keyring and delete the folders."""
        keyring.set_keyring(self.previous_keyring)
        self.folder.cleanup()

    def log_in(self) -> None:
        """Run the whole login flow."""
        _, passport = self.app.start_login()
        self.app.finish_login(redirect_for(passport), passport)


class LoginTests(AppTestCase):
    """Login, logout and site changes."""

    def test_login_and_logout(self) -> None:
        """Login stores the user and token; logout removes the token."""
        self.assertFalse(self.app.is_logged_in())
        launch_url, _ = self.app.start_login()
        self.assertTrue(launch_url.startswith(SITE + "/admin/tool/mobile/launch.php?"))
        self.log_in()
        self.assertTrue(self.app.is_logged_in())
        self.assertEqual(CampusApp(self.app.app_dir).settings.user_id, 42)
        self.app.logout()
        self.assertFalse(self.app.is_logged_in())
        self.assertIsNone(auth.load_token(SITE, 42))

    def test_changing_site_forgets_user_and_courses(self) -> None:
        """A new site URL needs a new login and a new selection."""
        self.log_in()
        self.app.set_selection([1072])
        self.app.set_site_url("https://moodle.example.test/2027-28/")
        self.assertEqual(self.app.settings.site_url, "https://moodle.example.test/2027-28")
        self.assertEqual((self.app.settings.user_id, self.app.settings.selected_courses), (0, []))


class CourseListTests(AppTestCase):
    """Course selection rules (RF3)."""

    def test_first_time_selects_nothing(self) -> None:
        """The first list has nothing selected and nothing marked new (user decides)."""
        self.log_in()
        choices = self.app.list_courses()
        self.assertEqual(len(choices), len(COURSES))
        self.assertFalse(any(choice.selected or choice.is_new for choice in choices))
        self.assertEqual(self.app.settings.selected_courses, [])
        self.assertEqual(self.app.settings.known_courses, [402, 472, 1072])

    def test_new_course_is_proposed_not_selected(self) -> None:
        """A course that appears later is marked new and left unselected."""
        self.log_in()
        self.app.list_courses()
        self.app.set_selection([1072])
        FakeClient.enrolled = [*COURSES, {"id": 999, "fullname": "Xarxes [MO1]"}]
        choices = {choice.course_id: choice for choice in self.app.list_courses()}
        self.assertTrue(choices[999].is_new)
        self.assertFalse(choices[999].selected)
        self.assertTrue(choices[1072].selected)
        self.assertFalse(choices[472].selected)

    def test_not_logged_in(self) -> None:
        """Without a token the app asks to log in again."""
        with self.assertRaises(AppError) as context:
            self.app.list_courses()
        self.assertEqual(context.exception.key, "error_invalid_token")


class SyncTests(AppTestCase):
    """run_sync() and take_over()."""

    def setUp(self) -> None:
        """Log in and get the course list."""
        super().setUp()
        self.log_in()
        self.choices = self.app.list_courses()
        self.app.set_selection([472, 1072])

    def test_destination_is_required(self) -> None:
        """Without a destination folder the sync does not start."""
        with self.assertRaises(AppError) as context:
            self.app.run_sync(self.choices)
        self.assertEqual(context.exception.key, "error_no_destination")

    def test_sync_then_calendar(self) -> None:
        """Selected courses are synced and the calendar is exported afterwards."""
        self.app.set_destination(self.destination)
        result = self.app.run_sync(self.choices)
        self.assertEqual(result.summary.new, 2)  # one file in each selected course
        self.assertEqual(result.calendar_error, "")
        self.assertEqual(result.calendar_events, 1)
        self.assertEqual(result.calendar_path, self.destination / "2026-27" / "calendari.ics")
        self.assertTrue(
            (self.destination / "2026-27" / "SO" / "00 - General" / "guia.pdf").exists()
        )
        self.assertFalse((self.destination / "2026-27" / "VIUUAB").exists())

    def test_other_owner_then_take_over(self) -> None:
        """Another computer's folder is refused until take_over()."""
        self.app.set_destination(self.destination)
        take_over_ownership(self.destination, "another-machine")
        with self.assertRaises(NotOwnerError):
            self.app.run_sync(self.choices)
        self.app.take_over()
        self.assertEqual(self.app.run_sync(self.choices).summary.new, 2)


class AliasTests(AppTestCase):
    """course_aliases() and rename_course() (RF4)."""

    def setUp(self) -> None:
        """Log in, select two courses and sync them once."""
        super().setUp()
        self.log_in()
        self.app.list_courses()
        self.app.set_selection([472, 1072])
        self.app.set_destination(self.destination)
        self.app.run_sync(self.app.list_courses())
        self.year_dir = self.destination / "2026-27"

    def test_aliases_after_sync(self) -> None:
        """Synced courses have aliases; the others do not."""
        self.assertEqual(self.app.course_aliases(), {1072: "SO", 472: "EC"})
        choices = {choice.course_id: choice for choice in self.app.list_courses()}
        self.assertEqual((choices[1072].alias, choices[402].alias), ("SO", ""))

    def test_rename_moves_folder_and_keeps_sync_working(self) -> None:
        """The folder moves, the registry follows, and the next sync finds everything."""
        self.assertEqual(self.app.rename_course(1072, " SistOp "), "SistOp")
        self.assertFalse((self.year_dir / "SO").exists())
        self.assertTrue((self.year_dir / "SistOp" / "00 - General" / "guia.pdf").exists())
        self.assertEqual(self.app.course_aliases()[1072], "SistOp")
        summary = self.app.run_sync(self.app.list_courses()).summary
        self.assertEqual((summary.new, summary.missing_locally), (0, 0))

    def test_case_only_rename(self) -> None:
        """'SO' -> 'So' works although Windows sees it as the same folder."""
        self.app.rename_course(1072, "So")
        self.assertEqual(self.app.course_aliases()[1072], "So")
        self.assertIn("So", [path.name for path in self.year_dir.iterdir()])

    def test_rename_errors(self) -> None:
        """Taken, invalid and never-synced aliases are refused with a text key."""
        cases = [(1072, "EC", "error_alias_taken"), (1072, "..", "error_alias_invalid")]
        cases.append((402, "VIU", "error_alias_not_synced"))
        for course_id, alias, key in cases:
            with self.assertRaises(AppError, msg=alias) as context:
                self.app.rename_course(course_id, alias)
            self.assertEqual(context.exception.key, key)
        self.assertTrue((self.year_dir / "SO").exists())

    def test_existing_folder_with_that_name_is_refused(self) -> None:
        """A folder the user created with the new name is never overwritten."""
        (self.year_dir / "Mine").mkdir()
        with self.assertRaises(AppError) as context:
            self.app.rename_course(1072, "Mine")
        self.assertEqual(context.exception.key, "error_alias_taken")
        self.assertTrue((self.year_dir / "SO").exists())


if __name__ == "__main__":
    unittest.main()
