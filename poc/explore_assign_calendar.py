"""Phase 1, last checks: calendar events (D3) and assignment attachments (RF5).

Reads the token from the keyring and calls:
- core_enrol_get_users_courses (to get every course id)
- core_calendar_get_calendar_events with all course ids, window
  today -30 days to +1 year (design.md section 8)
- mod_assign_get_assignments for one course you choose

Only summaries are printed. Full responses are saved, with secrets
replaced by '***', to %LOCALAPPDATA%\\campus-sync\\recordings\\.

Usage (PowerShell, venv active, from the project folder):
    python poc\\explore_assign_calendar.py https://<moodle-site-url> <userid>
"""

import logging
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import keyring
import keyring.errors
from explore_api import ask_course_id, format_timestamp, run_step, save_recording
from moodle_ws import KEYRING_SERVICE, call_function, keyring_username

# Temporary until the package is installable: make src/ importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from campus_sync.log_filter import configure_logging  # noqa: E402

EXPECTED_ARGS = 3  # script name + site URL + user id
DAYS_BEFORE = 30
DAYS_AFTER = 365
SAMPLE_SIZE = 5
PLUGINFILE_MARKER = "/webservice/pluginfile.php/"

logger = logging.getLogger("explore_assign_calendar")


def indexed_params(name: str, values: list[int]) -> dict:
    """Return Moodle array parameters: {'name[0]': v0, 'name[1]': v1, ...}."""
    result = {f"{name}[{index}]": value for index, value in enumerate(values)}
    return result


def get_course_ids(site_url: str, token: str, user_id: int) -> list[int]:
    """Return the ids of every course the user is enrolled in.

    Raises: MoodleError, requests.RequestException (from call_function).
    """
    courses = call_function(site_url, token, "core_enrol_get_users_courses", {"userid": user_id})
    result = [course["id"] for course in courses]
    return result


def calendar_params(course_ids: list[int]) -> dict:
    """Return the parameters for core_calendar_get_calendar_events."""
    now = datetime.now(UTC)
    result = indexed_params("events[courseids]", course_ids)
    result["options[userevents]"] = 1
    result["options[siteevents]"] = 1
    result["options[timestart]"] = int((now - timedelta(days=DAYS_BEFORE)).timestamp())
    result["options[timeend]"] = int((now + timedelta(days=DAYS_AFTER)).timestamp())
    return result


def summarize_events(events: list) -> None:
    """Print counts by type, module and course, plus a few sample events."""
    by_type = Counter(event.get("eventtype", "?") for event in events)
    by_module = Counter(event.get("modulename") or "-" for event in events)
    by_course = Counter(event.get("courseid", 0) for event in events)
    no_duration = len([event for event in events if not event.get("timeduration")])
    starts = sorted(event.get("timestart", 0) for event in events)
    print(f"  events          : {len(events)}")
    print(f"  by eventtype    : {dict(by_type)}")
    print(f"  by modulename   : {dict(by_module)}")
    print(f"  by courseid     : {dict(by_course)}")
    print(f"  timeduration = 0: {no_duration}")
    if starts:
        print(f"  first / last    : {format_timestamp(starts[0])} / {format_timestamp(starts[-1])}")
    print("  sample:")
    for event in events[:SAMPLE_SIZE]:
        date = format_timestamp(event.get("timestart", 0))
        print(f"    {date}  {event.get('eventtype')}  course {event.get('courseid')}")


def show_calendar_events(site_url: str, token: str, user_id: int) -> None:
    """Fetch, record and summarize calendar events for every enrolled course.

    Raises: MoodleError, requests.RequestException (from call_function).
    """
    course_ids = get_course_ids(site_url, token, user_id)
    params = calendar_params(course_ids)
    data = call_function(site_url, token, "core_calendar_get_calendar_events", params)
    path = save_recording(site_url, "core_calendar_get_calendar_events", data)
    print(f"\nCalendar events ({len(course_ids)} courses, -{DAYS_BEFORE} to +{DAYS_AFTER} days):")
    summarize_events(data.get("events", []))
    print(f"  warnings        : {len(data.get('warnings', []))}")
    print(f"  saved: {path}")


def summarize_assignments(assignments: list) -> None:
    """Print counts of intro attachments and embedded intro files."""
    with_attachments = 0
    attachment_count = 0
    attachment_bytes = 0
    pluginfile_urls = 0
    introfile_count = 0
    for assignment in assignments:
        attachments = assignment.get("introattachments", [])
        if attachments:
            with_attachments = with_attachments + 1
        introfile_count = introfile_count + len(assignment.get("introfiles", []))
        for attachment in attachments:
            attachment_count = attachment_count + 1
            attachment_bytes = attachment_bytes + attachment.get("filesize", 0)
            if PLUGINFILE_MARKER in attachment.get("fileurl", ""):
                pluginfile_urls = pluginfile_urls + 1
    print(f"  assignments              : {len(assignments)}")
    print(f"  with introattachments    : {with_attachments}")
    print(
        f"  introattachments (files) : {attachment_count} ({attachment_bytes / 1_000_000:.1f} MB)"
    )
    print(f"  via webservice/pluginfile: {pluginfile_urls} of {attachment_count}")
    print(f"  introfiles (ignored)     : {introfile_count}")


def show_assignments(site_url: str, token: str, course_id: int) -> None:
    """Fetch, record and summarize the assignments of one course.

    Raises: MoodleError, requests.RequestException (from call_function).
    """
    params = indexed_params("courseids", [course_id])
    data = call_function(site_url, token, "mod_assign_get_assignments", params)
    path = save_recording(site_url, f"mod_assign_get_assignments_{course_id}", data)
    print(f"\nAssignments of course {course_id}:")
    for course in data.get("courses", []):
        summarize_assignments(course.get("assignments", []))
    print(f"  warnings                 : {len(data.get('warnings', []))}")
    print(f"  saved: {path}")


def explore(site_url: str, token: str, user_id: int) -> int:
    """Run both checks and return the exit code (0 if both worked)."""
    calendar_ok = run_step("calendar events", show_calendar_events, site_url, token, user_id)
    assign_ok = True
    course_id = ask_course_id()
    if course_id is not None:
        assign_ok = run_step("assignments", show_assignments, site_url, token, course_id)
    result = 0 if calendar_ok and assign_ok else 1
    return result


def main(argv: list[str]) -> int:
    """Read the token from the keyring, run the checks and return the exit code."""
    exit_code = 1
    configure_logging()
    if len(argv) != EXPECTED_ARGS or not argv[2].isdigit():
        print("Usage: python poc\\explore_assign_calendar.py https://<moodle-site-url> <userid>")
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
