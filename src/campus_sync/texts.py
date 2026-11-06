"""User interface texts, loaded from campus_sync/strings/<language>.json.

The core and the interface never hard-code user-facing sentences: they ask
text(key, **values). Spanish first (design.md section 9); Catalan and
English later as more JSON files.
"""

import json
from importlib import resources

DEFAULT_LANGUAGE = "es"
_cache = {}


def load_strings(language: str = DEFAULT_LANGUAGE) -> dict[str, str]:
    """Return the texts of a language (read once and cached).

    Raises: FileNotFoundError if the language file does not exist;
    json.JSONDecodeError if it is corrupt.
    """
    if language not in _cache:
        source = resources.files("campus_sync") / "strings" / f"{language}.json"
        with source.open(encoding="utf-8") as strings_file:
            _cache[language] = json.load(strings_file)
    result = _cache[language]
    return result


def text(key: str, language: str = DEFAULT_LANGUAGE, **values: object) -> str:
    """Return the text for key with {placeholders} filled in.

    Raises: KeyError if the key or a placeholder value is missing.
    """
    result = load_strings(language)[key].format(**values)
    return result
