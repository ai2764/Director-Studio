"""Exercise the Windows llama-swap launcher through its public CLI."""

from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "start-llama-swap.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
WINDOWS_POWERSHELL = shutil.which("powershell.exe")
LOCAL_EXE = Path(r"D:\DirectorStudio-llama-swap-eval\llama-swap\llama-swap.exe")


def run_launcher(*args: str, shell: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [shell or POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(LAUNCHER), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )


@pytest.mark.skipif(sys.platform != "win32" or not POWERSHELL, reason="Windows PowerShell required")
def test_launcher_rejects_missing_config_without_creating_run_files(tmp_path):
    run_dir = tmp_path / "run"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    result = run_launcher(
        "-ExePath", sys.executable,
        "-ConfigPath", str(tmp_path / "missing.yaml"),
        "-RunDir", str(run_dir),
        "-Port", str(port),
    )
    assert result.returncode != 0
    assert "config" in (result.stdout + result.stderr).lower()
    assert not run_dir.exists()


@pytest.mark.skipif(sys.platform != "win32" or not POWERSHELL, reason="Windows PowerShell required")
def test_launcher_does_not_take_over_an_occupied_port(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("models: {}\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        result = run_launcher(
            "-ExePath", sys.executable,
            "-ConfigPath", str(config),
            "-RunDir", str(run_dir),
            "-Port", str(port),
        )
        assert result.returncode != 0
        assert "port" in (result.stdout + result.stderr).lower()
        assert not (run_dir / "llama-swap.pid").exists()
        assert listener.getsockname()[1] == port


@pytest.mark.skipif(
    sys.platform != "win32" or not POWERSHELL or not LOCAL_EXE.is_file(),
    reason="Local llama-swap executable unavailable",
)
def test_launcher_validates_then_starts_healthy_proxy(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("models: {}\n", encoding="utf-8")
    validation = run_launcher(
        "-ExePath", str(LOCAL_EXE), "-ConfigPath", str(config), "-ValidateOnly",
    )
    assert validation.returncode == 0, validation.stdout + validation.stderr

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    run_dir = tmp_path / "run"
    process_id = None
    try:
        started = run_launcher(
            "-ExePath", str(LOCAL_EXE),
            "-ConfigPath", str(config),
            "-RunDir", str(run_dir),
            "-Port", str(port),
        )
        assert started.returncode == 0, started.stdout + started.stderr
        process_id = int((run_dir / "llama-swap.pid").read_text().strip())
        assert (run_dir / "llama-swap.out.log").is_file()
        assert (run_dir / "llama-swap.err.log").is_file()
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            assert response.status == 200
        binding = subprocess.run(
            [POWERSHELL, "-NoProfile", "-Command",
             f"(Get-NetTCPConnection -LocalPort {port} -State Listen).LocalAddress"],
            capture_output=True, text=True, timeout=10,
        )
        assert binding.returncode == 0, binding.stderr
        assert set(binding.stdout.splitlines()) & {"0.0.0.0", "::"}
        before = {path.name: path.read_bytes() for path in run_dir.iterdir()}
        reused = run_launcher(
            "-ExePath", str(tmp_path / "missing.exe"),
            "-ConfigPath", str(tmp_path / "missing.yaml"),
            "-RunDir", str(run_dir),
            "-Port", str(port),
        )
        assert reused.returncode == 0, reused.stdout + reused.stderr
        assert "already running" in reused.stdout.lower()
        assert (run_dir / "llama-swap.pid").read_bytes() == before["llama-swap.pid"]
        # The existing proxy may append the health request to its own log.
        for name in ("llama-swap.out.log", "llama-swap.err.log"):
            assert (run_dir / name).read_bytes().startswith(before[name])
    finally:
        if process_id:
            subprocess.run(["taskkill", "/PID", str(process_id), "/T", "/F"], capture_output=True)


@pytest.mark.skipif(
    sys.platform != "win32" or not POWERSHELL or not LOCAL_EXE.is_file(),
    reason="Local llama-swap executable unavailable",
)
def test_launcher_reuses_healthy_loopback_only_proxy_without_stopping_it(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("models: {}\n", encoding="utf-8")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [str(LOCAL_EXE), "-config", str(config), "-listen", f"127.0.0.1:{port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    assert response.status == 200
                break
            except OSError:
                if time.monotonic() >= deadline:
                    pytest.fail("Fixture proxy did not become healthy")
                time.sleep(0.1)
        run_dir = tmp_path / "run"
        result = run_launcher(
            "-ExePath", str(LOCAL_EXE), "-ConfigPath", str(config),
            "-RunDir", str(run_dir), "-Port", str(port),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "loopback" in (result.stdout + result.stderr).lower()
        assert process.poll() is None
        assert not run_dir.exists()
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            assert response.status == 200
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.skipif(
    sys.platform != "win32" or not WINDOWS_POWERSHELL or not LOCAL_EXE.is_file(),
    reason="Windows PowerShell 5 and local llama-swap required",
)
def test_launcher_health_check_does_not_prompt_in_windows_powershell(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("models: {}\n", encoding="utf-8")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    run_dir = tmp_path / "run"
    process_id = None
    try:
        result = run_launcher(
            "-ExePath", str(LOCAL_EXE),
            "-ConfigPath", str(config),
            "-RunDir", str(run_dir),
            "-Port", str(port),
            shell=WINDOWS_POWERSHELL,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        process_id = int((run_dir / "llama-swap.pid").read_text().strip())
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            assert response.status == 200
    finally:
        if process_id:
            subprocess.run(["taskkill", "/PID", str(process_id), "/T", "/F"], capture_output=True)


@pytest.mark.skipif(
    sys.platform != "win32" or not POWERSHELL or not LOCAL_EXE.is_file(),
    reason="Local llama-swap executable unavailable",
)
def test_launcher_stops_its_proxy_when_health_check_fails(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("models: {}\n", encoding="utf-8")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    escaped_script = str(LAUNCHER).replace("'", "''")
    escaped_exe = str(LOCAL_EXE).replace("'", "''")
    escaped_config = str(config).replace("'", "''")
    escaped_run = str(tmp_path / "run").replace("'", "''")
    code = (
        "function Invoke-WebRequest { throw 'simulated health failure' }; "
        f"try {{ . '{escaped_script}' -ExePath '{escaped_exe}' "
        f"-ConfigPath '{escaped_config}' -RunDir '{escaped_run}' -Port {port}; "
        "exit 2 } catch { exit 0 }"
    )
    try:
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-Command", code],
            capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        with socket.socket() as check:
            check.settimeout(1)
            assert check.connect_ex(("127.0.0.1", port)) != 0
    finally:
        owner = subprocess.run(
            [POWERSHELL, "-NoProfile", "-Command",
             f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue).OwningProcess"],
            capture_output=True, text=True,
        ).stdout.strip()
        if owner.isdigit():
            subprocess.run(["taskkill", "/PID", owner, "/T", "/F"], capture_output=True)
