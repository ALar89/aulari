"""Turn exceptions into user-facing texts (no tkinter here, so it is testable).

Messages never include tokens: the core never puts them in exceptions, and
everything shown also passes through log_filter.redact() as a last guard.
"""

import keyring.errors
import requests

from campus_sync.app import AppError
from campus_sync.log_filter import redact
from campus_sync.moodle_client import InvalidTokenError, MoodleError
from campus_sync.sync import NotOwnerError, SyncLockedError
from campus_sync.texts import text


def error_message(error: Exception) -> str:
    """Return the text to show for an error raised by a CampusApp action."""
    if isinstance(error, AppError):
        result = text(error.key)
    elif isinstance(error, InvalidTokenError):
        result = text("error_invalid_token")
    elif isinstance(error, NotOwnerError):
        result = text("error_not_owner", host=error.owner_host)
    elif isinstance(error, SyncLockedError):
        result = text("error_locked_short")
    elif isinstance(error, requests.RequestException):
        result = text("error_network", error=type(error).__name__)
    elif isinstance(error, MoodleError):
        result = text("error_moodle", error=str(error))
    elif isinstance(error, keyring.errors.KeyringError):
        result = text("error_keyring")
    elif isinstance(error, ValueError):
        result = text("login_invalid_redirect")
    else:
        result = text("error_unexpected")
    result = redact(result)
    return result
