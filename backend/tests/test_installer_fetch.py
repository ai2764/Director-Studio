"""installer/fetch.py: sha256-verified cache + safe archive extraction; installer notices helpers."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import zipfile

import pytest

INSTALLER = Path(__file__).resolve().parents[2] / "installer"
sys.path.insert(0, str(INSTALLER))

import fetch  # noqa: E402
import notices  # noqa: E402


def _tar(path: Path, members: list[tuple[str, bytes | None, str | None]]) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, data, link in members:
            info = tarfile.TarInfo(name)
            if link is not None:
                info.type, info.linkname = tarfile.SYMTYPE, link
                tar.addfile(info)
            else:
                info.size = len(data or b"")
                info.mode = 0o755
                tar.addfile(info, io.BytesIO(data or b""))
    return path


def test_extract_strips_and_filters(tmp_path):
    archive = _tar(tmp_path / "a.tar.gz", [
        ("pkg/bin/ffmpeg", b"x", None), ("pkg/bin/ffplay", b"y", None), ("pkg/LICENSE.txt", b"L", None),
        ("pkg/share/terminfo/E/eterm", b"t", None), ("pkg/bin/link", None, "ffmpeg"),
    ])
    out = tmp_path / "out"
    written = fetch.extract(archive, out, strip_components=1, include=["bin/ffmpeg", "LICENSE.txt"])
    assert sorted(written) == ["LICENSE.txt", "bin/ffmpeg"]
    assert (out / "bin" / "ffmpeg").stat().st_mode & 0o111
    out2 = tmp_path / "out2"
    written = fetch.extract(archive, out2, strip_components=1, exclude=["share/terminfo/*"])
    assert "share/terminfo/E/eterm" not in written and (out2 / "bin" / "link").is_symlink()


@pytest.mark.parametrize("members", [
    [("../evil", b"x", None)],
    [("pkg/../../evil", b"x", None)],
    [("/abs/evil", b"x", None)],
    [("pkg/link", None, "../../etc/passwd")],
])
def test_extract_rejects_escapes(tmp_path, members):
    archive = _tar(tmp_path / "bad.tar.gz", members)
    with pytest.raises(fetch.FetchError):
        fetch.extract(archive, tmp_path / "out", strip_components=0)
    assert not (tmp_path / "evil").exists()


def test_extract_zip_rejects_traversal(tmp_path):
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ok/a.txt", "a")
        zf.writestr("../evil.txt", "x")
    with pytest.raises(fetch.FetchError):
        fetch.extract(archive, tmp_path / "out")


def test_cached_download_verifies_sha256(tmp_path):
    payload = b"hello"
    src = tmp_path / "src.bin"
    src.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    cache = tmp_path / "cache"
    got = fetch.cached_download(src.as_uri(), digest, cache=cache)
    assert got.read_bytes() == payload
    with pytest.raises(fetch.FetchError):
        fetch.cached_download(src.as_uri(), "0" * 64, cache=tmp_path / "cache2")
    assert not list((tmp_path / "cache2").glob("*.part"))


def test_install_component_variant_excludes(tmp_path):
    archive = _tar(tmp_path / "ollama.tar.gz", [
        ("bin/ollama", b"o", None), ("lib/ollama/cuda_v12/libggml-cuda.so", b"c", None),
        ("lib/ollama/libggml-cpu.so", b"c", None),
    ])
    spec = {"url": archive.as_uri(), "sha256": fetch.sha256_of(archive), "strip_components": 0,
            "exclude_by_variant": {"cpu": ["lib/ollama/cuda_v12/*"]}}
    dest = tmp_path / "rt" / "ollama"
    fetch.install_component(spec, dest, cache=tmp_path / "c", variant="cpu")
    assert (dest / "bin" / "ollama").is_file() and (dest / "lib" / "ollama" / "libggml-cpu.so").is_file()
    assert not (dest / "lib" / "ollama" / "cuda_v12").exists()


def test_notices_scan_target_dir_and_node_modules(tmp_path):
    site = tmp_path / "comfy-tools"
    dist = site / "comfy_cli-1.20.0.dist-info"
    (dist / "licenses").mkdir(parents=True)
    (dist / "METADATA").write_text("Metadata-Version: 2.4\nName: comfy-cli\nVersion: 1.20.0\n"
                                   "License-Expression: GPL-3.0-only\n\nbody\n")
    (dist / "licenses" / "LICENSE").write_text("GNU GENERAL PUBLIC LICENSE")
    other = site / "foo-1.0.dist-info"
    other.mkdir()
    (other / "METADATA").write_text("Name: foo\nVersion: 1.0\nLicense: UNKNOWN\n"
                                    "Classifier: License :: OSI Approved :: MIT License\n\n")
    rows = {r["Name"]: r for r in notices.scan_site_dir(site, label="tools")}
    assert rows["comfy-cli"]["License"] == "GPL-3.0-only" and "GNU" in rows["comfy-cli"]["LicenseText"]
    assert rows["foo"]["License"] == "MIT License"

    nm = tmp_path / "node_modules"
    (nm / "a").mkdir(parents=True)
    (nm / "a" / "package.json").write_text(json.dumps({"name": "a", "version": "1.0.0", "license": "MIT"}))
    (nm / "a" / "LICENSE").write_text("MIT text")
    (nm / "a" / "dist").mkdir()
    (nm / "a" / "dist" / "package.json").write_text(json.dumps({"type": "module"}))
    (nm / "b").mkdir()
    (nm / "b" / "package.json").write_text(json.dumps({"name": "b", "version": "2.0.0",
                                                       "licenses": [{"type": "MIT"}, {"type": "Apache-2.0"}]}))
    rows = {r["Name"]: r for r in notices.scan_node_modules(nm, label="harness")}
    assert set(rows) == {"a", "b"}
    assert rows["a"]["LicenseText"] == "MIT text" and rows["b"]["License"] == "MIT OR Apache-2.0"


def test_licence_overrides_resolve_unknown_packages():
    fetched = []
    rows = [{"Name": "comfy_aimdo", "Version": "0.5.5", "License": "UNKNOWN", "LicenseText": "GPL text"},
            {"Name": "comfyui-frontend-package", "Version": "1.53.10", "License": "UNKNOWN", "LicenseText": "", "URL": "UNKNOWN"},
            {"Name": "cookiecutter", "Version": "2.7.1", "License": "UNKNOWN", "LicenseText": ""},
            {"Name": "other", "Version": "1", "License": "MIT", "LicenseText": "m"}]
    notices.apply_overrides(rows, fetch=lambda url: fetched.append(url) or "TEXT")
    by = {r["Name"]: r for r in rows}
    assert by["comfy_aimdo"]["License"] == "GPL-3.0" and by["comfy_aimdo"]["LicenseText"] == "GPL text"
    assert by["comfyui-frontend-package"]["License"] == "GPL-3.0"
    assert by["comfyui-frontend-package"]["URL"] == "https://github.com/Comfy-Org/ComfyUI_frontend"
    assert by["cookiecutter"]["License"] == "BSD-3-Clause"
    assert by["other"]["License"] == "MIT"
    assert fetched == ["https://raw.githubusercontent.com/Comfy-Org/ComfyUI_frontend/v1.53.10/LICENSE",
                       "https://raw.githubusercontent.com/cookiecutter/cookiecutter/v2.7.1/LICENSE"]
    header = "\n".join(notices.header_lines(False))
    assert "AGPL-3.0: comfy-mcp" in header and "LGPL" in header and "Apache" not in header


def test_optional_custom_node_components(monkeypatch):
    import build_linux

    names = [n["name"] for n in build_linux.selected_custom_nodes()]
    assert names == ["ComfyUI-KJNodes", "ComfyUI-H3-Motion-Context"]
    assert [n["name"] for n in build_linux.selected_custom_nodes(("h3-continuation",))] == ["ComfyUI-KJNodes"]
    with pytest.raises(SystemExit):
        build_linux.selected_custom_nodes(("no-such-component",))
    # packs without Python requirements contribute only a comment to the lock input
    fetched: list[str] = []
    monkeypatch.setattr(build_linux, "_raw", lambda url: fetched.append(url) or "pkg==1\n")
    text = build_linux.comfy_requirements_in()
    assert "# ComfyUI-H3-Motion-Context (5335715abe54c1a9bfbe3494da29aae3e8635ce3): no Python requirements" in text
    assert not any("H3-Motion-Context" in url for url in fetched)
    assert any("ComfyUI-KJNodes" in url for url in fetched)


def test_vendor_runtime_notice_lists_nvidia_and_msvc_files(tmp_path):
    torch_lib = tmp_path / "runtime" / "comfy-python" / "Lib" / "site-packages" / "torch" / "lib"
    ollama = tmp_path / "runtime" / "ollama" / "lib" / "ollama" / "cuda_v12"
    linux = tmp_path / "runtime" / "comfy-python" / "lib" / "nvidia" / "cudnn" / "lib"
    for folder, names in ((torch_lib, ["cudnn64_9.dll", "c10.dll", "msvcp140.dll"]),
                          (ollama, ["cublas64_12.dll", "ggml-cuda.dll"]), (linux, ["libcudnn.so.9"])):
        folder.mkdir(parents=True)
        for name in names:
            (folder / name).write_bytes(b"x")
    text = "\n".join(notices.vendor_runtime_notice(tmp_path))
    assert "NVIDIA CUDA runtime components" in text and "docs.nvidia.com/cuda/eula" in text
    assert "torch/lib: cudnn64_9.dll" in text and "cuda_v12: cublas64_12.dll" in text and "libcudnn.so.9" in text
    assert "c10.dll" not in text and "ggml-cuda.dll" not in text
    assert "Microsoft Visual C++ runtime" in text and "msvcp140.dll" in text
    assert notices.vendor_runtime_notice(tmp_path / "empty") == []
