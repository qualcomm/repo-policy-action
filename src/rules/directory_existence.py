# Copyright (c) Qualcomm Technologies, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""directory-existence rule type for repo-policy-action.

Checks that at least one directory matching any of the provided glob
patterns exists under the repository root.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from gitignore import GitignoreMatcher
from reporter import Reporter, RuleResult
from rules._common import globs_any_or_skip

logger = logging.getLogger(__name__)


def run(
    repo_path: str,
    rule_name: str,
    level: str,
    options: dict[str, Any],
    reporter: Reporter,
    ignore_matcher: GitignoreMatcher | None = None,
) -> RuleResult:
    """Evaluate a directory-existence rule.

    Args:
        repo_path: Absolute path to the repository root.
        rule_name: Rule identifier for annotations.
        level: ``"error"`` or ``"warning"``.
        options: Rule options from the config. Expected keys:
            - ``"globsAny"`` (list[str]): glob patterns, at least one
              must match a directory.
        reporter: Reporter instance.
        ignore_matcher: When provided, directories ignored by
            ``.gitignore`` do not count as satisfying the rule.

    Returns:
        A RuleResult indicating pass or failure.
    """
    globs: list[str] = options.get("globsAny", [])

    skip_result = globs_any_or_skip(globs, rule_name, reporter)
    if skip_result is not None:
        return skip_result

    root = Path(repo_path)
    for pattern in globs:
        matches = [p for p in root.glob(pattern) if p.is_dir()]
        if ignore_matcher is not None:
            matches = [
                p
                for p in matches
                if not ignore_matcher.is_ignored(p, is_dir=True)
            ]
        if matches:
            logger.debug(
                "Rule '%s' passed — found directory '%s'.",
                rule_name,
                matches[0],
            )
            return reporter.rule_passed(
                rule_name,
                f"Found directory: {matches[0].relative_to(root)}",
            )

    return reporter.rule_failed(
        rule_name=rule_name,
        level=level,
        message=f"No directory matching {globs} found under {root}",
    )
