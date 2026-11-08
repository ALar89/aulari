"""Login screen: browser flow with launch.php, received automatically (RF2, D1).

'Open browser' registers this app for moodlemobile:// (url_handler), opens a
local socket (login_handoff) and opens launch.php. When the browser asks to
open Aulari, the callback process sends the URL here, and the window
checks it in the background. Registration and socket are removed when the
login ends, fails, times out or the screen closes.

A hidden manual paste field stays available as a fallback. The pasted URL
contains the token: the entry shows dots and is cleared right after reading.
On macOS the automatic login is not implemented yet: the manual fallback is
shown from the start, with a button that copies the launch.php URL (it holds
no token). The user pastes it into a browser tab whose developer tools are
already recording, so the redirect is captured even when the campus session
is still open and the browser redirects at once.
"""

import functools
import logging
import sys
import tkinter as tk
import webbrowser
from collections.abc import Callable
from tkinter import ttk

from aulari import auth
from aulari.app import CampusApp
from aulari.texts import text
from aulari.ui import url_handler
from aulari.ui.login_handoff import HandoffServer
from aulari.ui.messages import error_message
from aulari.ui.worker import Worker

PADDING = 12
WRAP_PIXELS = 480
SECRET_CHAR = "•"
POLL_MS = 300
WAIT_LIMIT_MS = 10 * 60 * 1000  # stop waiting for the browser after 10 minutes

logger = logging.getLogger(__name__)


def is_macos() -> bool:
    """Return True when running on macOS."""
    result = sys.platform == "darwin"
    return result


def system_text(key: str) -> str:
    """Return the text for key, or its '<key>_macos' variant on macOS.

    Raises: KeyError if the text is missing.
    """
    variant = f"{key}_macos" if is_macos() else key
    result = text(variant)
    return result


class LoginFrame(ttk.Frame):
    """Frame with the site URL, the login steps and the manual fallback."""

    def __init__(
        self,
        parent: tk.Misc,
        app: CampusApp,
        worker: Worker,
        on_logged_in: Callable[[], None],
        on_back: Callable[[], None] | None = None,
    ) -> None:
        """Build the widgets."""
        super().__init__(parent, padding=PADDING)
        self._app = app
        self._worker = worker
        self._on_logged_in = on_logged_in
        self._on_back = on_back
        self._passport = ""
        self._server = None
        self._waited_ms = 0
        self._site = tk.StringVar(value=app.settings.site_url)
        self._status = tk.StringVar()
        self._manual_visible = False
        self._copy_button = None
        self._build()
        self.bind("<Destroy>", self._on_destroy)

    # --- widgets -------------------------------------------------------

    def _build(self) -> None:
        """Create and place the widgets in a single column."""
        ttk.Label(self, text=text("login_title"), font=("", 14, "bold")).pack(anchor="w")
        ttk.Label(self, text=text("login_site_label")).pack(anchor="w", pady=(PADDING, 0))
        ttk.Entry(self, textvariable=self._site, width=60).pack(fill="x")
        ttk.Label(
            self, text=system_text("login_steps"), wraplength=WRAP_PIXELS, justify="left"
        ).pack(anchor="w", pady=PADDING)
        self._browser_button = ttk.Button(
            self, text=text("login_open_browser"), command=self._open_browser
        )
        self._browser_button.pack(anchor="w")
        if self._on_back is not None and self._app.previous_site() is not None:
            ttk.Button(self, text=text("login_back"), command=self._back).pack(
                anchor="w", pady=(PADDING // 2, 0)
            )
        ttk.Label(
            self, textvariable=self._status, wraplength=WRAP_PIXELS, foreground="#1a5fb4"
        ).pack(anchor="w", pady=(PADDING, 0))
        self._manual_toggle = ttk.Button(
            self, text=text("login_manual_toggle"), command=self._toggle_manual
        )
        self._manual_toggle.pack(anchor="w", pady=(PADDING, 0))
        self._manual = ttk.Frame(self)
        ttk.Label(
            self._manual,
            text=system_text("login_manual_steps"),
            wraplength=WRAP_PIXELS,
            justify="left",
        ).pack(anchor="w")
        if is_macos():
            self._copy_button = ttk.Button(
                self._manual, text=text("login_copy_launch"), command=self._copy_launch_url
            )
            self._copy_button.pack(anchor="w", pady=(PADDING // 2, PADDING // 2))
        self._paste = ttk.Entry(self._manual, show=SECRET_CHAR, width=60)
        self._paste.pack(fill="x")
        self._paste.bind("<Return>", self._on_return)
        self._login_button = ttk.Button(
            self._manual, text=text("login_button"), command=self._login
        )
        self._login_button.pack(anchor="w", pady=(PADDING, 0))
        if is_macos():
            self._toggle_manual()  # the only way to log in on macOS for now

    def _toggle_manual(self) -> None:
        """Show or hide the manual paste fallback."""
        self._manual_visible = not self._manual_visible
        if self._manual_visible:
            self._manual.pack(anchor="w", fill="x", pady=(PADDING, 0))
        else:
            self._manual.pack_forget()

    def _set_busy(self, busy: bool) -> None:
        """Enable or disable the buttons while a check is running."""
        state = "disabled" if busy else "normal"
        self._browser_button.configure(state=state)
        self._login_button.configure(state=state)
        if self._copy_button is not None:
            self._copy_button.configure(state=state)

    # --- waiting for the browser ----------------------------------------

    def _start_waiting(self) -> None:
        """Register the URL handler and open the local socket.

        If this fails, the manual fallback is shown instead.
        """
        try:
            url_handler.register(self._app.app_dir)
            self._server = HandoffServer(self._app.app_dir)
            self._waited_ms = 0
            self.after(POLL_MS, self._poll_browser)
            self._status.set(text("login_waiting"))
        except OSError as error:
            logger.warning("Automatic login not available: %s", error)
            self._stop_waiting()
            self._status.set(text("login_auto_unavailable"))
            if not self._manual_visible:
                self._toggle_manual()

    def _stop_waiting(self) -> None:
        """Close the socket and remove the URL handler (safe to call twice)."""
        if self._server is not None:
            self._server.close()
            self._server = None
        if sys.platform == "win32":  # only Windows registers a handler
            try:
                url_handler.unregister(self._app.app_dir)
            except OSError as error:
                logger.warning("Could not remove the moodlemobile:// handler: %s", error)

    def _poll_browser(self) -> None:
        """Main thread: check the socket; give up after WAIT_LIMIT_MS."""
        if self._server is not None:
            url = None
            try:
                url = self._server.poll()
            except OSError as error:
                logger.warning("Login handoff failed: %s", error)
            self._waited_ms = self._waited_ms + POLL_MS
            if url is not None:
                self._stop_waiting()
                self._check(url)
            elif self._waited_ms >= WAIT_LIMIT_MS:
                self._stop_waiting()
                self._status.set(text("login_timeout"))
            else:
                self.after(POLL_MS, self._poll_browser)

    def _on_destroy(self, event: tk.Event) -> None:
        """Clean up when this screen (not one of its children) is destroyed."""
        if event.widget is self:
            self._stop_waiting()

    # --- actions -------------------------------------------------------

    def _new_attempt(self) -> str:
        """Save the site URL and start a new login attempt; return its launch.php URL."""
        self._stop_waiting()
        self._app.set_site_url(self._site.get())
        self._site.set(self._app.settings.site_url)
        launch_url, self._passport = self._app.start_login()
        return launch_url

    def _open_browser(self) -> None:
        """Start a new attempt, wait for the browser and open it."""
        launch_url = self._new_attempt()
        self._start_waiting()
        webbrowser.open(launch_url)

    def _copy_launch_url(self) -> None:
        """macOS: start a new attempt and copy its launch.php URL (it holds no token)."""
        launch_url = self._new_attempt()
        self.clipboard_clear()
        self.clipboard_append(launch_url)
        self._status.set(text("login_copied"))

    def _back(self) -> None:
        """Return to the academic year that was active before (RF19)."""
        previous = self._app.previous_site()
        if previous is not None and self._on_back is not None:
            self._stop_waiting()
            self._app.switch_site(previous)
            self._on_back()

    def _on_return(self, _event: tk.Event) -> None:
        """Pressing Enter in the paste field logs in."""
        self._login()

    def _login(self) -> None:
        """Manual fallback: read and clear the pasted URL, then check it."""
        pasted = self._paste.get().strip()
        self._paste.delete(0, tk.END)
        if not self._passport:
            self._status.set(text("login_need_browser_first"))
        elif pasted:
            self._stop_waiting()
            self._check(pasted)

    def _check(self, redirect_url: str) -> None:
        """Check a redirect URL in the background."""
        logger.info("Login redirect received: %s", auth.redirect_shape(redirect_url))
        self._set_busy(True)
        self._status.set(text("login_working"))
        task = functools.partial(self._app.finish_login, redirect_url, self._passport)
        self._worker.start(task, self._login_done, self._login_failed)

    def _login_done(self, _user_id: object) -> None:
        """Main thread: the token is stored; go to the main screen."""
        self._passport = ""
        self._set_busy(False)
        self._on_logged_in()

    def _login_failed(self, error: Exception) -> None:
        """Main thread: show what went wrong and let the user try again."""
        logger.warning("Login failed: %s: %s", type(error).__name__, error)
        self._set_busy(False)
        self._status.set(error_message(error))
