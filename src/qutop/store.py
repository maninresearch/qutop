"""Local history in SQLite: calibration snapshots and status samples.

No network or UI imports. All timestamps are stored as ISO 8601 text in UTC,
so they sort correctly as strings.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from qutop.providers.base import (
    BackendStatus,
    Calibration,
    EdgeCalibration,
    QubitCalibration,
    Status,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

# Each entry upgrades the schema by one version. Never edit a released entry;
# append a new one instead.
_MIGRATIONS = [
    """
    CREATE TABLE calibration (
        id INTEGER PRIMARY KEY,
        provider TEXT NOT NULL,
        backend TEXT NOT NULL,
        calibrated_at TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        raw BLOB NOT NULL,
        UNIQUE (provider, backend, calibrated_at)
    );
    CREATE TABLE qubit_calibration (
        calibration_id INTEGER NOT NULL REFERENCES calibration (id) ON DELETE CASCADE,
        qubit INTEGER NOT NULL,
        t1_us REAL,
        t2_us REAL,
        readout_error REAL,
        sx_error REAL,
        PRIMARY KEY (calibration_id, qubit)
    ) WITHOUT ROWID;
    CREATE TABLE edge_calibration (
        calibration_id INTEGER NOT NULL REFERENCES calibration (id) ON DELETE CASCADE,
        qubit_a INTEGER NOT NULL,
        qubit_b INTEGER NOT NULL,
        gate TEXT NOT NULL,
        error REAL NOT NULL,
        PRIMARY KEY (calibration_id, qubit_a, qubit_b, gate)
    ) WITHOUT ROWID;
    CREATE TABLE status_sample (
        provider TEXT NOT NULL,
        backend TEXT NOT NULL,
        sampled_at TEXT NOT NULL,
        status TEXT NOT NULL,
        pending_jobs INTEGER NOT NULL,
        PRIMARY KEY (provider, backend, sampled_at)
    ) WITHOUT ROWID;
    """,
]

SCHEMA_VERSION = len(_MIGRATIONS)


class StoreError(Exception):
    pass


class Store:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=10)
        self._db.execute("PRAGMA foreign_keys = ON")
        # WAL lets the dashboard read while the collector writes.
        self._db.execute("PRAGMA journal_mode = WAL")
        try:
            self._migrate()
        except BaseException:
            self._db.close()
            raise

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _migrate(self) -> None:
        (version,) = self._db.execute("PRAGMA user_version").fetchone()
        if version > SCHEMA_VERSION:
            raise StoreError(
                f"history database is from a newer qutop (schema {version}, "
                f"this version understands {SCHEMA_VERSION}); upgrade qutop"
            )
        for number in range(version, SCHEMA_VERSION):
            with self._db:
                self._db.executescript(f"BEGIN; {_MIGRATIONS[number]}")
                self._db.execute(f"PRAGMA user_version = {number + 1}")

    # Writing

    def save_calibration(self, provider: str, cal: Calibration, fetched_at: datetime) -> bool:
        """Store a calibration. Returns False if this exact one is already stored."""
        raw = zlib.compress(json.dumps(cal.raw, default=_json_default).encode(), 6)
        with self._db:
            cur = self._db.execute(
                "INSERT INTO calibration (provider, backend, calibrated_at, fetched_at, raw)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (provider, cal.backend, _ts(cal.calibrated_at), _ts(fetched_at), raw),
            )
            if cur.rowcount == 0:
                return False
            cal_id = cur.lastrowid
            self._db.executemany(
                "INSERT INTO qubit_calibration VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (cal_id, q.qubit, q.t1_us, q.t2_us, q.readout_error, q.sx_error)
                    for q in cal.qubits
                ],
            )
            self._db.executemany(
                "INSERT OR IGNORE INTO edge_calibration VALUES (?, ?, ?, ?, ?)",
                [(cal_id, e.qubits[0], e.qubits[1], e.gate, e.error) for e in cal.edges],
            )
        return True

    def save_statuses(
        self, provider: str, statuses: Iterable[BackendStatus], sampled_at: datetime
    ) -> None:
        with self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO status_sample VALUES (?, ?, ?, ?, ?)",
                [
                    (provider, s.name, _ts(sampled_at), s.status.value, s.pending_jobs)
                    for s in statuses
                ],
            )

    # Reading

    def latest_calibrated_at(self, provider: str, backend: str) -> datetime | None:
        row = self._db.execute(
            "SELECT max(calibrated_at) FROM calibration WHERE provider = ? AND backend = ?",
            (provider, backend),
        ).fetchone()
        return None if row[0] is None else _parse_ts(row[0])

    def calibration_times(self, provider: str, backend: str) -> list[datetime]:
        rows = self._db.execute(
            "SELECT calibrated_at FROM calibration WHERE provider = ? AND backend = ?"
            " ORDER BY calibrated_at",
            (provider, backend),
        )
        return [_parse_ts(r[0]) for r in rows]

    def calibration(self, provider: str, backend: str, calibrated_at: datetime) -> Calibration:
        row = self._db.execute(
            "SELECT id, raw FROM calibration"
            " WHERE provider = ? AND backend = ? AND calibrated_at = ?",
            (provider, backend, _ts(calibrated_at)),
        ).fetchone()
        if row is None:
            raise KeyError((provider, backend, calibrated_at))
        cal_id, raw = row
        qubits = tuple(
            QubitCalibration(*r)
            for r in self._db.execute(
                "SELECT qubit, t1_us, t2_us, readout_error, sx_error FROM qubit_calibration"
                " WHERE calibration_id = ? ORDER BY qubit",
                (cal_id,),
            )
        )
        edges = tuple(
            EdgeCalibration((a, b), gate, error)
            for a, b, gate, error in self._db.execute(
                "SELECT qubit_a, qubit_b, gate, error FROM edge_calibration"
                " WHERE calibration_id = ? ORDER BY qubit_a, qubit_b",
                (cal_id,),
            )
        )
        return Calibration(
            backend=backend,
            calibrated_at=calibrated_at.astimezone(UTC),
            qubits=qubits,
            edges=edges,
            raw=json.loads(zlib.decompress(raw)),
        )

    def qubit_history(
        self, provider: str, backend: str, qubit: int
    ) -> list[tuple[datetime, QubitCalibration]]:
        rows = self._db.execute(
            "SELECT c.calibrated_at, q.qubit, q.t1_us, q.t2_us, q.readout_error, q.sx_error"
            " FROM qubit_calibration q JOIN calibration c ON c.id = q.calibration_id"
            " WHERE c.provider = ? AND c.backend = ? AND q.qubit = ?"
            " ORDER BY c.calibrated_at",
            (provider, backend, qubit),
        )
        return [(_parse_ts(r[0]), QubitCalibration(*r[1:])) for r in rows]

    def status_history(
        self, provider: str, backend: str, since: datetime | None = None
    ) -> list[tuple[datetime, Status, int]]:
        rows = self._db.execute(
            "SELECT sampled_at, status, pending_jobs FROM status_sample"
            " WHERE provider = ? AND backend = ? AND sampled_at >= ? ORDER BY sampled_at",
            (provider, backend, "" if since is None else _ts(since)),
        )
        return [(_parse_ts(t), Status(s), n) for t, s, n in rows]


def _ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime; qutop stores only timezone-aware times")
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _parse_ts(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot store {type(value).__name__} in calibration record")
