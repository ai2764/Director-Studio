"""GET/PUT /api/config business logic: masking, validation, persistence, hot apply."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from ...config import Settings, settings
from ...runtime_paths import runtime_paths
from ..system import control
from ...settings_registry import BY_KEY, PUBLIC_FIELDS, FieldSpec
from ..net_headers import HeaderParseError, auth_headers, header_names_hint, parse_headers
from . import envfile

logger = logging.getLogger("director_studio.config")

_lock = asyncio.Lock()
restart_pending: set[str] = set()
_ENV_PATH_OVERRIDE: Path | None = None  # tests


class ConfigError(Exception):
    def __init__(self, status: int, message: str, *, fields: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.fields = fields or {}


def env_path() -> Path:
    return _ENV_PATH_OVERRIDE or runtime_paths.env_file


# ---- sources ---------------------------------------------------------------------

def _portable_keys() -> set[str]:
    return {k.strip() for k in os.environ.get("DS_PORTABLE_DEFAULT_KEYS", "").split(",") if k.strip()}


def env_override(spec: FieldSpec) -> str | None:
    for key in (spec.key, *spec.aliases):
        if key in os.environ:
            return key
    return None


def load_settings_from_text(text: str | None) -> Settings:
    """Construct a Settings object from ``.env`` text + the process environment."""
    directory = env_path().parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".env.validate-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text or "")
        return Settings(_env_file=name)
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def secret_state(spec: FieldSpec, value: Any) -> dict[str, Any]:
    text = "" if value is None else str(value)
    state: dict[str, Any] = {"set": bool(text.strip())}
    if not text.strip():
        return state
    if spec.key in {"DS_LLM_EXTRA_HEADERS"}:
        state["hint"] = header_names_hint(text)
    elif spec.key == "DS_COMFY_REMOTE_AUTH" and settings.comfy_remote_auth_type == "headers":
        state["hint"] = header_names_hint(text)
    elif len(text) >= 12:
        state["hint"] = f"…{text[-4:]}"
    else:
        state["hint"] = "••••"
    return state


def _default_of(spec: FieldSpec) -> Any:
    field = Settings.model_fields[spec.attr]
    if spec.secret:
        return None
    if spec.key in runtime_paths.install_defaults:  # package default (ds-install.json)
        try:
            return _jsonable(getattr(Settings.model_validate({spec.attr: runtime_paths.install_defaults[spec.key]}), spec.attr))
        except ValidationError:
            pass
    default = field.get_default(call_default_factory=True)
    return _jsonable(default)


# ---- GET ----------------------------------------------------------------------------

def get_config() -> dict[str, Any]:
    text = envfile.read_text(env_path())
    file_values = envfile.parse(text)
    try:
        current = load_settings_from_text(text)
    except ValidationError:
        current = settings
    portable = _portable_keys()
    fields: list[dict[str, Any]] = []
    for spec in PUBLIC_FIELDS:
        env_key = env_override(spec)
        if env_key and spec.key in portable:
            source, locked = "portable", False
        elif env_key:
            source, locked = "env", True
        elif any(k in file_values for k in (spec.key, *spec.aliases)):
            source, locked = "file", False
        else:
            source, locked = "default", False
        raw = getattr(current, spec.attr)
        entry: dict[str, Any] = {
            "key": spec.key,
            "source": source,
            "locked": locked,
            "pending_restart": spec.key in restart_pending,
        }
        if spec.secret:
            entry["secret"] = secret_state(spec, raw)
        else:
            entry["value"] = _jsonable(raw)
            entry["default"] = _default_of(spec)
        fields.append(entry)
    return {
        "revision": envfile.revision_of(text),
        "env_file": str(env_path()),
        "env_exists": text is not None,
        "fields": fields,
        "restart_pending": sorted(restart_pending),
    }


# ---- PUT ----------------------------------------------------------------------------

def _to_env_string(spec: FieldSpec, value: Any) -> str | None:
    if value is None:
        return None
    if spec.type == "bool":
        if isinstance(value, bool):
            return "true" if value else "false"
        lowered = str(value).strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return "true"
        if lowered in {"false", "0", "no", "off"}:
            return "false"
        raise ConfigError(422, "invalid boolean", fields={spec.key: "must be true or false"})
    if spec.type == "multi":
        items = value if isinstance(value, list) else [s for s in str(value).replace(" ", ",").split(",")]
        items = [str(i).strip() for i in items if str(i).strip()]
        bad = [i for i in items if spec.options and i not in spec.options]
        if bad:
            raise ConfigError(422, "invalid option", fields={spec.key: f"unknown: {', '.join(bad)}"})
        return ",".join(items)
    if spec.type == "enum" and str(value) not in spec.options:
        raise ConfigError(422, "invalid option", fields={spec.key: f"must be one of {', '.join(spec.options)}"})
    if isinstance(value, float) and value.is_integer() and spec.type == "int":
        value = int(value)
    return str(value).strip()


def normalize_changes(changes: dict[str, Any]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    errors: dict[str, str] = {}
    for key, value in changes.items():
        spec = BY_KEY.get(key)
        if spec is None or spec.tier == "internal":
            errors[key] = "unknown or non-editable setting"
            continue
        if env_override(spec) and spec.key not in _portable_keys():
            errors[key] = "locked: set by an environment variable / launcher"
            continue
        if spec.secret:
            if isinstance(value, dict):
                action = value.get("secret")
                if action == "keep":
                    continue
                if action == "clear":
                    out[key] = None
                    for alias in spec.aliases:
                        out[alias] = None
                    continue
                if action == "set" and isinstance(value.get("value"), str):
                    value = value["value"]
                else:
                    errors[key] = "secret action must be keep / clear / set"
                    continue
            if value is None:
                out[key] = None
                continue
            if not isinstance(value, str):
                errors[key] = "secret must be a string"
                continue
            value = value.strip()
            if "\n" in value or "\r" in value:
                # .env values are single-line: store multi-line header lists as compact JSON.
                try:
                    value = json.dumps(parse_headers(value), ensure_ascii=False, separators=(",", ":"))
                except HeaderParseError as exc:
                    errors[key] = str(exc)
                    continue
            out[key] = value
            continue
        try:
            out[key] = _to_env_string(spec, value)
        except ConfigError as exc:
            errors.update(exc.fields)
    if errors:
        raise ConfigError(422, "invalid settings", fields=errors)
    return out


def semantic_validate(cfg: Settings) -> dict[str, str]:
    from ..comfy import launch_options as lo
    from ..comfy.remote_proxy import RemoteUrlError, normalize_remote_url
    from ..comfy.runtime import is_loopback_url

    errors: dict[str, str] = {}
    for key, attr in (("DS_LLM_BASE_URL", "llm_base_url"), ("DS_OLLAMA_BASE_URL", "ollama_base_url"),
                      ("DS_H3_MINIMAX_BASE_URL", "h3_minimax_base_url"), ("DS_COMFY_HF_ENDPOINT", "comfy_hf_endpoint"),
                      ("DS_COMFY_HTTP_PROXY", "comfy_http_proxy")):
        value = str(getattr(cfg, attr) or "").strip()
        if value:
            parts = urlsplit(value)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                errors[key] = "must be an http(s) URL"
            elif parts.username or parts.password:
                if key != "DS_COMFY_HTTP_PROXY":
                    errors[key] = "credentials in URLs are not allowed; use the key/header fields"
    if cfg.comfy_mode == "external-local" and not is_loopback_url(cfg.comfy_base_url):
        errors["DS_COMFY_BASE_URL"] = "local mode only accepts http://127.0.0.1|localhost:<port>; use remote mode"
    if cfg.qwen_image_21_comfy_base_url and not is_loopback_url(cfg.qwen_image_21_comfy_base_url):
        errors["DS_QWEN_IMAGE_21_COMFY_BASE_URL"] = "must be empty or a loopback URL"
    if cfg.comfy_mode == "remote":
        try:
            normalize_remote_url(cfg.comfy_remote_url)
        except RemoteUrlError as exc:
            errors["DS_COMFY_REMOTE_URL"] = str(exc)
        try:
            auth_headers(cfg.comfy_remote_auth_type, cfg.comfy_remote_auth)
        except HeaderParseError as exc:
            errors["DS_COMFY_REMOTE_AUTH"] = str(exc)
    try:
        parse_headers(cfg.llm_extra_headers)
    except HeaderParseError as exc:
        errors["DS_LLM_EXTRA_HEADERS"] = str(exc)
    try:
        lo.build_launch(cfg, port=8188, extra_model_paths=None, allowed=_cached_allowed(cfg))
    except lo.LaunchOptionError as exc:
        key = "DS_COMFY_EXTRA_ARGS" if "option" in str(exc) or "managed" in str(exc) else "DS_COMFY_CUDA_DEVICE"
        errors[key] = str(exc)
    return errors


def _cached_allowed(cfg: Settings) -> set[str] | None:
    from ..comfy.runtime import runtime_dir

    try:
        data = json.loads((runtime_dir() / "comfy_cli_cache.json").read_text(encoding="utf-8"))
        return set(data.get("options") or []) or None
    except (OSError, ValueError):
        return None


async def put_config(revision: str, changes: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(changes, dict) or not changes:
        raise ConfigError(400, "no changes")
    normalized = normalize_changes(changes)
    path = env_path()
    async with _lock:
        with envfile.file_lock(path):
            text = envfile.read_text(path)
            if envfile.revision_of(text) != revision:
                raise ConfigError(409, ".env was modified outside the settings page; reload and try again")
            try:
                new_text = envfile.apply_changes(text, normalized)
            except envfile.EnvFileError as exc:
                raise ConfigError(422, str(exc)) from None
            try:
                old_cfg = load_settings_from_text(text)
            except ValidationError:
                old_cfg = settings
            try:
                new_cfg = load_settings_from_text(new_text)
            except ValidationError as exc:
                fields: dict[str, str] = {}
                for err in exc.errors():
                    loc = str(err.get("loc", [""])[0])
                    fields["DS_" + loc.upper()] = err.get("msg", "invalid")
                raise ConfigError(422, "invalid settings", fields=fields) from None
            errors = semantic_validate(new_cfg)
            touched_attrs = {BY_KEY[k].attr for k in normalized if k in BY_KEY}
            # Only block on errors related to what the user is changing (plus mode coupling).
            if errors:
                relevant = {k: v for k, v in errors.items() if k in normalized
                            or BY_KEY.get(k, None) is not None and BY_KEY[k].group in {BY_KEY[c].group for c in normalized if c in BY_KEY}}
                if relevant:
                    raise ConfigError(422, "invalid settings", fields=relevant)
            envfile.atomic_write(path, new_text)
            new_revision = envfile.revision_of(new_text)

    changed = [spec for spec in BY_KEY.values()
               if spec.tier != "internal" and getattr(old_cfg, spec.attr) != getattr(new_cfg, spec.attr)]
    _ = touched_attrs
    result = await apply_changed(changed, new_cfg)
    result["revision"] = new_revision
    return result


async def apply_changed(changed: list[FieldSpec], new_cfg: Settings) -> dict[str, Any]:
    applied: list[str] = []
    restart: list[str] = []
    warnings: list[str] = []
    comfy_changed = False
    llm_changed = False
    for spec in changed:
        if env_override(spec) and spec.key not in _portable_keys():
            continue
        if spec.apply == "restart":
            restart_pending.add(spec.key)
            restart.append(spec.key)
            continue
        if spec.attr not in {"comfy_base_url", "ollama_base_url"}:
            setattr(settings, spec.attr, getattr(new_cfg, spec.attr))
        applied.append(spec.key)
        if spec.apply == "comfy":
            comfy_changed = True
        if spec.group == "llm":
            llm_changed = True
    comfy_result = None
    if llm_changed:
        from ..llm.ollama_runtime import ollama_runtime

        await ollama_runtime.reconfigure(new_cfg.ollama_base_url)
        if ollama_runtime.error:
            warnings.append(f"bundled Ollama: {ollama_runtime.error}")
        from ..llm.llama_swap_runtime import llama_swap_runtime

        await llama_swap_runtime.reconfigure()
        if llama_swap_runtime.error:
            warnings.append(f"built-in llama.cpp: {llama_swap_runtime.error}")
        warnings += rebuild_llm()
    if comfy_changed:
        from ..comfy.runtime import comfy_runtime

        comfy_result = await comfy_runtime.reconfigure(new_cfg.comfy_base_url)
        if comfy_result == "deferred":
            warnings.append("ComfyUI is busy; the new ComfyUI settings apply when current jobs finish")
    return {"applied": applied, "restart_required": restart, "comfy": comfy_result, "warnings": warnings}


_pending_llm_reset = False


def pipeline_busy() -> bool:
    from ..comfy.runtime import _comfy_busy

    return _comfy_busy()


def rebuild_llm() -> list[str]:
    global _pending_llm_reset
    from ..llm.factory import reset_llm_provider
    from ..vram.orchestrator import reset_orchestrator

    reset_llm_provider()
    if reset_orchestrator():
        _pending_llm_reset = False
        return []
    _pending_llm_reset = True

    async def retry() -> None:
        global _pending_llm_reset
        while _pending_llm_reset:
            await asyncio.sleep(2.0)
            if reset_orchestrator():
                _pending_llm_reset = False

    try:
        asyncio.get_running_loop().create_task(retry())
    except RuntimeError:
        pass
    return ["GPU is busy; the new LLM settings fully apply after the current job"]


# ---- setup state ---------------------------------------------------------------

def setup_state_path() -> Path:
    return Path(settings.data_dir) / "setup_state.json"


def setup_state() -> dict[str, Any]:
    try:
        state = json.loads(setup_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    completed = bool(state.get("completed"))
    text = envfile.read_text(env_path())
    legacy = False
    if not completed and text is not None:
        example = runtime_paths.install_root / "backend" / ".env.example"
        example_text = envfile.read_text(example)
        # Only hand-written entries count: a .env that Settings itself created holds nothing
        # but the managed block (e.g. a fresh installed package after its first save).
        hand_written = envfile.parse(text.split(envfile.MANAGED_MARKER, 1)[0])
        legacy = bool(hand_written) and (
            example_text is None or hand_written != envfile.parse(example_text)
        )
    return {
        "completed": completed,
        "completed_at": state.get("completed_at"),
        "skipped": bool(state.get("skipped")),
        "legacy_env_detected": legacy,
        "admin_allowed": True,
        "installed": runtime_paths.installed,
        "restart_pending": sorted(restart_pending),
        **control.info(),
    }


def complete_setup(*, skipped: bool = False) -> dict[str, Any]:
    """Mark first-run setup as done (``skipped`` = closed via "Skip"; reopen from Settings)."""
    path = setup_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"completed": True, "skipped": bool(skipped),
                                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}),
                    encoding="utf-8")
    return setup_state()
