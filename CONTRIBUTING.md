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

## Releases

Every push to `main` publishes a dev build to TestPyPI once CI passes:

    pipx install --index-url https://test.pypi.org/simple/ \
        --pip-args="--extra-index-url https://pypi.org/simple" qutop

To release a version:

1. Rename `[Unreleased]` in `CHANGELOG.md` to `[X.Y.Z] - YYYY-MM-DD` and add
   a new empty `[Unreleased]` above it.
2. Commit as `release: vX.Y.Z` and push to `main`.
3. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.

The tag publishes to PyPI (after approval in the `pypi` environment) and
creates a GitHub Release with that changelog section as its notes. The
version comes from the tag; there is no version number to edit.
