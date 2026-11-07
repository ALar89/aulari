"""Headless sync started by the Windows Task Scheduler (decision D7).

'Aulari.exe --background' loads the settings, and if automatic sync is on
and the last background run is old enough, it syncs every academic year
(the same as the 'sync all' button) and returns what is worth telling the
user. The task fires at logon and every N hours; the minimum gap avoids a
second run right after the logon one.

The time of the last run is kept in background_state.json, not in
config.json: an open window saves its own copy of the settings and would
otherwise overwrite it (found in the first real test, design.md section 10).
"""

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from aulari.app import AppError, CampusApp, SiteSyncResult

# Run only if at least this fraction of the interval has passed since the last run.
MIN_GAP_FACTOR = 0.8
SECONDS_PER_HOUR = 3600
NO_SESSION = "site_skipped_no_session"
STATE_NAME = "background_state.json"

logger = logging.getLogger(__name__)


@dataclass
class BackgroundOutcome:
    """What a background run did, for the notice window."""

    ran: bool = False
    new_files: int = 0
    new_versions: int = 0
    announcements: int = 0
    expired_sites: list[str] = field(default_factory=list)
    errors: int = 0

    def worth_notifying(self) -> bool:
        """Return True if the user should see a notice."""
        result = bool(
            self.new_files or self.new_versions or self.announcements or self.expired_sites
        )
        return result


def load_last_run(app_dir: Path) -> float:
    """Return the Unix time of the last background run (0 if none or unreadable)."""
    result = 0.0
    path = app_dir / STATE_NAME
    if path.is_file():
        try:
            with path.open(encoding="utf-8") as state_file:
                data = json.load(state_file)
            if isinstance(data, dict) and isinstance(data.get("last_run"), (int, float)):
                result = float(data["last_run"])
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("Ignoring unreadable %s: %s", STATE_NAME, error)
    return result


def save_last_run(app_dir: Path, moment: float) -> None:
    """Remember the time of this background run.

    Raises: OSError if the file cannot be written.
    """
    app_dir.mkdir(parents=True, exist_ok=True)
    with (app_dir / STATE_NAME).open("w", encoding="utf-8") as state_file:
        json.dump({"last_run": moment}, state_file)


def should_run(app: CampusApp, now: float) -> bool:
    """Return True if automatic sync is on and the last run is old enough."""
    hours = app.settings.auto_sync_hours
    gap = hours * SECONDS_PER_HOUR * MIN_GAP_FACTOR
    result = hours > 0 and now - load_last_run(app.app_dir) >= gap
    return result


def _session_expired(item: SiteSyncResult) -> bool:
    """Return True if a site could not be synced because its session is gone."""
    expired_result = item.result is not None and item.result.summary.invalid_token
    expired_error = isinstance(item.error, AppError) and item.error.key == "error_invalid_token"
    result = item.skipped_key == NO_SESSION or expired_result or expired_error
    return result


def summarize(results: list[SiteSyncResult]) -> BackgroundOutcome:
    """Add up the per-site results of a 'sync all' run."""
    outcome = BackgroundOutcome(ran=True)
    for item in results:
        if item.result is not None:
            outcome.new_files = outcome.new_files + item.result.summary.new
            outcome.new_versions = outcome.new_versions + item.result.summary.new_versions
            outcome.announcements = outcome.announcements + len(item.result.announcements)
            outcome.errors = outcome.errors + len(item.result.summary.errors)
        if item.error is not None and not _session_expired(item):
            outcome.errors = outcome.errors + 1
        if _session_expired(item):
            outcome.expired_sites.append(item.site_url)
    return outcome


def run_background(app: CampusApp, now: float | None = None) -> BackgroundOutcome:
    """Sync every academic year if it is time to; return the outcome.

    Raises: OSError if the time of the run cannot be saved.
    """
    moment = now if now is not None else time.time()
    outcome = BackgroundOutcome()
    due = should_run(app, moment)
    logger.info(
        "Background run: every %s h, last %s, due %s",
        app.settings.auto_sync_hours,
        load_last_run(app.app_dir),
        due,
    )
    if due:
        outcome = summarize(app.run_sync_all())
        save_last_run(app.app_dir, moment)
        logger.info("Background run finished: %s (saved last %s)", outcome, moment)
    return outcome
