from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "stage_windows_comfy.py"
CONFIG = REPO_ROOT / "packaging" / "windows-comfy-runtime.json"

spec = importlib.util.spec_from_file_location("stage_windows_comfy", SCRIPT)
assert spec is not None and spec.loader is not None
stager = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = stager
spec.loader.exec_module(stager)


def _python_zip(path: Path, *, unsafe: bool = False) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("python.exe", b"private-python")
        archive.writestr("python313.dll", b"python-dll")
        archive.writestr("python313.zip", b"stdlib")
        archive.writestr("python313._pth", "python313.zip\n.\n")
        archive.writestr("LICENSE.txt", "Python license")
        archive.writestr("_ssl.pyd", b"ssl-extension")
        archive.writestr("libcrypto-3.dll", b"crypto-runtime")
        archive.writestr("pythonw.exe", b"not-required")
        if unsafe:
            archive.writestr("../escape.txt", "escape")
    return path


def _config_for(archive: Path):
    return stager.RuntimeConfig(
        platform="windows",
        arch="x64",
        version="3.13.14",
        archive="python-3.13.14-embed-amd64.zip",
        url="https://example.invalid/python.zip",
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        pip_version="25.1.1",
        pip_wheel="pip-25.1.1-py3-none-any.whl",
        pip_url="https://example.invalid/pip.whl",
        pip_sha256="1" * 64,
        comfy_mcp_version="0.10.0",
        comfy_cli_version="1.20.0",
    )


def test_runtime_config_pins_python_and_comfy_packages() -> None:
    config = stager.load_runtime_config(CONFIG)

    assert config.platform == "windows"
    assert config.arch == "x64"
    assert config.version == "3.13.14"
    assert config.archive == "python-3.13.14-embed-amd64.zip"
    assert config.url == (
        "https://www.python.org/ftp/python/3.13.14/"
        "python-3.13.14-embed-amd64.zip"
    )
    assert config.sha256 == (
        "90b4e5b9898b72d744650524bff92377"
        "c367f44bd5fbd09e3148656c080ad907"
    )
    assert config.pip_version == "25.1.1"
    assert config.pip_wheel == "pip-25.1.1-py3-none-any.whl"
    assert config.pip_sha256 == (
        "2913a38a2abf4ea6b64ab507bd9e967f"
        "3b53dc1ede74b01b0931e1ce548751af"
    )
    assert config.comfy_mcp_version == "0.10.0"
    assert config.comfy_cli_version == "1.20.0"


def test_python_extraction_configures_private_site_packages(tmp_path: Path) -> None:
    archive = _python_zip(tmp_path / "python.zip")
    destination = tmp_path / "package"

    stager.extract_python_runtime(archive, destination, _config_for(archive))

    runtime = destination / "runtime" / "python"
    assert (runtime / "python.exe").read_bytes() == b"private-python"
    assert (runtime / "python313.dll").is_file()
    assert (runtime / "python313.zip").is_file()
    assert (runtime / "_ssl.pyd").read_bytes() == b"ssl-extension"
    assert (runtime / "libcrypto-3.dll").read_bytes() == b"crypto-runtime"
    assert (runtime / "python313._pth").read_text(encoding="utf-8") == (
        "python313.zip\n.\nLib/site-packages\n"
        "../../data/tools/comfy/site-packages\nimport site\n"
    )
    sitecustomize = runtime / "Lib" / "site-packages" / "sitecustomize.py"
    assert sitecustomize.is_file()
    assert "site.addsitedir" in sitecustomize.read_text(encoding="utf-8")
    assert "data" in sitecustomize.read_text(encoding="utf-8")
    assert not (runtime / "pythonw.exe").exists()
    assert (destination / "THIRD_PARTY_LICENSES" / "python.txt").read_text(
        encoding="utf-8"
    ) == "Python license"


def test_python_extraction_rejects_checksum_mismatch(tmp_path: Path) -> None:
    archive = _python_zip(tmp_path / "python.zip")
    config = _config_for(archive)
    bad = config.__class__(**{**config.__dict__, "sha256": "0" * 64})

    with pytest.raises(stager.StageError, match="checksum"):
        stager.extract_python_runtime(archive, tmp_path / "package", bad)


def test_python_extraction_rejects_path_traversal(tmp_path: Path) -> None:
    archive = _python_zip(tmp_path / "python.zip", unsafe=True)

    with pytest.raises(stager.StageError, match="unsafe archive member"):
        stager.extract_python_runtime(
            archive, tmp_path / "package", _config_for(archive)
        )


def test_python_extraction_rejects_missing_runtime_file(tmp_path: Path) -> None:
    archive = tmp_path / "python.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("python.exe", b"python")

    with pytest.raises(stager.StageError, match="missing"):
        stager.extract_python_runtime(
            archive, tmp_path / "package", _config_for(archive)
        )


def test_direct_requirements_are_exact() -> None:
    assert (REPO_ROOT / "portable-tools-requirements.txt").read_text(
        encoding="utf-8"
    ).splitlines() == [
        "comfy-cli==1.20.0",
        "comfy-mcp==0.10.0",
    ]


def test_stage_packages_rejects_unhashed_requirement(tmp_path: Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text("comfy-mcp==0.10.0\n", encoding="utf-8")

    with pytest.raises(stager.StageError, match="hash"):
        stager.validate_requirements_lock(lock, _config_for(_python_zip(tmp_path / "p.zip")))


def _pip_wheel(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("pip/__init__.py", "__version__ = '25.1.1'\n")
        archive.writestr("pip-25.1.1.dist-info/METADATA", "Name: pip\nVersion: 25.1.1\n")
        archive.writestr("pip-25.1.1.dist-info/licenses/LICENSE.txt", "MIT pip license")
    return path


def test_extract_pip_bootstrap_and_write_runtime_manifest(tmp_path: Path) -> None:
    destination = tmp_path / "package"
    wheel = _pip_wheel(tmp_path / "pip.whl")
    config = _config_for(_python_zip(tmp_path / "python.zip"))
    config = config.__class__(
        **{**config.__dict__, "pip_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
    )
    lock = tmp_path / "requirements.lock"
    lock.write_text(
        "comfy-cli==1.20.0 --hash=sha256:" + "a" * 64 + "\n"
        "comfy-mcp==0.10.0 --hash=sha256:" + "b" * 64 + "\n",
        encoding="utf-8",
    )

    stager.extract_pip_runtime(wheel, destination, config)
    stager.write_bootstrap_metadata(destination, lock, config)

    runtime = destination / "runtime"
    assert (runtime / "python" / "Lib" / "site-packages" / "pip" / "__init__.py").is_file()
    assert (destination / "THIRD_PARTY_LICENSES" / "pip.txt").read_text(
        encoding="utf-8"
    ) == "MIT pip license"
    assert (runtime / "comfy-requirements.lock").read_bytes() == lock.read_bytes()
    manifest = json.loads((runtime / "comfy-bootstrap.json").read_text(encoding="utf-8"))
    assert manifest == {
        "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
        "packages": {"comfy-cli": "1.20.0", "comfy-mcp": "0.10.0"},
    }
    assert not (runtime / "python" / "Lib" / "site-packages" / "comfy_mcp").exists()
    assert not (runtime / "python" / "Lib" / "site-packages" / "comfy_cli").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows executable launcher only")
def test_comfy_launcher_uses_adjacent_private_python(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime" / "python"
    runtime.mkdir(parents=True)
    stager.write_comfy_launcher(runtime)

    launcher = runtime / "comfy.exe"
    assert launcher.is_file()
    assert b"#!python.exe" in launcher.read_bytes()


def test_stage_windows_comfy_orchestrates_supplied_offline_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / "packaging").mkdir(parents=True)
    lock = repo / "packaging" / "windows-comfy-requirements.lock"
    lock.write_text("locked", encoding="utf-8")
    destination = tmp_path / "package"
    archive = tmp_path / "python.zip"
    archive.write_bytes(b"archive")
    pip_wheel = tmp_path / "pip.whl"
    pip_wheel.write_bytes(b"pip")
    calls: list[tuple[object, ...]] = []
    config = _config_for(_python_zip(tmp_path / "config.zip"))

    monkeypatch.setattr(stager, "load_runtime_config", lambda: config)
    monkeypatch.setattr(
        stager,
        "extract_python_runtime",
        lambda archive_path, package, loaded: calls.append(
            ("extract", archive_path, package, loaded)
        ),
    )
    monkeypatch.setattr(
        stager, "extract_pip_runtime", lambda wheel, package, loaded: calls.append(
            ("pip", wheel, package, loaded)
        )
    )
    monkeypatch.setattr(
        stager, "write_comfy_launcher", lambda runtime: calls.append(("launcher", runtime))
    )
    monkeypatch.setattr(
        stager, "write_bootstrap_metadata", lambda package, lock_path, loaded: calls.append(
            ("metadata", package, lock_path, loaded)
        )
    )

    stager.stage_windows_comfy(
        repo,
        destination,
        python_archive=archive,
        pip_wheel=pip_wheel,
    )

    assert calls[0] == ("extract", archive, destination, config)
    assert calls[1] == ("pip", pip_wheel, destination, config)
    assert calls[-1] == ("metadata", destination, lock, config)
