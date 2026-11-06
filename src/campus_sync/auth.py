"""Browser login flow (launch.php) and token storage in the OS keyring.

Flow verified on e-aules in phase 1 (docs/design.md sections 4 and 10):
1. build_launch_url() gives the launch.php URL with a random passport.
2. The user logs in in the browser; Moodle redirects to
   moodlemobile://token=<base64(signature:::token[:::privatetoken])>.
3. parse_launch_redirect() checks signature == md5(wwwroot + passport)
   and returns only the token (the private token is discarded).
4. login() validates the token with site_info and stores it in the keyring.

The token is never logged, printed or written to disk.
"""

import base64
import hashlib
import re
import secrets
from collections.abc import Callable
from urllib.parse import unquote, urlencode, urlparse

import keyring

from campus_sync.moodle_client import MoodleClient

KEYRING_SERVICE = "campus-sync"
MOBILE_SERVICE = "moodle_mobile_app"
URL_SCHEME = "moodlemobile"
TOKEN_MARKER = "token="
TOKEN_SEPARATOR = ":::"
MIN_TOKEN_PARTS = 2
PASSPORT_BYTES = 16
_TOKEN_FORMAT = re.compile(r"[A-Za-z0-9]{16,128}")
_BASE64_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")


def new_passport() -> str:
    """Return a random passport for one login attempt."""
    result = secrets.token_hex(PASSPORT_BYTES)
    return result


def build_launch_url(site_url: str, passport: str) -> str:
    """Return the launch.php URL that starts the browser login flow."""
    query = urlencode({"service": MOBILE_SERVICE, "passport": passport, "urlscheme": URL_SCHEME})
    result = f"{site_url.rstrip('/')}/admin/tool/mobile/launch.php?{query}"
    return result


def _extract_payload(pasted_url: str) -> str:
    """Return the base64 text after 'token=' in the pasted redirect URL.

    Raises: ValueError if the text does not contain 'token='.
    """
    position = pasted_url.find(TOKEN_MARKER)
    if position < 0:
        raise ValueError("The pasted text does not contain 'token='")
    result = unquote(pasted_url[position + len(TOKEN_MARKER) :].strip())
    return result


def _decode_payload(payload: str) -> list[str]:
    """Decode the base64 payload into its ':::'-separated parts.

    Raises: ValueError (binascii.Error or UnicodeDecodeError) if the
    payload is not valid base64 or not valid UTF-8.
    """
    padding = "=" * (-len(payload) % 4)
    decoded = base64.b64decode(payload + padding, validate=True).decode("utf-8")
    result = decoded.split(TOKEN_SEPARATOR)
    return result


def _signature_matches(site_url: str, passport: str, signature: str) -> bool:
    """Return True if signature == md5(wwwroot + passport), as launch.php builds it."""
    expected = hashlib.md5((site_url + passport).encode("utf-8")).hexdigest()
    result = secrets.compare_digest(expected, signature)
    return result


def redirect_shape(url: str) -> str:
    """Describe a redirect URL for diagnosis without revealing the token.

    Shows the total length, the text before 'token=' and any character after
    it that is not base64 (with its position from the end). Base64 characters,
    which carry the token, are never shown.
    """
    position = url.find(TOKEN_MARKER)
    prefix = url[: position + len(TOKEN_MARKER)] if position >= 0 else "(no 'token=')"
    payload = url[position + len(TOKEN_MARKER) :] if position >= 0 else ""
    odd = [
        f"{char!r}@-{len(payload) - index}"
        for index, char in enumerate(payload)
        if char not in _BASE64_CHARS
    ]
    last = payload[-1:] if payload else ""
    ending = last if last in {"/", "=", ""} else ("alnum" if last.isalnum() else "other")
    result = (
        f"length={len(url)} prefix={prefix!r} payload={len(payload)} "
        f"non-base64={odd} last={ending!r} padding_ok={len(payload) % 4 == 0}"
    )
    return result


def parse_launch_redirect(pasted_url: str, site_url: str, passport: str) -> str:
    """Return the token from the moodlemobile:// redirect, after checking it.

    Raises: ValueError if the URL is malformed, the signature does not match
    this site and passport, or the token has an unexpected format. The
    messages never contain the token.
    """
    parts = _decode_payload(_extract_payload(pasted_url))
    if len(parts) < MIN_TOKEN_PARTS:
        raise ValueError("Unexpected token format (no ':::' separator)")
    if not _signature_matches(site_url.rstrip("/"), passport, parts[0]):
        raise ValueError("Signature mismatch: wrong site or URL from another attempt")
    result = parts[1]
    # parts[2], if present, is the private token: it is dropped here.
    if not _TOKEN_FORMAT.fullmatch(result):
        raise ValueError("The token has an unexpected format")
    return result


def keyring_username(site_url: str, user_id: int) -> str:
    """Return '<host><path>|<userid>'; the path keeps academic year sites apart (D6)."""
    parsed = urlparse(site_url.rstrip("/"))
    result = f"{parsed.hostname}{parsed.path}|{user_id}"
    return result


def store_token(site_url: str, user_id: int, token: str) -> None:
    """Save the token in the OS keyring.

    Raises: keyring.errors.KeyringError if the keyring cannot store it.
    """
    keyring.set_password(KEYRING_SERVICE, keyring_username(site_url, user_id), token)


def load_token(site_url: str, user_id: int) -> str | None:
    """Return the stored token, or None if there is none.

    Raises: keyring.errors.KeyringError if the keyring cannot be read.
    """
    result = keyring.get_password(KEYRING_SERVICE, keyring_username(site_url, user_id))
    return result


def delete_token(site_url: str, user_id: int) -> bool:
    """Remove the stored token ('log out', RF2); return True if there was one.

    The token stays valid in Moodle until the user revokes it in their
    profile; the interface explains how.

    Raises: keyring.errors.KeyringError if the keyring cannot be changed.
    """
    result = False
    if load_token(site_url, user_id) is not None:
        keyring.delete_password(KEYRING_SERVICE, keyring_username(site_url, user_id))
        result = True
    return result


def login(
    site_url: str,
    pasted_url: str,
    passport: str,
    client_factory: Callable[[str, str], MoodleClient] = MoodleClient,
) -> int:
    """Check the redirect, validate the token with site_info, store it; return the user id.

    Raises: ValueError if the redirect is invalid or the token belongs to
    another site; InvalidTokenError, MoodleError or requests.RequestException
    from the web service call; keyring.errors.KeyringError when storing.
    """
    clean_site = site_url.rstrip("/")
    token = parse_launch_redirect(pasted_url, clean_site, passport)
    with client_factory(clean_site, token) as client:
        site_info = client.call("core_webservice_get_site_info")
    if str(site_info.get("siteurl", "")).rstrip("/") != clean_site:
        raise ValueError("The token belongs to a different site URL")
    result = int(site_info["userid"])
    store_token(clean_site, result, token)
    return result
