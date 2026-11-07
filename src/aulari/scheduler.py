"""Automatic sync with the Windows Task Scheduler (decision D7).

A per-user task runs 'Aulari.exe --background' when the user logs on to
Windows and then every N hours. It is created with schtasks.exe (part of
Windows, no administrator rights) from an XML definition, and deleted when
the user turns automatic sync off.

The command runner is a parameter so tests never touch the real scheduler.
"""

import getpass
import os
import secrets
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = r"Aulari\Sync"
BACKGROUND_FLAG = "--background"
ALLOWED_HOURS = (24, 12, 6)  # never more often than every 6 hours (be nice to the server)
LOGON_DELAY = "PT2M"  # wait 2 minutes after logon: network and desktop are ready
TIME_LIMIT = "PT1H"
SCHTASKS = "schtasks"

Runner = Callable[[list[str]], subprocess.CompletedProcess]


class SchedulerError(Exception):
    """schtasks.exe failed; the message has its output (no secrets in it)."""


def run_command(arguments: list[str]) -> subprocess.CompletedProcess:
    """Run a command without a shell and capture its text output."""
    result = subprocess.run(
        arguments, capture_output=True, text=True, check=False, creationflags=_no_window()
    )
    return result


def _no_window() -> int:
    """Return the flag that hides the console window of child processes on Windows."""
    result = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return result


def background_command() -> tuple[str, str]:
    """Return (program, arguments) that the task runs.

    A packaged executable runs itself; in development pythonw.exe runs the
    package without opening a console window.
    """
    if getattr(sys, "frozen", False):
        result = (sys.executable, BACKGROUND_FLAG)
    else:
        python = Path(sys.executable)
        windowed = python.with_name("pythonw.exe")
        interpreter = windowed if windowed.is_file() else python
        result = (str(interpreter), f"-m aulari {BACKGROUND_FLAG}")
    return result


def app_command() -> list[str]:
    """Return the command that opens the full Aulari window."""
    program, arguments = background_command()
    parts = arguments.split()
    result = [program, *parts[: len(parts) - 1]]
    return result


def current_user() -> str:
    """Return DOMAIN\\user for the task principal (the user running the app)."""
    domain = os.environ.get("USERDOMAIN", "")
    user = getpass.getuser()
    result = f"{domain}\\{user}" if domain else user
    return result


def task_xml(hours: int, program: str, arguments: str, user: str, start: datetime) -> str:
    """Return the Task Scheduler XML: logon trigger plus a repetition every 'hours'.

    Raises: ValueError if hours is not one of ALLOWED_HOURS.
    """
    if hours not in ALLOWED_HOURS:
        raise ValueError(f"Unsupported interval: {hours} hours")
    result = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Aulari: sync course material in the background.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
      <Delay>{LOGON_DELAY}</Delay>
    </LogonTrigger>
    <TimeTrigger>
      <Enabled>true</Enabled>
      <StartBoundary>{start.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>
      <Repetition>
        <Interval>PT{hours}H</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>{TIME_LIMIT}</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(program)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""
    return result


def _check(result: subprocess.CompletedProcess) -> None:
    """Raise if schtasks.exe failed.

    Raises: SchedulerError with schtasks' own message.
    """
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise SchedulerError(message or f"schtasks exit code {result.returncode}")


def enable(hours: int, runner: Runner = run_command, start: datetime | None = None) -> None:
    """Create (or replace) the automatic sync task.

    Raises: ValueError for an unsupported interval; SchedulerError if
    schtasks fails; OSError if the temporary XML file cannot be written.
    """
    program, arguments = background_command()
    xml = task_xml(hours, program, arguments, current_user(), start or datetime.now())
    path = Path(tempfile.gettempdir()) / f"aulari-task-{secrets.token_hex(4)}.xml"
    try:
        # schtasks.exe expects the XML file in UTF-16.
        with path.open("w", encoding="utf-16") as xml_file:
            xml_file.write(xml)
        _check(runner([SCHTASKS, "/Create", "/TN", TASK_NAME, "/XML", str(path), "/F"]))
    finally:
        path.unlink(missing_ok=True)


def is_enabled(runner: Runner = run_command) -> bool:
    """Return True if the automatic sync task exists."""
    result = runner([SCHTASKS, "/Query", "/TN", TASK_NAME]).returncode == 0
    return result


def disable(runner: Runner = run_command) -> None:
    """Delete the automatic sync task if it exists.

    Raises: SchedulerError if schtasks fails to delete an existing task.
    """
    if is_enabled(runner):
        _check(runner([SCHTASKS, "/Delete", "/TN", TASK_NAME, "/F"]))
