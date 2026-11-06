"""Unit tests for campus_sync.log_filter."""

import io
import logging
import unittest

from campus_sync.log_filter import RedactingFilter, redact

SECRET = "abc123SECRETxyz"


class RedactTextTests(unittest.TestCase):
    """Tests for the redact() function on plain strings."""

    def test_query_parameters(self) -> None:
        """Every secret query parameter loses its value."""
        for name in ["token", "wstoken", "privatetoken", "userprivateaccesskey"]:
            text = f"https://site/x.php?a=1&{name}={SECRET}&b=2"
            redacted = redact(text)
            self.assertNotIn(SECRET, redacted)
            self.assertIn(f"{name}=***&b=2", redacted)

    def test_moodlemobile_url(self) -> None:
        """The launch.php redirect URL with a base64 payload is redacted."""
        redacted = redact(f"moodlemobile://token={SECRET}==")
        self.assertEqual(redacted, "moodlemobile://token=***")

    def test_json_fields(self) -> None:
        """Secrets inside JSON bodies are redacted."""
        text = f'{{"token": "{SECRET}", "privatetoken":"{SECRET}", "userid": 5}}'
        redacted = redact(text)
        self.assertNotIn(SECRET, redacted)
        self.assertIn('"userid": 5', redacted)

    def test_tokenpluginfile_path(self) -> None:
        """The key segment of /tokenpluginfile.php/ is redacted."""
        text = f"https://site/tokenpluginfile.php/{SECRET}/123/user/icon/f1"
        self.assertEqual(redact(text), "https://site/tokenpluginfile.php/***/123/user/icon/f1")

    def test_text_without_secrets_is_unchanged(self) -> None:
        """Ordinary text passes through untouched."""
        text = "Downloaded 3 files from course 42"
        self.assertEqual(redact(text), text)


class RedactingFilterTests(unittest.TestCase):
    """Tests for RedactingFilter attached to a real logging handler."""

    def setUp(self) -> None:
        """Create a logger whose handler writes to an in-memory stream."""
        self.stream = io.StringIO()
        handler = logging.StreamHandler(self.stream)
        handler.addFilter(RedactingFilter())
        self.logger = logging.getLogger("test_log_filter")
        self.logger.handlers = [handler]
        self.logger.propagate = False
        self.logger.setLevel(logging.DEBUG)

    def test_secret_in_args(self) -> None:
        """A secret passed as a %s argument is redacted."""
        self.logger.info("Calling %s", f"https://site/x?wstoken={SECRET}")
        output = self.stream.getvalue()
        self.assertNotIn(SECRET, output)
        self.assertIn("wstoken=***", output)

    def test_secret_in_exception(self) -> None:
        """A secret in an exception message and traceback is redacted."""
        try:
            raise ValueError(f"bad url token={SECRET}")
        except ValueError:
            self.logger.exception("Request failed")
        output = self.stream.getvalue()
        self.assertNotIn(SECRET, output)
        self.assertIn("ValueError: bad url token=***", output)


if __name__ == "__main__":
    unittest.main()
