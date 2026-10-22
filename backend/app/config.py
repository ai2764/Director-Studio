from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .runtime_paths import runtime_paths


_PROJECT_ROOT = runtime_paths.bundle_root
_DEFAULT_DATA_DIR = runtime_paths.data_root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DS_", env_file=".env", extra="ignore")

    comfy_base_url: str = "http://127.0.0.1:8188"
    qwen_image_21_comfy_base_url: str = ""
    # How Director Studio reaches ComfyUI:
    #   external-local: an already running ComfyUI at DS_COMFY_BASE_URL (legacy default)
    #   managed:        DS starts/stops a bundled ComfyUI (DS_COMFY_DIR) in the background
    #   remote:         a remote ComfyUI (tunnel) reached through DS's loopback proxy
    comfy_mode: Literal["managed", "external-local", "remote"] = "external-local"
    comfy_dir: str = ""
    comfy_python: str = ""
    comfy_models_dir: str = ""
    comfy_port: int = Field(default=0, ge=0, le=65535)
    comfy_startup_timeout_sec: float = Field(default=180.0, gt=0)
    comfy_dev_cpu: bool = False  # internal/dev only: run managed ComfyUI with --cpu
    comfy_vram_preset: Literal["auto", "high", "save", "extreme"] = "auto"
    comfy_reserve_vram_gb: float = Field(default=0.0, ge=0, le=256)
    comfy_cuda_device: str = ""
    comfy_ram_preset: Literal["auto", "low"] = "auto"
    comfy_precision_preset: Literal["auto", "fp8"] = "auto"
    comfy_attention: Literal["auto", "sage", "pytorch", "split", "quad"] = "auto"
    comfy_unet_precision: Literal["auto", "fp8_e4m3fn", "fp8_e5m2", "fp16", "bf16", "fp32"] = "auto"
    comfy_text_enc_precision: Literal["auto", "fp8_e4m3fn", "fp8_e5m2", "fp16", "bf16", "fp32"] = "auto"
    comfy_vae_precision: Literal["auto", "fp16", "bf16", "fp32"] = "auto"
    comfy_fast: str = ""
    comfy_preview_method: Literal["default", "none", "auto", "latent2rgb", "taesd"] = "default"
    comfy_troubleshoot: str = ""
    comfy_hf_endpoint: str = ""
    deps_hf_endpoint: str = ""
    hf_token: str = Field(default="", repr=False)
    deps_concurrency: int = Field(default=2, ge=1, le=4)
    comfy_http_proxy: str = ""
    comfy_extra_args: str = ""
    comfy_remote_url: str = ""
    comfy_remote_auth_type: Literal["none", "bearer", "basic", "headers"] = "none"
    comfy_remote_auth: str = Field(default="", repr=False)
    comfy_remote_verify_tls: bool = True
    comfy_proxy_port: int = Field(default=0, ge=0, le=65535)
    host: str = "127.0.0.1"
    port: int = 8790

    # Repo root: Director-Studio/
    project_root: Path = _PROJECT_ROOT
    frontend_dist: Path = _PROJECT_ROOT / "frontend" / "dist"
    data_dir: Path = _DEFAULT_DATA_DIR
    jobs_dir: Path = _DEFAULT_DATA_DIR / "jobs"
    library_root: Path = _DEFAULT_DATA_DIR / "library"
    projects_dir: Path = _DEFAULT_DATA_DIR / "projects"
    workflow_profiles_dir: Path = _DEFAULT_DATA_DIR / "workflow_profiles"
    workflows_dir: Path = Path(__file__).resolve().parents[1] / "workflows"
    h3_builtin_workflow: Literal["h3_ref2va_turbo4_video_context.api.json", "h3_ref2va_fast4.api.json", "h3_ref2va.api.json"] = "h3_ref2va_turbo4_video_context.api.json"

    # Legacy convenience path (actor pipeline)
    library_dir: Path = _DEFAULT_DATA_DIR / "library" / "actors"
    workflow_path: Path = workflows_dir / "qwen_actor_asset_workbench.api.json"

    poll_interval_sec: float = 1.5
    job_timeout_sec: float = 1800.0
    max_upload_mb: int = 20

    # H3 execution provider. ``local`` runs the configured Comfy workflow via
    # the official Comfy MCP transport; ``minimax`` uses the official
    # asynchronous MiniMax H3 V2 API. ``mcp`` remains a legacy alias for local.
    h3_provider: str = "local"
    # Experiment switch. The main instance leaves this false.
    video_context_enabled: bool = False
    comfy_mcp_command: str = "comfy-mcp"
    comfy_mcp_args: str = ""
    comfy_mcp_comfy_bin: str = "comfy"
    h3_minimax_base_url: str = "https://api.minimax.io"
    h3_minimax_api_key: str | None = Field(
        default=None,
        repr=False,
        validation_alias=AliasChoices(
            "DS_H3_MINIMAX_API_KEY",
            "MINIMAX_API_KEY",
        ),
    )
    h3_minimax_model: str = "MiniMax-H3"
    h3_minimax_resolution: str = "768P"
    h3_minimax_poll_interval_sec: float = 10.0
    h3_minimax_timeout_sec: float = 3600.0
    h3_minimax_max_get_retries: int = 3
    h3_minimax_retry_delay_sec: float = 2.0
    h3_minimax_max_request_mb: float = 64.0

    # Optional local ChatGPT Browser Bridge. API_TOKEN is read at runtime from
    # the external env file and is never copied into Director Studio state.
    gpt_bridge_base_url: str | None = None
    gpt_bridge_env_file: Path | None = None
    gpt_bridge_timeout_sec: float = 600.0
    gpt_bridge_max_file_mb: int = 20
    gpt_bridge_max_total_mb: int = 100
    gpt_bridge_action_delay_sec: float = 1.5
    gpt_bridge_job_cooldown_sec: float = 15.0

    # Director LLM. Exactly one provider is active for the process.
    llm_provider: Literal[
        "ollama", "lm-studio", "llama-swap", "openai-compatible"
    ] = "ollama"
    llm_base_url: str = ""
    llm_api_key: str | None = Field(default=None, repr=False)
    llm_timeout_sec: float = 600.0
    # Extra HTTP headers for the Director LLM (tunnels: Cloudflare Access, Basic, ...).
    # JSON object or "Key: value" / "Key=value" lines. Secret: never returned by the API.
    llm_extra_headers: str = Field(default="", repr=False)

    # Local Ollama / Director agent (VRAM exclusive with Comfy)
    ollama_base_url: str = "http://127.0.0.1:11434"
    # system:  use the user's own Ollama at DS_OLLAMA_BASE_URL (default, legacy behaviour)
    # bundled: DS starts its private Ollama (DS_OLLAMA_DIR, default runtime/ollama) on a
    #          free loopback port with models stored under the DS data directory.
    ollama_mode: Literal["system", "bundled"] = "system"
    ollama_dir: str = ""
    ollama_port: int = Field(default=0, ge=0, le=65535)
    ollama_models_dir: str = ""
    # llama-swap (provider=llama-swap):
    # remote:  the user's own llama-swap at DS_LLM_BASE_URL; DS reads the context capacity
    # bundled: DS starts its private llama-swap (runtime/llama-swap) + llama-server
    #          (runtime/llama.cpp) on a loopback port and generates its config.yaml from the
    #          GGUF files in DS_LLAMA_MODELS_DIR (default <models>/llm).
    llama_swap_mode: Literal["remote", "bundled"] = "remote"
    llama_swap_dir: str = ""
    llama_cpp_dir: str = ""
    llama_swap_port: int = Field(default=0, ge=0, le=65535)
    llama_models_dir: str = ""
    llama_gpu_layers: str = "auto"  # llama-server -ngl: auto | all | <n> (0 = CPU only)
    llama_mtp: bool = True  # MTP speculative decoding for models that carry MTP layers
    # Directory holding ffmpeg/ffprobe; empty = bundled runtime/ffmpeg, then PATH.
    ffmpeg_dir: str = ""
    director_plan_model: str = ""
    # Fallback for generic OpenAI-compatible servers that cannot report capacity.
    # Ollama, LM Studio and llama-swap are discovered from the loaded model.
    director_num_ctx: int = 32768
    director_num_predict: int = 4096
    # Disable for a same-request before/after image-context benchmark.
    director_reuse_verified_images: bool = True
    vram_policy: str = "exclusive"  # exclusive: one of LLM/Comfy at a time, others queue
    # Does the Director LLM share a GPU with ComfyUI (GPU handoff on/off)? See
    # app/core/vram/shared_gpu.py. auto: local ComfyUI = shared; remote = decided by host.
    vram_shared_gpu: Literal["auto", "true", "false"] = "auto"
    # Max seconds to wait in GPU queue (Plan waits for H3, next gen waits for casting, …)
    vram_acquire_timeout_sec: float = 3600.0
    # Multi-turn residency: keep a local LLM loaded between chat/plan turns.
    # Comfy jobs still release local LLMs before taking the GPU.
    llm_keep_loaded: bool = True

    director_agent_runtime: Literal["legacy", "harness"] = "harness"
    director_task_context_mode: Literal["off", "shadow", "pilot"] = "off"
    director_task_context_projects: list[str] = Field(default_factory=list)
    harness_managed: bool = True
    harness_base_url: str = "http://127.0.0.1:8791"
    harness_internal_token: str = Field(default="", repr=False)
    harness_turn_timeout_sec: float = Field(default=1800, gt=0, le=7200)
    harness_max_steps: int = Field(default=12, ge=1, le=32)
    harness_max_tool_calls: int = Field(default=64, ge=1, le=64)

    @field_validator("harness_base_url")
    @classmethod
    def _loopback_sidecar(cls, value: str) -> str:
        from urllib.parse import urlsplit

        url = urlsplit(value)
        if (url.scheme != "http" or url.hostname != "127.0.0.1"
                or url.username or url.password or url.path not in {"", "/"}
                or url.query or url.fragment or url.port is None):
            raise ValueError("Harness URL must be http://127.0.0.1:<port>")
        return value.rstrip("/")

    @model_validator(mode="before")
    @classmethod
    def _derive_persistent_paths(cls, values: Any) -> Any:
        """Let one data root relocate the complete durable project store."""
        if not isinstance(values, dict):
            return values
        configured = dict(values)
        for env_key, value in runtime_paths.install_defaults.items():
            # Package defaults (ds-install.json) sit between .env and code defaults.
            name = env_key.removeprefix("DS_").lower()
            if name in cls.model_fields and name not in configured and env_key not in configured:
                configured[name] = value
        data_root = Path(configured.get("data_dir") or _DEFAULT_DATA_DIR)
        configured.setdefault("data_dir", data_root)
        configured.setdefault("jobs_dir", data_root / "jobs")
        configured.setdefault("library_root", data_root / "library")
        configured.setdefault("projects_dir", data_root / "projects")
        configured.setdefault("workflow_profiles_dir", data_root / "workflow_profiles")
        configured.setdefault(
            "library_dir",
            Path(configured["library_root"]) / "actors",
        )
        return configured

    @property
    def gpt_bridge_configured(self) -> bool:
        return bool(self.gpt_bridge_base_url and self.gpt_bridge_env_file)


settings = Settings(_env_file=runtime_paths.env_file)
settings.jobs_dir.mkdir(parents=True, exist_ok=True)
settings.library_root.mkdir(parents=True, exist_ok=True)
settings.library_dir.mkdir(parents=True, exist_ok=True)
settings.projects_dir.mkdir(parents=True, exist_ok=True)


def library_kind_dir(asset_kind: str, project_id: str | None = None) -> Path:
    """Library kind folder — project-rooted when project_id set, else global pool."""
    from .core.paths import global_library_kind_dir, project_library_kind_dir

    if project_id:
        return project_library_kind_dir(project_id, asset_kind)
    return global_library_kind_dir(asset_kind)
