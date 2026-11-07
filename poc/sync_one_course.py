"""Phase 2 check: sync ONE real course with the core modules into a test folder.

Uses aulari.sync end to end (lock, owner.json, registry, downloads).
To keep the real data clean it uses a separate test registry
(%LOCALAPPDATA%\\aulari\\poc_registry.sqlite3) and, by default, the
destination poc\\out\\sync-test\\ (ignored by git). Run it twice: the second
run should download nothing.

Usage (PowerShell, venv active, from the project folder):
    python poc\\sync_one_course.py https://<moodle-site-url> <userid> <courseid> [destination]
"""

import logging
import os
import sys
from pathlib import Path

import keyring.errors
import requests

from aulari import auth, organizer
from aulari.log_filter import configure_logging
from aulari.moodle_client import InvalidTokenError, MoodleClient, MoodleError
from aulari.registry import Registry, RegistryError
from aulari.sync import (
    Course,
    NotOwnerError,
    Progress,
    Syncer,
    SyncLockedError,
    SyncSummary,
    SyncTarget,
    load_machine_id,
)

MIN_ARGS = 4  # script name + site URL + user id + course id
MAX_ARGS = 5  # ... + destination
DEFAULT_DESTINATION = Path(__file__).resolve().parent / "out" / "sync-test"
POC_REGISTRY_NAME = "poc_registry.sqlite3"

logger = logging.getLogger("sync_one_course")


def app_dir() -> Path:
    """Return %LOCALAPPDATA%\\aulari.

    Raises: KeyError if LOCALAPPDATA is not defined.
    """
    result = Path(os.environ["LOCALAPPDATA"]) / "aulari"
    return result


def find_course(client: MoodleClient, user_id: int, course_id: int) -> Course | None:
    """Return the enrolled course with that id, or None.

    Raises: MoodleError, requests.RequestException.
    """
    result = None
    courses = client.call("core_enrol_get_users_courses", {"userid": user_id})
    index = 0
    while index < len(courses) and result is None:
        if courses[index]["id"] == course_id:
            result = Course(course_id, str(courses[index].get("fullname", "")))
        index = index + 1
    return result


def print_progress(report: Progress) -> None:
    """Print one line per item (file names only)."""
    print(f"  [{report.done + 1}/{report.total}] {report.filename}")


def print_summary(summary: SyncSummary) -> None:
    """Print the sync summary."""
    print("\nSummary")
    print(f"  new               : {summary.new}")
    print(f"  new versions      : {summary.new_versions}")
    print(f"  adopted           : {summary.adopted}")
    print(f"  unchanged         : {summary.unchanged}")
    print(f"  missing locally   : {summary.missing_locally}")
    print(f"  removed in Moodle : {summary.removed_in_moodle}")
    print(f"  cancelled         : {summary.cancelled}")
    print(f"  invalid token     : {summary.invalid_token}")
    print(f"  errors            : {len(summary.errors)}")
    for message in summary.errors:
        print(f"    - {message}")


def run(site_url: str, user_id: int, course_id: int, destination: Path) -> int:
    """Sync one course and return the exit code (0 if there were no errors).

    Raises: everything the sync modules raise for setup problems.
    """
    exit_code = 1
    year = organizer.academic_year_from_site(site_url)
    token = auth.load_token(site_url, user_id)
    if year is None:
        print("The site URL has no academic year (e.g. /2026-27).")
    elif token is None:
        print("No token in the keyring. Run poc\\browser_login.py first.")
    else:
        destination.mkdir(parents=True, exist_ok=True)
        target = SyncTarget(site_url, user_id, year, destination)
        machine_id = load_machine_id(app_dir())
        with (
            MoodleClient(site_url, token) as client,
            Registry(app_dir() / POC_REGISTRY_NAME) as registry,
        ):
            course = find_course(client, user_id, course_id)
            if course is None:
                print(f"Course {course_id} is not in your enrolled courses.")
            else:
                print(f"Syncing course {course_id} into {destination}")
                summary = Syncer(client, registry, target, print_progress).run([course], machine_id)
                print_summary(summary)
                ok = not summary.errors and not summary.invalid_token
                exit_code = 0 if ok else 1
    return exit_code


def main(argv: list[str]) -> int:
    """Parse arguments, run the sync and return the exit code."""
    exit_code = 1
    configure_logging()
    valid = MIN_ARGS <= len(argv) <= MAX_ARGS and argv[2].isdigit() and argv[3].isdigit()
    if not valid:
        print(
            "Usage: python poc\\sync_one_course.py https://<site> <userid> <courseid> [destination]"
        )
    else:
        destination = Path(argv[4]) if len(argv) == MAX_ARGS else DEFAULT_DESTINATION
        try:
            exit_code = run(argv[1].rstrip("/"), int(argv[2]), int(argv[3]), destination)
        except InvalidTokenError:
            logger.error("The token is no longer valid. Run poc\\browser_login.py again.")
        except (SyncLockedError, NotOwnerError, RegistryError) as error:
            logger.error("%s", error)
        except MoodleError as error:
            logger.error("Moodle error: %s", error)
        except requests.RequestException as error:
            logger.error("Network/HTTP error: %s", error)
        except keyring.errors.KeyringError as error:
            logger.error("Keyring error: %s", error)
        except (OSError, KeyError, ValueError) as error:
            logger.error("Local error: %s", error)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
