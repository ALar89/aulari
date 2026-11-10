"""The Aulari icon on the windows (title bar, taskbar and Alt+Tab).

The icon lives in aulari/assets/aulari.ico, the same file PyInstaller puts on
the executable. Its images are stored as PNG, so they are read straight from
the .ico and handed to Tk with iconphoto(): one asset for everything.
A missing or damaged icon is only logged; the app works without it.

macOS is different: Tk turns only the first image of iconphoto() into the
Dock icon of the whole app (tkMacOSXWm.c), and windows have no title-bar
icon. Inside the app bundle nothing is set, so the Dock keeps the bundle's
.icns (up to 1024 px); when run from source, only the largest image is given.
"""

import base64
import logging
import struct
import sys
import tkinter as tk
from importlib import resources

ICON_NAME = "aulari.ico"
DIRECTORY_SIZE = 6
ENTRY_SIZE = 16

logger = logging.getLogger(__name__)


def read_icon_images(data: bytes) -> list[tuple[int, bytes]]:
    """Return (size, PNG bytes) for every image of an .ico whose images are PNG.

    Raises: ValueError if the data is not an icon file; struct.error if it is
    truncated.
    """
    result = []
    reserved, kind, count = struct.unpack("<HHH", data[:DIRECTORY_SIZE])
    if reserved != 0 or kind != 1:
        raise ValueError("Not an icon file")
    for index in range(count):
        start = DIRECTORY_SIZE + index * ENTRY_SIZE
        side, _height, _colors, _reserved, _planes, _bits, length, offset = struct.unpack(
            "<BBBBHHII", data[start : start + ENTRY_SIZE]
        )
        result.append((side or 256, data[offset : offset + length]))
    return result


def load_icon_data() -> bytes:
    """Return the bytes of the packaged icon.

    Raises: OSError if the file is missing.
    """
    source = resources.files("aulari") / "assets" / ICON_NAME
    with source.open("rb") as icon_file:
        result = icon_file.read()
    return result


def images_for_system(images: list[tuple[int, bytes]]) -> list[tuple[int, bytes]]:
    """Return the (size, PNG bytes) images to hand to Tk on this system.

    On macOS Tk uses only the first image, so it gets just the largest one;
    elsewhere Tk picks the right size from all of them.

    Raises: ValueError if images is empty on macOS.
    """
    result = images
    if sys.platform == "darwin":
        result = [max(images)]  # tuples compare by size first
    return result


def set_window_icon(root: tk.Tk) -> None:
    """Give this window, and the ones opened later, the Aulari icon.

    In the macOS app bundle nothing is set: the Dock keeps the bundle's .icns.
    """
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        logger.debug("macOS app bundle: the Dock keeps the bundle icon")
    else:
        try:
            images = [
                tk.PhotoImage(master=root, data=base64.b64encode(png))
                for _size, png in images_for_system(read_icon_images(load_icon_data()))
            ]
            root.iconphoto(True, *images)
            root.aulari_icon_images = images  # keep them alive as long as the window
        except (OSError, ValueError, struct.error, tk.TclError) as error:
            logger.warning("Could not set the window icon: %s", error)
