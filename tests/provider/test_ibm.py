from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from qiskit_ibm_runtime.fake_provider import FakeFez, FakeKingston, FakeTorino
from qiskit_ibm_runtime.models import BackendStatus

from qutop.providers.base import NotConfiguredError, ProviderError, Status
from qutop.providers.ibm import IBMProvider, median_2q_error, status_from, summarize


@pytest.mark.parametrize("backend_cls", [FakeKingston, FakeFez, FakeTorino])
def test_summarize_fake_backends(backend_cls: Any) -> None:
    backend = backend_cls()
    s = summarize(backend)
    assert s.name == backend.name
    assert s.num_qubits == backend.num_qubits
    assert s.status is Status.ONLINE
    assert s.median_2q_error is not None
    assert 0 < s.median_2q_error < 0.05
    assert s.median_readout_error is not None
    assert 0 < s.median_readout_error < 0.1
    assert isinstance(s.calibrated_at, datetime)


def test_2q_median_ignores_fractional_gates() -> None:
    def gate(name: str, error: float) -> SimpleNamespace:
        param = SimpleNamespace(name="gate_error", value=error)
        return SimpleNamespace(gate=name, qubits=[0, 1], parameters=[param])

    gates = [gate("cz", 0.002), gate("cz", 0.004), gate("rzz", 0.5), gate("rzz", 0.5)]
    assert median_2q_error(gates) == pytest.approx(0.003)


def test_2q_median_without_native_gate() -> None:
    assert median_2q_error([]) is None


@pytest.mark.parametrize(
    ("operational", "message", "expected"),
    [
        (True, "active", Status.ONLINE),
        (False, "maintenance", Status.MAINTENANCE),
        (True, "maintenance", Status.MAINTENANCE),
        (False, "internal", Status.OFFLINE),
        (True, "internal", Status.OFFLINE),
    ],
)
def test_status_from(operational: bool, message: str, expected: Status) -> None:
    assert status_from(operational, message) is expected


def test_summarize_without_properties() -> None:
    backend = SimpleNamespace(
        name="ibm_test",
        num_qubits=5,
        status=lambda: BackendStatus("ibm_test", "1", False, 0, "maintenance"),
        properties=lambda: None,
    )
    s = summarize(backend)
    assert s.status is Status.MAINTENANCE
    assert s.median_2q_error is None
    assert s.median_readout_error is None
    assert s.calibrated_at is None


def test_provider_uses_service_backends() -> None:
    service = SimpleNamespace(backends=lambda: [FakeKingston()])
    [only] = IBMProvider(service).backends()
    assert only.name == "fake_kingston"


def test_provider_wraps_request_errors() -> None:
    def fail() -> list[Any]:
        raise ConnectionError("timed out")

    with pytest.raises(ProviderError, match="timed out"):
        IBMProvider(SimpleNamespace(backends=fail)).backends()


def test_missing_account(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("QISKIT_IBM_TOKEN", "QISKIT_IBM_CHANNEL", "QISKIT_IBM_INSTANCE", "QISKIT_IBM_URL"):
        monkeypatch.delenv(var, raising=False)
    import qiskit_ibm_runtime.accounts.management as management

    monkeypatch.setattr(
        management, "_DEFAULT_ACCOUNT_CONFIG_JSON_FILE", str(tmp_path / "qiskit-ibm.json")
    )
    with pytest.raises(NotConfiguredError):
        IBMProvider().backends()
