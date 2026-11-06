"""Write synced items to disk safely (docs/design.md sections 5 and 6).

- Paths: every component is sanitized and the final path must stay inside
  the destination; total length is limited to MAX_PATH_LENGTH.
- Atomic download: write to <destination>/.campus-sync-tmp/, check the size,
  then move to the final name without ever overwriting an existing file.
- Mark-of-the-Web: downloads get a Zone.Identifier stream (Windows, NTFS)
  so that Office opens them in Protected View.
- Links are written as .url shortcuts with the exact safe format.
- Existing files are only checked by name and size, never read, so cloud
  placeholders (Proton Drive, OneDrive) are not downloaded by accident.
The app never opens or runs what it downloads.
"""

import hashlib
import logging
import secrets
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from campus_sync import organizer
from campus_sync.moodle_client import MoodleClient
from campus_sync.moodle_items import KIND_URL, SyncItem

TEMP_DIR_NAME = ".campus-sync-tmp"
TEMP_SUFFIX = ".part"
MAX_PATH_LENGTH = 240
MIN_FILENAME_LENGTH = 20
URL_SUFFIX = ".url"
ZONE_IDENTIFIER = "[ZoneTransfer]\r\nZoneId=3\r\n"  # 3 = Internet zone
# Characters kept as they are when percent-encoding a URL for a .url file.
URL_SAFE_CHARS = ":/?#[]@!$&'()*+,;=%~"

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    """A download finished but its result is not valid (for example, wrong size)."""


def item_relative_dir(course_folder: str, section_folder: str, item: SyncItem) -> PurePosixPath:
    """Return the folder of an item relative to the destination, sanitized.

    <course folder>/<section folder>/[subfolder]/[filepath parts]

    Raises: ValueError if a Moodle name has no usable characters.
    """
    result = PurePosixPath(course_folder) / section_folder
    if item.subfolder:
        result = result / organizer.sanitize_filename(item.subfolder)
    for part in item.key.filepath.split("/"):
        if part:
            result = result / organizer.sanitize_filename(part)
    return result


def item_filename(item: SyncItem) -> str:
    """Return the file name of an item before sanitizing ('.url' added for links)."""
    result = item.key.filename
    if item.kind == KIND_URL:
        result = result + URL_SUFFIX
    return result


def plan_target(destination: Path, relative_dir: PurePosixPath, filename: str) -> Path:
    """Return the absolute target path for a file, sanitized and inside destination.

    The file name is shortened (with a hash) if the full path would exceed
    MAX_PATH_LENGTH characters.

    Raises: ValueError if the folder alone is too long, a name is unusable,
    or the path escapes the destination.
    """
    folder = destination.resolve() / relative_dir
    available = MAX_PATH_LENGTH - len(str(folder)) - 1
    if available < MIN_FILENAME_LENGTH:
        raise ValueError(f"Folder path is too long: {relative_dir}")
    max_length = min(organizer.FILE_MAX_LENGTH, available)
    name = organizer.sanitize_filename(filename, max_length)
    result = organizer.ensure_inside(destination, folder / name)
    return result


def _existing_names(folder: Path) -> set[str]:
    """Return the names in folder (empty if it does not exist yet)."""
    result = set()
    if folder.is_dir():
        result = {entry.name for entry in folder.iterdir()}
    return result


def free_unique_path(path: Path) -> Path:
    """Return path, or 'name (2).ext', 'name (3).ext'... if it is taken."""
    name = organizer.unique_name(path.name, _existing_names(path.parent))
    result = path.with_name(name)
    return result


def free_versioned_path(path: Path, timemodified: int) -> Path:
    """Return a free name for a new version: 'Name (YYYY-MM-DD).ext' (RF7).

    If that is taken, 'Name (YYYY-MM-DD HHMM).ext', and then ' (2)', ' (3)'...
    Dates use Europe/Madrid time.
    """
    moment = organizer.to_local_time(timemodified)
    existing = _existing_names(path.parent)
    folded = {name.casefold() for name in existing}
    name = organizer.versioned_filename(path.name, moment)
    if name.casefold() in folded:
        name = organizer.versioned_filename(path.name, moment, include_time=True)
    result = path.with_name(organizer.unique_name(name, existing))
    return result


def adoptable(path: Path, expected_size: int) -> bool:
    """Return True if a file already at path has the expected size (RF11).

    Only metadata is read, never the content (cloud placeholders stay online).
    """
    result = path.is_file() and path.stat().st_size == expected_size
    return result


def mark_downloaded_from_internet(path: Path) -> None:
    """Add the Mark-of-the-Web (Zone.Identifier stream) on Windows.

    Some file systems (FAT, exFAT, some cloud folders) have no alternate
    data streams: then a warning is logged and the download is still valid.
    """
    if sys.platform == "win32":
        stream = Path(f"{path}:Zone.Identifier")
        try:
            with stream.open("w", encoding="ascii", newline="") as zone_file:
                zone_file.write(ZONE_IDENTIFIER)
        except OSError as error:
            logger.warning("Could not add Mark-of-the-Web to %s: %s", path.name, error)


def _temp_path(destination: Path) -> Path:
    """Return a new, unused temporary file path inside the destination volume."""
    temp_dir = destination / TEMP_DIR_NAME
    temp_dir.mkdir(parents=True, exist_ok=True)
    result = temp_dir / (secrets.token_hex(8) + TEMP_SUFFIX)
    return result


def _move_exclusive(source: Path, target: Path) -> None:
    """Move source to target, failing if target exists (never overwrite).

    On Windows, rename already refuses to replace an existing file; the
    explicit check also covers other systems.

    Raises: FileExistsError if target exists; OSError on other errors.
    """
    if target.exists():
        raise FileExistsError(f"Target already exists: {target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)


def download_atomic(
    client: MoodleClient,
    item: SyncItem,
    destination: Path,
    target: Path,
    skip_if_sha256: str = "",
) -> tuple[int, str, bool]:
    """Download an item to target through a temporary file.

    Returns (size, SHA-256, moved). If the content's SHA-256 equals
    skip_if_sha256 (same content as the previous version), the file is not
    moved and moved is False (design.md section 6). The temporary file is
    always removed, also on errors.

    Raises: DownloadError if the size differs from Moodle's; FileExistsError
    if target appeared meanwhile; plus everything MoodleClient.download raises.
    """
    moved = False
    temp = _temp_path(destination)
    try:
        size, sha256 = client.download(item.url, temp)
        if item.filesize and size != item.filesize:
            raise DownloadError(f"Size mismatch for {target.name}: {size} != {item.filesize}")
        if sha256 != skip_if_sha256:
            _move_exclusive(temp, target)
            moved = True
    finally:
        temp.unlink(missing_ok=True)
    if moved:
        mark_downloaded_from_internet(target)
    result = (size, sha256, moved)
    return result


def write_url_shortcut(item: SyncItem, target: Path) -> str:
    """Write a .url shortcut for a link item; return the SHA-256 of its text.

    Non-ASCII characters in the URL are percent-encoded so the file is plain
    ASCII. The file is created exclusively (never overwritten).

    Raises: ValueError for unsafe URLs (see organizer.url_shortcut_content);
    FileExistsError if target exists; OSError on write errors.
    """
    content = url_shortcut_text(item)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="ascii", newline="") as url_file:
        url_file.write(content)
    mark_downloaded_from_internet(target)
    result = sha256_text(content)
    return result


def url_shortcut_text(item: SyncItem) -> str:
    """Return the exact .url text for a link item (non-ASCII percent-encoded).

    Raises: ValueError for unsafe URLs (see organizer.url_shortcut_content).
    """
    safe_url = quote(item.url, safe=URL_SAFE_CHARS)
    result = organizer.url_shortcut_content(safe_url)
    return result


def sha256_text(text: str) -> str:
    """Return the SHA-256 hex digest of an ASCII text."""
    result = hashlib.sha256(text.encode("ascii")).hexdigest()
    return result


def clean_temp_dir(destination: Path) -> int:
    """Delete leftover temporary files from an interrupted sync; return how many.

    Raises: OSError if a file cannot be deleted.
    """
    count = 0
    temp_dir = destination / TEMP_DIR_NAME
    if temp_dir.is_dir():
        for leftover in temp_dir.glob("*" + TEMP_SUFFIX):
            leftover.unlink()
            count = count + 1
    return count
