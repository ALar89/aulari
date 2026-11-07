"""Unit tests for aulari.calendar_export (no network)."""

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import aulari
from aulari import calendar_export
from aulari.calendar_export import CalendarEvent, CalendarRequest, IcsOptions
from aulari.registry import Registry

NOW = datetime(2026, 11, 6, 18, 0, tzinfo=UTC)
DUE = int(datetime(2026, 11, 9, 22, 59, tzinfo=UTC).timestamp())
OPTIONS = IcsOptions("2026-27.moodle.example.test", "Campus 2026-27", {1072: "SO"})


def make_event(event_id: int = 501, duration: int = 0, **changes: object) -> CalendarEvent:
    """Return an event like the 'due' events seen on e-aules (no duration)."""
    fields = {
        "event_id": event_id,
        "name": "Entrega P1",
        "description": "",
        "course_id": 1072,
        "timestart": DUE,
        "timeduration": duration,
        "timemodified": DUE - 86400,
        "url": "https://moodle.example.test/2026-27/mod/assign/view.php?id=55132",
    }
    fields.update(changes)
    return CalendarEvent(**fields)


def unfold(ics: str) -> list[str]:
    """Undo line folding and return the content lines."""
    return ics.replace("\r\n ", "").split("\r\n")


class TextFormatTests(unittest.TestCase):
    """Tests for escape_text(), fold_line(), format_utc() and plain_text()."""

    def test_escape_text(self) -> None:
        """Backslash, semicolon, comma and newlines are escaped."""
        self.assertEqual(calendar_export.escape_text("a\\b;c,d\ne"), "a\\\\b\\;c\\,d\\ne")

    def test_short_line_is_not_folded(self) -> None:
        """Lines up to 75 octets stay as they are."""
        line = "SUMMARY:" + "x" * 67
        self.assertEqual(calendar_export.fold_line(line), line)

    def test_long_line_is_folded_at_75_octets(self) -> None:
        """Every physical line has at most 75 octets and unfolding restores the text."""
        line = "DESCRIPTION:" + "Pràctica de concurrència àèìòú " * 10
        folded = calendar_export.fold_line(line)
        for physical in folded.split("\r\n"):
            self.assertLessEqual(len(physical.encode("utf-8")), calendar_export.MAX_LINE_OCTETS)
        self.assertEqual(folded.replace("\r\n ", ""), line)

    def test_format_utc(self) -> None:
        """Timestamps become UTC date-times with a Z."""
        self.assertEqual(calendar_export.format_utc(DUE), "20261109T225900Z")

    def test_plain_text(self) -> None:
        """HTML tags and entities become plain text on one line."""
        html = "<p>Entrega&nbsp;de la <b>pr&agrave;ctica</b></p>\n<p>Grup A</p>"
        self.assertEqual(calendar_export.plain_text(html), "Entrega de la pràctica Grup A")


class EventConversionTests(unittest.TestCase):
    """Tests for event_from_moodle()."""

    def test_moodle_event(self) -> None:
        """Moodle fields map to CalendarEvent; missing ones get safe defaults."""
        data = {"id": 9, "name": "Qüestionari", "courseid": 153, "timestart": DUE, "url": None}
        event = calendar_export.event_from_moodle(data)
        self.assertEqual((event.event_id, event.course_id, event.timeduration), (9, 153, 0))
        self.assertEqual((event.description, event.url), ("", ""))


class BuildIcsTests(unittest.TestCase):
    """Tests for build_ics()."""

    def test_calendar_structure(self) -> None:
        """The calendar has the required properties and CRLF line ends."""
        ics = calendar_export.build_ics([make_event()], OPTIONS, {501: 0}, NOW)
        self.assertTrue(ics.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"))
        self.assertTrue(ics.endswith("END:VCALENDAR\r\n"))
        self.assertNotIn("\n", ics.replace("\r\n", ""))
        lines = unfold(ics)
        self.assertIn(f"PRODID:-//Aulari//Aulari {aulari.__version__}//EN", lines)
        self.assertIn("X-WR-CALNAME:Campus 2026-27", lines)

    def test_event_without_duration_has_no_dtend(self) -> None:
        """Zero-duration events (all of them on e-aules) have no DTEND."""
        lines = unfold(calendar_export.build_ics([make_event()], OPTIONS, {501: 0}, NOW))
        self.assertIn("UID:moodle-501@2026-27.moodle.example.test", lines)
        self.assertIn("DTSTART:20261109T225900Z", lines)
        self.assertIn("DTSTAMP:20261106T180000Z", lines)
        self.assertIn("SUMMARY:[SO] Entrega P1", lines)
        self.assertIn("SEQUENCE:0", lines)
        self.assertFalse([line for line in lines if line.startswith("DTEND")])

    def test_event_with_duration_has_dtend(self) -> None:
        """An event lasting one hour ends one hour later."""
        lines = unfold(calendar_export.build_ics([make_event(duration=3600)], OPTIONS, {}, NOW))
        self.assertIn("DTEND:20261109T235900Z", lines)

    def test_unknown_course_and_unsafe_url(self) -> None:
        """No alias means no prefix; non-http URLs are left out."""
        event = make_event(course_id=0, url="javascript:alert(1)", description="a, b; c")
        lines = unfold(calendar_export.build_ics([event], OPTIONS, {}, NOW))
        self.assertIn("SUMMARY:Entrega P1", lines)
        self.assertIn("DESCRIPTION:a\\, b\\; c", lines)
        self.assertFalse([line for line in lines if line.startswith("URL")])

    def test_events_are_sorted(self) -> None:
        """Output order is by start time, so the file is stable between exports."""
        late = make_event(event_id=2, timestart=DUE + 100)
        early = make_event(event_id=3, timestart=DUE - 100)
        lines = unfold(calendar_export.build_ics([late, early], OPTIONS, {}, NOW))
        uids = [line for line in lines if line.startswith("UID")]
        self.assertEqual(uids[0], "UID:moodle-3@2026-27.moodle.example.test")


class FakeCalendarClient:
    """Stand-in for MoodleClient that returns canned calendar events."""

    def __init__(self, events: list[dict]) -> None:
        """Store the events and remember the last call."""
        self.events = events
        self.params = {}

    def call(self, function: str, params: dict) -> dict:
        """Record the parameters and return the events."""
        self.params = params
        return {"events": self.events, "warnings": []}


class ExportTests(unittest.TestCase):
    """Tests for export_calendar(), sequences and the atomic write."""

    def setUp(self) -> None:
        """Create a destination and a registry with one site."""
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.registry = Registry(root / "app" / "registry.sqlite3")
        site_id = self.registry.get_or_create_site(
            "https://moodle.example.test/2026-27", 42, "2026-27"
        )
        self.request = CalendarRequest(
            site_id,
            "https://moodle.example.test/2026-27",
            "2026-27",
            root / "Campus",
            {1072: "SO", 472: "EC"},
        )
        self.event = {
            "id": 501,
            "name": "Entrega P1",
            "courseid": 1072,
            "timestart": DUE,
            "timemodified": 100,
        }

    def tearDown(self) -> None:
        """Close the registry and delete the folders."""
        self.registry.close()
        self.folder.cleanup()

    def export(self, events: list[dict]) -> tuple[Path, int, FakeCalendarClient]:
        """Run export_calendar() with a fake client."""
        client = FakeCalendarClient(events)
        path, count = calendar_export.export_calendar(client, self.registry, self.request, NOW)
        return path, count, client

    def test_export_writes_file_with_all_courses(self) -> None:
        """The file goes to <destination>/<year>/calendari.ics; every course id is sent."""
        path, count, client = self.export([self.event])
        self.assertEqual(path, Path(self.folder.name) / "Campus" / "2026-27" / "calendari.ics")
        self.assertEqual(count, 1)
        self.assertEqual(path.read_bytes()[:17], b"BEGIN:VCALENDAR\r\n")
        self.assertEqual(client.params["events[courseids][0]"], 472)
        self.assertEqual(client.params["events[courseids][1]"], 1072)
        self.assertFalse(list(path.parent.glob("*.part")))

    def test_sequence_rises_only_when_event_changes(self) -> None:
        """Same event: SEQUENCE 0, 0; after a change in Moodle: 1."""
        sequences = []
        for timemodified in [100, 100, 200]:
            path, _, _ = self.export([{**self.event, "timemodified": timemodified}])
            sequences.append(
                [
                    line
                    for line in unfold(path.read_bytes().decode("utf-8"))
                    if line.startswith("SEQUENCE")
                ]
            )
        self.assertEqual(sequences, [["SEQUENCE:0"], ["SEQUENCE:0"], ["SEQUENCE:1"]])


class MigrationTests(unittest.TestCase):
    """A registry created before phase 3 (schema 1) gains the calendar table."""

    def test_version_1_database_is_migrated(self) -> None:
        """Data is kept and calendar_events is added."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "registry.sqlite3"
            with Registry(path) as registry:
                site_id = registry.get_or_create_site("https://x.test", 1, "2026-27")
            conn = sqlite3.connect(path)
            with conn:
                conn.execute("DROP TABLE calendar_events")
                conn.execute("DROP TABLE checked_forums")
                conn.execute("DROP TABLE seen_discussions")
                conn.execute("PRAGMA user_version = 1")
            conn.close()
            with Registry(path) as registry:
                self.assertEqual(
                    registry.get_or_create_site("https://x.test", 1, "2026-27"), site_id
                )
                self.assertEqual(registry.event_sequence(site_id, 7, 100), 0)
            conn = sqlite3.connect(path)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            conn.close()
            self.assertEqual(version, 3)


if __name__ == "__main__":
    unittest.main()
