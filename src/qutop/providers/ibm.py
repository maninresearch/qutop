"""IBM Quantum, through its REST API, using only the standard library.

Endpoints and headers follow IBM's Qiskit Runtime REST API as used by the
official ``qiskit-ibm-runtime`` client. Nothing here submits jobs or uses
QPU time: qutop only reads the backend list and backend properties.

Calibration is parsed from the JSON form of IBM's backend properties, the
shape ``GET /backends/{name}/properties`` returns.
"""

from __future__ import annotations

from datetime import UTC, datetime
from statistics import median
from typing import Any
from urllib.parse import quote

from qutop.providers.base import (
    BackendStatus,
    BackendSummary,
    Calibration,
    EdgeCalibration,
    ProviderError,
    QubitCalibration,
    Status,
)
from qutop.providers.http import Http, HttpError
from qutop.providers.ibm_account import (
    Account,
    IAMToken,
    api_base,
    load_account,
    resolve_instances,
)

# Pins the API behaviour qutop was written against.
API_VERSION = "2026-04-15"

# Native two-qubit gates, in order of preference. Backends can also report
# other two-qubit gates (for example ``rzz`` with fractional gates enabled);
# those are not what most circuits are compiled to, so they are left out.
NATIVE_2Q_GATES = ("cz", "ecr", "cx")

_TO_MICROSECONDS = {"s": 1e6, "ms": 1e3, "us": 1.0, "µs": 1.0, "ns": 1e-3}


class IBMProvider:
    key = "ibm"
    name = "IBM Quantum"

    def __init__(
        self,
        account: Account | None = None,
        http: Http | None = None,
        account_name: str | None = None,
    ) -> None:
        self._account = account
        self._account_name = account_name
        self._http = http or Http()
        self._token: IAMToken | None = None
        self._instances: list[str] | None = None
        self._backend_instance: dict[str, str] = {}
        self._num_qubits: dict[str, int] = {}

    def _auth(self) -> tuple[Account, IAMToken]:
        if self._account is None:
            self._account = load_account(name=self._account_name)
        if self._token is None:
            self._token = IAMToken(self._account, self._http)
        return self._account, self._token

    def _get(self, crn: str, path: str) -> Any:
        account, token = self._auth()
        url = f"{api_base(account, crn)}{path}"
        for attempt in range(2):
            headers = {
                "Authorization": f"Bearer {token.get()}",
                "Service-CRN": crn,
                "IBM-API-Version": API_VERSION,
            }
            try:
                return self._http.request_json("GET", url, headers=headers)
            except HttpError as exc:
                if exc.status == 401 and attempt == 0:  # token revoked or expired early
                    token.invalidate()
                    continue
                raise
        raise AssertionError("unreachable")  # pragma: no cover

    def _crns(self) -> list[str]:
        if self._instances is None:
            account, token = self._auth()
            self._instances = resolve_instances(account, self._http, token)
        return self._instances

    def statuses(self) -> list[BackendStatus]:
        seen: dict[str, BackendStatus] = {}
        for crn in self._crns():
            data = self._get(crn, "/backends")
            for item in _devices(data):
                status = parse_backend_item(item)
                if status.name in seen:
                    continue
                seen[status.name] = status
                self._backend_instance[status.name] = crn
                if isinstance(item.get("qubits"), int):
                    self._num_qubits[status.name] = item["qubits"]
        return list(seen.values())

    def calibration(self, backend: str) -> Calibration | None:
        if backend not in self._backend_instance:
            self.statuses()
        crn = self._backend_instance.get(backend)
        if crn is None:
            raise ProviderError(f"backend {backend!r} is not available to this account")
        data = self._get(crn, f"/backends/{quote(backend, safe='')}/properties")
        if not isinstance(data, dict) or not data.get("last_update_date"):
            return None  # simulators and some new devices report no calibration
        data["backend_name"] = backend
        return parse_calibration(data, backend)

    def backends(self) -> list[BackendSummary]:
        summaries = []
        for status in self.statuses():
            cal = self.calibration(status.name)
            summaries.append(summarize(status, self._num_qubits.get(status.name), cal))
        return summaries


def _devices(data: Any) -> list[dict[str, Any]]:
    devices = data.get("devices", []) if isinstance(data, dict) else data
    if not isinstance(devices, list):
        raise ProviderError("IBM Quantum backend list has an unexpected format")
    return [d for d in devices if isinstance(d, dict) and d.get("name")]


def parse_backend_item(item: dict[str, Any]) -> BackendStatus:
    """One entry of ``GET /backends``: name, status, queue length."""
    raw = item.get("status")
    if isinstance(raw, dict):
        state = str(raw.get("name", ""))
        reason = str(raw.get("reason") or "")
    else:
        state, reason = str(raw or ""), ""
    queue = item.get("queue_length")
    return BackendStatus(
        name=str(item["name"]),
        status=status_from(state, reason),
        status_message=reason or state,
        pending_jobs=max(int(queue), 0) if isinstance(queue, int) else 0,
    )


def status_from(state: str, reason: str = "") -> Status:
    text = f"{state} {reason}".lower()
    if "maint" in text:
        return Status.MAINTENANCE
    if state.lower() in ("online", "active"):
        return Status.ONLINE
    return Status.OFFLINE


def summarize(
    status: BackendStatus, num_qubits: int | None, cal: Calibration | None
) -> BackendSummary:
    if num_qubits is None:
        num_qubits = len(cal.qubits) if cal is not None else 0
    return BackendSummary(
        name=status.name,
        status=status.status,
        status_message=status.status_message,
        pending_jobs=status.pending_jobs,
        num_qubits=num_qubits,
        median_2q_error=None if cal is None else median_2q_error(cal),
        median_readout_error=None if cal is None else median_readout_error(cal),
        calibrated_at=None if cal is None else cal.calibrated_at,
    )


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
