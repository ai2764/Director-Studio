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
        git(self.root, "config", "user.name", "Example Developer")
        git(self.root, "config", "user.email", "developer@example.invalid")

    def scan(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCANNER), *args],
            cwd=self.root,
            text=True,
            input=stdin,
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

    def commit(self, message: str = "Test commit") -> str:
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", message)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()

    def test_blocks_private_runtime_file(self) -> None:
        target = self.root / ".run" / "chat.jsonl"
        target.parent.mkdir()
        target.write_text('{"role":"user","content":"Example private conversation"}\n')
        git(self.root, "add", ".")

        self.assertEqual(self.scan().returncode, 1)

    def test_history_catches_secret_removed_by_a_later_commit(self) -> None:
        target = self.root / "settings.py"
        target.write_text("MODE = 'example'\n")
        base = self.commit()
        token = "ghp_" + "A" * 36
        target.write_text(f"TOKEN = '{token}'\n")
        secret_commit = self.commit()
        target.write_text("TOKEN = 'example'\n")
        self.commit()

        result = self.scan("--range", f"{base}..HEAD")

        self.assertEqual(result.returncode, 1)
        self.assertIn(secret_commit[:12], result.stderr)
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_history_checks_messages_even_for_empty_commits(self) -> None:
        (self.root / "README.md").write_text("Example\n")
        base = self.commit()
        token = "ghp_" + "A" * 36
        git(self.root, "commit", "--allow-empty", "-qm", f"Example {token}")

        result = self.scan("--range", f"{base}..HEAD")

        self.assertEqual(result.returncode, 1)
        self.assertIn("commit message", result.stderr)
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_pre_push_new_branch_checks_every_unpublished_commit(self) -> None:
        target = self.root / "settings.py"
        target.write_text("MODE = 'example'\n")
        base = self.commit()
        git(self.root, "update-ref", "refs/remotes/origin/main", base)
        target.write_text("TOKEN = '" + "ghp_" + "A" * 36 + "'\n")
        self.commit()
        target.write_text("TOKEN = 'example'\n")
        head = self.commit()

        result = self.scan("--pre-push", stdin=f"refs/heads/example {head} refs/heads/example {'0' * 40}\n")

        self.assertEqual(result.returncode, 1)

    def test_pre_push_deletion_does_not_scan_a_nonexistent_commit(self) -> None:
        result = self.scan("--pre-push", stdin=f"refs/heads/example {'0' * 40} refs/heads/example {'a' * 40}\n")
        self.assertEqual(result.returncode, 0)

    def test_pre_push_with_no_updates_is_allowed(self) -> None:
        self.assertEqual(self.scan("--pre-push", stdin="\n").returncode, 0)

    def test_invalid_revision_cannot_report_success(self) -> None:
        self.assertEqual(self.scan("--range", "missing..HEAD").returncode, 2)

    def test_clean_history_is_allowed(self) -> None:
        target = self.root / "settings.py"
        target.write_text("MODE = 'example'\n")
        base = self.commit()
        target.write_text("MODE = 'test'\n")
        self.commit()

        self.assertEqual(self.scan("--range", f"{base}..HEAD").returncode, 0)

    def test_commit_message_is_checked_before_it_enters_history(self) -> None:
        token = "ghp_" + "A" * 36
        message = self.root / "message.txt"
        message.write_text(f"Example {token}\n")

        result = self.scan("--commit-message", str(message))

        self.assertEqual(result.returncode, 1)
        self.assertIn("commit message", result.stderr)
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_existing_remote_branch_scan_ignores_already_published_commits(self) -> None:
        target = self.root / "settings.py"
        target.write_text("TOKEN = '" + "ghp_" + "A" * 36 + "'\n")
        base = self.commit()
        (self.root / "README.md").write_text("Example\n")
        head = self.commit()

        result = self.scan("--pre-push", stdin=f"refs/heads/example {head} refs/heads/example {base}\n")

        self.assertEqual(result.returncode, 0)

    def test_merge_commit_scans_its_new_file_snapshot(self) -> None:
        (self.root / "README.md").write_text("Example\n")
        base = self.commit()
        git(self.root, "checkout", "-qb", "side")
        (self.root / "side.py").write_text("MODE = 'example'\n")
        self.commit()
        git(self.root, "checkout", "-qb", "target", base)
        git(self.root, "merge", "--no-commit", "--no-ff", "side")
        (self.root / "settings.py").write_text("TOKEN = '" + "ghp_" + "A" * 36 + "'\n")
        self.commit("Merge example")

        self.assertEqual(self.scan("--range", f"{base}..HEAD").returncode, 1)

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
