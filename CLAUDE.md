# CLAUDE.md

This file provides guidance for Claude when working in this repository.

## Project Purpose

`repo-policy-action` is a Python-based GitHub Action that validates repository policy rules (for example, required files, directory checks, file type checks, and file content checks).

## Repository Layout

- `src/`: action implementation
  - `main.py`: entry point
  - `config.py`: configuration loading and validation
  - `language_detector.py`: language detection helpers
  - `gitignore.py`: `.gitignore`-aware path matcher (built on `pathspec`) used to exclude ignored files/directories from all rule scans
  - `reporter.py`: output/report formatting
  - `rules/`: individual policy rule implementations (`file_existence.py`, `file_contents.py`, `file_type.py`, `directory_existence.py`)
- `tests/`: pytest test suite aligned with modules under `src/`
- `scripts/`: developer utilities (e.g. `compare_tools.py` for RPA vs repolinter comparison)
- `action.yml`: GitHub Action definition
- `README.md`: user-facing action documentation

## Development Commands

Run from repository root. Dependencies are declared in `pyproject.toml` and
locked in `uv.lock`; [uv](https://docs.astral.sh/uv/) is the recommended tool.

Set up the environment:

- With uv (installs project + dev group from the lock): `uv sync`
- With venv: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt`

Common tasks (prefix with `uv run` when using uv, or activate the venv first):

- Run tests: `pytest`
- Run tests with coverage: `pytest --cov=src --cov-report=term-missing`
- Run full local checks: `tox` (provisioned via tox-uv)

`requirements.txt` and `requirements-dev.txt` are generated from `pyproject.toml`
+ `uv.lock` via `uv export`; edit dependencies in `pyproject.toml` and re-run
`uv lock` + `uv export`, never by hand.

## Coding Guidelines

- Prefer small, focused changes.
- Preserve existing public interfaces unless a change explicitly requires breaking behavior.
- Add or update tests in `tests/` for behavior changes.
- Keep rule implementations deterministic and easy to diagnose in CI output.
- Avoid adding dependencies unless there is a clear, justified need.
- Maintain compatibility with the existing `repolint.json` config format for v1.x; planned breaking changes will be reserved for v2.x with a clear migration path.

## Change Safety

- Do not revert unrelated local changes.
- Stage and commit only files relevant to the requested task.
- If behavior changes, update `README.md` and tests together.
