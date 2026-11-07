"""Create anonymized test fixtures from recorded Moodle responses.

Reads the latest recordings of one course from
%LOCALAPPDATA%\\aulari\\recordings\\ (outside the repository) and writes
tests/fixtures/course_contents.json and tests/fixtures/assignments.json.

Anonymization keeps only the fields the code uses (whitelist) and replaces:
- every name (sections, modules, files, folders, assignments) by a generic one;
- every URL host by moodle.example.test, and file names inside URLs;
- external links by https://external.example.org/linkN;
- every id (course, section, module, assignment, context) by an invented
  one, consistently in both files.
Sizes, timestamps and the structure are kept. Descriptions,
authors and any other field are dropped. Review the output by hand before
committing it.

Usage (PowerShell, venv active, from the project folder):
    python poc\\make_fixtures.py <courseid>
"""

import json
import os
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

EXPECTED_ARGS = 2  # script name + course id
FAKE_SITE = "https://moodle.example.test/2026-27"
FAKE_EXTERNAL = "https://external.example.org/link"
PLUGINFILE_MARKER = "pluginfile.php"
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
CONTENT_FIELDS = ["type", "filesize", "timemodified", "mimetype"]
PAGE_HTML_NAME = "index.html"
# First invented number for each kind of id (ranges do not overlap).
ID_BASES = {"course": 100, "section": 1000, "module": 5000, "assign": 7000, "context": 9000}


class Anonymizer:
    """Replaces names consistently: the same original always gets the same fake."""

    def __init__(self) -> None:
        """Start with empty name tables."""
        self._files = {}
        self._dirs = {}
        self._links = {}
        self._ids = {kind: {} for kind in ID_BASES}

    def number(self, kind: str, original: int) -> int:
        """Return the invented id for an original id of a given kind."""
        table = self._ids[kind]
        if original not in table:
            table[original] = ID_BASES[kind] + len(table) + 1
        result = table[original]
        return result

    def file_name(self, name: str) -> str:
        """Return 'file_NN.ext' for a file name, keeping the extension.

        'index.html' (the generated HTML of a page) is generic and the code
        relies on it, so it is kept.
        """
        if name == PAGE_HTML_NAME:
            self._files[name] = name
        if name not in self._files:
            suffix = PurePosixPath(name).suffix.lower()
            self._files[name] = f"file_{len(self._files) + 1:02d}{suffix}"
        result = self._files[name]
        return result

    def file_path(self, filepath: str) -> str:
        """Return '/dir_N/.../' for a Moodle filepath, part by part."""
        parts = []
        for part in filepath.split("/"):
            if part:
                if part not in self._dirs:
                    self._dirs[part] = f"dir_{len(self._dirs) + 1}"
                parts.append(self._dirs[part])
        result = "/" + "".join([part + "/" for part in parts])
        return result

    def external_link(self, url: str) -> str:
        """Return a fake external URL for a real one."""
        if url not in self._links:
            self._links[url] = f"{FAKE_EXTERNAL}{len(self._links) + 1}"
        result = self._links[url]
        return result

    def file_url(self, fileurl: str, filepath: str, filename: str) -> str:
        """Return the pluginfile.php URL on the fake site with anonymized names.

        Raises: ValueError if the URL is not a pluginfile.php URL.
        """
        segments = unquote(urlparse(fileurl).path).split("/")
        if PLUGINFILE_MARKER not in segments:
            raise ValueError("Not a pluginfile.php URL")
        start = segments.index(PLUGINFILE_MARKER) + 1
        # Keep everything before filepath + filename (contextid, component,
        # filearea and the optional itemid/revision), whatever its length.
        name_segments = len([part for part in filepath.split("/") if part]) + 1
        prefix_parts = segments[start : len(segments) - name_segments]
        prefix_parts[0] = str(self.number("context", int(prefix_parts[0])))
        prefix = "/".join(prefix_parts)
        path = self.file_path(filepath) + self.file_name(filename)
        result = f"{FAKE_SITE}/webservice/pluginfile.php/{prefix}{path}?forcedownload=1"
        return result


def anonymize_content(content: dict, anon: Anonymizer) -> dict:
    """Return a whitelisted, anonymized copy of one module content entry."""
    result = {field: content[field] for field in CONTENT_FIELDS if field in content}
    if content.get("type") == "url":
        result["filename"] = "link"
        result["filepath"] = "/"
        result["fileurl"] = anon.external_link(str(content.get("fileurl", "")))
    else:
        filepath = str(content.get("filepath", "/"))
        filename = str(content.get("filename", ""))
        result["filename"] = anon.file_name(filename)
        result["filepath"] = anon.file_path(filepath)
        result["fileurl"] = anon.file_url(str(content.get("fileurl", "")), filepath, filename)
    return result


def anonymize_module(module: dict, index: int, anon: Anonymizer) -> dict:
    """Return a whitelisted, anonymized copy of one course module."""
    result = {
        "id": anon.number("module", int(module["id"])),
        "modname": module.get("modname"),
        "name": f"{module.get('modname')} {index}",
        "uservisible": module.get("uservisible", True),
    }
    if "contents" in module:
        result["contents"] = [anonymize_content(content, anon) for content in module["contents"]]
    return result


def anonymize_sections(sections: list, anon: Anonymizer) -> list:
    """Return anonymized core_course_get_contents data."""
    result = []
    counter = 0
    for section in sections:
        modules = []
        for module in section.get("modules", []):
            counter = counter + 1
            modules.append(anonymize_module(module, counter, anon))
        result.append(
            {
                "id": anon.number("section", int(section["id"])),
                "section": section.get("section", 0),
                "name": f"Section {section.get('section', 0)}",
                "uservisible": section.get("uservisible", True),
                "modules": modules,
            }
        )
    return result


def anonymize_assignments(data: dict, anon: Anonymizer) -> dict:
    """Return anonymized mod_assign_get_assignments data."""
    courses = []
    for course in data.get("courses", []):
        assignments = []
        for index, assignment in enumerate(course.get("assignments", []), start=1):
            attachments = []
            for attachment in assignment.get("introattachments", []):
                anonymized = anonymize_content({**attachment, "type": "file"}, anon)
                del anonymized["type"]
                attachments.append(anonymized)
            assignments.append(
                {
                    "id": anon.number("assign", int(assignment.get("id", 0))),
                    "cmid": anon.number("module", int(assignment.get("cmid", 0))),
                    "name": f"assign {index}",
                    "introattachments": attachments,
                    "introfiles": [{} for _ in assignment.get("introfiles", [])],
                }
            )
        course_id = anon.number("course", int(course.get("id", 0)))
        courses.append({"id": course_id, "assignments": assignments})
    warnings = []
    for warning in data.get("warnings", []):
        anonymized = {key: warning.get(key) for key in ["item", "warningcode", "message"]}
        # Warning itemids are module ids (cmid) of assignments without access.
        anonymized["itemid"] = anon.number("module", int(warning.get("itemid", 0)))
        warnings.append(anonymized)
    result = {"courses": courses, "warnings": warnings}
    return result


def latest_recording(pattern: str) -> Path:
    """Return the newest recording that matches pattern.

    Raises: FileNotFoundError if there is none; KeyError if LOCALAPPDATA is unset.
    """
    folder = Path(os.environ["LOCALAPPDATA"]) / "aulari" / "recordings"
    matches = sorted(folder.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No recording matches {pattern}")
    result = matches[-1]
    return result


def write_fixture(name: str, data: object) -> Path:
    """Write a fixture as pretty JSON and return its path.

    Raises: OSError on write errors.
    """
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    result = FIXTURES_DIR / name
    with result.open("w", encoding="utf-8", newline="\n") as out_file:
        json.dump(data, out_file, indent=2, ensure_ascii=False)
        out_file.write("\n")
    return result


def main(argv: list[str]) -> int:
    """Create both fixtures and return the exit code."""
    exit_code = 1
    if len(argv) != EXPECTED_ARGS or not argv[1].isdigit():
        print("Usage: python poc\\make_fixtures.py <courseid>")
    else:
        course_id = argv[1]
        anon = Anonymizer()
        contents_path = latest_recording(f"*core_course_get_contents_{course_id}_*.json")
        assign_path = latest_recording(f"*mod_assign_get_assignments_{course_id}_*.json")
        with contents_path.open(encoding="utf-8") as in_file:
            sections = json.load(in_file)
        with assign_path.open(encoding="utf-8") as in_file:
            assignments = json.load(in_file)
        print(write_fixture("course_contents.json", anonymize_sections(sections, anon)))
        print(write_fixture("assignments.json", anonymize_assignments(assignments, anon)))
        exit_code = 0
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
