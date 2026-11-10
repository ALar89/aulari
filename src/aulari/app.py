"""Application layer: the actions the interface offers, without any tkinter.

The interface (phase 4b-4d) only calls CampusApp methods and shows texts;
everything here can be tested without a window. Methods that use the
network are meant to run in a worker thread (CLAUDE.md section 3).
"""

import functools
import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

import keyring.errors
import requests

from aulari import auth, calendar_export, extras, news, organizer, scheduler
from aulari.calendar_export import CalendarRequest, export_calendar
from aulari.config import Settings, load_settings, save_settings
from aulari.moodle_client import MoodleClient, MoodleError
from aulari.organizer import LOCAL_TIMEZONE
from aulari.registry import Registry, RegistryError
from aulari.sync import (
    Course,
    NotOwnerError,
    Progress,
    Syncer,
    SyncLock,
    SyncLockedError,
    SyncSummary,
    SyncTarget,
    course_folder_path,
    course_root,
    load_machine_id,
    take_over_ownership,
)

REGISTRY_NAME = "registry.sqlite3"
DEADLINE_DAYS = 60
MAX_DEADLINES = 8

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Deadline:
    """An upcoming calendar event of a selected course (RF15)."""

    timestart: int
    course_alias: str
    name: str


@dataclass(frozen=True)
class SiteSyncResult:
    """Outcome of one academic year in 'sync all': result, skip reason or error."""

    site_url: str
    result: SyncResult | None = None
    skipped_key: str = ""  # text key when the site was skipped
    error: Exception | None = None


class AppError(Exception):
    """A problem the user can fix; key is a text key of aulari/strings."""

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
    alias: str = ""  # folder alias once the course has been synced (RF4)


@dataclass
class SyncResult:
    """Sync summary plus the calendar export result."""

    summary: SyncSummary
    calendar_path: Path | None = None
    calendar_events: int = 0
    calendar_error: str = ""
    news_path: Path | None = None  # Novedades.txt, if this sync changed anything (RF14)
    announcements: list[extras.Announcement] = field(default_factory=list)  # RF16
    announcements_error: str = ""


# Errors that only stop one site in 'sync all'.
SITE_ERRORS = (
    AppError,
    MoodleError,
    requests.RequestException,
    SyncLockedError,
    NotOwnerError,
    RegistryError,
    keyring.errors.KeyringError,
    OSError,
)


def _event_start(event: calendar_export.CalendarEvent) -> int:
    """Sort key: event start time."""
    result = event.timestart
    return result


@dataclass
class SubfolderPlan:
    """What changes when the campus subfolder changes (RF20)."""

    changes: list[tuple[int, int, str]] = field(default_factory=list)  # site, course, new folder
    moves: list[tuple[str, str]] = field(default_factory=list)  # old, new section folder
    old_folders: list[str] = field(default_factory=list)

    def add(
        self, site_id: int, course_id: int, folders: tuple[str, str], sections: list[str]
    ) -> None:
        """Add one course: its (old, new) folder and the section folders to move."""
        old_folder, new_folder = folders
        self.changes.append((site_id, course_id, new_folder))
        self.old_folders.append(old_folder)
        for section in sections:
            self.moves.append((f"{old_folder}/{section}", f"{new_folder}/{section}"))


@dataclass
class StylePlan:
    """What changes on disk and in the registry when the space style changes (RF21)."""

    moves: list[tuple[int, str, str]] = field(default_factory=list)  # depth, old, new
    sections: list[dict] = field(default_factory=list)
    versions: list[dict] = field(default_factory=list)

    def ordered_moves(self) -> list[tuple[str, str]]:
        """Return the (old, new) moves without repeats, outer folders first."""
        result = [(old, new) for _depth, old, new in sorted(set(self.moves))]
        return result


def plan_section_styles(plan: StylePlan, sections: list[dict], style: str) -> None:
    """Add to plan the section folders whose name changes with style."""
    for section in sections:
        new_name = organizer.apply_space_style(section["plain_name"], style)
        if new_name != section["folder_name"]:
            folder = section["folder"]
            plan.moves.append((1, f"{folder}/{section['folder_name']}", f"{folder}/{new_name}"))
            plan.sections.append({**section, "folder_name": new_name})


def plan_version_styles(plan: StylePlan, versions: list[dict], style: str) -> None:
    """Add to plan the files (and their folders) whose path changes with style.

    Each folder is renamed inside its already renamed parent, so the moves
    of depth k start from the new names of the first k - 1 parts. A path
    that does not follow its original form (it should not happen) is left alone.
    """
    for row in versions:
        folder = row["folder"]
        prefix = folder + "/"
        old_parts = row["relative_path"][len(prefix) :].split("/")
        new_parts = organizer.styled_path(row["plain_path"], style).split("/")
        consistent = row["relative_path"].startswith(prefix) and len(old_parts) == len(new_parts)
        if consistent and old_parts != new_parts:
            for depth in range(1, len(new_parts) + 1):
                if old_parts[depth - 1] != new_parts[depth - 1]:
                    old = "/".join([*new_parts[: depth - 1], old_parts[depth - 1]])
                    new = "/".join(new_parts[:depth])
                    plan.moves.append((depth, f"{prefix}{old}", f"{prefix}{new}"))
            plan.versions.append({**row, "relative_path": prefix + "/".join(new_parts)})


def clean_folder_name(name: str) -> str:
    """Return a sanitized folder name typed by the user, or '' for a blank one.

    Raises: AppError('error_folder_invalid') if it has no usable characters.
    """
    result = ""
    if name.strip():
        try:
            result = organizer.sanitize_filename(name.strip())
        except ValueError as error:
            raise AppError("error_folder_invalid") from error
    return result


def site_year_dir(site: dict) -> str:
    """Return the academic year folder name of a saved site ('' if it has no year)."""
    result = site["year_folder"] or organizer.academic_year_from_site(site["site_url"]) or ""
    return result


def move_and_update(pending: list[tuple[Path, Path]], update: Callable[[], None]) -> None:
    """Rename each (old, new) path, then call update; undo the moves if anything fails.

    An old path that does not exist (yet or any more) is skipped. Nothing is
    ever overwritten: a new path that already exists stops everything.

    Raises: FileExistsError if a new path is taken; OSError; sqlite3.Error; KeyError.
    """
    done = []
    try:
        for old_path, new_path in pending:
            if old_path.exists():
                # A case-only change ('SO' -> 'So') is the same folder on Windows.
                if new_path.exists() and not old_path.samefile(new_path):
                    raise FileExistsError(f"Already exists: {new_path.name}")
                new_path.parent.mkdir(parents=True, exist_ok=True)
                old_path.rename(new_path)
                done.append((old_path, new_path))
        update()
    except OSError, sqlite3.Error, KeyError:
        for old_path, new_path in reversed(done):
            new_path.rename(old_path)
        raise


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

    # --- several academic years (RF19) ---------------------------------

    def _active_site(self) -> dict:
        """Return the active site as a saved-site dict."""
        result = {
            "site_url": self.settings.site_url,
            "user_id": self.settings.user_id,
            "selected_courses": list(self.settings.selected_courses),
            "known_courses": list(self.settings.known_courses),
            "year_folder": self.settings.year_folder,
        }
        return result

    def _load_site(self, site: dict) -> None:
        """Make a saved site the active one."""
        self.settings.site_url = site["site_url"]
        self.settings.user_id = site["user_id"]
        self.settings.selected_courses = list(site["selected_courses"])
        self.settings.known_courses = list(site["known_courses"])
        self.settings.year_folder = site["year_folder"]

    def _find_other(self, site_url: str) -> dict | None:
        """Return the saved (inactive) site with that URL, or None."""
        result = None
        index = 0
        while index < len(self.settings.other_sites) and result is None:
            if self.settings.other_sites[index]["site_url"] == site_url:
                result = self.settings.other_sites[index]
            index = index + 1
        return result

    def sites(self) -> list[str]:
        """Return the URLs of every saved site, sorted (newest academic year last)."""
        urls = [site["site_url"] for site in self.settings.other_sites]
        result = sorted([self.settings.site_url, *urls])
        return result

    def previous_site(self) -> str | None:
        """Return the site that was active before the current one, if any."""
        result = None
        if self.settings.other_sites:
            result = self.settings.other_sites[-1]["site_url"]
        return result

    def suggested_site_url(self) -> str:
        """Return the current URL with the previous academic year ('2026-27' -> '2025-26')."""
        result = self.settings.site_url
        year = organizer.academic_year_from_site(result)
        if year is not None:
            first = int(year[:4]) - 1
            result = result.replace(year, f"{first}-{(first + 1) % 100:02d}")
        return result

    def switch_site(self, site_url: str) -> None:
        """Make another saved site active; the current one is kept in the list.

        Raises: AppError('error_site_unknown'); OSError when saving.
        """
        clean = site_url.strip().rstrip("/")
        if clean != self.settings.site_url:
            target = self._find_other(clean)
            if target is None:
                raise AppError("error_site_unknown")
            self.settings.other_sites.remove(target)
            self.settings.other_sites.append(self._active_site())
            self._load_site(target)
            self.save()

    def add_site(self, site_url: str) -> None:
        """Add a new academic year site and make it active (the user then logs in).

        Raises: AppError('error_site_exists' or 'error_no_year'); OSError.
        """
        clean = site_url.strip().rstrip("/")
        if clean in self.sites():
            raise AppError("error_site_exists")
        if organizer.academic_year_from_site(clean) is None:
            raise AppError("error_no_year")
        self.settings.other_sites.append(self._active_site())
        self._load_site(
            {
                "site_url": clean,
                "user_id": 0,
                "selected_courses": [],
                "known_courses": [],
                "year_folder": "",
            }
        )
        self.save()

    def remove_site(self, site_url: str) -> None:
        """Forget a site and delete its token; removing the active one activates another.

        The last remaining site is only logged out.

        Raises: AppError('error_site_unknown'); keyring.errors.KeyringError; OSError.
        """
        clean = site_url.strip().rstrip("/")
        if clean == self.settings.site_url:
            self.logout()
            if self.settings.other_sites:
                self._load_site(self.settings.other_sites.pop())
                self.save()
        else:
            target = self._find_other(clean)
            if target is None:
                raise AppError("error_site_unknown")
            if target["user_id"] > 0:
                auth.delete_token(clean, target["user_id"])
            self.settings.other_sites.remove(target)
            self.save()

    def set_site_url(self, site_url: str) -> None:
        """Change the site; the user must log in again for a new site."""
        clean = site_url.strip().rstrip("/")
        if self._find_other(clean) is not None:
            # A saved site: switch to it instead of creating a duplicate (RF19).
            self.switch_site(clean)
        elif clean != self.settings.site_url:
            self.settings.site_url = clean
            self.settings.user_id = 0
            self.settings.selected_courses = []
            self.settings.known_courses = []
            self.settings.year_folder = ""
        self.save()

    def set_destination(self, destination: Path) -> None:
        """Remember the destination folder."""
        self.settings.destination = str(destination)
        self.save()

    def set_selection(self, course_ids: list[int]) -> None:
        """Remember which courses are selected (RF3)."""
        self.settings.selected_courses = sorted(course_ids)
        self.save()

    def set_auto_sync(self, hours: int, runner: scheduler.Runner = scheduler.run_command) -> None:
        """Turn automatic sync on every 'hours' hours, or off with 0 (D7).

        Raises: ValueError for an unsupported interval; SchedulerError if the
        Windows Task Scheduler refuses; OSError when saving the settings.
        """
        if hours == 0:
            scheduler.disable(runner)
        else:
            scheduler.enable(hours, runner)
        self.settings.auto_sync_hours = hours
        self.save()

    def set_language_preference(self, language: str) -> None:
        """Remember the interface language ('' = follow the system)."""
        self.settings.language = language
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
        result = self._course_choices(remember=True)
        return result

    def _course_choices(self, remember: bool) -> list[CourseChoice]:
        """Build the course list; remember=False leaves 'known courses' untouched.

        Syncing every year in the background must not mark new courses as seen:
        the user still has to see them tagged as new.

        Raises: AppError, MoodleError, requests.RequestException, OSError.
        """
        enrolled = self._fetch_courses()
        aliases = self.course_aliases()
        first_time = not self.settings.known_courses
        known = set(self.settings.known_courses)
        selected = set(self.settings.selected_courses)
        result = []
        for course in enrolled:
            course_id = int(course["id"])
            is_new = not first_time and course_id not in known
            result.append(
                CourseChoice(
                    course_id,
                    str(course.get("fullname", "")),
                    is_new,
                    course_id in selected,
                    aliases.get(course_id, ""),
                )
            )
        if remember:
            self.settings.known_courses = sorted(int(course["id"]) for course in enrolled)
            self.save()
        return result

    # --- folders and news ----------------------------------------------

    def year_folder(self) -> str | None:
        """Return the folder name of the active academic year ('2026-27' by default, RF20).

        None if the site URL has no academic year.
        """
        result = organizer.academic_year_from_site(self.settings.site_url)
        if result is not None and self.settings.year_folder:
            result = self.settings.year_folder
        return result

    def course_folder(self, choice: CourseChoice) -> Path | None:
        """Return the folder of a synced course on disk, or None if it does not exist.

        It is the course folder itself, above the campus subfolder (RF20).
        """
        result = None
        year_dir = self.year_folder()
        if choice.alias and year_dir is not None and self.settings.destination:
            folder = Path(self.settings.destination) / year_dir / choice.alias
            if folder.is_dir():
                result = folder
        return result

    def news_file(self) -> Path | None:
        """Return Novedades.txt of the current academic year, or None if there is none yet."""
        result = None
        year_dir = self.year_folder()
        if year_dir is not None and self.settings.destination:
            path = news.news_path(Path(self.settings.destination), year_dir)
            if path.is_file():
                result = path
        return result

    # --- upcoming deadlines (RF15) --------------------------------------

    def upcoming_deadlines(
        self, courses: list[CourseChoice], now: datetime | None = None
    ) -> list[Deadline]:
        """Return the next MAX_DEADLINES events of the selected courses (RF15).

        Only events from now to DEADLINE_DAYS days ahead are kept, sorted by date.

        Raises: AppError, MoodleError, requests.RequestException.
        """
        moment = now if now is not None else datetime.now(UTC)
        selected = [
            choice for choice in courses if choice.course_id in self.settings.selected_courses
        ]
        aliases = {
            choice.course_id: choice.alias or organizer.course_alias(choice.fullname)
            for choice in selected
        }
        result = []
        if aliases:
            with self._client_factory(self.settings.site_url, self._token()) as client:
                events = calendar_export.fetch_events(client, sorted(aliases), moment)
            start = int(moment.timestamp())
            end = int((moment + timedelta(days=DEADLINE_DAYS)).timestamp())
            upcoming = sorted(
                [event for event in events if start <= event.timestart <= end],
                key=_event_start,
            )
            result = [
                Deadline(event.timestart, aliases.get(event.course_id, ""), event.name)
                for event in upcoming[:MAX_DEADLINES]
            ]
        return result

    # --- aliases (RF4) --------------------------------------------------

    def _site_id(self, registry: Registry, year: str) -> int:
        """Return the registry id of the current site."""
        result = registry.get_or_create_site(self.settings.site_url, self.settings.user_id, year)
        return result

    def course_aliases(self) -> dict[int, str]:
        """Return {course id: folder alias} for the courses already synced.

        Raises: OSError, RegistryError.
        """
        result = {}
        year = organizer.academic_year_from_site(self.settings.site_url)
        if year is not None and self.settings.user_id > 0:
            with Registry(self.app_dir / REGISTRY_NAME) as registry:
                result = registry.course_aliases(self._site_id(registry, year))
        return result

    def rename_course(self, course_id: int, new_alias: str) -> str:
        """Rename a course alias: move its folder on disk and update the registry (RF4).

        The disk move happens first, under the sync lock; if the registry
        update fails, the folder is moved back. Returns the final alias.

        Raises: AppError('error_alias_invalid', 'error_alias_not_synced',
        'error_alias_taken', 'error_no_destination', 'error_no_year');
        SyncLockedError; OSError; sqlite3.Error.
        """
        target = self._target()
        try:
            alias = organizer.sanitize_filename(new_alias.strip())
        except ValueError as error:
            raise AppError("error_alias_invalid") from error
        with Registry(self.app_dir / REGISTRY_NAME) as registry:
            site_id = self._site_id(registry, target.academic_year)
            folders = registry.course_folders(site_id)
            if course_id not in folders:
                raise AppError("error_alias_not_synced")
            aliases = registry.course_aliases(site_id)
            others = {name.casefold() for cid, name in aliases.items() if cid != course_id}
            if alias.casefold() in others:
                raise AppError("error_alias_taken")
            # The campus subfolder, if any, moves with the course folder (RF20).
            old_root = course_root(folders[course_id])
            new_root = f"{PurePosixPath(old_root).parent.as_posix()}/{alias}"
            new_folder = new_root + folders[course_id][len(old_root) :]
            if new_root != old_root:
                update = functools.partial(
                    registry.rename_course_folder, site_id, course_id, alias, new_folder
                )
                self._move_folders([(old_root, new_root)], "error_alias_taken", update)
        return alias

    def _move_folders(
        self, moves: list[tuple[str, str]], taken_key: str, update: Callable[[], None]
    ) -> None:
        """Move folders inside the destination, then update the registry (RF4, RF20).

        moves holds (old, new) paths relative to the destination, in order; an
        old path that does not exist when its turn comes is skipped, so a move
        can depend on an earlier one (RF21). Everything runs under the sync
        lock. If a move or the update fails, what was already moved is moved
        back. Without a destination only the registry is updated.

        Raises: AppError(taken_key) if a new path already exists; ValueError
        if a path escapes the destination; SyncLockedError; OSError;
        sqlite3.Error; KeyError.
        """
        if not self.settings.destination:
            update()
        else:
            destination = Path(self.settings.destination)
            pairs = []
            for old, new in moves:
                old_path = destination / old
                new_path = destination / new
                # Check both stay inside, but keep the unresolved paths: resolve()
                # returns the on-disk case and would turn 'So' back into 'SO'.
                organizer.ensure_inside(destination, old_path)
                organizer.ensure_inside(destination, new_path)
                pairs.append((old_path, new_path))
            with SyncLock(destination):
                pending = [
                    (old_path, new_path) for old_path, new_path in pairs if old_path.exists()
                ]
                for old_path, new_path in pending:
                    # A case-only change ('SO' -> 'So') is the same folder on Windows.
                    if new_path.exists() and not old_path.samefile(new_path):
                        raise AppError(taken_key)
                try:
                    move_and_update(pairs, update)
                except FileExistsError as error:
                    raise AppError(taken_key) from error

    # --- academic year folder and campus subfolder (RF20) --------------

    def rename_year_folder(self, new_name: str) -> str:
        """Rename the folder of the active academic year ('2026-27' -> '2n Curs', RF20).

        The folder moves on disk with everything inside (courses, calendar,
        Novedades.txt, the user's own folders) and the registry is updated.
        An empty name goes back to the academic year. Returns the final name.

        Raises: AppError('error_no_year', 'error_folder_invalid',
        'error_folder_taken'); SyncLockedError; OSError; sqlite3.Error.
        """
        year = organizer.academic_year_from_site(self.settings.site_url)
        old = self.year_folder()
        if year is None or old is None:
            raise AppError("error_no_year")
        name = clean_folder_name(new_name) or year
        others = {site_year_dir(site).casefold() for site in self.settings.other_sites}
        if name.casefold() in others:
            raise AppError("error_folder_taken")
        if name != old:
            with Registry(self.app_dir / REGISTRY_NAME) as registry:
                # Every user of this site, also one whose session was closed.
                changes = []
                for site_id in registry.site_ids(self.settings.site_url):
                    for course_id, folder in registry.course_folders(site_id).items():
                        if folder.startswith(old + "/"):
                            changes.append((site_id, course_id, name + folder[len(old) :]))
                update = functools.partial(registry.move_course_folders, changes)
                self._move_folders([(old, name)], "error_folder_taken", update)
            self.settings.year_folder = "" if name == year else name
            self.save()
        return name

    def set_campus_subfolder(self, new_name: str) -> str:
        """Put the campus material of every course in a subfolder, or take it out (RF20).

        'Campus' turns '2n Curs/BD/01 - Tema 1' into '2n Curs/BD/Campus/01 - Tema 1';
        an empty name takes it out again. Only the section folders that Aulari
        created are moved: anything else in the course folder stays where it is.
        Returns the final name ('' = no subfolder).

        Raises: AppError('error_folder_invalid', 'error_folder_taken');
        SyncLockedError; OSError; sqlite3.Error.
        """
        name = clean_folder_name(new_name)
        if name != self.settings.campus_subfolder:
            with Registry(self.app_dir / REGISTRY_NAME) as registry:
                plan = self._subfolder_plan(registry, name)
                update = functools.partial(registry.move_course_folders, plan.changes)
                self._move_folders(plan.moves, "error_folder_taken", update)
            self.settings.campus_subfolder = name
            self.save()
            self._remove_empty_folders(plan.old_folders)
        return name

    def set_space_style(self, style: str) -> None:
        """Use spaces, '_' or '-' in the names Aulari creates, also the existing ones (RF21).

        Every section folder, subfolder and file Aulari downloaded is renamed
        from its original name, so changing back and forth loses nothing
        ('P1_enunciat.pdf' from Moodle keeps its '_'). Names the user typed
        (academic year folder, aliases, campus subfolder) are not touched.

        Raises: ValueError for an unknown style; AppError('error_names_taken')
        if a new name is already taken (nothing is changed); SyncLockedError;
        OSError; sqlite3.Error.
        """
        if style not in organizer.SPACE_STYLES:
            raise ValueError(f"Unknown space style: {style!r}")
        if style != self.settings.space_style:
            plan = StylePlan()
            with Registry(self.app_dir / REGISTRY_NAME) as registry:
                plan_section_styles(plan, registry.all_sections(), style)
                plan_version_styles(plan, registry.all_versions(), style)
                update = functools.partial(registry.rename_paths, plan.sections, plan.versions)
                self._move_folders(plan.ordered_moves(), "error_names_taken", update)
            self.settings.space_style = style
            self.save()

    def _subfolder_plan(self, registry: Registry, name: str) -> SubfolderPlan:
        """Return what changes when the campus subfolder becomes name.

        Every registered course of every academic year is included, also those
        of a year whose session was closed.

        Raises: AppError('error_folder_taken') if a section folder has that name.
        """
        result = SubfolderPlan()
        for site_id in registry.site_ids():
            for course_id, folder in registry.course_folders(site_id).items():
                sections = registry.section_folders(site_id, course_id)
                if name.casefold() in {section.casefold() for section in sections}:
                    raise AppError("error_folder_taken")
                root = PurePosixPath(course_root(folder))
                new_folder = course_folder_path(root.parent.as_posix(), root.name, name)
                if new_folder != folder:
                    result.add(site_id, course_id, (folder, new_folder), sections)
        return result

    def _remove_empty_folders(self, folders: list[str]) -> None:
        """Delete the old campus subfolders that were left empty (failures are logged)."""
        if self.settings.destination:
            destination = Path(self.settings.destination)
            for folder in folders:
                path = destination / folder
                if folder != course_root(folder) and path.is_dir() and not any(path.iterdir()):
                    try:
                        path.rmdir()
                    except OSError as error:
                        logger.warning("Could not remove an empty folder: %s", error)

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
            self.settings.site_url,
            self.settings.user_id,
            year,
            Path(self.settings.destination),
            self.settings.year_folder,
            self.settings.campus_subfolder,
            self.settings.space_style,
        )
        return result

    def _selected_aliases(self, registry: Registry, site_id: int) -> dict[int, str]:
        """Return {course id: alias} of the selected courses already synced."""
        aliases = registry.course_aliases(site_id)
        result = {
            course_id: aliases[course_id]
            for course_id in self.settings.selected_courses
            if course_id in aliases
        }
        return result

    def _export_calendar(
        self, client: MoodleClient, registry: Registry, target: SyncTarget, result: SyncResult
    ) -> None:
        """Export the calendar of the selected courses into result (errors are kept)."""
        site_id = registry.get_or_create_site(target.site_url, target.user_id, target.academic_year)
        aliases = self._selected_aliases(registry, site_id)
        request = CalendarRequest(
            site_id,
            target.site_url,
            target.academic_year,
            target.destination,
            aliases,
            target.year_dir(),
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
                self._check_announcements(client, registry, target, result)
        self._write_news(target, result)
        return result

    def run_sync_all(
        self,
        progress: Callable[[Progress], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> list[SiteSyncResult]:
        """Sync every saved academic year, one after another, then return to the active one.

        Sites without a session or without selected courses are skipped; an
        error in one site does not stop the others. Cancelling stops between
        files and between sites.

        Raises: OSError if the settings cannot be saved when switching sites.
        """
        original = self.settings.site_url
        urls = self.sites()
        results = []
        index = 0
        try:
            while index < len(urls) and not (cancel is not None and cancel.is_set()):
                self.switch_site(urls[index])
                results.append(self._sync_active_site(progress, cancel))
                index = index + 1
        finally:
            self.switch_site(original)
        return results

    def _sync_active_site(
        self,
        progress: Callable[[Progress], None] | None,
        cancel: threading.Event | None,
    ) -> SiteSyncResult:
        """Sync the active site for run_sync_all(), turning errors into a result."""
        site_url = self.settings.site_url
        result = SiteSyncResult(site_url)
        try:
            if not self.settings.selected_courses:
                result = SiteSyncResult(site_url, skipped_key="site_skipped_no_courses")
            elif not self.is_logged_in():
                result = SiteSyncResult(site_url, skipped_key="site_skipped_no_session")
            else:
                courses = self._course_choices(remember=False)
                result = SiteSyncResult(site_url, self.run_sync(courses, progress, cancel))
        except SITE_ERRORS as error:
            result = SiteSyncResult(site_url, error=error)
        return result

    def _check_announcements(
        self, client: MoodleClient, registry: Registry, target: SyncTarget, result: SyncResult
    ) -> None:
        """Look for new announcements in the selected courses (errors are kept, RF16)."""
        site_id = registry.get_or_create_site(target.site_url, target.user_id, target.academic_year)
        aliases = self._selected_aliases(registry, site_id)
        try:
            result.announcements = extras.new_announcements(client, registry, site_id, aliases)
        except (MoodleError, requests.RequestException) as error:
            result.announcements_error = str(error)

    # --- submissions and grades (RF17, RF18) ----------------------------

    def _aliases_for(self, courses: list[CourseChoice], selected_only: bool) -> dict[int, str]:
        """Return {course id: alias} (computed alias for courses never synced)."""
        result = {
            choice.course_id: choice.alias or organizer.course_alias(choice.fullname)
            for choice in courses
            if not selected_only or choice.course_id in self.settings.selected_courses
        }
        return result

    def submissions(self, courses: list[CourseChoice]) -> list[extras.SubmissionRow]:
        """Return the submission state of every assignment of the selected courses.

        Raises: AppError, MoodleError, requests.RequestException.
        """
        aliases = self._aliases_for(courses, selected_only=True)
        with self._client_factory(self.settings.site_url, self._token()) as client:
            result = extras.submissions(client, aliases)
        return result

    def course_grades(self, courses: list[CourseChoice]) -> list[extras.CourseGrade]:
        """Return the overall grade of each course (all enrolled courses, RF18).

        Raises: AppError, MoodleError, requests.RequestException.
        """
        names = {choice.course_id: choice.fullname for choice in courses}
        with self._client_factory(self.settings.site_url, self._token()) as client:
            result = extras.course_grades(client, self.settings.user_id, names)
        return result

    def grade_items(self, course_id: int) -> list[extras.GradeItem]:
        """Return the detailed grade report of one course (RF18).

        Raises: AppError, MoodleError, requests.RequestException.
        """
        with self._client_factory(self.settings.site_url, self._token()) as client:
            result = extras.grade_items(client, course_id, self.settings.user_id)
        return result

    def _write_news(self, target: SyncTarget, result: SyncResult) -> None:
        """Add this sync's changes to Novedades.txt (a failure is only logged)."""
        path = news.news_path(target.destination, target.year_dir())
        try:
            if news.write_news(path, result.summary.changes, datetime.now(LOCAL_TIMEZONE)):
                result.news_path = path
        except OSError as error:
            logger.warning("Could not write %s: %s", path.name, error)

    def take_over(self) -> None:
        """Make this computer the owner of the destination folder (D2).

        Raises: AppError('error_no_destination'); OSError.
        """
        if not self.settings.destination:
            raise AppError("error_no_destination")
        take_over_ownership(Path(self.settings.destination), load_machine_id(self.app_dir))
