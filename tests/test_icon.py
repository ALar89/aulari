"""Tests for the packaged icon and the window icon helper."""

import struct
import tkinter as tk
import unittest
from pathlib import Path

from aulari.ui import icon

EXPECTED_SIZES = [16, 24, 32, 48, 64, 128, 256]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ICNS_PATH = Path(__file__).resolve().parent.parent / "packaging" / "aulari.icns"
# Type -> pixel size of every image the macOS icon must hold (see packaging/make_icon.py).
EXPECTED_ICNS = {
    b"icp4": 16,
    b"icp5": 32,
    b"icp6": 64,
    b"ic07": 128,
    b"ic08": 256,
    b"ic09": 512,
    b"ic10": 1024,
    b"ic11": 32,
    b"ic12": 64,
    b"ic13": 256,
    b"ic14": 512,
}
ICNS_HEADER_SIZE = 8


def read_icns_entries(data: bytes) -> dict[bytes, bytes]:
    """Return type -> image bytes for every entry of an .icns file.

    Raises: ValueError if the data is not an .icns file or its length is wrong.
    """
    if data[:4] != b"icns" or struct.unpack(">I", data[4:8])[0] != len(data):
        raise ValueError("Not an .icns file")
    result = {}
    position = ICNS_HEADER_SIZE
    while position < len(data):
        kind, length = struct.unpack(">4sI", data[position : position + ICNS_HEADER_SIZE])
        result[kind] = data[position + ICNS_HEADER_SIZE : position + length]
        position = position + length
    return result


class IconFileTests(unittest.TestCase):
    """The .ico used by the executable and the windows."""

    def test_icon_has_every_size_as_png(self) -> None:
        """The icon holds the seven sizes, each a square PNG of its size."""
        images = icon.read_icon_images(icon.load_icon_data())
        self.assertEqual([size for size, _png in images], EXPECTED_SIZES)
        for size, png in images:
            self.assertTrue(png.startswith(PNG_SIGNATURE))
            self.assertEqual(struct.unpack(">II", png[16:24]), (size, size))

    def test_not_an_icon(self) -> None:
        """Other data is refused."""
        with self.assertRaises(ValueError):
            icon.read_icon_images(PNG_SIGNATURE)


class IcnsFileTests(unittest.TestCase):
    """The .icns used by the macOS app bundle."""

    def test_icns_has_every_size_as_png(self) -> None:
        """The icon holds the eleven entries, each a square PNG of its size."""
        entries = read_icns_entries(ICNS_PATH.read_bytes())
        self.assertEqual(set(entries), set(EXPECTED_ICNS))
        for kind, png in entries.items():
            size = EXPECTED_ICNS[kind]
            self.assertTrue(png.startswith(PNG_SIGNATURE), kind)
            self.assertEqual(struct.unpack(">II", png[16:24]), (size, size), kind)


class WindowIconTests(unittest.TestCase):
    """set_window_icon()."""

    def test_window_gets_the_icon(self) -> None:
        """The images are loaded into Tk and kept alive on the window."""
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("No display available for Tkinter")
        root.withdraw()
        icon.set_window_icon(root)
        self.assertEqual(len(root.aulari_icon_images), len(EXPECTED_SIZES))
        self.assertEqual(root.aulari_icon_images[-1].width(), 256)
        root.destroy()


if __name__ == "__main__":
    unittest.main()
