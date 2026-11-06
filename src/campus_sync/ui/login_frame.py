"""Login screen: browser flow with launch.php and a hidden paste field (RF2, D1).

The pasted moodlemobile:// URL contains the token, so the entry shows dots,
is cleared right after reading it, and is never logged.
"""

import functools
import tkinter as tk
import webbrowser
from collections.abc import Callable
from tkinter import ttk

from campus_sync.app import CampusApp
from campus_sync.texts import text
from campus_sync.ui.messages import error_message
from campus_sync.ui.worker import Worker

PADDING = 12
WRAP_PIXELS = 480
SECRET_CHAR = "•"


class LoginFrame(ttk.Frame):
    """Frame with the site URL, the login steps and the paste field."""

    def __init__(
        self,
        parent: tk.Misc,
        app: CampusApp,
        worker: Worker,
        on_logged_in: Callable[[], None],
    ) -> None:
        """Build the widgets."""
        super().__init__(parent, padding=PADDING)
        self._app = app
        self._worker = worker
        self._on_logged_in = on_logged_in
        self._passport = ""
        self._site = tk.StringVar(value=app.settings.site_url)
        self._status = tk.StringVar()
        self._build()

    def _build(self) -> None:
        """Create and place the widgets in a single column."""
        ttk.Label(self, text=text("login_title"), font=("", 14, "bold")).pack(anchor="w")
        ttk.Label(self, text=text("login_site_label")).pack(anchor="w", pady=(PADDING, 0))
        ttk.Entry(self, textvariable=self._site, width=60).pack(fill="x")
        ttk.Label(self, text=text("login_steps"), wraplength=WRAP_PIXELS, justify="left").pack(
            anchor="w", pady=PADDING
        )
        self._browser_button = ttk.Button(
            self, text=text("login_open_browser"), command=self._open_browser
        )
        self._browser_button.pack(anchor="w")
        ttk.Label(self, text=text("login_paste_label")).pack(anchor="w", pady=(PADDING, 0))
        self._paste = ttk.Entry(self, show=SECRET_CHAR, width=60)
        self._paste.pack(fill="x")
        self._paste.bind("<Return>", self._on_return)
        self._login_button = ttk.Button(self, text=text("login_button"), command=self._login)
        self._login_button.pack(anchor="w", pady=(PADDING, 0))
        ttk.Label(
            self, textvariable=self._status, wraplength=WRAP_PIXELS, foreground="#b00020"
        ).pack(anchor="w", pady=(PADDING, 0))

    def _set_busy(self, busy: bool) -> None:
        """Enable or disable the buttons while a check is running."""
        state = "disabled" if busy else "normal"
        self._browser_button.configure(state=state)
        self._login_button.configure(state=state)

    def _open_browser(self) -> None:
        """Save the site URL, start a new attempt and open the system browser."""
        self._app.set_site_url(self._site.get())
        self._site.set(self._app.settings.site_url)
        launch_url, self._passport = self._app.start_login()
        webbrowser.open(launch_url)
        self._status.set("")

    def _on_return(self, _event: tk.Event) -> None:
        """Pressing Enter in the paste field logs in."""
        self._login()

    def _login(self) -> None:
        """Read and clear the pasted URL, then check it in the background."""
        pasted = self._paste.get().strip()
        self._paste.delete(0, tk.END)
        if not self._passport:
            self._status.set(text("login_need_browser_first"))
        elif pasted:
            self._set_busy(True)
            self._status.set(text("login_working"))
            task = functools.partial(self._app.finish_login, pasted, self._passport)
            self._worker.start(task, self._login_done, self._login_failed)

    def _login_done(self, _user_id: object) -> None:
        """Main thread: the token is stored; go to the main screen."""
        self._passport = ""
        self._set_busy(False)
        self._on_logged_in()

    def _login_failed(self, error: Exception) -> None:
        """Main thread: show what went wrong and let the user try again."""
        self._set_busy(False)
        self._status.set(error_message(error))
