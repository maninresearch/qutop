"""Test doubles shared across test modules."""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

from qutop.providers.base import BackendStatus, BackendSummary, Calibration, Status
from qutop.providers.ibm import parse_calibration

FIXTURES = Path(__file__).parent / "fixtures"


@cache
def _kingston_json() -> bytes:
    return gzip.decompress((FIXTURES / "ibm" / "kingston_properties.json.gz").read_bytes())


def kingston_properties() -> dict[str, Any]:
    """Real ibm_kingston calibration (see fixtures/README.md). A fresh copy each call."""
    data: dict[str, Any] = json.loads(_kingston_json())
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


class FakeTransport:
    """Answers HTTP requests from a routing table; records what was asked."""

    def __init__(self, routes: dict[tuple[str, str], Any] | None = None) -> None:
        from qutop.providers.http import Request, Response

        self.Request = Request
        self.Response = Response
        self.routes: dict[tuple[str, str], Any] = routes or {}
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float) -> Any:
        self.requests.append(request)
        path = request.url.split("?")[0]
        answer = self.routes.get((request.method, path))
        if answer is None:
            return self.Response(404, {}, b'{"message": "not found"}')
        if callable(answer):
            answer = answer(request)
        if isinstance(answer, self.Response):
            return answer
        return self.Response(200, {}, json.dumps(answer).encode())


CRN = "crn:v1:bluemix:public:quantum-computing:us-east:a/abc123:inst-1::"
API = "https://quantum.cloud.ibm.com/api/v1"
IAM = "https://iam.cloud.ibm.com/identity/token"
SEARCH = "https://api.global-search-tagging.cloud.ibm.com/v3/resources/search"


def ibm_routes(
    overrides: dict[tuple[str, str], Any] | None = None,
) -> dict[tuple[str, str], Any]:
    """A small IBM Quantum account: kingston online, torino in maintenance."""
    props = kingston_properties()
    routes: dict[tuple[str, str], Any] = {
        ("POST", IAM): {"access_token": "iam-token", "expires_in": 3600},
        ("POST", SEARCH): {"items": [{"name": "my-instance", "crn": CRN}]},
        ("GET", f"{API}/backends"): {
            "devices": [
                {
                    "name": "ibm_kingston",
                    "qubits": 156,
                    "queue_length": 12,
                    "status": {"name": "online"},
                },
                {
                    "name": "ibm_torino",
                    "qubits": 133,
                    "queue_length": 0,
                    "status": {"name": "offline", "reason": "maintenance"},
                },
            ]
        },
        ("GET", f"{API}/backends/ibm_kingston/properties"): props,
        ("GET", f"{API}/backends/ibm_torino/properties"): {},
    }
    routes.update(overrides or {})
    return routes
