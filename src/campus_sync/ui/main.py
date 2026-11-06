"""Main window: shows the login screen or the main screen (phase 4).

Start with:  python -m campus_sync
"""

import json
import logging
import os
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import keyring.errors

from campus_sync.app import CampusApp
from campus_sync.log_filter import configure_logging
from campus_sync.texts import text
from campus_sync.ui import url_handler
from campus_sync.ui.home_frame import HomeFrame
from campus_sync.ui.login_frame import LoginFrame
from campus_sync.ui.messages import error_message
from campus_sync.ui.worker import Worker, log_thread_exceptions

MIN_WIDTH = 620
MIN_HEIGHT = 560


def default_app_dir() -> Path:
    """Return %LOCALAPPDATA%\\campus-sync (the home folder as a fallback)."""
    base = os.environ.get("LOCALAPPDATA")
    result = Path(base) / "campus-sync" if base else Path.home() / ".campus-sync"
    return result


class MainWindow:
    """Owns the root window and switches between screens."""

    def __init__(self, root: tk.Tk, app: CampusApp) -> None:
        """Show the login screen or the main screen depending on the stored token."""
        self._root = root
        self._app = app
        self._worker = Worker(root)
        self._frame = None
        self._remove_leftover_handler()
        logged_in = False
        try:
            logged_in = app.is_logged_in()
        except keyring.errors.KeyringError as error:
            messagebox.showerror(text("app_title"), error_message(error))
        if logged_in:
            self.show_home()
        else:
            self.show_login()

    def _remove_leftover_handler(self) -> None:
        """Remove a moodlemobile:// registration left by a closed or crashed login."""
        try:
            url_handler.unregister(self._app.app_dir)
        except OSError:
            # Not Windows, or the registry is not writable: nothing to clean.
            logging.getLogger(__name__).debug("No URL handler cleanup possible")

    def _show(self, frame: ttk.Frame) -> None:
        """Replace the current screen with frame."""
        if self._frame is not None:
            self._frame.destroy()
        self._frame = frame
        self._frame.pack(fill="both", expand=True)

    def show_login(self) -> None:
        """Show the login screen."""
        self._show(LoginFrame(self._root, self._app, self._worker, self.show_home))

    def show_home(self) -> None:
        """Show the main screen."""
        self._show(HomeFrame(self._root, self._app, self._worker, self.show_login))


def run() -> None:
    """Start the application."""
    configure_logging()
    log_thread_exceptions()
    root = tk.Tk()
    root.title(text("app_title"))
    root.minsize(MIN_WIDTH, MIN_HEIGHT)
    try:
        app = CampusApp(default_app_dir())
        MainWindow(root, app)
        root.mainloop()
    except json.JSONDecodeError:
        messagebox.showerror(text("app_title"), text("error_config_corrupt"))
        root.destroy()
