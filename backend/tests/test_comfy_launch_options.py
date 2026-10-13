from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.comfy import launch_options as lo

HELP = """usage: main.py [-h] [--listen [IP]] [--port PORT] [--highvram] [--novram] [--cpu]
  --disable-smart-memory  --reserve-vram RESERVE_VRAM --cuda-device DEVICE_ID
  --cache-none --disable-pinned-memory --fp8_e4m3fn-unet --fp8_e4m3fn-text-enc --bf16-vae
  --use-sage-attention --use-pytorch-cross-attention --fast [FAST ...] --preview-method
  --disable-all-custom-nodes --extra-model-paths-config --disable-auto-launch --deterministic
"""


def cfg(**kw):
    base = dict(comfy_vram_preset="auto", comfy_reserve_vram_gb=0, comfy_cuda_device="", comfy_ram_preset="auto",
                comfy_precision_preset="auto", comfy_attention="auto", comfy_unet_precision="auto",
                comfy_text_enc_precision="auto", comfy_vae_precision="auto", comfy_fast="",
                comfy_preview_method="default", comfy_troubleshoot="", comfy_dev_cpu=False, comfy_extra_args="",
                comfy_hf_endpoint="", comfy_http_proxy="")
    base.update(kw)
    return SimpleNamespace(**base)


def test_help_parsing():
    options = lo.parse_help_options(HELP)
    assert {"--listen", "--port", "--fast", "--fp8_e4m3fn-unet", "--disable-smart-memory"} <= options
    assert "-h" not in options


def test_auto_preset_is_minimal():
    built = lo.build_launch(cfg(), port=1234, extra_model_paths=Path("/x.yaml"))
    assert built.args == ["--listen", "127.0.0.1", "--port", "1234", "--disable-auto-launch",
                          "--extra-model-paths-config", "/x.yaml"]


@pytest.mark.parametrize("preset,expected", [
    ("high", ["--highvram"]),
    ("save", ["--disable-smart-memory", "--reserve-vram", "1.5"]),
    ("extreme", ["--novram"]),
])
def test_vram_presets(preset, expected):
    args = lo.build_launch(cfg(comfy_vram_preset=preset), port=1, extra_model_paths=None).args
    assert args[5:] == expected


def test_full_options_and_env():
    built = lo.build_launch(cfg(
        comfy_vram_preset="save", comfy_reserve_vram_gb=3, comfy_cuda_device="1", comfy_ram_preset="low",
        comfy_precision_preset="fp8", comfy_vae_precision="bf16", comfy_attention="sage", comfy_fast="fp16_accumulation",
        comfy_preview_method="taesd", comfy_troubleshoot="disable_all_custom_nodes", comfy_hf_endpoint="https://hf-mirror.com",
        comfy_http_proxy="http://127.0.0.1:7890", comfy_extra_args="--deterministic"),
        port=1, extra_model_paths=None, sage_available=True)
    args = built.args
    for flag in ("--disable-smart-memory", "--cuda-device", "--cache-none", "--disable-pinned-memory",
                 "--fp8_e4m3fn-unet", "--fp8_e4m3fn-text-enc", "--bf16-vae", "--use-sage-attention", "--fast",
                 "--preview-method", "--disable-all-custom-nodes", "--deterministic"):
        assert flag in args
    assert args[args.index("--reserve-vram") + 1] == "3"
    assert built.env["HF_ENDPOINT"] == "https://hf-mirror.com"
    assert built.env["HTTPS_PROXY"] == "http://127.0.0.1:7890"


def test_sage_only_when_importable():
    built = lo.build_launch(cfg(comfy_attention="sage"), port=1, extra_model_paths=None, sage_available=False)
    assert "--use-sage-attention" not in built.args and built.warnings


@pytest.mark.parametrize("extra", ["--listen 0.0.0.0", "--port 1", "--cpu", "--enable-cors-header", "--tls-keyfile k",
                                   "--auto-launch", "--multi-user", "--force-fp32", "--cpu-vae", "--windows-standalone-build",
                                   "--gpu-only", "-h", "--normalvram"])
def test_blacklist(extra):
    _args, errors = lo.validate_extra_args(extra, None)
    assert errors


def test_extra_args_validated_against_help():
    allowed = lo.parse_help_options(HELP)
    args, errors = lo.validate_extra_args("--deterministic --preview-method taesd", allowed)
    assert not errors and args == ["--deterministic", "--preview-method", "taesd"]
    _args, errors = lo.validate_extra_args("--made-up-flag", allowed)
    assert errors


def test_unsupported_generated_flags_are_filtered():
    built = lo.build_launch(cfg(comfy_vae_precision="fp32"), port=1, extra_model_paths=None,
                            allowed=lo.parse_help_options(HELP))
    assert "--fp32-vae" not in built.args and built.warnings


def test_dev_cpu_flag_and_invalid_inputs():
    assert "--cpu" in lo.build_launch(cfg(comfy_dev_cpu=True), port=1, extra_model_paths=None).args
    with pytest.raises(lo.LaunchOptionError):
        lo.build_launch(cfg(comfy_cuda_device="0; rm -rf /"), port=1, extra_model_paths=None)
    with pytest.raises(lo.LaunchOptionError):
        lo.build_launch(cfg(comfy_fast="warp_speed"), port=1, extra_model_paths=None)


def test_extra_model_paths_yaml(tmp_path):
    target = lo.write_extra_model_paths(tmp_path / "models", tmp_path / "rt" / "extra.yaml")
    text = target.read_text()
    assert "director_studio:" in text and "diffusion_models: |" in text and "    unet/" in text
    assert (tmp_path / "models" / "loras").is_dir()
    assert "custom_nodes" not in text


def test_extra_model_paths_yaml_adds_user_custom_nodes(tmp_path):
    import yaml

    nodes = tmp_path / "data dir" / "comfy" / "custom_nodes"
    target = lo.write_extra_model_paths(tmp_path / "models", tmp_path / "rt" / "extra.yaml", nodes)
    config = yaml.safe_load(target.read_text())
    assert config["director_studio_user_nodes"] == {"custom_nodes": nodes.resolve().as_posix()}
    assert "base_path" not in config["director_studio_user_nodes"]  # absolute path, not under models/
    assert nodes.is_dir()


def test_child_environment_drops_ds_secrets(monkeypatch):
    monkeypatch.setenv("DS_LLM_API_KEY", "secret")
    monkeypatch.setenv("MINIMAX_API_KEY", "secret")
    env = lo.child_environment({"HF_ENDPOINT": "x"})
    assert "DS_LLM_API_KEY" not in env and "MINIMAX_API_KEY" not in env and env["HF_ENDPOINT"] == "x"
