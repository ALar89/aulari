"""Turn Moodle API responses into a flat list of items to synchronize (RF5).

Pure functions, no network. Input is the JSON of core_course_get_contents
and mod_assign_get_assignments; output is a list of SyncItem.

What is kept:
- 'resource' and 'folder' files (folders keep their inner filepath);
- 'url' modules (saved later as .url shortcuts);
- assignment intro attachments ('introattachments').
What is skipped: modules with uservisible = false, 'page', 'label' and any
other module type, and embedded 'introfiles' (design.md sections 3 and 10).
"""

from dataclasses import dataclass
from urllib.parse import unquote, urlparse

from campus_sync.registry import FileKey

KIND_FILE = "file"
KIND_URL = "url"
URL_FILEAREA = "url"
FILE_MODULES = {"resource", "folder"}
URL_MODULE = "url"
PLUGINFILE_MARKER = "pluginfile.php"
MIN_PLUGINFILE_SEGMENTS = 3  # contextid / component / filearea


@dataclass(frozen=True)
class SectionInfo:
    """A course section: Moodle id, position number and name."""

    section_id: int
    number: int
    name: str


@dataclass(frozen=True)
class SyncItem:
    """One file or link to synchronize, with where it belongs in the course.

    subfolder is the folder or assignment name for files that live inside
    a 'folder' module or an assignment, or '' for plain resources and links.
    """

    key: FileKey
    section: SectionInfo
    kind: str
    subfolder: str
    url: str
    filesize: int
    timemodified: int


def filearea_from_url(fileurl: str) -> str:
    """Return the file area of a pluginfile.php URL.

    .../pluginfile.php/<contextid>/<component>/<filearea>/... -> <filearea>

    Raises: ValueError if the URL is not a pluginfile.php URL.
    """
    segments = unquote(urlparse(fileurl).path).split("/")
    position = -1
    index = 0
    while index < len(segments) and position < 0:
        if segments[index] == PLUGINFILE_MARKER:
            position = index
        index = index + 1
    after = segments[position + 1 :] if position >= 0 else []
    if len(after) < MIN_PLUGINFILE_SEGMENTS:
        raise ValueError(f"Not a pluginfile.php URL: {urlparse(fileurl).path}")
    result = after[2]
    return result


def _section_info(section: dict) -> SectionInfo:
    """Return the SectionInfo of a core_course_get_contents section."""
    result = SectionInfo(
        section_id=int(section.get("id", 0)),
        number=int(section.get("section", 0)),
        name=str(section.get("name", "")),
    )
    return result


def _file_item(cmid: int, content: dict, section: SectionInfo, subfolder: str) -> SyncItem:
    """Return the SyncItem for one file of a module (resource, folder or assignment).

    Raises: ValueError if the file URL is not a pluginfile.php URL.
    """
    key = FileKey(
        cmid=cmid,
        filearea=filearea_from_url(content["fileurl"]),
        filepath=str(content.get("filepath", "/")),
        filename=str(content["filename"]),
    )
    result = SyncItem(
        key=key,
        section=section,
        kind=KIND_FILE,
        subfolder=subfolder,
        url=str(content["fileurl"]),
        filesize=int(content.get("filesize", 0)),
        timemodified=int(content.get("timemodified", 0)),
    )
    return result


def _url_item(module: dict, content: dict, section: SectionInfo) -> SyncItem:
    """Return the SyncItem for a 'url' module (external link)."""
    key = FileKey(
        cmid=int(module["id"]),
        filearea=URL_FILEAREA,
        filepath="/",
        filename=str(module.get("name") or content.get("filename", "link")),
    )
    result = SyncItem(
        key=key,
        section=section,
        kind=KIND_URL,
        subfolder="",
        url=str(content.get("fileurl", "")),
        filesize=0,
        timemodified=int(content.get("timemodified", 0)),
    )
    return result


def _module_items(module: dict, section: SectionInfo) -> list[SyncItem]:
    """Return the items of one module, or [] if it is hidden or not supported.

    Raises: ValueError if a file URL is not a pluginfile.php URL.
    """
    items = []
    modname = module.get("modname")
    # Files of a 'folder' module go into a subfolder with the module name.
    subfolder = str(module.get("name", "")) if modname == "folder" else ""
    if module.get("uservisible", True):
        for content in module.get("contents", []):
            is_file = content.get("type") == "file"
            if modname in FILE_MODULES and is_file:
                items.append(_file_item(int(module["id"]), content, section, subfolder))
            elif modname == URL_MODULE and content.get("type") == "url":
                items.append(_url_item(module, content, section))
    return items


def items_from_contents(sections: list[dict]) -> list[SyncItem]:
    """Return every file and link of a course from core_course_get_contents.

    Raises: ValueError if a file URL is not a pluginfile.php URL.
    """
    result = []
    for section in sections:
        info = _section_info(section)
        for module in section.get("modules", []):
            result.extend(_module_items(module, info))
    return result


def sections_by_module(sections: list[dict]) -> dict[int, SectionInfo]:
    """Return {cmid: SectionInfo} for every visible module of the course."""
    result = {}
    for section in sections:
        info = _section_info(section)
        for module in section.get("modules", []):
            if module.get("uservisible", True):
                result[int(module["id"])] = info
    return result


def items_from_assignments(assign_data: dict, sections: list[dict]) -> list[SyncItem]:
    """Return the intro attachments of the assignments as items.

    Each attachment goes to the section of its assignment, in a subfolder
    named after the assignment. Assignments whose module is not visible
    in the course contents are skipped.

    Raises: ValueError if an attachment URL is not a pluginfile.php URL.
    """
    result = []
    module_sections = sections_by_module(sections)
    for course in assign_data.get("courses", []):
        for assignment in course.get("assignments", []):
            cmid = int(assignment.get("cmid", 0))
            if cmid in module_sections:
                name = str(assignment.get("name", ""))
                for attachment in assignment.get("introattachments", []):
                    result.append(_file_item(cmid, attachment, module_sections[cmid], name))
    return result
