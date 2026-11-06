"""Tests for the parts of the interface that can run without a person.

messages.error_message() is pure. Worker needs a Tk root; the tests are
skipped where no display is available (for example, a headless CI).
"""

import threading
import tkinter as tk
import unittest

import keyring.errors
import requests

from campus_sync.app import AppError
from campus_sync.moodle_client import InvalidTokenError, MoodleError
from campus_sync.sync import NotOwnerError, SyncLockedError
from campus_sync.texts import text
from campus_sync.ui.messages import error_message
from campus_sync.ui.worker import UnexpectedError, Worker

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
