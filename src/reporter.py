# Copyright (c) Qualcomm Technologies, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""GitHub Actions annotation output for repo-policy-action.

Emits workflow commands (``::error`` / ``::warning``) that GitHub
Actions renders as inline annotations on the PR files view, and prints
a human-readable summary table at the end of the run.

See: https://docs.github.com/en/actions/writing-workflows/\
choosing-what-your-workflow-does/workflow-commands-for-github-actions
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RuleResult:
    """The outcome of evaluating a single policy rule.

    Attributes:
        rule_name: The rule identifier from the config (e.g.
            ``"license-file-exists"``).
        level: ``"error"``, ``"warning"``, or ``"off"``.
        passed: True if the rule passed, False if it failed.
        message: Human-readable description of what was checked and
            why it failed (or passed).
        file_path: Optional path to the offending file, for
            annotations that should point to a specific location.
    """

    rule_name: str
    level: str
    passed: bool
    message: str
    file_path: str | None = None


class RuleResultList(list[RuleResult]):
    """A list of RuleResult objects that behaves like a single RuleResult.

    This is used when a rule produces multiple failures (e.g. across multiple
    files) but unit tests or callers expect a single RuleResult-like interface.
    """

    def __init__(self, results: list[RuleResult]):
        super().__init__(results)
        self._primary = (
            results[0]
            if results
            else RuleResult(
                rule_name="unknown",
                level="off",
                passed=True,
                message="No results.",
            )
        )

    @property
    def passed(self) -> bool:
        """Return the passed status of the primary (first) result."""
        return self._primary.passed

    @property
    def rule_name(self) -> str:
        """Return the rule name of the primary (first) result."""
        return self._primary.rule_name

    @property
    def level(self) -> str:
        """Return the level of the primary (first) result."""
        return self._primary.level

    @property
    def message(self) -> str:
        """Return the message of the primary (first) result."""
        return self._primary.message

    @property
    def file_path(self) -> str | None:
        """Return the file path of the primary (first) result."""
        return self._primary.file_path


class Reporter:
    """Emits GitHub Actions workflow command annotations and summaries."""

    def rule_passed(self, rule_name: str, message: str) -> RuleResult:
        """Record and log a passing rule.

        Args:
            rule_name: Rule identifier.
            message: Description of what was checked.

        Returns:
            A RuleResult marked as passed.
        """
        logger.info("[PASS] %s: %s", rule_name, message)
        return RuleResult(
            rule_name=rule_name,
            level="off",
            passed=True,
            message=message,
        )

    def rule_failed(
        self,
        rule_name: str,
        level: str,
        message: str,
        file_path: str | None = None,
    ) -> RuleResult:
        """Record, annotate, and log a failing rule.

        Args:
            rule_name: Rule identifier.
            level: ``"error"`` or ``"warning"``.
            message: Description of the failure.
            file_path: Optional file path for inline annotation.

        Returns:
            A RuleResult marked as failed.
        """
        annotation = _build_annotation(
            level=level,
            title=f"Policy Violation: {rule_name}",
            message=message,
            file_path=file_path,
        )
        print(annotation)  # workflow commands must go to stdout
        logger.warning("[FAIL] %s: %s", rule_name, message)
        return RuleResult(
            rule_name=rule_name,
            level=level,
            passed=False,
            message=message,
            file_path=file_path,
        )

    def error(self, message: str) -> None:
        """Emit a non-rule error annotation (e.g. config load failure).

        Args:
            message: The error message.
        """
        print(f"::error title=repo-policy-action::{message}")
        logger.error(message)

    def warning(self, message: str) -> None:
        """Emit a non-rule warning annotation.

        Args:
            message: The warning message.
        """
        print(f"::warning title=repo-policy-action::{message}")
        logger.warning(message)

    def summary(self, results: list[RuleResult]) -> None:
        """Print a human-readable summary of all rule results.

        Args:
            results: All RuleResult objects from the run.
        """
        passed = [r for r in results if r.passed]
        failed = [r for r in results if not r.passed]
        errors = [r for r in failed if r.level == "error"]
        warnings = [r for r in failed if r.level == "warning"]

        print("\n── Repository Policy Check Summary ──────────────────")
        print(f"  Passed  : {len(passed)}")
        print(f"  Warnings: {len(warnings)}")
        print(f"  Errors  : {len(errors)}")

        if failed:
            print("\nFailed rules:")
            for result in failed:
                indicator = "✖" if result.level == "error" else "⚠"
                print(f"  {indicator} {result.rule_name}: {result.message}")

        print("─────────────────────────────────────────────────────\n")


def _build_annotation(
    level: str,
    title: str,
    message: str,
    file_path: str | None,
) -> str:
    """Build a GitHub Actions workflow command annotation string.

    Args:
        level: ``"error"`` or ``"warning"``.
        title: Annotation title shown in the UI.
        message: The annotation body.
        file_path: Optional file path for inline placement.

    Returns:
        A formatted ``::error`` or ``::warning`` workflow command.
    """
    params = f"title={title}"
    if file_path:
        params += f",file={file_path}"
    return f"::{level} {params}::{message}"
