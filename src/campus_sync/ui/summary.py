"""Build the user-facing texts of a sync result (no tkinter, so it is testable)."""

from campus_sync.app import CourseChoice, SyncResult
from campus_sync.log_filter import redact
from campus_sync.sync import ChangedFile, Progress, SyncSummary
from campus_sync.texts import text

MAX_ERRORS_SHOWN = 5
NEWLINE = "\n"
MAX_CHANGES_SHOWN = 15
PATH_PREFIX_PARTS = 2  # '<academic year>/<course alias>'


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


def change_line(change: ChangedFile) -> str:
    """Return '[SO] 02 - TEORIA/Tema 5.pdf', plus a tag for new versions."""
    parts = change.relative_path.split("/")
    if len(parts) > PATH_PREFIX_PARTS:
        line = f"[{parts[1]}] {'/'.join(parts[PATH_PREFIX_PARTS:])}"
    else:
        line = change.relative_path
    if change.is_new_version:
        line = f"{line} {text('change_new_version_tag')}"
    result = f"  • {line}"
    return result


def _changes_lines(summary: SyncSummary) -> list[str]:
    """Return the 'Novedades' block: at most MAX_CHANGES_SHOWN files."""
    lines = []
    if summary.changes:
        lines.append(text("summary_changes_title"))
        lines.extend([change_line(change) for change in summary.changes[:MAX_CHANGES_SHOWN]])
        if len(summary.changes) > MAX_CHANGES_SHOWN:
            hidden = len(summary.changes) - MAX_CHANGES_SHOWN
            lines.append(text("summary_more_changes", count=hidden))
    return lines


def _error_lines(summary: SyncSummary) -> list[str]:
    """Return the error count and at most MAX_ERRORS_SHOWN messages."""
    lines = [text("summary_errors", count=len(summary.errors))]
    lines.extend([f"  • {message}" for message in summary.errors[:MAX_ERRORS_SHOWN]])
    if len(summary.errors) > MAX_ERRORS_SHOWN:
        lines.append(text("summary_more_errors", count=len(summary.errors) - MAX_ERRORS_SHOWN))
    return lines


def _count_lines(summary: SyncSummary) -> list[str]:
    """Return the counters; optional ones only when they are not zero."""
    lines = [
        text("summary_new", count=summary.new),
        text("summary_new_versions", count=summary.new_versions),
    ]
    optional = [
        ("summary_adopted", summary.adopted),
        ("summary_unchanged", summary.unchanged),
        ("summary_restored", summary.restored),
        ("summary_missing", summary.missing_locally),
        ("summary_removed", summary.removed_in_moodle),
    ]
    for key, count in optional:
        if count or key == "summary_unchanged":
            lines.append(text(key, count=count))
    return lines


def summary_text(result: SyncResult) -> str:
    """Return the multi-line summary shown after a sync (RF10)."""
    summary = result.summary
    lines = []
    if summary.invalid_token:
        lines.append(text("error_invalid_token"))
    if summary.cancelled:
        lines.append(text("summary_cancelled"))
    lines.extend(_count_lines(summary))
    lines.extend(_changes_lines(summary))
    lines.extend(_error_lines(summary))
    if result.calendar_path is not None:
        lines.append(text("calendar_done", count=result.calendar_events, path=result.calendar_path))
    elif result.calendar_error:
        lines.append(text("calendar_error", error=result.calendar_error))
    result_text = redact(NEWLINE.join(lines))
    return result_text
