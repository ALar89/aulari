"""Main screen: course selection, destination folder, sync with progress (RF3, RF10).

Network and disk work runs in the Worker thread; this frame only touches
widgets from the main thread, in the worker callbacks.
"""

import functools
import os
import sys
import threading
import tkinter as tk
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import keyring.errors

from aulari import organizer
from aulari.app import AppError, CampusApp, CourseChoice, SyncResult
from aulari.extras import Announcement, CourseGrade
from aulari.moodle_client import InvalidTokenError
from aulari.scheduler import ALLOWED_HOURS, SchedulerError
from aulari.sync import NotOwnerError, Progress
from aulari.texts import text
from aulari.ui.messages import error_message
from aulari.ui.news_window import NewsWindow
from aulari.ui.summary import (
    announcement_rows,
    course_label,
    deadline_line,
    grade_rows,
    item_rows,
    progress_text,
    submission_rows,
    summary_text,
    sync_all_text,
)
from aulari.ui.table_window import AnnouncementsWindow, TableWindow
from aulari.ui.worker import Worker

PADDING = 12


def site_label(site_url: str) -> str:
    """Return the academic year of a site ('2026-27'), or its URL if it has none."""
    year = organizer.academic_year_from_site(site_url)
    result = year if year is not None else site_url
    return result


def open_in_explorer(folder: Path) -> None:
    """Show a folder in the system file explorer (Windows: Explorer)."""
    if sys.platform == "win32":
        os.startfile(folder)  # noqa: S606 - opens a folder, never a downloaded file
    else:
        webbrowser.open(folder.as_uri())


WRAP_PIXELS = 520


class HomeFrame(ttk.Frame):
    """Courses, destination, sync button, progress and summary."""

    def __init__(
        self,
        parent: tk.Misc,
        app: CampusApp,
        worker: Worker,
        on_logged_out: Callable[[], None],
        on_site_changed: Callable[[], None],
    ) -> None:
        """Build the widgets and start loading the course list."""
        super().__init__(parent, padding=PADDING)
        self._app = app
        self._worker = worker
        self._on_logged_out = on_logged_out
        self._on_site_changed = on_site_changed
        self._site_urls = {site_label(url): url for url in app.sites()}
        self._site = tk.StringVar(value=site_label(app.settings.site_url))
        self._choices: list[CourseChoice] = []
        self._checks: dict[int, tk.BooleanVar] = {}
        self._cancel = threading.Event()
        self._destination = tk.StringVar(value=app.settings.destination)
        self._status = tk.StringVar()
        self._deadlines = tk.StringVar()
        self._announcements: list[Announcement] = []
        self._grades: list[CourseGrade] = []
        self._build()
        self._load_courses()

    # --- widgets -------------------------------------------------------

    def _build(self) -> None:
        """Create and place the widgets."""
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text=text("site_label")).pack(side="left")
        self._site_box = ttk.Combobox(
            top, textvariable=self._site, values=list(self._site_urls), state="readonly", width=12
        )
        self._site_box.pack(side="left", padx=(PADDING // 2, 0))
        self._site_box.bind("<<ComboboxSelected>>", self._site_selected)
        self._add_site_button = ttk.Button(
            top, text=text("add_site_button"), command=self._add_site
        )
        self._add_site_button.pack(side="left", padx=(PADDING, 0))
        self._remove_site_button = ttk.Button(
            top, text=text("remove_site_button"), command=self._remove_site
        )
        self._remove_site_button.pack(side="left", padx=(PADDING // 2, 0))
        self._logout_button = ttk.Button(top, text=text("logout_button"), command=self._logout)
        self._logout_button.pack(side="right")

        ttk.Label(self, text=text("courses_title"), font=("", 12, "bold")).pack(
            anchor="w", pady=(PADDING, 0)
        )
        self._courses = ttk.Frame(self)
        self._courses.pack(fill="x")

        ttk.Label(self, text=text("deadlines_title"), font=("", 12, "bold")).pack(
            anchor="w", pady=(PADDING, 0)
        )
        ttk.Label(self, textvariable=self._deadlines, justify="left").pack(anchor="w")

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
        self._open_button = ttk.Button(row, text=text("open_folder"), command=self._open_folder)
        self._open_button.pack(side="left", padx=(PADDING, 0))

        self._build_auto_row()

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", pady=(PADDING, 0))
        self._sync_button = ttk.Button(buttons, text=text("sync_button"), command=self._sync)
        self._sync_button.pack(side="left")
        self._sync_all_button = ttk.Button(
            buttons, text=text("sync_all_button"), command=self._sync_all
        )
        self._sync_all_button.pack(side="left", padx=(PADDING // 2, 0))
        self._restore_button = ttk.Button(
            buttons,
            text=text("restore_button"),
            command=functools.partial(self._sync, restore_missing=True),
        )
        self._restore_button.pack(side="left", padx=(PADDING, 0))
        self._news_button = ttk.Button(buttons, text=text("news_button"), command=self._show_news)
        self._news_button.pack(side="left", padx=(PADDING, 0))
        self._cancel_button = ttk.Button(
            buttons, text=text("cancel_button"), command=self._cancel_sync, state="disabled"
        )
        self._cancel_button.pack(side="left", padx=(PADDING, 0))

        extra = ttk.Frame(self)
        extra.pack(fill="x", pady=(PADDING // 2, 0))
        self._announcements_button = ttk.Button(
            extra, text=text("announcements_button"), command=self._show_announcements
        )
        self._announcements_button.pack(side="left")
        self._submissions_button = ttk.Button(
            extra, text=text("submissions_button"), command=self._load_submissions
        )
        self._submissions_button.pack(side="left", padx=(PADDING, 0))
        self._grades_button = ttk.Button(
            extra, text=text("grades_button"), command=self._load_grades
        )
        self._grades_button.pack(side="left", padx=(PADDING, 0))
        self._progress = ttk.Progressbar(self, mode="determinate")
        self._progress.pack(fill="x", pady=(PADDING, 0))
        ttk.Label(self, textvariable=self._status, wraplength=WRAP_PIXELS, justify="left").pack(
            anchor="w", pady=(PADDING, 0)
        )

    def _build_auto_row(self) -> None:
        """Create the automatic sync checkbox and interval selector (D7)."""
        auto_row = ttk.Frame(self)
        auto_row.pack(fill="x", pady=(PADDING // 2, 0))
        self._auto = tk.BooleanVar(value=self._app.settings.auto_sync_hours > 0)
        self._auto_check = ttk.Checkbutton(
            auto_row, text=text("auto_sync_label"), variable=self._auto, command=self._auto_changed
        )
        self._auto_check.pack(side="left")
        self._auto_labels = {text(f"auto_every_{hours}"): hours for hours in ALLOWED_HOURS}
        current = self._app.settings.auto_sync_hours or ALLOWED_HOURS[0]
        self._auto_interval = tk.StringVar(value=text(f"auto_every_{current}"))
        self._auto_box = ttk.Combobox(
            auto_row,
            textvariable=self._auto_interval,
            values=list(self._auto_labels),
            state="readonly",
            width=16,
        )
        self._auto_box.pack(side="left", padx=(PADDING // 2, 0))
        self._auto_box.bind("<<ComboboxSelected>>", self._auto_changed)

    def _set_busy(self, busy: bool, syncing: bool = False) -> None:
        """Enable or disable the controls while a background task runs."""
        state = "disabled" if busy else "normal"
        self._sync_button.configure(state=state)
        self._sync_all_button.configure(state=state)
        self._restore_button.configure(state=state)
        self._news_button.configure(state=state)
        self._announcements_button.configure(state=state)
        self._submissions_button.configure(state=state)
        self._grades_button.configure(state=state)
        self._choose_button.configure(state=state)
        self._open_button.configure(state=state)
        self._logout_button.configure(state=state)
        self._add_site_button.configure(state=state)
        self._remove_site_button.configure(state=state)
        self._site_box.configure(state="disabled" if busy else "readonly")
        self._cancel_button.configure(state="normal" if syncing else "disabled")
        for row in self._courses.winfo_children():
            for widget in row.winfo_children():
                widget.configure(state=state)

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
            row = ttk.Frame(self._courses)
            row.pack(fill="x")
            ttk.Checkbutton(
                row,
                text=course_label(choice),
                variable=variable,
                command=self._selection_changed,
            ).pack(side="left", anchor="w")
            if choice.alias:
                # 'Abrir' has a fixed width, so it goes at the right edge and stays
                # aligned; the alias button, whose width varies, goes to its left.
                ttk.Button(
                    row,
                    text=text("open_course_folder"),
                    command=functools.partial(self._open_course_folder, choice),
                ).pack(side="right")
                ttk.Button(
                    row,
                    text=text("alias_button", alias=choice.alias),
                    command=functools.partial(self._ask_alias, choice),
                ).pack(side="right", padx=(0, PADDING // 2))
        new_courses = [choice for choice in self._choices if choice.is_new]
        self._status.set(text("courses_new") if new_courses else "")
        if not self._choices:
            self._status.set(text("courses_none"))
        self._load_deadlines()

    # --- upcoming deadlines (RF15) -------------------------------------

    def _load_deadlines(self) -> None:
        """Fetch the upcoming deadlines in the background (controls stay disabled)."""
        self._set_busy(True)
        self._deadlines.set(text("deadlines_loading"))
        task = functools.partial(self._app.upcoming_deadlines, self._choices)
        self._worker.start(task, self._deadlines_loaded, self._deadlines_failed)

    def _deadlines_loaded(self, deadlines: object) -> None:
        """Main thread: show one line per deadline."""
        lines = [deadline_line(deadline) for deadline in deadlines]
        self._deadlines.set("\n".join(lines) if lines else text("deadlines_none"))
        self._set_busy(False)

    def _deadlines_failed(self, error: Exception) -> None:
        """Main thread: deadlines are optional; show a short note and go on."""
        self._deadlines.set(text("deadlines_unavailable"))
        self._task_failed(error)

    def _ask_alias(self, choice: CourseChoice) -> None:
        """Ask for a new folder alias and rename the course folder (RF4)."""
        new_alias = simpledialog.askstring(
            text("app_title"),
            text("alias_prompt", course=choice.fullname),
            initialvalue=choice.alias,
            parent=self,
        )
        if new_alias and new_alias.strip() != choice.alias:
            self._set_busy(True)
            task = functools.partial(self._app.rename_course, choice.course_id, new_alias)
            self._worker.start(task, self._alias_done, self._task_failed)

    def _alias_done(self, alias: object) -> None:
        """Main thread: the folder was renamed; reload the list to show it."""
        self._worker.start(self._app.list_courses, self._courses_loaded, self._task_failed)
        self._status.set(text("alias_done", alias=alias))

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

    def _open_folder(self) -> None:
        """Open the destination folder in the file explorer (RF13).

        Only the folder is opened, never a downloaded file.
        """
        folder = Path(self._app.settings.destination) if self._app.settings.destination else None
        if folder is not None and folder.is_dir():
            open_in_explorer(folder)
        else:
            self._status.set(text("error_no_destination"))

    def _open_course_folder(self, choice: CourseChoice) -> None:
        """Open the folder of one course in the file explorer."""
        folder = self._app.course_folder(choice)
        if folder is not None:
            open_in_explorer(folder)
        else:
            self._status.set(text("course_folder_missing"))

    def _show_news(self) -> None:
        """Show Novedades.txt in a scrollable window inside the app."""
        path = self._app.news_file()
        if path is None:
            self._status.set(text("news_none"))
        else:
            try:
                NewsWindow(self, path)
            except OSError as error:
                self._status.set(error_message(error))

    def _auto_changed(self, _event: tk.Event | None = None) -> None:
        """Create, change or delete the automatic sync task (D7)."""
        hours = self._auto_labels.get(self._auto_interval.get(), ALLOWED_HOURS[0])
        wanted = hours if self._auto.get() else 0
        try:
            self._app.set_auto_sync(wanted)
            key = "auto_sync_on" if wanted else "auto_sync_off"
            self._status.set(text(key, label=self._auto_interval.get()))
        except (SchedulerError, ValueError, OSError) as error:
            self._auto.set(self._app.settings.auto_sync_hours > 0)
            self._status.set(error_message(error))

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

    def _sync_all(self) -> None:
        """Sync every saved academic year in the background, then show one summary."""
        self._cancel = threading.Event()
        self._progress.configure(value=0, maximum=1)
        self._status.set(text("sync_starting"))
        self._set_busy(True, syncing=True)
        task = functools.partial(self._app.run_sync_all, self._worker.report, self._cancel)
        self._worker.start(task, self._sync_all_done, self._task_failed, self._sync_progress)

    def _sync_all_done(self, results: object) -> None:
        """Main thread: show the per-year summary and any new announcements."""
        self._set_busy(False)
        items = list(results)
        self._status.set(sync_all_text(items))
        self._announcements = []
        for item in items:
            if item.result is not None:
                self._announcements.extend(item.result.announcements)
        self._load_deadlines()
        if self._announcements and messagebox.askyesno(
            text("app_title"),
            text("announcements_notice", count=len(self._announcements)),
            parent=self,
        ):
            self._show_announcements()

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
            self._announcements = result.announcements
            if result.summary.invalid_token:
                self._session_expired()
            else:
                self._load_deadlines()
                if result.announcements and messagebox.askyesno(
                    text("app_title"),
                    text("announcements_notice", count=len(result.announcements)),
                    parent=self,
                ):
                    self._show_announcements()

    # --- announcements, submissions, grades (RF16-RF18) ----------------

    def _show_announcements(self) -> None:
        """Show the announcements found by the last sync (RF16)."""
        if self._announcements:
            rows, messages = announcement_rows(self._announcements)
            AnnouncementsWindow(self, rows, messages)
        else:
            self._status.set(text("announcements_none"))

    def _load_submissions(self) -> None:
        """Fetch the submission state of the selected courses in the background (RF17)."""
        self._set_busy(True)
        self._status.set(text("loading"))
        task = functools.partial(self._app.submissions, self._choices)
        self._worker.start(task, self._submissions_loaded, self._task_failed)

    def _submissions_loaded(self, rows: object) -> None:
        """Main thread: open the submissions table."""
        self._set_busy(False)
        self._status.set("" if rows else text("no_rows"))
        columns = [
            (text("column_course"), 10),
            (text("column_task"), 40),
            (text("column_due"), 16),
            (text("column_status"), 12),
            (text("column_grade"), 10),
            (text("column_comments"), 11),
        ]
        TableWindow(self, text("submissions_title"), columns, submission_rows(list(rows)))

    def _load_grades(self) -> None:
        """Fetch the overall grade of each course in the background (RF18)."""
        self._set_busy(True)
        self._status.set(text("loading"))
        task = functools.partial(self._app.course_grades, self._choices)
        self._worker.start(task, self._grades_loaded, self._task_failed)

    def _grades_loaded(self, grades: object) -> None:
        """Main thread: open the grades table; a double click shows the detail."""
        self._set_busy(False)
        self._status.set("")
        self._grades = list(grades)
        columns = [(text("column_course"), 60), (text("column_grade"), 12)]
        TableWindow(
            self, text("grades_title"), columns, grade_rows(self._grades), self._load_grade_items
        )

    def _load_grade_items(self, index: int) -> None:
        """Fetch the grade report of the course in row index (RF18)."""
        if not self._worker.is_busy():
            grade = self._grades[index]
            self._set_busy(True)
            self._status.set(text("loading"))
            task = functools.partial(self._app.grade_items, grade.course_id)
            done = functools.partial(self._grade_items_loaded, grade.course_name)
            self._worker.start(task, done, self._task_failed)

    def _grade_items_loaded(self, course_name: str, items: object) -> None:
        """Main thread: open the grade report of one course."""
        self._set_busy(False)
        self._status.set("")
        columns = [
            (text("column_item"), 45),
            (text("column_grade"), 10),
            (text("column_range"), 10),
            (text("column_percentage"), 12),
        ]
        title = text("grade_items_title", course=course_name)
        TableWindow(self, title, columns, item_rows(list(items)))

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

    # --- academic year sites (RF19) -------------------------------------

    def _site_selected(self, _event: tk.Event) -> None:
        """Switch to the academic year chosen in the selector."""
        url = self._site_urls.get(self._site.get(), self._app.settings.site_url)
        try:
            self._app.switch_site(url)
            self._on_site_changed()
        except (AppError, OSError) as error:
            self._status.set(error_message(error))

    def _add_site(self) -> None:
        """Ask for the URL of another academic year and log in to it."""
        url = simpledialog.askstring(
            text("app_title"),
            text("add_site_prompt"),
            initialvalue=self._app.suggested_site_url(),
            parent=self,
        )
        if url:
            try:
                self._app.add_site(url)
                self._on_site_changed()
            except (AppError, OSError) as error:
                self._status.set(error_message(error))

    def _remove_site(self) -> None:
        """Forget the current academic year (and its token) after confirming."""
        label = site_label(self._app.settings.site_url)
        if messagebox.askyesno(
            text("app_title"), text("remove_site_confirm", site=label), parent=self
        ):
            try:
                self._app.remove_site(self._app.settings.site_url)
                self._on_site_changed()
            except (AppError, OSError, keyring.errors.KeyringError) as error:
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
