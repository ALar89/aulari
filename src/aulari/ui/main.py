"""Main window: shows the login screen or the main screen (phase 4).

Start with:  python -m aulari
"""

import json
import logging
import os
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import keyring.errors

from aulari.app import CampusApp
from aulari.log_filter import configure_logging
from aulari.texts import text
from aulari.ui import url_handler
from aulari.ui.home_frame import HomeFrame
from aulari.ui.login_frame import LoginFrame
from aulari.ui.messages import error_message
from aulari.ui.worker import Worker, log_thread_exceptions

LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "aulari.log"
MIN_WIDTH = 620
MIN_HEIGHT = 560


def default_app_dir() -> Path:
    """Return %LOCALAPPDATA%\\aulari (the home folder as a fallback)."""
    base = os.environ.get("LOCALAPPDATA")
    result = Path(base) / "aulari" if base else Path.home() / ".aulari"
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
        self._first_run_notice()
        logged_in = False
        try:
            logged_in = app.is_logged_in()
        except keyring.errors.KeyringError as error:
            messagebox.showerror(text("app_title"), error_message(error))
        self._show_for_session(logged_in)

    def _first_run_notice(self) -> None:
        """Show once that the material is for personal use only (RF12)."""
        if not self._app.settings.first_run_notice_seen:
            messagebox.showinfo(text("app_title"), text("first_run_notice"), parent=self._root)
            try:
                self._app.accept_first_run_notice()
            except OSError as error:
                logging.getLogger(__name__).warning("Could not save settings: %s", error)

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

    def _show_for_session(self, logged_in: bool) -> None:
        """Show the main screen when logged in, otherwise the login screen."""
        if logged_in:
            self.show_home()
        else:
            self.show_login()

    def show_start(self) -> None:
        """Show the right screen for the active site (after switching academic year)."""
        logged_in = False
        try:
            logged_in = self._app.is_logged_in()
        except keyring.errors.KeyringError as error:
            messagebox.showerror(text("app_title"), error_message(error))
        self._show_for_session(logged_in)

    def show_login(self) -> None:
        """Show the login screen."""
        self._show(LoginFrame(self._root, self._app, self._worker, self.show_home, self.show_start))

    def show_home(self) -> None:
        """Show the main screen."""
        self._show(HomeFrame(self._root, self._app, self._worker, self.show_login, self.show_start))


def run() -> None:
    """Start the application."""
    configure_logging(log_file=default_app_dir() / LOG_DIR_NAME / LOG_FILE_NAME)
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
