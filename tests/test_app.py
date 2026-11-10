"""Unit tests for aulari.app with a fake Moodle and an in-memory keyring."""

import base64
import hashlib
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import keyring
from keyring.backend import KeyringBackend

from aulari import auth
from aulari.app import AppError, CampusApp
from aulari.sync import NotOwnerError, take_over_ownership

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
    filename = "guia.pdf"
    timemodified = 1790000000
    content = b"hello"
    discussions = []  # announcements in the news forum of course 1072

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
                                    "filename": FakeClient.filename,
                                    "filepath": "/",
                                    "filesize": 5,
                                    "timemodified": FakeClient.timemodified,
                                    "fileurl": FILE_URL,
                                }
                            ],
                        }
                    ],
                }
            ],
            "mod_assign_get_assignments": {
                "courses": [{"id": 1072, "assignments": [{"id": 6118, "name": "P1", "duedate": 9}]}]
            },
            "mod_assign_get_submission_status": {
                "lastattempt": {"submission": {"status": "submitted"}},
                "feedback": {
                    "gradefordisplay": "8,50&nbsp;/&nbsp;10,00",
                    "plugins": [{"type": "comments"}],
                },
            },
            "mod_forum_get_forums_by_courses": [
                {"id": 3003, "course": 1072, "type": "news"},
                {"id": 3004, "course": 1072, "type": "general"},
            ],
            "mod_forum_get_forum_discussions": {"discussions": FakeClient.discussions},
            "gradereport_overview_get_course_grades": {
                "grades": [{"courseid": 1072, "grade": "7,25"}, {"courseid": 472, "grade": "-"}]
            },
            "gradereport_user_get_grade_items": {
                "usergrades": [
                    {
                        "gradeitems": [
                            {
                                "itemname": "P1",
                                "gradeformatted": "8,50",
                                "rangeformatted": "0–10",
                                "percentageformatted": "85,00 %",
                            },
                            {"itemname": None, "itemtype": "course", "gradeformatted": ""},
                        ]
                    }
                ]
            },
            "core_calendar_get_calendar_events": {
                "events": [{"id": 9, "name": "Entrega", "courseid": 1072, "timestart": 1790000000}]
            },
        }
        return answers[function]

    def download(self, url: str, destination: Path) -> tuple[int, str]:
        """Write five bytes."""
        destination.write_bytes(FakeClient.content)
        return len(FakeClient.content), hashlib.sha256(FakeClient.content).hexdigest()


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
        FakeClient.filename = "guia.pdf"
        FakeClient.timemodified = 1790000000
        FakeClient.content = b"hello"
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

    def test_news_file_after_sync(self) -> None:
        """A sync with new files writes Novedades.txt and reports its path."""
        self.app.set_destination(self.destination)
        result = self.app.run_sync(self.choices)
        self.assertEqual(result.news_path, self.destination / "2026-27" / "Novedades.txt")
        self.assertIn("[SO] 00 - General/guia.pdf", result.news_path.read_text(encoding="utf-8"))
        self.assertIsNone(self.app.run_sync(self.choices).news_path)

    def test_upcoming_deadlines(self) -> None:
        """Only future events of the selected courses, with their alias."""
        now = datetime.fromtimestamp(1789000000, UTC)
        deadlines = self.app.upcoming_deadlines(self.app.list_courses(), now)
        self.assertEqual(len(deadlines), 1)
        self.assertEqual((deadlines[0].course_alias, deadlines[0].name), ("SO", "Entrega"))
        later = datetime.fromtimestamp(1790000001, UTC)
        self.assertEqual(self.app.upcoming_deadlines(self.app.list_courses(), later), [])

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

    def test_course_folder_and_news_file(self) -> None:
        """Synced courses have a folder on disk; Novedades.txt exists after the sync."""
        choices = {choice.course_id: choice for choice in self.app.list_courses()}
        self.assertEqual(self.app.course_folder(choices[1072]), self.year_dir / "SO")
        self.assertIsNone(self.app.course_folder(choices[402]))
        self.assertEqual(self.app.news_file(), self.year_dir / "Novedades.txt")

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


class YearFolderTests(AppTestCase):
    """rename_year_folder(): the academic year folder gets the user's name (RF20)."""

    def setUp(self) -> None:
        """Log in, select two courses and choose the destination."""
        super().setUp()
        self.log_in()
        self.app.list_courses()
        self.app.set_selection([472, 1072])
        self.app.set_destination(self.destination)

    def test_name_chosen_before_the_first_sync(self) -> None:
        """An existing folder of the user with that name is used, and its content kept."""
        notes = self.destination / "2n Curs" / "SO" / "Apunts"
        notes.mkdir(parents=True)
        self.assertEqual(self.app.rename_year_folder(" 2n Curs "), "2n Curs")
        self.assertEqual(CampusApp(self.app.app_dir).settings.year_folder, "2n Curs")
        result = self.app.run_sync(self.app.list_courses())
        year_dir = self.destination / "2n Curs"
        self.assertTrue((year_dir / "SO" / "00 - General" / "guia.pdf").exists())
        self.assertTrue(notes.is_dir())
        self.assertEqual(result.calendar_path, year_dir / "calendari.ics")
        self.assertEqual(result.news_path, year_dir / "Novedades.txt")
        # The calendar keeps the academic year in its UIDs: re-importing does not duplicate.
        self.assertIn("@2026-27.", result.calendar_path.read_text(encoding="utf-8"))
        self.assertFalse((self.destination / "2026-27").exists())

    def test_rename_after_sync_and_back(self) -> None:
        """The folder moves with everything inside; an empty name restores the year."""
        self.app.run_sync(self.app.list_courses())
        (self.destination / "2026-27" / "Mine.txt").write_text("x", encoding="utf-8")
        self.app.rename_year_folder("2n Curs")
        year_dir = self.destination / "2n Curs"
        self.assertTrue((year_dir / "SO" / "00 - General" / "guia.pdf").exists())
        self.assertTrue((year_dir / "Mine.txt").exists())
        choices = {choice.course_id: choice for choice in self.app.list_courses()}
        self.assertEqual(self.app.course_folder(choices[1072]), year_dir / "SO")
        self.assertEqual(self.app.news_file(), year_dir / "Novedades.txt")
        summary = self.app.run_sync(self.app.list_courses()).summary
        self.assertEqual((summary.new, summary.missing_locally), (0, 0))
        self.assertEqual(self.app.rename_year_folder(""), "2026-27")
        self.assertEqual(self.app.settings.year_folder, "")
        self.assertTrue((self.destination / "2026-27" / "SO" / "00 - General").is_dir())

    def test_errors(self) -> None:
        """Invalid names, another year's folder and an existing folder are refused."""
        self.app.run_sync(self.app.list_courses())
        self.app.add_site("https://moodle.example.test/2025-26")
        self.app.switch_site(SITE)
        (self.destination / "Mine").mkdir()
        cases = [("..", "error_folder_invalid"), ("2025-26", "error_folder_taken")]
        cases.append(("Mine", "error_folder_taken"))
        for name, key in cases:
            with self.assertRaises(AppError, msg=name) as context:
                self.app.rename_year_folder(name)
            self.assertEqual(context.exception.key, key)
        self.assertTrue((self.destination / "2026-27" / "SO").is_dir())

    def test_rename_after_logout(self) -> None:
        """The registry follows even if the session was closed in between."""
        self.app.run_sync(self.app.list_courses())
        self.app.logout()
        self.app.rename_year_folder("2n Curs")
        self.app.set_campus_subfolder("Campus")
        self.log_in()
        summary = self.app.run_sync(self.app.list_courses()).summary
        self.assertEqual((summary.new, summary.missing_locally), (0, 0))
        guia = self.destination / "2n Curs" / "SO" / "Campus" / "00 - General" / "guia.pdf"
        self.assertTrue(guia.exists())

    def test_name_follows_the_site(self) -> None:
        """Each academic year keeps its own folder name when switching."""
        self.app.rename_year_folder("2n Curs")
        self.app.add_site("https://moodle.example.test/2025-26")
        self.assertEqual(self.app.year_folder(), "2025-26")
        self.app.rename_year_folder("1r Curs")
        self.app.switch_site(SITE)
        self.assertEqual(self.app.year_folder(), "2n Curs")


class SubfolderTests(AppTestCase):
    """set_campus_subfolder(): campus material in a subfolder of each course (RF20)."""

    def setUp(self) -> None:
        """Log in, select two courses and choose the destination."""
        super().setUp()
        self.log_in()
        self.app.list_courses()
        self.app.set_selection([472, 1072])
        self.app.set_destination(self.destination)
        self.course_dir = self.destination / "2026-27" / "SO"

    def test_chosen_before_the_first_sync(self) -> None:
        """New courses go straight into the subfolder."""
        self.assertEqual(self.app.set_campus_subfolder(" Campus "), "Campus")
        self.app.run_sync(self.app.list_courses())
        self.assertTrue((self.course_dir / "Campus" / "00 - General" / "guia.pdf").exists())
        self.assertEqual(self.app.course_aliases(), {1072: "SO", 472: "EC"})
        choices = {choice.course_id: choice for choice in self.app.list_courses()}
        self.assertEqual(self.app.course_folder(choices[1072]), self.course_dir)

    def test_turned_on_and_off_after_sync(self) -> None:
        """Only Aulari's section folders move; the user's folders stay; sync goes on."""
        self.app.run_sync(self.app.list_courses())
        (self.course_dir / "Apunts").mkdir()
        self.app.set_campus_subfolder("Campus")
        self.assertTrue((self.course_dir / "Campus" / "00 - General" / "guia.pdf").exists())
        self.assertFalse((self.course_dir / "00 - General").exists())
        self.assertTrue((self.course_dir / "Apunts").is_dir())
        summary = self.app.run_sync(self.app.list_courses()).summary
        self.assertEqual((summary.new, summary.missing_locally), (0, 0))
        self.app.set_campus_subfolder("")
        self.assertTrue((self.course_dir / "00 - General" / "guia.pdf").exists())
        self.assertFalse((self.course_dir / "Campus").exists())  # left empty, so removed
        self.assertEqual(self.app.settings.campus_subfolder, "")

    def test_renamed_and_moved_with_the_alias(self) -> None:
        """'Campus' -> 'Material' keeps a non-empty old folder; an alias moves it all."""
        self.app.set_campus_subfolder("Campus")
        self.app.run_sync(self.app.list_courses())
        (self.course_dir / "Campus" / "mine.txt").write_text("x", encoding="utf-8")
        self.app.set_campus_subfolder("Material")
        self.assertTrue((self.course_dir / "Material" / "00 - General" / "guia.pdf").exists())
        self.assertTrue((self.course_dir / "Campus" / "mine.txt").exists())
        self.app.rename_course(1072, "SistOp")
        moved = self.destination / "2026-27" / "SistOp"
        self.assertTrue((moved / "Material" / "00 - General" / "guia.pdf").exists())
        summary = self.app.run_sync(self.app.list_courses()).summary
        self.assertEqual((summary.new, summary.missing_locally), (0, 0))

    def test_conflicts_move_nothing(self) -> None:
        """A section with that name, or a folder already there, stops the whole change."""
        self.app.run_sync(self.app.list_courses())
        (self.destination / "2026-27" / "EC" / "Campus" / "00 - General").mkdir(parents=True)
        for name in ("00 - general", "Campus"):
            with self.assertRaises(AppError, msg=name) as context:
                self.app.set_campus_subfolder(name)
            self.assertEqual(context.exception.key, "error_folder_taken")
        self.assertTrue((self.course_dir / "00 - General" / "guia.pdf").exists())
        self.assertFalse((self.course_dir / "Campus").exists())
        self.assertEqual(self.app.settings.campus_subfolder, "")


class SpaceStyleTests(AppTestCase):
    """set_space_style(): names without spaces, also for what was downloaded (RF21)."""

    def setUp(self) -> None:
        """Log in, select one course with a file name that has spaces and an underscore."""
        super().setUp()
        FakeClient.filename = "P1_enunciat v2.pdf"
        self.log_in()
        self.app.list_courses()
        self.app.set_selection([1072])
        self.app.set_destination(self.destination)
        self.course_dir = self.destination / "2026-27" / "SO"

    def sync(self) -> tuple[int, int]:
        """Sync and return (new files, files missing locally)."""
        summary = self.app.run_sync(self.app.list_courses()).summary
        return summary.new, summary.missing_locally

    def test_chosen_before_the_first_sync(self) -> None:
        """New sections and files get the style; names the user typed keep their spaces."""
        self.app.rename_year_folder("2n Curs")
        self.app.set_campus_subfolder("Material campus")
        self.app.set_space_style("_")
        self.sync()
        course = self.destination / "2n Curs" / "SO" / "Material campus"
        self.assertTrue((course / "00_General" / "P1_enunciat_v2.pdf").exists())
        self.assertEqual(CampusApp(self.app.app_dir).settings.space_style, "_")

    def test_changed_after_sync_and_back(self) -> None:
        """Everything Aulari wrote is renamed from the original names, losslessly."""
        self.sync()
        (self.course_dir / "Apunts de classe").mkdir()
        self.app.set_space_style("-")
        self.assertTrue((self.course_dir / "00-General" / "P1_enunciat-v2.pdf").exists())
        self.assertFalse((self.course_dir / "00 - General").exists())
        self.assertTrue((self.course_dir / "Apunts de classe").is_dir())
        self.assertEqual(self.sync(), (0, 0))
        self.app.set_space_style("_")
        self.assertTrue((self.course_dir / "00_General" / "P1_enunciat_v2.pdf").exists())
        self.app.set_space_style("")
        self.assertTrue((self.course_dir / "00 - General" / "P1_enunciat v2.pdf").exists())
        self.assertEqual(self.sync(), (0, 0))

    def test_new_version_keeps_the_style(self) -> None:
        """A new version gets a styled name next to the renamed previous one (RF7)."""
        self.sync()
        self.app.set_space_style("_")
        FakeClient.timemodified = 1790100000
        FakeClient.content = b"HELLO"
        summary = self.app.run_sync(self.app.list_courses()).summary
        self.assertEqual(summary.new_versions, 1)
        styled = sorted(path.name for path in (self.course_dir / "00_General").iterdir())
        self.assertEqual(styled, ["P1_enunciat_v2.pdf", "P1_enunciat_v2_(2026-09-22).pdf"])
        self.app.set_space_style("")
        plain = sorted(path.name for path in (self.course_dir / "00 - General").iterdir())
        self.assertEqual(plain, ["P1_enunciat v2 (2026-09-22).pdf", "P1_enunciat v2.pdf"])

    def test_taken_name_changes_nothing(self) -> None:
        """If a styled name already exists, nothing is renamed and the style stays."""
        self.sync()
        (self.course_dir / "00_General").mkdir()
        with self.assertRaises(AppError) as context:
            self.app.set_space_style("_")
        self.assertEqual(context.exception.key, "error_names_taken")
        self.assertTrue((self.course_dir / "00 - General" / "P1_enunciat v2.pdf").exists())
        self.assertEqual(self.app.settings.space_style, "")
        self.assertEqual(self.sync(), (0, 0))

    def test_rollback_of_a_partial_rename(self) -> None:
        """A clash found halfway (file level) moves the renamed folders back."""
        self.sync()
        (self.course_dir / "00 - General" / "P1_enunciat_v2.pdf").write_bytes(b"mine")
        with self.assertRaises(AppError):
            self.app.set_space_style("_")
        self.assertTrue((self.course_dir / "00 - General" / "P1_enunciat v2.pdf").exists())
        self.assertFalse((self.course_dir / "00_General").exists())

    def test_unknown_style(self) -> None:
        """Only '', '_' and '-' are accepted."""
        with self.assertRaises(ValueError):
            self.app.set_space_style(".")


def discussion(discussion_id: int, subject: str, when: int) -> dict:
    """Return a news forum discussion as Moodle gives it."""
    return {
        "id": discussion_id * 10,
        "discussion": discussion_id,
        "subject": subject,
        "message": f"<p>{subject} &amp; more</p>",
        "timemodified": when,
    }


class ExtrasTests(AppTestCase):
    """Announcements (RF16), submissions (RF17) and grades (RF18)."""

    def setUp(self) -> None:
        """Log in, select SO and set a destination."""
        super().setUp()
        FakeClient.discussions = [discussion(1, "Benvinguda", 100)]
        self.log_in()
        self.choices = self.app.list_courses()
        self.app.set_selection([1072])
        self.app.set_destination(self.destination)

    def test_first_sync_marks_existing_announcements_as_seen(self) -> None:
        """Old announcements are not reported; a later one is, only once."""
        self.assertEqual(self.app.run_sync(self.choices).announcements, [])
        FakeClient.discussions = [discussion(1, "Benvinguda", 100), discussion(2, "Examen", 200)]
        result = self.app.run_sync(self.choices)
        self.assertEqual(
            [(a.course_alias, a.subject) for a in result.announcements], [("SO", "Examen")]
        )
        self.assertEqual(result.announcements[0].message, "Examen & more")
        self.assertEqual(self.app.run_sync(self.choices).announcements, [])

    def test_submissions(self) -> None:
        """Each assignment of the selected courses has status, grade and comments flag."""
        rows = self.app.submissions(self.choices)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(
            (row.course_alias, row.name, row.status, row.grade),
            ("SO", "P1", "submitted", "8,50 / 10,00"),
        )
        self.assertTrue(row.has_comments)

    def test_grades(self) -> None:
        """Course grades use course names; empty grades become a dash."""
        grades = {grade.course_id: grade for grade in self.app.course_grades(self.choices)}
        self.assertEqual(grades[1072].grade, "7,25")
        self.assertEqual(grades[1072].course_name, "Sistemes Operatius [MO86824]")
        self.assertEqual(grades[472].grade, "-")
        items = self.app.grade_items(1072)
        self.assertEqual(
            (items[0].name, items[0].grade, items[0].percentage), ("P1", "8,50", "85,00 %")
        )
        self.assertEqual((items[1].name, items[1].grade), ("course", "—"))


class SitesTests(AppTestCase):
    """Several academic year sites (RF19)."""

    def setUp(self) -> None:
        """Log in to 2026-27 and select a course."""
        super().setUp()
        self.log_in()
        self.app.list_courses()
        self.app.set_selection([1072])

    def test_add_switch_and_back(self) -> None:
        """Each site keeps its own user and selection when switching."""
        old = "https://moodle.example.test/2025-26"
        self.assertEqual(self.app.suggested_site_url(), old)
        self.app.add_site(old)
        self.assertEqual(self.app.settings.site_url, old)
        self.assertFalse(self.app.is_logged_in())
        self.assertEqual(self.app.sites(), [old, SITE])
        self.assertEqual(self.app.previous_site(), SITE)
        self.app.set_selection([5])
        self.app.switch_site(SITE)
        self.assertEqual(
            (self.app.settings.user_id, self.app.settings.selected_courses), (42, [1072])
        )
        self.assertTrue(self.app.is_logged_in())
        reopened = CampusApp(self.app.app_dir, client_factory=FakeClient)
        reopened.switch_site(old)
        self.assertEqual(reopened.settings.selected_courses, [5])

    def test_errors(self) -> None:
        """Duplicates, unknown sites and URLs without a year are refused."""
        cases = [
            (self.app.add_site, SITE + "/", "error_site_exists"),
            (self.app.add_site, "https://moodle.example.test", "error_no_year"),
            (self.app.switch_site, "https://other.test/2020-21", "error_site_unknown"),
        ]
        for action, url, key in cases:
            with self.assertRaises(AppError, msg=url) as context:
                action(url)
            self.assertEqual(context.exception.key, key)

    def test_remove_sites(self) -> None:
        """Removing the active site deletes its token and activates the other one."""
        old = "https://moodle.example.test/2025-26"
        self.app.add_site(old)
        self.app.switch_site(SITE)
        self.app.remove_site(SITE)
        self.assertIsNone(auth.load_token(SITE, 42))
        self.assertEqual((self.app.settings.site_url, self.app.sites()), (old, [old]))
        self.app.remove_site(old)
        self.assertEqual(self.app.sites(), [old])

    def test_editing_url_to_saved_site_switches(self) -> None:
        """Typing a saved site's URL on the login screen switches instead of duplicating."""
        old = "https://moodle.example.test/2025-26"
        self.app.add_site(old)
        self.app.set_site_url(SITE)
        self.assertEqual(self.app.sites(), [old, SITE])
        self.assertEqual(self.app.settings.user_id, 42)


class SyncAllTests(AppTestCase):
    """run_sync_all(): every academic year, one after another."""

    def setUp(self) -> None:
        """2026-27 logged in with SO selected, plus 2025-26 and 2024-25 saved."""
        super().setUp()
        self.log_in()
        self.app.list_courses()
        self.app.set_selection([1072])
        self.app.set_destination(self.destination)
        self.old = "https://moodle.example.test/2025-26"
        self.app.add_site(self.old)
        auth.store_token(self.old, 42, TOKEN)
        self.app.settings.user_id = 42
        self.app.set_selection([472])
        self.empty = "https://moodle.example.test/2024-25"
        self.app.add_site(self.empty)
        self.app.switch_site(SITE)

    def test_every_site_is_synced_and_active_site_restored(self) -> None:
        """Each site with a session and courses is synced into its own year folder."""
        results = {item.site_url: item for item in self.app.run_sync_all()}
        self.assertEqual(results[SITE].result.summary.new, 1)
        self.assertEqual(results[self.old].result.summary.new, 1)
        self.assertEqual(results[self.empty].skipped_key, "site_skipped_no_courses")
        self.assertEqual(self.app.settings.site_url, SITE)
        self.assertTrue((self.destination / "2026-27" / "SO").is_dir())
        self.assertTrue((self.destination / "2025-26" / "EC").is_dir())

    def test_new_courses_stay_new(self) -> None:
        """Syncing every year does not mark new courses as already seen."""
        FakeClient.enrolled = [*COURSES, {"id": 999, "fullname": "Xarxes [MO1]"}]
        self.app.run_sync_all()
        choices = {choice.course_id: choice for choice in self.app.list_courses()}
        self.assertTrue(choices[999].is_new)

    def test_site_without_session_is_skipped(self) -> None:
        """A saved site whose token is gone is reported, not synced."""
        auth.delete_token(self.old, 42)
        results = {item.site_url: item for item in self.app.run_sync_all()}
        self.assertEqual(results[self.old].skipped_key, "site_skipped_no_session")
        self.assertIsNotNone(results[SITE].result)


class AutoSyncSettingTests(AppTestCase):
    """set_auto_sync() with a fake schtasks runner (never the real scheduler)."""

    def test_on_and_off(self) -> None:
        """Turning it on creates the task and saves the interval; off deletes it."""
        calls = []

        def runner(arguments: list[str]) -> subprocess.CompletedProcess:
            """Record the call; the task exists once created."""
            calls.append(arguments[1])
            code = 0 if arguments[1] != "/Query" or "/Create" in calls else 1
            return subprocess.CompletedProcess(arguments, code, "", "")

        self.app.set_auto_sync(24, runner)
        self.assertEqual(CampusApp(self.app.app_dir).settings.auto_sync_hours, 24)
        self.app.set_auto_sync(0, runner)
        self.assertEqual(self.app.settings.auto_sync_hours, 0)
        self.assertEqual(calls, ["/Create", "/Query", "/Delete"])


if __name__ == "__main__":
    unittest.main()
