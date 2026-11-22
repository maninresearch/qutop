from datetime import UTC, datetime, timedelta

import pytest

from qutop import display

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "-"), (0.0, "0.00%"), (0.00214, "0.21%"), (0.011, "1.10%"), (1.0, "100.00%")],
)
def test_percent(value: float | None, expected: str) -> None:
    assert display.percent(value) == expected


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=0), "0s"),
        (timedelta(seconds=59), "59s"),
        (timedelta(minutes=12, seconds=30), "12m"),
        (timedelta(hours=3, minutes=59), "3h"),
        (timedelta(days=2, hours=5), "2d"),
        (timedelta(seconds=-30), "0s"),  # clock skew
    ],
)
def test_age(delta: timedelta, expected: str) -> None:
    assert display.age(NOW - delta, NOW) == expected


def test_age_unknown() -> None:
    assert display.age(None, NOW) == "-"


def test_table_aligns_columns() -> None:
    out = display.table(["NAME", "N"], [["a", "1"], ["longer", "123"]], right={1})
    assert out.splitlines() == [
        "NAME      N",
        "a         1",
        "longer  123",
    ]
