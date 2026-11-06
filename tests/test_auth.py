"""Unit tests for campus_sync.auth, with an in-memory keyring and no network."""

import base64
import hashlib
import unittest
from urllib.parse import parse_qs, quote, urlparse

import keyring
from keyring.backend import KeyringBackend

from campus_sync import auth

SITE = "https://moodle.example.test/2026-27"
PASSPORT = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
TOKEN = "0123456789abcdef0123456789abcdef"
PRIVATE_TOKEN = "PRIVATEtokenNEVERstored0123456789"
USER_ID = 42


class MemoryKeyring(KeyringBackend):
    """Keyring backend that keeps passwords in a dict (never touches Windows)."""

    priority = 1

    def __init__(self) -> None:
        """Start with an empty store."""
        super().__init__()
        self.store = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        """Save a password."""
        self.store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        """Return a password or None."""
        return self.store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        """Remove a password."""
        del self.store[(service, username)]


def make_redirect(site: str, passport: str, token: str, private: str | None = None) -> str:
    """Build a moodlemobile:// redirect exactly as launch.php does."""
    signature = hashlib.md5((site + passport).encode("utf-8")).hexdigest()
    parts = [signature, token] if private is None else [signature, token, private]
    payload = base64.b64encode(":::".join(parts).encode("utf-8")).decode("ascii")
    return f"moodlemobile://token={payload}"


class FakeClient:
    """Stand-in for MoodleClient that answers site_info with canned data."""

    instances = []

    def __init__(self, site_url: str, token: str) -> None:
        """Record how the client was created."""
        self.site_url = site_url
        self.token = token
        self.site_info = {"userid": USER_ID, "siteurl": SITE}
        FakeClient.instances.append(self)

    def __enter__(self) -> FakeClient:
        """Support 'with'."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Nothing to close."""

    def call(self, function: str) -> dict:
        """Return site_info."""
        return self.site_info


class KeyringTestCase(unittest.TestCase):
    """Base class that swaps the real keyring for a MemoryKeyring."""

    def setUp(self) -> None:
        """Install the in-memory keyring."""
        self.previous_keyring = keyring.get_keyring()
        self.memory = MemoryKeyring()
        keyring.set_keyring(self.memory)
        FakeClient.instances = []

    def tearDown(self) -> None:
        """Restore the original keyring."""
        keyring.set_keyring(self.previous_keyring)


class LaunchUrlTests(unittest.TestCase):
    """Tests for new_passport() and build_launch_url()."""

    def test_passports_are_random(self) -> None:
        """Two passports are different and long enough."""
        first = auth.new_passport()
        self.assertNotEqual(first, auth.new_passport())
        self.assertEqual(len(first), 2 * auth.PASSPORT_BYTES)

    def test_launch_url(self) -> None:
        """The URL points to launch.php with service, passport and scheme."""
        url = auth.build_launch_url(SITE + "/", PASSPORT)
        parsed = urlparse(url)
        self.assertEqual(parsed.path, "/2026-27/admin/tool/mobile/launch.php")
        query = parse_qs(parsed.query)
        self.assertEqual(query["service"], ["moodle_mobile_app"])
        self.assertEqual(query["passport"], [PASSPORT])
        self.assertEqual(query["urlscheme"], ["moodlemobile"])


class ParseRedirectTests(unittest.TestCase):
    """Tests for parse_launch_redirect()."""

    def test_valid_redirect_returns_token_only(self) -> None:
        """The token is returned and the private token is dropped."""
        url = make_redirect(SITE, PASSPORT, TOKEN, PRIVATE_TOKEN)
        self.assertEqual(auth.parse_launch_redirect(url, SITE, PASSPORT), TOKEN)

    def test_url_encoded_and_unpadded_payload(self) -> None:
        """Browsers may percent-encode the payload or drop '=' padding."""
        url = make_redirect(SITE, PASSPORT, TOKEN)
        prefix, payload = url.split("token=")
        self.assertEqual(
            auth.parse_launch_redirect(prefix + "token=" + quote(payload), SITE, PASSPORT), TOKEN
        )
        self.assertEqual(auth.parse_launch_redirect(url.rstrip("="), SITE, PASSPORT), TOKEN)

    def test_trailing_slash_from_browser_is_removed(self) -> None:
        """Windows/browser add a '/' after the payload (seen on e-aules); it is ignored."""
        url = make_redirect(SITE, PASSPORT, TOKEN, PRIVATE_TOKEN) + "/"
        self.assertEqual(auth.parse_launch_redirect(url, SITE, PASSPORT), TOKEN)

    def test_slash_that_belongs_to_the_data_is_kept(self) -> None:
        """A '/' inside a correctly padded payload is data, not an extra slash."""
        payload = base64.b64encode(bytes([255, 255, 255])).decode("ascii")  # "////"
        self.assertEqual(auth._extract_payload("moodlemobile://token=" + payload), "////")

    def test_wrong_passport_or_site_is_rejected(self) -> None:
        """A redirect from another attempt or another site fails the signature."""
        url = make_redirect(SITE, PASSPORT, TOKEN)
        with self.assertRaises(ValueError):
            auth.parse_launch_redirect(url, SITE, "another-passport")
        with self.assertRaises(ValueError):
            auth.parse_launch_redirect(url, "https://moodle.example.test/2025-26", PASSPORT)

    def test_malformed_input_is_rejected(self) -> None:
        """Missing marker, bad base64 or a missing separator raise ValueError."""
        bad_inputs = [
            "https://moodle.example.test/",
            "moodlemobile://token=%%%not-base64%%%",
            "moodlemobile://token=" + base64.b64encode(b"no-separator").decode("ascii"),
        ]
        for pasted in bad_inputs:
            with self.assertRaises(ValueError, msg=pasted):
                auth.parse_launch_redirect(pasted, SITE, PASSPORT)

    def test_strange_token_is_rejected_without_leaking_it(self) -> None:
        """A token with odd characters is rejected and not echoed in the error."""
        odd_token = "abc<script>def0123456789"
        with self.assertRaises(ValueError) as context:
            auth.parse_launch_redirect(make_redirect(SITE, PASSPORT, odd_token), SITE, PASSPORT)
        self.assertNotIn(odd_token, str(context.exception))


class TokenStorageTests(KeyringTestCase):
    """Tests for keyring_username(), store_token(), load_token(), delete_token()."""

    def test_username_keeps_academic_year(self) -> None:
        """Each academic year site gets its own keyring entry (D6)."""
        self.assertEqual(
            auth.keyring_username(SITE + "/", USER_ID), "moodle.example.test/2026-27|42"
        )

    def test_store_load_delete(self) -> None:
        """A stored token can be read back and deleted once."""
        self.assertIsNone(auth.load_token(SITE, USER_ID))
        auth.store_token(SITE, USER_ID, TOKEN)
        self.assertEqual(auth.load_token(SITE, USER_ID), TOKEN)
        self.assertTrue(auth.delete_token(SITE, USER_ID))
        self.assertIsNone(auth.load_token(SITE, USER_ID))
        self.assertFalse(auth.delete_token(SITE, USER_ID))


class LoginTests(KeyringTestCase):
    """Tests for login()."""

    def test_login_stores_only_the_token(self) -> None:
        """login() returns the user id and the keyring holds the token, never the private one."""
        url = make_redirect(SITE, PASSPORT, TOKEN, PRIVATE_TOKEN)
        user_id = auth.login(SITE + "/", url, PASSPORT, client_factory=FakeClient)
        self.assertEqual(user_id, USER_ID)
        self.assertEqual(
            self.memory.store, {("campus-sync", "moodle.example.test/2026-27|42"): TOKEN}
        )
        self.assertNotIn(PRIVATE_TOKEN, repr(self.memory.store))
        self.assertEqual(FakeClient.instances[0].site_url, SITE)

    def test_token_for_another_site_is_not_stored(self) -> None:
        """If site_info reports another siteurl, nothing is stored."""

        class OtherSiteClient(FakeClient):
            """Fake client whose site_info reports a different site."""

            def call(self, function: str) -> dict:
                """Return site_info for another site."""
                return {"userid": USER_ID, "siteurl": "https://other.example.test"}

        with self.assertRaises(ValueError):
            auth.login(
                SITE, make_redirect(SITE, PASSPORT, TOKEN), PASSPORT, client_factory=OtherSiteClient
            )
        self.assertEqual(self.memory.store, {})


class RedirectShapeTests(unittest.TestCase):
    """Tests for redirect_shape() (diagnosis without the token)."""

    def test_shape_never_contains_the_token(self) -> None:
        """Length and odd characters are shown, base64 content is not."""
        url = make_redirect(SITE, PASSPORT, TOKEN, PRIVATE_TOKEN) + "/"
        shape = auth.redirect_shape(url)
        payload = url.split("token=")[1]
        self.assertIn("prefix='moodlemobile://token='", shape)
        self.assertIn("last='/'", shape)
        self.assertNotIn(payload[:8], shape)
        self.assertNotIn(TOKEN, shape)

    def test_percent_encoding_is_visible(self) -> None:
        """Characters like '%' after token= are listed with their position."""
        shape = auth.redirect_shape("moodlemobile://token=abc%3D")
        self.assertIn("'%'@-3", shape)


if __name__ == "__main__":
    unittest.main()
