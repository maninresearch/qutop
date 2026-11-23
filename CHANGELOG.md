# Changelog

All notable changes to qutop are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

## [0.0.1] - 2026-11-23

First preview release.

### Added

- `qutop list` prints every IBM Quantum backend your account can see: status,
  pending jobs, qubit count, median two-qubit and readout error, and
  calibration age. `--json` prints the same data as JSON.
- `qutop collect` records backend status and every new calibration to a
  local SQLite database, so drift can be studied later. Each calibration is
  kept in full alongside per-qubit T1, T2, readout and `sx` error and
  per-pair two-qubit gate error. `--once` polls a single time and exits.

[Unreleased]: https://github.com/maninresearch/qutop/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/maninresearch/qutop/releases/tag/v0.0.1
