"""User interface texts, loaded from aulari/strings/<language>.json.

The core and the interface never hard-code user-facing sentences: they ask
text(key, **values). Available languages: Catalan, Spanish and English
(design.md section 9). The current language is chosen once at start (from
the settings or the system) and can be changed from the window.
"""

import json
import locale
from importlib import resources

DEFAULT_LANGUAGE = "es"
FALLBACK_LANGUAGE = "en"
# Language code -> name shown in the selector (each in its own language).
LANGUAGES = {"ca": "Català", "es": "Español", "en": "English"}
_cache = {}
_current = {"language": DEFAULT_LANGUAGE}


def load_strings(language: str = DEFAULT_LANGUAGE) -> dict[str, str]:
    """Return the texts of a language (read once and cached).

    Raises: FileNotFoundError if the language file does not exist;
    json.JSONDecodeError if it is corrupt.
    """
    if language not in _cache:
        source = resources.files("aulari") / "strings" / f"{language}.json"
        with source.open(encoding="utf-8") as strings_file:
            _cache[language] = json.load(strings_file)
    result = _cache[language]
    return result


def detect_language(locale_name: str | None = None) -> str:
    """Return 'ca', 'es' or 'en' from a locale name ('ca_ES', 'Catalan_Spain', ...).

    Without an argument the system locale is used; unknown locales give English.
    """
    name = locale_name if locale_name is not None else (locale.getlocale()[0] or "")
    lowered = name.lower()
    result = FALLBACK_LANGUAGE
    if lowered.startswith(("ca", "catalan")):
        result = "ca"
    elif lowered.startswith(("es", "spanish")):
        result = "es"
    return result


def set_language(language: str) -> str:
    """Make language the current one (unknown codes fall back); return the code used."""
    result = language if language in LANGUAGES else DEFAULT_LANGUAGE
    _current["language"] = result
    return result


def current_language() -> str:
    """Return the code of the current language."""
    result = _current["language"]
    return result


def text(key: str, language: str | None = None, **values: object) -> str:
    """Return the text for key, in language or the current one, with {placeholders} filled in.

    Raises: KeyError if the key or a placeholder value is missing.
    """
    code = language if language is not None else _current["language"]
    result = load_strings(code)[key].format(**values)
    return result
