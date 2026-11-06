"""Logging filter that redacts Moodle secrets from every log record.

It removes the values of token=, wstoken=, privatetoken= and
userprivateaccesskey= (in URLs and in JSON), and the key inside
/tokenpluginfile.php/<key>/ paths. Exception tracebacks are redacted too.
"""

import logging
import re

REDACTED = "***"

# key=value in URLs, query strings and moodlemobile://token=... payloads.
_QUERY_PATTERN = re.compile(r"(?i)\b((?:ws|private)?token|userprivateaccesskey)=[^&\s\"'<>]+")
# "key": "value" in JSON bodies.
_JSON_PATTERN = re.compile(
    r"(?i)(\"(?:(?:ws|private)?token|userprivateaccesskey)\"\s*:\s*)\"[^\"]*\""
)
# /tokenpluginfile.php/<key>/ in file URLs.
_TOKENPLUGINFILE_PATTERN = re.compile(r"(?i)(/tokenpluginfile\.php/)[^/\s]+(/)")


def redact(text: str) -> str:
    """Return text with every known secret replaced by '***'."""
    result = _QUERY_PATTERN.sub(r"\1=" + REDACTED, text)
    result = _JSON_PATTERN.sub(r'\1"' + REDACTED + '"', result)
    result = _TOKENPLUGINFILE_PATTERN.sub(r"\1" + REDACTED + r"\2", result)
    return result


class RedactingFilter(logging.Filter):
    """Filter that rewrites each record so that no secret reaches a handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact message, traceback and stack info; always keep the record."""
        # getMessage() merges msg and args, so secrets in args are covered too.
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """Set up root logging with the redacting filter on its handler.

    The filter goes on the handler (not on a logger) so that it also sees
    records propagated from library loggers such as urllib3.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
