# Copyright (c) Qualcomm Technologies, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""file-contents / file-starts-with rule types for repo-policy-action.

Checks that files matching a glob pattern contain (or start with) a
given string or regex. Used for copyright header enforcement and README
license reference checks.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import magic

from fs_utils import SKIP_DIRS
from gitignore import GitignoreMatcher
from reporter import Reporter, RuleResult

logger = logging.getLogger(__name__)


@dataclass
class _Options:  # pylint: disable=too-many-instance-attributes
    """Normalised file-contents/file-starts-with rule options."""

    globs: list[str]
    content_patterns: list[str]
    flag_names: list[str]
    line_count: int | None
    fail_on_missing: bool
    skip_paths: list[str]
    skip_binary: bool
    fail_message: str | None


def _parse_options(options: dict[str, Any]) -> _Options:
    """Normalise rule options into canonical types.

    Handles two schemas:
    - Internal (``content``, ``flags`` as list, ``fail-on-non-existent``,
      ``skip-paths-matching`` as list).
    - repolint.json v2 (``patterns`` list, ``flags`` as string ``"i"``,
      ``succeed-on-non-existent``, ``skip-paths-matching`` as object).

    ``content_patterns`` is a list of pattern strings — all must match
    (AND semantics), matching repolinter's behaviour.
    """
    globs: list[str] = options.get("globsAll", [])

    content_patterns: list[str] = []
    single = options.get("content", "")
    if single:
        content_patterns = [single]
    else:
        raw_patterns = options.get("patterns", [])
        if isinstance(raw_patterns, list):
            content_patterns = [p for p in raw_patterns if p]
        elif isinstance(raw_patterns, str) and raw_patterns:
            content_patterns = [raw_patterns]

    raw_flags = options.get("flags", [])
    if isinstance(raw_flags, str):
        flag_names: list[str] = [raw_flags] if raw_flags else []
    else:
        flag_names = list(raw_flags)

    line_count: int | None = options.get("lineCount")

    fail_on_missing: bool = options.get("fail-on-non-existent", False)
    if not fail_on_missing and "succeed-on-non-existent" in options:
        fail_on_missing = not options["succeed-on-non-existent"]

    raw_skip = options.get("skip-paths-matching", [])
    if isinstance(raw_skip, dict):
        skip_paths: list[str] = raw_skip.get("patterns", [])
    else:
        skip_paths = list(raw_skip)

    skip_binary: bool = options.get("skip-binary-files", False)
    fail_message: str | None = options.get("fail-message")

    return _Options(
        globs=globs,
        content_patterns=content_patterns,
        flag_names=flag_names,
        line_count=line_count,
        fail_on_missing=fail_on_missing,
        skip_paths=skip_paths,
        skip_binary=skip_binary,
        fail_message=fail_message,
    )


def run(
    repo_path: str,
    rule_name: str,
    level: str,
    options: dict[str, Any],
    reporter: Reporter,
    ignore_matcher: GitignoreMatcher | None = None,
) -> RuleResult:
    """Evaluate a file-contents or file-starts-with rule.

    Scans every file matched by ``globsAll`` and checks whether each
    contains the required content. Reports the first offending file.

    Args:
        repo_path: Absolute path to the repository root.
        rule_name: Rule identifier for annotations.
        level: ``"error"`` or ``"warning"``.
        options: Rule options from the config. See ``_parse_options``
            for the full list of supported keys.
        reporter: Reporter instance.
        ignore_matcher: When provided, files ignored by ``.gitignore``
            are excluded from the scan.

    Returns:
        A RuleResult indicating pass or failure.
    """
    opts = _parse_options(options)

    if not opts.content_patterns:
        logger.warning(
            "Rule '%s' has no content pattern — skipping.", rule_name
        )
        return reporter.rule_passed(
            rule_name, "No content pattern configured — skipped."
        )

    compiled_patterns = _compile_all_patterns(
        opts.content_patterns, opts.flag_names, rule_name, level
    )
    if isinstance(compiled_patterns, dict):
        return reporter.rule_failed(**compiled_patterns)

    root = Path(repo_path)
    matched_files = _find_files(root, opts.globs, ignore_matcher)

    if not matched_files:
        if opts.fail_on_missing:
            return reporter.rule_failed(
                rule_name=rule_name,
                level=level,
                message=opts.fail_message
                or f"No files matched patterns {opts.globs}",
            )
        return reporter.rule_passed(
            rule_name, f"No files matched {opts.globs} — skipped."
        )

    failure = _scan_files(
        matched_files, root, compiled_patterns, opts, rule_name
    )
    if failure is not None:
        return reporter.rule_failed(level=level, **failure)

    return reporter.rule_passed(
        rule_name,
        f"All patterns found in all {len(matched_files)} matched file(s).",
    )


def _scan_files(
    matched_files: list[Path],
    root: Path,
    compiled_patterns: list[tuple[str, re.Pattern]],
    opts: _Options,
    rule_name: str,
) -> dict[str, Any] | None:
    """Scan matched files against the compiled patterns.

    Returns kwargs (minus ``level``) for ``reporter.rule_failed`` on the
    first offending file, or ``None`` if every file passed.
    """
    skip_patterns = _compile_skip_patterns(opts.skip_paths, rule_name)
    mime_detector = magic.Magic(mime=True) if opts.skip_binary else None

    for file_path in matched_files:
        rel = str(file_path.relative_to(root))
        if _should_skip_file(
            file_path,
            rel,
            skip_patterns,
            opts.skip_binary,
            mime_detector,
            rule_name,
        ):
            continue

        failure = _check_patterns(
            file_path,
            rel,
            compiled_patterns,
            opts.line_count,
            rule_name,
            opts.fail_message,
        )
        if failure is not None:
            return failure
    return None


def _compile_all_patterns(
    content_patterns: list[str],
    flag_names: list[str],
    rule_name: str,
    level: str,
) -> list[tuple[str, re.Pattern]] | dict[str, Any]:
    """Compile every content pattern, or return failure kwargs on error.

    Returns:
        A list of ``(raw, compiled)`` pairs, or a dict of kwargs for
        ``reporter.rule_failed`` if a pattern fails to compile.
    """
    compiled_patterns: list[tuple[str, re.Pattern]] = []
    for raw in content_patterns:
        compiled = _compile_pattern(raw, flag_names, rule_name)
        if compiled is None:
            return {
                "rule_name": rule_name,
                "level": level,
                "message": f"Invalid regex pattern: {raw!r}",
            }
        compiled_patterns.append((raw, compiled))
    return compiled_patterns


# pylint: disable-next=too-many-arguments,too-many-positional-arguments
def _should_skip_file(
    file_path: Path,
    rel: str,
    skip_patterns: list[re.Pattern],
    skip_binary: bool,
    mime_detector: "magic.Magic | None",
    rule_name: str,
) -> bool:
    """Decide whether a matched file should be skipped for content checks."""
    # Broken symlinks: skip rather than raise.
    if file_path.is_symlink() and not file_path.exists():
        logger.debug(
            "Rule '%s': skipping broken symlink %s.", rule_name, file_path
        )
        return True

    if _should_skip_path(rel, skip_patterns):
        logger.debug(
            "Rule '%s': skipping %s (skip-paths-matching).", rule_name, rel
        )
        return True

    if skip_binary and mime_detector and _is_binary(file_path, mime_detector):
        logger.debug("Rule '%s': skipping binary file %s.", rule_name, rel)
        return True

    return False


# pylint: disable-next=too-many-arguments,too-many-positional-arguments
def _check_patterns(
    file_path: Path,
    rel: str,
    compiled_patterns: list[tuple[str, re.Pattern]],
    line_count: int | None,
    rule_name: str,
    fail_message: str | None,
) -> dict[str, Any] | None:
    """Check all patterns against a file (AND semantics).

    Returns kwargs (minus ``level``) for ``reporter.rule_failed`` on the
    first missing pattern, or ``None`` if every pattern matched.
    """
    for raw, compiled in compiled_patterns:
        if not _file_contains(file_path, compiled, line_count):
            return {
                "rule_name": rule_name,
                "message": fail_message
                or (f"Pattern {raw!r} not found in {rel}"),
                "file_path": rel,
            }
    return None


def _compile_pattern(
    pattern: str, flag_names: list[str], rule_name: str
) -> re.Pattern | None:
    """Compile a regex pattern with optional flags.

    Args:
        pattern: The regex pattern string.
        flag_names: List of ``re`` flag names e.g. ``["IGNORECASE"]``.
        rule_name: Used only for log messages.

    Returns:
        Compiled pattern, or None if the pattern is invalid.
    """
    flags = re.MULTILINE
    for name in flag_names:
        flag = getattr(re, name.upper(), None)
        if flag is None:
            logger.warning(
                "Rule '%s': unknown regex flag '%s'.", rule_name, name
            )
        else:
            flags |= flag
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        logger.error(
            "Rule '%s': failed to compile pattern %r: %s",
            rule_name,
            pattern,
            exc,
        )
        return None


def _compile_skip_patterns(
    skip_paths: list[str], rule_name: str
) -> list[re.Pattern]:
    """Compile skip-paths-matching entries into regex patterns.

    Entries that look like file extensions (start with ``.``) are
    converted to a suffix-match pattern. Other entries are compiled
    as-is.

    Args:
        skip_paths: List of extension strings or regex patterns.
        rule_name: Used only for log messages.

    Returns:
        List of compiled regex patterns.
    """
    compiled: list[re.Pattern] = []
    for entry in skip_paths:
        if entry.startswith("."):
            pattern = re.escape(entry) + "$"
        else:
            pattern = entry
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            logger.warning(
                "Rule '%s': invalid skip pattern %r: %s",
                rule_name,
                entry,
                exc,
            )
    return compiled


def _should_skip_path(rel_path: str, skip_patterns: list[re.Pattern]) -> bool:
    """Return True if the relative path matches any skip pattern.

    Args:
        rel_path: Relative path string to check.
        skip_patterns: Compiled patterns from skip-paths-matching.

    Returns:
        True if the file should be skipped.
    """
    return any(p.search(rel_path) for p in skip_patterns)


def _is_binary(path: Path, mime_detector: magic.Magic) -> bool:
    """Return True if libmagic identifies the file as binary.

    Args:
        path: Path to the file.
        mime_detector: Initialised magic.Magic(mime=True) instance.

    Returns:
        True if the file is not a text/* MIME type.
    """
    try:
        mime = mime_detector.from_file(str(path))
        return not mime.startswith("text/")
    except OSError:
        return False


def _find_files(
    root: Path,
    globs: list[str],
    ignore_matcher: GitignoreMatcher | None = None,
) -> list[Path]:
    """Return all files under root that match any of the glob patterns.

    Args:
        root: Repository root path.
        globs: List of glob patterns to match.
        ignore_matcher: When provided, files ignored by ``.gitignore``
            are excluded from the results.

    Returns:
        Sorted, deduplicated list of matching file paths.
    """
    found: set[Path] = set()
    for pattern in globs:
        for match in root.glob(pattern):
            if _in_skip_dir(match, root):
                continue
            if ignore_matcher is not None and ignore_matcher.is_ignored(
                match, is_dir=match.is_dir()
            ):
                continue
            if match.is_file():
                if match.stat().st_size == 0:
                    logger.debug("Skipping empty file %s.", match)
                    continue
                found.add(match)
            # Include broken symlinks so we can handle them explicitly.
            elif match.is_symlink():
                found.add(match)
    return sorted(found)


def _in_skip_dir(path: Path, root: Path) -> bool:
    """Return True if path is inside a directory that should be skipped.

    Args:
        path: File path to check.
        root: Repository root.

    Returns:
        True if any ancestor directory name is in ``SKIP_DIRS``.
    """
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return any(part in SKIP_DIRS for part in relative.parts[:-1])


def _file_contains(
    path: Path, pattern: re.Pattern, line_count: int | None
) -> bool:
    """Return True if the file content matches the pattern.

    Args:
        path: Path to the file.
        pattern: Compiled regex to search for.
        line_count: If set, only read the first N lines.

    Returns:
        True if the pattern matches; False otherwise or on read error.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            if line_count is not None:
                text = "".join(line for _, line in zip(range(line_count), fh))
            else:
                text = fh.read()
        return bool(pattern.search(text))
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return False
