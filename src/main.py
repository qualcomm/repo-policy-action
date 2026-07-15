# Copyright (c) Qualcomm Technologies, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""Entry point for repo-policy-action.

Loads config, runs all applicable rules against the target repository,
emits GitHub Actions annotations, and exits with the appropriate code.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from config import load_config
from gitignore import load_gitignore_matcher
from language_detector import detect_languages
from reporter import Reporter
from rules import run_all_rules

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_URL = (
    "https://raw.githubusercontent.com/qualcomm/.github/main/repolint.json"
)


@click.command()
@click.option(
    "--repo-path",
    required=True,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="Absolute path to the repository root to check.",
)
@click.option(
    "--config-file",
    default=None,
    type=click.Path(exists=True, resolve_path=True),
    help=(
        "Path to a local repo-policy.json or repolint.json override. "
        "Auto-detected from the repo root if not provided."
    ),
)
@click.option(
    "--config-url",
    default=_DEFAULT_CONFIG_URL,
    show_default=True,
    help="URL to fetch the default policy config from.",
)
@click.option(
    "--fail-on-error/--no-fail-on-error",
    default=True,
    show_default=True,
    help="Exit non-zero when any error-level rule fails.",
)
@click.option(
    "--fail-on-warning/--no-fail-on-warning",
    default=False,
    show_default=True,
    help="Exit non-zero when any warning-level rule fails.",
)
@click.option(
    "--respect-gitignore/--no-respect-gitignore",
    default=True,
    show_default=True,
    help=(
        "Skip files and directories matched by the repository's "
        ".gitignore files when evaluating rules."
    ),
)
def main(
    repo_path: str,
    config_file: str | None,
    config_url: str,
    fail_on_error: bool,
    fail_on_warning: bool,
    respect_gitignore: bool,
) -> None:
    """Enforce repository policy standards for Qualcomm open-source projects."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    reporter = Reporter()

    config = load_config(
        repo_path=repo_path,
        config_file=config_file,
        config_url=config_url,
        reporter=reporter,
    )
    if config is None:
        reporter.error("Failed to load policy config. Cannot continue.")
        sys.exit(1)

    ignore_matcher = load_gitignore_matcher(
        Path(repo_path), respect_gitignore=respect_gitignore
    )
    languages = detect_languages(repo_path, ignore_matcher)
    results = run_all_rules(
        repo_path=repo_path,
        config=config,
        languages=languages,
        reporter=reporter,
        ignore_matcher=ignore_matcher,
    )

    errors = [r for r in results if r.level == "error" and not r.passed]
    warnings = [r for r in results if r.level == "warning" and not r.passed]

    reporter.summary(results)

    if errors and fail_on_error:
        sys.exit(1)
    if warnings and fail_on_warning:
        sys.exit(1)


if __name__ == "__main__":
    main()  # pylint: disable=no-value-for-parameter
