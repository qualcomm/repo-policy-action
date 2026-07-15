# Copyright (c) Qualcomm Technologies, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""no-file-type-exists rule type for repo-policy-action.

Checks that no binary files of specified types exist in the repository.
Uses python-magic to detect files by their magic bytes rather than
extension — this is more robust than extension matching for detecting
compiled binaries that may be committed without the expected extension.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import magic

from fs_utils import walk_files
from gitignore import GitignoreMatcher
from reporter import Reporter, RuleResult

logger = logging.getLogger(__name__)

# MIME type prefixes that indicate compiled/binary executables and
# shared libraries. Text-based formats (PDF, SVG, etc.) are excluded
# intentionally — the rule targets compiled artifacts only.
_BINARY_MIME_PREFIXES = (
    "application/x-executable",
    "application/x-sharedlib",
    "application/x-pie-executable",
    "application/x-dosexec",  # .exe / .dll (PE format, older libmagic dbs)
    "application/vnd.microsoft.portable-executable",  # .exe / .dll, newer dbs
    "application/x-object",  # .o object files
    "application/x-mach-binary",  # macOS Mach-O binaries
)

# Extension allow-list: even if magic returns a binary MIME type, these
# extensions are excluded because they are commonly legitimate (e.g.
# test fixtures, fonts — teams can override at config level if needed).
_ALLOWED_EXTENSIONS = frozenset(
    {
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".ico",
    }
)


def run(
    repo_path: str,
    rule_name: str,
    level: str,
    options: dict[str, Any],
    reporter: Reporter,
    ignore_matcher: GitignoreMatcher | None = None,
) -> RuleResult:
    """Evaluate a no-file-type-exists rule.

    Scans the repository for binary files identified by magic bytes.
    Reports the first offending file found.

    Args:
        repo_path: Absolute path to the repository root.
        rule_name: Rule identifier for annotations.
        level: ``"error"`` or ``"warning"``.
        options: Rule options from the config. Expected keys:
            - ``"type"`` (str, optional): not used for routing but
              present in the config for compatibility.
            - ``"extensions"`` (list[str], optional): additional
              extensions to flag regardless of MIME type.
        reporter: Reporter instance.
        ignore_matcher: When provided, files ignored by ``.gitignore``
            are excluded from the scan.

    Returns:
        A RuleResult indicating pass or failure.
    """
    extra_extensions: set[str] = {
        ext.lower() for ext in options.get("extensions", [])
    }
    root = Path(repo_path)
    mime_detector = magic.Magic(mime=True)

    for file_path in walk_files(root, ignore_matcher):
        if file_path.suffix.lower() in _ALLOWED_EXTENSIONS:
            continue

        if file_path.suffix.lower() in extra_extensions:
            return reporter.rule_failed(
                rule_name=rule_name,
                level=level,
                message=(
                    f"Binary file found (matched extension "
                    f"{file_path.suffix!r}): "
                    f"{file_path.relative_to(root)}"
                ),
                file_path=str(file_path.relative_to(root)),
            )

        try:
            mime = mime_detector.from_file(str(file_path))
        except OSError as exc:
            logger.warning(
                "Could not determine MIME type of %s: %s", file_path, exc
            )
            continue

        if any(mime.startswith(prefix) for prefix in _BINARY_MIME_PREFIXES):
            return reporter.rule_failed(
                rule_name=rule_name,
                level=level,
                message=(
                    f"Binary file found (MIME: {mime}): "
                    f"{file_path.relative_to(root)}"
                ),
                file_path=str(file_path.relative_to(root)),
            )

    return reporter.rule_passed(rule_name, "No binary files detected.")
