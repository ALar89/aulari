"""Phase 2 check: download ONE file with campus_sync.moodle_client.

Verifies that webservice/pluginfile.php accepts the token in the POST body.
It picks the smallest file of the given course, downloads it to poc\\out\\
(ignored by git) and compares the size with the one Moodle reports.

Usage (PowerShell, venv active, from the project folder):
    python poc\\download_one.py https://<moodle-site-url> <userid> <courseid>
"""

import logging
import sys
from pathlib import Path

import keyring
import keyring.errors
import requests
from moodle_ws import KEYRING_SERVICE, keyring_username

from campus_sync.log_filter import configure_logging
from campus_sync.moodle_client import InvalidTokenError, MoodleClient, MoodleError
from campus_sync.organizer import ensure_inside, sanitize_filename

EXPECTED_ARGS = 4  # script name + site URL + user id + course id
OUTPUT_DIR = Path(__file__).resolve().parent / "out"
# First bytes of common formats, to tell a real file from an error page.
MAGIC_NUMBERS = {
    b"%PDF": "PDF",
    b"PK\x03\x04": "ZIP / Office document",
    b"\x89PNG": "PNG image",
    b"\xff\xd8\xff": "JPEG image",
    b"<!DOCTYPE": "HTML page (probably an error)",
    b"<html": "HTML page (probably an error)",
    b"{": "JSON (probably an error)",
}

logger = logging.getLogger("download_one")


def smallest_file(sections: list) -> dict | None:
    """Return the smallest 'file' content in a course, or None if there is none."""
    result = None
    for section in sections:
        for module in section.get("modules", []):
            for content in module.get("contents", []):
                is_file = content.get("type") == "file"
                size = content.get("filesize", 0)
                if is_file and (result is None or size < result.get("filesize", 0)):
                    result = content
    return result


def describe_start(path: Path) -> str:
    """Return the format guessed from the first bytes of a file."""
    result = "unknown format"
    with path.open("rb") as in_file:
        start = in_file.read(16)
    prefixes = list(MAGIC_NUMBERS)
    index = 0
    found = False
    while index < len(prefixes) and not found:
        if start.lstrip().startswith(prefixes[index]):
            result = MAGIC_NUMBERS[prefixes[index]]
            found = True
        index = index + 1
    return result


def check_download(client: MoodleClient, course_id: int) -> int:
    """Download the smallest file of the course and report; return the exit code.

    Raises: MoodleError, requests.RequestException, OSError, ValueError.
    """
    exit_code = 1
    sections = client.call("core_course_get_contents", {"courseid": course_id})
    content = smallest_file(sections)
    if content is None:
        print("This course has no files. Try another course id.")
    else:
        OUTPUT_DIR.mkdir(exist_ok=True)
        name = sanitize_filename(content.get("filename", "file"))
        target = ensure_inside(OUTPUT_DIR, OUTPUT_DIR / name)
        expected = content.get("filesize", 0)
        size, sha256 = client.download(content["fileurl"], target)
        print(f"Saved to      : {target}")
        print(f"Moodle size   : {expected} bytes")
        print(f"Downloaded    : {size} bytes")
        print(f"Format        : {describe_start(target)}")
        print(f"SHA-256 start : {sha256[:12]}")
        if size == expected:
            print("\nOK: the POST download works (sizes match).")
            exit_code = 0
        else:
            print("\nWARNING: sizes differ. Do not delete the file; report this result.")
    return exit_code


def main(argv: list[str]) -> int:
    """Read the token, run the check and return the exit code."""
    exit_code = 1
    configure_logging()
    if len(argv) != EXPECTED_ARGS or not (argv[2].isdigit() and argv[3].isdigit()):
        print("Usage: python poc\\download_one.py https://<moodle-site-url> <userid> <courseid>")
    else:
        site_url = argv[1].rstrip("/")
        username = keyring_username(site_url, int(argv[2]))
        try:
            token = keyring.get_password(KEYRING_SERVICE, username)
            if token is None:
                print(f"No token in keyring for {username!r}. Run browser_login.py first.")
            else:
                with MoodleClient(site_url, token) as client:
                    exit_code = check_download(client, int(argv[3]))
        except InvalidTokenError:
            logger.error("The token is no longer valid. Run browser_login.py again.")
        except MoodleError as error:
            logger.error("Moodle error: %s", error)
        except requests.RequestException as error:
            logger.error("Network/HTTP error: %s", error)
        except (OSError, ValueError) as error:
            logger.error("Local error: %s", error)
        except keyring.errors.KeyringError as error:
            logger.error("Keyring error: %s", error)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
