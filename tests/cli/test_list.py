import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from qutop import __version__
from qutop.cli import EXIT_ERROR, EXIT_NOT_CONFIGURED, EXIT_OK, main
from qutop.providers.base import BackendSummary, NotConfiguredError, ProviderError, Status
from stubs import StubProvider

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def summary(name: str, status: Status, queue: int, cal_hours: float) -> BackendSummary:
    return BackendSummary(
        name=name,
        status=status,
        status_message="active" if status is Status.ONLINE else "maintenance",
        pending_jobs=queue,
        num_qubits=156,
        median_2q_error=0.0021,
        median_readout_error=0.011,
        calibrated_at=NOW - timedelta(hours=cal_hours),
    )


def run(argv: list[str], provider: StubProvider) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, provider=provider, now=NOW, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


BACKENDS = [
    summary("ibm_torino", Status.MAINTENANCE, 0, 26),
    summary("ibm_fez", Status.ONLINE, 87, 5),
    summary("ibm_kingston", Status.ONLINE, 12, 3),
]


def test_list_table() -> None:
    code, out, err = run(["list"], StubProvider(BACKENDS))
    assert code == EXIT_OK
    assert err == ""
    assert out.splitlines() == [
        "BACKEND       STATUS  QUEUE  QUBITS  2Q ERR  RO ERR  CAL",
        "ibm_kingston  online     12     156   0.21%   1.10%   3h",
        "ibm_fez       online     87     156   0.21%   1.10%   5h",
        "ibm_torino    maint.      -     156   0.21%   1.10%   1d",
    ]


def test_list_json() -> None:
    code, out, _ = run(["list", "--json"], StubProvider(BACKENDS))
    assert code == EXIT_OK
    data = json.loads(out)
    assert [b["name"] for b in data] == ["ibm_kingston", "ibm_fez", "ibm_torino"]
    assert data[0]["status"] == "online"
    assert data[0]["median_2q_error"] == 0.0021
    assert data[0]["calibrated_at"] == "2026-11-20T09:00:00+00:00"


def test_list_empty() -> None:
    code, out, _ = run(["list"], StubProvider([]))
    assert code == EXIT_OK
    assert out.strip() == "No backends available to this account."


def test_not_configured() -> None:
    code, out, err = run(["list"], StubProvider(NotConfiguredError("no saved IBM Quantum account")))
    assert code == EXIT_NOT_CONFIGURED
    assert out == ""
    assert err == "qutop: no saved IBM Quantum account\n"


def test_provider_error() -> None:
    code, _, err = run(["list"], StubProvider(ProviderError("IBM Quantum request failed: 503")))
    assert code == EXIT_ERROR
    assert "503" in err


def test_no_command_prints_help() -> None:
    code, out, _ = run([], StubProvider([]))
    assert code == EXIT_OK
    assert out.startswith("usage: qutop")


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out == f"qutop {__version__}\n"


def test_bad_command_exits_2() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["nope"])
    assert exc.value.code == 2


def test_account_option(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    saved = tmp_path / ".qiskit" / "qiskit-ibm.json"
    saved.parent.mkdir()
    saved.write_text('{"default": {"channel": "ibm_quantum_platform", "token": "k"}}')
    monkeypatch.setattr("qutop.providers.ibm_account.DEFAULT_ACCOUNT_FILE", saved)
    err = io.StringIO()
    code = main(["--account", "work", "list"], out=io.StringIO(), err=err)
    assert code == EXIT_NOT_CONFIGURED
    assert "no saved IBM Quantum account named 'work'" in err.getvalue()
