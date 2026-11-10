"""Tests for the parts of the interface that can run without a person.

messages.error_message() is pure. Worker needs a Tk root; the tests are
skipped where no display is available (for example, a headless CI).
"""

import os
import tempfile
import threading
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import keyring.errors
import requests

from aulari.app import AppError
from aulari.moodle_client import InvalidTokenError, MoodleError
from aulari.sync import NotOwnerError, SyncLockedError
from aulari.texts import text
from aulari.ui.login_frame import LoginFrame, system_text
from aulari.ui.main import default_app_dir
from aulari.ui.messages import error_message
from aulari.ui.worker import UnexpectedError, Worker

TOKEN = "0123456789abcdef0123456789abcdef"
TIMEOUT_MS = 3000


class ErrorMessageTests(unittest.TestCase):
    """Tests for error_message()."""

    def test_known_errors(self) -> None:
        """Each kind of error gets its own text."""
        cases = [
            (AppError("error_no_destination"), text("error_no_destination")),
            (InvalidTokenError("invalidtoken", "x"), text("error_invalid_token")),
            (NotOwnerError("PC-CASA"), text("error_not_owner", host="PC-CASA")),
            (SyncLockedError("locked"), text("error_locked_short")),
            (keyring.errors.KeyringError("x"), text("error_keyring")),
            (ValueError("Signature mismatch"), text("login_invalid_redirect")),
            (RuntimeError("bug"), text("error_unexpected")),
        ]
        for error, expected in cases:
            self.assertEqual(error_message(error), expected, type(error).__name__)

    def test_network_error_shows_only_its_type(self) -> None:
        """Network errors show the error type, never the URL inside the message."""
        error = requests.ConnectionError(f"https://x.test/a?token={TOKEN}")
        message = error_message(error)
        self.assertIn("ConnectionError", message)
        self.assertNotIn(TOKEN, message)

    def test_moodle_error_is_redacted(self) -> None:
        """A Moodle error text goes through the redaction filter."""
        message = error_message(MoodleError("x", f"bad wstoken={TOKEN}"))
        self.assertNotIn(TOKEN, message)


class AppDirTests(unittest.TestCase):
    """Tests for default_app_dir() on each system (sys.platform is faked)."""

    def test_macos_uses_application_support(self) -> None:
        """macOS keeps the data in ~/Library/Application Support, even with LOCALAPPDATA."""
        expected = Path.home() / "Library" / "Application Support" / "aulari"
        with (
            mock.patch("sys.platform", "darwin"),
            mock.patch.dict(os.environ, {"LOCALAPPDATA": "/elsewhere"}),
        ):
            self.assertEqual(default_app_dir(), expected)

    def test_windows_uses_localappdata(self) -> None:
        """Windows keeps the data in %LOCALAPPDATA%\\aulari, as before."""
        base = Path("C:/Users/student/AppData/Local")
        with (
            mock.patch("sys.platform", "win32"),
            mock.patch.dict(os.environ, {"LOCALAPPDATA": str(base)}),
        ):
            self.assertEqual(default_app_dir(), base / "aulari")

    def test_other_systems_use_hidden_home_folder(self) -> None:
        """Without LOCALAPPDATA (Linux, for example) the data goes to ~/.aulari."""
        with mock.patch("sys.platform", "linux"), mock.patch.dict(os.environ, clear=False):
            os.environ.pop("LOCALAPPDATA", None)
            self.assertEqual(default_app_dir(), Path.home() / ".aulari")


class FakeLoginApp:
    """The few CampusApp members LoginFrame uses; each attempt gets a new passport."""

    def __init__(self, app_dir: Path) -> None:
        """Start with the default site and no attempts."""
        self.app_dir = app_dir
        self.settings = SimpleNamespace(site_url="https://moodle.example.test/2026-27")
        self.attempts = 0

    def set_site_url(self, site_url: str) -> None:
        """Remember the site URL typed in the screen."""
        self.settings.site_url = site_url

    def start_login(self) -> tuple[str, str]:
        """Return a launch URL and passport that change on every attempt."""
        self.attempts = self.attempts + 1
        passport = f"passport{self.attempts}"
        result = (f"{self.settings.site_url}/admin/tool/mobile/launch.php?p={passport}", passport)
        return result

    def previous_site(self) -> None:
        """No other academic year to go back to."""
        return None


class LoginFrameMacTests(unittest.TestCase):
    """The macOS copy button of LoginFrame (sys.platform is faked, clipboard is mocked)."""

    def setUp(self) -> None:
        """Create a hidden root and a fake app, or skip without a display."""
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("No display available for Tkinter")
        self.root.withdraw()
        self.folder = tempfile.TemporaryDirectory()
        self.app = FakeLoginApp(Path(self.folder.name))

    def tearDown(self) -> None:
        """Destroy the root window and delete the folder."""
        self.root.destroy()
        self.folder.cleanup()

    def test_copy_button_copies_a_new_attempt(self) -> None:
        """On macOS the manual part is open and each press copies a fresh launch URL."""
        with mock.patch("sys.platform", "darwin"):
            frame = LoginFrame(self.root, self.app, Worker(self.root), lambda: None)
            self.assertTrue(frame._manual_visible)
            with (
                mock.patch.object(frame, "clipboard_clear"),
                mock.patch.object(frame, "clipboard_append") as append,
            ):
                frame._copy_button.invoke()
                frame._copy_button.invoke()
            status = frame._status.get()
            frame.destroy()
        self.assertEqual(status, text("login_copied"))
        copied = [call.args[0] for call in append.call_args_list]
        self.assertEqual(self.app.attempts, 2)
        self.assertTrue(copied[-1].endswith("launch.php?p=passport2"))
        self.assertNotEqual(copied[0], copied[1])
        self.assertEqual(frame._passport, "passport2")

    def test_no_copy_button_elsewhere(self) -> None:
        """Outside macOS the screen is unchanged: no copy button, manual part hidden."""
        with mock.patch("sys.platform", "linux"):
            frame = LoginFrame(self.root, self.app, Worker(self.root), lambda: None)
            self.assertIsNone(frame._copy_button)
            self.assertFalse(frame._manual_visible)
            frame.destroy()


class SystemTextTests(unittest.TestCase):
    """Tests for system_text(): the login steps depend on the system."""

    def test_macos_gets_its_own_steps(self) -> None:
        """On macOS the manual steps are the Chrome ones, without F12."""
        with mock.patch("sys.platform", "darwin"):
            steps = system_text("login_manual_steps")
        self.assertEqual(steps, text("login_manual_steps_macos"))
        self.assertNotIn("F12", steps)

    def test_windows_keeps_its_steps(self) -> None:
        """On Windows (and elsewhere) the texts are the usual ones."""
        for platform in ("win32", "linux"):
            with mock.patch("sys.platform", platform):
                self.assertEqual(system_text("login_steps"), text("login_steps"))
                self.assertEqual(system_text("login_manual_steps"), text("login_manual_steps"))


class WorkerTests(unittest.TestCase):
    """Tests for Worker with a real (hidden) Tk root."""

    def setUp(self) -> None:
        """Create a hidden root window, or skip if there is no display."""
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("No display available for Tkinter")
        self.root.withdraw()
        self.worker = Worker(self.root)
        self.events = []
        self.main_thread = threading.current_thread()

    def tearDown(self) -> None:
        """Destroy the root window."""
        self.root.destroy()

    def record(self, kind: str) -> callable:
        """Return a callback that records (kind, value, ran in main thread)."""

        def callback(value: object) -> None:
            """Store the call."""
            self.events.append((kind, value, threading.current_thread() is self.main_thread))

        return callback

    def wait_until_finished(self) -> None:
        """Run the Tk event loop until a done/error callback arrives or time runs out."""
        self.root.after(TIMEOUT_MS, self.root.quit)
        self.root.after(10, self.check_finished)
        self.root.mainloop()

    def check_finished(self) -> None:
        """Stop the event loop once the task has finished."""
        kinds = [event[0] for event in self.events]
        if "done" in kinds or "error" in kinds:
            self.root.quit()
        else:
            self.root.after(10, self.check_finished)

    def test_result_and_progress_arrive_in_main_thread(self) -> None:
        """Progress and result callbacks run in the main thread, in order."""

        def task() -> int:
            """Report twice and return a value."""
            self.worker.report(1)
            self.worker.report(2)
            return 42

        self.worker.start(task, self.record("done"), self.record("error"), self.record("progress"))
        self.wait_until_finished()
        self.assertEqual(
            self.events, [("progress", 1, True), ("progress", 2, True), ("done", 42, True)]
        )
        self.assertFalse(self.worker.is_busy())

    def test_expected_error_is_delivered(self) -> None:
        """An expected error reaches on_error."""

        def task() -> None:
            """Fail like a wrong redirect."""
            raise ValueError("Signature mismatch")

        self.worker.start(task, self.record("done"), self.record("error"))
        self.wait_until_finished()
        self.assertEqual(self.events[0][0], "error")
        self.assertIsInstance(self.events[0][1], ValueError)

    def test_unexpected_error_does_not_hang(self) -> None:
        """A bug in the task still ends with on_error(UnexpectedError)."""

        def task() -> None:
            """Fail with an error nobody expects."""
            raise ZeroDivisionError("bug")

        previous_hook = threading.excepthook
        threading.excepthook = lambda args: None  # keep the test output clean
        try:
            self.worker.start(task, self.record("done"), self.record("error"))
            self.wait_until_finished()
        finally:
            threading.excepthook = previous_hook
        self.assertEqual(self.events[0][0], "error")
        self.assertIsInstance(self.events[0][1], UnexpectedError)

    def test_only_one_task_at_a_time(self) -> None:
        """Starting a second task while one runs raises RuntimeError."""
        gate = threading.Event()
        self.worker.start(gate.wait, self.record("done"), self.record("error"))
        with self.assertRaises(RuntimeError):
            self.worker.start(lambda: None, self.record("done"), self.record("error"))
        gate.set()
        self.wait_until_finished()


if __name__ == "__main__":
    unittest.main()
