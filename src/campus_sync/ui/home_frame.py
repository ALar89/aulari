"""Main screen: course selection, destination folder, sync with progress (RF3, RF10).

Network and disk work runs in the Worker thread; this frame only touches
widgets from the main thread, in the worker callbacks.
"""

import functools
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import keyring.errors

from campus_sync.app import AppError, CampusApp, CourseChoice, SyncResult
from campus_sync.moodle_client import InvalidTokenError
from campus_sync.sync import NotOwnerError, Progress
from campus_sync.texts import text
from campus_sync.ui.messages import error_message
from campus_sync.ui.summary import course_label, progress_text, summary_text
from campus_sync.ui.worker import Worker

PADDING = 12
WRAP_PIXELS = 520


class HomeFrame(ttk.Frame):
    """Courses, destination, sync button, progress and summary."""

    def __init__(
        self,
        parent: tk.Misc,
        app: CampusApp,
        worker: Worker,
        on_logged_out: Callable[[], None],
    ) -> None:
        """Build the widgets and start loading the course list."""
        super().__init__(parent, padding=PADDING)
        self._app = app
        self._worker = worker
        self._on_logged_out = on_logged_out
        self._choices: list[CourseChoice] = []
        self._checks: dict[int, tk.BooleanVar] = {}
        self._cancel = threading.Event()
        self._destination = tk.StringVar(value=app.settings.destination)
        self._status = tk.StringVar()
        self._build()
        self._load_courses()

    # --- widgets -------------------------------------------------------

    def _build(self) -> None:
        """Create and place the widgets."""
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text=text("logged_in_as", site=self._app.settings.site_url)).pack(
            side="left"
        )
        self._logout_button = ttk.Button(top, text=text("logout_button"), command=self._logout)
        self._logout_button.pack(side="right")

        ttk.Label(self, text=text("courses_title"), font=("", 12, "bold")).pack(
            anchor="w", pady=(PADDING, 0)
        )
        self._courses = ttk.Frame(self)
        self._courses.pack(fill="x")

        ttk.Label(self, text=text("destination_label"), font=("", 12, "bold")).pack(
            anchor="w", pady=(PADDING, 0)
        )
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self._destination, state="readonly").pack(
            side="left", fill="x", expand=True
        )
        self._choose_button = ttk.Button(
            row, text=text("destination_choose"), command=self._choose_destination
        )
        self._choose_button.pack(side="left", padx=(PADDING, 0))

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", pady=(PADDING, 0))
        self._sync_button = ttk.Button(buttons, text=text("sync_button"), command=self._sync)
        self._sync_button.pack(side="left")
        self._restore_button = ttk.Button(
            buttons,
            text=text("restore_button"),
            command=functools.partial(self._sync, restore_missing=True),
        )
        self._restore_button.pack(side="left", padx=(PADDING, 0))
        self._cancel_button = ttk.Button(
            buttons, text=text("cancel_button"), command=self._cancel_sync, state="disabled"
        )
        self._cancel_button.pack(side="left", padx=(PADDING, 0))
        self._progress = ttk.Progressbar(self, mode="determinate")
        self._progress.pack(fill="x", pady=(PADDING, 0))
        ttk.Label(self, textvariable=self._status, wraplength=WRAP_PIXELS, justify="left").pack(
            anchor="w", pady=(PADDING, 0)
        )

    def _set_busy(self, busy: bool, syncing: bool = False) -> None:
        """Enable or disable the controls while a background task runs."""
        state = "disabled" if busy else "normal"
        self._sync_button.configure(state=state)
        self._restore_button.configure(state=state)
        self._choose_button.configure(state=state)
        self._logout_button.configure(state=state)
        self._cancel_button.configure(state="normal" if syncing else "disabled")
        for child in self._courses.winfo_children():
            child.configure(state=state)

    # --- courses -------------------------------------------------------

    def _load_courses(self) -> None:
        """Fetch the course list in the background."""
        self._set_busy(True)
        self._status.set(text("courses_loading"))
        self._worker.start(self._app.list_courses, self._courses_loaded, self._task_failed)

    def _courses_loaded(self, choices: object) -> None:
        """Main thread: show one checkbox per course."""
        self._choices = list(choices)
        for child in self._courses.winfo_children():
            child.destroy()
        self._checks = {}
        for choice in self._choices:
            variable = tk.BooleanVar(value=choice.selected)
            self._checks[choice.course_id] = variable
            ttk.Checkbutton(
                self._courses,
                text=course_label(choice),
                variable=variable,
                command=self._selection_changed,
            ).pack(anchor="w")
        new_courses = [choice for choice in self._choices if choice.is_new]
        self._status.set(text("courses_new") if new_courses else "")
        if not self._choices:
            self._status.set(text("courses_none"))
        self._set_busy(False)

    def _selection_changed(self) -> None:
        """Save the selection every time a checkbox changes."""
        selected = [course_id for course_id, var in self._checks.items() if var.get()]
        self._app.set_selection(selected)

    # --- destination ---------------------------------------------------

    def _choose_destination(self) -> None:
        """Ask for the destination folder and remember it."""
        folder = filedialog.askdirectory(
            parent=self, title=text("destination_label"), mustexist=True
        )
        if folder:
            self._app.set_destination(Path(folder))
            self._destination.set(self._app.settings.destination)

    # --- sync ----------------------------------------------------------

    def _sync(self, restore_missing: bool = False) -> None:
        """Start a sync of the selected courses in the background.

        restore_missing also downloads again the files deleted locally (RF8).
        """
        self._cancel = threading.Event()
        self._progress.configure(value=0, maximum=1)
        self._status.set(text("sync_starting"))
        self._set_busy(True, syncing=True)
        task = functools.partial(
            self._app.run_sync, self._choices, self._worker.report, self._cancel, restore_missing
        )
        self._worker.start(task, self._sync_done, self._sync_failed, self._sync_progress)

    def _cancel_sync(self) -> None:
        """Ask the sync to stop after the current file."""
        self._cancel.set()
        self._cancel_button.configure(state="disabled")
        self._status.set(text("sync_cancelling"))

    def _sync_progress(self, report: object) -> None:
        """Main thread: update the progress bar and line."""
        if isinstance(report, Progress):
            self._progress.configure(maximum=max(report.total, 1), value=report.done + 1)
            self._status.set(progress_text(report))

    def _sync_done(self, result: object) -> None:
        """Main thread: show the summary."""
        self._set_busy(False)
        if isinstance(result, SyncResult):
            self._status.set(summary_text(result))
            if result.summary.invalid_token:
                self._session_expired()

    def _sync_failed(self, error: Exception) -> None:
        """Main thread: offer to take over a folder owned by another computer."""
        self._set_busy(False)
        if isinstance(error, NotOwnerError):
            if messagebox.askyesno(text("app_title"), error_message(error), parent=self):
                self._take_over_and_sync()
            else:
                self._status.set("")
        else:
            self._task_failed(error)

    def _take_over_and_sync(self) -> None:
        """Make this computer the owner of the folder (D2) and sync again."""
        try:
            self._app.take_over()
            self._sync()
        except (AppError, OSError) as error:
            self._status.set(error_message(error))

    # --- errors and session --------------------------------------------

    def _task_failed(self, error: Exception) -> None:
        """Main thread: show an error; an expired session goes back to login."""
        self._set_busy(False)
        self._status.set(error_message(error))
        expired = isinstance(error, InvalidTokenError) or (
            isinstance(error, AppError) and error.key == "error_invalid_token"
        )
        if expired:
            self._session_expired()

    def _session_expired(self) -> None:
        """Tell the user to log in again and show the login screen."""
        messagebox.showinfo(text("app_title"), text("error_invalid_token"), parent=self)
        self._on_logged_out()

    def _logout(self) -> None:
        """Delete the token and explain how long it stays valid in the campus."""
        try:
            self._app.logout()
            messagebox.showinfo(text("app_title"), text("logout_done"), parent=self)
            self._on_logged_out()
        except keyring.errors.KeyringError as error:
            messagebox.showerror(text("app_title"), error_message(error), parent=self)
