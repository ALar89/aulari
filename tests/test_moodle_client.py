"""Unit tests for aulari.moodle_client, without network access."""

import hashlib
import json
import logging
import tempfile
import unittest
from pathlib import Path

import requests

from aulari import moodle_client
from aulari.moodle_client import InvalidTokenError, MoodleClient, MoodleError

SITE = "https://moodle.example.test/2026-27"
TOKEN = "0123456789abcdef0123456789abcdef"
HTTP_ERROR_STATUS = 400
FILE_URL = SITE + "/webservice/pluginfile.php/123/mod_resource/content/1/notes.pdf"


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code: int = 200, body: bytes = b"", headers: dict | None = None):
        """Store the canned status, body and headers."""
        self.status_code = status_code
        self.body = body
        self.headers = headers if headers is not None else {"Content-Type": "application/json"}
        self.closed = False

    def __enter__(self) -> FakeResponse:
        """Support 'with response:'."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close on leaving the 'with' block."""
        self.close()

    def close(self) -> None:
        """Remember that the response was closed."""
        self.closed = True

    def json(self) -> object:
        """Parse the body as JSON."""
        return json.loads(self.body)

    def raise_for_status(self) -> None:
        """Raise HTTPError for 4xx/5xx, like requests does."""
        if self.status_code >= HTTP_ERROR_STATUS:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int) -> list[bytes]:
        """Return the body split into chunks."""
        return [self.body[i : i + chunk_size] for i in range(0, len(self.body), chunk_size)]


class FakeSession:
    """Stand-in for requests.Session that returns queued responses."""

    def __init__(self, responses: list[FakeResponse]):
        """Queue the responses to return, in order."""
        self.responses = responses
        self.calls = []
        self.headers = {}
        self.closed = False

    def post(self, url: str, data: dict, timeout: float, stream: bool = False) -> FakeResponse:
        """Record the request and return the next queued response."""
        self.calls.append({"url": url, "data": data, "timeout": timeout, "stream": stream})
        return self.responses.pop(0)

    def close(self) -> None:
        """Remember that the session was closed."""
        self.closed = True


def json_response(data: object, status_code: int = 200) -> FakeResponse:
    """Return a FakeResponse whose body is data encoded as JSON."""
    return FakeResponse(status_code, json.dumps(data).encode("utf-8"))


def make_client(responses: list[FakeResponse]) -> tuple[MoodleClient, FakeSession, list]:
    """Return a client with a fake session and a sleep that only records delays."""
    session = FakeSession(responses)
    delays = []
    client = MoodleClient(SITE, TOKEN, session=session, sleep=delays.append)
    return client, session, delays


class CallTests(unittest.TestCase):
    """Tests for MoodleClient.call()."""

    def test_function_outside_whitelist_is_rejected(self) -> None:
        """A non-whitelisted function raises before any request is made."""
        client, session, _ = make_client([])
        with self.assertRaises(ValueError):
            client.call("core_user_update_users")
        self.assertEqual(session.calls, [])

    def test_token_goes_in_post_body(self) -> None:
        """wstoken is in the body, the URL has no token, and the User-Agent is honest."""
        client, session, _ = make_client([json_response({"userid": 5})])
        result = client.call("core_webservice_get_site_info")
        self.assertEqual(result, {"userid": 5})
        call = session.calls[0]
        self.assertEqual(call["url"], SITE + "/webservice/rest/server.php")
        self.assertNotIn(TOKEN, call["url"])
        self.assertEqual(call["data"]["wstoken"], TOKEN)
        self.assertTrue(session.headers["User-Agent"].startswith("Aulari/"))

    def test_params_are_sent(self) -> None:
        """Array parameters use Moodle's name[index] format."""
        client, session, _ = make_client([json_response({"courses": []})])
        client.call("mod_assign_get_assignments", moodle_client.array_param("courseids", [7, 9]))
        self.assertEqual(session.calls[0]["data"]["courseids[0]"], 7)
        self.assertEqual(session.calls[0]["data"]["courseids[1]"], 9)

    def test_invalid_token_raises_specific_error(self) -> None:
        """errorcode 'invalidtoken' becomes InvalidTokenError."""
        error = {"exception": "moodle_exception", "errorcode": "invalidtoken", "message": "x"}
        client, _, _ = make_client([json_response(error)])
        with self.assertRaises(InvalidTokenError):
            client.call("core_webservice_get_site_info")

    def test_other_moodle_error(self) -> None:
        """Any other Moodle exception becomes MoodleError with its errorcode."""
        error = {"exception": "x", "errorcode": "accessexception", "message": "denied"}
        client, _, _ = make_client([json_response(error)])
        with self.assertRaises(MoodleError) as context:
            client.call("core_course_get_contents", {"courseid": 1})
        self.assertEqual(context.exception.errorcode, "accessexception")
        self.assertNotIsInstance(context.exception, InvalidTokenError)


class RetryTests(unittest.TestCase):
    """Tests for the waits on HTTP 429/503."""

    def test_503_then_success(self) -> None:
        """A 503 is retried after 1 s and the failed response is closed."""
        busy = FakeResponse(503, b"")
        client, session, delays = make_client([busy, json_response([])])
        self.assertEqual(client.call("core_enrol_get_users_courses", {"userid": 1}), [])
        self.assertEqual(delays, [1.0])
        self.assertTrue(busy.closed)
        self.assertEqual(len(session.calls), 2)

    def test_retry_after_header_is_respected(self) -> None:
        """Retry-After from the server sets the wait."""
        limited = FakeResponse(429, b"", {"Retry-After": "5"})
        client, _, delays = make_client([limited, json_response([])])
        client.call("core_enrol_get_users_courses", {"userid": 1})
        self.assertEqual(delays, [5.0])

    def test_waits_grow_and_give_up(self) -> None:
        """Waits double (1, 2, 4) and after the last attempt the HTTP error is raised."""
        responses = [FakeResponse(503, b"") for _ in range(moodle_client.MAX_ATTEMPTS)]
        client, _, delays = make_client(responses)
        with self.assertRaises(requests.HTTPError):
            client.call("core_enrol_get_users_courses", {"userid": 1})
        self.assertEqual(delays, [1.0, 2.0, 4.0])

    def test_logs_do_not_contain_token(self) -> None:
        """The client itself never logs the token, even before redaction."""
        client, _, _ = make_client([FakeResponse(503, b""), json_response([])])
        with self.assertLogs("aulari.moodle_client", level=logging.DEBUG) as logs:
            client.call("core_enrol_get_users_courses", {"userid": 1})
        output = "\n".join(logs.output)
        self.assertIn("HTTP 503", output)
        self.assertNotIn(TOKEN, output)


class DownloadTests(unittest.TestCase):
    """Tests for MoodleClient.download()."""

    def test_download_writes_file_and_returns_hash(self) -> None:
        """The file is written, size and SHA-256 match, token goes in the body."""
        body = b"%PDF-1.7 fake content" * 10000
        response = FakeResponse(200, body, {"Content-Type": "application/pdf"})
        client, session, _ = make_client([response])
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "notes.pdf"
            size, sha256 = client.download(FILE_URL, target)
            self.assertEqual(target.read_bytes(), body)
        self.assertEqual(size, len(body))
        self.assertEqual(sha256, hashlib.sha256(body).hexdigest())
        self.assertEqual(session.calls[0]["data"], {"token": TOKEN})
        self.assertNotIn(TOKEN, session.calls[0]["url"])
        self.assertTrue(session.calls[0]["stream"])

    def test_foreign_urls_are_rejected(self) -> None:
        """Other hosts, schemes, paths or sites never receive the token."""
        bad_urls = [
            "https://evil.example.test/2026-27/webservice/pluginfile.php/1/a.pdf",
            "http://moodle.example.test/2026-27/webservice/pluginfile.php/1/a.pdf",
            SITE + "/pluginfile.php/1/a.pdf",
            "https://moodle.example.test/2025-26/webservice/pluginfile.php/1/a.pdf",
        ]
        client, session, _ = make_client([])
        with tempfile.TemporaryDirectory() as folder:
            for url in bad_urls:
                with self.assertRaises(ValueError, msg=url):
                    client.download(url, Path(folder) / "a.pdf")
        self.assertEqual(session.calls, [])

    def test_json_error_with_403_is_invalid_token(self) -> None:
        """An expired token on pluginfile.php (JSON + 403) raises InvalidTokenError."""
        error = {"error": "Invalid token", "errorcode": "invalidtoken"}
        client, _, _ = make_client([json_response(error, status_code=403)])
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "a.pdf"
            with self.assertRaises(InvalidTokenError):
                client.download(FILE_URL, target)
            self.assertFalse(target.exists())


class ContextManagerTests(unittest.TestCase):
    """Tests for 'with MoodleClient(...) as client:'."""

    def test_session_is_closed(self) -> None:
        """Leaving the 'with' block closes the HTTP session."""
        session = FakeSession([])
        with MoodleClient(SITE, TOKEN, session=session):
            self.assertFalse(session.closed)
        self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main()
