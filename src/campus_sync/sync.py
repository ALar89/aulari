"""Orchestrate a sync run: courses -> items -> disk + registry (RF5-RF11).

One run:
1. takes the lock file (<destination>/.campus-sync/sync.lock);
2. checks that this machine owns the destination (owner.json, decision D2);
3. removes leftover temporary files;
4. for each course: gets contents and assignments, and processes every
   item (new, unchanged, new version), then marks files no longer in
   Moodle as removed_in_moodle;
5. returns a summary.

An error in one file or course does not stop the others; an expired token
(InvalidTokenError) stops the run cleanly. Cancelling stops between files.
Progress is reported through a callback, so the core never imports tkinter.
"""

import json
import logging
import os
import socket
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Self

import requests

from campus_sync import downloader, organizer
from campus_sync.downloader import DownloadError
from campus_sync.moodle_client import InvalidTokenError, MoodleClient, MoodleError, array_param
from campus_sync.moodle_items import (
    KIND_URL,
    SyncItem,
    items_from_assignments,
    items_from_contents,
)
from campus_sync.registry import STATE_MISSING, STATE_PRESENT, FileStatus, Registry

CONTROL_DIR_NAME = ".campus-sync"
LOCK_NAME = "sync.lock"
OWNER_NAME = "owner.json"
MACHINE_ID_NAME = "machine_id"
UNKNOWN_SHA256 = ""  # adopted files are never read, so their hash is unknown

logger = logging.getLogger(__name__)


class SyncLockedError(Exception):
    """Another sync is running on this destination (or a stale lock file remains)."""


class NotOwnerError(Exception):
    """The destination belongs to another machine (decision D2)."""

    def __init__(self, owner_host: str) -> None:
        """Keep the owner's host name so the interface can show it."""
        super().__init__(f"This folder is synced by another computer: {owner_host}")
        self.owner_host = owner_host


@dataclass(frozen=True)
class Course:
    """A course selected for synchronization."""

    course_id: int
    fullname: str


@dataclass(frozen=True)
class SyncTarget:
    """Where and for whom a sync runs."""

    site_url: str
    user_id: int
    academic_year: str
    destination: Path


@dataclass(frozen=True)
class Progress:
    """Progress report sent to the callback before each item."""

    course_folder: str
    done: int
    total: int
    filename: str


@dataclass
class SyncSummary:
    """Result of a sync run (RF10)."""

    new: int = 0
    new_versions: int = 0
    adopted: int = 0
    unchanged: int = 0
    missing_locally: int = 0
    removed_in_moodle: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    invalid_token: bool = False


def load_machine_id(app_dir: Path) -> str:
    """Return this machine's random id, creating it on first use.

    Raises: OSError if the file cannot be read or written.
    """
    path = app_dir / MACHINE_ID_NAME
    if not path.is_file():
        app_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(uuid.uuid4().hex, encoding="ascii")
    result = path.read_text(encoding="ascii").strip()
    return result


def _owner_path(destination: Path) -> Path:
    """Return the path of owner.json inside the destination."""
    result = destination / CONTROL_DIR_NAME / OWNER_NAME
    return result


def _ensure_control_dir(destination: Path) -> None:
    """Create <destination>/.campus-sync if needed and keep it hidden on Windows."""
    control_dir = destination / CONTROL_DIR_NAME
    control_dir.mkdir(parents=True, exist_ok=True)
    downloader.mark_hidden(control_dir)


def take_over_ownership(destination: Path, machine_id: str) -> None:
    """Make this machine the owner of the destination ('take over', D2).

    Raises: OSError if owner.json cannot be written.
    """
    path = _owner_path(destination)
    _ensure_control_dir(destination)
    data = {"machine_id": machine_id, "hostname": socket.gethostname()}
    with path.open("w", encoding="utf-8") as owner_file:
        json.dump(data, owner_file)


def check_owner(destination: Path, machine_id: str) -> None:
    """Check that this machine owns the destination; claim it if nobody does.

    Raises: NotOwnerError if another machine owns it; OSError or
    json.JSONDecodeError if owner.json cannot be read.
    """
    path = _owner_path(destination)
    if not path.is_file():
        take_over_ownership(destination, machine_id)
    with path.open(encoding="utf-8") as owner_file:
        owner = json.load(owner_file)
    if owner.get("machine_id") != machine_id:
        raise NotOwnerError(str(owner.get("hostname", "?")))


class SyncLock:
    """Lock file that prevents two syncs on the same destination at once."""

    def __init__(self, destination: Path) -> None:
        """Remember where the lock file goes."""
        self._path = destination / CONTROL_DIR_NAME / LOCK_NAME

    def __enter__(self) -> Self:
        """Create the lock file exclusively.

        Raises: SyncLockedError if it already exists.
        """
        _ensure_control_dir(self._path.parent.parent)
        try:
            with self._path.open("x", encoding="ascii") as lock_file:
                lock_file.write(str(os.getpid()))
        except FileExistsError as error:
            raise SyncLockedError(f"Another sync is running (lock file: {self._path})") from error
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Remove the lock file."""
        self._path.unlink(missing_ok=True)


class Syncer:
    """One sync run over several courses of one site."""

    def __init__(
        self,
        client: MoodleClient,
        registry: Registry,
        target: SyncTarget,
        progress: Callable[[Progress], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        """Store the collaborators; nothing is touched until run()."""
        self._client = client
        self._registry = registry
        self._target = target
        self._destination = target.destination
        self._progress = progress
        self._cancel = cancel
        self._summary = SyncSummary()
        self._site_id = 0

    # --- helpers -------------------------------------------------------

    def _cancelled(self) -> bool:
        """Return True if the user asked to cancel."""
        result = self._cancel is not None and self._cancel.is_set()
        return result

    def _report(self, course_folder: str, done: int, total: int, filename: str) -> None:
        """Send a progress report if there is a callback."""
        if self._progress is not None:
            self._progress(Progress(course_folder, done, total, filename))

    def _relative(self, path: Path) -> str:
        """Return path relative to the destination, with '/' separators."""
        result = path.resolve().relative_to(self._destination.resolve()).as_posix()
        return result

    def _stored_path(self, relative_path: str) -> Path:
        """Return the absolute path of a registry path, checked to stay inside.

        Raises: ValueError if the stored path escapes the destination.
        """
        result = organizer.ensure_inside(self._destination, self._destination / relative_path)
        return result

    def _record(self, course_id: int, item: SyncItem, target: Path, sha256: str) -> bool:
        """Store a download in the registry; return True if it created a version."""
        version = {
            "relative_path": self._relative(target),
            "timemodified": item.timemodified,
            "filesize": item.filesize,
            "sha256": sha256,
        }
        result = self._registry.record_download(self._site_id, course_id, item.key, version)
        return result

    # --- courses -------------------------------------------------------

    def _course_folder(self, course: Course) -> str:
        """Return the course folder ('2026-27/SO'), creating it in the registry if new.

        A new alias that collides with another course of the same year gets
        ' (2)'; the user can rename it later (RF4).
        """
        existing = self._registry.course_folders(self._site_id)
        if course.course_id in existing:
            result = existing[course.course_id]
        else:
            year = self._target.academic_year
            alias = organizer.sanitize_filename(organizer.course_alias(course.fullname))
            used = {
                PurePosixPath(folder).name
                for folder in existing.values()
                if PurePosixPath(folder).parent == PurePosixPath(year)
            }
            alias = organizer.unique_name(alias, used, keep_extension=False)
            result = f"{year}/{alias}"
            self._registry.ensure_course(self._site_id, course.course_id, alias, result)
        return result

    def _course_items(self, course_id: int) -> list[SyncItem]:
        """Fetch contents and assignments of a course and return its items.

        Raises: MoodleError, requests.RequestException, ValueError.
        """
        sections = self._client.call("core_course_get_contents", {"courseid": course_id})
        assignments = self._client.call(
            "mod_assign_get_assignments", array_param("courseids", [course_id])
        )
        result = items_from_contents(sections) + items_from_assignments(assignments, sections)
        return result

    def _sync_course(self, course: Course) -> None:
        """Process every item of one course, then mark files gone from Moodle.

        Raises: InvalidTokenError to stop the run; MoodleError,
        requests.RequestException or ValueError for course-level errors.
        """
        folder = self._course_folder(course)
        items = self._course_items(course.course_id)
        seen = set()
        index = 0
        while index < len(items) and not self._cancelled():
            item = items[index]
            self._report(folder, index, len(items), item.key.filename)
            self._process_item_safely(course.course_id, folder, item)
            seen.add(item.key)
            index = index + 1
        if not self._cancelled():
            removed = self._registry.mark_removed(self._site_id, course.course_id, seen)
            self._summary.removed_in_moodle = self._summary.removed_in_moodle + removed

    # --- items ---------------------------------------------------------

    def _process_item_safely(self, course_id: int, folder: str, item: SyncItem) -> None:
        """Process one item; record its error and go on, except for an expired token.

        Raises: InvalidTokenError.
        """
        try:
            self._process_item(course_id, folder, item)
        except InvalidTokenError:
            raise
        except (MoodleError, DownloadError, OSError, ValueError) as error:
            # OSError also covers requests.RequestException and FileExistsError.
            message = f"{item.key.filename}: {error}"
            self._summary.errors.append(message)
            logger.warning("Item failed: %s", message)

    def _item_status(self, item: SyncItem) -> FileStatus:
        """Classify an item; links also count as changed if their URL changed.

        Raises: ValueError for an unsafe link URL.
        """
        result = self._registry.classify(self._site_id, item.key, item.timemodified, item.filesize)
        if item.kind == KIND_URL and result != FileStatus.NEW:
            latest = self._registry.latest_version(self._site_id, item.key)
            text_hash = downloader.sha256_text(downloader.url_shortcut_text(item))
            result = FileStatus.UNCHANGED if latest["sha256"] == text_hash else FileStatus.CHANGED
        return result

    def _process_item(self, course_id: int, folder: str, item: SyncItem) -> None:
        """Decide what to do with one item and do it.

        Raises: everything the handlers raise.
        """
        proposed = organizer.section_folder(item.section.number, item.section.name)
        section_dir = self._registry.section_folder(
            self._site_id, course_id, item.section.section_id, proposed
        )
        relative_dir = downloader.item_relative_dir(folder, section_dir, item)
        status = self._item_status(item)
        if status == FileStatus.UNCHANGED:
            self._handle_unchanged(item)
        elif status == FileStatus.NEW:
            self._handle_new(course_id, relative_dir, item)
        else:
            self._handle_changed(course_id, relative_dir, item)

    def _handle_unchanged(self, item: SyncItem) -> None:
        """Update last_checked and whether the local copy still exists.

        A file the user moved or deleted is marked missing, never re-downloaded
        automatically (that is RF8, 'download again').

        Raises: ValueError if the stored path escapes the destination.
        """
        latest = self._registry.latest_version(self._site_id, item.key)
        self._registry.mark_seen(self._site_id, item.key)
        if self._stored_path(latest["relative_path"]).exists():
            self._registry.set_state(self._site_id, item.key, STATE_PRESENT)
        else:
            self._registry.set_state(self._site_id, item.key, STATE_MISSING)
            self._summary.missing_locally = self._summary.missing_locally + 1
        self._summary.unchanged = self._summary.unchanged + 1

    def _handle_new(self, course_id: int, relative_dir: PurePosixPath, item: SyncItem) -> None:
        """Write a new item: link, adopted existing file, or fresh download.

        Raises: ValueError, OSError, DownloadError, MoodleError.
        """
        target = downloader.plan_target(
            self._destination, relative_dir, downloader.item_filename(item)
        )
        if item.kind != KIND_URL and downloader.adoptable(target, item.filesize):
            self._record(course_id, item, target, UNKNOWN_SHA256)
            self._summary.adopted = self._summary.adopted + 1
        else:
            if target.exists():
                target = downloader.free_unique_path(target)
            if item.kind == KIND_URL:
                sha256 = downloader.write_url_shortcut(item, target)
            else:
                _, sha256, _ = downloader.download_atomic(
                    self._client, item, self._destination, target
                )
            self._record(course_id, item, target, sha256)
            self._summary.new = self._summary.new + 1

    def _handle_changed(self, course_id: int, relative_dir: PurePosixPath, item: SyncItem) -> None:
        """Write a new version next to the previous one, never overwriting (RF7).

        If the previous folder no longer exists, the version goes to the
        item's current section folder (design.md section 6). If the content
        turns out identical (same SHA-256), nothing is written.

        Raises: ValueError, OSError, DownloadError, MoodleError.
        """
        latest = self._registry.latest_version(self._site_id, item.key)
        previous = self._stored_path(latest["relative_path"])
        folder = previous.parent
        if not folder.is_dir():
            folder = self._destination.resolve() / relative_dir
            folder.mkdir(parents=True, exist_ok=True)
        base = downloader.plan_target(
            self._destination,
            PurePosixPath(self._relative(folder)),
            downloader.item_filename(item),
        )
        target = downloader.free_versioned_path(base, item.timemodified)
        moved = True
        if item.kind == KIND_URL:
            sha256 = downloader.write_url_shortcut(item, target)
        else:
            _, sha256, moved = downloader.download_atomic(
                self._client, item, self._destination, target, skip_if_sha256=latest["sha256"]
            )
        created = self._record(course_id, item, target, sha256)
        if moved and created:
            self._summary.new_versions = self._summary.new_versions + 1
        else:
            self._summary.unchanged = self._summary.unchanged + 1

    # --- run -----------------------------------------------------------

    def run(self, courses: list[Course], machine_id: str) -> SyncSummary:
        """Synchronize the courses and return the summary.

        Raises: SyncLockedError if another sync holds the lock; NotOwnerError
        if another machine owns the destination; OSError on lock or owner
        file errors.
        """
        with SyncLock(self._destination):
            check_owner(self._destination, machine_id)
            downloader.clean_temp_dir(self._destination)
            self._site_id = self._registry.get_or_create_site(
                self._target.site_url, self._target.user_id, self._target.academic_year
            )
            index = 0
            stop = False
            while index < len(courses) and not stop:
                course = courses[index]
                try:
                    self._sync_course(course)
                except InvalidTokenError:
                    self._summary.invalid_token = True
                    logger.error("The token is no longer valid: log in again")
                except (MoodleError, requests.RequestException, ValueError) as error:
                    message = f"{course.fullname}: {error}"
                    self._summary.errors.append(message)
                    logger.warning("Course failed: %s", message)
                stop = self._summary.invalid_token or self._cancelled()
                index = index + 1
            self._summary.cancelled = self._cancelled()
            downloader.remove_empty_temp_dir(self._destination)
        result = self._summary
        return result
