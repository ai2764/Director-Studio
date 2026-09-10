from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .runtime_paths import runtime_paths


_PROJECT_ROOT = runtime_paths.bundle_root
_DEFAULT_DATA_DIR = runtime_paths.data_root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DS_", env_file=".env", extra="ignore")

    comfy_base_url: str = "http://127.0.0.1:8188"
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
    comfy_mcp_command: str = "comfy-mcp"
    comfy_mcp_args: str = ""
    comfy_mcp_comfy_bin: str = "comfy"
    h3_minimax_base_url: str = "https://api.minimax.io"
    h3_minimax_api_key: str | None = Field(
        default=None,
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
    llm_provider: Literal["ollama", "lm-studio", "openai-compatible"] = "ollama"
    llm_base_url: str = ""
    llm_api_key: str | None = None
    llm_timeout_sec: float = 600.0

    # Local Ollama / Director agent (VRAM exclusive with Comfy)
    ollama_base_url: str = "http://127.0.0.1:11434"
    director_plan_model: str = ""
    director_num_ctx: int = 32768
    director_num_predict: int = 4096
    vram_policy: str = "exclusive"  # exclusive: one of LLM/Comfy at a time, others queue
    # Max seconds to wait in GPU queue (Plan waits for H3, next gen waits for casting, …)
    vram_acquire_timeout_sec: float = 3600.0
    # Multi-turn residency: keep Ollama loaded between chat/plan turns.
    # Comfy jobs still unload Ollama in before_comfy_job / release_llm.
    llm_keep_loaded: bool = True

    @model_validator(mode="before")
    @classmethod
    def _derive_persistent_paths(cls, values: Any) -> Any:
        """Let one data root relocate the complete durable project store."""
        if not isinstance(values, dict):
            return values
        configured = dict(values)
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
