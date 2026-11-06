"""Application layer: the actions the interface offers, without any tkinter.

The interface (phase 4b-4d) only calls CampusApp methods and shows texts;
everything here can be tested without a window. Methods that use the
network are meant to run in a worker thread (CLAUDE.md section 3).
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import requests

from campus_sync import auth, organizer
from campus_sync.calendar_export import CalendarRequest, export_calendar
from campus_sync.config import Settings, load_settings, save_settings
from campus_sync.moodle_client import MoodleClient, MoodleError
from campus_sync.registry import Registry
from campus_sync.sync import (
    Course,
    Progress,
    Syncer,
    SyncSummary,
    SyncTarget,
    load_machine_id,
    take_over_ownership,
)

REGISTRY_NAME = "registry.sqlite3"


class AppError(Exception):
    """A problem the user can fix; key is a text key of campus_sync/strings."""

    def __init__(self, key: str) -> None:
        """Keep the text key so the interface can show the translated message."""
        super().__init__(key)
        self.key = key


@dataclass(frozen=True)
class CourseChoice:
    """A course as shown in the selection list (RF3)."""

    course_id: int
    fullname: str
    is_new: bool
    selected: bool


@dataclass
class SyncResult:
    """Sync summary plus the calendar export result."""

    summary: SyncSummary
    calendar_path: Path | None = None
    calendar_events: int = 0
    calendar_error: str = ""


class CampusApp:
    """Settings + login + course list + sync + calendar, for one user and site."""

    def __init__(
        self, app_dir: Path, client_factory: Callable[[str, str], MoodleClient] = MoodleClient
    ) -> None:
        """Load the settings from app_dir.

        Raises: json.JSONDecodeError if the config file is corrupt; OSError.
        """
        self.app_dir = app_dir
        self.settings: Settings = load_settings(app_dir)
        self._client_factory = client_factory

    # --- settings ------------------------------------------------------

    def save(self) -> None:
        """Save the settings. Raises: OSError."""
        save_settings(self.app_dir, self.settings)

    def set_site_url(self, site_url: str) -> None:
        """Change the site; the user must log in again for a new site."""
        clean = site_url.strip().rstrip("/")
        if clean != self.settings.site_url:
            self.settings.site_url = clean
            self.settings.user_id = 0
            self.settings.selected_courses = []
            self.settings.known_courses = []
        self.save()

    def set_destination(self, destination: Path) -> None:
        """Remember the destination folder."""
        self.settings.destination = str(destination)
        self.save()

    def set_selection(self, course_ids: list[int]) -> None:
        """Remember which courses are selected (RF3)."""
        self.settings.selected_courses = sorted(course_ids)
        self.save()

    def accept_first_run_notice(self) -> None:
        """Remember that the personal-use notice was shown (RF12)."""
        self.settings.first_run_notice_seen = True
        self.save()

    # --- login ---------------------------------------------------------

    def is_logged_in(self) -> bool:
        """Return True if there is a stored token for the current site and user.

        Raises: keyring.errors.KeyringError.
        """
        result = (
            self.settings.user_id > 0
            and auth.load_token(self.settings.site_url, self.settings.user_id) is not None
        )
        return result

    def start_login(self) -> tuple[str, str]:
        """Return (launch.php URL to open in the browser, passport to keep)."""
        passport = auth.new_passport()
        result = (auth.build_launch_url(self.settings.site_url, passport), passport)
        return result

    def finish_login(self, pasted_url: str, passport: str) -> int:
        """Check the pasted redirect, store the token and return the user id.

        Raises: ValueError for a wrong redirect; InvalidTokenError, MoodleError,
        requests.RequestException; keyring.errors.KeyringError.
        """
        result = auth.login(self.settings.site_url, pasted_url, passport, self._client_factory)
        if result != self.settings.user_id:
            self.settings.selected_courses = []
            self.settings.known_courses = []
        self.settings.user_id = result
        self.save()
        return result

    def logout(self) -> None:
        """Delete the token from this computer (RF2).

        Raises: keyring.errors.KeyringError.
        """
        if self.settings.user_id > 0:
            auth.delete_token(self.settings.site_url, self.settings.user_id)
        self.settings.user_id = 0
        self.save()

    def _token(self) -> str:
        """Return the stored token.

        Raises: AppError('error_invalid_token') if there is none.
        """
        result = auth.load_token(self.settings.site_url, self.settings.user_id)
        if result is None:
            raise AppError("error_invalid_token")
        return result

    # --- courses -------------------------------------------------------

    def _fetch_courses(self) -> list[dict]:
        """Return the enrolled courses of the user.

        Raises: AppError, MoodleError, requests.RequestException.
        """
        with self._client_factory(self.settings.site_url, self._token()) as client:
            result = client.call("core_enrol_get_users_courses", {"userid": self.settings.user_id})
        return result

    def list_courses(self) -> list[CourseChoice]:
        """Return the courses for the selection list and remember them (RF3).

        First time: nothing is selected, the user chooses (on e-aules the
        'in progress' classification also includes spaces that are not
        subjects, design.md section 10). Later: the saved selection is kept,
        and courses never seen before are marked new and left unselected.

        Raises: AppError, MoodleError, requests.RequestException, OSError.
        """
        enrolled = self._fetch_courses()
        first_time = not self.settings.known_courses
        known = set(self.settings.known_courses)
        selected = set(self.settings.selected_courses)
        result = []
        for course in enrolled:
            course_id = int(course["id"])
            is_new = not first_time and course_id not in known
            result.append(
                CourseChoice(
                    course_id, str(course.get("fullname", "")), is_new, course_id in selected
                )
            )
        self.settings.known_courses = sorted(int(course["id"]) for course in enrolled)
        self.save()
        return result

    # --- sync ----------------------------------------------------------

    def _target(self) -> SyncTarget:
        """Return the sync target for the current settings.

        Raises: AppError('error_no_destination' or 'error_no_year').
        """
        if not self.settings.destination:
            raise AppError("error_no_destination")
        year = organizer.academic_year_from_site(self.settings.site_url)
        if year is None:
            raise AppError("error_no_year")
        result = SyncTarget(
            self.settings.site_url, self.settings.user_id, year, Path(self.settings.destination)
        )
        return result

    def _export_calendar(
        self, client: MoodleClient, registry: Registry, target: SyncTarget, result: SyncResult
    ) -> None:
        """Export the calendar of the selected courses into result (errors are kept)."""
        site_id = registry.get_or_create_site(target.site_url, target.user_id, target.academic_year)
        folders = registry.course_folders(site_id)
        aliases = {
            course_id: PurePosixPath(folders[course_id]).name
            for course_id in self.settings.selected_courses
            if course_id in folders
        }
        request = CalendarRequest(
            site_id, target.site_url, target.academic_year, target.destination, aliases
        )
        try:
            path, count = export_calendar(client, registry, request, datetime.now(UTC))
            result.calendar_path = path
            result.calendar_events = count
        except (MoodleError, requests.RequestException, OSError) as error:
            result.calendar_error = str(error)

    def run_sync(
        self,
        courses: list[CourseChoice],
        progress: Callable[[Progress], None] | None = None,
        cancel: threading.Event | None = None,
        restore_missing: bool = False,
    ) -> SyncResult:
        """Sync the selected courses, then export the calendar (RF9, RF10).

        restore_missing downloads again the files deleted locally (RF8).

        Raises: AppError; SyncLockedError; NotOwnerError (offer take_over());
        OSError; RegistryError.
        """
        target = self._target()
        chosen = [
            Course(choice.course_id, choice.fullname)
            for choice in courses
            if choice.course_id in self.settings.selected_courses
        ]
        machine_id = load_machine_id(self.app_dir)
        with (
            self._client_factory(target.site_url, self._token()) as client,
            Registry(self.app_dir / REGISTRY_NAME) as registry,
        ):
            syncer = Syncer(client, registry, target, progress, cancel)
            summary = syncer.run(chosen, machine_id, restore_missing)
            result = SyncResult(summary)
            if not summary.invalid_token and not summary.cancelled:
                self._export_calendar(client, registry, target, result)
        return result

    def take_over(self) -> None:
        """Make this computer the owner of the destination folder (D2).

        Raises: AppError('error_no_destination'); OSError.
        """
        if not self.settings.destination:
            raise AppError("error_no_destination")
        take_over_ownership(Path(self.settings.destination), load_machine_id(self.app_dir))
