from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "verify_bundled_comfy.py"
spec = importlib.util.spec_from_file_location("verify_bundled_comfy", SCRIPT)
assert spec is not None and spec.loader is not None
verifier = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verifier
spec.loader.exec_module(verifier)


def _fake_package(root: Path) -> Path:
    runtime = root / "runtime" / "python"
    (runtime / "Lib" / "site-packages" / "pip").mkdir(parents=True)
    (runtime / "Lib" / "site-packages" / "sitecustomize.py").write_text(
        "import site\n", encoding="utf-8"
    )
    (runtime / "python.exe").write_bytes(b"python")
    (runtime / "comfy.exe").write_bytes(b"comfy")
    lock = root / "runtime" / "comfy-requirements.lock"
    lock.write_text("locked\n", encoding="utf-8")
    (root / "runtime" / "comfy-bootstrap.json").write_text(
        json.dumps(
            {
                "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
                "packages": {"comfy-cli": "1.20.0", "comfy-mcp": "0.10.0"},
            }
        ),
        encoding="utf-8",
    )
    return root


def test_verifier_runs_only_bundled_pip_and_confirms_dependencies_are_absent(
    tmp_path: Path,
) -> None:
    package = _fake_package(tmp_path / "package")
    runtime = package / "runtime" / "python"
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command, **kwargs):
        calls.append(([str(item) for item in command], dict(kwargs["env"])))
        return subprocess.CompletedProcess(command, 0, stdout="pip 25.1.1", stderr="")

    verifier.verify_bundled_comfy(
        package,
        environ={
            "SystemRoot": r"C:\Windows",
            "PATH": r"C:\system-python",
            "PYTHONHOME": "bad-home",
            "PYTHONPATH": "bad-path",
        },
        runner=fake_run,
    )

    assert calls[0][0] == [str(runtime / "python.exe"), "-m", "pip", "--version"]
    assert calls[1][0][0] == str(runtime / "python.exe")
    assert "comfy_mcp" in calls[1][0][-1]
    child_env = calls[0][1]
    assert child_env["PATH"].split(os.pathsep)[0] == str(runtime)
    assert "PYTHONHOME" not in child_env
    assert "PYTHONPATH" not in child_env
    assert child_env["PYTHONDONTWRITEBYTECODE"] == "1"


@pytest.mark.parametrize("package_name", ["comfy_mcp", "comfy_cli"])
def test_verifier_rejects_first_launch_dependency_in_package(
    tmp_path: Path, package_name: str
) -> None:
    package = _fake_package(tmp_path / "package")
    forbidden = package / "runtime" / "python" / "Lib" / "site-packages" / package_name
    forbidden.mkdir()

    with pytest.raises(verifier.VerifyError, match="must not be bundled"):
        verifier.verify_bundled_comfy(package)


def test_verifier_rejects_bootstrap_lock_mismatch(tmp_path: Path) -> None:
    package = _fake_package(tmp_path / "package")
    (package / "runtime" / "comfy-requirements.lock").write_text(
        "changed\n", encoding="utf-8"
    )

    with pytest.raises(verifier.VerifyError, match="checksum"):
        verifier.verify_bundled_comfy(package)


def test_verifier_reports_bounded_subprocess_failure(tmp_path: Path) -> None:
    package = _fake_package(tmp_path / "package")

    def failed_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 7, stdout="", stderr="x" * 20000)

    with pytest.raises(verifier.VerifyError) as failure:
        verifier.verify_bundled_comfy(package, runner=failed_run)

    assert len(str(failure.value)) < 5000
