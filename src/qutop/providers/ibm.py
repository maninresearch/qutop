"""IBM Quantum, through the official ``qiskit-ibm-runtime`` client.

Credentials come from the account saved with
``QiskitRuntimeService.save_account()``; qutop never reads or stores tokens
itself. Nothing here submits jobs or uses QPU time.

Calibration is parsed from the JSON form of IBM's backend properties (the
shape the REST API returns and ``BackendProperties.to_dict()`` produces),
so parsing does not depend on Qiskit's object model.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from statistics import median
from typing import Any, TypeVar

from qutop.providers.base import (
    BackendStatus,
    BackendSummary,
    Calibration,
    EdgeCalibration,
    NotConfiguredError,
    ProviderError,
    QubitCalibration,
    Status,
)

# Native two-qubit gates, in order of preference. Backends can also report
# other two-qubit gates (for example ``rzz`` with fractional gates enabled);
# those are not what most circuits are compiled to, so they are left out.
NATIVE_2Q_GATES = ("cz", "ecr", "cx")

T = TypeVar("T")

_TO_MICROSECONDS = {"s": 1e6, "ms": 1e3, "us": 1.0, "µs": 1.0, "ns": 1e-3}


class IBMProvider:
    key = "ibm"
    name = "IBM Quantum"

    def __init__(self, service: Any | None = None) -> None:
        self._service = service

    def _get_service(self) -> Any:
        if self._service is None:
            # Imported here: qiskit takes seconds to import, and commands
            # that never touch IBM should not pay for it.
            from qiskit_ibm_runtime import QiskitRuntimeService
            from qiskit_ibm_runtime.accounts import AccountNotFoundError

            try:
                self._service = QiskitRuntimeService()
            except AccountNotFoundError as exc:
                raise NotConfiguredError(
                    "no saved IBM Quantum account; save one with "
                    "QiskitRuntimeService.save_account(token=..., set_as_default=True)"
                ) from exc
            except Exception as exc:
                raise ProviderError(f"could not connect to IBM Quantum: {exc}") from exc
        return self._service

    def _call(self, what: str, fn: Callable[[], T]) -> T:
        try:
            return fn()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"IBM Quantum {what} failed: {exc}") from exc

    def backends(self) -> list[BackendSummary]:
        service = self._get_service()
        return self._call("request", lambda: [summarize(b) for b in service.backends()])

    def statuses(self) -> list[BackendStatus]:
        service = self._get_service()
        return self._call("status request", lambda: [_status(b) for b in service.backends()])

    def calibration(self, backend: str) -> Calibration | None:
        service = self._get_service()

        def fetch() -> Calibration | None:
            properties = service.backend(backend).properties(refresh=True)
            return None if properties is None else parse_calibration(properties.to_dict(), backend)

        return self._call(f"calibration request for {backend}", fetch)


def summarize(backend: Any) -> BackendSummary:
    """Build a summary from an ``IBMBackend`` (or a fake backend in tests)."""
    status = _status(backend)
    properties = backend.properties()
    cal = None if properties is None else parse_calibration(properties.to_dict())

    return BackendSummary(
        name=status.name,
        status=status.status,
        status_message=status.status_message,
        pending_jobs=status.pending_jobs,
        num_qubits=int(backend.num_qubits),
        median_2q_error=None if cal is None else median_2q_error(cal),
        median_readout_error=None if cal is None else median_readout_error(cal),
        calibrated_at=None if cal is None else cal.calibrated_at,
    )


def _status(backend: Any) -> BackendStatus:
    status = backend.status()
    return BackendStatus(
        name=backend.name,
        status=status_from(status.operational, status.status_msg),
        status_message=status.status_msg,
        pending_jobs=int(status.pending_jobs),
    )


def status_from(operational: bool, message: str) -> Status:
    if operational and message.lower() == "active":
        return Status.ONLINE
    if "maint" in message.lower():
        return Status.MAINTENANCE
    return Status.OFFLINE


def parse_calibration(data: dict[str, Any], backend: str | None = None) -> Calibration:
    """Parse IBM backend properties in their JSON form.

    ``backend`` overrides the name inside the record, so a calibration is
    always filed under the name it was requested by.
    """
    sx_errors: dict[int, float] = {}
    by_gate: dict[str, list[EdgeCalibration]] = {}
    for gate in data.get("gates", []):
        qubits = gate.get("qubits", [])
        error = _param(gate.get("parameters", []), "gate_error")
        if error is None:
            continue
        if gate["gate"] == "sx" and len(qubits) == 1:
            sx_errors[qubits[0]] = error
        elif gate["gate"] in NATIVE_2Q_GATES and len(qubits) == 2:
            edge = EdgeCalibration((qubits[0], qubits[1]), gate["gate"], error)
            by_gate.setdefault(gate["gate"], []).append(edge)

    edges: list[EdgeCalibration] = []
    for name in NATIVE_2Q_GATES:
        if by_gate.get(name):
            edges = by_gate[name]
            break

    qubits = tuple(
        QubitCalibration(
            qubit=index,
            t1_us=_time_us(params, "T1"),
            t2_us=_time_us(params, "T2"),
            readout_error=_param(params, "readout_error"),
            sx_error=sx_errors.get(index),
        )
        for index, params in enumerate(data.get("qubits", []))
    )

    return Calibration(
        backend=backend or data["backend_name"],
        calibrated_at=_timestamp(data["last_update_date"]),
        qubits=qubits,
        edges=tuple(edges),
        raw=data,
    )


def median_2q_error(cal: Calibration) -> float | None:
    values = [e.error for e in cal.edges]
    return median(values) if values else None


def median_readout_error(cal: Calibration) -> float | None:
    values = [q.readout_error for q in cal.qubits if q.readout_error is not None]
    return median(values) if values else None


def _find(params: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((p for p in params if p.get("name") == name), None)


def _param(params: list[dict[str, Any]], name: str) -> float | None:
    p = _find(params, name)
    return None if p is None or p.get("value") is None else float(p["value"])


def _time_us(params: list[dict[str, Any]], name: str) -> float | None:
    p = _find(params, name)
    if p is None or p.get("value") is None:
        return None
    factor = _TO_MICROSECONDS.get(p.get("unit") or "us")
    return None if factor is None else float(p["value"]) * factor


def _timestamp(value: datetime | str) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
