"""Minimal Moodle web service client shared by the PoC scripts.

Only whitelisted read-only functions can be called, and the token always
travels in the POST body.
"""

from urllib.parse import urlparse

import requests

USER_AGENT = "CampusSync/0.1.0-poc (+https://github.com/TODO/campus-sync)"
TIMEOUT_SECONDS = 30
KEYRING_SERVICE = "campus-sync"

# Only these read-only web service functions may be called (design.md section 7).
ALLOWED_FUNCTIONS = {
    "core_webservice_get_site_info",
    "core_enrol_get_users_courses",
    "core_course_get_contents",
    "core_calendar_get_calendar_export_token",
    "core_calendar_get_calendar_events",
    "mod_assign_get_assignments",
}


class MoodleError(Exception):
    """Moodle answered with an exception object instead of data."""


def call_function(site_url: str, token: str, function: str, params: dict) -> dict | list:
    """Call a whitelisted web service function and return its JSON data.

    Raises: ValueError if the function is not whitelisted;
    requests.RequestException on network/HTTP errors or invalid JSON;
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
