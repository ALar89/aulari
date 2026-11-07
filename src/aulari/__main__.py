"""Entry point: 'python -m aulari'.

With '--login-callback <url>' (started by Windows for a moodlemobile:// link)
it only hands the URL to the window that is waiting, and exits. The URL is
never printed or logged.

With '--background' (started by the Windows Task Scheduler, decision D7) it
syncs every academic year without a window and shows a small notice only
if there is something new.
"""

import json
import logging
import sys
import tkinter as tk
from tkinter import messagebox

from aulari import scheduler
from aulari.app import CampusApp
from aulari.background import run_background
from aulari.log_filter import configure_logging
from aulari.texts import detect_language, set_language, text
from aulari.ui.login_handoff import send_to_running_app
from aulari.ui.main import LOG_DIR_NAME, LOG_FILE_NAME, default_app_dir, run
from aulari.ui.notice import NoticeWindow
from aulari.ui.summary import notice_text
from aulari.ui.url_handler import CALLBACK_FLAG

CALLBACK_ARGS = 3  # program + flag + URL
BACKGROUND_ARGS = 2  # program + flag


def handle_callback(url: str) -> None:
    """Send the URL to the waiting window, or tell the user to open the app."""
    sent = False
    try:
        sent = send_to_running_app(default_app_dir(), url)
    except OSError, json.JSONDecodeError, KeyError, ValueError:
        sent = False
    if not sent:
        set_language(detect_language())
        root = tk.Tk()
        root.withdraw()  # only the message box, no empty main window
        messagebox.showinfo(text("app_title"), text("callback_no_app"), parent=root)
        root.destroy()


def run_background_mode() -> None:
    """Sync in the background and show a notice only if there is news (D7)."""
    configure_logging(log_file=default_app_dir() / LOG_DIR_NAME / LOG_FILE_NAME)
    try:
        app = CampusApp(default_app_dir())
        set_language(app.settings.language or detect_language())
        outcome = run_background(app)
        if outcome.worth_notifying():
            root = tk.Tk()
            NoticeWindow(root, notice_text(outcome), scheduler.app_command())
            root.mainloop()
    except (OSError, json.JSONDecodeError) as error:
        logging.getLogger("aulari.background").error("Background sync failed: %s", error)


def main(argv: list[str]) -> None:
    """Run the callback mode, the background mode or the full application."""
    if len(argv) == CALLBACK_ARGS and argv[1] == CALLBACK_FLAG:
        handle_callback(argv[2])
    elif len(argv) == BACKGROUND_ARGS and argv[1] == scheduler.BACKGROUND_FLAG:
        run_background_mode()
    else:
        run()


if __name__ == "__main__":
    main(sys.argv)
