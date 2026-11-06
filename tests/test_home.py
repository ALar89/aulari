"""Tests for the main screen texts and a smoke test of HomeFrame with a fake app."""

import threading
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk

from campus_sync.app import CourseChoice, SyncResult
from campus_sync.config import Settings
from campus_sync.sync import Progress, SyncSummary
from campus_sync.texts import text
from campus_sync.ui import summary
from campus_sync.ui.home_frame import HomeFrame
from campus_sync.ui.worker import Worker

TOKEN = "0123456789abcdef0123456789abcdef"
CHOICES = [
    CourseChoice(1072, "Sistemes Operatius", True, False, True),
    CourseChoice(999, "Xarxes", False, True, False),
]
TIMEOUT_MS = 3000


class SummaryTextTests(unittest.TestCase):
    """Tests for course_label(), progress_text() and summary_text()."""

    def test_new_course_is_marked(self) -> None:
        """New courses get the '(nueva)' tag; others do not."""
        self.assertEqual(summary.course_label(CHOICES[0]), "Sistemes Operatius")
        self.assertTrue(summary.course_label(CHOICES[1]).endswith(text("course_new_tag")))

    def test_progress_line(self) -> None:
        """The line shows the alias, a 1-based counter and the file name."""
        line = summary.progress_text(Progress("2026-27/SO", 2, 34, "Tema 1.pdf"))
        self.assertEqual(
            line, text("sync_progress", course="SO", done=3, total=34, filename="Tema 1.pdf")
        )

    def test_summary_with_calendar(self) -> None:
        """Counts and the calendar line appear; zero optional counts are hidden."""
        result = SyncResult(
            SyncSummary(new=2, unchanged=31), Path("C:/Campus/2026-27/calendari.ics"), 6
        )
        lines = summary.summary_text(result).splitlines()
        self.assertIn(text("summary_new", count=2), lines)
        self.assertIn(text("summary_errors", count=0), lines)
        self.assertNotIn(text("summary_adopted", count=0), lines)
        self.assertTrue(lines[-1].startswith(text("calendar_done", count=6, path="")[:20]))

    def test_errors_are_limited_and_redacted(self) -> None:
        """At most five errors are listed, and secrets never reach the screen."""
        errors = [f"file{index}.pdf: bad token={TOKEN}" for index in range(8)]
        text_shown = summary.summary_text(SyncResult(SyncSummary(errors=errors)))
        self.assertEqual(text_shown.count("•"), summary.MAX_ERRORS_SHOWN)
        self.assertIn(text("summary_more_errors", count=3), text_shown)
        self.assertNotIn(TOKEN, text_shown)

    def test_expired_and_cancelled(self) -> None:
        """An expired token and a cancel are reported first."""
        lines = summary.summary_text(
            SyncResult(SyncSummary(invalid_token=True, cancelled=True))
        ).splitlines()
        self.assertEqual(lines[:2], [text("error_invalid_token"), text("summary_cancelled")])


class FakeApp:
    """Stand-in for CampusApp with the methods HomeFrame uses."""

    def __init__(self) -> None:
        """Start with a destination and no saved selection."""
        self.settings = Settings(
            site_url="https://moodle.example.test/2026-27", destination="C:/Campus"
        )
        self.selection = None
        self.synced_with = None

    def list_courses(self) -> list[CourseChoice]:
        """Return the two test courses."""
        return list(CHOICES)

    def set_selection(self, course_ids: list[int]) -> None:
        """Remember the selection."""
        self.selection = sorted(course_ids)

    def run_sync(self, courses: list, progress: object, cancel: threading.Event) -> SyncResult:
        """Report one progress step and return a summary."""
        self.synced_with = courses
        progress(Progress("2026-27/SO", 0, 1, "Tema 1.pdf"))
        return SyncResult(SyncSummary(new=1))


class HomeFrameSmokeTests(unittest.TestCase):
    """HomeFrame builds, loads courses, saves the selection and shows a summary."""

    def setUp(self) -> None:
        """Create a hidden root and the frame, or skip without a display."""
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("No display available for Tkinter")
        self.root.withdraw()
        self.app = FakeApp()
        self.frame = HomeFrame(self.root, self.app, Worker(self.root), lambda: None)
        self.frame.pack()

    def tearDown(self) -> None:
        """Destroy the root window."""
        self.root.destroy()

    def pump_until(self, condition: object) -> None:
        """Run the event loop until condition() is true or time runs out."""

        def check() -> None:
            """Stop when the condition holds, otherwise check again soon."""
            if condition():
                self.root.quit()
            else:
                self.root.after(20, check)

        self.root.after(TIMEOUT_MS, self.root.quit)
        self.root.after(20, check)
        self.root.mainloop()

    def checkbuttons(self) -> list[ttk.Checkbutton]:
        """Return the course checkboxes."""
        return list(self.frame._courses.winfo_children())

    def test_courses_selection_and_sync(self) -> None:
        """Two checkboxes appear; ticking saves; syncing shows the summary."""
        self.pump_until(lambda: len(self.checkbuttons()) == len(CHOICES))
        self.assertEqual(len(self.checkbuttons()), len(CHOICES))
        self.assertEqual(self.frame._status.get(), text("courses_new"))
        self.checkbuttons()[1].invoke()
        self.assertEqual(self.app.selection, [999, 1072])
        self.frame._sync()
        self.pump_until(lambda: text("summary_new", count=1) in self.frame._status.get())
        self.assertIn(text("summary_new", count=1), self.frame._status.get())
        self.assertEqual(str(self.frame._sync_button.cget("state")), "normal")


if __name__ == "__main__":
    unittest.main()
