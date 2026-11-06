"""Novedades.txt: what each sync added or updated, newest first (RF14).

The file lives in <destination>/<academic year>/. Each sync with changes
adds a dated block at the top; older blocks are kept below, up to
MAX_LINES lines in total. A sync without changes leaves the file alone.
"""

from datetime import datetime
from pathlib import Path

from aulari.calendar_export import write_atomic
from aulari.sync import ChangedFile
from aulari.texts import text

NEWS_FILENAME = "Novedades.txt"
MAX_LINES = 2000
PATH_PREFIX_PARTS = 2  # '<academic year>/<course alias>'
DATE_FORMAT = "%Y-%m-%d %H:%M"


def news_path(destination: Path, academic_year: str) -> Path:
    """Return <destination>/<academic year>/Novedades.txt."""
    result = destination / academic_year / NEWS_FILENAME
    return result


def change_line(change: ChangedFile) -> str:
    """Return '[SO] 02 - TEORIA/Tema 5.pdf', plus a tag for new versions."""
    parts = change.relative_path.split("/")
    if len(parts) > PATH_PREFIX_PARTS:
        result = f"[{parts[1]}] {'/'.join(parts[PATH_PREFIX_PARTS:])}"
    else:
        result = change.relative_path
    if change.is_new_version:
        result = f"{result} {text('change_new_version_tag')}"
    return result


def news_block(changes: list[ChangedFile], moment: datetime) -> list[str]:
    """Return the lines of one dated block (header, files, blank line)."""
    header = text("news_header", date=moment.strftime(DATE_FORMAT), count=len(changes))
    lines = [header]
    lines.extend([f"  {change_line(change)}" for change in changes])
    lines.append("")
    return lines


def write_news(path: Path, changes: list[ChangedFile], moment: datetime) -> bool:
    """Add a block for this sync at the top of the file; return True if written.

    Raises: OSError on read or write errors.
    """
    written = False
    if changes:
        previous = []
        if path.is_file():
            previous = path.read_text(encoding="utf-8").splitlines()
        lines = (news_block(changes, moment) + previous)[:MAX_LINES]
        write_atomic(path, "\n".join(lines) + "\n")
        written = True
    return written
