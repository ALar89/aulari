"""Pass the moodlemobile:// URL from the callback process to the open window.

When the browser opens moodlemobile://token=..., Windows starts a second
copy of the app with --login-callback. That copy sends the URL to the
window that is waiting, through a local socket:
- the socket listens only on 127.0.0.1, on a random port;
- port and a one-time key are written to login_handoff.json in the app
  data folder (readable only by this user); the token is never written;
- messages are limited in size and must carry the right key.
"""

import json
import secrets
import select
import socket
from pathlib import Path

HANDOFF_NAME = "login_handoff.json"
HOST = "127.0.0.1"
MAX_MESSAGE_BYTES = 8192
READ_TIMEOUT_SECONDS = 2.0
CONNECT_TIMEOUT_SECONDS = 5.0
KEY_BYTES = 16


def _read_message(connection: socket.socket) -> bytes:
    """Read at most MAX_MESSAGE_BYTES + 1 bytes, until the sender closes.

    Raises: OSError (including TimeoutError) on socket errors.
    """
    data = b""
    receiving = True
    while receiving and len(data) <= MAX_MESSAGE_BYTES:
        chunk = connection.recv(MAX_MESSAGE_BYTES + 1 - len(data))
        data = data + chunk
        receiving = chunk != b""
    return data


class HandoffServer:
    """Listening socket of the window that waits for the browser."""

    def __init__(self, app_dir: Path) -> None:
        """Open the socket and publish its port and key in login_handoff.json.

        Raises: OSError if the socket or the file cannot be created.
        """
        self._path = app_dir / HANDOFF_NAME
        self._key = secrets.token_hex(KEY_BYTES)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.bind((HOST, 0))
        self._socket.listen(1)
        app_dir.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as handoff_file:
            json.dump({"port": self._socket.getsockname()[1], "key": self._key}, handoff_file)

    def _check(self, data: bytes) -> str | None:
        """Return the URL inside a message if it is well formed and has our key."""
        result = None
        if len(data) <= MAX_MESSAGE_BYTES:
            try:
                message = json.loads(data.decode("utf-8"))
            except UnicodeDecodeError, json.JSONDecodeError:
                message = None
            if (
                isinstance(message, dict)
                and isinstance(message.get("key"), str)
                and isinstance(message.get("url"), str)
                and secrets.compare_digest(message["key"], self._key)
            ):
                result = message["url"]
        return result

    def poll(self) -> str | None:
        """Return the URL if a valid message arrived, without blocking; else None.

        Raises: OSError on socket errors.
        """
        result = None
        readable, _, _ = select.select([self._socket], [], [], 0)
        if readable:
            connection, _ = self._socket.accept()
            with connection:
                connection.settimeout(READ_TIMEOUT_SECONDS)
                result = self._check(_read_message(connection))
        return result

    def close(self) -> None:
        """Close the socket and delete login_handoff.json."""
        self._socket.close()
        self._path.unlink(missing_ok=True)


def send_to_running_app(app_dir: Path, url: str) -> bool:
    """Send url to the waiting window; return False if no window is waiting.

    Raises: OSError on unexpected socket errors; json.JSONDecodeError if the
    handoff file is corrupt.
    """
    result = False
    path = app_dir / HANDOFF_NAME
    if path.is_file():
        with path.open(encoding="utf-8") as handoff_file:
            info = json.load(handoff_file)
        message = json.dumps({"key": info["key"], "url": url}).encode("utf-8")
        try:
            with socket.create_connection(
                (HOST, int(info["port"])), timeout=CONNECT_TIMEOUT_SECONDS
            ) as connection:
                connection.sendall(message)
            result = True
        except ConnectionRefusedError:
            # A stale file from a window that is no longer open.
            result = False
    return result
