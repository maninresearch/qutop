"""Text formatting shared by the CLI and, later, the TUI."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def percent(value: float | None) -> str:
    """0.00214 -> '0.21%'. Unknown values print as '-'."""
    if value is None:
        return "-"
    return f"{value * 100:.2f}%"


def age(then: datetime | None, now: datetime) -> str:
    """Compact age: '45s', '12m', '3h', '2d'."""
    if then is None:
        return "-"
    seconds = int((now - then).total_seconds())
    if seconds < 0:
        return "0s"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def table(headers: Sequence[str], rows: Sequence[Sequence[str]], right: set[int]) -> str:
    """Plain aligned columns. Indexes in ``right`` are right-aligned."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def line(cells: Sequence[str]) -> str:
        parts = [
            cell.rjust(widths[i]) if i in right else cell.ljust(widths[i])
            for i, cell in enumerate(cells)
        ]
        return "  ".join(parts).rstrip()

    return "\n".join([line(headers), *(line(row) for row in rows)])
