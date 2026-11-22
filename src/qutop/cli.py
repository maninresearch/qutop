"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TextIO

from qutop import __version__, display
from qutop.providers.base import (
    BackendSummary,
    NotConfiguredError,
    Provider,
    ProviderError,
    Status,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2  # argparse uses this for bad arguments
EXIT_NOT_CONFIGURED = 3

_STATUS_ORDER = {Status.ONLINE: 0, Status.MAINTENANCE: 1, Status.OFFLINE: 2}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qutop",
        description="Terminal dashboard for quantum hardware.",
    )
    parser.add_argument("--version", action="version", version=f"qutop {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    list_cmd = commands.add_parser(
        "list",
        help="print backends with status, queue and median errors",
        description=(
            "Print every backend your account can see: status, pending jobs, "
            "qubit count, median two-qubit and readout error, and the age of "
            "the last calibration."
        ),
    )
    list_cmd.add_argument(
        "--json",
        action="store_true",
        help="print JSON instead of a table; error rates are fractions, times are ISO 8601 UTC",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    provider: Provider | None = None,
    now: datetime | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)

    if args.command is None:
        build_parser().print_help(out)
        return EXIT_OK

    if provider is None:
        from qutop.providers.ibm import IBMProvider

        provider = IBMProvider()

    try:
        backends = provider.backends()
    except NotConfiguredError as exc:
        print(f"qutop: {exc}", file=err)
        return EXIT_NOT_CONFIGURED
    except ProviderError as exc:
        print(f"qutop: {exc}", file=err)
        return EXIT_ERROR

    backends.sort(key=lambda b: (_STATUS_ORDER[b.status], b.pending_jobs, b.name))
    if args.json:
        json.dump([_to_json(b) for b in backends], out, indent=2)
        out.write("\n")
    else:
        print(render_list(backends, now or datetime.now(UTC)), file=out)
    return EXIT_OK


def render_list(backends: Sequence[BackendSummary], now: datetime) -> str:
    if not backends:
        return "No backends available to this account."
    headers = ["BACKEND", "STATUS", "QUEUE", "QUBITS", "2Q ERR", "RO ERR", "CAL"]
    rows = [
        [
            b.name,
            "maint." if b.status is Status.MAINTENANCE else b.status.value,
            str(b.pending_jobs) if b.status is Status.ONLINE else "-",
            str(b.num_qubits),
            display.percent(b.median_2q_error),
            display.percent(b.median_readout_error),
            display.age(b.calibrated_at, now),
        ]
        for b in backends
    ]
    return display.table(headers, rows, right={2, 3, 4, 5, 6})


def _to_json(backend: BackendSummary) -> dict[str, Any]:
    data = asdict(backend)
    data["status"] = backend.status.value
    if backend.calibrated_at is not None:
        data["calibrated_at"] = backend.calibrated_at.astimezone(UTC).isoformat()
    return data
