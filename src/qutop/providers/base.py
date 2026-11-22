"""Plain data types shared by all providers, and the provider interface.

Everything outside ``qutop.providers`` works on these types only, so it can
be tested without a network or an account.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class Status(StrEnum):
    ONLINE = "online"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"


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


class ProviderError(Exception):
    """A provider could not be reached or returned something unusable."""


class NotConfiguredError(ProviderError):
    """No saved account or credentials were found for a provider."""


class Provider(Protocol):
    name: str

    def backends(self) -> list[BackendSummary]: ...
