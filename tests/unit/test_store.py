import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from qutop.providers.base import BackendStatus, Status
from qutop.store import SCHEMA_VERSION, Store, StoreError
from stubs import kingston_calibration, online

T0 = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    with Store(tmp_path / "sub" / "history.sqlite3") as s:
        yield s


def test_creates_parent_dir_and_schema(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "h.sqlite3"
    Store(path).close()
    with closing(sqlite3.connect(path)) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION


def test_reopen_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "h.sqlite3"
    with Store(path) as s:
        s.save_calibration("ibm", kingston_calibration(T0), T0)
    with Store(path) as s:
        assert s.calibration_times("ibm", "ibm_kingston") == [T0]


def test_refuses_newer_schema(tmp_path: Path) -> None:
    path = tmp_path / "h.sqlite3"
    Store(path).close()
    db = sqlite3.connect(path)
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    db.close()
    with pytest.raises(StoreError, match="newer qutop"):
        Store(path)


def test_calibration_round_trip(store: Store) -> None:
    cal = kingston_calibration(T0)
    assert store.save_calibration("ibm", cal, T0 + timedelta(minutes=5))
    loaded = store.calibration("ibm", "ibm_kingston", T0)
    assert loaded.qubits == cal.qubits
    assert set(loaded.edges) == set(cal.edges)
    assert loaded.raw["backend_name"] == "ibm_kingston"
    assert len(loaded.raw["qubits"]) == 156


def test_same_calibration_stored_once(store: Store) -> None:
    cal = kingston_calibration(T0)
    assert store.save_calibration("ibm", cal, T0)
    assert not store.save_calibration("ibm", cal, T0 + timedelta(minutes=30))
    assert store.calibration_times("ibm", "ibm_kingston") == [T0]


def test_latest_and_history(store: Store) -> None:
    assert store.latest_calibrated_at("ibm", "ibm_kingston") is None
    for days in (2, 0, 1):
        at = T0 + timedelta(days=days)
        store.save_calibration("ibm", kingston_calibration(at), at)
    assert store.latest_calibrated_at("ibm", "ibm_kingston") == T0 + timedelta(days=2)
    history = store.qubit_history("ibm", "ibm_kingston", 47)
    assert [t for t, _ in history] == [T0 + timedelta(days=d) for d in range(3)]
    assert all(q.qubit == 47 for _, q in history)


def test_providers_are_separate(store: Store) -> None:
    store.save_calibration("ibm", kingston_calibration(T0), T0)
    assert store.latest_calibrated_at("other", "ibm_kingston") is None


def test_missing_calibration(store: Store) -> None:
    with pytest.raises(KeyError):
        store.calibration("ibm", "ibm_kingston", T0)


def test_status_samples(store: Store) -> None:
    store.save_statuses("ibm", [online("ibm_fez", 87), online("ibm_kingston", 12)], T0)
    later = T0 + timedelta(minutes=1)
    store.save_statuses(
        "ibm", [BackendStatus("ibm_fez", Status.MAINTENANCE, "maintenance", 0)], later
    )
    assert store.status_history("ibm", "ibm_fez") == [
        (T0, Status.ONLINE, 87),
        (later, Status.MAINTENANCE, 0),
    ]
    assert store.status_history("ibm", "ibm_fez", since=later) == [(later, Status.MAINTENANCE, 0)]


def test_rejects_naive_times(store: Store) -> None:
    with pytest.raises(ValueError, match="naive"):
        store.save_statuses("ibm", [online("ibm_fez")], datetime(2026, 11, 20))


def test_raw_is_compressed(tmp_path: Path) -> None:
    path = tmp_path / "h.sqlite3"
    with Store(path) as s:
        s.save_calibration("ibm", kingston_calibration(T0), T0)
    with closing(sqlite3.connect(path)) as db:
        (size,) = db.execute("SELECT length(raw) FROM calibration").fetchone()
    assert size < 100_000  # about 650 kB as plain JSON
