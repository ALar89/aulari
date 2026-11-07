"""Build the user-facing texts of a sync result (no tkinter, so it is testable)."""

from aulari.app import CourseChoice, Deadline, SyncResult
from aulari.extras import Announcement, CourseGrade, GradeItem, SubmissionRow
from aulari.log_filter import redact
from aulari.organizer import to_local_time
from aulari.sync import Progress
from aulari.texts import text

MAX_ERRORS_SHOWN = 5


def course_label(choice: CourseChoice) -> str:
    """Return the text next to a course checkbox, marking new courses (RF3)."""
    result = choice.fullname
    if choice.is_new:
        result = f"{result}  {text('course_new_tag')}"
    return result


def progress_text(report: Progress) -> str:
    """Return the progress line: 'SO: 3 de 34 — Tema 1.pdf'."""
    course = report.course_folder.rsplit("/", 1)[-1]
    result = text(
        "sync_progress",
        course=course,
        done=report.done + 1,
        total=report.total,
        filename=report.filename,
    )
    return result


def deadline_line(deadline: Deadline) -> str:
    """Return 'lun 09/11 23:59  [SO] Entrega P1' in Europe/Madrid time (RF15)."""
    moment = to_local_time(deadline.timestart)
    weekdays = text("weekdays").split(",")
    result = text(
        "deadline_line",
        weekday=weekdays[moment.weekday()],
        date=moment.strftime("%d/%m %H:%M"),
        course=deadline.course_alias or "—",
        name=deadline.name,
    )
    return result


def summary_text(result: SyncResult) -> str:
    """Return the multi-line summary shown after a sync (RF10)."""
    summary = result.summary
    lines = []
    if summary.invalid_token:
        lines.append(text("error_invalid_token"))
    if summary.cancelled:
        lines.append(text("summary_cancelled"))
    lines.append(text("summary_new", count=summary.new))
    lines.append(text("summary_new_versions", count=summary.new_versions))
    if summary.adopted:
        lines.append(text("summary_adopted", count=summary.adopted))
    lines.append(text("summary_unchanged", count=summary.unchanged))
    if summary.restored:
        lines.append(text("summary_restored", count=summary.restored))
    if summary.missing_locally:
        lines.append(text("summary_missing", count=summary.missing_locally))
    if summary.removed_in_moodle:
        lines.append(text("summary_removed", count=summary.removed_in_moodle))
    lines.append(text("summary_errors", count=len(summary.errors)))
    for message in summary.errors[:MAX_ERRORS_SHOWN]:
        lines.append(f"  • {message}")
    if len(summary.errors) > MAX_ERRORS_SHOWN:
        lines.append(text("summary_more_errors", count=len(summary.errors) - MAX_ERRORS_SHOWN))
    lines.extend(_after_sync_lines(result))
    result_text = redact("\n".join(lines))
    return result_text


def _after_sync_lines(result: SyncResult) -> list[str]:
    """Return the lines about announcements, Novedades.txt and the calendar."""
    lines = []
    if result.announcements:
        lines.append(text("summary_announcements", count=len(result.announcements)))
    elif result.announcements_error:
        lines.append(text("announcements_error", error=result.announcements_error))
    if result.news_path is not None:
        lines.append(text("summary_news", path=result.news_path))
    if result.calendar_path is not None:
        lines.append(text("calendar_done", count=result.calendar_events, path=result.calendar_path))
    elif result.calendar_error:
        lines.append(text("calendar_error", error=result.calendar_error))
    return lines


def _date(timestamp: int) -> str:
    """Return 'dd/mm/yyyy hh:mm' in Madrid time, or a dash for no date."""
    result = "—"
    if timestamp:
        result = to_local_time(timestamp).strftime("%d/%m/%Y %H:%M")
    return result


def announcement_rows(announcements: list[Announcement]) -> tuple[list[tuple[str, ...]], list[str]]:
    """Return (table rows, messages) for the announcements window (RF16)."""
    rows = [(_date(item.time), item.course_alias, item.subject) for item in announcements]
    messages = [item.message for item in announcements]
    result = (rows, messages)
    return result


def submission_rows(rows: list[SubmissionRow]) -> list[tuple[str, ...]]:
    """Return the submissions table rows (RF17)."""
    statuses = {
        "submitted": text("status_submitted"),
        "new": text("status_new"),
        "draft": text("status_draft"),
        "error": text("status_error"),
    }
    result = [
        (
            row.course_alias,
            row.name,
            _date(row.duedate),
            statuses.get(row.status, row.status),
            row.grade,
            text("yes") if row.has_comments else "",
        )
        for row in rows
    ]
    return result


def grade_rows(grades: list[CourseGrade]) -> list[tuple[str, ...]]:
    """Return the course grades table rows (RF18)."""
    result = [(grade.course_name, grade.grade) for grade in grades]
    return result


def item_rows(items: list[GradeItem]) -> list[tuple[str, ...]]:
    """Return the grade items table rows (RF18)."""
    result = [(item.name, item.grade, item.grade_range, item.percentage) for item in items]
    return result
