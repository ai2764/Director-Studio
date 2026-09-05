"""Projects / Shots HTTP API — dual human gates + H3 Ref2AV submit.

Human approve/reject/edit/submit paths work with the LLM cold.
Gate 1 (layout approve) defaults to ``rewrite_prompt=false`` so no Ollama wake
is required; pass ``?rewrite_prompt=true`` (or body flag) to fill PromptSections
via DirectorService (may take time while the LLM loads).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from ..agents.director import DirectorService
from ..agents.director.planner import role_to_library_kind
from ..agents.director.skill_loader import with_director_skill
from ..config import settings
from ..core.h3 import (
    compose_h3_prompt,
    frames_for_audio_seconds,
    frames_for_seconds,
    validate_h3_prompt,
)
from ..core.jobs import create_job, load_job, start_pipeline_job
from ..core.library.store import (
    asset_dir,
    create_external_asset,
    delete_asset,
    load_asset,
    write_asset,
)
from ..core.projects.models import (
    Project,
    ProjectMode,
    PromptSections,
    RefRole,
    Shot,
    ShotRef,
    ShotVoiceRef,
    ShotStatus,
    picture_ref_signature,
    voice_ref_signature,
)
from ..core.projects.chat_history import (
    DirectorChatImage,
    DirectorChatMessage,
    agent_history,
    append_chat_message,
    load_chat_history,
)
from ..core.projects.chat_sessions import (
    DirectorChatSessionConflict,
    director_chat_sessions,
)
from ..core.projects.layouts import (
    LayoutBrief,
    LayoutReference,
    LayoutReviewStatus,
    layout_prompt_signature,
    mirror_legacy_layout_fields,
    selected_layout_prompt_context,
    sync_selected_layout_refs,
)
from ..core.projects.store import (
    create_project,
    list_projects,
    list_shots,
    load_project,
    load_shot,
    save_project,
    save_shot,
)
from ..core.paths import ensure_project_tree
from ..core.projects.transitions import (
    apply_transition,
    assert_h3_submittable,
    review_layout_reference,
    select_layout_reference,
)
from ..core.schemas import JobStatus, LibraryAsset
from ..core.vram import GenerationActiveError
from ..core.vram.ollama_client import OllamaClient

logger = logging.getLogger("director_studio.api.projects")
_background_chat_tasks: set[Any] = set()

router = APIRouter(tags=["projects"])


class H3SubmitOptions(BaseModel):
    h3_provider: Literal["local", "minimax"] | None = None
    width: int | None = None
    height: int | None = None

LIBRARY_KINDS = ("actors", "costumes", "scenes", "props", "layouts")
CHAT_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
CHAT_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_CHAT_IMAGES = 4


@dataclass(frozen=True)
class ChatUploadBatch:
    encoded: list[str]
    captions: list[str]
    history_images: list[DirectorChatImage]
    directory: Path | None = None


def _cleanup_chat_upload_batch(project_id: str, directory: Path | None) -> None:
    if directory is None or not directory.exists():
        return
    root = (ensure_project_tree(project_id) / "agent" / "chat_uploads").resolve()
    target = directory.resolve()
    if target.parent != root or not target.name.startswith("upl_"):
        logger.error("Refusing to clean unexpected chat upload path: %s", target)
        return
    shutil.rmtree(target)


def _generation_active_http(error: GenerationActiveError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": error.code,
            "message": "Local image or video generation is using the GPU.",
            "generation_count": len(error.reservations),
        },
    )


async def _assert_chat_available() -> None:
    from ..core.vram import get_orchestrator

    reservations = await get_orchestrator().generation_reservations()
    if reservations:
        raise GenerationActiveError(reservations)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateProjectBody(BaseModel):
    name: str
    script_text: str = ""
    mode: ProjectMode = ProjectMode.director


class UpdateProjectBody(BaseModel):
    name: str | None = None
    script_text: str | None = None
    script_locked: bool | None = None


class ChatHistoryItem(BaseModel):
    role: str
    content: str


class ChatBody(BaseModel):
    message: str = Field(min_length=1)
    history: list[ChatHistoryItem] = Field(default_factory=list)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatImageOut(BaseModel):
    url: str
    caption: str = ""
    shot_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    actions: list[str] = Field(default_factory=list)
    project: Project
    shots: list[Shot] = Field(default_factory=list)
    images: list[ChatImageOut] = Field(default_factory=list)
    thinking: str = ""
    steps: list[str] = Field(default_factory=list)


class ChatSessionStatus(BaseModel):
    active: bool
    session_id: str | None = None
    started_at: str | None = None


class RejectLayoutBody(BaseModel):
    feedback: str = ""


class ReviewLayoutReferenceBody(BaseModel):
    status: LayoutReviewStatus
    feedback: str = ""
    human_override: bool = False


class SelectLayoutReferenceBody(BaseModel):
    selected_for_h3: bool


class ApproveLayoutBody(BaseModel):
    """Optional body for layout approve.

    rewrite_prompt: when true, wakes LLM and rewrites six-section prompt
    (slow / requires Ollama). Default is false (cold path).
    """

    rewrite_prompt: bool | None = None
    layout_asset_id: str | None = None


class ShotPatchBody(BaseModel):
    refs: list[ShotRef] | None = None
    voice_refs: list[ShotVoiceRef] | None = None
    prompt_sections: PromptSections | None = None
    duration_s: float | None = None
    dialogue: list[str] | None = None
    title: str | None = None
    script_beat: str | None = None
    shot_type: str | None = None
    camera_angle: str | None = None
    camera_motion: str | None = None
    composition: str | None = None
    feedback: str | None = None
    layout_asset_id: str | None = None
    scene_id: str | None = None
    source_audio_path: str | None = None


class ShotMaterialSelection(BaseModel):
    role: RefRole
    asset_id: str = Field(min_length=1)
    file_key: str | None = None


class ReplaceShotMaterialsBody(BaseModel):
    materials: list[ShotMaterialSelection] = Field(max_length=9)


class ProjectDetailResponse(BaseModel):
    project: Project
    shots: list[Shot] = Field(default_factory=list)


class SubmitResponse(BaseModel):
    shot: Shot
    job_id: str


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


class OllamaPlanProvider:
    """Production PlanProvider backed by local Ollama."""

    def __init__(self, model: str | None = None) -> None:
        # Optional fixed model (tests); otherwise always use live runtime selection.
        self._fixed_model = model
        self.client = OllamaClient()

    @property
    def model(self) -> str:
        if self._fixed_model:
            return self._fixed_model
        from ..core.vram.director_model import get_director_model

        return get_director_model()

    async def complete(
        self,
        system: str,
        user: str,
        *,
        guides: Iterable[str] = (),
    ) -> str:
        prompt = with_director_skill(f"{system}\n\n{user}", guides=guides)
        return await self.client.generate(self.model, prompt)

    async def complete_with_images(
        self,
        system: str,
        user: str,
        *,
        images: list[str],
        guides: Iterable[str] = (),
    ) -> str:
        prompt = with_director_skill(f"{system}\n\n{user}", guides=guides)
        return await self.client.chat(
            self.model,
            prompt,
            images=images,
            require_vision=True,
        )


def get_director_service(request: Request) -> DirectorService:
    """Resolve DirectorService; tests inject via ``app.state.director_service``."""
    svc = getattr(request.app.state, "director_service", None)
    if svc is not None:
        return svc
    return DirectorService(plan_provider=OllamaPlanProvider())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_shot(shot_id: str) -> Shot:
    for project in list_projects():
        shot = load_shot(project.id, shot_id)
        if shot is not None:
            return shot
    raise HTTPException(404, "Shot not found")


def _validate_voice_refs(shot: Shot) -> list[tuple[ShotVoiceRef, LibraryAsset, Path]]:
    resolved: list[tuple[ShotVoiceRef, LibraryAsset, Path]] = []
    total_duration = 0.0
    for ref in shot.voice_refs:
        asset = load_asset("voices", ref.asset_id)
        if asset is None:
            raise ValueError(f"Voice asset not found: {ref.asset_id}")
        if asset.kind != "voices":
            raise ValueError(f"asset is not a Voice reference: {ref.asset_id}")
        if asset.project_id != shot.project_id:
            raise ValueError(f"Voice asset belongs to another project: {ref.asset_id}")
        if not bool((asset.meta or {}).get("h3_ready")):
            raise ValueError(f"Voice asset is not H3-ready: {ref.asset_id}")
        filename = (asset.files or {}).get(ref.file_key)
        if not filename:
            raise ValueError(
                f"Voice asset missing file key {ref.file_key!r}: {ref.asset_id}"
            )
        path = asset_dir("voices", ref.asset_id, project_id=asset.project_id) / filename
        if not path.is_file():
            raise ValueError(f"Voice reference file not found: {ref.asset_id}/{filename}")
        try:
            duration = float((asset.meta or {}).get("duration_s"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Voice asset duration is invalid: {ref.asset_id}") from exc
        total_duration += duration
        resolved.append((ref, asset, path))
    if total_duration > 15.0:
        raise ValueError("Voice reference total duration must not exceed 15 seconds")
    return resolved


def _voice_signature(refs: list[ShotVoiceRef]) -> str:
    return voice_ref_signature(refs)


def _http_value_error(exc: ValueError) -> HTTPException:
    return HTTPException(400, str(exc))


def _update_layout_asset_review(asset_id: str, review_status: str) -> None:
    """Best-effort: set library layout meta.review_status when asset exists."""
    asset = load_asset("layouts", asset_id)
    if asset is None:
        return
    meta = dict(asset.meta or {})
    meta["review_status"] = review_status
    asset.meta = meta
    path = asset_dir("layouts", asset_id) / "asset.json"
    if not path.parent.exists():
        return
    # Drop computed urls for persistence cleanliness
    dump = asset.model_dump()
    dump.pop("urls", None)
    path.write_text(
        LibraryAsset.model_validate(dump).model_dump_json(indent=2),
        encoding="utf-8",
    )


def _load_ref_asset(ref: ShotRef) -> LibraryAsset | None:
    kind = role_to_library_kind(ref.role.value)
    if kind:
        asset = load_asset(kind, ref.asset_id)
        if asset:
            return asset
    for k in LIBRARY_KINDS:
        asset = load_asset(k, ref.asset_id)
        if asset:
            return asset
    return None


def _read_asset_image_bytes(
    asset: LibraryAsset,
    *,
    role: str | None = None,
    file_key: str | None = None,
) -> tuple[str, bytes] | None:
    from ..core.library.images import resolve_asset_image

    hit = resolve_asset_image(asset, role=role, file_key=file_key)
    if not hit:
        return None
    name, data, _key = hit
    return name, data


def _ensure_shot_layout_ref(shot: Shot) -> Shot:
    """Bind active Layouts without stealing non-Layout Picture order."""
    return sync_selected_layout_refs(shot)


def _collect_h3_images(shot: Shot) -> dict[str, tuple[str, bytes]]:
    """Pack every Agent-selected H3 Picture in exact picture_index order."""
    images: dict[str, tuple[str, bytes]] = {}

    ordered = sorted(shot.refs or [], key=lambda r: r.picture_index)
    if len(ordered) > 9:
        raise ValueError("H3 supports at most 9 image refs")
    for ref in ordered:
        asset = _load_ref_asset(ref)
        if not asset:
            raise ValueError(
                f"missing library asset for ref picture {ref.picture_index}: {ref.asset_id}"
            )
        pair = _read_asset_image_bytes(
            asset,
            role=ref.role.value,
            file_key=ref.file_key,
        )
        if not pair:
            raise ValueError(
                f"no image file for ref picture {ref.picture_index}: {ref.asset_id}"
            )
        images[f"ref_{len(images)}"] = pair

    if not images:
        raise ValueError("at least one image ref is required for H3 submit")
    return images


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@router.post("/projects", response_model=Project)
async def create_project_endpoint(body: CreateProjectBody) -> Project:
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    return create_project(name, body.script_text or "", mode=body.mode)


@router.get("/projects", response_model=list[Project])
async def list_projects_endpoint() -> list[Project]:
    return list_projects()


@router.get("/projects/{project_id}", response_model=ProjectDetailResponse)
async def get_project_endpoint(project_id: str) -> ProjectDetailResponse:
    project = load_project(project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return ProjectDetailResponse(project=project, shots=list_shots(project_id))


@router.patch("/projects/{project_id}", response_model=Project)
async def update_project_endpoint(
    project_id: str,
    body: UpdateProjectBody,
) -> Project:
    """Update project metadata, subject to the approved-screenplay lock."""
    project = load_project(project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    updates: dict[str, Any] = {}
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "name cannot be empty")
        updates["name"] = name
    explicitly_unlocking = (
        "script_locked" in body.model_fields_set and body.script_locked is False
    )
    script_is_changing = (
        body.script_text is not None and body.script_text != project.script_text
    )
    if project.script_locked and script_is_changing and not explicitly_unlocking:
        raise HTTPException(
            409,
            "The screenplay is locked; explicitly unlock it before changing script_text.",
        )
    if body.script_text is not None:
        updates["script_text"] = body.script_text
    if body.script_locked is not None:
        updates["script_locked"] = body.script_locked
    if not updates:
        return project
    project = project.model_copy(update=updates)
    save_project(project)
    return project


@router.post("/projects/{project_id}/plan", response_model=ProjectDetailResponse)
async def plan_project_endpoint(
    project_id: str,
    svc: DirectorService = Depends(get_director_service),
) -> ProjectDetailResponse:
    if load_project(project_id) is None:
        raise HTTPException(404, "Project not found")
    try:
        project = await svc.plan_project(project_id)
    except ValueError as e:
        raise _http_value_error(e) from e
    except Exception as e:
        logger.exception("plan_project failed for %s", project_id)
        raise HTTPException(503, f"Director plan failed: {e}") from e
    return ProjectDetailResponse(project=project, shots=list_shots(project_id))


def _chat_result_to_response(result) -> ChatResponse:
    assert result.project is not None
    return ChatResponse(
        reply=result.reply,
        actions=result.actions,
        project=result.project,
        shots=result.shots,
        images=[
            ChatImageOut(url=img.url, caption=img.caption, shot_id=img.shot_id)
            for img in (result.images or [])
        ],
        thinking=getattr(result, "thinking", "") or "",
        steps=list(getattr(result, "steps", None) or []),
    )


async def _make_chat_fn(on_progress=None):
    """Build chat_fn that streams tokens/thinking into on_progress when possible.

    Supports optional ``images`` (list of base64) for multimodal models.
    """
    from ..core.vram import get_orchestrator
    from ..core.vram.director_model import get_director_model

    orch = get_orchestrator()

    async def chat_fn(
        system: str,
        user: str,
        images: list[str] | None = None,
        **_kwargs,
    ) -> str | dict:
        guides = tuple(_kwargs.get("guides") or ())
        system = with_director_skill(system, guides=guides)
        # When images are present, keep system separate for /api/chat.
        # Text-only keeps the combined prompt for /api/generate compatibility.
        use_images = list(images or [])
        prompt = user if use_images else f"{system}\n\n{user}"
        # Multi-turn residency: keep Ollama loaded unless DS_LLM_KEEP_LOADED=false
        keep = bool(getattr(settings, "llm_keep_loaded", True))

        async def _runtime(text: str) -> None:
            if on_progress:
                await on_progress({"type": "runtime", "text": text})

        async with orch.llm_session(
            release_on_exit=not keep,
            on_status=_runtime,
            fail_if_generation_pending=True,
        ):
            # Always (re)load / verify GPU residency after Comfy may have unloaded it
            await orch.ensure_llm_ready(on_status=_runtime)
            plan_model = get_director_model()
            if use_images:
                label = f"Thinking with {plan_model} · {len(use_images)} image{'s' if len(use_images) != 1 else ''}…"
            else:
                label = f"Thinking with {plan_model}…"
            await _runtime(label)
            tools = list(_kwargs.get("tools") or [])
            require_vision = bool(_kwargs.get("require_vision"))
            provided_messages = _kwargs.get("messages")
            response_format = _kwargs.get("format")
            forced_tool_name = ""
            forced_tool_schema: dict | None = None
            if len(tools) == 1 and provided_messages is None:
                function = tools[0].get("function") or {}
                if function.get("name") in {"queue_gpt_ref_frame", "queue_actor_design"}:
                    forced_tool_name = str(function.get("name"))
                    forced_tool_schema = {
                        "type": "object",
                        "properties": {
                            "tool": {
                                "type": "string",
                                "const": forced_tool_name,
                            },
                            "params": function.get("parameters") or {
                                "type": "object"
                            },
                        },
                        "required": ["tool", "params"],
                        "additionalProperties": False,
                    }

            # Function calling, tool-result turns, and schema-constrained output
            # all use Ollama's native chat API. Ordinary text chat can continue
            # through the streaming generate path below.
            if (
                tools
                or provided_messages is not None
                or response_format is not None
                or require_vision
            ):
                if provided_messages is not None:
                    messages = [dict(item) for item in provided_messages]
                else:
                    user_message: dict = {"role": "user", "content": user}
                    if use_images:
                        user_message["images"] = use_images
                    messages = [user_message]
                if forced_tool_schema is not None:
                    messages[-1]["content"] = (
                        f"{messages[-1]['content']}\n\n"
                        "Return exactly one JSON object matching the supplied schema. "
                        "The application will validate and execute it. Do not describe "
                        "the call, wrap it in markdown, or claim it succeeded."
                    )
                if system and not (
                    messages and messages[0].get("role") == "system"
                ):
                    messages.insert(0, {"role": "system", "content": system})
                try:
                    result = await orch.ollama.chat_response(
                        plan_model,
                        messages=messages,
                        tools=None if forced_tool_schema is not None else tools or None,
                        format=forced_tool_schema or response_format,
                        require_vision=require_vision or bool(use_images),
                    )
                except Exception as exc:
                    if (
                        "XML syntax error" not in str(exc)
                        or provided_messages is not None
                        or use_images
                        or not tools
                    ):
                        raise
                    await _runtime(
                        "Native tool formatting failed; retrying once with the text tool protocol…"
                    )
                    fallback_prompt = (
                        f"{system}\n\n{user}\n\n"
                        "AVAILABLE_TOOLS_JSON:\n"
                        f"{json.dumps(tools, ensure_ascii=False)}\n\n"
                        "Return the requested state-changing action as one fenced JSON "
                        'object shaped exactly like {"tool":"tool_name","params":{...}}. '
                        "Do not claim the action succeeded; the application will validate "
                        "and execute it."
                    )
                    return await orch.ollama.generate(plan_model, fallback_prompt)
                if on_progress:
                    if result.get("thinking"):
                        await on_progress(
                            {"type": "think", "text": result["thinking"]}
                        )
                    if result.get("content"):
                        await on_progress(
                            {"type": "token", "text": result["content"]}
                        )
                return result

            # Prefer streaming so UI can show tokens live
            if on_progress and hasattr(orch.ollama, "generate_stream"):
                parts: list[str] = []
                think_buf: list[str] = []
                in_think = False
                try:
                    async for chunk in orch.ollama.generate_stream(
                        plan_model,
                        prompt,
                        images=use_images or None,
                        system=system if use_images else None,
                    ):
                        kind = chunk.get("kind") if isinstance(chunk, dict) else "token"
                        text = (
                            chunk.get("text")
                            if isinstance(chunk, dict)
                            else str(chunk)
                        ) or ""
                        if not text:
                            continue
                        if kind == "think":
                            think_buf.append(text)
                            await on_progress({"type": "think", "text": text})
                            continue
                        # Detect inline <think> tags while streaming tokens
                        parts.append(text)
                        lower = text.lower()
                        if "<think" in lower:
                            in_think = True
                        if in_think:
                            await on_progress({"type": "think", "text": text})
                        else:
                            await on_progress({"type": "token", "text": text})
                        if "</think" in lower or "</thinking" in lower:
                            in_think = False
                    raw = "".join(parts)
                    if think_buf and "<think>" not in raw.lower():
                        raw = f"<think>{''.join(think_buf)}</think>\n{raw}"
                    return raw
                except Exception:
                    logger.exception("stream generate failed; falling back to non-stream")
            if use_images:
                return await orch.ollama.chat(
                    plan_model, user, system=system, images=use_images
                )
            return await orch.ollama.generate(plan_model, prompt)

    return chat_fn


@router.post("/projects/{project_id}/chat", response_model=ChatResponse)
async def project_chat_endpoint(
    project_id: str,
    body: ChatBody,
    svc: DirectorService = Depends(get_director_service),
) -> ChatResponse:
    """
    Chat with the Director agent for this project.

    Natural language drives plan / reference-frame / approve / reject / status / script save.
    """
    if load_project(project_id) is None:
        raise HTTPException(404, "Project not found")
    msg = (body.message or "").strip()
    if not msg:
        raise HTTPException(400, "message is required")
    try:
        await _assert_chat_available()
    except GenerationActiveError as exc:
        raise _generation_active_http(exc) from exc
    if (await director_chat_sessions.snapshot(project_id)).active:
        raise HTTPException(
            409,
            "Director chat is already running for this project",
        )

    from ..agents.director.chat import handle_chat

    stored_history = load_chat_history(project_id)
    history = agent_history(stored_history)
    if not history:
        history = [{"role": h.role, "content": h.content} for h in (body.history or [])]
    chat_fn = await _make_chat_fn(on_progress=None)
    append_chat_message(project_id, role="user", content=msg)

    try:
        result = await handle_chat(
            project_id=project_id,
            message=msg,
            svc=svc,
            chat_fn=chat_fn,
            history=history,
        )
    except ValueError as e:
        raise _http_value_error(e) from e
    except GenerationActiveError as e:
        raise _generation_active_http(e) from e
    except Exception as e:
        logger.exception("project chat failed for %s", project_id)
        raise HTTPException(503, f"Director chat failed: {e}") from e

    response = _chat_result_to_response(result)
    append_chat_message(
        project_id,
        role="assistant",
        content=response.reply,
        images=[DirectorChatImage.model_validate(image.model_dump()) for image in response.images],
    )
    return response


@router.get(
    "/projects/{project_id}/chat/history",
    response_model=list[DirectorChatMessage],
)
async def project_chat_history_endpoint(project_id: str) -> list[DirectorChatMessage]:
    if load_project(project_id) is None:
        raise HTTPException(404, "Project not found")
    return load_chat_history(project_id)


@router.get(
    "/projects/{project_id}/chat/session",
    response_model=ChatSessionStatus,
)
async def project_chat_session_endpoint(project_id: str) -> ChatSessionStatus:
    if load_project(project_id) is None:
        raise HTTPException(404, "Project not found")
    return ChatSessionStatus.model_validate(
        asdict(await director_chat_sessions.snapshot(project_id))
    )


@router.post(
    "/projects/{project_id}/chat/session/cancel",
    response_model=ChatSessionStatus,
)
async def cancel_project_chat_session_endpoint(project_id: str) -> ChatSessionStatus:
    if load_project(project_id) is None:
        raise HTTPException(404, "Project not found")
    await director_chat_sessions.cancel(project_id)
    return ChatSessionStatus(active=False)


async def _persist_chat_images(
    project_id: str,
    uploads: list[UploadFile],
) -> ChatUploadBatch:
    if len(uploads) > MAX_CHAT_IMAGES:
        raise HTTPException(400, f"Director chat accepts at most {MAX_CHAT_IMAGES} images")

    validated: list[tuple[str, bytes]] = []
    max_bytes = settings.max_upload_mb * 1024 * 1024
    for index, upload in enumerate(uploads, start=1):
        original_name = Path(upload.filename or f"image-{index}.png").name
        extension = Path(original_name).suffix.lower()
        if extension not in CHAT_IMAGE_EXTENSIONS:
            raise HTTPException(400, f"{original_name}: unsupported image type")
        data = await upload.read()
        if not data:
            raise HTTPException(400, f"{original_name}: empty file")
        if len(data) > max_bytes:
            raise HTTPException(
                400,
                f"{original_name}: file exceeds {settings.max_upload_mb}MB",
            )
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
                image_format = (image.format or "").upper()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise HTTPException(400, f"{original_name}: invalid image") from exc
        if image_format not in CHAT_IMAGE_FORMATS:
            raise HTTPException(400, f"{original_name}: unsupported image type")
        validated.append((original_name, data))

    if not validated:
        return ChatUploadBatch([], [], [])

    batch_id = f"upl_{uuid.uuid4().hex}"
    upload_dir = ensure_project_tree(project_id) / "agent" / "chat_uploads" / batch_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    encoded: list[str] = []
    captions: list[str] = []
    history_images: list[DirectorChatImage] = []
    for index, (original_name, data) in enumerate(validated, start=1):
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(original_name).stem).strip(".-")
        safe_stem = safe_stem[:80] or f"image-{index}"
        extension = Path(original_name).suffix.lower()
        stored_name = f"{index:02d}-{safe_stem}{extension}"
        (upload_dir / stored_name).write_bytes(data)
        encoded.append(base64.b64encode(data).decode("ascii"))
        captions.append(original_name)
        history_images.append(
            DirectorChatImage(
                url=(
                    f"/api/files/projects/{project_id}/agent/chat_uploads/"
                    f"{batch_id}/{stored_name}"
                ),
                caption=original_name,
            )
        )
    return ChatUploadBatch(encoded, captions, history_images, upload_dir)


def _parse_chat_history(raw: str) -> list[ChatHistoryItem]:
    try:
        payload = json.loads(raw or "[]")
        if not isinstance(payload, list):
            raise ValueError
        return [ChatHistoryItem.model_validate(item) for item in payload]
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise HTTPException(400, "history must be a JSON array of chat messages") from exc


async def _project_chat_stream_response(
    *,
    project_id: str,
    message: str,
    request_history: list[ChatHistoryItem],
    svc: DirectorService,
    user_images_b64: list[str] | None = None,
    user_image_captions: list[str] | None = None,
    user_history_images: list[DirectorChatImage] | None = None,
    user_upload_dir: Path | None = None,
):
    from ..agents.director.chat import handle_chat

    if load_project(project_id) is None:
        raise HTTPException(404, "Project not found")
    msg = (message or "").strip()
    if not msg:
        raise HTTPException(400, "message is required")
    try:
        await _assert_chat_available()
    except GenerationActiveError as exc:
        _cleanup_chat_upload_batch(project_id, user_upload_dir)
        raise _generation_active_http(exc) from exc

    stored_history = load_chat_history(project_id)
    history = agent_history(stored_history)
    if not history:
        history = [
            {"role": item.role, "content": item.content}
            for item in request_history
        ]
    queue: asyncio.Queue = asyncio.Queue()

    async def on_progress(ev: dict) -> None:
        await queue.put(ev)

    chat_fn = await _make_chat_fn(on_progress=on_progress)
    try:
        session = await director_chat_sessions.reserve(project_id)
    except DirectorChatSessionConflict as exc:
        _cleanup_chat_upload_batch(project_id, user_upload_dir)
        raise HTTPException(
            409,
            "Director chat is already running for this project",
        ) from exc

    append_chat_message(
        project_id,
        role="user",
        content=msg,
        images=list(user_history_images or []),
    )

    async def runner() -> None:
        try:
            result = await handle_chat(
                project_id=project_id,
                message=msg,
                svc=svc,
                chat_fn=chat_fn,
                history=history,
                on_progress=on_progress,
                user_images_b64=list(user_images_b64 or []),
                user_image_captions=list(user_image_captions or []),
            )
            response = _chat_result_to_response(result)
            append_chat_message(
                project_id,
                role="assistant",
                content=response.reply,
                images=[
                    DirectorChatImage.model_validate(image.model_dump())
                    for image in response.images
                ],
            )
            await queue.put(
                {"type": "result", "data": response.model_dump(mode="json")}
            )
        except asyncio.CancelledError:
            raise
        except GenerationActiveError as exc:
            await queue.put(
                {
                    "type": "error",
                    "code": exc.code,
                    "message": "Local image or video generation is using the GPU.",
                    "generation_count": len(exc.reservations),
                }
            )
        except ValueError as exc:
            await queue.put({"type": "error", "message": str(exc)})
        except Exception as exc:
            logger.exception("project chat stream failed for %s", project_id)
            await queue.put(
                {"type": "error", "message": f"Director chat failed: {exc}"}
            )
        finally:
            await director_chat_sessions.finish(project_id, session.session_id or "")
            await queue.put(None)

    async def event_gen():
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    task = asyncio.create_task(runner())
    try:
        await director_chat_sessions.attach(
            project_id,
            session.session_id or "",
            task,
        )
    except Exception:
        await director_chat_sessions.finish(project_id, session.session_id or "")
        raise
    _background_chat_tasks.add(task)
    task.add_done_callback(_background_chat_tasks.discard)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/projects/{project_id}/chat/stream/images")
async def project_chat_image_stream_endpoint(
    project_id: str,
    message: str = Form(...),
    history: str = Form("[]"),
    images: list[UploadFile] = File(...),
    svc: DirectorService = Depends(get_director_service),
):
    if load_project(project_id) is None:
        raise HTTPException(404, "Project not found")
    try:
        await _assert_chat_available()
    except GenerationActiveError as exc:
        raise _generation_active_http(exc) from exc
    batch = await _persist_chat_images(project_id, images)
    if not batch.encoded:
        raise HTTPException(400, "at least one image is required")
    return await _project_chat_stream_response(
        project_id=project_id,
        message=message,
        request_history=_parse_chat_history(history),
        svc=svc,
        user_images_b64=batch.encoded,
        user_image_captions=batch.captions,
        user_history_images=batch.history_images,
        user_upload_dir=batch.directory,
    )


@router.post("/projects/{project_id}/chat/stream")
async def project_chat_stream_endpoint(
    project_id: str,
    body: ChatBody,
    svc: DirectorService = Depends(get_director_service),
):
    """
    SSE stream of Director chat progress.

    Events (JSON in ``data:`` lines):
    - status / think / token / tool — live progress
    - result — final ChatResponse payload
    - error — {message}
    """
    return await _project_chat_stream_response(
        project_id=project_id,
        message=body.message,
        request_history=body.history,
        svc=svc,
    )


# ---------------------------------------------------------------------------
# Shots
# ---------------------------------------------------------------------------


@router.get("/shots/{shot_id}", response_model=Shot)
async def get_shot_endpoint(shot_id: str) -> Shot:
    return _find_shot(shot_id)


@router.post("/shots/{shot_id}/plan", response_model=ProjectDetailResponse)
async def plan_shot_endpoint(
    shot_id: str,
    svc: DirectorService = Depends(get_director_service),
) -> ProjectDetailResponse:
    """Replan parent project (v1: full project replan via DirectorService)."""
    shot = _find_shot(shot_id)
    try:
        project = await svc.plan_project(shot.project_id)
    except ValueError as e:
        raise _http_value_error(e) from e
    except Exception as e:
        logger.exception("plan_shot failed for %s", shot_id)
        raise HTTPException(503, f"Director plan failed: {e}") from e
    return ProjectDetailResponse(project=project, shots=list_shots(project.id))


@router.post("/shots/{shot_id}/ref-frame", response_model=list[Shot])
async def queue_ref_frame_endpoint(
    shot_id: str,
    svc: DirectorService = Depends(get_director_service),
) -> list[Shot]:
    shot = _find_shot(shot_id)
    try:
        return await svc.queue_ref_frames(shot.project_id, shot_ids=[shot_id])
    except ValueError as e:
        raise _http_value_error(e) from e
    except Exception as e:
        logger.exception("queue_ref_frame failed for %s", shot_id)
        raise HTTPException(503, f"Reference-frame queue failed: {e}") from e


@router.post("/shots/{shot_id}/layouts", response_model=Shot)
async def queue_layout_endpoint(
    shot_id: str,
    brief: LayoutBrief,
    svc: DirectorService = Depends(get_director_service),
) -> Shot:
    _find_shot(shot_id)
    try:
        return await svc.queue_reference_frame(shot_id, brief=brief)
    except ValueError as e:
        raise _http_value_error(e) from e
    except Exception as e:
        logger.exception("queue_layout failed for %s", shot_id)
        raise HTTPException(503, f"Layout queue failed: {e}") from e


@router.delete(
    "/shots/{shot_id}/layouts/{layout_ref_id}",
    response_model=Shot,
)
async def delete_layout_reference_endpoint(
    shot_id: str,
    layout_ref_id: str,
) -> Shot:
    """Remove one unwanted Layout from the Shot and delete its private asset."""
    shot = _find_shot(shot_id)
    target = next(
        (layout for layout in shot.layout_refs if layout.id == layout_ref_id),
        None,
    )
    if target is None:
        raise HTTPException(404, f"LayoutReference not found: {layout_ref_id}")
    if target.job_status in {
        JobStatus.queued,
        JobStatus.uploading,
        JobStatus.running,
    }:
        raise HTTPException(409, "Cannot delete a Layout while its job is active")

    remaining_layouts = [
        layout for layout in shot.layout_refs if layout.id != layout_ref_id
    ]
    remaining_refs = [
        ref
        for ref in shot.refs
        if not (
            ref.role == RefRole.layout_ref_frame
            and target.asset_id
            and ref.asset_id == target.asset_id
        )
    ]
    remaining_refs = [
        ref.model_copy(update={"picture_index": index})
        for index, ref in enumerate(
            sorted(remaining_refs, key=lambda item: item.picture_index),
            start=1,
        )
    ]
    updated = shot.model_copy(
        update={"layout_refs": remaining_layouts, "refs": remaining_refs}
    )
    if target.asset_id and target.asset_id == shot.layout_asset_id:
        updated = updated.model_copy(
            update={
                "layout_asset_id": None,
                "layout_review_status": None,
                "ref_frame_job_id": None,
            }
        )
    if remaining_layouts:
        updated = sync_selected_layout_refs(updated)
    else:
        updated = updated.model_copy(
            update={
                "layout_asset_id": None,
                "layout_review_status": None,
                "ref_frame_job_id": None,
            }
        )
    save_shot(updated)

    if target.asset_id and load_asset("layouts", target.asset_id) is not None:
        asset_is_still_used = any(
            any(layout.asset_id == target.asset_id for layout in candidate.layout_refs)
            or any(ref.asset_id == target.asset_id for ref in candidate.refs)
            for project in list_projects()
            for candidate in list_shots(project.id)
        )
        if not asset_is_still_used:
            delete_asset("layouts", target.asset_id)
    return updated


@router.post(
    "/shots/{shot_id}/layouts/{layout_ref_id}/review",
    response_model=Shot,
)
async def review_layout_reference_endpoint(
    shot_id: str,
    layout_ref_id: str,
    body: ReviewLayoutReferenceBody,
) -> Shot:
    shot = _find_shot(shot_id)
    target = next(
        (layout for layout in shot.layout_refs if layout.id == layout_ref_id),
        None,
    )
    try:
        updated = review_layout_reference(
            shot,
            layout_ref_id,
            body.status,
            body.feedback,
            human_override=body.human_override,
        )
        updated = sync_selected_layout_refs(updated)
    except ValueError as exc:
        raise _http_value_error(exc) from exc
    if target and target.asset_id:
        _update_layout_asset_review(target.asset_id, body.status.value)
    save_shot(updated)
    return updated


@router.post(
    "/shots/{shot_id}/layouts/{layout_ref_id}/selection",
    response_model=Shot,
)
async def select_layout_reference_endpoint(
    shot_id: str,
    layout_ref_id: str,
    body: SelectLayoutReferenceBody,
) -> Shot:
    shot = _find_shot(shot_id)
    try:
        updated = select_layout_reference(
            shot,
            layout_ref_id,
            body.selected_for_h3,
        )
        updated = sync_selected_layout_refs(updated)
    except ValueError as exc:
        raise _http_value_error(exc) from exc
    save_shot(updated)
    return updated


@router.post("/shots/{shot_id}/layout/insert", response_model=Shot)
async def insert_layout_ref_frame(
    shot_id: str,
    file: UploadFile = File(...),
    name: str = Form(""),
    notes: str = Form(""),
    approve: bool = Form(
        True,
        description="When true, mark layout approved and bind it as a ref (any free Picture).",
    ),
) -> Shot:
    """Insert an external / pre-made layout still as this shot's reference frame.

    Does not require Comfy or the ref_frame pipeline. Layout remains optional for
    H3 submit — leave ``approve=false`` to only attach for review.
    """
    shot = _find_shot(shot_id)
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > 40 * 1024 * 1024:
        raise HTTPException(400, "file too large (max 40 MB)")

    raw_name = Path(file.filename or "layout.png").name
    safe = re.sub(r"[^\w.\-]+", "_", raw_name)[:120] or "layout.png"
    label = (name or "").strip() or f"layout:{shot.title or shot.id}"

    try:
        asset = create_external_asset(
            kind="layouts",
            name=label,
            notes=(notes or "").strip() or f"Inserted reference frame for {shot.id}",
            project_id=shot.project_id,
            image_bytes=data,
            image_filename=safe,
            file_key="layout",
            source_filename=file.filename or safe,
        )
    except ValueError as e:
        raise _http_value_error(e) from e

    # Always stamp review status on the asset
    review = "approved" if approve else "pending_review"
    meta = dict(asset.meta or {})
    meta["review_status"] = review
    meta["inserted_for_shot"] = shot.id
    asset = write_asset(asset.model_copy(update={"meta": meta}))

    inserted_layout = LayoutReference(
        id=f"lref_{uuid.uuid4().hex[:12]}",
        asset_id=asset.id,
        review_status=(
            LayoutReviewStatus.usable
            if approve
            else LayoutReviewStatus.pending_review
        ),
        selected_for_h3=approve,
    )
    working = shot.model_copy(
        update={"layout_refs": [*shot.layout_refs, inserted_layout]}
    )
    working = mirror_legacy_layout_fields(
        working,
        compatibility_primary_layout_id=inserted_layout.id,
    )
    try:
        working = sync_selected_layout_refs(working)
    except ValueError as e:
        raise _http_value_error(e) from e
    if approve:
        # After insert+approve, ready for Gate 2 prompt review (or already past plan)
        next_status = working.status
        if next_status in (
            ShotStatus.ref_frame_pending,
            ShotStatus.needs_review,
            ShotStatus.draft,
            ShotStatus.blocked,
            ShotStatus.planning,
        ):
            next_status = ShotStatus.needs_review
        working = working.model_copy(
            update={
                "status": next_status,
            }
        )
    save_shot(working)
    return working


@router.post("/shots/{shot_id}/layout/skip", response_model=Shot)
async def skip_layout_endpoint(shot_id: str) -> Shot:
    """Skip Gate 1 (no reference frame) and move shot to needs_review for Gate 2 / H3."""
    shot = _find_shot(shot_id)
    try:
        shot = apply_transition(shot, "skip_layout")
    except ValueError as e:
        raise _http_value_error(e) from e
    save_shot(shot)
    return shot


@router.post("/shots/{shot_id}/ref-frame/approve", response_model=Shot)
async def approve_ref_frame_endpoint(
    shot_id: str,
    rewrite_prompt: bool = Query(
        False,
        description=(
            "When true, wake LLM and rewrite six-section prompt after layout approve. "
            "Default false so Gate 1 works with LLM cold."
        ),
    ),
    body: ApproveLayoutBody | None = None,
    svc: DirectorService = Depends(get_director_service),
) -> Shot:
    """Gate 1: approve layout reference-frame.

    Default ``rewrite_prompt=false`` — no Ollama required.
    Set query/body ``rewrite_prompt=true`` to call write_prompts_after_layout
    (may take time while the LLM loads).
    """
    shot = _find_shot(shot_id)
    do_rewrite = rewrite_prompt
    layout_asset_id = shot.layout_asset_id
    if body is not None:
        if body.rewrite_prompt is not None:
            do_rewrite = body.rewrite_prompt
        if body.layout_asset_id:
            layout_asset_id = body.layout_asset_id

    payload: dict[str, Any] = {}
    if layout_asset_id and layout_asset_id != shot.layout_asset_id:
        payload["layout_asset_id"] = layout_asset_id

    try:
        shot = apply_transition(shot, "approve_layout", **payload)
    except ValueError as e:
        raise _http_value_error(e) from e

    if shot.layout_asset_id:
        _update_layout_asset_review(shot.layout_asset_id, "approved")

    save_shot(shot)

    if do_rewrite:
        try:
            shot = await svc.write_prompts_after_layout(shot.id)
        except ValueError as e:
            raise _http_value_error(e) from e
        except Exception as e:
            logger.exception("write_prompts_after_layout failed for %s", shot_id)
            raise HTTPException(
                503,
                f"Layout approved but prompt rewrite failed: {e}",
            ) from e

    return shot


@router.post("/shots/{shot_id}/ref-frame/reject", response_model=Shot)
async def reject_ref_frame_endpoint(
    shot_id: str,
    body: RejectLayoutBody | None = None,
) -> Shot:
    """Gate 1 fail: mark layout rejected; default status ref_frame_pending."""
    shot = _find_shot(shot_id)
    reviewed_asset_id = shot.layout_asset_id
    feedback = (body.feedback if body else "") or ""
    try:
        shot = apply_transition(
            shot,
            "reject_layout",
            feedback=feedback,
            status=ShotStatus.ref_frame_pending,
        )
    except ValueError as e:
        raise _http_value_error(e) from e

    if reviewed_asset_id:
        _update_layout_asset_review(reviewed_asset_id, "rejected")

    save_shot(shot)
    return shot


@router.patch("/shots/{shot_id}", response_model=Shot)
async def patch_shot_endpoint(shot_id: str, body: ShotPatchBody) -> Shot:
    """Edit refs, prompt sections, duration, dialogue, etc. (LLM not required)."""
    shot = _find_shot(shot_id)
    updates: dict[str, Any] = {}
    data = body.model_dump(exclude_unset=True)
    for key, val in data.items():
        if val is not None:
            updates[key] = val
    if "prompt_sections" in updates and isinstance(updates["prompt_sections"], dict):
        updates["prompt_sections"] = PromptSections.model_validate(
            updates["prompt_sections"]
        )
    if "refs" in updates and isinstance(updates["refs"], list):
        updates["refs"] = [
            r if isinstance(r, ShotRef) else ShotRef.model_validate(r)
            for r in updates["refs"]
        ]
    if "voice_refs" in updates and isinstance(updates["voice_refs"], list):
        updates["voice_refs"] = [
            ref
            if isinstance(ref, ShotVoiceRef)
            else ShotVoiceRef.model_validate(ref)
            for ref in updates["voice_refs"]
        ]
    if not updates:
        return shot
    payload = shot.model_dump(mode="python")
    payload.update(updates)
    shot = Shot.model_validate(payload)
    if "voice_refs" in updates:
        try:
            _validate_voice_refs(shot)
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        meta = dict(shot.meta or {})
        meta["prompt_voice_signature"] = ""
        shot = shot.model_copy(update={"meta": meta})
    save_shot(shot)
    return shot


@router.put("/shots/{shot_id}/materials", response_model=Shot)
async def replace_shot_materials_endpoint(
    shot_id: str,
    body: ReplaceShotMaterialsBody,
    rewrite_prompt: bool = Query(
        False,
        description="Rewrite the six-section H3 prompt from the saved Picture inventory.",
    ),
    svc: DirectorService = Depends(get_director_service),
) -> Shot:
    """Replace a Shot's Pictures and optionally rewrite its H3 prompt."""
    shot = _find_shot(shot_id)
    seen: set[tuple[str, str, str]] = set()
    resolved: list[tuple[ShotMaterialSelection, LibraryAsset]] = []
    for material in body.materials:
        role = material.role.value
        kind = role_to_library_kind(role)
        if kind not in LIBRARY_KINDS:
            raise HTTPException(400, f"unsupported Picture role: {role}")
        asset = load_asset(kind, material.asset_id)
        if asset is None:
            raise HTTPException(404, f"Library asset not found: {kind}/{material.asset_id}")
        if asset.project_id not in (None, shot.project_id):
            raise HTTPException(400, f"asset belongs to another project: {material.asset_id}")
        file_key = (material.file_key or "").strip()
        if file_key and not (asset.files or {}).get(file_key):
            raise HTTPException(
                400,
                f"asset {material.asset_id} has no file_key {file_key!r}",
            )
        identity = (role, material.asset_id, file_key)
        if identity in seen:
            raise HTTPException(400, f"duplicate Picture material: {material.asset_id}")
        seen.add(identity)
        resolved.append((material, asset))

    selected_layout_ids = {
        material.asset_id
        for material, _asset in resolved
        if material.role == RefRole.layout_ref_frame
    }
    existing_layouts = {layout.asset_id: layout for layout in shot.layout_refs if layout.asset_id}
    layout_refs = [
        layout.model_copy(
            update={"selected_for_h3": bool(layout.asset_id in selected_layout_ids)}
        )
        for layout in shot.layout_refs
    ]
    for material, asset in resolved:
        if material.role != RefRole.layout_ref_frame or asset.id in existing_layouts:
            continue
        review_value = str((asset.meta or {}).get("review_status") or "")
        review_status = (
            LayoutReviewStatus.usable
            if review_value in {"approved", "usable"}
            else LayoutReviewStatus.pending_review
        )
        layout_refs.append(
            LayoutReference(
                id=f"lref_{uuid.uuid4().hex[:12]}",
                asset_id=asset.id,
                purpose=asset.name or "Library Layout",
                state_description=asset.notes or "",
                review_status=review_status,
                selected_for_h3=True,
                activation_mode="append",
            )
        )

    non_layout_refs: list[ShotRef] = []
    for material, _asset in resolved:
        if material.role == RefRole.layout_ref_frame:
            continue
        non_layout_refs.append(
            ShotRef(
                role=material.role,
                asset_id=material.asset_id,
                file_key=material.file_key,
                picture_index=len(non_layout_refs) + 1,
                notes="human-selected in Shot materials",
            )
        )

    working_update: dict[str, Any] = {
        "refs": non_layout_refs,
        "layout_refs": layout_refs,
    }
    if not selected_layout_ids:
        # The material editor is authoritative for the active Picture set.
        # Clear the legacy projection so sync_selected_layout_refs cannot
        # interpret an explicitly removed final Layout as a legacy selection.
        working_update.update(
            {
                "layout_asset_id": None,
                "layout_review_status": None,
                "ref_frame_job_id": None,
            }
        )
    working = shot.model_copy(update=working_update)
    try:
        working = sync_selected_layout_refs(working)
    except ValueError as exc:
        raise _http_value_error(exc) from exc
    previous_refs = sorted(shot.refs, key=lambda item: item.picture_index)
    current_refs = sorted(working.refs, key=lambda item: item.picture_index)

    def ref_identity(ref: ShotRef) -> tuple[str, str, str]:
        return (ref.role.value, ref.asset_id, str(ref.file_key or ""))

    previous_by_identity = {ref_identity(ref): ref for ref in previous_refs}
    current_by_identity = {ref_identity(ref): ref for ref in current_refs}

    def ref_payload(ref: ShotRef) -> dict[str, Any]:
        return {
            "role": ref.role.value,
            "asset_id": ref.asset_id,
            "file_key": ref.file_key or "",
            "picture_index": ref.picture_index,
        }

    added = [
        ref_payload(ref)
        for ref in current_refs
        if ref_identity(ref) not in previous_by_identity
    ]
    removed = [
        ref_payload(ref)
        for ref in previous_refs
        if ref_identity(ref) not in current_by_identity
    ]
    reordered = [
        {
            "role": ref.role.value,
            "asset_id": ref.asset_id,
            "file_key": ref.file_key or "",
            "from_picture_index": previous_by_identity[ref_identity(ref)].picture_index,
            "to_picture_index": ref.picture_index,
        }
        for ref in current_refs
        if ref_identity(ref) in previous_by_identity
        and previous_by_identity[ref_identity(ref)].picture_index != ref.picture_index
    ]
    if added or removed or reordered:
        meta = dict(working.meta or {})
        meta["prompt_picture_signature"] = ""
        meta["prompt_layout_signature"] = ""
        meta["material_review_pending"] = True
        meta["material_changes"] = {
            "added": added,
            "removed": removed,
            "reordered": reordered,
        }
        working = working.model_copy(update={"meta": meta})
    save_shot(working)
    if not rewrite_prompt:
        return working
    try:
        return await svc.write_prompts_after_layout(working.id)
    except Exception as exc:
        logger.exception(
            "prompt rewrite failed after saving materials for %s",
            working.id,
        )
        raise HTTPException(
            503,
            f"Materials saved but prompt rewrite failed: {exc}",
        ) from exc


@router.post("/shots/{shot_id}/approve", response_model=Shot)
async def approve_shot_endpoint(shot_id: str) -> Shot:
    """Gate 2: approve full shot for H3 submit (LLM not required)."""
    shot = _find_shot(shot_id)
    try:
        shot = apply_transition(shot, "approve_shot")
    except ValueError as e:
        raise _http_value_error(e) from e
    save_shot(shot)
    return shot


@router.post("/shots/{shot_id}/submit", response_model=Shot)
async def submit_shot_endpoint(
    shot_id: str,
    svc: DirectorService = Depends(get_director_service),
    options: H3SubmitOptions | None = None,
) -> Shot:
    """Queue pure H3 Ref2AV after shot approve + preflight.

    Layout reference-frame is optional. Requires approved status, complete six-section
    prompt, and ≥1 image ref. Stages library ref bytes in picture order.
    If the current layout is newer than the prompt, refreshes the prompt through
    the Director Agent before preflight. Matching prompts do not wake the LLM.
    """
    shot = _find_shot(shot_id)
    h3_provider = str(
        (
            options.h3_provider
            if options and options.h3_provider
            else settings.h3_provider
        )
        or "local"
    ).strip().lower()
    if h3_provider not in {"local", "minimax"}:
        raise HTTPException(400, f"Unsupported H3 provider: {h3_provider}")
    if h3_provider == "minimax" and not str(
        settings.h3_minimax_api_key or ""
    ).strip():
        raise HTTPException(400, "MiniMax H3 API key is not configured")

    # Concurrent submit guard
    if shot.status in (ShotStatus.queued, ShotStatus.running):
        raise HTTPException(409, f"shot already {shot.status.value}")
    if shot.h3_job_id:
        existing = load_job(shot.h3_job_id)
        if existing and existing.status in (
            JobStatus.queued,
            JobStatus.uploading,
            JobStatus.running,
        ):
            raise HTTPException(
                409,
                f"H3 job already active: {shot.h3_job_id} ({existing.status.value})",
            )

    try:
        synchronized = sync_selected_layout_refs(shot)
        selected_layouts = selected_layout_prompt_context(synchronized)
        current_layout_signature = layout_prompt_signature(synchronized)
    except ValueError as e:
        raise _http_value_error(e) from e
    if synchronized.refs != shot.refs:
        shot = synchronized
        save_shot(shot)
    else:
        shot = synchronized

    if bool((shot.meta or {}).get("material_review_pending")):
        raise HTTPException(
            409,
            detail={
                "code": "material_review_required",
                "shot_id": shot.id,
                "message": (
                    "Shot references changed. Ask the Director to review the current "
                    "materials and refresh the H3 prompt before submitting."
                ),
                "changes": (shot.meta or {}).get("material_changes") or {},
            },
        )

    prompt_layout_signature = str(
        (shot.meta or {}).get("prompt_layout_signature") or ""
    )
    layout_contract_present = (
        bool(shot.layout_refs)
        or bool(selected_layouts)
        or any(
            key in (shot.meta or {})
            for key in (
                "prompt_layout_asset_id",
                "prompt_layout_asset_ids",
                "prompt_layout_signature",
            )
        )
    )
    prompt_voice_signature = str(
        (shot.meta or {}).get("prompt_voice_signature") or ""
    )
    prompt_picture_signature = str(
        (shot.meta or {}).get("prompt_picture_signature") or ""
    )
    current_picture_signature = picture_ref_signature(shot.refs)
    picture_contract_present = "prompt_picture_signature" in (shot.meta or {})
    current_voice_signature = _voice_signature(shot.voice_refs)
    voice_contract_present = bool(shot.voice_refs) or (
        "prompt_voice_signature" in (shot.meta or {})
    )
    if (
        (picture_contract_present and prompt_picture_signature != current_picture_signature)
        or (
            layout_contract_present
            and prompt_layout_signature != current_layout_signature
        )
        or (
            not shot.source_audio_path
            and voice_contract_present
            and prompt_voice_signature != current_voice_signature
        )
    ):
        try:
            shot = await svc.write_prompts_after_layout(shot.id)
        except ValueError as e:
            raise _http_value_error(e) from e
        except Exception as e:
            logger.exception(
                "write_prompts_after_layout before H3 submit failed for %s",
                shot_id,
            )
            raise HTTPException(
                503,
                f"Prompt refresh for current layout failed: {e}",
            ) from e

    try:
        assert_h3_submittable(shot)
    except ValueError as e:
        raise _http_value_error(e) from e

    prompt_text = compose_h3_prompt(shot.prompt_sections)
    required_layout_indices = [
        int(item["picture_index"]) for item in selected_layouts
    ]
    try:
        validate_h3_prompt(
            prompt_text,
            list(shot.dialogue),
            audio_count=0 if shot.source_audio_path else len(shot.voice_refs),
            required_picture_indices=required_layout_indices,
            submitted_picture_indices=(
                ref.picture_index for ref in shot.refs
            ),
        )
    except ValueError as e:
        raise _http_value_error(e) from e

    try:
        frames = (
            frames_for_audio_seconds(shot.duration_s)
            if shot.source_audio_path
            else frames_for_seconds(shot.duration_s)
        )
    except ValueError as e:
        raise _http_value_error(e) from e

    try:
        images = _collect_h3_images(shot)
    except ValueError as e:
        raise _http_value_error(e) from e

    image_keys = list(images.keys())
    audio_keys: list[str] = []
    if not shot.source_audio_path:
        try:
            resolved_voice_refs = _validate_voice_refs(shot)
        except ValueError as e:
            raise _http_value_error(e) from e
        for ref, _asset, audio_path in resolved_voice_refs:
            key = f"voice_audio_{ref.audio_index}"
            audio_keys.append(key)
            images[key] = (audio_path.name, audio_path.read_bytes())
    project = load_project(shot.project_id)
    portrait = bool(
        project
        and any(
            marker in project.script_text.lower()
            for marker in ("9:16", "9／16", "竖屏", "vertical")
        )
    )
    width = (
        int(options.width)
        if options is not None and options.width is not None
        else (480 if portrait else 864)
    )
    height = (
        int(options.height)
        if options is not None and options.height is not None
        else (864 if portrait else 480)
    )
    native_audio_key: str | None = None
    if shot.source_audio_path:
        raise HTTPException(
            400,
            "The official H3 Ref2AV workflow cannot preserve locked source audio "
            "exactly; remove the source track or submit it as reference audio.",
        )
    job = create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name=f"h3:{shot.title}",
        notes=shot.script_beat,
        params={
            "h3_provider": h3_provider,
            "prompt": prompt_text,
            "dialogue": list(shot.dialogue),
            "frames": frames,
            "duration_s": shot.duration_s,
            "image_keys": image_keys,
            "audio_keys": audio_keys,
            "native_audio_key": native_audio_key,
            "width": width,
            "height": height,
            "shot_id": shot.id,
            "project_id": shot.project_id,
            "layout_asset_id": shot.layout_asset_id,
            "layout_asset_ids": [
                str(item["asset_id"]) for item in selected_layouts
            ],
            "layout_picture_indices": required_layout_indices,
            "ref_roles": [
                r.role.value
                for r in sorted(shot.refs or [], key=lambda x: x.picture_index)
            ][: len(image_keys)],
            "output_prefix": f"director-studio/{shot.project_id}/{shot.id}/h3",
        },
        project_id=shot.project_id,
    )

    try:
        await start_pipeline_job(job, images=images or None)
    except Exception as e:
        logger.exception("start_pipeline_job h3_ref2va failed for %s", shot_id)
        raise HTTPException(503, f"Failed to start H3 job: {e}") from e

    try:
        shot = apply_transition(shot, "submit_h3")
    except ValueError as e:
        raise _http_value_error(e) from e

    shot = shot.model_copy(update={"h3_job_id": job.id})
    save_shot(shot)
    return shot
