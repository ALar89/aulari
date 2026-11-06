"""Unit tests for aulari.moodle_items, with synthetic Moodle responses.

The structure mirrors what e-aules returned in phase 1 (design.md section 10);
names and ids are invented.
"""

import unittest

from aulari import moodle_items
from aulari.moodle_items import KIND_FILE, KIND_URL

BASE = "https://moodle.example.test/2026-27/webservice/pluginfile.php"


def file_content(filename: str, filepath: str = "/", component: str = "mod_resource") -> dict:
    """Return a 'file' content entry as core_course_get_contents gives it."""
    return {
        "type": "file",
        "filename": filename,
        "filepath": filepath,
        "filesize": 1000,
        "timemodified": 1790000000,
        "fileurl": f"{BASE}/77/{component}/content/3{filepath}{filename}?forcedownload=1",
    }


SECTIONS = [
    {
        "id": 900,
        "section": 0,
        "name": "Presentació",
        "modules": [
            {"id": 1, "modname": "forum", "name": "Avisos", "uservisible": True},
            {
                "id": 2,
                "modname": "resource",
                "name": "Guia",
                "contents": [file_content("guia.pdf")],
            },
            {
                "id": 3,
                "modname": "page",
                "name": "Pàgina",
                "contents": [
                    {"type": "file", "filename": "index.html", "filepath": "/", "filesize": 0},
                    file_content("apunts.pdf", "/", "mod_page"),
                ],
            },
        ],
    },
    {
        "id": 901,
        "section": 1,
        "name": "Tema 1",
        "modules": [
            {
                "id": 4,
                "modname": "folder",
                "name": "Exemples",
                "contents": [
                    file_content("a.c", "/", "mod_folder"),
                    file_content("b.c", "/extra/", "mod_folder"),
                ],
            },
            {
                "id": 5,
                "modname": "url",
                "name": "Documentació oficial",
                "contents": [
                    {
                        "type": "url",
                        "filename": "Documentació oficial",
                        "fileurl": "https://docs.example.org/",
                        "timemodified": 1790000001,
                    }
                ],
            },
            {
                "id": 6,
                "modname": "resource",
                "name": "Ocult",
                "uservisible": False,
                "contents": [file_content("ocult.pdf")],
            },
            {"id": 7, "modname": "assign", "name": "Pràctica 1", "uservisible": True},
            {"id": 8, "modname": "assign", "name": "Pràctica oculta", "uservisible": False},
            {"id": 9, "modname": "label", "name": "Etiqueta"},
        ],
    },
]

ASSIGNMENTS = {
    "courses": [
        {
            "id": 1072,
            "assignments": [
                {
                    "cmid": 7,
                    "name": "Pràctica 1",
                    "introattachments": [
                        {
                            "filename": "enunciat.pdf",
                            "filepath": "/",
                            "filesize": 500,
                            "timemodified": 1790000002,
                            "fileurl": f"{BASE}/88/mod_assign/introattachment/0/enunciat.pdf",
                        }
                    ],
                    "introfiles": [{"filename": "imatge.png"}],
                },
                {"cmid": 8, "name": "Pràctica oculta", "introattachments": [file_content("x.pdf")]},
            ],
        }
    ],
    "warnings": [],
}


class FileareaTests(unittest.TestCase):
    """Tests for filearea_from_url()."""

    def test_known_file_areas(self) -> None:
        """content for resources and folders, introattachment for assignments."""
        self.assertEqual(
            moodle_items.filearea_from_url(f"{BASE}/77/mod_resource/content/3/a.pdf"), "content"
        )
        self.assertEqual(
            moodle_items.filearea_from_url(f"{BASE}/88/mod_assign/introattachment/0/b.pdf"),
            "introattachment",
        )

    def test_not_a_pluginfile_url(self) -> None:
        """Other URLs raise ValueError."""
        for url in ["https://docs.example.org/", f"{BASE}/77"]:
            with self.assertRaises(ValueError, msg=url):
                moodle_items.filearea_from_url(url)


class ContentsTests(unittest.TestCase):
    """Tests for items_from_contents()."""

    def setUp(self) -> None:
        """Convert the synthetic course once."""
        self.items = moodle_items.items_from_contents(SECTIONS)
        self.by_name = {item.key.filename: item for item in self.items}

    def test_only_supported_visible_items(self) -> None:
        """Resources, folder files and links; no page, label, forum or hidden module."""
        self.assertEqual(
            sorted(self.by_name), ["Documentació oficial", "a.c", "apunts.pdf", "b.c", "guia.pdf"]
        )

    def test_resource_item(self) -> None:
        """A resource keeps section, key, size and has no subfolder."""
        item = self.by_name["guia.pdf"]
        self.assertEqual(item.kind, KIND_FILE)
        self.assertEqual((item.section.number, item.section.name), (0, "Presentació"))
        self.assertEqual((item.key.cmid, item.key.filearea, item.key.filepath), (2, "content", "/"))
        self.assertEqual(item.subfolder, "")
        self.assertEqual(item.filesize, 1000)

    def test_page_files_without_page_html(self) -> None:
        """Files embedded in a page are kept, in a subfolder; its index.html is not."""
        item = self.by_name["apunts.pdf"]
        self.assertEqual(item.subfolder, "Pàgina")
        self.assertEqual((item.key.cmid, item.key.filearea), (3, "content"))
        self.assertNotIn("index.html", self.by_name)

    def test_folder_files_keep_filepath(self) -> None:
        """Folder files go into a subfolder named after the module and keep filepath."""
        item = self.by_name["b.c"]
        self.assertEqual(item.subfolder, "Exemples")
        self.assertEqual(item.key.filepath, "/extra/")

    def test_url_item(self) -> None:
        """A url module becomes a link item named after the module."""
        item = self.by_name["Documentació oficial"]
        self.assertEqual(item.kind, KIND_URL)
        self.assertEqual(item.url, "https://docs.example.org/")
        self.assertEqual(item.key.filearea, "url")


class AssignmentTests(unittest.TestCase):
    """Tests for items_from_assignments()."""

    def test_visible_assignment_attachments(self) -> None:
        """Only attachments of visible assignments, in a subfolder; introfiles ignored."""
        items = moodle_items.items_from_assignments(ASSIGNMENTS, SECTIONS)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.key.filename, "enunciat.pdf")
        self.assertEqual(item.key.filearea, "introattachment")
        self.assertEqual(item.subfolder, "Pràctica 1")
        self.assertEqual(item.section.number, 1)
        self.assertEqual(item.filesize, 500)

    def test_no_assignments(self) -> None:
        """An empty response gives no items."""
        self.assertEqual(moodle_items.items_from_assignments({"courses": []}, SECTIONS), [])


if __name__ == "__main__":
    unittest.main()
