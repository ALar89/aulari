"""Block 2 checks: are the functions for RF16-RF18 available to students?

Reads the token from the keyring and calls, read-only:
- mod_forum_get_forums_by_courses + mod_forum_get_forum_discussions (RF16)
- mod_assign_get_assignments + mod_assign_get_submission_status (RF17)
- gradereport_overview_get_course_grades (RF18)
- gradereport_user_get_grade_items for one course (RF18)

Only counts, dates and which fields exist are printed: never announcement
titles, names, grades or comments. Full responses are saved, with secrets
replaced by '***', to %LOCALAPPDATA%\\aulari\\recordings\\ (they contain
personal data such as grades: they stay on this computer).

Usage (PowerShell, venv active, from the project folder):
    python poc\\explore_extras.py https://<moodle-site-url> <userid>
"""

import logging
import sys

import keyring
import keyring.errors
from explore_api import ask_course_id, format_timestamp, run_step, save_recording
from moodle_ws import KEYRING_SERVICE, call_function, keyring_username

from aulari.log_filter import configure_logging

EXPECTED_ARGS = 3  # script name + site URL + user id
MAX_FORUMS = 3
MAX_ASSIGNMENTS = 3
NEWS_FORUM_TYPE = "news"

logger = logging.getLogger("explore_extras")


def indexed(name: str, values: list[int]) -> dict:
    """Return Moodle array parameters: {'name[0]': v0, ...}."""
    result = {f"{name}[{index}]": value for index, value in enumerate(values)}
    return result


def check_announcements(site_url: str, token: str, user_id: int) -> None:
    """RF16: news forums of the enrolled courses and their latest discussions.

    Raises: MoodleError, requests.RequestException.
    """
    courses = call_function(site_url, token, "core_enrol_get_users_courses", {"userid": user_id})
    course_ids = [int(course["id"]) for course in courses]
    forums = call_function(
        site_url, token, "mod_forum_get_forums_by_courses", indexed("courseids", course_ids)
    )
    save_recording(site_url, "mod_forum_get_forums_by_courses", forums)
    news = [forum for forum in forums if forum.get("type") == NEWS_FORUM_TYPE]
    print("\n[RF16] Announcements")
    print(f"  forums visible     : {len(forums)}")
    print(f"  news forums        : {len(news)} (courses: {len({f.get('course') for f in news})})")
    for forum in news[:MAX_FORUMS]:
        data = call_function(
            site_url, token, "mod_forum_get_forum_discussions", {"forumid": forum["id"]}
        )
        path = save_recording(site_url, f"mod_forum_get_forum_discussions_{forum['id']}", data)
        discussions = data.get("discussions", [])
        newest = max([d.get("timemodified", 0) for d in discussions], default=0)
        fields = sorted(discussions[0]) if discussions else []
        print(
            f"  forum in course {forum.get('course')}: {len(discussions)} discussions, "
            f"newest {format_timestamp(newest)}"
        )
        print(f"    fields: {fields}")
        print(f"    saved : {path}")


def check_submissions(site_url: str, token: str, course_id: int) -> None:
    """RF17: submission status of the first assignments of a course.

    Raises: MoodleError, requests.RequestException.
    """
    data = call_function(
        site_url, token, "mod_assign_get_assignments", indexed("courseids", [course_id])
    )
    assignments = []
    for course in data.get("courses", []):
        assignments.extend(course.get("assignments", []))
    print(f"\n[RF17] Submissions in course {course_id}: {len(assignments)} assignments")
    for assignment in assignments[:MAX_ASSIGNMENTS]:
        status = call_function(
            site_url, token, "mod_assign_get_submission_status", {"assignid": assignment["id"]}
        )
        path = save_recording(
            site_url, f"mod_assign_get_submission_status_{assignment['id']}", status
        )
        attempt = status.get("lastattempt") or {}
        submission = attempt.get("submission") or {}
        feedback = status.get("feedback") or {}
        due = format_timestamp(assignment.get("duedate", 0))
        print(f"  assignment {assignment['id']} (due {due}):")
        print(f"    top-level fields   : {sorted(status)}")
        print(f"    submission status  : {submission.get('status', '-')}")
        print(f"    has feedback       : {bool(feedback)}")
        print(f"    has grade text     : {bool(feedback.get('gradefordisplay'))}")
        print(f"    saved: {path}")


def check_grades_overview(site_url: str, token: str, user_id: int) -> None:
    """RF18: one final grade per course.

    Raises: MoodleError, requests.RequestException.
    """
    data = call_function(
        site_url, token, "gradereport_overview_get_course_grades", {"userid": user_id}
    )
    path = save_recording(site_url, "gradereport_overview_get_course_grades", data)
    grades = data.get("grades", [])
    with_grade = [grade for grade in grades if str(grade.get("grade", "")).strip() not in {"", "-"}]
    print("\n[RF18] Grades overview")
    print(f"  courses listed : {len(grades)}")
    print(f"  with a grade   : {len(with_grade)}")
    print(f"  fields         : {sorted(grades[0]) if grades else []}")
    print(f"  warnings       : {len(data.get('warnings', []))}")
    print(f"  saved: {path}")


def check_grade_items(site_url: str, token: str, course_id: int, user_id: int) -> None:
    """RF18: grade items of one course.

    Raises: MoodleError, requests.RequestException.
    """
    data = call_function(
        site_url,
        token,
        "gradereport_user_get_grade_items",
        {"courseid": course_id, "userid": user_id},
    )
    path = save_recording(site_url, f"gradereport_user_get_grade_items_{course_id}", data)
    users = data.get("usergrades", [])
    items = users[0].get("gradeitems", []) if users else []
    print(f"\n[RF18] Grade items in course {course_id}")
    print(f"  items          : {len(items)}")
    print(f"  item fields    : {sorted(items[0]) if items else []}")
    print(f"  saved: {path}")


def explore(site_url: str, token: str, user_id: int) -> int:
    """Run every check; a failing one does not stop the others."""
    results = [
        run_step("announcements", check_announcements, site_url, token, user_id),
        run_step("grades overview", check_grades_overview, site_url, token, user_id),
    ]
    course_id = ask_course_id()
    if course_id is not None:
        results.append(run_step("submissions", check_submissions, site_url, token, course_id))
        results.append(
            run_step("grade items", check_grade_items, site_url, token, course_id, user_id)
        )
    result = 0 if all(results) else 1
    return result


def main(argv: list[str]) -> int:
    """Read the token from the keyring, run the checks and return the exit code."""
    exit_code = 1
    configure_logging()
    if len(argv) != EXPECTED_ARGS or not argv[2].isdigit():
        print("Usage: python poc\\explore_extras.py https://<moodle-site-url> <userid>")
    else:
        site_url = argv[1].rstrip("/")
        user_id = int(argv[2])
        username = keyring_username(site_url, user_id)
        try:
            token = keyring.get_password(KEYRING_SERVICE, username)
            if token is None:
                print(f"No token in keyring for {username!r}. Log in with the app first.")
            else:
                exit_code = explore(site_url, token, user_id)
        except keyring.errors.KeyringError as error:
            logger.error("Keyring error: %s", error)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
