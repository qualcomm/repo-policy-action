# Copyright (c) Qualcomm Technologies, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""Rule dispatcher for repo-policy-action.

Iterates over all rules in the loaded config, filters out rules that
don't apply to the detected languages/packagers, and routes each rule
to the appropriate handler module.
"""

from __future__ import annotations

import logging
from typing import Any

from gitignore import GitignoreMatcher
from reporter import Reporter, RuleResult
from rules.directory_existence import run as run_directory_existence
from rules.file_contents import run as run_file_contents
from rules.file_existence import run as run_file_existence
from rules.file_type import run as run_file_type

logger = logging.getLogger(__name__)

_RULE_HANDLERS = {
    "file-existence": run_file_existence,
    "file-starts-with": run_file_contents,
    "file-contents": run_file_contents,
    "no-file-type-exists": run_file_type,
    "file-type-exclusion": run_file_type,
    "directory-existence": run_directory_existence,
}


def run_all_rules(
    repo_path: str,
    config: dict[str, Any],
    languages: dict[str, set[str]],
    reporter: Reporter,
    ignore_matcher: GitignoreMatcher | None = None,
) -> list[RuleResult]:
    """Run all applicable rules from the config.

    Args:
        repo_path: Absolute path to the repository root.
        config: Parsed policy config dict.
        languages: Output of ``detect_languages`` — detected language
            and packager sets.
        reporter: Reporter instance for emitting annotations.
        ignore_matcher: When provided, files and directories ignored by
            ``.gitignore`` are excluded from every rule's scan.

    Returns:
        List of RuleResult objects, one per evaluated rule.
    """
    results: list[RuleResult] = []
    rules: dict[str, Any] = config.get("rules", {})

    for rule_name, rule_def in rules.items():
        level: str = rule_def.get("level", "warning")
        if level == "off":
            logger.debug("Rule '%s' is disabled (level=off).", rule_name)
            continue

        if not _rule_applies(rule_def, languages):
            logger.debug(
                "Rule '%s' skipped — language/packager condition not met.",
                rule_name,
            )
            continue

        rule_spec: dict[str, Any] = rule_def.get("rule", {})
        rule_type: str = rule_spec.get("type", "")
        handler = _RULE_HANDLERS.get(rule_type)

        if handler is None:
            logger.warning(
                "Rule '%s' has unsupported type '%s' — skipping.",
                rule_name,
                rule_type,
            )
            continue

        result = handler(
            repo_path=repo_path,
            rule_name=rule_name,
            level=level,
            options=rule_spec.get("options", {}),
            reporter=reporter,
            ignore_matcher=ignore_matcher,
        )
        if isinstance(result, list):
            results.extend(result)
        else:
            results.append(result)

    return results


def _rule_applies(
    rule_def: dict[str, Any],
    languages: dict[str, set[str]],
) -> bool:
    """Check whether a rule's ``where`` conditions are satisfied.

    A rule with no ``where`` clause always applies. Otherwise all
    conditions must be satisfied (AND semantics).

    Args:
        rule_def: The rule definition dict from the config.
        languages: Detected language and packager sets.

    Returns:
        True if the rule should be evaluated.
    """
    where: list[str] = rule_def.get("where", [])
    if not where:
        return True

    detected_languages = languages.get("languages", set())
    detected_packagers = languages.get("packagers", set())

    for condition in where:
        if "=" not in condition:
            logger.warning("Unrecognised where condition: '%s'", condition)
            continue
        axiom, value = condition.split("=", 1)
        # "language=" is the repolinter-native alias for "linguist=".
        if axiom == "language":
            axiom = "linguist"
        # Wildcard "*" matches any detected value for this axiom.
        if value == "*":
            if axiom == "linguist" and not detected_languages:
                return False
            if axiom == "packagers" and not detected_packagers:
                return False
            continue
        # Case-insensitive comparison: config values like "javascript" must
        # match detected names like "JavaScript".
        value_lower = value.lower()
        if axiom == "linguist" and value_lower not in {
            lang.lower() for lang in detected_languages
        }:
            return False
        if axiom == "packagers" and value_lower not in {
            pkg.lower() for pkg in detected_packagers
        }:
            return False

    return True
