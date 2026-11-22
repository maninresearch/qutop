"""IBM Quantum, through the official ``qiskit-ibm-runtime`` client.

Credentials come from the account saved with
``QiskitRuntimeService.save_account()``; qutop never reads or stores tokens
itself. Nothing here submits jobs or uses QPU time.
"""

from __future__ import annotations

from statistics import median
from typing import TYPE_CHECKING, Any

from qutop.providers.base import BackendSummary, NotConfiguredError, ProviderError, Status

if TYPE_CHECKING:
    from collections.abc import Iterable

# Native two-qubit gates, in order of preference. Backends can also report
# other two-qubit gates (for example ``rzz`` with fractional gates enabled);
# those are not what most circuits are compiled to, so they are left out of
# the median.
NATIVE_2Q_GATES = ("cz", "ecr", "cx")


class IBMProvider:
    name = "IBM Quantum"

    def __init__(self, service: Any | None = None) -> None:
        self._service = service

    def _get_service(self) -> Any:
        if self._service is None:
            # Imported here: qiskit takes a second or two to import, and
            # commands that never touch IBM should not pay for it.
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

    def backends(self) -> list[BackendSummary]:
        service = self._get_service()
        try:
            return [summarize(backend) for backend in service.backends()]
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"IBM Quantum request failed: {exc}") from exc


def summarize(backend: Any) -> BackendSummary:
    """Build a summary from an ``IBMBackend`` (or a fake backend in tests)."""
    status = backend.status()
    properties = backend.properties()

    if properties is None:
        two_q = readout = None
        calibrated_at = None
    else:
        two_q = median_2q_error(properties.gates)
        readout = _median_or_none(_qubit_values(properties.qubits, "readout_error"))
        calibrated_at = properties.last_update_date

    return BackendSummary(
        name=backend.name,
        status=status_from(status.operational, status.status_msg),
        status_message=status.status_msg,
        pending_jobs=int(status.pending_jobs),
        num_qubits=int(backend.num_qubits),
        median_2q_error=two_q,
        median_readout_error=readout,
        calibrated_at=calibrated_at,
    )


def status_from(operational: bool, message: str) -> Status:
    if operational and message.lower() == "active":
        return Status.ONLINE
    if "maint" in message.lower():
        return Status.MAINTENANCE
    return Status.OFFLINE


def median_2q_error(gates: Iterable[Any]) -> float | None:
    by_name: dict[str, list[float]] = {}
    for gate in gates:
        if len(gate.qubits) != 2:
            continue
        for param in gate.parameters:
            if param.name == "gate_error":
                by_name.setdefault(gate.gate, []).append(float(param.value))
    for name in NATIVE_2Q_GATES:
        if by_name.get(name):
            return median(by_name[name])
    return None


def _qubit_values(qubits: Iterable[Iterable[Any]], name: str) -> list[float]:
    return [float(p.value) for qubit in qubits for p in qubit if p.name == name]


def _median_or_none(values: list[float]) -> float | None:
    return median(values) if values else None
