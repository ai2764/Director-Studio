"""Reject high-confidence secrets in the index or every new commit before a push.

The index is the source of truth: unstaged edits and untracked files are not
part of the pending commit. Diagnostics never include matched values.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath


MAX_SCAN_BYTES = 10 * 1024 * 1024
PRIVATE_RUNTIME_PREFIXES = (
    ".run/", "data/", "backend/data/", ".codex/", ".codex-remote-attachments/",
    "reports/", "docs/evaluations/",
)
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
    normalized = path.casefold()
    if normalized.startswith(PRIVATE_RUNTIME_PREFIXES):
        return True
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


def _content_problems(content: bytes, label: str) -> list[str]:
    problems = []
    for rule_name, pattern in CONTENT_RULES:
        match = pattern.search(content)
        if match:
            line = content.count(b"\n", 0, match.start()) + 1
            problems.append(f"{label}:{line}: {rule_name}")
    return problems


def _blob_problems(path: str, mode: str, object_id: str, *, commit: str = "") -> list[str]:
    label = f"{commit[:12]} {path!r}" if commit else repr(path)
    if _sensitive_name(path):
        return [f"{label}: sensitive filename or private runtime path"]
    if mode == "160000":  # Git submodule pointer, not a file blob.
        return []
    size = int(_git("cat-file", "-s", object_id))
    if size > MAX_SCAN_BYTES:
        return [f"{label}: file exceeds scan limit"]
    return _content_problems(_git("cat-file", "blob", object_id), label)


def _range_commits(revision_range: str) -> list[str]:
    parts = revision_range.split("..")
    if len(parts) != 2 or not all(parts):
        raise ValueError("Use a base..head commit range")
    commits = [_git("rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
               .decode("ascii").strip() for ref in parts]
    return _git("rev-list", "--reverse", f"{commits[0]}..{commits[1]}").decode("ascii").split()


def _push_commits() -> list[str]:
    commits: list[str] = []
    for line in sys.stdin:
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4:
            raise ValueError("Invalid pre-push update")
        _, local_id, _, remote_id = fields
        if not all(re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value)
                   for value in (local_id, remote_id)):
            raise ValueError("Invalid pre-push object ID")
        if set(local_id) == {"0"}:  # Deleting a remote reference.
            continue
        if set(remote_id) == {"0"}:
            # A new remote branch still exposes all its unpublished ancestors.
            new = _git("rev-list", "--reverse", local_id, "--not", "--remotes")
            commits.extend(new.decode("ascii").split())
        else:
            commits.extend(_range_commits(f"{remote_id}..{local_id}"))
    return list(dict.fromkeys(commits))


def _commit_problems(commit: str) -> list[str]:
    problems = _content_problems(_git("show", "-s", "--format=%B", commit),
                                 f"{commit[:12]} commit message")
    parent_list = _git("rev-list", "--parents", "-n", "1", commit).decode("ascii").split()
    if len(parent_list) > 1:
        changed = _git("diff", "--name-only", "-z", "--no-renames", "--diff-filter=AM",
                       parent_list[1], commit)
    else:
        changed = _git("diff-tree", "--root", "--no-commit-id", "--name-only", "-z",
                       "--no-renames", "--diff-filter=AM", "-r", commit)
    entries = {}
    for record in _git("ls-tree", "-r", "-z", commit).split(b"\0"):
        if record:
            header, path = record.split(b"\t", 1)
            mode, _, object_id = header.decode("ascii").split()
            entries[path] = (mode, object_id)
    for raw_path in filter(None, changed.split(b"\0")):
        path = raw_path.decode("utf-8", errors="surrogateescape")
        mode, object_id = entries[raw_path]
        problems.extend(_blob_problems(path, mode, object_id, commit=commit))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--range", dest="revision_range", help="Scan every commit in base..head, including messages")
    mode.add_argument("--pre-push", action="store_true", help="Read Git reference updates from stdin")
    mode.add_argument("--commit-message", type=Path, help="Check a pending Git commit message")
    args = parser.parse_args()
    try:
        problems: list[str] = []
        if args.commit_message:
            if args.commit_message.stat().st_size > MAX_SCAN_BYTES:
                problems.append("commit message: exceeds scan limit")
            else:
                problems.extend(_content_problems(args.commit_message.read_bytes(), "commit message"))
            summary = "commit message"
        elif args.revision_range or args.pre_push:
            commits = _range_commits(args.revision_range) if args.revision_range else _push_commits()
            for commit in commits:
                problems.extend(_commit_problems(commit))
            summary = f"{len(commits)} commits"
        else:
            staged = _git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR")
            entries = _index_entries()
            paths = list(filter(None, staged.split(b"\0")))
            for raw_path in paths:
                path = raw_path.decode("utf-8", errors="surrogateescape")
                entry = entries.get(raw_path)
                if entry is None:
                    problems.append(f"{path!r}: could not read staged index entry")
                    continue
                problems.extend(_blob_problems(path, *entry))
            summary = f"{len(paths)} staged files"
        if problems:
            print("Commit/push blocked by secret check:", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1
        print(f"Secret check passed: {summary}.")
        return 0
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as exc:
        print(f"Secret check could not run: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
