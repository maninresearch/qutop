# Contributing

## Setup

qutop uses [uv](https://docs.astral.sh/uv/).

    git clone https://github.com/maninresearch/qutop
    cd qutop
    uv sync

## Checks

All of these run in CI and must pass before a change is merged:

    uv run ruff format --check
    uv run ruff check
    uv run mypy
    uv run pytest

Tests run offline and need no IBM Quantum account. Provider tests use the
backend snapshots that ship with `qiskit_ibm_runtime.fake_provider`.

## Changes

- One topic per pull request. Bug fixes come with a test that fails without
  the fix.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat(ui): ...`, `fix(ibm): ...`, `test: ...`, `docs: ...`, `ci: ...`.
- User-visible changes get a line under `[Unreleased]` in `CHANGELOG.md`,
  written for users rather than copied from commit messages.
- Only `src/qutop/providers/` may talk to the network. Everything else takes
  plain data so it can be tested offline.
