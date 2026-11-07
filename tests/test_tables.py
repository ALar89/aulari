"""Tests for the RF16-RF18 table texts and windows."""

import tkinter as tk
import unittest
from datetime import UTC, datetime

from aulari.app import SyncResult
from aulari.extras import Announcement, CourseGrade, GradeItem, SubmissionRow
from aulari.sync import SyncSummary
from aulari.texts import text
from aulari.ui import summary
from aulari.ui.table_window import AnnouncementsWindow, TableWindow

WHEN = int(datetime(2026, 11, 9, 22, 59, tzinfo=UTC).timestamp())  # 23:59 in Madrid


class RowTextTests(unittest.TestCase):
    """Formatting of table rows and the announcements summary line."""

    def test_announcement_rows(self) -> None:
        """Rows show date, course and subject; messages go separately."""
        rows, messages = summary.announcement_rows([Announcement("SO", "Examen", "Dilluns", WHEN)])
        self.assertEqual(rows, [("09/11/2026 23:59", "SO", "Examen")])
        self.assertEqual(messages, ["Dilluns"])

    def test_submission_rows(self) -> None:
        """Statuses are translated, missing dates become a dash."""
        rows = summary.submission_rows(
            [
                SubmissionRow("SO", "P1", WHEN, "submitted", "8,50", True),
                SubmissionRow("EC", "P2", 0, "new", "—", False),
            ]
        )
        self.assertEqual(
            rows[0], ("SO", "P1", "09/11/2026 23:59", text("status_submitted"), "8,50", text("yes"))
        )
        self.assertEqual(rows[1], ("EC", "P2", "—", text("status_new"), "—", ""))

    def test_grade_and_item_rows(self) -> None:
        """Grades and grade items keep their order and values."""
        self.assertEqual(summary.grade_rows([CourseGrade(1, "SO", "7,25")]), [("SO", "7,25")])
        items = [GradeItem("P1", "8,50", "0–10", "85,00 %")]
        self.assertEqual(summary.item_rows(items), [("P1", "8,50", "0–10", "85,00 %")])

    def test_summary_mentions_announcements(self) -> None:
        """New announcements, or a check error, appear in the sync summary."""
        found = SyncResult(SyncSummary(), announcements=[Announcement("SO", "A", "", WHEN)])
        self.assertIn(text("summary_announcements", count=1), summary.summary_text(found))
        failed = SyncResult(SyncSummary(), announcements_error="accessexception")
        self.assertIn(
            text("announcements_error", error="accessexception"), summary.summary_text(failed)
        )


class WindowTests(unittest.TestCase):
    """TableWindow and AnnouncementsWindow with a hidden root."""

    def setUp(self) -> None:
        """Create a hidden root, or skip without a display."""
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("No display available for Tkinter")
        self.root.withdraw()

    def tearDown(self) -> None:
        """Destroy the root."""
        self.root.destroy()

    def test_table_and_double_click(self) -> None:
        """Rows are shown and a double click calls on_open with the row index."""
        opened = []
        window = TableWindow(
            self.root, "T", [("A", 5), ("B", 5)], [("1", "x"), ("2", "y")], opened.append
        )
        children = window.tree.get_children()
        self.assertEqual([window.tree.item(child)["values"][1] for child in children], ["x", "y"])
        window.tree.selection_set(children[1])
        window._double_click(None)
        self.assertEqual(opened, [1])

    def test_announcement_message_follows_selection(self) -> None:
        """The first message is shown; selecting another row shows its message."""
        rows = [("d1", "SO", "Primer"), ("d2", "EC", "Segon")]
        window = AnnouncementsWindow(self.root, rows, ["text 1", "text 2"])
        window._show_message()
        self.assertEqual(window.message.get("1.0", "end").strip(), "text 1")
        window.tree.selection_set(window.tree.get_children()[1])
        window._show_message()
        self.assertEqual(window.message.get("1.0", "end").strip(), "text 2")


if __name__ == "__main__":
    unittest.main()
