"""The refresh loop that records history.

Two jobs on separate schedules:

- status: status and queue length of every backend, every ``status_interval``
- calibration: each backend's calibration, every ``calibration_interval``;
  stored only when the provider's calibration time is new

A failing job is retried with exponential back-off and never stops the other
one. A missing account is the only error that ends the loop.

The clock and sleep are injected so the schedule can be tested without
waiting.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from qutop.providers.base import NotConfiguredError, ProviderError

if TYPE_CHECKING:
    from qutop.providers.base import Provider
    from qutop.store import Store

log = logging.getLogger("qutop.collect")

MIN_STATUS_INTERVAL = 30.0
MIN_CALIBRATION_INTERVAL = 600.0
MAX_BACKOFF = 3600.0


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


@dataclass
class _Job:
    name: str
    interval: float
    run: Callable[[datetime], None]
    due: datetime | None = None  # None: run on the first tick
    failures: int = 0

    def delay(self) -> float:
        if self.failures == 0:
            return self.interval
        backoff: float = self.interval * 2.0**self.failures
        return min(backoff, max(MAX_BACKOFF, self.interval))


@dataclass
class Collector:
    provider: Provider
    store: Store
    status_interval: float = 60.0
    calibration_interval: float = 1800.0
    now: Callable[[], datetime] = _utcnow
    sleep: Callable[[float], None] = _sleep
    _backends: list[str] = field(default_factory=list, init=False)
    _jobs: list[_Job] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if self.status_interval < MIN_STATUS_INTERVAL:
            raise ValueError(f"status interval must be at least {MIN_STATUS_INTERVAL:.0f}s")
        if self.calibration_interval < MIN_CALIBRATION_INTERVAL:
            raise ValueError(
                f"calibration interval must be at least {MIN_CALIBRATION_INTERVAL:.0f}s"
            )
        self._jobs = [
            _Job("status", self.status_interval, self._poll_status),
            _Job("calibration", self.calibration_interval, self._poll_calibrations),
        ]

    def tick(self) -> float:
        """Run every job that is due. Returns seconds until the next one is due."""
        for job in self._jobs:
            now = self.now()
            if job.due is not None and now < job.due:
                continue
            try:
                job.run(now)
            except NotConfiguredError:
                raise
            except ProviderError as exc:
                job.failures += 1
                log.warning("%s: %s (retrying in %.0fs)", job.name, exc, job.delay())
            else:
                job.failures = 0
            job.due = now + timedelta(seconds=job.delay())

        now = self.now()
        wait = min((j.due - now).total_seconds() for j in self._jobs if j.due is not None)
        return max(wait, 0.0)

    def run(self, should_stop: Callable[[], bool] = lambda: False) -> None:
        while not should_stop():
            wait = self.tick()
            if should_stop():
                break
            self.sleep(wait)

    def _poll_status(self, now: datetime) -> None:
        statuses = self.provider.statuses()
        self.store.save_statuses(self.provider.key, statuses, now)
        self._backends = sorted(s.name for s in statuses)
        log.info("status: %d backends", len(statuses))

    def _poll_calibrations(self, now: datetime) -> None:
        if not self._backends:
            self._poll_status(now)
        errors: list[str] = []
        for backend in self._backends:
            try:
                cal = self.provider.calibration(backend)
            except NotConfiguredError:
                raise
            except ProviderError as exc:
                errors.append(str(exc))
                continue
            if cal is None:
                continue
            if self.store.save_calibration(self.provider.key, cal, now):
                log.info(
                    "calibration: saved %s from %s",
                    backend,
                    cal.calibrated_at.strftime("%Y-%m-%d %H:%M UTC"),
                )
        if errors and len(errors) == len(self._backends):
            raise ProviderError(errors[0])
        for message in errors:
            log.warning("calibration: %s", message)
