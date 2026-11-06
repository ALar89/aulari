"""Build the user-facing texts of a sync result (no tkinter, so it is testable)."""

from campus_sync.app import CourseChoice, SyncResult
from campus_sync.log_filter import redact
from campus_sync.sync import Progress
from campus_sync.texts import text

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
    if result.calendar_path is not None:
        lines.append(text("calendar_done", count=result.calendar_events, path=result.calendar_path))
    elif result.calendar_error:
        lines.append(text("calendar_error", error=result.calendar_error))
    result_text = redact("\n".join(lines))
    return result_text
