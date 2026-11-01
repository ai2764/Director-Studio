"""Shot Workspace endpoints. They never dispatch a general Director Agent turn."""
import uuid
import asyncio
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..core.projects.store import load_project, load_shot, list_projects, list_shots, save_project, save_shot, new_shot_id
from ..core.projects.models import Shot, ShotRef, ShotVoiceRef, ShotStatus
from ..core.projects.dialogue import apply_dialogue_update
from ..core.projects.chat_sessions import director_chat_sessions, DirectorChatSessionConflict
from ..core.managed_runs.store import _project_lock, active_run_for_project
from ..core.jobs import load_job
from .references import snapshot, selected_references
from . import tasks
from .timings import timed

router = APIRouter(prefix="/shot-workspace", tags=["shot-workspace"])


def project(project_id):
    value = load_project(project_id)
    if not value:
        raise HTTPException(404, "Project not found")
    if value.authoring_mode != "shot_workspace" or value.mode != "director":
        raise HTTPException(409, "Select Shot Workspace for this film project first.")
    return value


def find_shot(shot_id):
    for item in list_projects():
        if shot_id in item.shot_ids and (shot := load_shot(item.id, shot_id)):
            project(item.id)
            return shot
    raise HTTPException(404, "Shot not found")


async def ensure_idle(project_id):
    if (await director_chat_sessions.snapshot(project_id)).active or active_run_for_project(project_id):
        raise HTTPException(409, "Finish or cancel the current project task before editing.")


def ensure_shot_idle(shot):
    for job_id in (shot.h3_job_id, shot.ref_frame_job_id, *(layout.job_id for layout in shot.layout_refs)):
        job = load_job(job_id) if job_id else None
        if job and job.status in {"queued", "uploading", "running"}:
            raise HTTPException(409, "Finish or cancel generation before editing this shot.")


class CreateShot(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)


@router.post("/projects/{project_id}/shots", response_model=Shot)
async def add_shot(project_id: str, body: CreateShot):
    project(project_id)
    await ensure_idle(project_id)
    with _project_lock(project_id):
        current = project(project_id)
        shots = list_shots(project_id)
        if existing := next((shot for shot in shots if shot.meta.get("workspace_create_request") == body.request_id), None):
            return existing
        title = f"Shot {len(shots) + 1}"
        shot = Shot(id=new_shot_id(), project_id=project_id, scene_id="Scene 1", title=title, script_beat="", duration_s=5,
            meta={"workspace_create_request": body.request_id, "manual_default_title": title, "asset_binding_policy": "explicit"})
        previous = load_shot(project_id, current.shot_ids[-1]) if current.shot_ids else None
        if previous:
            # Copy bindings, never the prior shot's prompt, jobs or motion context.
            shot.refs = [ref.model_copy(deep=True) for ref in previous.refs]
            from ..core.projects.layouts import LayoutReference
            selected_layouts = {ref.asset_id for ref in shot.refs if ref.role.value == "layout_ref_frame"}
            shot.layout_refs = [LayoutReference(id=f"lref_{uuid.uuid4().hex[:12]}", asset_id=layout.asset_id,
                purpose=layout.purpose, state_description=layout.state_description,
                review_status=layout.review_status, selected_for_h3=True, activation_mode="append")
                for layout in previous.layout_refs if layout.asset_id in selected_layouts]
        save_shot(shot)
        save_project(current.model_copy(update={"shot_ids": [*current.shot_ids, shot.id]}))
        return shot


class Brief(BaseModel):
    description: str = Field(default="", max_length=8000)
    title: str = Field(default="", max_length=200)
    expected_version: str
    duration_s: float = Field(gt=0, le=120, allow_inf_nan=False)
    dialogue: list[str] = Field(default_factory=list, max_length=30)
    shot_type: str = ""
    camera_angle: str = ""
    camera_motion: str = ""
    composition: str = ""
    original_description: str = ""


@router.get("/shots/{shot_id}/description")
def get_brief(shot_id: str):
    try:
        return snapshot(find_shot(shot_id))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/shots/{shot_id}/description", response_model=Shot)
async def save_brief(shot_id: str, body: Brief):
    shot = find_shot(shot_id)
    await ensure_idle(shot.project_id)
    ensure_shot_idle(shot)
    from .dialogue import parse_cues
    try:
        lines = parse_cues(body.dialogue, shot.scene_id, shot=shot)
        with _project_lock(shot.project_id):
            shot = find_shot(shot_id)
            if snapshot(shot)["version"] != body.expected_version:
                raise ValueError("The shot or references changed. Refresh before saving.")
            updates = {key: getattr(body, key) for key in ("duration_s", "shot_type", "camera_angle", "camera_motion", "composition")}
            updates.update(title=body.title.strip() or shot.title, script_beat=body.description,
                           dialogue_lines=lines, dialogue=[line.text for line in lines])
            revised = apply_dialogue_update(shot, updates)
            changed = snapshot(revised)["version"] != snapshot(shot)["version"]
            if changed:
                revised.meta = {**shot.meta, "prompt_brief_stale": bool(any(shot.prompt_sections.model_dump().values()) or shot.meta.get("prompt_brief_stale"))}
                save_shot(revised)
            from .conversations import append_brief, history
            if not history(shot):
                append_brief(shot, shot.script_beat, "Previous saved brief")
            append_brief(revised, body.description, "Saved brief")
            return revised if changed else shot
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class Refine(BaseModel):
    amendment: str = Field(default="", max_length=4000)
    description: str = Field(min_length=1, max_length=8000)
    title: str = Field(default="", max_length=200)
    suggest_title: bool = False
    expected_version: str
    duration_s: float | None = Field(default=None, gt=0, le=120, allow_inf_nan=False)


@router.post("/shots/{shot_id}/description/refine")
@timed("refinement")
async def refine_brief(shot_id: str, body: Refine):
    from ..config import settings
    from ..core.vram import get_orchestrator
    from .conversations import append_brief
    from .refinement import refine
    from .model import WorkspaceModel
    shot = find_shot(shot_id)
    await ensure_idle(shot.project_id)
    try:
        orch = get_orchestrator()
        async with orch.llm_session(release_on_exit=not settings.llm_keep_loaded):
            await orch.ensure_llm_ready()
            append_brief(shot, body.description, "Before refinement")
            result = await refine(shot, body.description, body.expected_version, WorkspaceModel(),
                                title=body.title, suggest_title=body.suggest_title, amendment=body.amendment, duration_s=body.duration_s)
            if result.get("description"):
                append_brief(shot, result["description"], "Refined draft")
            return result
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class ConversationTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    text: str = Field(max_length=600)


class DescriptionAssist(Refine):
    recent_turns: list[ConversationTurn] = Field(default_factory=list, max_length=2)
    description: str = Field(default="", max_length=8000)
    amendment: str = Field(min_length=1, max_length=4000)


@router.get("/shots/{shot_id}/description/conversation")
def description_conversation(shot_id: str):
    from .conversations import history, append_brief
    shot = find_shot(shot_id)
    if not history(shot):
        append_brief(shot, shot.script_beat, "Initial saved brief")
    return {"messages": history(shot)}


@router.post("/shots/{shot_id}/description/assist")
@timed("assistant")
async def assist_description(shot_id: str, body: DescriptionAssist, request: Request = None):
    from ..config import settings
    from ..core.vram import get_orchestrator
    from .coordinator import converse
    from .model import WorkspaceModel
    shot = find_shot(shot_id)
    await ensure_idle(shot.project_id)
    from .conversations import append, append_brief
    append_brief(shot, body.description, "Submitted brief")
    append(shot, "user", body.amendment)
    try:
        orch = get_orchestrator()
        async with orch.llm_session(release_on_exit=not settings.llm_keep_loaded):
            await orch.ensure_llm_ready()
            pending = asyncio.create_task(converse(shot, body.description, body.amendment, body.expected_version, WorkspaceModel(),
                title=body.title, suggest_title=body.suggest_title,
                turns=[turn.model_dump() for turn in body.recent_turns], duration_s=body.duration_s))
            try:
                while not pending.done():
                    await asyncio.wait({pending}, timeout=0.25)
                    if request is not None and await request.is_disconnected():
                        raise asyncio.CancelledError()
                result = await pending
                if request is not None and await request.is_disconnected():
                    raise asyncio.CancelledError()
            finally:
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
        if result.get("kind") == "draft" and result.get("draft"):
            draft = result["draft"]
            if draft.get("questions"):
                raise ValueError("The assistant returned unresolved questions. No description was saved.")
            saved = await save_brief(shot_id, Brief(
                expected_version=body.expected_version,
                **{key: draft[key] for key in ("description", "title", "duration_s", "dialogue",
                    "shot_type", "camera_angle", "camera_motion", "composition")},
            ))
            result = {**result, "kind": "updated", "reply": "Description updated and saved.",
                      "saved_shot": saved.model_dump(mode="json"), "version": snapshot(saved)["version"]}
        reply = result["reply"]
        if result.get("kind") == "updated":
            reply += f"\n\nDuration: {result['draft']['duration_s']:g}s\n\n" + result["draft"]["description"]
        append(shot, "assistant", reply, proposal=result.get("proposed_change"), scope=result.get("context_scope"))
        return result
    except asyncio.CancelledError:
        append(shot, "error", "Request cancelled. Any changes already saved are kept.")
        raise
    except TimeoutError as exc:
        append(shot, "error", "Description assistant timed out. Please retry.")
        raise HTTPException(504, "Description assistant timed out. Your description was kept; retry when ready.") from exc
    except ValueError as exc:
        append(shot, "error", str(exc))
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        append(shot, "error", "The assistant could not complete this request. Please retry.")
        raise


@router.get("/shots/{shot_id}/timings")
def operation_timings(shot_id: str):
    from .timings import history
    return {"records": history(find_shot(shot_id))}


class Material(BaseModel):
    role: str
    asset_id: str
    file_key: str | None = None
    notes: str = ""


class Materials(BaseModel):
    materials: list[Material] = Field(max_length=9)
    expected_refs: list[ShotRef]
    revision_request: str = Field(default="", max_length=4000)


@router.put("/shots/{shot_id}/materials", response_model=Shot)
async def replace_materials(shot_id: str, body: Materials):
    shot = find_shot(shot_id)
    await ensure_idle(shot.project_id)
    ensure_shot_idle(shot)
    try:
        refs = [ShotRef(**item.model_dump(), picture_index=index + 1) for index, item in enumerate(body.materials)]
        if len({(ref.role, ref.asset_id, ref.file_key) for ref in refs}) != len(refs):
            raise ValueError("A reference image was selected more than once.")
        with _project_lock(shot.project_id):
            shot = find_shot(shot_id)
            if shot.refs != body.expected_refs:
                raise ValueError("References changed. Refresh before replacing them.")
            saved = shot.model_copy(update={"refs": refs, "meta": {**shot.meta, "asset_binding_policy": "explicit"}})
            selected_references(saved)
            from ..core.library.store import load_asset
            from ..core.projects.layouts import LayoutReference, LayoutReviewStatus, sync_selected_layout_refs
            layout_ids = {ref.asset_id for ref in refs if ref.role.value == "layout_ref_frame"}
            layouts = [item.model_copy(update={"selected_for_h3": item.asset_id in layout_ids}) for item in shot.layout_refs]
            known = {item.asset_id for item in layouts}
            for asset_id in layout_ids - known:
                asset = load_asset("layouts", asset_id)
                layouts.append(LayoutReference(id=f"lref_{uuid.uuid4().hex[:12]}", asset_id=asset_id,
                    purpose=asset.name or "Library layout", state_description=asset.notes or "",
                    review_status=LayoutReviewStatus.usable if (asset.meta or {}).get("review_status") in {"approved", "usable"} else LayoutReviewStatus.pending_review,
                    selected_for_h3=True, activation_mode="append"))
            saved.layout_refs = layouts
            if not layout_ids:
                saved.layout_asset_id = None
                saved.layout_review_status = None
                saved.ref_frame_job_id = None
            saved = sync_selected_layout_refs(saved)
            # Preserve the user's selected variant and binding notes after native layout synchronization.
            selected = {(ref.role, ref.asset_id): ref for ref in refs}
            saved.refs = [ref.model_copy(update={"file_key": selected[(ref.role, ref.asset_id)].file_key,
                "notes": selected[(ref.role, ref.asset_id)].notes}) if (ref.role, ref.asset_id) in selected else ref for ref in saved.refs]
            refs = saved.refs
            note = body.revision_request.strip() or shot.meta.get("workspace_reference_note", "")
            saved.meta["workspace_reference_note"] = note
            if refs != shot.refs or note != shot.meta.get("workspace_reference_note", ""):
                saved.meta["prompt_brief_stale"] = bool(any(shot.prompt_sections.model_dump().values()) or shot.meta.get("prompt_brief_stale"))
                save_shot(saved)
            return saved
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/shots/{shot_id}/prompt-check")
def get_prompt_check(shot_id: str):
    from .writer import prompt_check
    return prompt_check(find_shot(shot_id))


class VoiceReferences(BaseModel):
    voice_refs: list[ShotVoiceRef] = Field(max_length=3)
    expected_refs: list[ShotVoiceRef] = Field(max_length=3)


@router.put("/shots/{shot_id}/voice-references", response_model=Shot)
async def replace_voice_references(shot_id: str, body: VoiceReferences):
    shot = find_shot(shot_id)
    await ensure_idle(shot.project_id)
    ensure_shot_idle(shot)
    from ..api.projects import _validate_voice_refs
    try:
        with _project_lock(shot.project_id):
            shot = find_shot(shot_id)
            if shot.voice_refs != body.expected_refs:
                raise ValueError("Voice references changed. Refresh before saving.")
            if shot.source_audio_path and body.voice_refs:
                raise ValueError("This shot uses native source audio. Remove it before adding Voice references.")
            saved = Shot.model_validate({**shot.model_dump(), "voice_refs": body.voice_refs})
            _validate_voice_refs(saved)
            if saved.voice_refs != shot.voice_refs:
                saved.meta = {**saved.meta, "prompt_voice_signature": "", "prompt_brief_stale": bool(any(shot.prompt_sections.model_dump().values()))}
                save_shot(saved)
            return saved
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class PromptRequest(BaseModel):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    revision_request: str = Field(default="", max_length=4000)


@router.post("/shots/{shot_id}/prompt", status_code=202)
async def write_prompt(shot_id: str, body: PromptRequest):
    shot = find_shot(shot_id)
    ensure_shot_idle(shot)
    if active_run_for_project(shot.project_id):
        raise HTTPException(409, "Finish or cancel the current project task first.")
    try:
        return await tasks.start(shot.project_id, shot.id, body.request_id, revision_request=body.revision_request)
    except (ValueError, DirectorChatSessionConflict) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/projects/{project_id}/task")
def task_status(project_id: str):
    project(project_id)
    return tasks.status(project_id)


@router.post("/projects/{project_id}/task/cancel")
async def cancel_task(project_id: str):
    project(project_id)
    return await tasks.cancel(project_id)


class Remove(BaseModel):
    expected_shot: Shot


@router.delete("/shots/{shot_id}")
async def delete_shot(shot_id: str, body: Remove):
    from ..core.projects.shot_removal import remove_shot
    shot = find_shot(shot_id)
    await ensure_idle(shot.project_id)
    try:
        return remove_shot(shot.project_id, shot_id, body.expected_shot)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/projects/{project_id}/removed-shots/{archive_id}/restore")
async def undo_delete(project_id: str, archive_id: str):
    from ..core.projects.shot_removal import restore_shot
    project(project_id)
    await ensure_idle(project_id)
    try:
        return restore_shot(project_id, archive_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class ProjectResolution(BaseModel):
    preset: str


@router.get("/projects/{project_id}/resolution")
def get_project_resolution(project_id: str):
    from .resolution import default_resolution
    return {"preset": default_resolution(project(project_id))}


@router.put("/projects/{project_id}/resolution")
def set_project_resolution(project_id: str, body: ProjectResolution):
    from ..pipelines.h3_ref2va.resolutions import LOCAL_H3_PRESETS
    if body.preset not in LOCAL_H3_PRESETS:
        raise HTTPException(400, "Unknown video size")
    with _project_lock(project_id):
        current = project(project_id)
        current.workspace_default_resolution = body.preset
        save_project(current)
    return {"preset": body.preset}
