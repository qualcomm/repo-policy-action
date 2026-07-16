# Copyright (c) Qualcomm Technologies, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""`.gitignore`-aware path filtering for repo-policy-action.

Provides a matcher that reads every ``.gitignore`` in a repository tree
(root and nested) and reports whether a given path is ignored, mirroring
git's hierarchical semantics: a ``.gitignore`` applies to its own
directory and everything below it, and deeper files are matched relative
to the directory the pattern was declared in.

Uses ``pathspec`` (the same engine ``black`` and ``pip`` use) so pattern
support — wildcards, anchoring, negation, directory-only patterns —
matches real git behaviour rather than an approximation.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pathspec

logger = logging.getLogger(__name__)

# Directory names never walked when collecting .gitignore files or
# repository content — shared with fs_utils.walk_files() so the two
# walks stay in sync rather than drifting as separate copies. Reading a
# .gitignore from inside e.g. a vendored dependency or the .git dir would
# apply rules the repository owner never wrote.
SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "vendor",
        ".tox",
        ".venv",
        "ENV",
        "venv",
        "__pycache__",
        "dist",
        "build",
    }
)

_GITIGNORE_NAME = ".gitignore"


class GitignoreMatcher:
    """Matches paths against the ``.gitignore`` files found under a root.

    Each ``.gitignore`` is compiled into a ``pathspec`` spec anchored at
    the directory that contains it. A path is ignored if any spec whose
    anchor is an ancestor of (or equal to) the path matches it, evaluated
    relative to that anchor.
    """

    def __init__(
        self, root: Path, specs: list[tuple[Path, pathspec.PathSpec]]
    ) -> None:
        """Store the repo root and the ordered (anchor, spec) pairs.

        Args:
            root: Repository root the paths are resolved against.
            specs: ``(anchor_dir, compiled_spec)`` pairs, one per
                ``.gitignore`` file, where ``anchor_dir`` is the directory
                that declared the patterns.
        """
        self._root = root
        # Shallowest anchor first so parent .gitignore rules are evaluated
        # before nested ones (matching git's ordering).
        self._specs = sorted(specs, key=lambda pair: len(pair[0].parts))

    def is_ignored(self, path: Path, *, is_dir: bool | None = None) -> bool:
        """Return True if ``path`` is ignored by any applicable spec.

        Args:
            path: Absolute or root-relative path to test.
            is_dir: Whether the path is a directory. When None it is
                inferred from the filesystem (``path.is_dir()``); pass it
                explicitly to avoid a stat call or when the path does not
                exist on disk.

        Returns:
            True if any ``.gitignore`` under an ancestor directory ignores
            the path.
        """
        abs_path = path if path.is_absolute() else self._root / path
        try:
            rel_to_root = abs_path.relative_to(self._root)
        except ValueError:
            # Outside the repo root — not our concern, treat as not ignored.
            return False

        if is_dir is None:
            is_dir = abs_path.is_dir()

        for anchor, spec in self._specs:
            try:
                rel_to_anchor = abs_path.relative_to(anchor)
            except ValueError:
                continue  # This spec does not govern the path.
            candidate = rel_to_anchor.as_posix()
            if is_dir:
                # pathspec matches directory-only patterns (``build/``)
                # against a trailing-slash form.
                candidate += "/"
            if spec.match_file(candidate):
                logger.debug(
                    "Path %s ignored by .gitignore in %s.",
                    rel_to_root,
                    anchor.relative_to(self._root) or ".",
                )
                return True
        return False


def load_gitignore_matcher(
    root: Path, *, respect_gitignore: bool
) -> GitignoreMatcher | None:
    """Build a matcher from the ``.gitignore`` files under ``root``.

    Args:
        root: Repository root to scan.
        respect_gitignore: When False, returns None so callers skip all
            gitignore filtering (a None matcher means "ignore nothing").

    Returns:
        A ``GitignoreMatcher``, or None when disabled or no ``.gitignore``
        files exist under the root.
    """
    if not respect_gitignore:
        return None

    specs = _collect_specs(root)
    if not specs:
        logger.debug("No .gitignore files found under %s.", root)
        return None

    logger.info("Respecting %d .gitignore file(s) under %s.", len(specs), root)
    return GitignoreMatcher(root, specs)


def _collect_specs(root: Path) -> list[tuple[Path, pathspec.PathSpec]]:
    """Find and compile every ``.gitignore`` under ``root``.

    Args:
        root: Repository root to scan.

    Returns:
        ``(anchor_dir, compiled_spec)`` pairs — one per readable,
        non-empty ``.gitignore`` file outside the skip-scan directories.
    """
    specs: list[tuple[Path, pathspec.PathSpec]] = []
    for gitignore in _find_gitignore_files(root):
        spec = _compile_gitignore(gitignore)
        if spec is not None:
            specs.append((gitignore.parent, spec))
    return specs


def _find_gitignore_files(root: Path) -> list[Path]:
    """Return every ``.gitignore`` under root, skipping scan-excluded dirs.

    Args:
        root: Repository root to scan.

    Returns:
        List of paths to ``.gitignore`` files.
    """
    found: list[Path] = []
    _walk_for_gitignore(root, found)
    return found


def _walk_for_gitignore(directory: Path, found: list[Path]) -> None:
    """Recursively collect ``.gitignore`` files into ``found``.

    Args:
        directory: Directory to scan.
        found: Accumulator appended to in place.
    """
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        logger.warning("Could not read directory %s: %s", directory, exc)
        return

    for entry in entries:
        if entry.is_dir():
            if entry.name not in SKIP_DIRS and not entry.is_symlink():
                _walk_for_gitignore(entry, found)
        elif entry.name == _GITIGNORE_NAME and entry.is_file():
            found.append(entry)


def _compile_gitignore(path: Path) -> pathspec.PathSpec | None:
    """Compile one ``.gitignore`` file into a pathspec spec.

    Args:
        path: Path to the ``.gitignore`` file.

    Returns:
        A compiled ``GitIgnoreSpec``, or None if the file is empty or
        unreadable.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None

    spec = pathspec.GitIgnoreSpec.from_lines(lines)
    # Blank lines and comments compile to null patterns (include is None);
    # a file with only those governs nothing.
    if not any(p.include is not None for p in spec.patterns):
        return None
    return spec
