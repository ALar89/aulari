"""Client for the Moodle web service API (read-only, whitelisted functions).

Rules from CLAUDE.md section 4 and docs/design.md section 7:
- only the read functions in ALLOWED_FUNCTIONS can be called;
- the token always travels in the POST body, never in a URL;
- honest User-Agent, timeouts, and growing waits on HTTP 429/503;
- files are only downloaded from webservice/pluginfile.php of the same site.
"""

import hashlib
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Self
from urllib.parse import urlparse

import requests

from campus_sync import PROJECT_URL, __version__

USER_AGENT = f"CampusSync/{__version__} (+{PROJECT_URL})"
TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 4
BASE_DELAY_SECONDS = 1
MAX_DELAY_SECONDS = 60
RETRY_STATUS_CODES = {429, 503}
CHUNK_SIZE = 64 * 1024
REST_PATH = "/webservice/rest/server.php"
PLUGINFILE_PATH = "/webservice/pluginfile.php/"
INVALID_TOKEN_CODE = "invalidtoken"

ALLOWED_FUNCTIONS = frozenset(
    {
        "core_webservice_get_site_info",
        "tool_mobile_get_public_config",
        "core_enrol_get_users_courses",
        "core_course_get_enrolled_courses_by_timeline_classification",
        "core_course_get_contents",
        "mod_assign_get_assignments",
        "core_calendar_get_calendar_events",
    }
)

logger = logging.getLogger(__name__)


class MoodleError(Exception):
    """Moodle answered with an exception object instead of data."""

    def __init__(self, errorcode: str, message: str) -> None:
        """Keep Moodle's error code so that callers can react to it."""
        super().__init__(f"{errorcode}: {message}")
        self.errorcode = errorcode


class InvalidTokenError(MoodleError):
    """The token is expired or revoked: the user must log in again."""


def array_param(name: str, values: list[int]) -> dict[str, int]:
    """Return Moodle array parameters: {'name[0]': v0, 'name[1]': v1, ...}."""
    result = {f"{name}[{index}]": value for index, value in enumerate(values)}
    return result


def raise_if_moodle_error(data: object) -> None:
    """Raise if data is a Moodle exception object.

    Raises: InvalidTokenError for 'invalidtoken'; MoodleError for any other error.
    """
    if isinstance(data, dict) and ("exception" in data or "errorcode" in data):
        errorcode = str(data.get("errorcode", "unknown"))
        # REST errors use 'message'; pluginfile.php errors use 'error'.
        message = str(data.get("message", data.get("error", "")))
        if errorcode == INVALID_TOKEN_CODE:
            raise InvalidTokenError(errorcode, message)
        raise MoodleError(errorcode, message)


class MoodleClient:
    """Session with one Moodle site, authenticated with a web service token."""

    def __init__(
        self,
        site_url: str,
        token: str,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create the client. session and sleep can be replaced in tests."""
        self._site_url = site_url.rstrip("/")
        self._token = token
        self._session = session if session is not None else requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT
        self._sleep = sleep

    def __enter__(self) -> Self:
        """Allow 'with MoodleClient(...) as client:'."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the HTTP session when the 'with' block ends."""
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    def _retry_delay(self, response: requests.Response, attempt: int) -> float:
        """Return seconds to wait: Retry-After if valid, else 1, 2, 4... capped."""
        result = float(BASE_DELAY_SECONDS * 2**attempt)
        retry_after = response.headers.get("Retry-After", "")
        if retry_after.isdigit():
            result = float(retry_after)
        result = min(result, MAX_DELAY_SECONDS)
        return result

    def _post(self, url: str, data: dict, stream: bool = False) -> requests.Response:
        """POST with retries on 429/503; the caller must close the response.

        Raises: requests.RequestException on network errors.
        """
        result = None
        attempt = 0
        while attempt < MAX_ATTEMPTS and result is None:
            response = self._session.post(url, data=data, timeout=TIMEOUT_SECONDS, stream=stream)
            is_last = attempt == MAX_ATTEMPTS - 1
            if response.status_code in RETRY_STATUS_CODES and not is_last:
                delay = self._retry_delay(response, attempt)
                response.close()
                logger.warning("HTTP %s, retrying in %.0f s", response.status_code, delay)
                self._sleep(delay)
            else:
                result = response
            attempt = attempt + 1
        return result

    def call(self, function: str, params: dict | None = None) -> dict | list:
        """Call a whitelisted web service function and return its JSON data.

        Raises: ValueError if the function is not whitelisted;
        requests.RequestException on network/HTTP errors or invalid JSON;
        InvalidTokenError or MoodleError if Moodle returns an error.
        """
        if function not in ALLOWED_FUNCTIONS:
            raise ValueError(f"Function not in whitelist: {function}")
        body = {"wstoken": self._token, "wsfunction": function, "moodlewsrestformat": "json"}
        if params is not None:
            body.update(params)
        logger.debug("Calling %s", function)
        with self._post(self._site_url + REST_PATH, body) as response:
            response.raise_for_status()
            result = response.json()
        raise_if_moodle_error(result)
        return result

    def _check_file_url(self, fileurl: str) -> None:
        """Check that fileurl is webservice/pluginfile.php on this same site.

        Raises: ValueError otherwise, so the token is never sent elsewhere.
        """
        site = urlparse(self._site_url)
        target = urlparse(fileurl)
        same_origin = (target.scheme, target.netloc) == (site.scheme, site.netloc)
        if not same_origin or not target.path.startswith(site.path + PLUGINFILE_PATH):
            raise ValueError(f"Refusing to download from an unexpected URL: {target.path}")

    def download(self, fileurl: str, destination: Path) -> tuple[int, str]:
        """Download a file to destination and return (size in bytes, SHA-256 hex).

        Raises: ValueError if the URL is not this site's pluginfile.php;
        requests.RequestException on network/HTTP errors;
        InvalidTokenError or MoodleError if Moodle answers with an error;
        OSError if the file cannot be written.
        """
        self._check_file_url(fileurl)
        size = 0
        digest = hashlib.sha256()
        with self._post(fileurl, {"token": self._token}, stream=True) as response:
            # pluginfile.php reports errors as JSON, sometimes with HTTP 403:
            # check it first so that an expired token is recognised.
            if response.headers.get("Content-Type", "").startswith("application/json"):
                raise_if_moodle_error(response.json())
            response.raise_for_status()
            with destination.open("wb") as out_file:
                for chunk in response.iter_content(CHUNK_SIZE):
                    out_file.write(chunk)
                    digest.update(chunk)
                    size = size + len(chunk)
        result = (size, digest.hexdigest())
        return result
