# Copyright (c) Qualcomm Technologies, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""Tests for rules/file_existence.py."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.gitignore import load_gitignore_matcher
from src.reporter import Reporter
from src.rules.file_existence import run


class TestFileExistenceRule(unittest.TestCase):
    def setUp(self):
        self.reporter = Reporter()

    def _make_repo(self, files: list[str]) -> str:
        tmp = tempfile.mkdtemp()
        for rel_path in files:
            path = Path(tmp) / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        return tmp

    def test_passes_when_file_exists(self):
        """Rule passes when a matching file is found at the repo root."""
        repo = self._make_repo(["LICENSE"])
        result = run(
            repo_path=repo,
            rule_name="license-file-exists",
            level="error",
            options={"globsAny": ["LICENSE", "COPYING", "NOTICE"]},
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_fails_when_no_file_exists(self):
        """Rule fails when no matching file is found."""
        repo = self._make_repo([])
        result = run(
            repo_path=repo,
            rule_name="license-file-exists",
            level="error",
            options={"globsAny": ["LICENSE", "COPYING", "NOTICE"]},
            reporter=self.reporter,
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.level, "error")

    def test_matches_nested_file(self):
        """Rule passes when the file is in a subdirectory."""
        repo = self._make_repo([".github/CONTRIBUTING.md"])
        result = run(
            repo_path=repo,
            rule_name="contributing-file-exists",
            level="warning",
            options={
                "globsAny": ["CONTRIBUTING*"],
                "dirs": ["", "docs", ".github"],
            },
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_level_is_preserved_on_failure(self):
        """Failing result preserves the configured level."""
        repo = self._make_repo([])
        result = run(
            repo_path=repo,
            rule_name="changelog-file-exists",
            level="warning",
            options={"globsAny": ["CHANGELOG*"]},
            reporter=self.reporter,
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.level, "warning")

    def test_no_globs_returns_pass(self):
        """Rule with no patterns configured passes (skipped)."""
        repo = self._make_repo([])
        result = run(
            repo_path=repo,
            rule_name="empty-rule",
            level="error",
            options={},
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_passes_case_insensitive_with_nocase(self):
        """nocase=True finds a file regardless of case."""
        repo = self._make_repo(["license"])  # lowercase filename
        result = run(
            repo_path=repo,
            rule_name="license-file-exists",
            level="error",
            options={"globsAny": ["LICENSE"], "nocase": True},
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_fails_case_sensitive_without_nocase(self):
        """Without nocase, mismatched case is not found."""
        repo = self._make_repo(["license"])  # lowercase, pattern is upper
        result = run(
            repo_path=repo,
            rule_name="license-file-exists",
            level="error",
            options={"globsAny": ["LICENSE"], "nocase": False},
            reporter=self.reporter,
        )
        self.assertFalse(result.passed)

    def test_custom_fail_message_used_on_failure(self):
        """fail-message overrides the default failure message."""
        repo = self._make_repo([])
        result = run(
            repo_path=repo,
            rule_name="license-file-exists",
            level="error",
            options={
                "globsAny": ["LICENSE"],
                "fail-message": "Add a LICENSE file.",
            },
            reporter=self.reporter,
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.message, "Add a LICENSE file.")

    def test_broken_symlink_not_counted_as_match(self):
        """A broken symlink does not satisfy file-existence."""
        import os

        repo = self._make_repo([])
        symlink = Path(repo) / "LICENSE"
        symlink.symlink_to("/nonexistent/target")
        result = run(
            repo_path=repo,
            rule_name="license-file-exists",
            level="error",
            options={"globsAny": ["LICENSE"]},
            reporter=self.reporter,
        )
        self.assertFalse(result.passed)

    def test_nocase_wildcard_pattern_finds_file(self):
        """nocase=True with a wildcard pattern (e.g. README*) finds files."""
        repo = self._make_repo(["README.md"])
        result = run(
            repo_path=repo,
            rule_name="readme-file-exists",
            level="error",
            options={"globsAny": ["README*"], "nocase": True},
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_nocase_wildcard_finds_lowercase_readme(self):
        """nocase=True with README* pattern finds readme.rst (lowercase)."""
        repo = self._make_repo(["readme.rst"])
        result = run(
            repo_path=repo,
            rule_name="readme-file-exists",
            level="error",
            options={"globsAny": ["README*"], "nocase": True},
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_brace_expansion_single_pattern(self):
        """Brace expression in glob is expanded before searching."""
        repo = self._make_repo([".github/CONTRIBUTING.md"])
        result = run(
            repo_path=repo,
            rule_name="contributing-file-exists",
            level="warning",
            options={
                "globsAny": ["{docs/,.github/,}CONTRIBUTING*"],
                "nocase": True,
            },
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_brace_expansion_root_alternative(self):
        """The empty alternative in a brace expression matches the repo root."""
        repo = self._make_repo(["CODE_OF_CONDUCT.md"])
        result = run(
            repo_path=repo,
            rule_name="code-of-conduct-file-exists",
            level="warning",
            options={
                "globsAny": [
                    "{docs/,.github/,}CODE_OF_CONDUCT*",
                ],
                "nocase": True,
            },
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_gitignored_file_does_not_satisfy_rule(self):
        """A file matched by .gitignore does not count as existing."""
        repo = self._make_repo(["LICENSE"])
        (Path(repo) / ".gitignore").write_text("LICENSE\n")
        matcher = load_gitignore_matcher(Path(repo), respect_gitignore=True)
        result = run(
            repo_path=repo,
            rule_name="license-file-exists",
            level="error",
            options={"globsAny": ["LICENSE", "COPYING", "NOTICE"]},
            reporter=self.reporter,
            ignore_matcher=matcher,
        )
        self.assertFalse(result.passed)

    def test_gitignored_file_satisfies_rule_when_not_respected(self):
        """Without a matcher, a gitignored file still satisfies the rule."""
        repo = self._make_repo(["LICENSE"])
        (Path(repo) / ".gitignore").write_text("LICENSE\n")
        result = run(
            repo_path=repo,
            rule_name="license-file-exists",
            level="error",
            options={"globsAny": ["LICENSE", "COPYING", "NOTICE"]},
            reporter=self.reporter,
            ignore_matcher=None,
        )
        self.assertTrue(result.passed)


if __name__ == "__main__":
    unittest.main()
