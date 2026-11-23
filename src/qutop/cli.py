"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from qutop import __version__, display, paths
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

    collect_cmd = commands.add_parser(
        "collect",
        help="record status and calibration history until stopped",
        description=(
            "Poll status and queue length of every backend, and save each new "
            "calibration to the local history database. Runs until interrupted. "
            "A calibration is stored only when the provider reports a new "
            "calibration time, so leaving this running costs little disk."
        ),
    )
    collect_cmd.add_argument(
        "--once",
        action="store_true",
        help="poll everything once and exit (for cron or systemd timers)",
    )
    collect_cmd.add_argument(
        "--db",
        type=_path,
        metavar="PATH",
        help=f"history database (default: {_display_path(paths.default_db_path())})",
    )
    collect_cmd.add_argument(
        "--status-interval",
        type=float,
        default=60.0,
        metavar="SECONDS",
        help="seconds between status polls (default: 60, minimum: 30)",
    )
    collect_cmd.add_argument(
        "--calibration-interval",
        type=float,
        default=1800.0,
        metavar="SECONDS",
        help="seconds between calibration checks (default: 1800, minimum: 600)",
    )
    collect_cmd.add_argument(
        "-q", "--quiet", action="store_true", help="only print warnings and errors"
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

    if args.command == "collect":
        return _collect(args, provider, err)

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


def _collect(args: argparse.Namespace, provider: Provider, err: TextIO) -> int:
    from qutop.poller import Collector
    from qutop.store import Store, StoreError

    handler = logging.StreamHandler(err)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger = logging.getLogger("qutop")
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING if args.quiet else logging.INFO)

    try:
        store = Store(args.db or paths.default_db_path())
    except (StoreError, OSError) as exc:
        print(f"qutop: {exc}", file=err)
        return EXIT_ERROR

    try:
        collector = Collector(
            provider,
            store,
            status_interval=args.status_interval,
            calibration_interval=args.calibration_interval,
        )
        if args.once:
            collector.tick()
        else:
            collector.run()
    except ValueError as exc:
        print(f"qutop: {exc}", file=err)
        return EXIT_USAGE
    except NotConfiguredError as exc:
        print(f"qutop: {exc}", file=err)
        return EXIT_NOT_CONFIGURED
    except KeyboardInterrupt:
        pass
    finally:
        store.close()
        logger.removeHandler(handler)
    return EXIT_OK


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _display_path(path: Path) -> str:
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home) :] if text.startswith(home) else text


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
