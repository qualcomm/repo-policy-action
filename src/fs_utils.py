# Copyright (c) Qualcomm Technologies, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""Shared filesystem walking helpers for repo-policy-action.

Centralises the directory skip-list and recursive walk used by
multiple rule modules and the language detector, so they stay in
sync rather than drifting as separate copies.
"""

from __future__ import annotations

from pathlib import Path

from gitignore import SKIP_DIRS, GitignoreMatcher


def walk_files(root: Path, ignore_matcher: GitignoreMatcher | None = None):
    """Yield all files under root, skipping directories in SKIP_DIRS.

    Args:
        root: Repository root path.
        ignore_matcher: When provided, files and directories ignored by
            ``.gitignore`` are pruned from the walk.

    Yields:
        Path objects for each non-skipped file.
    """
    for item in root.iterdir():
        if item.is_dir():
            if item.name in SKIP_DIRS:
                continue
            if ignore_matcher is not None and ignore_matcher.is_ignored(
                item, is_dir=True
            ):
                continue
            yield from walk_files(item, ignore_matcher)
        else:
            if ignore_matcher is not None and ignore_matcher.is_ignored(
                item, is_dir=False
            ):
                continue
            yield item
