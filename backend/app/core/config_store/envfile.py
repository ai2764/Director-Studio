"""Fidelity-preserving ``.env`` reader/writer.

Design goals (see docs/config-ui-plan.md section 4.1):
- keep comments, blank lines and key order untouched;
- only rewrite the *last* active occurrence of a key (dotenv semantics:
  last one wins) and comment out earlier duplicates;
- append new keys under a managed marker section;
- atomic replace (tmp file in the same directory + fsync + os.replace),
  ``.env.bak`` backup, POSIX ``0600`` permissions, and an inter-process
  file lock in addition to the caller's in-process lock.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import shutil
import time
from typing import Iterator, Mapping

MANAGED_MARKER = "# --- Managed by Director Studio Settings ---"
_KEY_LINE = re.compile(r"^(?P<indent>\s*)(?P<export>export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<value>.*)$")
_SAFE_UNQUOTED = re.compile(r"^[A-Za-z0-9_./:@,+\-=~%*#!?&\[\]{}^]*$")


class EnvFileError(ValueError):
    pass


def revision_of(text: str | bytes | None) -> str:
    if text is None:
        return "missing"
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()[:16]


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _unquote(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] == "'":
        end = value.find("'", 1)
        return value[1:end] if end > 0 else value[1:]
    if value[0] == '"':
        out: list[str] = []
        i = 1
        while i < len(value):
            ch = value[i]
            if ch == "\\" and i + 1 < len(value):
                nxt = value[i + 1]
                out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, "\\" + nxt))
                i += 2
                continue
            if ch == '"':
                break
            out.append(ch)
            i += 1
        return "".join(out)
    # Unquoted: strip inline comments introduced by " #".
    hash_at = re.search(r"\s#", value)
    if hash_at:
        value = value[: hash_at.start()]
    return value.strip()


def parse(text: str | None) -> dict[str, str]:
    """Return active ``KEY -> value`` pairs (last occurrence wins)."""
    values: dict[str, str] = {}
    for line in (text or "").splitlines():
        match = _KEY_LINE.match(line)
        if match:
            values[match.group("key")] = _unquote(match.group("value"))
    return values


def format_value(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise EnvFileError("values must not contain newlines or NUL bytes")
    if value == "" or _SAFE_UNQUOTED.match(value) and not value.startswith("#"):
        return value
    if "'" not in value:
        # Single quotes: python-dotenv performs no escape or ${VAR} processing.
        return f"'{value}'"
    if "${" in value:
        raise EnvFileError("values containing both ' and ${ cannot be stored safely")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def apply_changes(text: str | None, changes: Mapping[str, str | None]) -> str:
    """Return new file text. ``None`` removes every active assignment of the key."""
    newline = "\r\n" if text and "\r\n" in text else "\n"
    lines = (text or "").splitlines()
    trailing_newline = bool(text) and (text.endswith("\n"))
    for key in changes:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise EnvFileError(f"invalid key: {key!r}")
    positions: dict[str, list[int]] = {}
    for idx, line in enumerate(lines):
        match = _KEY_LINE.match(line)
        if match and match.group("key") in changes:
            positions.setdefault(match.group("key"), []).append(idx)

    drop: set[int] = set()
    appended: list[str] = []
    for key, value in changes.items():
        found = positions.get(key, [])
        if value is None:
            drop.update(found)
            continue
        rendered = f"{key}={format_value(value)}"
        if found:
            last = found[-1]
            match = _KEY_LINE.match(lines[last])
            assert match is not None
            lines[last] = f"{match.group('indent')}{match.group('export') or ''}{rendered}"
            for earlier in found[:-1]:
                lines[earlier] = f"# (superseded by Director Studio Settings) {lines[earlier].lstrip()}"
        else:
            appended.append(rendered)

    out = [line for idx, line in enumerate(lines) if idx not in drop]
    if appended:
        if MANAGED_MARKER not in out:
            if out and out[-1].strip():
                out.append("")
            out.append(MANAGED_MARKER)
        out.extend(appended)
    result = newline.join(out)
    if out and (trailing_newline or appended or not text):
        result += newline
    return result


@contextmanager
def file_lock(path: Path, timeout: float = 10.0) -> Iterator[None]:
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":  # pragma: no cover - windows only
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise EnvFileError(f"timed out waiting for lock {lock_path}")
                time.sleep(0.05)
        yield
    finally:
        try:
            if os.name == "nt":  # pragma: no cover
                import msvcrt

                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _restrict(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, 0o600)


def atomic_write(path: Path, text: str, *, backup: bool = True) -> None:
    """Write ``text`` atomically. Caller should hold :func:`file_lock`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        bak = path.with_name(path.name + ".bak")
        shutil.copyfile(path, bak)
        _restrict(bak)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _restrict(tmp)
        last_error: OSError | None = None
        for _ in range(3):
            try:
                os.replace(tmp, path)
                break
            except PermissionError as exc:  # Windows: file held by an editor
                last_error = exc
                time.sleep(0.2)
        else:
            raise EnvFileError(f"could not replace {path}: {last_error}")
        _restrict(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
