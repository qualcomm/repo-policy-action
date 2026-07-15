# Copyright (c) Qualcomm Technologies, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""Tests for gitignore.py — the .gitignore-aware path matcher."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.gitignore import GitignoreMatcher, load_gitignore_matcher


class TestLoadGitignoreMatcher(unittest.TestCase):
    def _make_repo(self, files: dict[str, str]) -> Path:
        """Create a temp repo. Keys are relative paths, values file text."""
        tmp = Path(tempfile.mkdtemp())
        for rel_path, content in files.items():
            path = tmp / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return tmp

    def test_disabled_returns_none(self):
        """respect_gitignore=False yields no matcher."""
        repo = self._make_repo({".gitignore": "build/\n"})
        self.assertIsNone(load_gitignore_matcher(repo, respect_gitignore=False))

    def test_no_gitignore_returns_none(self):
        """A repo with no .gitignore yields no matcher."""
        repo = self._make_repo({"README.md": "hi\n"})
        self.assertIsNone(load_gitignore_matcher(repo, respect_gitignore=True))

    def test_empty_gitignore_returns_none(self):
        """A .gitignore with only blanks/comments yields no matcher."""
        repo = self._make_repo({".gitignore": "\n# just a comment\n\n"})
        self.assertIsNone(load_gitignore_matcher(repo, respect_gitignore=True))

    def test_matches_root_pattern(self):
        """A root .gitignore matches files and directories under root."""
        repo = self._make_repo({".gitignore": "build/\n*.log\n"})
        matcher = load_gitignore_matcher(repo, respect_gitignore=True)
        assert matcher is not None
        self.assertTrue(matcher.is_ignored(repo / "build", is_dir=True))
        self.assertTrue(
            matcher.is_ignored(repo / "build" / "x.o", is_dir=False)
        )
        self.assertTrue(matcher.is_ignored(repo / "a.log", is_dir=False))
        self.assertFalse(matcher.is_ignored(repo / "src.py", is_dir=False))

    def test_negation(self):
        """A negation pattern (!keep.log) re-includes an ignored file."""
        repo = self._make_repo({".gitignore": "*.log\n!keep.log\n"})
        matcher = load_gitignore_matcher(repo, respect_gitignore=True)
        assert matcher is not None
        self.assertTrue(matcher.is_ignored(repo / "a.log", is_dir=False))
        self.assertFalse(matcher.is_ignored(repo / "keep.log", is_dir=False))

    def test_nested_gitignore_scoped_to_its_directory(self):
        """A nested .gitignore only governs its own subtree."""
        repo = self._make_repo(
            {
                ".gitignore": "*.log\n",
                "sub/.gitignore": "secret.txt\n",
            }
        )
        matcher = load_gitignore_matcher(repo, respect_gitignore=True)
        assert matcher is not None
        # Nested rule applies within sub/.
        self.assertTrue(
            matcher.is_ignored(repo / "sub" / "secret.txt", is_dir=False)
        )
        # ...but not at the root.
        self.assertFalse(matcher.is_ignored(repo / "secret.txt", is_dir=False))
        # Root rule still applies inside the nested directory.
        self.assertTrue(
            matcher.is_ignored(repo / "sub" / "debug.log", is_dir=False)
        )

    def test_relative_path_input(self):
        """Root-relative paths are resolved against the repo root."""
        repo = self._make_repo({".gitignore": "build/\n"})
        matcher = load_gitignore_matcher(repo, respect_gitignore=True)
        assert matcher is not None
        self.assertTrue(matcher.is_ignored(Path("build/x.o"), is_dir=False))

    def test_path_outside_root_not_ignored(self):
        """A path outside the repo root is never reported as ignored."""
        repo = self._make_repo({".gitignore": "*\n"})
        matcher = load_gitignore_matcher(repo, respect_gitignore=True)
        assert matcher is not None
        self.assertFalse(matcher.is_ignored(Path("/etc/hosts"), is_dir=False))

    def test_scan_skips_vendored_gitignore(self):
        """A .gitignore inside node_modules/ is not loaded."""
        repo = self._make_repo(
            {
                "node_modules/dep/.gitignore": "*\n",
                "keep.py": "x = 1\n",
            }
        )
        # Only the vendored .gitignore exists, and it must be skipped, so
        # no matcher is produced.
        self.assertIsNone(load_gitignore_matcher(repo, respect_gitignore=True))

    def test_is_dir_inferred_from_disk(self):
        """When is_dir is omitted it is inferred from the filesystem."""
        repo = self._make_repo(
            {".gitignore": "build/\n", "build/x.o": "data\n"}
        )
        matcher = load_gitignore_matcher(repo, respect_gitignore=True)
        assert matcher is not None
        # build/ exists as a directory on disk; the directory-only pattern
        # should match without an explicit is_dir hint.
        self.assertTrue(matcher.is_ignored(repo / "build"))


class TestGitignoreMatcherConstruction(unittest.TestCase):
    def test_specs_sorted_shallowest_first(self):
        """Anchors are ordered parent-before-child."""
        root = Path("/repo")
        import pathspec  # local import; only needed for this test

        deep = (
            Path("/repo/a/b"),
            pathspec.GitIgnoreSpec.from_lines(["x"]),
        )
        shallow = (
            Path("/repo"),
            pathspec.GitIgnoreSpec.from_lines(["y"]),
        )
        matcher = GitignoreMatcher(root, [deep, shallow])
        anchors = [anchor for anchor, _ in matcher._specs]
        self.assertEqual(anchors[0], Path("/repo"))
        self.assertEqual(anchors[1], Path("/repo/a/b"))


if __name__ == "__main__":
    unittest.main()
