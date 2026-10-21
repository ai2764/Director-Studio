"""ComfyUI launch-option schema -> command-line arguments.

DS never copies ComfyUI code: the set of supported flags is discovered by
running the bundled ComfyUI's own ``main.py --help`` as a separate process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Any

# Flags DS manages itself or that would break/expose the managed instance.
BLACKLIST: frozenset[str] = frozenset({
    "--listen", "--port", "--cpu", "--normalvram", "--gpu-only", "--highvram", "--lowvram", "--novram",
    "--windows-standalone-build", "--auto-launch", "--disable-auto-launch", "--multi-user",
    "--tls-keyfile", "--tls-certfile", "--enable-cors-header", "--force-fp32", "--cpu-vae",
    "--extra-model-paths-config", "--base-directory", "--output-directory", "--input-directory",
    "--temp-directory", "--user-directory", "--models-directory", "--front-end-root", "--comfy-api-base",
    "--database-url", "--enable-manager", "--enable-manager-legacy-ui", "--quick-test-for-ci",
    "--reserve-vram", "--cuda-device", "--directml", "--oneapi-device-selector", "--help",
    "--list-feature-flags", "--dont-print-server", "--max-upload-size",
})

FAST_OPTIONS = ("fp16_accumulation", "fp8_matrix_mult", "cublas_ops", "autotune")
TROUBLESHOOT_FLAGS: dict[str, str] = {
    "disable_all_custom_nodes": "--disable-all-custom-nodes",
    "disable_cuda_malloc": "--disable-cuda-malloc",
    "disable_dynamic_vram": "--disable-dynamic-vram",
    "disable_xformers": "--disable-xformers",
    "disable_async_offload": "--disable-async-offload",
    "disable_mmap": "--disable-mmap",
    "deterministic": "--deterministic",
}
ATTENTION_FLAGS = {
    "sage": "--use-sage-attention",
    "pytorch": "--use-pytorch-cross-attention",
    "split": "--use-split-cross-attention",
    "quad": "--use-quad-cross-attention",
}
UNET_FLAGS = {p: f"--{p}-unet" for p in ("fp8_e4m3fn", "fp8_e5m2", "fp16", "bf16", "fp32")}
TEXT_ENC_FLAGS = {p: f"--{p}-text-enc" for p in ("fp8_e4m3fn", "fp8_e5m2", "fp16", "bf16", "fp32")}
VAE_FLAGS = {p: f"--{p}-vae" for p in ("fp16", "bf16", "fp32")}
DEFAULT_SAVE_RESERVE_GB = 1.5

MODEL_FOLDERS = (
    "checkpoints", "diffusion_models", "text_encoders", "vae", "loras", "clip_vision", "controlnet",
    "upscale_models", "embeddings", "audio_encoders", "model_patches", "latent_upscale_models",
)
_OPTION_RE = re.compile(r"(?<![\w-])(--[A-Za-z0-9][A-Za-z0-9_-]*)")


class LaunchOptionError(ValueError):
    pass


def split_list(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[,\s]+", value)
    return [item.strip() for item in items if item and item.strip()]


def parse_help_options(help_text: str) -> set[str]:
    return set(_OPTION_RE.findall(help_text))


def validate_extra_args(text: str, allowed: set[str] | None) -> tuple[list[str], list[str]]:
    """Return ``(args, errors)`` for the free-text extra-args field."""
    errors: list[str] = []
    try:
        tokens = shlex.split(text or "", posix=True)
    except ValueError as exc:
        return [], [f"cannot parse arguments: {exc}"]
    for token in tokens:
        if token.startswith("--"):
            name = token.split("=", 1)[0]
            if name in BLACKLIST:
                errors.append(f"{name} is managed by Director Studio and cannot be set here")
            elif allowed is not None and name not in allowed:
                errors.append(f"{name} is not a ComfyUI option (see main.py --help)")
        elif token.startswith("-"):
            errors.append(f"short option {token} is not allowed; use the long --form")
        elif any(ch in token for ch in "\n\r\x00"):
            errors.append("arguments must not contain control characters")
    return (tokens if not errors else []), errors


@dataclass
class BuiltLaunch:
    args: list[str]
    env: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def build_launch(
    cfg: Any,
    *,
    port: int,
    extra_model_paths: Path | None,
    allowed: set[str] | None = None,
    sage_available: bool = False,
) -> BuiltLaunch:
    """Translate DS settings (``cfg`` = Settings-like object) into ComfyUI args."""
    args: list[str] = ["--listen", "127.0.0.1", "--port", str(int(port)), "--disable-auto-launch"]
    if extra_model_paths is not None:
        args += ["--extra-model-paths-config", str(extra_model_paths)]
    warnings: list[str] = []

    preset = getattr(cfg, "comfy_vram_preset", "auto")
    reserve = float(getattr(cfg, "comfy_reserve_vram_gb", 0) or 0)
    if preset == "high":
        args.append("--highvram")
    elif preset == "save":
        args.append("--disable-smart-memory")
        reserve = reserve or DEFAULT_SAVE_RESERVE_GB
    elif preset == "extreme":
        args.append("--novram")
    if reserve > 0:
        args += ["--reserve-vram", f"{reserve:g}"]

    device = str(getattr(cfg, "comfy_cuda_device", "") or "").strip()
    if device:
        if not re.fullmatch(r"\d+(,\d+)*|all", device):
            raise LaunchOptionError("GPU index must look like 0 or 0,1")
        args += ["--cuda-device", device]

    if getattr(cfg, "comfy_ram_preset", "auto") == "low":
        args += ["--cache-none", "--disable-pinned-memory"]

    unet = getattr(cfg, "comfy_unet_precision", "auto")
    text_enc = getattr(cfg, "comfy_text_enc_precision", "auto")
    if getattr(cfg, "comfy_precision_preset", "auto") == "fp8":
        unet = unet if unet != "auto" else "fp8_e4m3fn"
        text_enc = text_enc if text_enc != "auto" else "fp8_e4m3fn"
    if unet != "auto":
        args.append(UNET_FLAGS[unet])
    if text_enc != "auto":
        args.append(TEXT_ENC_FLAGS[text_enc])
    vae = getattr(cfg, "comfy_vae_precision", "auto")
    if vae != "auto":
        args.append(VAE_FLAGS[vae])

    attention = getattr(cfg, "comfy_attention", "auto")
    if attention == "sage" and not sage_available:
        warnings.append("SageAttention is not installed in the ComfyUI Python; using default attention")
    elif attention != "auto":
        args.append(ATTENTION_FLAGS[attention])

    fast = split_list(getattr(cfg, "comfy_fast", ""))
    bad_fast = [item for item in fast if item not in FAST_OPTIONS]
    if bad_fast:
        raise LaunchOptionError(f"unknown --fast options: {', '.join(bad_fast)}")
    if fast:
        args += ["--fast", *fast]

    preview = getattr(cfg, "comfy_preview_method", "default")
    if preview and preview != "default":
        args += ["--preview-method", preview]

    for item in split_list(getattr(cfg, "comfy_troubleshoot", "")):
        flag = TROUBLESHOOT_FLAGS.get(item)
        if flag is None:
            raise LaunchOptionError(f"unknown troubleshooting toggle: {item}")
        args.append(flag)

    if getattr(cfg, "comfy_dev_cpu", False):
        args.append("--cpu")  # internal/dev only (GPU-less CI boxes)

    extra, errors = validate_extra_args(getattr(cfg, "comfy_extra_args", ""), allowed)
    if errors:
        raise LaunchOptionError("; ".join(errors))
    args += extra

    if allowed is not None:
        filtered: list[str] = []
        for token in args:
            name = token.split("=", 1)[0]
            if name.startswith("--") and name not in allowed:
                warnings.append(f"{name} is not supported by this ComfyUI version; skipped")
                continue
            filtered.append(token)
        args = filtered

    env: dict[str, str] = {}
    hf = str(getattr(cfg, "comfy_hf_endpoint", "") or "").strip()
    if hf:
        env["HF_ENDPOINT"] = hf
    proxy = str(getattr(cfg, "comfy_http_proxy", "") or "").strip()
    if proxy:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env[key] = proxy
        env.setdefault("NO_PROXY", "127.0.0.1,localhost")
        env.setdefault("no_proxy", "127.0.0.1,localhost")
    return BuiltLaunch(args=args, env=env, warnings=warnings)


# ---- runtime discovery -----------------------------------------------------

def find_comfy_python(comfy_dir: Path, configured: str = "") -> Path | None:
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_file() else None
    candidates = [
        comfy_dir / "venv" / "bin" / "python",
        comfy_dir / ".venv" / "bin" / "python",
        comfy_dir / "venv" / "Scripts" / "python.exe",
        comfy_dir / ".venv" / "Scripts" / "python.exe",
        comfy_dir.parent / "python_embeded" / "python.exe",
        # DS installer layout: runtime/ComfyUI + runtime/comfy-python (python-build-standalone)
        comfy_dir.parent / "comfy-python" / "bin" / "python3",
        comfy_dir.parent / "comfy-python" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / "comfy_cli_cache.json"


def probe_runtime(python: Path, comfy_dir: Path, cache_dir: Path, *, timeout: float = 120.0) -> dict[str, Any]:
    """Return ``{"options": [...], "sage": bool}`` (cached by python+main.py mtime)."""
    main = comfy_dir / "main.py"
    key = f"{python}|{main.stat().st_mtime_ns}|{(comfy_dir / 'comfy' / 'cli_args.py').stat().st_mtime_ns if (comfy_dir / 'comfy' / 'cli_args.py').exists() else 0}"
    cache_file = _cache_path(cache_dir)
    try:
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        if cached.get("key") == key:
            return cached
    except (OSError, ValueError):
        pass
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    result = subprocess.run(
        [str(python), "main.py", "--help"], cwd=str(comfy_dir), capture_output=True, text=True,
        timeout=timeout, creationflags=flags,
    )
    options = sorted(parse_help_options(result.stdout))
    if not options:
        raise LaunchOptionError(f"could not read ComfyUI --help: {result.stderr[-500:]}")
    sage = subprocess.run(
        [str(python), "-c", "import sageattention"], capture_output=True, timeout=timeout, creationflags=flags,
    ).returncode == 0
    data = {"key": key, "options": options, "sage": sage}
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass
    return data


def write_extra_model_paths(models_dir: Path, target: Path, custom_nodes_dir: Path | None = None) -> Path:
    """Create the DS models folder layout and an extra_model_paths.yaml for it.

    ``custom_nodes_dir`` (DS data dir) is added as an extra ComfyUI custom_nodes
    search path, so user-installed node packs live outside the program folder and
    survive upgrades/uninstall of an installed package.
    """
    models_dir.mkdir(parents=True, exist_ok=True)
    for folder in MODEL_FOLDERS:
        (models_dir / folder).mkdir(exist_ok=True)
    base = models_dir.resolve().as_posix()
    lines = [
        "# Generated by Director Studio. Do not edit; change DS_COMFY_MODELS_DIR instead.",
        "director_studio:",
        f"  base_path: {json.dumps(base)}",
        "  is_default: true",
    ]
    aliases = {"diffusion_models": ["diffusion_models", "unet"], "text_encoders": ["text_encoders", "clip"]}
    for folder in MODEL_FOLDERS:
        paths = aliases.get(folder, [folder])
        if len(paths) == 1:
            lines.append(f"  {folder}: {paths[0]}/")
        else:
            lines.append(f"  {folder}: |")
            lines.extend(f"    {p}/" for p in paths)
    if custom_nodes_dir is not None:
        custom_nodes_dir.mkdir(parents=True, exist_ok=True)
        lines += ["director_studio_user_nodes:",
                  f"  custom_nodes: {json.dumps(custom_nodes_dir.resolve().as_posix())}"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def child_environment(extra: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DS_")}
    # Never leak DS secrets/tokens to ComfyUI; drop the backend's own venv from PATH.
    env.pop("VIRTUAL_ENV", None)
    venv_bin = str(Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin"))
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if p != venv_bin)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUNBUFFERED"] = "1"
    for key in ("MINIMAX_API_KEY",):
        env.pop(key, None)
    env.update(extra)
    return env
