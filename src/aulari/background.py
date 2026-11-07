"""Headless sync started by the Windows Task Scheduler (decision D7).

'Aulari.exe --background' loads the settings, and if automatic sync is on
and the last background run is old enough, it syncs every academic year
(the same as the 'sync all' button) and returns what is worth telling the
user. The task fires at logon and every N hours; the minimum gap avoids a
second run right after the logon one.
"""

import time
from dataclasses import dataclass, field

from aulari.app import AppError, CampusApp, SiteSyncResult

# Run only if at least this fraction of the interval has passed since the last run.
MIN_GAP_FACTOR = 0.8
SECONDS_PER_HOUR = 3600
NO_SESSION = "site_skipped_no_session"


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


def should_run(app: CampusApp, now: float) -> bool:
    """Return True if automatic sync is on and the last run is old enough."""
    hours = app.settings.auto_sync_hours
    gap = hours * SECONDS_PER_HOUR * MIN_GAP_FACTOR
    result = hours > 0 and now - app.settings.last_background_sync >= gap
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

    Raises: OSError if the settings cannot be saved.
    """
    moment = now if now is not None else time.time()
    outcome = BackgroundOutcome()
    if should_run(app, moment):
        outcome = summarize(app.run_sync_all())
        app.settings.last_background_sync = moment
        app.save()
    return outcome
