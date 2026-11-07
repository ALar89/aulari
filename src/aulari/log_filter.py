"""Logging filter that redacts Moodle secrets from every log record.

It removes the values of token=, wstoken=, privatetoken= and
userprivateaccesskey= (in URLs and in JSON), and the key inside
/tokenpluginfile.php/<key>/ paths. Exception tracebacks are redacted too.
"""

import logging
import logging.handlers
import re
from pathlib import Path

REDACTED = "***"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 3

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


def _add_handler(root: logging.Logger, handler: logging.Handler) -> None:
    """Attach handler to root with the standard format and the redacting filter."""
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)


def configure_logging(level: int = logging.INFO, log_file: Path | None = None) -> None:
    """Set up root logging with the redacting filter on every handler.

    Logs go to the console and, if log_file is given, to a rotating file
    (design.md section 9: rotation in the app data folder). The filter goes
    on the handlers (not on a logger) so that it also sees records
    propagated from library loggers such as urllib3.

    Raises: OSError if the log folder cannot be created.
    """
    root = logging.getLogger()
    root.setLevel(level)
    _add_handler(root, logging.StreamHandler())
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
        )
        _add_handler(root, file_handler)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
