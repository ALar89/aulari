"""Announcements, submissions and grades (RF16-RF18), without any interface.

All calls are read-only and whitelisted in moodle_client. Grades, names and
comments are personal data: they are returned to the interface, never
logged and never written to disk (except the 'seen' announcement ids).
"""

from dataclasses import dataclass

from aulari.calendar_export import plain_text
from aulari.moodle_client import InvalidTokenError, MoodleClient, MoodleError, array_param
from aulari.registry import Registry

NEWS_FORUM_TYPE = "news"
STATUS_SUBMITTED = "submitted"
STATUS_ERROR = "error"
NO_VALUE = "—"


@dataclass(frozen=True)
class Announcement:
    """A new announcement of a news forum (RF16)."""

    course_alias: str
    subject: str
    message: str
    time: int


@dataclass(frozen=True)
class SubmissionRow:
    """One assignment and the state of the user's submission (RF17)."""

    course_alias: str
    name: str
    duedate: int
    status: str
    grade: str
    has_comments: bool


@dataclass(frozen=True)
class CourseGrade:
    """The overall grade of one course (RF18)."""

    course_id: int
    course_name: str
    grade: str


@dataclass(frozen=True)
class GradeItem:
    """One line of the grade report of a course (RF18)."""

    name: str
    grade: str
    grade_range: str
    percentage: str


def _text(value: object) -> str:
    """Return a displayable value, or a dash when it is empty."""
    result = str(value).strip() if value is not None else ""
    if not result:
        result = NO_VALUE
    return result


def _discussion_id(discussion: dict) -> int:
    """Return the discussion id ('id' is the id of its first post)."""
    result = int(discussion.get("discussion") or discussion["id"])
    return result


def _announcement_time(announcement: Announcement) -> int:
    """Sort key: newest first when used with reverse=True."""
    result = announcement.time
    return result


def new_announcements(
    client: MoodleClient, registry: Registry, site_id: int, aliases: dict[int, str]
) -> list[Announcement]:
    """Return announcements not seen before in the news forums of the courses.

    The first time a forum is checked, its current announcements are only
    remembered as seen, so that old ones do not appear as new.

    Raises: MoodleError, requests.RequestException.
    """
    result = []
    if aliases:
        forums = client.call(
            "mod_forum_get_forums_by_courses", array_param("courseids", sorted(aliases))
        )
        news_forums = [forum for forum in forums if forum.get("type") == NEWS_FORUM_TYPE]
        for forum in news_forums:
            data = client.call("mod_forum_get_forum_discussions", {"forumid": forum["id"]})
            discussions = data.get("discussions", [])
            ids = [_discussion_id(discussion) for discussion in discussions]
            if registry.forum_checked(site_id, int(forum["id"])):
                seen = registry.seen_discussions(site_id, ids)
                alias = aliases.get(int(forum.get("course", 0)), "")
                result.extend(
                    [
                        Announcement(
                            alias,
                            _text(discussion.get("subject") or discussion.get("name")),
                            plain_text(str(discussion.get("message") or "")),
                            int(discussion.get("timemodified") or 0),
                        )
                        for discussion in discussions
                        if _discussion_id(discussion) not in seen
                    ]
                )
            registry.mark_discussions_seen(site_id, int(forum["id"]), ids)
    result.sort(key=_announcement_time, reverse=True)
    return result


def _submission_row(client: MoodleClient, alias: str, assignment: dict) -> SubmissionRow:
    """Return the row of one assignment; one failing call gives an 'error' row.

    Raises: InvalidTokenError, requests.RequestException.
    """
    status = STATUS_ERROR
    grade = NO_VALUE
    has_comments = False
    try:
        data = client.call("mod_assign_get_submission_status", {"assignid": assignment["id"]})
        submission = (data.get("lastattempt") or {}).get("submission") or {}
        feedback = data.get("feedback") or {}
        status = str(submission.get("status") or "new")
        grade = _text(feedback.get("gradefordisplay"))
        has_comments = bool(feedback.get("plugins"))
    except InvalidTokenError:
        raise
    except MoodleError:
        status = STATUS_ERROR
    result = SubmissionRow(
        alias,
        _text(assignment.get("name")),
        int(assignment.get("duedate") or 0),
        status,
        grade,
        has_comments,
    )
    return result


def submissions(client: MoodleClient, aliases: dict[int, str]) -> list[SubmissionRow]:
    """Return one row per assignment of the courses, sorted by due date (RF17).

    Raises: MoodleError, requests.RequestException.
    """
    result = []
    if aliases:
        data = client.call("mod_assign_get_assignments", array_param("courseids", sorted(aliases)))
        for course in data.get("courses", []):
            alias = aliases.get(int(course.get("id", 0)), "")
            for assignment in course.get("assignments", []):
                result.append(_submission_row(client, alias, assignment))
    result.sort(key=_row_due)
    return result


def _row_due(row: SubmissionRow) -> tuple[int, str]:
    """Sort key: due date (no date last), then name."""
    result = (row.duedate or 2**62, row.name)
    return result


def course_grades(client: MoodleClient, user_id: int, names: dict[int, str]) -> list[CourseGrade]:
    """Return the overall grade of every course in the grade overview (RF18).

    Raises: MoodleError, requests.RequestException.
    """
    data = client.call("gradereport_overview_get_course_grades", {"userid": user_id})
    result = [
        CourseGrade(
            int(grade.get("courseid", 0)),
            names.get(int(grade.get("courseid", 0)), str(grade.get("courseid"))),
            _text(grade.get("grade")),
        )
        for grade in data.get("grades", [])
    ]
    return result


def grade_items(client: MoodleClient, course_id: int, user_id: int) -> list[GradeItem]:
    """Return the grade report lines of one course (RF18).

    Raises: MoodleError, requests.RequestException.
    """
    data = client.call(
        "gradereport_user_get_grade_items", {"courseid": course_id, "userid": user_id}
    )
    users = data.get("usergrades", [])
    items = users[0].get("gradeitems", []) if users else []
    result = [
        GradeItem(
            _text(item.get("itemname") or item.get("itemtype")),
            _text(item.get("gradeformatted")),
            _text(item.get("rangeformatted")),
            _text(item.get("percentageformatted")),
        )
        for item in items
    ]
    return result
