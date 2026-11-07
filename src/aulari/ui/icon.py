"""The Aulari icon on the windows (title bar, taskbar and Alt+Tab).

The icon lives in aulari/assets/aulari.ico, the same file PyInstaller puts on
the executable. Its images are stored as PNG, so they are read straight from
the .ico and handed to Tk with iconphoto(): one asset for everything.
A missing or damaged icon is only logged; the app works without it.
"""

import base64
import logging
import struct
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


def set_window_icon(root: tk.Tk) -> None:
    """Give this window, and the ones opened later, the Aulari icon."""
    try:
        images = [
            tk.PhotoImage(master=root, data=base64.b64encode(png))
            for _size, png in read_icon_images(load_icon_data())
        ]
        root.iconphoto(True, *images)
        root.aulari_icon_images = images  # keep them alive as long as the window
    except (OSError, ValueError, struct.error, tk.TclError) as error:
        logger.warning("Could not set the window icon: %s", error)
