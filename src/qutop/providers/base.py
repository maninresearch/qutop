"""Plain data types shared by all providers, and the provider interface.

Everything outside ``qutop.providers`` works on these types only, so it can
be tested without a network or an account.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class Status(StrEnum):
    ONLINE = "online"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"


@dataclass(frozen=True, slots=True)
class BackendStatus:
    """What a backend is doing right now. Cheap to fetch, changes by the minute."""

    name: str
    status: Status
    status_message: str
    pending_jobs: int


@dataclass(frozen=True, slots=True)
class BackendSummary:
    """One row of the backends table.

    Error rates are fractions (0.0021, not 0.21%). ``None`` means the
    provider did not report the value.
    """

    name: str
    status: Status
    status_message: str
    pending_jobs: int
    num_qubits: int
    median_2q_error: float | None
    median_readout_error: float | None
    calibrated_at: datetime | None


@dataclass(frozen=True, slots=True)
class QubitCalibration:
    qubit: int
    t1_us: float | None
    t2_us: float | None
    readout_error: float | None
    sx_error: float | None


@dataclass(frozen=True, slots=True)
class EdgeCalibration:
    """Error of the native two-qubit gate on one pair, as the provider lists it."""

    qubits: tuple[int, int]
    gate: str
    error: float


@dataclass(frozen=True, slots=True)
class Calibration:
    """One calibration of one backend.

    ``raw`` is the provider's full record, kept as-is so later analysis is
    not limited to the fields parsed here.
    """

    backend: str
    calibrated_at: datetime
    qubits: tuple[QubitCalibration, ...]
    edges: tuple[EdgeCalibration, ...]
    raw: dict[str, Any]


class ProviderError(Exception):
    """A provider could not be reached or returned something unusable."""


class NotConfiguredError(ProviderError):
    """No saved account or credentials were found for a provider."""


class Provider(Protocol):
    key: str
    """Short stable id stored with every record, e.g. ``"ibm"``."""
    name: str

    def backends(self) -> list[BackendSummary]: ...

    def statuses(self) -> list[BackendStatus]: ...

    def calibration(self, backend: str) -> Calibration | None: ...
