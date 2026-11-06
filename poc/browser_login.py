"""Phase 1, steps 2-3: get a Moodle token with the browser login flow.

Flow (admin/tool/mobile/launch.php):
1. The script prints a launch.php URL with a random passport.
2. You open it in a browser tab with DevTools (Network, "Preserve log")
   and log in as usual (CAS).
3. Moodle redirects to moodlemobile://token=<base64>. Copy that URL.
4. The script reads it with getpass (hidden, never echoed), checks the
   signature, discards the private token, calls core_webservice_get_site_info
   to learn the user id and stores the token only in the OS keyring.

Usage (PowerShell, venv active, from the project folder):
    python poc\\browser_login.py https://<moodle-site-url>

Never paste the token or the moodlemobile:// URL anywhere else.
"""

import base64
import getpass
import hashlib
import logging
import secrets
import sys
from pathlib import Path
from urllib.parse import unquote, urlencode, urlparse

import keyring
import keyring.errors
import requests

# Temporary until the package is installable: make src/ importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from campus_sync.log_filter import configure_logging  # noqa: E402

USER_AGENT = "CampusSync/0.1.0-poc (+https://github.com/TODO/campus-sync)"
EXPECTED_ARGS = 2  # script name + site URL
TIMEOUT_SECONDS = 30
MOBILE_SERVICE = "moodle_mobile_app"
URL_SCHEME = "moodlemobile"
KEYRING_SERVICE = "campus-sync"
TOKEN_SEPARATOR = ":::"
MIN_TOKEN_PARTS = 2

# Only these read-only web service functions may be called.
ALLOWED_FUNCTIONS = {"core_webservice_get_site_info"}

logger = logging.getLogger("browser_login")


class MoodleError(Exception):
    """Moodle answered with an exception object instead of data."""


def build_launch_url(site_url: str, passport: str) -> str:
    """Return the launch.php URL that starts the browser login flow."""
    query = urlencode({"service": MOBILE_SERVICE, "passport": passport, "urlscheme": URL_SCHEME})
    result = f"{site_url}/admin/tool/mobile/launch.php?{query}"
    return result


def extract_token_payload(pasted_url: str) -> str:
    """Return the base64 text after 'token=' in the pasted redirect URL.

    Raises: ValueError if the text does not contain 'token='.
    """
    marker = "token="
    position = pasted_url.find(marker)
    if position < 0:
        raise ValueError("The pasted text does not contain 'token='")
    result = unquote(pasted_url[position + len(marker) :].strip())
    return result


def decode_launch_token(payload: str) -> list[str]:
    """Decode the base64 payload into its ':::'-separated parts.

    The parts are: signature, token and (optionally) private token.

    Raises: ValueError (binascii.Error or UnicodeDecodeError) if the
    payload is not valid base64 or not valid UTF-8.
    """
    padding = "=" * (-len(payload) % 4)
    decoded = base64.b64decode(payload + padding, validate=True).decode("utf-8")
    result = decoded.split(TOKEN_SEPARATOR)
    return result


def signature_matches(site_url: str, passport: str, signature: str) -> bool:
    """Return True if signature == md5(wwwroot + passport), as launch.php builds it."""
    expected = hashlib.md5((site_url + passport).encode("utf-8")).hexdigest()
    result = secrets.compare_digest(expected, signature)
    return result


def call_function(site_url: str, token: str, function: str, params: dict) -> dict:
    """Call a whitelisted web service function and return its JSON data.

    The token goes in the POST body, never in the URL.

    Raises: ValueError if the function is not whitelisted;
    requests.RequestException on network/HTTP errors;
    MoodleError if Moodle returns an exception object.
    """
    if function not in ALLOWED_FUNCTIONS:
        raise ValueError(f"Function not in whitelist: {function}")
    endpoint = f"{site_url}/webservice/rest/server.php"
    body = {"wstoken": token, "wsfunction": function, "moodlewsrestformat": "json"}
    body.update(params)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    with requests.post(endpoint, data=body, headers=headers, timeout=TIMEOUT_SECONDS) as response:
        response.raise_for_status()
        result = response.json()
    if isinstance(result, dict) and "exception" in result:
        raise MoodleError(f"{result.get('errorcode')}: {result.get('message')}")
    return result


def keyring_username(site_url: str, user_id: int) -> str:
    """Return '<host><path>|<userid>' (the path keeps academic years apart)."""
    parsed = urlparse(site_url)
    result = f"{parsed.hostname}{parsed.path}|{user_id}"
    return result


def print_instructions(launch_url: str) -> None:
    """Explain to the user how to capture the moodlemobile:// redirect."""
    print("1. Open a NEW browser tab and press F12 -> Network tab.")
    print("   Tick 'Preserve log'. Type 'launch' in the filter box.")
    print("2. In that tab, open this URL and log in as usual:\n")
    print(f"   {launch_url}\n")
    print("3. The browser will try to open 'moodlemobile://...' and fail (that is expected).")
    print("   In the Network tab, click the launch.php request, Headers, and copy the")
    print("   'Location' value (moodlemobile://token=...).")
    print("   If instead you see a page with a button/link, right-click it -> copy link.")
    print("4. Paste it below. Nothing is shown while you paste. Do not paste it anywhere else.\n")


def store_token(site_url: str, token: str) -> int:
    """Validate the token with site_info, store it in the keyring, return exit code.

    Raises: requests.RequestException, MoodleError (from call_function);
    keyring.errors.KeyringError if the keyring cannot store it.
    """
    exit_code = 1
    site_info = call_function(site_url, token, "core_webservice_get_site_info", {})
    returned_root = str(site_info.get("siteurl", "")).rstrip("/")
    if returned_root != site_url:
        print(f"Warning: site_info siteurl is {returned_root!r}, expected {site_url!r}")
    username = keyring_username(site_url, site_info["userid"])
    keyring.set_password(KEYRING_SERVICE, username, token)
    stored = keyring.get_password(KEYRING_SERVICE, username)
    if stored == token:
        print(f"\nToken stored in keyring: service={KEYRING_SERVICE!r} user={username!r}")
        print(f"Token length : {len(token)}")
        print(f"Keyring      : {type(keyring.get_keyring()).__name__}")
        print(f"Site name    : {site_info.get('sitename')}")
        print(f"Release      : {site_info.get('release')}")
        print(f"Version      : {site_info.get('version')}")
        exit_code = 0
    else:
        print("Error: the token read back from the keyring does not match.")
    return exit_code


def main(argv: list[str]) -> int:
    """Run the browser login flow and return the process exit code."""
    exit_code = 1
    configure_logging()
    if len(argv) != EXPECTED_ARGS:
        print("Usage: python poc\\browser_login.py https://<moodle-site-url>")
    else:
        site_url = argv[1].rstrip("/")
        passport = secrets.token_hex(16)
        print_instructions(build_launch_url(site_url, passport))
        pasted_url = getpass.getpass("moodlemobile:// URL (hidden): ")
        try:
            parts = decode_launch_token(extract_token_payload(pasted_url))
            if len(parts) < MIN_TOKEN_PARTS:
                print("Error: unexpected token format (no ':::' separator).")
            elif not signature_matches(site_url, passport, parts[0]):
                print("Error: signature mismatch. Wrong site URL, or a URL from another attempt.")
            else:
                # parts[2], if present, is the private token: never kept or stored.
                exit_code = store_token(site_url, parts[1])
        except ValueError as error:
            logger.error("Could not decode the pasted URL: %s", error)
        except MoodleError as error:
            logger.error("Moodle error: %s", error)
        except requests.RequestException as error:
            logger.error("Network error: %s", error)
        except keyring.errors.KeyringError as error:
            logger.error("Keyring error: %s", error)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
