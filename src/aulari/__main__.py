"""Entry point: 'python -m aulari'.

With '--login-callback <url>' (started by Windows for a moodlemobile:// link)
it only hands the URL to the window that is waiting, and exits. The URL is
never printed or logged.
"""

import json
import sys
import tkinter as tk
from tkinter import messagebox

from aulari.texts import detect_language, set_language, text
from aulari.ui.login_handoff import send_to_running_app
from aulari.ui.main import default_app_dir, run
from aulari.ui.url_handler import CALLBACK_FLAG

CALLBACK_ARGS = 3  # program + flag + URL


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


def main(argv: list[str]) -> None:
    """Run the callback mode or the full application."""
    if len(argv) == CALLBACK_ARGS and argv[1] == CALLBACK_FLAG:
        handle_callback(argv[2])
    else:
        run()


if __name__ == "__main__":
    main(sys.argv)
