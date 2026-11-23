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

qutop has no dependencies beyond Python 3.11 or newer. It talks to the IBM
Quantum API directly; Qiskit is not needed to run it.

## Usage

qutop uses the IBM Quantum account saved by Qiskit in
`~/.qiskit/qiskit-ibm.json`, picking the same default account Qiskit would
(`--account NAME` picks another). If you have never saved one:

    python -c "from qiskit_ibm_runtime import QiskitRuntimeService; QiskitRuntimeService.save_account(token='...', instance='...', set_as_default=True)"

or set `QISKIT_IBM_TOKEN` (your IBM Cloud API key) and `QISKIT_IBM_INSTANCE`
(the instance CRN or name). qutop only reads these; it never stores or prints
credentials.

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
