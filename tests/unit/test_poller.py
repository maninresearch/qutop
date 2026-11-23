from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from qutop.poller import Collector
from qutop.providers.base import Calibration, NotConfiguredError, ProviderError
from qutop.store import Store
from stubs import FakeClock, StubProvider, kingston_calibration, online

CAL_TIME = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    with Store(tmp_path / "h.sqlite3") as s:
        yield s


def make(provider: StubProvider, store: Store, clock: FakeClock) -> Collector:
    return Collector(provider, store, now=clock.now, sleep=clock.sleep)


def kingston_provider() -> StubProvider:
    cal = kingston_calibration(CAL_TIME)
    return StubProvider(
        statuses=[online("ibm_kingston", 3)], calibrations={"ibm_kingston": lambda: cal}
    )


def test_first_tick_polls_everything(store: Store) -> None:
    clock = FakeClock()
    provider = kingston_provider()
    wait = make(provider, store, clock).tick()
    assert provider.status_calls == 1
    assert provider.calibration_calls == ["ibm_kingston"]
    assert store.calibration_times("stub", "ibm_kingston") == [CAL_TIME]
    assert store.status_history("stub", "ibm_kingston")[0][2] == 3
    assert wait == 60


def test_schedule_over_an_hour(store: Store) -> None:
    clock = FakeClock()
    provider = kingston_provider()
    collector = make(provider, store, clock)
    end = clock.time + timedelta(hours=1)
    collector.run(should_stop=lambda: clock.time >= end)
    assert provider.status_calls == 60
    assert len(provider.calibration_calls) == 2  # at 0 and 30 minutes
    # unchanged calibration is not stored twice
    assert store.calibration_times("stub", "ibm_kingston") == [CAL_TIME]


def test_new_calibration_is_stored(store: Store) -> None:
    clock = FakeClock()
    times = iter([CAL_TIME, CAL_TIME + timedelta(days=1)])

    def fetch() -> Calibration:
        return kingston_calibration(next(times))

    provider = StubProvider(statuses=[online("ibm_kingston")], calibrations={"ibm_kingston": fetch})
    collector = Collector(
        provider, store, calibration_interval=600, now=clock.now, sleep=clock.sleep
    )
    collector.tick()
    clock.sleep(600)
    collector.tick()
    assert len(store.calibration_times("stub", "ibm_kingston")) == 2


def test_status_failure_backs_off_and_recovers(store: Store) -> None:
    clock = FakeClock()
    provider = kingston_provider()
    collector = make(provider, store, clock)
    collector.tick()

    provider.status_error = ProviderError("503")
    clock.sleep(60)
    collector.tick()
    status_job = collector._jobs[0]
    assert status_job.failures == 1
    assert status_job.due == clock.time + timedelta(seconds=120)

    clock.sleep(120)
    collector.tick()
    assert status_job.failures == 2
    assert status_job.due == clock.time + timedelta(seconds=240)

    provider.status_error = None
    clock.sleep(240)
    collector.tick()
    assert status_job.failures == 0
    assert status_job.due == clock.time + timedelta(seconds=60)


def test_backoff_is_capped(store: Store) -> None:
    clock = FakeClock()
    provider = kingston_provider()
    provider.status_error = ProviderError("down")
    collector = make(provider, store, clock)
    for _ in range(12):
        clock.sleep(collector.tick())
    assert max(clock.sleeps) <= 3600


def test_one_failing_backend_does_not_stop_others(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> Calibration:
        raise ProviderError("ibm_broken: 500")

    cal = kingston_calibration(CAL_TIME)
    provider = StubProvider(
        statuses=[online("ibm_kingston"), online("ibm_broken")],
        calibrations={"ibm_kingston": lambda: cal, "ibm_broken": broken},
    )
    collector = make(provider, store, FakeClock())
    collector.tick()
    assert store.calibration_times("stub", "ibm_kingston") == [CAL_TIME]
    assert collector._jobs[1].failures == 0
    assert "ibm_broken: 500" in caplog.text


def test_all_backends_failing_counts_as_failure(store: Store) -> None:
    def broken() -> Calibration:
        raise ProviderError("500")

    provider = StubProvider(statuses=[online("a")], calibrations={"a": broken})
    collector = make(provider, store, FakeClock())
    collector.tick()
    assert collector._jobs[1].failures == 1


def test_missing_account_stops(store: Store) -> None:
    provider = StubProvider()
    provider.status_error = NotConfiguredError("no account")
    with pytest.raises(NotConfiguredError):
        make(provider, store, FakeClock()).tick()


@pytest.mark.parametrize(("status", "calibration"), [(10, 1800), (60, 60)])
def test_refuses_aggressive_polling(store: Store, status: float, calibration: float) -> None:
    with pytest.raises(ValueError, match="at least"):
        Collector(StubProvider(), store, status_interval=status, calibration_interval=calibration)
