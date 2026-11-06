"""Local SQLite registry of what was downloaded (docs/design.md section 6).

The database lives in the app data folder (%LOCALAPPDATA%\\campus-sync\\),
never inside the synced destination. Paths are stored relative to the
destination, with '/' separators.

A file is identified by (site, cmid, filearea, filepath, filename); 'site'
already includes the user id. Each download of a file is a row in
file_versions, so the history of 'Name (YYYY-MM-DD).ext' copies is kept.
This module only touches the database: moving folders or files on disk is
the job of the sync module.
"""

import enum
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

SCHEMA_VERSION = 1
STATE_PRESENT = "present"
STATE_MISSING = "missing"
STATE_REMOVED = "removed_in_moodle"
VALID_STATES = {STATE_PRESENT, STATE_MISSING, STATE_REMOVED}

_SCHEMA = """
CREATE TABLE sites (
    id            INTEGER PRIMARY KEY,
    site_url      TEXT    NOT NULL,
    user_id       INTEGER NOT NULL,
    academic_year TEXT    NOT NULL,
    UNIQUE (site_url, user_id)
);
CREATE TABLE courses (
    site_id   INTEGER NOT NULL REFERENCES sites (id),
    course_id INTEGER NOT NULL,
    alias     TEXT    NOT NULL,
    folder    TEXT    NOT NULL,
    PRIMARY KEY (site_id, course_id)
);
CREATE TABLE sections (
    site_id     INTEGER NOT NULL,
    course_id   INTEGER NOT NULL,
    section_id  INTEGER NOT NULL,
    folder_name TEXT    NOT NULL,
    PRIMARY KEY (site_id, course_id, section_id),
    FOREIGN KEY (site_id, course_id) REFERENCES courses (site_id, course_id)
);
CREATE TABLE files (
    id           INTEGER PRIMARY KEY,
    site_id      INTEGER NOT NULL,
    course_id    INTEGER NOT NULL,
    cmid         INTEGER NOT NULL,
    filearea     TEXT    NOT NULL,
    filepath     TEXT    NOT NULL,
    filename     TEXT    NOT NULL,
    state        TEXT    NOT NULL CHECK (state IN ('present', 'missing', 'removed_in_moodle')),
    last_checked TEXT    NOT NULL,
    UNIQUE (site_id, cmid, filearea, filepath, filename),
    FOREIGN KEY (site_id, course_id) REFERENCES courses (site_id, course_id)
);
CREATE TABLE file_versions (
    file_id       INTEGER NOT NULL REFERENCES files (id),
    version       INTEGER NOT NULL,
    relative_path TEXT    NOT NULL,
    timemodified  INTEGER NOT NULL,
    filesize      INTEGER NOT NULL,
    sha256        TEXT    NOT NULL,
    downloaded_at TEXT    NOT NULL,
    PRIMARY KEY (file_id, version)
);
"""

_LATEST_VERSION = """
SELECT v.* FROM file_versions AS v
WHERE v.file_id = ? ORDER BY v.version DESC LIMIT 1
"""


class RegistryError(Exception):
    """The database cannot be used (for example, created by a newer version)."""


class FileStatus(enum.Enum):
    """Result of comparing a Moodle file with the registry."""

    NEW = "new"
    UNCHANGED = "unchanged"
    CHANGED = "changed"


@dataclass(frozen=True)
class FileKey:
    """Stable identity of a Moodle file inside one site (design.md section 6)."""

    cmid: int
    filearea: str
    filepath: str
    filename: str


def _now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    result = datetime.now(UTC).isoformat(timespec="seconds")
    return result


class Registry:
    """Connection to the registry database. Use it with 'with Registry(path) as r:'."""

    def __init__(self, path: Path) -> None:
        """Open (and create if needed) the database at path.

        Raises: sqlite3.Error if the file cannot be opened;
        RegistryError if it was created by a newer schema version.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        try:
            self._create_or_check_schema()
        except RegistryError:
            # Do not leave the file open (Windows would keep it locked).
            self._conn.close()
            raise

    def __enter__(self) -> Self:
        """Allow 'with Registry(path) as registry:'."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the connection when the 'with' block ends."""
        self.close()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def _create_or_check_schema(self) -> None:
        """Create the tables on a new database, or check the schema version.

        Raises: RegistryError if the database comes from a newer version.
        """
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            with self._conn:
                self._conn.executescript(_SCHEMA)
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif version > SCHEMA_VERSION:
            raise RegistryError(f"Registry schema {version} is newer than {SCHEMA_VERSION}")

    # --- sites, courses and sections -------------------------------------

    def get_or_create_site(self, site_url: str, user_id: int, academic_year: str) -> int:
        """Return the id of a site, creating it on first use."""
        with self._conn:
            self._conn.execute(
                "INSERT INTO sites (site_url, user_id, academic_year) VALUES (?, ?, ?) "
                "ON CONFLICT (site_url, user_id) DO NOTHING",
                (site_url, user_id, academic_year),
            )
        row = self._conn.execute(
            "SELECT id FROM sites WHERE site_url = ? AND user_id = ?", (site_url, user_id)
        ).fetchone()
        result = row["id"]
        return result

    def ensure_course(self, site_id: int, course_id: int, alias: str, folder: str) -> dict:
        """Return the course row, creating it with alias and folder if it is new.

        An existing course keeps its alias and folder: use rename_course_folder().
        """
        with self._conn:
            self._conn.execute(
                "INSERT INTO courses (site_id, course_id, alias, folder) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (site_id, course_id) DO NOTHING",
                (site_id, course_id, alias, folder),
            )
        row = self._conn.execute(
            "SELECT * FROM courses WHERE site_id = ? AND course_id = ?", (site_id, course_id)
        ).fetchone()
        result = dict(row)
        return result

    def rename_course_folder(
        self, site_id: int, course_id: int, new_alias: str, new_folder: str
    ) -> None:
        """Change a course alias and folder and every stored path, in one transaction (RF4).

        Raises: KeyError if the course is not registered.
        """
        row = self._conn.execute(
            "SELECT folder FROM courses WHERE site_id = ? AND course_id = ?", (site_id, course_id)
        ).fetchone()
        if row is None:
            raise KeyError(f"Course {course_id} is not registered")
        old_prefix = row["folder"] + "/"
        with self._conn:
            self._conn.execute(
                "UPDATE courses SET alias = ?, folder = ? WHERE site_id = ? AND course_id = ?",
                (new_alias, new_folder, site_id, course_id),
            )
            self._conn.execute(
                "UPDATE file_versions SET relative_path = ? || substr(relative_path, ?) "
                "WHERE substr(relative_path, 1, ?) = ? AND file_id IN "
                "(SELECT id FROM files WHERE site_id = ? AND course_id = ?)",
                (
                    new_folder + "/",
                    len(old_prefix) + 1,
                    len(old_prefix),
                    old_prefix,
                    site_id,
                    course_id,
                ),
            )

    def section_folder(self, site_id: int, course_id: int, section_id: int, proposed: str) -> str:
        """Return the stored folder of a section, or store and return proposed.

        Once created, a section folder never changes, even if the teacher
        renames or reorders sections (design.md section 6).
        """
        with self._conn:
            self._conn.execute(
                "INSERT INTO sections (site_id, course_id, section_id, folder_name) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (site_id, course_id, section_id) DO NOTHING",
                (site_id, course_id, section_id, proposed),
            )
        row = self._conn.execute(
            "SELECT folder_name FROM sections WHERE site_id = ? AND course_id = ? "
            "AND section_id = ?",
            (site_id, course_id, section_id),
        ).fetchone()
        result = row["folder_name"]
        return result

    # --- files and versions ----------------------------------------------

    def _file_id(self, site_id: int, key: FileKey) -> int | None:
        """Return the id of a registered file, or None."""
        row = self._conn.execute(
            "SELECT id FROM files WHERE site_id = ? AND cmid = ? AND filearea = ? "
            "AND filepath = ? AND filename = ?",
            (site_id, key.cmid, key.filearea, key.filepath, key.filename),
        ).fetchone()
        result = None if row is None else row["id"]
        return result

    def latest_version(self, site_id: int, key: FileKey) -> dict | None:
        """Return the newest version row of a file, or None if never downloaded."""
        result = None
        file_id = self._file_id(site_id, key)
        if file_id is not None:
            row = self._conn.execute(_LATEST_VERSION, (file_id,)).fetchone()
            result = None if row is None else dict(row)
        return result

    def classify(self, site_id: int, key: FileKey, timemodified: int, filesize: int) -> FileStatus:
        """Compare a Moodle file with the registry: NEW, UNCHANGED or CHANGED.

        A new version is detected when timemodified OR filesize differs.
        """
        latest = self.latest_version(site_id, key)
        if latest is None:
            result = FileStatus.NEW
        elif latest["timemodified"] != timemodified or latest["filesize"] != filesize:
            result = FileStatus.CHANGED
        else:
            result = FileStatus.UNCHANGED
        return result

    def record_download(
        self,
        site_id: int,
        course_id: int,
        key: FileKey,
        version: dict,
    ) -> bool:
        """Store a finished download; return True if it created a new version.

        version holds relative_path, timemodified, filesize and sha256. If the
        SHA-256 equals the latest version, no version is added: only its
        date and size are updated (design.md section 6).
        """
        created = False
        now = _now()
        with self._conn:
            self._conn.execute(
                "INSERT INTO files (site_id, course_id, cmid, filearea, filepath, filename, "
                "state, last_checked) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (site_id, cmid, filearea, filepath, filename) "
                "DO UPDATE SET course_id = excluded.course_id, state = excluded.state, "
                "last_checked = excluded.last_checked",
                (
                    site_id,
                    course_id,
                    key.cmid,
                    key.filearea,
                    key.filepath,
                    key.filename,
                    STATE_PRESENT,
                    now,
                ),
            )
            file_id = self._file_id(site_id, key)
            latest = self._conn.execute(_LATEST_VERSION, (file_id,)).fetchone()
            if latest is not None and latest["sha256"] == version["sha256"]:
                self._conn.execute(
                    "UPDATE file_versions SET timemodified = ?, filesize = ? "
                    "WHERE file_id = ? AND version = ?",
                    (version["timemodified"], version["filesize"], file_id, latest["version"]),
                )
            else:
                number = 1 if latest is None else latest["version"] + 1
                self._conn.execute(
                    "INSERT INTO file_versions (file_id, version, relative_path, timemodified, "
                    "filesize, sha256, downloaded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        file_id,
                        number,
                        version["relative_path"],
                        version["timemodified"],
                        version["filesize"],
                        version["sha256"],
                        now,
                    ),
                )
                created = True
        return created

    def mark_seen(self, site_id: int, key: FileKey) -> None:
        """Update last_checked; a file back in Moodle stops being 'removed_in_moodle'."""
        with self._conn:
            self._conn.execute(
                "UPDATE files SET last_checked = ?, "
                "state = CASE WHEN state = ? THEN ? ELSE state END "
                "WHERE site_id = ? AND cmid = ? AND filearea = ? AND filepath = ? AND filename = ?",
                (
                    _now(),
                    STATE_REMOVED,
                    STATE_PRESENT,
                    site_id,
                    key.cmid,
                    key.filearea,
                    key.filepath,
                    key.filename,
                ),
            )

    def set_state(self, site_id: int, key: FileKey, state: str) -> None:
        """Set the state of a file: present, missing or removed_in_moodle.

        Raises: ValueError if state is not one of the valid states.
        """
        if state not in VALID_STATES:
            raise ValueError(f"Invalid state: {state}")
        with self._conn:
            self._conn.execute(
                "UPDATE files SET state = ? WHERE site_id = ? AND cmid = ? AND filearea = ? "
                "AND filepath = ? AND filename = ?",
                (state, site_id, key.cmid, key.filearea, key.filepath, key.filename),
            )

    def mark_removed(self, site_id: int, course_id: int, seen: set[FileKey]) -> int:
        """Mark files of a course not in seen as removed_in_moodle; return how many.

        Nothing is ever deleted locally (design.md section 6).
        """
        rows = self._conn.execute(
            "SELECT id, cmid, filearea, filepath, filename FROM files "
            "WHERE site_id = ? AND course_id = ? AND state != ?",
            (site_id, course_id, STATE_REMOVED),
        ).fetchall()
        removed_ids = [
            (row["id"],)
            for row in rows
            if FileKey(row["cmid"], row["filearea"], row["filepath"], row["filename"]) not in seen
        ]
        with self._conn:
            self._conn.executemany(
                f"UPDATE files SET state = '{STATE_REMOVED}' WHERE id = ?", removed_ids
            )
        result = len(removed_ids)
        return result

    def files_of_course(self, site_id: int, course_id: int) -> list[dict]:
        """Return every file of a course with its state and latest version (RF8, RF11)."""
        rows = self._conn.execute(
            "SELECT f.cmid, f.filearea, f.filepath, f.filename, f.state, v.version, "
            "v.relative_path, v.timemodified, v.filesize, v.sha256 FROM files AS f "
            "LEFT JOIN file_versions AS v ON v.file_id = f.id AND v.version = "
            "(SELECT MAX(version) FROM file_versions WHERE file_id = f.id) "
            "WHERE f.site_id = ? AND f.course_id = ? ORDER BY f.id",
            (site_id, course_id),
        ).fetchall()
        result = [dict(row) for row in rows]
        return result
