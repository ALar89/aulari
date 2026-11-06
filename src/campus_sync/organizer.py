"""Folder and file names: sanitizing, aliases, academic years and versions.

Pure functions only (no network, no disk writes), so they are easy to test.
Rules come from docs/design.md section 5, RF6, RF7 and decisions D4/D6.
"""

import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

SECTION_MAX_LENGTH = 60
FILE_MAX_LENGTH = 100
HASH_LENGTH = 8
MAX_EXTENSION_LENGTH = 10
REPLACEMENT_CHAR = "_"
NO_ACADEMIC_YEAR = "Sin curso"
ACADEMIC_YEAR_START_MONTH = 9  # 1 September

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)

# Noise found in e-aules course names (design.md section 10).
_COURSE_CODE = re.compile(r"\[[A-Z]{1,4}\d+\]")
_YEAR_IN_PARENTHESES = re.compile(r"\(\d{4}-\d{2}\)")
_TRAILING_DASH_YEAR = re.compile(r"\s+-\s+[^-]*\b\d{2}-\d{2}\s*$")
_TRAILING_YEAR = re.compile(r"\s+\d{2}-\d{2}\s*$")

_WORD_SEPARATORS = re.compile(r"[\s\-–—,;:/()]+")
_ELIDED_ARTICLE = re.compile(r"^[ldLD]['’]")
_STOP_WORDS = {
    "de", "del", "dels", "la", "el", "els", "les", "los", "las", "lo",
    "a", "al", "als", "en", "per", "amb", "con", "para", "por", "the", "of", "and",
}  # fmt: skip
_CONNECTORS = {"i", "y"}
_ROMAN_NUMERALS = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5,
    "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
}  # fmt: skip
_SITE_YEAR = re.compile(r"/(\d{4})-(\d{2})(?:/|$)")


def _strip_accents(text: str) -> str:
    """Return text without diacritics ('Càlcul' -> 'Calcul')."""
    decomposed = unicodedata.normalize("NFD", text)
    result = "".join([char for char in decomposed if not unicodedata.combining(char)])
    return result


def _split_extension(name: str) -> tuple[str, str]:
    """Split 'notes.pdf' into ('notes', '.pdf'); long or missing suffixes stay in the stem."""
    suffix = Path(name).suffix
    result = (name, "")
    if suffix and len(suffix) <= MAX_EXTENSION_LENGTH and len(suffix) < len(name):
        result = (name[: -len(suffix)], suffix)
    return result


def _truncate_with_hash(name: str, max_length: int) -> str:
    """Shorten name to max_length, keeping its extension and adding a short hash.

    The hash comes from the full name, so two long names that share a
    prefix still get different results.
    """
    result = name
    if len(name) > max_length:
        stem, suffix = _split_extension(name)
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:HASH_LENGTH]
        keep = max_length - len(suffix) - HASH_LENGTH - 1
        result = f"{stem[:keep].rstrip(' .')}~{digest}{suffix}"
    return result


def sanitize_filename(name: str, max_length: int = FILE_MAX_LENGTH) -> str:
    """Return a safe Windows file or folder name built from a Moodle name.

    Normalizes to NFC, replaces forbidden and control characters, strips
    surrounding spaces and trailing dots, avoids reserved device names and
    truncates with a short hash.

    Raises: ValueError if nothing usable is left (for example '..' or '').
    """
    result = unicodedata.normalize("NFC", name)
    result = _INVALID_CHARS.sub(REPLACEMENT_CHAR, result)
    result = result.strip().rstrip(". ")
    if not result:
        raise ValueError(f"Name has no usable characters: {name!r}")
    stem = result.split(".")[0].strip().upper()
    if stem in _RESERVED_NAMES:
        result = REPLACEMENT_CHAR + result
    result = _truncate_with_hash(result, max_length)
    return result


def ensure_inside(destination: Path, candidate: Path) -> Path:
    """Return candidate resolved, after checking it stays inside destination.

    Raises: ValueError if the resolved path escapes the destination folder.
    """
    root = destination.resolve()
    result = candidate.resolve()
    if not result.is_relative_to(root):
        raise ValueError(f"Path escapes the destination folder: {result}")
    return result


def unique_name(name: str, existing: set[str], keep_extension: bool = True) -> str:
    """Return name, or 'name (2)', 'name (3)'... if it collides with existing.

    Comparison ignores case, as Windows does. Use keep_extension=False for
    folders, so that 'Tema 1. Intro' is not split at the dot.
    """
    folded = {item.casefold() for item in existing}
    stem, suffix = (name, "")
    if keep_extension:
        stem, suffix = _split_extension(name)
    result = name
    counter = 2
    while result.casefold() in folded:
        result = f"{stem} ({counter}){suffix}"
        counter = counter + 1
    return result


def clean_course_name(fullname: str) -> str:
    """Remove course codes and academic year suffixes from a course name.

    'Sistemes Operatius - Grau Enginyeria Informàtica 26-27 [MO86824]'
    becomes 'Sistemes Operatius'.
    """
    result = _COURSE_CODE.sub(" ", fullname)
    result = _YEAR_IN_PARENTHESES.sub(" ", result)
    result = " ".join(result.split())
    result = _TRAILING_DASH_YEAR.sub("", result)
    result = _TRAILING_YEAR.sub("", result)
    result = result.strip(" -")
    return result


def _significant_words(name: str) -> list[str]:
    """Return the words of name without elided articles and stop words."""
    words = []
    for raw_word in _WORD_SEPARATORS.split(name):
        word = _ELIDED_ARTICLE.sub("", raw_word)
        if word and word.lower() not in _STOP_WORDS:
            words.append(word)
    return words


def _word_to_alias_part(word: str) -> str:
    """Return the alias fragment for one significant word (D4 rules)."""
    if word in _CONNECTORS:
        result = word
    elif word in _ROMAN_NUMERALS:
        result = str(_ROMAN_NUMERALS[word])
    elif word.isdigit():
        result = word
    elif word.isupper() and len(word) > 1:
        result = _strip_accents(word)
    else:
        result = _strip_accents(word[0]).upper()
    return result


def course_alias(fullname: str) -> str:
    """Return a short folder alias for a course name.

    Initials of the significant words, numbers and Roman numerals as digits,
    lowercase connectors 'i'/'y' (D4), uppercase acronyms kept whole.
    A name with a single significant word keeps that word.
    'Camps i Ones Electromagnètiques' -> 'CiOE', 'Càlcul II' -> 'C2'.
    """
    cleaned = clean_course_name(fullname)
    words = _significant_words(cleaned)
    if len(words) == 1:
        result = words[0]
    else:
        result = "".join([_word_to_alias_part(word) for word in words])
    if not result:
        result = cleaned
    return result


def academic_year_from_site(site_url: str) -> str | None:
    """Return '2026-27' if the site URL path contains a valid academic year (D6)."""
    result = None
    match = _SITE_YEAR.search(urlparse(site_url).path)
    if match is not None:
        first_year = int(match.group(1))
        if (first_year + 1) % 100 == int(match.group(2)):
            result = f"{first_year}-{match.group(2)}"
    return result


def academic_year_from_startdate(startdate: int) -> str:
    """Return the academic year of a course start date (cutoff 1 September, UTC).

    Fallback rule from design.md section 5; 0 means 'no date'.
    """
    result = NO_ACADEMIC_YEAR
    if startdate:
        start = datetime.fromtimestamp(startdate, UTC)
        first_year = start.year
        if start.month < ACADEMIC_YEAR_START_MONTH:
            first_year = first_year - 1
        result = f"{first_year}-{(first_year + 1) % 100:02d}"
    return result


def section_folder(number: int, name: str) -> str:
    """Return the folder name for a course section: '00 - General', '03 - Tema 3'.

    Raises: ValueError if the result has no usable characters.
    """
    label = "General" if number == 0 else name.strip()
    folder = f"{number:02d} - {label}" if label else f"{number:02d}"
    result = sanitize_filename(folder, SECTION_MAX_LENGTH)
    return result


def versioned_filename(name: str, local_moment: datetime, include_time: bool = False) -> str:
    """Return 'Name (YYYY-MM-DD).ext', or 'Name (YYYY-MM-DD HHMM).ext' (RF7).

    local_moment must already be in Europe/Madrid time; the caller converts it.
    """
    stem, suffix = _split_extension(name)
    pattern = "%Y-%m-%d %H%M" if include_time else "%Y-%m-%d"
    result = f"{stem} ({local_moment.strftime(pattern)}){suffix}"
    return result


def url_shortcut_content(url: str) -> str:
    """Return the exact text of a Windows .url file for an external link.

    Raises: ValueError if the scheme is not http/https or the URL contains
    a line break (which could inject extra keys into the file).
    """
    if "\r" in url or "\n" in url:
        raise ValueError("URL contains a line break")
    if urlparse(url).scheme.lower() not in {"http", "https"}:
        raise ValueError("Only http and https links are allowed")
    result = f"[InternetShortcut]\r\nURL={url}\r\n"
    return result
