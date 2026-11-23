"""Test doubles shared across test modules."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Any

from qutop.providers.base import BackendStatus, BackendSummary, Calibration, Status
from qutop.providers.ibm import parse_calibration


@cache
def kingston_properties() -> dict[str, Any]:
    """Real calibration data shipped with qiskit-ibm-runtime's fake provider."""
    from qiskit_ibm_runtime.fake_provider import FakeKingston

    data: dict[str, Any] = FakeKingston().properties().to_dict()
    return data


def kingston_calibration(at: datetime | None = None) -> Calibration:
    data = dict(kingston_properties())
    if at is not None:
        data["last_update_date"] = at
    return parse_calibration(data)


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.time = start or datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.time

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.time += timedelta(seconds=seconds)


class StubProvider:
    key = "stub"
    name = "Stub"

    def __init__(
        self,
        summaries: list[BackendSummary] | Exception | None = None,
        statuses: list[BackendStatus] | None = None,
        calibrations: dict[str, Callable[[], Calibration | None]] | None = None,
    ) -> None:
        self.summaries = summaries if summaries is not None else []
        self.status_list = statuses if statuses is not None else []
        self.calibrations = calibrations or {}
        self.status_error: Exception | None = None
        self.status_calls = 0
        self.calibration_calls: list[str] = []

    def backends(self) -> list[BackendSummary]:
        if isinstance(self.summaries, Exception):
            raise self.summaries
        return list(self.summaries)

    def statuses(self) -> list[BackendStatus]:
        self.status_calls += 1
        if self.status_error is not None:
            raise self.status_error
        return list(self.status_list)

    def calibration(self, backend: str) -> Calibration | None:
        self.calibration_calls.append(backend)
        fetch = self.calibrations.get(backend)
        return None if fetch is None else fetch()


def online(name: str, queue: int = 0) -> BackendStatus:
    return BackendStatus(name, Status.ONLINE, "active", queue)
