"""Small notice window shown after a background sync with news (decision D7).

It appears in the bottom-right corner, stays on top, offers to open Aulari
and closes by itself after a while. It is only shown when there are new
files or announcements, or a session has expired.
"""

import subprocess
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from aulari.texts import text

PADDING = 12
WIDTH = 360
HEIGHT = 150
MARGIN_RIGHT = 20
MARGIN_BOTTOM = 70  # leave room for the Windows taskbar
CLOSE_AFTER_MS = 30_000
WRAP_PIXELS = WIDTH - 2 * PADDING


def open_app(command: list[str]) -> None:
    """Start the full Aulari window as a separate process."""
    subprocess.Popen(command)  # noqa: S603 - our own program, fixed arguments


class NoticeWindow:
    """A small always-on-top window with a message and two buttons."""

    def __init__(
        self,
        root: tk.Tk,
        message: str,
        app_command: list[str],
        launcher: Callable[[list[str]], None] = open_app,
    ) -> None:
        """Place the window in the corner and schedule its automatic close."""
        self._root = root
        self._app_command = app_command
        self._launcher = launcher
        root.title(text("app_title"))
        root.resizable(False, False)
        root.attributes("-topmost", True)
        x = root.winfo_screenwidth() - WIDTH - MARGIN_RIGHT
        y = root.winfo_screenheight() - HEIGHT - MARGIN_BOTTOM
        root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
        frame = ttk.Frame(root, padding=PADDING)
        frame.pack(fill="both", expand=True)
        self.label = ttk.Label(frame, text=message, wraplength=WRAP_PIXELS, justify="left")
        self.label.pack(anchor="w", fill="x", expand=True)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text=text("close_button"), command=root.destroy).pack(side="right")
        ttk.Button(buttons, text=text("notice_open_app"), command=self._open).pack(
            side="right", padx=(0, PADDING // 2)
        )
        root.after(CLOSE_AFTER_MS, root.destroy)

    def _open(self) -> None:
        """Open the full app and close the notice."""
        self._launcher(self._app_command)
        self._root.destroy()
