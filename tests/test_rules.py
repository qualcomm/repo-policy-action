# Copyright (c) Qualcomm Technologies, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""Integration-style tests for the rule dispatcher (rules/__init__.py)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.language_detector import detect_languages
from src.reporter import Reporter
from src.rules import run_all_rules

_MINIMAL_CONFIG = {
    "version": 2,
    "rules": {
        "license-file-exists": {
            "level": "error",
            "rule": {
                "type": "file-existence",
                "options": {"globsAny": ["LICENSE", "COPYING"]},
            },
        },
        "readme-file-exists": {
            "level": "error",
            "rule": {
                "type": "file-existence",
                "options": {"globsAny": ["README*"]},
            },
        },
        "disabled-rule": {
            "level": "off",
            "rule": {
                "type": "file-existence",
                "options": {"globsAny": ["NONEXISTENT"]},
            },
        },
    },
}

_LANGUAGE_CONDITIONAL_CONFIG = {
    "version": 2,
    "rules": {
        "rust-cargo-exists": {
            "level": "error",
            "where": ["linguist=Rust"],
            "rule": {
                "type": "file-existence",
                "options": {"globsAny": ["Cargo.toml"]},
            },
        },
    },
}


class TestRunAllRules(unittest.TestCase):
    def setUp(self):
        self.reporter = Reporter()

    def _make_repo(self, files: list[str]) -> str:
        tmp = tempfile.mkdtemp()
        for rel_path in files:
            path = Path(tmp) / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        return tmp

    def test_passing_repo(self):
        """All rules pass for a repo with all required files."""
        repo = self._make_repo(["LICENSE", "README.md"])
        languages = detect_languages(repo)
        results = run_all_rules(
            repo_path=repo,
            config=_MINIMAL_CONFIG,
            languages=languages,
            reporter=self.reporter,
        )
        self.assertTrue(all(r.passed for r in results))

    def test_failing_repo(self):
        """Error-level rules fail for a repo missing required files."""
        repo = self._make_repo([])
        languages = detect_languages(repo)
        results = run_all_rules(
            repo_path=repo,
            config=_MINIMAL_CONFIG,
            languages=languages,
            reporter=self.reporter,
        )
        failed = [r for r in results if not r.passed]
        self.assertEqual(len(failed), 2)

    def test_off_rules_are_skipped(self):
        """Rules with level='off' are not evaluated."""
        repo = self._make_repo(["LICENSE", "README.md"])
        languages = detect_languages(repo)
        results = run_all_rules(
            repo_path=repo,
            config=_MINIMAL_CONFIG,
            languages=languages,
            reporter=self.reporter,
        )
        rule_names = [r.rule_name for r in results]
        self.assertNotIn("disabled-rule", rule_names)

    def test_language_conditional_skipped_when_not_detected(self):
        """Language-conditional rules are skipped for non-matching repos."""
        repo = self._make_repo(["src/main.py", "src/utils.py"])
        languages = detect_languages(repo)
        results = run_all_rules(
            repo_path=repo,
            config=_LANGUAGE_CONDITIONAL_CONFIG,
            languages=languages,
            reporter=self.reporter,
        )
        # No Rust detected → rule should not have been evaluated
        self.assertEqual(len(results), 0)

    def test_language_conditional_applied_when_detected(self):
        """Language-conditional rules run when the language is detected."""
        repo = self._make_repo(["src/main.rs", "src/lib.rs"])
        languages = detect_languages(repo)
        results = run_all_rules(
            repo_path=repo,
            config=_LANGUAGE_CONDITIONAL_CONFIG,
            languages=languages,
            reporter=self.reporter,
        )
        # Rust detected, Cargo.toml missing → rule should fail
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].passed)


_WILDCARD_AXIOM_CONFIG = {
    "version": 2,
    "rules": {
        "any-language-check": {
            "level": "warning",
            "where": ["linguist=*"],
            "rule": {
                "type": "file-existence",
                "options": {"globsAny": ["README*"]},
            },
        },
    },
}

_UNKNOWN_AXIOM_CONFIG = {
    "version": 2,
    "rules": {
        "unknown-axiom-rule": {
            "level": "warning",
            "where": ["unknown_axiom=somevalue"],
            "rule": {
                "type": "file-existence",
                "options": {"globsAny": ["README*"]},
            },
        },
    },
}


class TestAxiomEdgeCases(unittest.TestCase):
    def setUp(self):
        self.reporter = Reporter()

    def _make_repo(self, files: list[str]) -> str:
        tmp = tempfile.mkdtemp()
        for rel_path in files:
            path = Path(tmp) / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        return tmp

    def test_wildcard_axiom_runs_when_any_language_detected(self):
        """linguist=* runs the rule when any language is detected."""
        repo = self._make_repo(["src/main.py", "src/utils.py", "README.md"])
        languages = detect_languages(repo)
        results = run_all_rules(
            repo_path=repo,
            config=_WILDCARD_AXIOM_CONFIG,
            languages=languages,
            reporter=self.reporter,
        )
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].passed)

    def test_wildcard_axiom_skipped_when_no_language_detected(self):
        """linguist=* skips the rule when no language is detected."""
        repo = self._make_repo(["README.md", "config.yaml"])
        languages = detect_languages(repo)
        results = run_all_rules(
            repo_path=repo,
            config=_WILDCARD_AXIOM_CONFIG,
            languages=languages,
            reporter=self.reporter,
        )
        self.assertEqual(len(results), 0)

    def test_unknown_axiom_key_does_not_prevent_rule_running(self):
        """An unrecognised axiom key in where is logged and skipped,
        not treated as a failing condition."""
        repo = self._make_repo(["README.md"])
        languages = detect_languages(repo)
        results = run_all_rules(
            repo_path=repo,
            config=_UNKNOWN_AXIOM_CONFIG,
            languages=languages,
            reporter=self.reporter,
        )
        # Unknown axiom is skipped (non-blocking); rule still runs.
        self.assertEqual(len(results), 1)


_LANGUAGE_AXIOM_CONFIG = {
    "version": 2,
    "rules": {
        "js-package-metadata": {
            "level": "warning",
            "where": ["language=javascript"],
            "rule": {
                "type": "file-existence",
                "options": {"globsAny": ["package.json"]},
            },
        },
    },
}


_FILE_TYPE_EXCLUSION_CONFIG = {
    "version": 2,
    "rules": {
        "binaries-not-present": {
            "level": "warning",
            "rule": {
                "type": "file-type-exclusion",
                "options": {
                    "type": ["**/*.exe", "**/*.dll", "!node_modules/**"]
                },
            },
        },
    },
}


class TestLanguageAxiomAlias(unittest.TestCase):
    def setUp(self):
        self.reporter = Reporter()

    def _make_repo(self, files: list[str]) -> str:
        tmp = tempfile.mkdtemp()
        for rel_path in files:
            path = Path(tmp) / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        return tmp

    def test_language_axiom_skips_when_language_not_detected(self):
        """language=javascript skips the rule when no JS files are present."""
        repo = self._make_repo(["src/main.py", "README.md"])
        languages = detect_languages(repo)
        results = run_all_rules(
            repo_path=repo,
            config=_LANGUAGE_AXIOM_CONFIG,
            languages=languages,
            reporter=self.reporter,
        )
        self.assertEqual(len(results), 0)

    def test_language_axiom_runs_when_language_detected(self):
        """language=javascript runs the rule when JS files are present."""
        repo = self._make_repo(["src/index.js", "src/utils.js", "package.json"])
        languages = detect_languages(repo)
        results = run_all_rules(
            repo_path=repo,
            config=_LANGUAGE_AXIOM_CONFIG,
            languages=languages,
            reporter=self.reporter,
        )
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].passed)

    def test_language_axiom_fails_when_file_missing(self):
        """language=javascript + JS detected + package.json absent → fails."""
        repo = self._make_repo(["src/index.js", "src/utils.js"])
        languages = detect_languages(repo)
        results = run_all_rules(
            repo_path=repo,
            config=_LANGUAGE_AXIOM_CONFIG,
            languages=languages,
            reporter=self.reporter,
        )
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].passed)

    def test_language_axiom_case_insensitive(self):
        """language= value is matched case-insensitively against detected names.

        The canonical repolint.json uses lowercase ('language=javascript')
        while the language detector emits title-case ('JavaScript').  Both
        must resolve to the same outcome.
        """
        # Config uses lower-case; detector will emit 'JavaScript'.
        repo = self._make_repo(["src/index.js", "src/utils.js", "package.json"])
        languages = detect_languages(repo)
        # Confirm detector emits title-case so the test is meaningful.
        self.assertIn("JavaScript", languages["languages"])

        results = run_all_rules(
            repo_path=repo,
            config=_LANGUAGE_AXIOM_CONFIG,
            languages=languages,
            reporter=self.reporter,
        )
        # Rule should run (lowercase axiom matched title-case detection) and pass.
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].passed)

    def test_linguist_axiom_case_insensitive(self):
        """linguist= value is also matched case-insensitively."""
        config = {
            "version": 2,
            "rules": {
                "rust-check": {
                    "level": "warning",
                    "where": [
                        "linguist=rust"
                    ],  # lowercase, detector emits 'Rust'
                    "rule": {
                        "type": "file-existence",
                        "options": {"globsAny": ["Cargo.toml"]},
                    },
                },
            },
        }
        repo = self._make_repo(["src/main.rs", "src/lib.rs", "Cargo.toml"])
        languages = detect_languages(repo)
        self.assertIn("Rust", languages["languages"])

        results = run_all_rules(
            repo_path=repo,
            config=config,
            languages=languages,
            reporter=self.reporter,
        )
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].passed)


class TestFileTypeExclusionAlias(unittest.TestCase):
    def setUp(self):
        self.reporter = Reporter()

    def _make_repo(self, files: list[str]) -> str:
        tmp = tempfile.mkdtemp()
        for rel_path in files:
            path = Path(tmp) / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        return tmp

    def test_file_type_exclusion_type_is_dispatched(self):
        """file-type-exclusion rule type is routed to the binary checker."""
        repo = self._make_repo(["src/main.py", "README.md"])
        languages = detect_languages(repo)
        results = run_all_rules(
            repo_path=repo,
            config=_FILE_TYPE_EXCLUSION_CONFIG,
            languages=languages,
            reporter=self.reporter,
        )
        # No actual binaries → rule passes.
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].passed)


_MULTI_FILE_CONTENT_CONFIG = {
    "version": 2,
    "rules": {
        "copyright-header-check": {
            "level": "error",
            "rule": {
                "type": "file-contents",
                "options": {
                    "globsAll": ["*.py"],
                    "content": "Copyright",
                },
            },
        },
    },
}


class TestMultipleFailuresFlattened(unittest.TestCase):
    """Verify that RuleResultList failures are flattened into run_all_rules results."""

    def setUp(self):
        self.reporter = Reporter()

    def _make_repo(self, files: dict[str, str]) -> str:
        tmp = tempfile.mkdtemp()
        for rel_path, content in files.items():
            path = Path(tmp) / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return tmp

    def test_multiple_failures_flattened_into_results(self):
        """When a rule returns multiple failures (RuleResultList), they are
        all flattened into the results list returned by run_all_rules."""
        repo = self._make_repo(
            {
                "file1.py": "print('hello')",
                "file2.py": "print('world')",
                "file3.py": "# Copyright Qualcomm\nprint('ok')",
            }
        )
        languages = detect_languages(repo)
        results = run_all_rules(
            repo_path=repo,
            config=_MULTI_FILE_CONTENT_CONFIG,
            languages=languages,
            reporter=self.reporter,
        )
        # Two files are missing the copyright header → 2 failures in results
        failed = [r for r in results if not r.passed]
        self.assertEqual(len(failed), 2)
        paths = {r.file_path for r in failed}
        self.assertIn("file1.py", paths)
        self.assertIn("file2.py", paths)
        self.assertNotIn("file3.py", paths)


if __name__ == "__main__":
    unittest.main()
