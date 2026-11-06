"""Unit tests for campus_sync.organizer."""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from campus_sync import organizer


class SanitizeFilenameTests(unittest.TestCase):
    """Tests for sanitize_filename()."""

    def test_forbidden_characters_are_replaced(self) -> None:
        """Every Windows-forbidden character and control char becomes '_'."""
        self.assertEqual(
            organizer.sanitize_filename('a<b>c:d"e/f\\g|h?i*j\x01k'), "a_b_c_d_e_f_g_h_i_j_k"
        )

    def test_trailing_dots_and_spaces_are_removed(self) -> None:
        """Windows silently drops trailing dots and spaces, so we remove them."""
        self.assertEqual(organizer.sanitize_filename("  Tema 1. . "), "Tema 1")

    def test_dot_names_are_rejected(self) -> None:
        """'.', '..' and empty names raise ValueError."""
        for name in [".", "..", "", "   "]:
            with self.assertRaises(ValueError):
                organizer.sanitize_filename(name)

    def test_path_traversal_becomes_harmless(self) -> None:
        """Separators are replaced, so '../x' cannot climb out of a folder."""
        self.assertEqual(organizer.sanitize_filename("../../secret.txt"), ".._.._secret.txt")

    def test_reserved_names_are_prefixed(self) -> None:
        """Device names are reserved with or without extension, in any case."""
        self.assertEqual(organizer.sanitize_filename("CON"), "_CON")
        self.assertEqual(organizer.sanitize_filename("nul.txt"), "_nul.txt")
        self.assertEqual(organizer.sanitize_filename("Com3.tar.gz"), "_Com3.tar.gz")
        self.assertEqual(organizer.sanitize_filename("CONSOLE.txt"), "CONSOLE.txt")

    def test_unicode_is_normalized_to_nfc(self) -> None:
        """A decomposed 'à' (a + combining grave) becomes the single NFC char."""
        self.assertEqual(organizer.sanitize_filename("Ca\u0300lcul"), "C\u00e0lcul")

    def test_long_names_are_truncated_with_hash(self) -> None:
        """Long names keep their extension, fit the limit and stay distinct."""
        first = organizer.sanitize_filename("a" * 150 + "1.pdf")
        second = organizer.sanitize_filename("a" * 150 + "2.pdf")
        self.assertEqual(len(first), organizer.FILE_MAX_LENGTH)
        self.assertTrue(first.endswith(".pdf"))
        self.assertNotEqual(first, second)

    def test_short_names_are_unchanged(self) -> None:
        """A normal name passes through untouched."""
        self.assertEqual(organizer.sanitize_filename("Pràctica 2.pdf"), "Pràctica 2.pdf")


class EnsureInsideTests(unittest.TestCase):
    """Tests for ensure_inside()."""

    def test_path_inside_is_accepted(self) -> None:
        """A child path is returned resolved."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = organizer.ensure_inside(root, root / "2026-27" / "SO" / "a.pdf")
            self.assertTrue(result.is_relative_to(root.resolve()))

    def test_path_outside_is_rejected(self) -> None:
        """A path that climbs out with '..' raises ValueError."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(ValueError):
                organizer.ensure_inside(root, root / ".." / "evil.txt")


class UniqueNameTests(unittest.TestCase):
    """Tests for unique_name()."""

    def test_no_collision(self) -> None:
        """A free name is returned as is."""
        self.assertEqual(organizer.unique_name("a.pdf", {"b.pdf"}), "a.pdf")

    def test_collision_ignores_case(self) -> None:
        """'A.PDF' collides with 'a.pdf' and the next free number is used."""
        existing = {"a.pdf", "a (2).pdf"}
        self.assertEqual(organizer.unique_name("A.PDF", existing), "A (3).PDF")

    def test_folder_names_are_not_split_at_dots(self) -> None:
        """With keep_extension=False the counter goes at the end."""
        result = organizer.unique_name("Tema 1. Intro", {"tema 1. intro"}, keep_extension=False)
        self.assertEqual(result, "Tema 1. Intro (2)")


class CourseNameTests(unittest.TestCase):
    """Tests for clean_course_name() and course_alias() with e-aules names."""

    def test_clean_course_name(self) -> None:
        """Codes and academic year suffixes seen on e-aules are removed."""
        cases = {
            "Programació 3 (2026-27) [MO88446]": "Programació 3",
            "Sistemes Operatius - Grau Enginyeria Informàtica 26-27 [MO86824]": (
                "Sistemes Operatius"
            ),
            "Xarxes [MO86254]": "Xarxes",
            "Consell d'Estudiants - Informació [MO90892]": "Consell d'Estudiants - Informació",
        }
        for fullname, expected in cases.items():
            self.assertEqual(organizer.clean_course_name(fullname), expected)

    def test_course_alias(self) -> None:
        """Aliases follow design.md section 5 and decision D4."""
        cases = {
            "Camps i Ones Electromagnètiques [MO88104]": "CiOE",
            "Sistemes Digitals i Llenguatges de Descripció del Hardware [MO85902]": "SDiLDH",
            "Sistemes Operatius - Grau Enginyeria Informàtica 26-27 [MO86824]": "SO",
            "Estructura de Computadors [MO86223]": "EC",
            "Programació 3 (2026-27) [MO88446]": "P3",
            "Càlcul II": "C2",
            "Història de l'Enginyeria": "HE",
            "Història de l\u2019Enginyeria": "HE",
            "Xarxes [MO86254]": "Xarxes",
            "VIU la UAB [MO86153]": "VIUUAB",
        }
        for fullname, expected in cases.items():
            self.assertEqual(organizer.course_alias(fullname), expected, fullname)


class AcademicYearTests(unittest.TestCase):
    """Tests for the academic year helpers (D6)."""

    def test_year_from_site_url(self) -> None:
        """The year is read from the e-aules URL path."""
        self.assertEqual(
            organizer.academic_year_from_site("https://e-aules.uab.cat/2026-27"), "2026-27"
        )
        self.assertEqual(
            organizer.academic_year_from_site("https://e-aules.uab.cat/2025-26/"), "2025-26"
        )

    def test_no_year_in_site_url(self) -> None:
        """Sites without a valid year in the path return None."""
        self.assertIsNone(organizer.academic_year_from_site("https://moodle.example.org"))
        self.assertIsNone(organizer.academic_year_from_site("https://x.org/2026-30"))

    def test_year_from_startdate(self) -> None:
        """Cutoff on 1 September; 0 means no academic year."""
        september = int(datetime(2026, 9, 3, tzinfo=UTC).timestamp())
        july = int(datetime(2026, 7, 20, tzinfo=UTC).timestamp())
        self.assertEqual(organizer.academic_year_from_startdate(september), "2026-27")
        self.assertEqual(organizer.academic_year_from_startdate(july), "2025-26")
        self.assertEqual(organizer.academic_year_from_startdate(0), organizer.NO_ACADEMIC_YEAR)


class SectionAndVersionTests(unittest.TestCase):
    """Tests for section_folder(), versioned_filename() and url_shortcut_content()."""

    def test_section_folder(self) -> None:
        """Section 0 is always 'General'; others keep their sanitized name."""
        self.assertEqual(organizer.section_folder(0, "Presentació"), "00 - General")
        self.assertEqual(organizer.section_folder(3, "Tema 3: Processos"), "03 - Tema 3_ Processos")
        self.assertEqual(organizer.section_folder(4, "  "), "04")
        self.assertLessEqual(
            len(organizer.section_folder(5, "x" * 200)), organizer.SECTION_MAX_LENGTH
        )

    def test_versioned_filename(self) -> None:
        """Date, and optionally time, go before the extension."""
        moment = datetime(2026, 11, 6, 18, 5)
        self.assertEqual(
            organizer.versioned_filename("Tema 1.pdf", moment), "Tema 1 (2026-11-06).pdf"
        )
        self.assertEqual(
            organizer.versioned_filename("Tema 1.pdf", moment, include_time=True),
            "Tema 1 (2026-11-06 1805).pdf",
        )

    def test_url_shortcut_content(self) -> None:
        """Only http(s) without line breaks is accepted, with the exact format."""
        self.assertEqual(
            organizer.url_shortcut_content("https://example.org/a?b=1"),
            "[InternetShortcut]\r\nURL=https://example.org/a?b=1\r\n",
        )
        for bad_url in ["file:///C:/x.exe", "javascript:alert(1)", "https://x.org/\r\nIconFile=x"]:
            with self.assertRaises(ValueError):
                organizer.url_shortcut_content(bad_url)


class LocalTimeTests(unittest.TestCase):
    """Tests for to_local_time() (Europe/Madrid, RF7)."""

    def test_winter_and_summer_offsets(self) -> None:
        """CET is UTC+1 in winter and CEST is UTC+2 in summer."""
        winter = int(datetime(2026, 11, 6, 17, 5, tzinfo=UTC).timestamp())
        summer = int(datetime(2026, 7, 1, 10, 0, tzinfo=UTC).timestamp())
        self.assertEqual(organizer.to_local_time(winter).strftime("%H:%M"), "18:05")
        self.assertEqual(organizer.to_local_time(summer).strftime("%H:%M"), "12:00")

    def test_date_changes_near_midnight(self) -> None:
        """23:30 UTC is already the next day in Madrid, so the version date moves."""
        late = int(datetime(2026, 11, 6, 23, 30, tzinfo=UTC).timestamp())
        name = organizer.versioned_filename("a.pdf", organizer.to_local_time(late))
        self.assertEqual(name, "a (2026-11-07).pdf")


if __name__ == "__main__":
    unittest.main()
