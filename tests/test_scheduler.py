"""Tests for automatic sync (D7): task definition, background runs and the notice.

No test touches the real Windows Task Scheduler: schtasks is replaced by a
fake runner that only records the commands.
"""

import subprocess
import tkinter as tk
import unittest
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from aulari import background, scheduler
from aulari.app import AppError, SiteSyncResult, SyncResult
from aulari.background import BackgroundOutcome
from aulari.config import Settings
from aulari.extras import Announcement
from aulari.sync import SyncSummary
from aulari.texts import text
from aulari.ui.notice import NoticeWindow
from aulari.ui.summary import notice_text

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
DAY = 24 * 3600


class FakeRunner:
    """Records schtasks calls; the task 'exists' after /Create until /Delete."""

    def __init__(self, fail: bool = False) -> None:
        """Start with no task."""
        self.calls = []
        self.exists = False
        self.fail = fail
        self.xml_seen = ""

    def __call__(self, arguments: list[str]) -> subprocess.CompletedProcess:
        """Pretend to run schtasks."""
        self.calls.append(arguments)
        code = 0
        if "/Create" in arguments:
            self.xml_seen = Path(arguments[arguments.index("/XML") + 1]).read_text(
                encoding="utf-16"
            )
            self.exists = not self.fail
            code = 1 if self.fail else 0
        elif "/Query" in arguments:
            code = 0 if self.exists else 1
        elif "/Delete" in arguments:
            self.exists = False
        return subprocess.CompletedProcess(arguments, code, "", "ERROR: denied" if code else "")


class TaskDefinitionTests(unittest.TestCase):
    """task_xml(), enable() and disable()."""

    def test_xml_has_logon_and_repetition(self) -> None:
        """The task runs at logon (after a delay) and every N hours, as the user."""
        xml = scheduler.task_xml(
            24, r"C:\Apps\Aulari.exe", "--background", "PC\\alu", datetime(2026, 11, 7, 9, 0)
        )
        root = ElementTree.fromstring(xml.split("?>", 1)[1])
        self.assertEqual(root.find(".//t:LogonTrigger/t:UserId", NS).text, "PC\\alu")
        self.assertEqual(root.find(".//t:LogonTrigger/t:Delay", NS).text, scheduler.LOGON_DELAY)
        self.assertEqual(root.find(".//t:Repetition/t:Interval", NS).text, "PT24H")
        self.assertEqual(root.find(".//t:StartBoundary", NS).text, "2026-11-07T09:00:00")
        self.assertEqual(root.find(".//t:Exec/t:Command", NS).text, r"C:\Apps\Aulari.exe")
        self.assertEqual(root.find(".//t:Exec/t:Arguments", NS).text, "--background")
        self.assertEqual(root.find(".//t:RunLevel", NS).text, "LeastPrivilege")

    def test_unsupported_interval(self) -> None:
        """Intervals other than 24, 12 and 6 hours are refused (be nice to the server)."""
        with self.assertRaises(ValueError):
            scheduler.task_xml(1, "a.exe", "", "u", datetime(2026, 1, 1))

    def test_enable_and_disable(self) -> None:
        """enable() imports the XML with /F and removes the temp file; disable() deletes."""
        runner = FakeRunner()
        scheduler.enable(12, runner)
        create = runner.calls[0]
        self.assertEqual(create[:4], ["schtasks", "/Create", "/TN", scheduler.TASK_NAME])
        self.assertIn("/F", create)
        self.assertIn("<Interval>PT12H</Interval>", runner.xml_seen)
        self.assertFalse(Path(create[create.index("/XML") + 1]).exists())
        self.assertTrue(scheduler.is_enabled(runner))
        scheduler.disable(runner)
        self.assertFalse(scheduler.is_enabled(runner))
        scheduler.disable(runner)  # no task: nothing to delete
        self.assertEqual(sum(1 for call in runner.calls if "/Delete" in call), 1)

    def test_failure_is_reported(self) -> None:
        """A refused /Create raises SchedulerError with schtasks' message."""
        with self.assertRaises(scheduler.SchedulerError) as context:
            scheduler.enable(24, FakeRunner(fail=True))
        self.assertIn("denied", str(context.exception))

    def test_commands(self) -> None:
        """The background command adds the flag; the app command does not."""
        program, arguments = scheduler.background_command()
        self.assertTrue(arguments.endswith(scheduler.BACKGROUND_FLAG))
        self.assertEqual(scheduler.app_command()[0], program)
        self.assertNotIn(scheduler.BACKGROUND_FLAG, scheduler.app_command())


class FakeApp:
    """Minimal CampusApp for run_background()."""

    def __init__(self, hours: int, last: float, results: list[SiteSyncResult]) -> None:
        """Settings with the interval and last run; canned sync results."""
        self.settings = Settings(auto_sync_hours=hours, last_background_sync=last)
        self.results = results
        self.synced = 0
        self.saved = 0

    def run_sync_all(self) -> list[SiteSyncResult]:
        """Count the call and return the canned results."""
        self.synced = self.synced + 1
        return self.results

    def save(self) -> None:
        """Count saves."""
        self.saved = self.saved + 1


def announcement() -> Announcement:
    """Return any announcement."""
    return Announcement("SO", "Examen", "", 1)


class BackgroundTests(unittest.TestCase):
    """should_run(), summarize() and run_background()."""

    def test_runs_only_when_on_and_old_enough(self) -> None:
        """Off never runs; a run right after the last one (logon + timer) is skipped."""
        now = 10 * DAY
        self.assertFalse(background.should_run(FakeApp(0, 0, []), now))
        self.assertFalse(background.should_run(FakeApp(24, now - 3600, []), now))
        self.assertTrue(background.should_run(FakeApp(24, now - DAY, []), now))

    def test_summary_of_all_years(self) -> None:
        """Counts add up; expired sessions are listed, not counted as errors."""
        summary = SyncSummary(new=2, new_versions=1, errors=["x.pdf: reset"])
        results = [
            SiteSyncResult(
                "https://x.test/2026-27", SyncResult(summary, announcements=[announcement()])
            ),
            SiteSyncResult("https://x.test/2025-26", skipped_key="site_skipped_no_session"),
            SiteSyncResult("https://x.test/2024-25", error=AppError("error_invalid_token")),
            SiteSyncResult("https://x.test/2023-24", skipped_key="site_skipped_no_courses"),
        ]
        outcome = background.summarize(results)
        self.assertEqual(
            (outcome.new_files, outcome.new_versions, outcome.announcements), (2, 1, 1)
        )
        self.assertEqual(outcome.errors, 1)
        self.assertEqual(
            outcome.expired_sites, ["https://x.test/2025-26", "https://x.test/2024-25"]
        )
        self.assertTrue(outcome.worth_notifying())

    def test_run_background_saves_the_time(self) -> None:
        """A due run syncs once and remembers when; a quiet run is not worth a notice."""
        app = FakeApp(24, 0, [SiteSyncResult("https://x.test/2026-27", SyncResult(SyncSummary()))])
        outcome = background.run_background(app, now=5 * DAY)
        self.assertTrue(outcome.ran)
        self.assertFalse(outcome.worth_notifying())
        self.assertEqual(
            (app.synced, app.saved, app.settings.last_background_sync), (1, 1, 5 * DAY)
        )
        self.assertFalse(background.run_background(app, now=5 * DAY + 60).ran)


class NoticeTests(unittest.TestCase):
    """notice_text() and NoticeWindow."""

    def test_notice_text(self) -> None:
        """The message lists what is new and which sessions expired."""
        outcome = BackgroundOutcome(True, 3, 0, 1, ["https://x.test/2025-26"], 0)
        message = notice_text(outcome)
        self.assertIn(text("notice_new_files", count=3), message)
        self.assertIn(text("notice_announcements", count=1), message)
        self.assertNotIn(text("notice_new_versions", count=0), message)
        self.assertIn(text("notice_session_expired", site="2025-26"), message)

    def test_window_opens_app(self) -> None:
        """The 'open' button launches the app command and closes the notice."""
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("No display available for Tkinter")
        launched = []
        root.withdraw()
        notice = NoticeWindow(root, "Aulari: 3 fitxers nous.", ["aulari.exe"], launched.append)
        self.assertEqual(notice.label.cget("text"), "Aulari: 3 fitxers nous.")
        notice._open()
        self.assertEqual(launched, [["aulari.exe"]])


if __name__ == "__main__":
    unittest.main()
