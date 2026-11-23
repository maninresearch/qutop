from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from qiskit_ibm_runtime.fake_provider import FakeFez, FakeKingston, FakeTorino
from qiskit_ibm_runtime.models import BackendStatus

from qutop.providers.base import NotConfiguredError, ProviderError, Status
from qutop.providers.ibm import (
    IBMProvider,
    median_2q_error,
    parse_calibration,
    status_from,
    summarize,
)
from stubs import kingston_properties


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
    assert s.calibrated_at is not None
    assert s.calibrated_at.tzinfo is UTC


def test_parse_kingston_calibration() -> None:
    cal = parse_calibration(kingston_properties())
    assert cal.backend == "ibm_kingston"
    assert cal.calibrated_at == datetime(2026, 4, 15, 7, 15, 15, tzinfo=UTC)
    assert len(cal.qubits) == 156
    q0 = cal.qubits[0]
    assert q0.qubit == 0
    assert q0.t1_us == pytest.approx(384.16052477415343)
    assert q0.t2_us == pytest.approx(353.8457928146477)
    assert q0.readout_error == pytest.approx(0.009521484375)
    assert q0.sx_error is not None
    assert cal.edges
    assert {e.gate for e in cal.edges} == {"cz"}  # rzz is reported too, but not native


def gate(name: str, qubits: list[int], error: float) -> dict[str, Any]:
    return {
        "gate": name,
        "qubits": qubits,
        "parameters": [{"name": "gate_error", "value": error, "unit": ""}],
    }


def props(gates: list[dict[str, Any]], qubits: list[list[dict[str, Any]]] | None = None) -> Any:
    return {
        "backend_name": "ibm_test",
        "last_update_date": "2026-11-20T09:00:00Z",
        "qubits": qubits or [],
        "gates": gates,
    }


def test_2q_median_ignores_fractional_gates() -> None:
    gates = [
        gate("cz", [0, 1], 0.002),
        gate("cz", [1, 2], 0.004),
        gate("rzz", [0, 1], 0.5),
        gate("rzz", [1, 2], 0.5),
    ]
    assert median_2q_error(parse_calibration(props(gates))) == pytest.approx(0.003)


def test_2q_falls_back_to_ecr() -> None:
    cal = parse_calibration(props([gate("ecr", [0, 1], 0.007)]))
    assert [e.gate for e in cal.edges] == ["ecr"]


def test_2q_median_without_native_gate() -> None:
    assert median_2q_error(parse_calibration(props([]))) is None


def test_time_units_and_missing_values() -> None:
    qubits = [
        [
            {"name": "T1", "value": 0.2, "unit": "ms"},
            {"name": "T2", "value": 95000, "unit": "ns"},
        ],
        [{"name": "T1", "value": 1, "unit": "fortnights"}],
    ]
    cal = parse_calibration(props([], qubits))
    assert cal.qubits[0].t1_us == pytest.approx(200)
    assert cal.qubits[0].t2_us == pytest.approx(95)
    assert cal.qubits[0].readout_error is None
    assert cal.qubits[1].t1_us is None  # unknown unit: refuse to guess


def test_timestamp_is_utc() -> None:
    data = props([])
    data["last_update_date"] = "2026-11-20T11:00:00+02:00"
    assert parse_calibration(data).calibrated_at == datetime(2026, 11, 20, 9, tzinfo=UTC)


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


def fake_service(*backends: Any) -> Any:
    by_name = {b.name: b for b in backends}
    return SimpleNamespace(backends=lambda: list(backends), backend=by_name.__getitem__)


def test_provider_backends_and_statuses() -> None:
    provider = IBMProvider(fake_service(FakeKingston()))
    [summary] = provider.backends()
    assert summary.name == "fake_kingston"
    [status] = provider.statuses()
    assert status.name == "fake_kingston"
    assert status.status is Status.ONLINE


def test_provider_calibration() -> None:
    backend = FakeKingston()
    props_with_refresh = SimpleNamespace(
        name=backend.name, properties=lambda refresh=False: backend.properties()
    )
    cal = IBMProvider(fake_service(props_with_refresh)).calibration("fake_kingston")
    assert cal is not None
    assert cal.backend == "fake_kingston"  # the name it was asked for
    assert len(cal.qubits) == 156


def test_provider_calibration_none() -> None:
    backend = SimpleNamespace(name="ibm_sim", properties=lambda refresh=False: None)
    assert IBMProvider(fake_service(backend)).calibration("ibm_sim") is None


def test_provider_wraps_request_errors() -> None:
    def fail() -> list[Any]:
        raise ConnectionError("timed out")

    provider = IBMProvider(SimpleNamespace(backends=fail))
    with pytest.raises(ProviderError, match="timed out"):
        provider.backends()
    with pytest.raises(ProviderError, match="status request failed"):
        provider.statuses()


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
