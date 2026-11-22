# qutop

A terminal dashboard for quantum hardware, in the spirit of `htop` and `nvtop`.

qutop shows which IBM Quantum backends are up, how long their queues are,
how each qubit is calibrated today, and where your own jobs are. In the
background it keeps a local history of calibration snapshots, so you can see
how a device drifts over days and weeks.

**Status:** early development. Nothing is released yet and the interface will
change.

## Install

Not on PyPI yet. Once released:

    pipx install qutop

## Usage

qutop reads the IBM Quantum account you saved with Qiskit:

    python -c "from qiskit_ibm_runtime import QiskitRuntimeService; QiskitRuntimeService.save_account(token='...', set_as_default=True)"

Then:

    qutop list

Reading backend status and calibration data uses no QPU time.

## Limits

- IBM Quantum only for now.
- IBM recalibrates roughly once a day, so calibration data is at most that fresh.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

qutop is not affiliated with IBM, Qiskit or QuTiP.
