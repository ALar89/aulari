"""Phase 1, steps 3-5: explore courses, contents and calendar export.

Reads the token stored by browser_login.py from the keyring and calls:
- core_webservice_get_site_info
- core_enrol_get_users_courses
- core_course_get_contents (for one course you choose)
- core_calendar_get_calendar_export_token (decision D3)

Only summaries are printed. Full responses are saved, with secrets
replaced by '***', to %LOCALAPPDATA%\\campus-sync\\recordings\\.

Usage (PowerShell, venv active, from the project folder):
    python poc\\explore_api.py https://<moodle-site-url> <userid>
"""

import json
import logging
import os
import sys
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import keyring
import keyring.errors
import requests
from moodle_ws import KEYRING_SERVICE, MoodleError, call_function, keyring_username

# Temporary until the package is installable: make src/ importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from campus_sync.log_filter import REDACTED, configure_logging, redact  # noqa: E402

EXPECTED_ARGS = 3  # script name + site URL + user id
# Keys whose values are secrets and must never reach a recording.
SECRET_KEYS = {"token", "wstoken", "privatetoken", "userprivateaccesskey"}
PLUGINFILE_MARKER = "/webservice/pluginfile.php/"

logger = logging.getLogger("explore_api")


def sanitize_for_recording(value: object) -> object:
    """Return a copy of a JSON value with secret keys and secret URL parts redacted."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in SECRET_KEYS:
                result[key] = REDACTED
            else:
                result[key] = sanitize_for_recording(item)
    elif isinstance(value, list):
        result = [sanitize_for_recording(item) for item in value]
    elif isinstance(value, str):
        result = redact(value)
    else:
        # Base case: numbers, booleans and None are copied as they are.
        result = value
    return result


def save_recording(site_url: str, function: str, data: object) -> Path:
    """Save a sanitized response and return the file path.

    Raises: KeyError if LOCALAPPDATA is not defined; OSError on write errors.
    """
    parsed = urlparse(site_url)
    site_label = f"{parsed.hostname}{parsed.path}".replace("/", "_")
    folder = Path(os.environ["LOCALAPPDATA"]) / "campus-sync" / "recordings"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    result = folder / f"{site_label}_{function}_{stamp}.json"
    with result.open("w", encoding="utf-8") as out_file:
        json.dump(sanitize_for_recording(data), out_file, indent=2, ensure_ascii=False)
    return result


def format_timestamp(timestamp: int) -> str:
    """Return a Unix timestamp as YYYY-MM-DD (UTC), or '-' if it is 0."""
    result = "-"
    if timestamp:
        result = datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d")
    return result


def show_site_info(site_url: str, token: str) -> None:
    """Call site_info, record it and print non-personal fields.

    Raises: MoodleError, requests.RequestException (from call_function).
    """
    data = call_function(site_url, token, "core_webservice_get_site_info", {})
    path = save_recording(site_url, "core_webservice_get_site_info", data)
    print(f"Site: {data.get('sitename')} | release {data.get('release')}")
    print(f"  saved: {path}")


def show_courses(site_url: str, token: str, user_id: int) -> None:
    """List the user's courses with id, short name and dates.

    Raises: MoodleError, requests.RequestException (from call_function).
    """
    courses = call_function(site_url, token, "core_enrol_get_users_courses", {"userid": user_id})
    path = save_recording(site_url, "core_enrol_get_users_courses", courses)
    print(f"\nCourses: {len(courses)}")
    print(f"  {'id':>7}  {'start':<10}  {'end':<10}  shortname / fullname")
    for course in courses:
        start = format_timestamp(course.get("startdate", 0))
        end = format_timestamp(course.get("enddate", 0))
        names = f"{course.get('shortname')} / {course.get('fullname')}"
        print(f"  {course.get('id'):>7}  {start:<10}  {end:<10}  {names}")
    print(f"  saved: {path}")


def summarize_contents(sections: list) -> None:
    """Print counts of sections, module types and files in a course."""
    module_types = Counter()
    content_types = Counter()
    file_count = 0
    total_bytes = 0
    pluginfile_urls = 0
    hidden_modules = 0
    for section in sections:
        for module in section.get("modules", []):
            module_types[module.get("modname", "?")] += 1
            if not module.get("uservisible", True):
                hidden_modules = hidden_modules + 1
            for content in module.get("contents", []):
                content_types[content.get("type", "?")] += 1
                if content.get("type") == "file":
                    file_count = file_count + 1
                    total_bytes = total_bytes + content.get("filesize", 0)
                    if PLUGINFILE_MARKER in content.get("fileurl", ""):
                        pluginfile_urls = pluginfile_urls + 1
    print(f"  sections        : {len(sections)}")
    print(f"  modules by type : {dict(module_types)}")
    print(f"  not uservisible : {hidden_modules}")
    print(f"  contents by type: {dict(content_types)}")
    print(f"  files           : {file_count} ({total_bytes / 1_000_000:.1f} MB)")
    print(f"  fileurl via webservice/pluginfile.php: {pluginfile_urls} of {file_count}")


def show_course_contents(site_url: str, token: str, course_id: int) -> None:
    """Fetch, record and summarize the contents of one course.

    Raises: MoodleError, requests.RequestException (from call_function).
    """
    sections = call_function(site_url, token, "core_course_get_contents", {"courseid": course_id})
    path = save_recording(site_url, f"core_course_get_contents_{course_id}", sections)
    print(f"\nContents of course {course_id}:")
    summarize_contents(sections)
    print(f"  saved: {path}")


def show_calendar_export(site_url: str, token: str) -> None:
    """Check whether the native calendar export token is available (D3).

    Raises: MoodleError, requests.RequestException (from call_function).
    """
    data = call_function(site_url, token, "core_calendar_get_calendar_export_token", {})
    path = save_recording(site_url, "core_calendar_get_calendar_export_token", data)
    export_token = data.get("token", "")
    print("\nCalendar export token: available")
    print(f"  length  : {len(export_token)}")
    print(f"  warnings: {data.get('warnings', [])}")
    print(f"  saved: {path}")


def run_step(description: str, step: Callable[..., None], *args: object) -> bool:
    """Run one exploration step; log Moodle/network errors and return success.

    A failing step does not stop the others.
    """
    success = False
    try:
        step(*args)
        success = True
    except MoodleError as error:
        logger.error("%s: Moodle error: %s", description, error)
    except requests.RequestException as error:
        logger.error("%s: network error: %s", description, error)
    return success


def ask_course_id() -> int | None:
    """Ask for a course id; return None if the user just presses Enter."""
    result = None
    answer = input("\nCourse id to inspect (Enter to skip): ").strip()
    if answer.isdigit():
        result = int(answer)
    elif answer:
        print("Not a number, skipping course contents.")
    return result


def explore(site_url: str, token: str, user_id: int) -> int:
    """Run every exploration step and return the exit code (0 if all worked)."""
    all_ok = run_step("site_info", show_site_info, site_url, token)
    if all_ok:
        courses_ok = run_step("courses", show_courses, site_url, token, user_id)
        course_id = ask_course_id()
        contents_ok = True
        if course_id is not None:
            contents_ok = run_step("contents", show_course_contents, site_url, token, course_id)
        calendar_ok = run_step("calendar export", show_calendar_export, site_url, token)
        all_ok = courses_ok and contents_ok and calendar_ok
    result = 0 if all_ok else 1
    return result


def main(argv: list[str]) -> int:
    """Read the token from the keyring, explore the API and return the exit code."""
    exit_code = 1
    configure_logging()
    if len(argv) != EXPECTED_ARGS or not argv[2].isdigit():
        print("Usage: python poc\\explore_api.py https://<moodle-site-url> <userid>")
    else:
        site_url = argv[1].rstrip("/")
        user_id = int(argv[2])
        username = keyring_username(site_url, user_id)
        try:
            token = keyring.get_password(KEYRING_SERVICE, username)
            if token is None:
                print(f"No token in keyring for {username!r}. Run browser_login.py first.")
            else:
                exit_code = explore(site_url, token, user_id)
        except keyring.errors.KeyringError as error:
            logger.error("Keyring error: %s", error)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
