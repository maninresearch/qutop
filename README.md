# qutop

A terminal dashboard for quantum hardware, in the spirit of `htop` and `nvtop`.

qutop shows which IBM Quantum backends are up, how long their queues are,
how each qubit is calibrated today, and where your own jobs are. In the
background it keeps a local history of calibration snapshots, so you can see
how a device drifts over days and weeks.

**Status:** early preview. `qutop list` and `qutop collect` work; the live
dashboard is not built yet, and commands and options may still change.

## Install

    pipx install qutop

Version 0.0.x still depends on `qiskit-ibm-runtime`, which makes the install
large (about 250 MB). Later versions talk to the IBM Quantum API directly and
have no dependencies.

## Usage

qutop reads the IBM Quantum account you saved with Qiskit:

    python -c "from qiskit_ibm_runtime import QiskitRuntimeService; QiskitRuntimeService.save_account(token='...', set_as_default=True)"

Then:

    qutop list        # backends, queues, median errors
    qutop collect     # record history until stopped

`qutop collect` checks status every minute and calibration every 30 minutes,
and stores a calibration only when IBM publishes a new one. History goes to
`~/.local/share/qutop/history.sqlite3` (or `$XDG_DATA_HOME/qutop/`). Use
`qutop collect --once` from cron or a systemd timer instead of leaving it
running.

Reading backend status and calibration data uses no QPU time.

## Limits

- IBM Quantum only for now.
- IBM recalibrates roughly once a day, so calibration data is at most that fresh.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

qutop is not affiliated with IBM, Qiskit or QuTiP.
