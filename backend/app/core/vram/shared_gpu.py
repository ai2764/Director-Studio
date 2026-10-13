"""Does the Director LLM share a GPU with ComfyUI?

The VRAM orchestrator only hands the GPU over (unload the LLM before a Comfy job,
``POST /free`` on ComfyUI before an LLM turn) when both run on the same card.
``DS_VRAM_SHARED_GPU``:

- ``true`` / ``false``: explicit.
- ``auto`` (default): managed / external-local ComfyUI share the local GPU. For a
  remote ComfyUI the URLs decide: two different non-loopback hosts, or a loopback LLM
  next to a non-loopback ComfyUI, are separate machines; the same non-loopback host is
  shared; two loopback URLs (tunnels / port forwards to one PC) cannot be told apart
  and are treated as shared, which is the safe choice (a needless reload instead of
  an out-of-memory crash when llama-swap and ComfyUI sit on the same card).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from ...config import settings

_LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0", "::"}
_LLM_DEFAULT_URLS = {
    "lm-studio": "http://127.0.0.1:1234/v1",
    "llama-swap": "http://127.0.0.1:11435/v1",
}


@dataclass(frozen=True)
class SharedGpu:
    shared: bool
    setting: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"shared": self.shared, "setting": self.setting, "reason": self.reason}


def _host(url: str) -> str:
    try:
        return (urlsplit(url or "").hostname or "").lower().strip("[]")
    except ValueError:
        return ""


def _is_loopback(host: str) -> bool:
    return host in _LOOPBACK or host.startswith("127.")


def llm_url(cfg: Any = settings) -> str:
    provider = str(getattr(cfg, "llm_provider", "ollama"))
    if provider == "ollama":
        if str(getattr(cfg, "ollama_mode", "system")) == "bundled":
            return "http://127.0.0.1:11434"  # DS's private Ollama always runs on this machine
        return str(getattr(cfg, "ollama_base_url", "") or "http://127.0.0.1:11434")
    if provider == "llama-swap" and str(getattr(cfg, "llama_swap_mode", "remote")) == "bundled":
        return "http://127.0.0.1:11435/v1"  # DS's built-in llama.cpp always runs on this machine
    return str(getattr(cfg, "llm_base_url", "") or _LLM_DEFAULT_URLS.get(provider, ""))


def resolve_shared_gpu(cfg: Any = settings) -> SharedGpu:
    setting = str(getattr(cfg, "vram_shared_gpu", "auto") or "auto").lower()
    if setting in {"true", "false"}:
        return SharedGpu(setting == "true", setting, "explicit setting")
    if str(getattr(cfg, "comfy_mode", "external-local")) != "remote":
        return SharedGpu(True, "auto", "ComfyUI runs on this machine")
    comfy_host = _host(str(getattr(cfg, "comfy_remote_url", "")))
    llm_host = _host(llm_url(cfg))
    if not comfy_host or not llm_host:
        return SharedGpu(False, "auto", "remote ComfyUI or LLM URL not set")
    comfy_lo, llm_lo = _is_loopback(comfy_host), _is_loopback(llm_host)
    if comfy_lo and llm_lo:
        return SharedGpu(True, "auto", "both reached through loopback (tunnel/forward); assuming the same GPU")
    if comfy_lo != llm_lo:
        return SharedGpu(False, "auto", "LLM and remote ComfyUI are on different machines")
    if comfy_host == llm_host:
        return SharedGpu(True, "auto", f"LLM and remote ComfyUI share host {comfy_host}")
    return SharedGpu(False, "auto", f"different hosts ({llm_host} / {comfy_host})")


def gpu_shared_with_comfy(cfg: Any = settings) -> bool:
    return resolve_shared_gpu(cfg).shared
