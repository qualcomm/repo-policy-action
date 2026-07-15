# Copyright (c) Qualcomm Technologies, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""Tests for language_detector.py."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.gitignore import load_gitignore_matcher
from src.language_detector import detect_languages


class TestDetectLanguages(unittest.TestCase):
    def _make_repo(self, files: list[str]) -> str:
        """Create a temporary directory with the given (empty) files."""
        tmp = tempfile.mkdtemp()
        for rel_path in files:
            path = Path(tmp) / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        return tmp

    def test_detects_python(self):
        """Python is detected when ≥2 .py files are present."""
        repo = self._make_repo(["src/foo.py", "src/bar.py"])
        result = detect_languages(repo)
        self.assertIn("Python", result["languages"])

    def test_below_threshold_not_detected(self):
        """A single .py file does not trigger Python detection."""
        repo = self._make_repo(["src/only_one.py"])
        result = detect_languages(repo)
        self.assertNotIn("Python", result["languages"])

    def test_detects_rust(self):
        """Rust is detected when ≥2 .rs files are present."""
        repo = self._make_repo(["src/main.rs", "src/lib.rs"])
        result = detect_languages(repo)
        self.assertIn("Rust", result["languages"])

    def test_detects_multiple_languages(self):
        """Multiple languages can be detected simultaneously."""
        repo = self._make_repo(
            [
                "src/main.py",
                "src/utils.py",
                "web/app.js",
                "web/index.js",
            ]
        )
        result = detect_languages(repo)
        self.assertIn("Python", result["languages"])
        self.assertIn("JavaScript", result["languages"])

    def test_detects_npm_packager(self):
        """npm packager detected when package.json is present."""
        repo = self._make_repo(["package.json"])
        result = detect_languages(repo)
        self.assertIn("npm", result["packagers"])

    def test_detects_cargo_packager(self):
        """cargo packager detected when Cargo.toml is present."""
        repo = self._make_repo(["Cargo.toml"])
        result = detect_languages(repo)
        self.assertIn("cargo", result["packagers"])

    def test_skips_node_modules(self):
        """Files inside node_modules/ are ignored."""
        repo = self._make_repo(
            [
                "node_modules/dep/index.js",
                "node_modules/dep/utils.js",
                "node_modules/dep/main.js",
            ]
        )
        result = detect_languages(repo)
        self.assertNotIn("JavaScript", result["languages"])

    def test_skips_git_directory(self):
        """Files inside .git/ are ignored."""
        repo = self._make_repo(
            [".git/hooks/pre-commit.py", ".git/hooks/post-commit.py"]
        )
        result = detect_languages(repo)
        self.assertNotIn("Python", result["languages"])

    def test_empty_repo(self):
        """Empty repository returns empty language and packager sets."""
        repo = self._make_repo([])
        result = detect_languages(repo)
        self.assertEqual(result["languages"], set())
        self.assertEqual(result["packagers"], set())

    def test_gitignored_files_excluded(self):
        """Files matched by .gitignore are excluded from detection."""
        repo = self._make_repo(["src/main.py", "src/util.py"])
        (Path(repo) / ".gitignore").write_text("src/\n")
        matcher = load_gitignore_matcher(Path(repo), respect_gitignore=True)
        result = detect_languages(repo, matcher)
        self.assertNotIn("Python", result["languages"])

    def test_gitignored_files_included_without_matcher(self):
        """Without a matcher, gitignored files still count (opt-out)."""
        repo = self._make_repo(["src/main.py", "src/util.py"])
        (Path(repo) / ".gitignore").write_text("src/\n")
        result = detect_languages(repo, None)
        self.assertIn("Python", result["languages"])


if __name__ == "__main__":
    unittest.main()
