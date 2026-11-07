"""User settings stored as JSON in the app data folder (never any secret).

The token lives only in the keyring (CLAUDE.md section 4); this file holds
the site, the user id, the destination folder and the course selection.
"""

import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from aulari import organizer

CONFIG_NAME = "config.json"
DEFAULT_HOST = "https://e-aules.uab.cat"


def default_site_url(now: datetime | None = None) -> str:
    """Return the e-aules URL of the current academic year (UAB by default, RF1, D6)."""
    moment = now if now is not None else datetime.now(UTC)
    year = organizer.academic_year_from_startdate(int(moment.timestamp()))
    result = f"{DEFAULT_HOST}/{year}"
    return result


@dataclass
class Settings:
    """Everything the app remembers between runs."""

    site_url: str = field(default_factory=default_site_url)
    user_id: int = 0
    destination: str = ""
    selected_courses: list[int] = field(default_factory=list)
    known_courses: list[int] = field(default_factory=list)
    first_run_notice_seen: bool = False
    language: str = ""  # '' = follow the system language
    auto_sync_hours: int = 0  # 0 = automatic sync off (D7)
    last_background_sync: float = 0.0  # Unix time of the last background run
    # Other academic year sites (RF19): dicts with site_url, user_id,
    # selected_courses and known_courses. The active site uses the fields above.
    other_sites: list[dict] = field(default_factory=list)


def _int_list(value: object) -> list[int] | None:
    """Return value as a list of ints, or None if it is not one."""
    result = None
    if isinstance(value, list) and all(isinstance(item, int) for item in value):
        result = list(value)
    return result


def site_from_dict(data: object) -> dict | None:
    """Return a clean saved-site dict, or None if data is not a valid one."""
    result = None
    if isinstance(data, dict) and isinstance(data.get("site_url"), str) and data["site_url"]:
        user_id = data.get("user_id")
        result = {
            "site_url": data["site_url"],
            "user_id": user_id if isinstance(user_id, int) else 0,
            "selected_courses": _int_list(data.get("selected_courses")) or [],
            "known_courses": _int_list(data.get("known_courses")) or [],
        }
    return result


def settings_from_dict(data: dict) -> Settings:
    """Build Settings from parsed JSON, keeping defaults for missing or wrong values."""
    result = Settings()
    if isinstance(data.get("site_url"), str) and data["site_url"]:
        result.site_url = data["site_url"]
    if isinstance(data.get("user_id"), int):
        result.user_id = data["user_id"]
    if isinstance(data.get("destination"), str):
        result.destination = data["destination"]
    selected = _int_list(data.get("selected_courses"))
    if selected is not None:
        result.selected_courses = selected
    known = _int_list(data.get("known_courses"))
    if known is not None:
        result.known_courses = known
    if isinstance(data.get("first_run_notice_seen"), bool):
        result.first_run_notice_seen = data["first_run_notice_seen"]
    if isinstance(data.get("language"), str):
        result.language = data["language"]
    if isinstance(data.get("auto_sync_hours"), int):
        result.auto_sync_hours = data["auto_sync_hours"]
    if isinstance(data.get("last_background_sync"), (int, float)):
        result.last_background_sync = float(data["last_background_sync"])
    if isinstance(data.get("other_sites"), list):
        sites = [site_from_dict(item) for item in data["other_sites"]]
        result.other_sites = [site for site in sites if site is not None]
    return result


def load_settings(app_dir: Path) -> Settings:
    """Return the saved settings, or defaults if there is no config file yet.

    Raises: json.JSONDecodeError if the file is corrupt; OSError on read errors.
    """
    result = Settings()
    path = app_dir / CONFIG_NAME
    if path.is_file():
        with path.open(encoding="utf-8") as config_file:
            data = json.load(config_file)
        if isinstance(data, dict):
            result = settings_from_dict(data)
    return result


def save_settings(app_dir: Path, settings: Settings) -> None:
    """Save settings atomically (temporary file + replace).

    Raises: OSError on write errors (the previous file is kept intact).
    """
    app_dir.mkdir(parents=True, exist_ok=True)
    path = app_dir / CONFIG_NAME
    temp = app_dir / f".{CONFIG_NAME}.{secrets.token_hex(4)}.part"
    try:
        with temp.open("w", encoding="utf-8") as config_file:
            json.dump(asdict(settings), config_file, indent=2, ensure_ascii=False)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
