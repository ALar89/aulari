"""Phase 3 check: export the real calendar of all enrolled courses to an .ics file.

Uses aulari.calendar_export with the test registry
(%LOCALAPPDATA%\\aulari\\poc_registry.sqlite3) and, by default, the
destination poc\\out\\sync-test\\ (ignored by git). Course aliases come from
the registry if the course was already synced, otherwise from course_alias().

Usage (PowerShell, venv active, from the project folder):
    python poc\\export_calendar.py https://<moodle-site-url> <userid> [destination]
"""

import logging
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import keyring.errors
import requests

from aulari import auth, organizer
from aulari.calendar_export import CalendarRequest, export_calendar
from aulari.log_filter import configure_logging
from aulari.moodle_client import InvalidTokenError, MoodleClient, MoodleError
from aulari.registry import Registry, RegistryError

MIN_ARGS = 3  # script name + site URL + user id
MAX_ARGS = 4  # ... + destination
DEFAULT_DESTINATION = Path(__file__).resolve().parent / "out" / "sync-test"
POC_REGISTRY_NAME = "poc_registry.sqlite3"

logger = logging.getLogger("export_calendar")


def course_aliases(client: MoodleClient, registry: Registry, site_id: int, user_id: int) -> dict:
    """Return {course id: alias} for every enrolled course.

    Raises: MoodleError, requests.RequestException.
    """
    known = registry.course_folders(site_id)
    courses = client.call("core_enrol_get_users_courses", {"userid": user_id})
    result = {}
    for course in courses:
        course_id = int(course["id"])
        if course_id in known:
            result[course_id] = PurePosixPath(known[course_id]).name
        else:
            result[course_id] = organizer.course_alias(str(course.get("fullname", "")))
    return result


def run(site_url: str, user_id: int, destination: Path) -> int:
    """Export the calendar and return the exit code.

    Raises: everything the calendar and registry modules raise.
    """
    exit_code = 1
    year = organizer.academic_year_from_site(site_url)
    token = auth.load_token(site_url, user_id)
    app_dir = Path(os.environ["LOCALAPPDATA"]) / "aulari"
    if year is None:
        print("The site URL has no academic year (e.g. /2026-27).")
    elif token is None:
        print("No token in the keyring. Run poc\\browser_login.py first.")
    else:
        with (
            MoodleClient(site_url, token) as client,
            Registry(app_dir / POC_REGISTRY_NAME) as registry,
        ):
            site_id = registry.get_or_create_site(site_url, user_id, year)
            aliases = course_aliases(client, registry, site_id, user_id)
            request = CalendarRequest(site_id, site_url, year, destination, aliases)
            path, count = export_calendar(client, registry, request, datetime.now(UTC))
            ics = path.read_bytes().decode("utf-8")
            per_alias = Counter(
                line.split("]")[0][len("SUMMARY:[") :]
                for line in ics.replace("\r\n ", "").split("\r\n")
                if line.startswith("SUMMARY:[")
            )
            print(f"Courses     : {len(aliases)}")
            print(f"Events      : {count}")
            print(f"Per course  : {dict(per_alias)}")
            print(f"With DTEND  : {ics.count(chr(10) + 'DTEND:')}")
            print(f"File        : {path}")
            exit_code = 0
    return exit_code


def main(argv: list[str]) -> int:
    """Parse arguments, export and return the exit code."""
    exit_code = 1
    configure_logging()
    if not (MIN_ARGS <= len(argv) <= MAX_ARGS and argv[2].isdigit()):
        print("Usage: python poc\\export_calendar.py https://<site> <userid> [destination]")
    else:
        destination = Path(argv[3]) if len(argv) == MAX_ARGS else DEFAULT_DESTINATION
        try:
            exit_code = run(argv[1].rstrip("/"), int(argv[2]), destination)
        except InvalidTokenError:
            logger.error("The token is no longer valid. Run poc\\browser_login.py again.")
        except RegistryError as error:
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
