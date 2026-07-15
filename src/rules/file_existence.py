# Copyright (c) Qualcomm Technologies, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""file-existence rule type for repo-policy-action.

Checks that at least one file matching any of the provided glob
patterns exists somewhere under the repository root (optionally
restricted to specific subdirectories).
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
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
    """Evaluate a file-existence rule.

    Args:
        repo_path: Absolute path to the repository root.
        rule_name: Rule identifier for annotations.
        level: ``"error"`` or ``"warning"``.
        options: Rule options from the config. Expected keys:
            - ``"globsAny"`` (list[str]): glob patterns, at least one
              must match.
            - ``"dirs"`` (list[str], optional): restrict search to
              these subdirectories relative to the repo root.
            - ``"nocase"`` (bool, optional): case-insensitive filename
              matching (default: False).
            - ``"fail-message"`` (str, optional): custom message to
              emit on failure instead of the default.
        reporter: Reporter instance.
        ignore_matcher: When provided, files ignored by ``.gitignore``
            do not count as satisfying the rule.

    Returns:
        A RuleResult indicating pass or failure.
    """
    globs: list[str] = options.get("globsAny", [])
    dirs: list[str] = options.get("dirs", [""])
    nocase: bool = options.get("nocase", False)
    fail_message: str | None = options.get("fail-message")

    skip_result = globs_any_or_skip(globs, rule_name, reporter)
    if skip_result is not None:
        return skip_result

    root = Path(repo_path)
    # Expand brace expressions once before iterating dirs.
    expanded_globs = [e for g in globs for e in _expand_braces(g)]
    match = _find_first_match(root, dirs, expanded_globs, nocase)
    if match is not None:
        logger.debug("Rule '%s' passed — found '%s'.", rule_name, match)
        return reporter.rule_passed(
            rule_name, f"Found: {match.relative_to(root)}"
        )

    searched = ", ".join((str(root / d) if d else str(root)) for d in dirs)
    message = fail_message or (f"No file matching {globs} found in: {searched}")
    return reporter.rule_failed(
        rule_name=rule_name,
        level=level,
        message=message,
    )


def _find_first_match(
    root: Path, dirs: list[str], expanded_globs: list[str], nocase: bool
) -> Path | None:
    """Return the first file matching any glob in any dir, or None.

    Args:
        root: Repository root path.
        dirs: Subdirectories (relative to root) to search; "" for root.
        expanded_globs: Brace-expanded glob patterns to try.
        nocase: Whether to match filenames case-insensitively.

    Returns:
        The first matching Path, or None if nothing matched.
    """
    for search_dir in dirs:
        base = root / search_dir if search_dir else root
        if not base.is_dir():
            continue
        for pattern in expanded_globs:
            if nocase:
                matches = _glob_nocase(base, pattern)
            else:
                matches = [
                    p
                    for p in base.glob(pattern)
                    if _is_accessible_file(p) and _case_matches(p, pattern)
                ]
            matches = _filter_ignored(matches, ignore_matcher)
            if matches:
                return matches[0]
    return None


def _filter_ignored(
    matches: list[Path],
    ignore_matcher: GitignoreMatcher | None,
) -> list[Path]:
    """Drop paths ignored by ``.gitignore`` from a match list.

    Args:
        matches: Candidate paths from a glob.
        ignore_matcher: When None, ``matches`` is returned unchanged.

    Returns:
        The filtered list of paths.
    """
    if ignore_matcher is None:
        return matches
    return [
        p
        for p in matches
        if not ignore_matcher.is_ignored(p, is_dir=p.is_dir())
    ]


def _expand_braces(pattern: str) -> list[str]:
    """Expand a single-level brace expression into a list of patterns.

    Handles simple ``{a,b,c}stem`` syntax as used in repolint.json, e.g.
    ``"{docs/,.github/,}CONTRIB*"`` → ``["docs/CONTRIB*", ".github/CONTRIB*",
    "CONTRIB*"]``.  Nested braces are not supported.

    Args:
        pattern: A glob pattern string, possibly containing ``{a,b}`` syntax.

    Returns:
        List of expanded patterns (length 1 when no braces are present).
    """
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    before = pattern[: match.start()]
    after = pattern[match.end() :]
    alternatives = match.group(1).split(",")
    return [f"{before}{alt}{after}" for alt in alternatives]


def _case_matches(path: Path, pattern: str) -> bool:
    """Return True if the filename's case exactly matches the pattern.

    On case-insensitive filesystems (macOS HFS+), Path.glob() normalises
    the returned name to match the pattern, hiding the actual on-disk
    casing. When nocase=False we read the real name via os.listdir to
    enforce exact case.

    Args:
        path: Candidate file path.
        pattern: The original glob pattern string.

    Returns:
        True if the file name matches the last component of the pattern
        exactly (case-sensitive), or if the pattern contains a wildcard.
    """
    pattern_name = Path(pattern).name
    if any(c in pattern_name for c in ("*", "?", "[")):
        return True
    try:
        disk_names = os.listdir(path.parent)
    except OSError:
        return False
    return pattern_name in disk_names


def _is_accessible_file(path: Path) -> bool:
    """Return True for regular files and resolvable symlinks to files."""
    return path.is_file() and (not path.is_symlink() or path.exists())


def _glob_nocase(base: Path, pattern: str) -> list[Path]:
    """Case-insensitive glob by walking the tree and comparing lowercased.

    Uses fnmatch so wildcard patterns like ``"README*"`` and
    ``"CONTRIB*"`` are resolved correctly.

    Args:
        base: Directory to search under.
        pattern: Glob pattern (e.g. ``"LICENSE"`` or ``"README*"``).

    Returns:
        List of matching file paths.
    """
    pattern_lower = pattern.lower()
    results: list[Path] = []
    for candidate in base.rglob("*"):
        if not _is_accessible_file(candidate):
            continue
        try:
            rel = candidate.relative_to(base)
        except ValueError:
            continue
        if fnmatch.fnmatch(str(rel).lower(), pattern_lower):
            results.append(candidate)
    return results
