from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "launch.sh"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_text(path: Path, timeout: float = 10) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        time.sleep(0.05)
    pytest.fail(f"Timed out waiting for {path}")


def _base_env(bin_dir: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("DS_PORT", None)
    environment.pop("DS_STARTUP_TIMEOUT_SEC", None)
    environment["PATH"] = os.pathsep.join([str(bin_dir), environment["PATH"]])
    return environment


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process semantics")
def test_launcher_opens_healthy_ui_and_stops_child(tmp_path: Path) -> None:
    port = _free_port()
    package = tmp_path / "package with spaces"
    bin_dir = package / "bin"
    api_dir = package / "api"
    package.mkdir()
    bin_dir.mkdir()
    api_dir.mkdir()

    shutil.copy2(LAUNCHER, package / "launch.sh")
    (package / "launch.sh").chmod(0o755)
    (package / ".env").write_text(f'DS_PORT="{port}"\n', encoding="utf-8")
    (api_dir / "health").write_text("ok\n", encoding="utf-8")
    (package / "DirectorStudio").write_text(
        "#!/usr/bin/env bash\n"
        'exec python3 -m http.server "$DS_PORT" --bind 127.0.0.1\n',
        encoding="utf-8",
    )
    (package / "DirectorStudio").chmod(0o755)
    open_capture = tmp_path / "opened-url"
    (bin_dir / "xdg-open").write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$1" > "$OPEN_CAPTURE"\n',
        encoding="utf-8",
    )
    (bin_dir / "xdg-open").chmod(0o755)

    environment = _base_env(bin_dir)
    environment["OPEN_CAPTURE"] = str(open_capture)
    launcher = subprocess.Popen(
        [str(package / "launch.sh")],
        cwd=tmp_path,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert _wait_for_text(open_capture) == f"http://127.0.0.1:{port}"
    launcher.send_signal(signal.SIGTERM)
    assert launcher.wait(timeout=10) == 143
    assert not _can_connect(port)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process semantics")
def test_launcher_propagates_early_child_exit_without_opening_browser(
    tmp_path: Path,
) -> None:
    port = _free_port()
    package = tmp_path / "package"
    bin_dir = package / "bin"
    package.mkdir()
    bin_dir.mkdir()

    shutil.copy2(LAUNCHER, package / "launch.sh")
    (package / "launch.sh").chmod(0o755)
    (package / ".env").write_text(f"DS_PORT={port}\n", encoding="utf-8")
    (package / "DirectorStudio").write_text(
        "#!/usr/bin/env bash\nexit 23\n", encoding="utf-8"
    )
    (package / "DirectorStudio").chmod(0o755)
    open_capture = tmp_path / "opened-url"
    (bin_dir / "xdg-open").write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$1" > "$OPEN_CAPTURE"\n',
        encoding="utf-8",
    )
    (bin_dir / "xdg-open").chmod(0o755)

    environment = _base_env(bin_dir)
    environment["OPEN_CAPTURE"] = str(open_capture)
    environment["DS_STARTUP_TIMEOUT_SEC"] = "2"
    completed = subprocess.run(
        [str(package / "launch.sh")],
        cwd=package,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 23
    assert not open_capture.exists()


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process semantics")
def test_launcher_maps_pre_health_zero_exit_to_failure_without_opening_browser(
    tmp_path: Path,
) -> None:
    port = _free_port()
    package = tmp_path / "package with spaces"
    bin_dir = package / "bin"
    package.mkdir()
    bin_dir.mkdir()

    shutil.copy2(LAUNCHER, package / "launch.sh")
    (package / "launch.sh").chmod(0o755)
    (package / ".env").write_text(f"DS_PORT={port}\n", encoding="utf-8")
    (package / "DirectorStudio").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
    )
    (package / "DirectorStudio").chmod(0o755)
    open_capture = tmp_path / "opened-url"
    (bin_dir / "xdg-open").write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$1" > "$OPEN_CAPTURE"\n',
        encoding="utf-8",
    )
    (bin_dir / "xdg-open").chmod(0o755)

    environment = _base_env(bin_dir)
    environment["OPEN_CAPTURE"] = str(open_capture)
    environment["DS_STARTUP_TIMEOUT_SEC"] = "2"
    completed = subprocess.run(
        [str(package / "launch.sh")],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert not open_capture.exists()


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process semantics")
def test_launcher_timeout_kills_child_that_ignores_term(tmp_path: Path) -> None:
    package = tmp_path / "package"
    bin_dir = package / "bin"
    package.mkdir()
    bin_dir.mkdir()

    shutil.copy2(LAUNCHER, package / "launch.sh")
    (package / "launch.sh").chmod(0o755)
    child_pid = tmp_path / "child-pid"
    (package / "DirectorStudio").write_text(
        "#!/usr/bin/env bash\n"
        "trap '' TERM\n"
        'printf "%s\\n" "$$" > "$CHILD_PID_CAPTURE"\n'
        "while true; do sleep 1; done\n",
        encoding="utf-8",
    )
    (package / "DirectorStudio").chmod(0o755)

    environment = _base_env(bin_dir)
    environment["CHILD_PID_CAPTURE"] = str(child_pid)
    environment["DS_PORT"] = str(_free_port())
    environment["DS_STARTUP_TIMEOUT_SEC"] = "1"
    started = time.monotonic()
    completed = subprocess.run(
        [str(package / "launch.sh")],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=8,
    )

    pid = int(_wait_for_text(child_pid))
    assert completed.returncode == 1
    assert time.monotonic() - started < 7
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def _can_connect(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False
