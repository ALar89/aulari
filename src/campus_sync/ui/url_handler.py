"""Register this app as the handler of moodlemobile:// links, only while logging in.

e-aules always redirects the browser login to moodlemobile://token=...
(design.md section 10), so Windows must know which program opens that
scheme. The registration is written for the current user only
(HKCU\\Software\\Classes, no administrator rights), any previous handler (for
example the official Moodle app) is backed up and restored, and everything
is removed as soon as the login finishes.

The base key path is a parameter so tests can use a harmless test key.
"""

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

if sys.platform == "win32":
    import winreg

SCHEME = "moodlemobile"
CLASSES_PATH = r"Software\Classes"
CALLBACK_FLAG = "--login-callback"
BACKUP_NAME = "url_handler_backup.json"
HANDLER_NAME = "URL:Campus Sync login"
URL_PROTOCOL_VALUE = "URL Protocol"
COMMAND_SUBKEY = r"shell\open\command"


@dataclass(frozen=True)
class Handler:
    """What is registered for the scheme: description, URL Protocol flag, command."""

    name: str
    url_protocol: bool
    command: str


def _require_windows() -> None:
    """Raise if not on Windows.

    Raises: OSError on other systems.
    """
    if sys.platform != "win32":
        raise OSError("URL scheme registration is only implemented for Windows")


def handler_command() -> str:
    """Return the command Windows runs for a moodlemobile:// link.

    A packaged executable runs itself; in development pythonw.exe runs the
    package without opening a console window.
    """
    if getattr(sys, "frozen", False):
        result = f'"{sys.executable}" {CALLBACK_FLAG} "%1"'
    else:
        python = Path(sys.executable)
        windowed = python.with_name("pythonw.exe")
        interpreter = windowed if windowed.is_file() else python
        result = f'"{interpreter}" -m campus_sync {CALLBACK_FLAG} "%1"'
    return result


def is_ours(handler: Handler | None) -> bool:
    """Return True if the registered handler is this app."""
    result = handler is not None and CALLBACK_FLAG in handler.command
    return result


def _scheme_path(base_path: str) -> str:
    """Return the registry path of the scheme key under base_path."""
    result = f"{base_path}\\{SCHEME}"
    return result


def _read_default(key: object) -> str:
    """Return the default value of an open key, or '' if it has none."""
    result = ""
    count = winreg.QueryInfoKey(key)[1]
    index = 0
    while index < count:
        name, value, _ = winreg.EnumValue(key, index)
        if name == "":
            result = str(value)
        index = index + 1
    return result


def _has_value(key: object, value_name: str) -> bool:
    """Return True if an open key has a value with that name."""
    found = False
    count = winreg.QueryInfoKey(key)[1]
    index = 0
    while index < count and not found:
        found = winreg.EnumValue(key, index)[0] == value_name
        index = index + 1
    return found


def _key_exists(path: str) -> bool:
    """Return True if HKCU\\path exists (without raising for a missing key)."""
    parent, _, child = path.rpartition("\\")
    exists = False
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, parent) as parent_key:
        count = winreg.QueryInfoKey(parent_key)[0]
        index = 0
        while index < count and not exists:
            exists = winreg.EnumKey(parent_key, index).lower() == child.lower()
            index = index + 1
    return exists


def read_handler(base_path: str = CLASSES_PATH) -> Handler | None:
    """Return the current handler of moodlemobile://, or None if there is none.

    Raises: OSError (not Windows, or registry errors).
    """
    _require_windows()
    result = None
    path = _scheme_path(base_path)
    if _key_exists(path):
        command = ""
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            name = _read_default(key)
            url_protocol = _has_value(key, URL_PROTOCOL_VALUE)
        command_path = f"{path}\\{COMMAND_SUBKEY}"
        if _key_exists(command_path):
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, command_path) as key:
                command = _read_default(key)
        result = Handler(name, url_protocol, command)
    return result


def _write_handler(path: str, handler: Handler) -> None:
    """Write a handler at HKCU\\path."""
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, handler.name)
        if handler.url_protocol:
            winreg.SetValueEx(key, URL_PROTOCOL_VALUE, 0, winreg.REG_SZ, "")
    if handler.command:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{path}\\{COMMAND_SUBKEY}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, handler.command)


def _delete_tree(path: str) -> None:
    """Delete HKCU\\path and all its subkeys (recursive; base case: no subkeys)."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
        children = [winreg.EnumKey(key, index) for index in range(winreg.QueryInfoKey(key)[0])]
    for child in children:
        _delete_tree(f"{path}\\{child}")
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)


def register(app_dir: Path, base_path: str = CLASSES_PATH) -> None:
    """Make this app the handler of moodlemobile://, backing up any other handler.

    Raises: OSError (not Windows, registry or backup file errors).
    """
    _require_windows()
    current = read_handler(base_path)
    if current is not None and not is_ours(current):
        app_dir.mkdir(parents=True, exist_ok=True)
        with (app_dir / BACKUP_NAME).open("w", encoding="utf-8") as backup_file:
            json.dump(asdict(current), backup_file)
    if current is not None:
        _delete_tree(_scheme_path(base_path))
    _write_handler(_scheme_path(base_path), Handler(HANDLER_NAME, True, handler_command()))


def unregister(app_dir: Path, base_path: str = CLASSES_PATH) -> None:
    """Remove this app's handler and restore the previous one, if any.

    Does nothing if the current handler belongs to another program.

    Raises: OSError (not Windows, registry or backup file errors);
    json.JSONDecodeError if the backup file is corrupt.
    """
    _require_windows()
    backup_path = app_dir / BACKUP_NAME
    if is_ours(read_handler(base_path)):
        _delete_tree(_scheme_path(base_path))
        if backup_path.is_file():
            with backup_path.open(encoding="utf-8") as backup_file:
                previous = Handler(**json.load(backup_file))
            _write_handler(_scheme_path(base_path), previous)
    backup_path.unlink(missing_ok=True)
