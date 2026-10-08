"""Reject high-confidence secrets in the Git index before a commit.

The index is the source of truth: unstaged edits and untracked files are not
part of the pending commit. Diagnostics never include matched values.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import PurePosixPath


MAX_SCAN_BYTES = 10 * 1024 * 1024
CONTENT_RULES = (
    ("private key", re.compile(rb"-----BEGIN [^-\r\n]*PRIVATE KEY-----")),
    ("GitHub token", re.compile(rb"(?<![A-Za-z0-9_])(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}")),
    ("GitHub fine-grained token", re.compile(rb"github_pat_[A-Za-z0-9_]{20,}")),
    ("API secret token", re.compile(rb"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{20,}")),
    ("AWS access key", re.compile(rb"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![0-9A-Z])")),
    ("Google API key", re.compile(rb"AIza[0-9A-Za-z_-]{35}")),
    ("Slack token", re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}")),
)


def _git(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True,
    ).stdout


def _sensitive_name(path: str) -> bool:
    name = PurePosixPath(path).name.casefold()
    if name in {".env.example", ".env.sample", ".env.template"}:
        return False
    return (
        name == ".env"
        or name.startswith(".env.")
        or name in {
            ".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519",
            "credentials.json", "secrets.json",
        }
        or name.endswith((".pem", ".p12", ".pfx", ".key", ".keystore"))
    )


def _index_entries() -> dict[bytes, tuple[str, str]]:
    entries: dict[bytes, tuple[str, str]] = {}
    for record in _git("ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        header, path = record.split(b"\t", 1)
        mode, object_id, stage = header.decode("ascii").split(" ")
        if stage == "0":
            entries[path] = (mode, object_id)
    return entries


def main() -> int:
    try:
        staged = _git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR")
        entries = _index_entries()
        problems: list[str] = []
        for raw_path in filter(None, staged.split(b"\0")):
            path = raw_path.decode("utf-8", errors="surrogateescape")
            label = repr(path)
            if _sensitive_name(path):
                problems.append(f"{label}: sensitive filename")
                continue
            entry = entries.get(raw_path)
            if entry is None:
                problems.append(f"{label}: could not read staged index entry")
                continue
            mode, object_id = entry
            if mode == "160000":  # Git submodule pointer, not a file blob.
                continue
            size = int(_git("cat-file", "-s", object_id))
            if size > MAX_SCAN_BYTES:
                problems.append(f"{label}: staged file exceeds scan limit")
                continue
            content = _git("cat-file", "blob", object_id)
            for rule_name, pattern in CONTENT_RULES:
                match = pattern.search(content)
                if match:
                    line = content.count(b"\n", 0, match.start()) + 1
                    problems.append(f"{label}:{line}: {rule_name}")
        if problems:
            print("Commit blocked by staged secret check:", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1
        return 0
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"Staged secret check could not run: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
