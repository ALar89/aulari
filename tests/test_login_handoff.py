"""Tests for the automatic login: URL handler registration and the local handoff.

The registry tests never touch the real moodlemobile:// handler: they use
HKCU\\Software\\CampusSyncTest\\<random>, which no browser reads, and delete it.
"""

import json
import secrets
import socket
import sys
import tempfile
import unittest
from pathlib import Path

from campus_sync.ui import login_handoff, url_handler
from campus_sync.ui.login_handoff import HandoffServer
from campus_sync.ui.url_handler import Handler

if sys.platform == "win32":
    import winreg

URL = "moodlemobile://token=c2lnbmF0dXJlOjo6dG9rZW4="
OTHER_HANDLER = Handler("URL:Moodle Mobile", True, '"C:\\Moodle\\moodle.exe" "%1"')


@unittest.skipUnless(sys.platform == "win32", "Windows registry only")
class UrlHandlerTests(unittest.TestCase):
    """register()/unregister() on a throwaway test key."""

    def setUp(self) -> None:
        """Create an empty test base key and an app folder."""
        self.base = f"Software\\CampusSyncTest\\{secrets.token_hex(4)}"
        winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.base).Close()
        self.folder = tempfile.TemporaryDirectory()
        self.app_dir = Path(self.folder.name)

    def tearDown(self) -> None:
        """Delete the whole test key and the app folder."""
        url_handler._delete_tree("Software\\CampusSyncTest")
        self.folder.cleanup()

    def test_register_and_unregister_without_previous_handler(self) -> None:
        """Our handler is written, recognised, and removed completely."""
        self.assertIsNone(url_handler.read_handler(self.base))
        url_handler.register(self.app_dir, self.base)
        handler = url_handler.read_handler(self.base)
        self.assertTrue(url_handler.is_ours(handler))
        self.assertTrue(handler.url_protocol)
        self.assertIn('"%1"', handler.command)
        url_handler.unregister(self.app_dir, self.base)
        self.assertIsNone(url_handler.read_handler(self.base))

    def test_previous_handler_is_restored(self) -> None:
        """Another program's handler is backed up and put back afterwards."""
        url_handler._write_handler(f"{self.base}\\moodlemobile", OTHER_HANDLER)
        url_handler.register(self.app_dir, self.base)
        self.assertTrue(url_handler.is_ours(url_handler.read_handler(self.base)))
        backup = json.loads((self.app_dir / url_handler.BACKUP_NAME).read_text(encoding="utf-8"))
        self.assertNotIn("token", json.dumps(backup).lower())
        url_handler.unregister(self.app_dir, self.base)
        self.assertEqual(url_handler.read_handler(self.base), OTHER_HANDLER)
        self.assertFalse((self.app_dir / url_handler.BACKUP_NAME).exists())

    def test_unregister_leaves_other_programs_alone(self) -> None:
        """If the handler is not ours, unregister() does not touch it."""
        url_handler._write_handler(f"{self.base}\\moodlemobile", OTHER_HANDLER)
        url_handler.unregister(self.app_dir, self.base)
        self.assertEqual(url_handler.read_handler(self.base), OTHER_HANDLER)

    def test_register_twice_keeps_original_backup(self) -> None:
        """Registering again (a second click) does not overwrite the backup with ours."""
        url_handler._write_handler(f"{self.base}\\moodlemobile", OTHER_HANDLER)
        url_handler.register(self.app_dir, self.base)
        url_handler.register(self.app_dir, self.base)
        url_handler.unregister(self.app_dir, self.base)
        self.assertEqual(url_handler.read_handler(self.base), OTHER_HANDLER)


class HandlerCommandTests(unittest.TestCase):
    """Tests for handler_command() and is_ours()."""

    def test_command_runs_this_package_with_the_flag(self) -> None:
        """The command passes the link as one quoted argument."""
        command = url_handler.handler_command()
        self.assertIn("-m campus_sync --login-callback", command)
        self.assertTrue(command.endswith('"%1"'))

    def test_is_ours(self) -> None:
        """Only commands with our flag count as ours."""
        self.assertFalse(url_handler.is_ours(None))
        self.assertFalse(url_handler.is_ours(OTHER_HANDLER))
        self.assertTrue(url_handler.is_ours(Handler("x", True, "app --login-callback %1")))


class HandoffTests(unittest.TestCase):
    """HandoffServer and send_to_running_app() over real local sockets."""

    def setUp(self) -> None:
        """Create an app folder and a waiting server."""
        self.folder = tempfile.TemporaryDirectory()
        self.app_dir = Path(self.folder.name)
        self.server = HandoffServer(self.app_dir)
        info = json.loads((self.app_dir / login_handoff.HANDOFF_NAME).read_text(encoding="utf-8"))
        self.port = info["port"]

    def tearDown(self) -> None:
        """Close the server and delete the folder."""
        self.server.close()
        self.folder.cleanup()

    def send_raw(self, data: bytes) -> None:
        """Connect to the server and send raw bytes."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as connection:
            connection.sendall(data)

    def test_nothing_waiting(self) -> None:
        """poll() returns None immediately when nobody connected."""
        self.assertIsNone(self.server.poll())

    def test_url_is_delivered(self) -> None:
        """The callback process delivers the URL; the file never holds it."""
        self.assertNotIn("token", (self.app_dir / login_handoff.HANDOFF_NAME).read_text())
        self.assertTrue(login_handoff.send_to_running_app(self.app_dir, URL))
        self.assertEqual(self.server.poll(), URL)

    def test_wrong_key_is_ignored(self) -> None:
        """A message with another key is dropped and the server keeps waiting."""
        self.send_raw(json.dumps({"key": "not-the-key", "url": URL}).encode("utf-8"))
        self.assertIsNone(self.server.poll())
        self.assertTrue(login_handoff.send_to_running_app(self.app_dir, URL))
        self.assertEqual(self.server.poll(), URL)

    def test_garbage_and_huge_messages_are_ignored(self) -> None:
        """Non-JSON or oversized messages give None."""
        self.send_raw(b"\xff\xfe not json")
        self.assertIsNone(self.server.poll())
        self.send_raw(b"x" * (login_handoff.MAX_MESSAGE_BYTES + 100))
        self.assertIsNone(self.server.poll())

    def test_close_removes_file_and_refuses(self) -> None:
        """After close(), the callback reports that no window is waiting."""
        self.server.close()
        self.assertFalse((self.app_dir / login_handoff.HANDOFF_NAME).exists())
        self.assertFalse(login_handoff.send_to_running_app(self.app_dir, URL))

    def test_stale_file_is_detected(self) -> None:
        """A leftover file pointing to a closed port is treated as 'no window'."""
        path = self.app_dir / login_handoff.HANDOFF_NAME
        self.server.close()
        path.write_text(json.dumps({"port": self.port, "key": "k"}), encoding="utf-8")
        self.assertFalse(login_handoff.send_to_running_app(self.app_dir, URL))


if __name__ == "__main__":
    unittest.main()
