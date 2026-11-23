import io
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qutop.cli import EXIT_NOT_CONFIGURED, EXIT_OK, EXIT_USAGE, main
from qutop.providers.base import NotConfiguredError
from stubs import StubProvider, kingston_calibration, online

CAL_TIME = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)


def provider() -> StubProvider:
    cal = kingston_calibration(CAL_TIME)
    return StubProvider(
        statuses=[online("ibm_kingston", 4)], calibrations={"ibm_kingston": lambda: cal}
    )


def run(argv: list[str], stub: StubProvider) -> tuple[int, str]:
    err = io.StringIO()
    code = main(argv, provider=stub, out=io.StringIO(), err=err)
    return code, err.getvalue()


def test_collect_once(tmp_path: Path) -> None:
    db = tmp_path / "h.sqlite3"
    code, err = run(["collect", "--once", "--db", str(db)], provider())
    assert code == EXIT_OK
    assert "status: 1 backends" in err
    assert "calibration: saved ibm_kingston from 2026-11-20 09:00 UTC" in err
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute("SELECT backend, calibrated_at FROM calibration").fetchall()
    assert rows == [("ibm_kingston", "2026-11-20T09:00:00+00:00")]


def test_collect_quiet(tmp_path: Path) -> None:
    code, err = run(["collect", "--once", "-q", "--db", str(tmp_path / "h.sqlite3")], provider())
    assert code == EXIT_OK
    assert err == ""


def test_default_db_follows_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    code, _ = run(["collect", "--once", "-q"], provider())
    assert code == EXIT_OK
    assert (tmp_path / "qutop" / "history.sqlite3").exists()


def test_collect_not_configured(tmp_path: Path) -> None:
    stub = StubProvider()
    stub.status_error = NotConfiguredError("no saved IBM Quantum account")
    code, err = run(["collect", "--once", "--db", str(tmp_path / "h.sqlite3")], stub)
    assert code == EXIT_NOT_CONFIGURED
    assert "no saved IBM Quantum account" in err


def test_collect_rejects_short_interval(tmp_path: Path) -> None:
    argv = ["collect", "--once", "--status-interval", "5", "--db", str(tmp_path / "h.sqlite3")]
    code, err = run(argv, provider())
    assert code == EXIT_USAGE
    assert "at least 30s" in err


def test_collect_bad_db_path(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    code, err = run(["collect", "--once", "--db", str(blocker / "h.sqlite3")], provider())
    assert code == 1
    assert err.startswith("qutop: ")


def test_collect_stops_on_ctrl_c(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("qutop.poller.time.sleep", interrupt)
    code, _ = run(["collect", "-q", "--db", str(tmp_path / "h.sqlite3")], provider())
    assert code == EXIT_OK
