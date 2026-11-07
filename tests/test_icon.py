"""Tests for the packaged icon and the window icon helper."""

import struct
import tkinter as tk
import unittest

from aulari.ui import icon

EXPECTED_SIZES = [16, 24, 32, 48, 64, 128, 256]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


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
