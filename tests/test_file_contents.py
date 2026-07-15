# Copyright (c) Qualcomm Technologies, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""Tests for rules/file_contents.py."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.gitignore import load_gitignore_matcher
from src.reporter import Reporter
from src.rules.file_contents import run


class TestFileContentsRule(unittest.TestCase):
    def setUp(self):
        self.reporter = Reporter()

    def _make_repo(self, files: dict[str, str]) -> str:
        """Create a temporary repo with files mapping path→content."""
        tmp = tempfile.mkdtemp()
        for rel_path, content in files.items():
            path = Path(tmp) / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return tmp

    def test_passes_when_pattern_found(self):
        """Rule passes when the pattern is found in a matched file."""
        repo = self._make_repo(
            {"README.md": "This project is released under the MIT license."}
        )
        result = run(
            repo_path=repo,
            rule_name="readme-references-license",
            level="error",
            options={
                "globsAll": ["README*"],
                "content": "license|notice",
                "flags": ["IGNORECASE"],
            },
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_fails_when_pattern_missing(self):
        """Rule fails when the pattern is absent from a matched file."""
        repo = self._make_repo({"README.md": "Hello world."})
        result = run(
            repo_path=repo,
            rule_name="readme-references-license",
            level="error",
            options={
                "globsAll": ["README*"],
                "content": "license|notice",
                "flags": ["IGNORECASE"],
            },
            reporter=self.reporter,
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.level, "error")

    def test_file_starts_with_line_count(self):
        """lineCount restricts the search to the first N lines."""
        content = "\n".join(
            ["# Copyright Qualcomm Technologies, Inc."] + ["code"] * 20
        )
        repo = self._make_repo({"src/main.py": content})
        result = run(
            repo_path=repo,
            rule_name="source-qualcomm-license-headers-exist",
            level="warning",
            options={
                "globsAll": ["src/*.py"],
                "content": "Qualcomm",
                "lineCount": 5,
            },
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_line_count_does_not_match_later_content(self):
        """lineCount prevents matching content beyond the first N lines."""
        content = "\n".join(["code"] * 10 + ["# Copyright Qualcomm"])
        repo = self._make_repo({"src/main.py": content})
        result = run(
            repo_path=repo,
            rule_name="source-qualcomm-license-headers-exist",
            level="warning",
            options={
                "globsAll": ["src/*.py"],
                "content": "Qualcomm",
                "lineCount": 5,
            },
            reporter=self.reporter,
        )
        self.assertFalse(result.passed)

    def test_no_matching_files_with_fail_on_non_existent(self):
        """fail-on-non-existent causes failure when no files match."""
        repo = self._make_repo({})
        result = run(
            repo_path=repo,
            rule_name="readme-references-license",
            level="error",
            options={
                "globsAll": ["README*"],
                "content": "license",
                "fail-on-non-existent": True,
            },
            reporter=self.reporter,
        )
        self.assertFalse(result.passed)

    def test_no_matching_files_without_flag_passes(self):
        """Without fail-on-non-existent, no matching files is a pass."""
        repo = self._make_repo({})
        result = run(
            repo_path=repo,
            rule_name="readme-references-license",
            level="error",
            options={
                "globsAll": ["README*"],
                "content": "license",
                "fail-on-non-existent": False,
            },
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_skips_node_modules(self):
        """Files inside node_modules/ are not checked."""
        repo = self._make_repo(
            {"node_modules/pkg/index.js": "// no copyright here"}
        )
        result = run(
            repo_path=repo,
            rule_name="source-qualcomm-license-headers-exist",
            level="warning",
            options={
                "globsAll": ["**/*.js"],
                "content": "Qualcomm",
            },
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_skip_paths_matching_extension(self):
        """Files with a skipped extension are not checked."""
        repo = self._make_repo(
            {
                "src/main.py": "# Copyright Qualcomm",
                "src/data.bin": "no copyright here",
            }
        )
        result = run(
            repo_path=repo,
            rule_name="source-qualcomm-license-headers-exist",
            level="warning",
            options={
                "globsAll": ["src/*"],
                "content": "Qualcomm",
                "skip-paths-matching": [".bin"],
            },
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_skip_paths_matching_regex(self):
        """Files whose path matches a skip regex are not checked."""
        repo = self._make_repo(
            {
                "src/main.py": "# Copyright Qualcomm",
                "src/generated/auto.py": "no copyright here",
            }
        )
        result = run(
            repo_path=repo,
            rule_name="source-qualcomm-license-headers-exist",
            level="warning",
            options={
                "globsAll": ["**/*.py"],
                "content": "Qualcomm",
                "skip-paths-matching": ["generated/"],
            },
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_skip_binary_files_option(self):
        """skip-binary-files skips files detected as binary by magic."""
        repo = self._make_repo({})
        # Write a minimal ELF64 header — libmagic identifies this as binary
        elf = bytearray(64)
        elf[0:4] = b"\x7fELF"
        elf[4] = 2
        elf[5] = 1
        elf[6] = 1
        elf[16:18] = b"\x02\x00"
        elf[18:20] = b"\x3e\x00"
        elf[20:24] = b"\x01\x00\x00\x00"
        (Path(repo) / "tool").write_bytes(bytes(elf))
        result = run(
            repo_path=repo,
            rule_name="source-qualcomm-license-headers-exist",
            level="warning",
            options={
                "globsAll": ["*"],
                "content": "Qualcomm",
                "skip-binary-files": True,
            },
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_broken_symlink_skipped_gracefully(self):
        """A broken symlink in the glob results is skipped, not raised."""
        repo = self._make_repo({"README.md": "license"})
        broken = Path(repo) / "EXTRA.md"
        broken.symlink_to("/nonexistent/path")
        result = run(
            repo_path=repo,
            rule_name="readme-references-license",
            level="error",
            options={
                "globsAll": ["*.md"],
                "content": "license",
            },
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_custom_fail_message_used_on_failure(self):
        """fail-message overrides the default failure message."""
        repo = self._make_repo({"README.md": "Hello world."})
        result = run(
            repo_path=repo,
            rule_name="readme-references-license",
            level="error",
            options={
                "globsAll": ["README*"],
                "content": "license",
                "fail-message": "README must reference the license.",
            },
            reporter=self.reporter,
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.message, "README must reference the license.")

    def test_globsall_all_files_must_match(self):
        """globsAll requires every matched file to contain the pattern."""
        repo = self._make_repo(
            {
                "src/good.py": "# Copyright Qualcomm",
                "src/bad.py": "print('hello')",
            }
        )
        result = run(
            repo_path=repo,
            rule_name="source-qualcomm-license-headers-exist",
            level="warning",
            options={
                "globsAll": ["src/*.py"],
                "content": "Qualcomm",
            },
            reporter=self.reporter,
        )
        self.assertFalse(result.passed)
        self.assertIn("bad.py", result.file_path or "")

    def test_patterns_list_all_must_match_passes(self):
        """repolint 'patterns' list uses AND semantics: all patterns must match.

        A file containing both Copyright and SPDX should pass.
        """
        repo = self._make_repo(
            {
                "src/main.py": (
                    "# Copyright (c) Qualcomm Technologies, Inc.\n"
                    "# SPDX-License-Identifier: BSD-3-Clause\n"
                )
            }
        )
        result = run(
            repo_path=repo,
            rule_name="source-license-headers-exist",
            level="error",
            options={
                "globsAll": ["src/*.py"],
                "patterns": [
                    "Copyright(\\s)*(\\(c\\)|©)?",
                    "SPDX-License-Identifier",
                ],
                "flags": "i",
                "lineCount": 5,
                "succeed-on-non-existent": True,
            },
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_patterns_list_and_semantics_fails_when_one_missing(self):
        """'patterns' AND semantics: file with only one pattern fails.

        This is the key regression: a file that contains only the SPDX line
        but not the Copyright notice must FAIL (not pass under OR semantics).
        """
        repo = self._make_repo(
            {"src/main.py": "# SPDX-License-Identifier: Apache-2.0\n"}
        )
        result = run(
            repo_path=repo,
            rule_name="source-license-headers-exist",
            level="error",
            options={
                "globsAll": ["src/*.py"],
                "patterns": [
                    "Copyright(\\s)*(\\(c\\)|©)?",
                    "SPDX-License-Identifier",
                ],
                "flags": "i",
                "lineCount": 5,
            },
            reporter=self.reporter,
        )
        self.assertFalse(result.passed)

    def test_patterns_list_fails_when_none_match(self):
        """'patterns' list fails when no pattern is found in any file."""
        repo = self._make_repo({"src/main.py": "print('hello')"})
        result = run(
            repo_path=repo,
            rule_name="source-license-headers-exist",
            level="error",
            options={
                "globsAll": ["src/*.py"],
                "patterns": [
                    "Copyright",
                    "SPDX-License-Identifier",
                ],
                "flags": "i",
                "lineCount": 5,
            },
            reporter=self.reporter,
        )
        self.assertFalse(result.passed)

    def test_flags_as_string_ignorecase(self):
        """flags='i' string (repolint format) enables case-insensitive match."""
        repo = self._make_repo({"README.md": "Licensed under Apache"})
        result = run(
            repo_path=repo,
            rule_name="readme-references-license",
            level="error",
            options={
                "globsAll": ["README*"],
                "content": "license",
                "flags": "i",
            },
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_succeed_on_non_existent_passes_when_no_files(self):
        """succeed-on-non-existent=True passes when no files match the glob."""
        repo = self._make_repo({})
        result = run(
            repo_path=repo,
            rule_name="source-license-headers-exist",
            level="error",
            options={
                "globsAll": ["**/*.py"],
                "content": "Copyright",
                "succeed-on-non-existent": True,
            },
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_skip_paths_matching_as_dict(self):
        """skip-paths-matching as a dict with 'patterns' key is handled."""
        repo = self._make_repo(
            {
                "src/main.py": "# Copyright Qualcomm",
                "build/generated.py": "no copyright here",
            }
        )
        result = run(
            repo_path=repo,
            rule_name="source-qualcomm-license-headers-exist",
            level="warning",
            options={
                "globsAll": ["**/*.py"],
                "content": "Qualcomm",
                "skip-paths-matching": {"patterns": ["build/"]},
            },
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_empty_files_are_skipped(self):
        """Zero-byte files are skipped, matching repolinter behaviour.

        An empty __init__.py cannot contain a copyright header but should
        not cause the rule to fail — repolinter skips them silently.
        """
        repo = self._make_repo(
            {
                "src/__init__.py": "",
                "src/main.py": (
                    "# Copyright (c) Qualcomm Technologies, Inc.\n"
                    "# SPDX-License-Identifier: BSD-3-Clause\n"
                ),
            }
        )
        result = run(
            repo_path=repo,
            rule_name="source-license-headers-exist",
            level="error",
            options={
                "globsAll": ["src/*.py"],
                "patterns": [
                    "Copyright(\\s)*(\\(c\\)|©)?",
                    "SPDX-License-Identifier",
                ],
                "flags": "i",
                "lineCount": 5,
            },
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_gitignored_file_excluded_from_content_check(self):
        """A gitignored file is not scanned, so its content can't fail."""
        repo = self._make_repo(
            {
                "README.md": "MIT license",
                "generated/report.md": "just some notes",
                ".gitignore": "generated/\n",
            }
        )
        matcher = load_gitignore_matcher(Path(repo), respect_gitignore=True)
        result = run(
            repo_path=repo,
            rule_name="all-md-reference-license",
            level="error",
            options={
                "globsAll": ["**/*.md"],
                "content": "license",
                "flags": ["IGNORECASE"],
            },
            reporter=self.reporter,
            ignore_matcher=matcher,
        )
        self.assertTrue(result.passed)

    def test_gitignored_file_scanned_when_not_respected(self):
        """Without a matcher, the gitignored file is scanned and fails."""
        repo = self._make_repo(
            {
                "README.md": "MIT license",
                "generated/report.md": "just some notes",
                ".gitignore": "generated/\n",
            }
        )
        result = run(
            repo_path=repo,
            rule_name="all-md-reference-license",
            level="error",
            options={
                "globsAll": ["**/*.md"],
                "content": "license",
                "flags": ["IGNORECASE"],
            },
            reporter=self.reporter,
            ignore_matcher=None,
        )
        self.assertFalse(result.passed)


if __name__ == "__main__":
    unittest.main()
