"""Behavior checks for the staged-content commit guard."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCANNER = Path(__file__).resolve().parents[1] / "check_staged_secrets.py"


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


class StagedSecretCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        git(self.root, "init", "-q")

    def scan(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCANNER)],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_blocks_staged_token_without_printing_its_value(self) -> None:
        token = "ghp_" + "A" * 36
        (self.root / "settings.py").write_text(f"TOKEN = '{token}'\n")
        git(self.root, "add", "settings.py")

        result = self.scan()

        self.assertEqual(result.returncode, 1)
        self.assertIn("settings.py", result.stderr)
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_reads_staged_blob_instead_of_unstaged_worktree_edit(self) -> None:
        target = self.root / "settings.py"
        target.write_text("TOKEN = 'example'\n")
        git(self.root, "add", "settings.py")
        token = "ghp_" + "A" * 36
        target.write_text(f"TOKEN = '{token}'\n")

        self.assertEqual(self.scan().returncode, 0)

    def test_blocks_sensitive_filename_even_when_forced_into_index(self) -> None:
        (self.root / ".env").write_text("MODE=dev\n")
        git(self.root, "add", "-f", ".env")

        result = self.scan()

        self.assertEqual(result.returncode, 1)
        self.assertIn(".env", result.stderr)

    def test_allows_example_file_and_placeholders(self) -> None:
        (self.root / ".env.example").write_text("API_KEY=your-key-here\n")
        git(self.root, "add", ".env.example")

        self.assertEqual(self.scan().returncode, 0)


if __name__ == "__main__":
    unittest.main()
