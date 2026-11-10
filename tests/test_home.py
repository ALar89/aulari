"""Tests for the main screen texts and a smoke test of HomeFrame with a fake app."""

import tempfile
import threading
import tkinter as tk
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tkinter import ttk

from aulari.app import CourseChoice, Deadline, SyncResult
from aulari.config import Settings
from aulari.sync import Progress, SyncSummary
from aulari.texts import text
from aulari.ui import summary
from aulari.ui.home_frame import HomeFrame
from aulari.ui.news_window import NewsWindow
from aulari.ui.worker import Worker

TOKEN = "0123456789abcdef0123456789abcdef"
CHOICES = [
    CourseChoice(1072, "Sistemes Operatius", False, True),
    CourseChoice(999, "Xarxes", True, False),
]
TIMEOUT_MS = 3000
DUE = int(datetime(2026, 11, 9, 22, 59, tzinfo=UTC).timestamp())  # Monday 23:59 in Madrid


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

    def test_deadline_line(self) -> None:
        """Weekday and time are shown in Madrid time, with the course alias."""
        self.assertEqual(
            summary.deadline_line(Deadline(DUE, "SO", "Entrega P1")),
            text(
                "deadline_line", weekday="lun", date="09/11 23:59", course="SO", name="Entrega P1"
            ),
        )

    def test_news_line_in_summary(self) -> None:
        """When Novedades.txt was written, the summary says where."""
        result = SyncResult(SyncSummary(new=1), news_path=Path("C:/Campus/2026-27/Novedades.txt"))
        self.assertIn(text("summary_news", path=result.news_path), summary.summary_text(result))


class FakeApp:
    """Stand-in for CampusApp with the methods HomeFrame uses."""

    def __init__(self) -> None:
        """Start with a destination and no saved selection."""
        self.settings = Settings(
            site_url="https://moodle.example.test/2026-27", destination="C:/Campus"
        )
        self.selection = None
        self.synced_with = None

    def sites(self) -> list[str]:
        """Return the only saved site."""
        return [self.settings.site_url]

    def year_folder(self) -> str:
        """Return the academic year folder name (RF20)."""
        return self.settings.year_folder or "2026-27"

    def set_campus_subfolder(self, name: str) -> str:
        """Remember the campus subfolder (RF20)."""
        self.settings.campus_subfolder = name.strip()
        return self.settings.campus_subfolder

    def list_courses(self) -> list[CourseChoice]:
        """Return the two test courses."""
        return list(CHOICES)

    def upcoming_deadlines(self, courses: list[CourseChoice]) -> list[Deadline]:
        """Return one deadline."""
        return [Deadline(DUE, "SO", "Entrega P1")]

    def set_selection(self, course_ids: list[int]) -> None:
        """Remember the selection."""
        self.selection = sorted(course_ids)

    def run_sync(
        self, courses: list, progress: object, cancel: threading.Event, restore: bool = False
    ) -> SyncResult:
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
        self.frame = HomeFrame(self.root, self.app, Worker(self.root), lambda: None, lambda: None)
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
        """Return the course checkboxes (one per row)."""
        return [row.winfo_children()[0] for row in self.frame._courses.winfo_children()]

    def test_courses_selection_and_sync(self) -> None:
        """Two checkboxes appear; ticking saves; syncing shows the summary."""
        # Courses load first, then the deadlines; controls stay disabled until both end.
        self.pump_until(lambda: "Entrega P1" in self.frame._deadlines.get())
        self.assertEqual(len(self.checkbuttons()), len(CHOICES))
        self.assertEqual(self.frame._status.get(), text("courses_new"))
        self.checkbuttons()[1].invoke()
        self.assertEqual(self.app.selection, [999, 1072])
        self.frame._sync()
        self.pump_until(lambda: text("summary_new", count=1) in self.frame._status.get())
        self.assertIn(text("summary_new", count=1), self.frame._status.get())
        self.pump_until(lambda: "Entrega P1" in self.frame._deadlines.get())
        self.assertIn("[SO] Entrega P1", self.frame._deadlines.get())
        self.assertEqual(str(self.frame._sync_button.cget("state")), "normal")

    def test_folder_buttons(self) -> None:
        """The year folder and subfolder buttons show the current names (RF20)."""
        self.pump_until(lambda: "Entrega P1" in self.frame._deadlines.get())
        year_label = self.frame._year_folder_button.cget("text")
        self.assertEqual(year_label, text("year_folder_button", name="2026-27"))
        none = text("subfolder_button", name=text("subfolder_none"))
        self.assertEqual(self.frame._subfolder_button.cget("text"), none)
        self.frame._subfolder_done(self.app.set_campus_subfolder("Campus"))
        campus = text("subfolder_button", name="Campus")
        self.assertEqual(self.frame._subfolder_button.cget("text"), campus)
        self.assertEqual(self.frame._status.get(), text("subfolder_done", name="Campus"))


class NewsWindowTests(unittest.TestCase):
    """NewsWindow shows the file read-only."""

    def test_shows_file_content(self) -> None:
        """The text widget holds the file and cannot be edited."""
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("No display available for Tkinter")
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "Novedades.txt"
                path.write_text("== 2026-11-06 ==\n  [SO] Tema 1.pdf\n", encoding="utf-8")
                window = NewsWindow(root, path)
                self.assertIn("[SO] Tema 1.pdf", window.text.get("1.0", "end"))
                self.assertEqual(str(window.text.cget("state")), "disabled")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
