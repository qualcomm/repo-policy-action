# Copyright (c) Qualcomm Technologies, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""Tests for rules/file_type.py (binary prohibition)."""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.gitignore import load_gitignore_matcher
from src.reporter import Reporter
from src.rules.file_type import run


def _write_elf_header(path: Path) -> None:
    """Write a minimal but valid ELF64 header to simulate a Linux binary."""
    elf = bytearray(64)
    elf[0:4] = b"\x7fELF"
    elf[4] = 2  # ELFCLASS64
    elf[5] = 1  # ELFDATA2LSB
    elf[6] = 1  # EI_VERSION=1
    elf[16:18] = b"\x02\x00"  # ET_EXEC
    elf[18:20] = b"\x3e\x00"  # EM_X86_64
    elf[20:24] = b"\x01\x00\x00\x00"  # e_version=1
    path.write_bytes(bytes(elf))


def _write_pe_header(path: Path) -> None:
    """Write a minimal but valid MZ/PE header to simulate a Windows binary.

    A bare "MZ" stub (no e_lfanew pointer or PE signature) is classified
    inconsistently across libmagic database versions — it's ambiguous with
    plain MS-DOS executables and can even fall through as text/plain. Include
    the DOS stub's e_lfanew field plus a real "PE\0\0" + COFF header so this
    is unambiguously recognized as a PE binary everywhere.
    """
    dos_header = bytearray(64)
    dos_header[0:2] = b"MZ"
    struct.pack_into("<I", dos_header, 0x3C, len(dos_header))  # e_lfanew
    pe_signature = b"PE\x00\x00"
    coff_header = struct.pack(
        "<HHIIIHH", 0x8664, 0, 0, 0, 0, 0, 0x0102
    )  # machine=x86_64, characteristics=EXECUTABLE_IMAGE|LARGE_ADDRESS_AWARE
    path.write_bytes(bytes(dos_header) + pe_signature + coff_header)


class TestFileTypeRule(unittest.TestCase):
    def setUp(self):
        self.reporter = Reporter()

    def _make_repo(self, files: dict[str, bytes | str]) -> str:
        tmp = tempfile.mkdtemp()
        for rel_path, content in files.items():
            path = Path(tmp) / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        return tmp

    def test_passes_when_no_binaries(self):
        """Rule passes for a repo containing only text files."""
        repo = self._make_repo(
            {"src/main.py": "print('hello')", "README.md": "# Hi"}
        )
        result = run(
            repo_path=repo,
            rule_name="binaries-not-present",
            level="warning",
            options={},
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_fails_on_pe_binary(self):
        """Rule fails when a PE (Windows) executable is present."""
        repo = self._make_repo({})
        _write_pe_header(Path(repo) / "tool.exe")
        result = run(
            repo_path=repo,
            rule_name="binaries-not-present",
            level="warning",
            options={},
            reporter=self.reporter,
        )
        self.assertFalse(result.passed)

    def test_fails_on_elf_binary(self):
        """Rule fails when an ELF binary is present."""
        repo = self._make_repo({})
        _write_elf_header(Path(repo) / "tool")
        result = run(
            repo_path=repo,
            rule_name="binaries-not-present",
            level="warning",
            options={},
            reporter=self.reporter,
        )
        self.assertFalse(result.passed)

    def test_skips_node_modules(self):
        """Binaries inside node_modules/ are not flagged."""
        repo = self._make_repo({})
        binary_path = Path(repo) / "node_modules" / "native" / "binding.node"
        binary_path.parent.mkdir(parents=True)
        _write_elf_header(binary_path)
        result = run(
            repo_path=repo,
            rule_name="binaries-not-present",
            level="warning",
            options={},
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_allowed_extension_skipped(self):
        """Font files (.woff2) are in the allow-list and not flagged."""
        # woff2 files often have a binary signature; they should be skipped
        repo = self._make_repo(
            {"assets/font.woff2": b"\x77\x4f\x46\x32" + b"\x00" * 12}
        )
        result = run(
            repo_path=repo,
            rule_name="binaries-not-present",
            level="warning",
            options={},
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)

    def test_passes_explicitly_when_no_binaries(self):
        """Pass result is explicitly returned (not just no failure) for
        a clean repo — ensures the rule always produces a RuleResult."""
        repo = self._make_repo({"src/main.py": "print('hello')"})
        result = run(
            repo_path=repo,
            rule_name="binaries-not-present",
            level="warning",
            options={},
            reporter=self.reporter,
        )
        self.assertTrue(result.passed)
        self.assertIsNotNone(result.message)

    def test_gitignored_binary_not_flagged(self):
        """A binary matched by .gitignore is not flagged."""
        repo = self._make_repo({".gitignore": "out/\n"})
        binary_path = Path(repo) / "out" / "tool"
        binary_path.parent.mkdir(parents=True)
        _write_elf_header(binary_path)
        matcher = load_gitignore_matcher(Path(repo), respect_gitignore=True)
        result = run(
            repo_path=repo,
            rule_name="binaries-not-present",
            level="warning",
            options={},
            reporter=self.reporter,
            ignore_matcher=matcher,
        )
        self.assertTrue(result.passed)

    def test_gitignored_binary_flagged_when_not_respected(self):
        """Without a matcher, a gitignored binary is still flagged."""
        repo = self._make_repo({".gitignore": "out/\n"})
        binary_path = Path(repo) / "out" / "tool"
        binary_path.parent.mkdir(parents=True)
        _write_elf_header(binary_path)
        result = run(
            repo_path=repo,
            rule_name="binaries-not-present",
            level="warning",
            options={},
            reporter=self.reporter,
            ignore_matcher=None,
        )
        self.assertFalse(result.passed)


if __name__ == "__main__":
    unittest.main()
