"""Run slow tasks (network, disk) in a thread and hand results back to Tkinter.

Tkinter is not thread-safe: only the main thread may touch widgets
(CLAUDE.md section 3). The worker thread puts messages in a queue and the
main thread reads them every POLL_MS milliseconds with root.after().
"""

import json
import logging
import queue
import threading
import tkinter as tk
from collections.abc import Callable

import keyring.errors
import requests

from campus_sync.app import AppError
from campus_sync.moodle_client import MoodleError
from campus_sync.registry import RegistryError
from campus_sync.sync import NotOwnerError, SyncLockedError

POLL_MS = 100
MESSAGE_DONE = "done"
MESSAGE_ERROR = "error"
MESSAGE_PROGRESS = "progress"
# Errors a task may raise on purpose; anything else is a bug and is logged.
EXPECTED_ERRORS = (
    AppError,
    MoodleError,
    NotOwnerError,
    SyncLockedError,
    RegistryError,
    requests.RequestException,
    keyring.errors.KeyringError,
    json.JSONDecodeError,
    OSError,
    ValueError,
)

logger = logging.getLogger(__name__)


class UnexpectedError(Exception):
    """The task stopped with an error nobody expected (details are in the log)."""


class Worker:
    """Runs one task at a time in a background thread."""

    def __init__(self, root: tk.Misc) -> None:
        """Remember the Tk root used for root.after()."""
        self._root = root
        self._queue = queue.Queue()
        self._thread = None

    def is_busy(self) -> bool:
        """Return True while a task is running."""
        result = self._thread is not None and self._thread.is_alive()
        return result

    def report(self, value: object) -> None:
        """Send a progress value from the task thread to the main thread."""
        self._queue.put((MESSAGE_PROGRESS, value))

    def _run_task(self, task: Callable[[], object]) -> None:
        """Body of the worker thread: run the task and queue its outcome."""
        try:
            value = task()
            self._queue.put((MESSAGE_DONE, value))
        except EXPECTED_ERRORS as error:
            self._queue.put((MESSAGE_ERROR, error))

    def start(
        self,
        task: Callable[[], object],
        on_done: Callable[[object], None],
        on_error: Callable[[Exception], None],
        on_progress: Callable[[object], None] | None = None,
    ) -> None:
        """Run task in a thread; the callbacks run later in the main thread.

        Raises: RuntimeError if another task is still running.
        """
        if self.is_busy():
            raise RuntimeError("A task is already running")
        self._thread = threading.Thread(target=self._run_task, args=(task,), daemon=True)
        self._thread.start()
        self._root.after(POLL_MS, self._poll, on_done, on_error, on_progress)

    def _poll(
        self,
        on_done: Callable[[object], None],
        on_error: Callable[[Exception], None],
        on_progress: Callable[[object], None] | None,
    ) -> None:
        """Main thread: deliver queued messages; reschedule until the task ends."""
        finished = False
        while not self._queue.empty() and not finished:
            kind, value = self._queue.get()
            if kind == MESSAGE_PROGRESS and on_progress is not None:
                on_progress(value)
            elif kind == MESSAGE_DONE:
                finished = True
                on_done(value)
            elif kind == MESSAGE_ERROR:
                finished = True
                on_error(value)
        thread_died = self._thread is not None and not self._thread.is_alive()
        if not finished and thread_died and self._queue.empty():
            # The thread died with an unexpected exception (already logged).
            finished = True
            on_error(UnexpectedError())
        if not finished:
            self._root.after(POLL_MS, self._poll, on_done, on_error, on_progress)


def log_thread_exceptions() -> None:
    """Send uncaught exceptions of worker threads to the (redacted) log."""

    def hook(args: threading.ExceptHookArgs) -> None:
        """Log the exception with its traceback through the logging filters."""
        logger.error(
            "Unexpected error in a background task",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = hook
