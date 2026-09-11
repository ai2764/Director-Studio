"""DirectorService: plan → context → reference-frame queue → prompt write."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...config import settings
from ...core.jobs import create_job, load_job, start_pipeline_job
from ...core.library.store import load_asset
from ...core.h3.prompt import (
    validate_required_picture_bindings,
    validate_tail_frame_transition_prompt,
)
from ...core.projects.models import (
    AgentContext,
    Project,
    PromptSections,
    RefRole,
    Shot,
    ShotRef,
    ShotStatus,
    picture_ref_signature,
    voice_ref_signature,
)
from ...core.projects.layouts import (
    GptLayoutBrief,
    LayoutBrief,
    LayoutProvider,
    LayoutReference,
    LayoutSourceRef,
    layout_prompt_signature,
    mirror_legacy_layout_fields,
    selected_layout_prompt_context,
    sync_selected_layout_refs,
)
from ...core.projects.store import (
    list_projects,
    list_shots,
    load_project,
    load_shot,
    replace_project_shots,
    save_project,
    save_shot,
)
from ...core.schemas import JobStatus, LibraryAsset
from ...core.vram import get_director_model, get_orchestrator
from .context_io import load_agent_context, save_agent_context
from .visual_direction import analyze_ref_frame
from .planner import (
    AssetMatchDraft,
    PlanProvider,
    ShotSceneRefSelection,
    ShotRefsPatch,
    ShotRevisionSubmission,
    ShotDraft,
    parse_prompt_sections_json,
    parse_shot_drafts,
    parse_storyboard_validation,
    role_to_library_kind,
    role_to_ref_role,
)
from . import prompts as prompt_text
from .asset_catalog import (
    LIBRARY_KINDS,
    _asset_index,
    _default_file_key,
    _inventory,
    _read_asset_image_bytes,
    _repair_unique_file_key_typo,
    _script_hash,
)
from .casting_service import (
    _append_ref,
    _claim_storyboard_assets,
    _complete_refs_from_inventory,
    _default_asset_id,
    _find_by_name_or_tag,
    _heuristic_match,
    _kind_candidates,
    _resolve_voice_matches,
    _shot_from_draft,
    _validate_materialized_storyboard_bindings,
    _validate_storyboard_bindings,
    recast_shot_assets as _recast_shot_assets,
)
from .reference_service import (
    _actor_image_for_ref_frame,
    _crop_sheet_front_panel,
    _match_library_actor,
    _match_library_scene,
    _scene_image_for_ref_frame,
    build_ref_frame_brief,
    build_tail_frame_revision_brief,
)

logger = logging.getLogger("director_studio.director")


def _record_layout_generation_issue(shot: Shot, reasons: list[str]) -> Shot:
    """Keep optional Layout failures out of the shot's H3 blocking state."""
    cleaned = [str(reason).strip()[:500] for reason in reasons if str(reason).strip()]
    meta = dict(shot.meta or {})
    meta["layout_generation_issues"] = list(dict.fromkeys(cleaned))
    updates: dict[str, Any] = {"meta": meta}
    if shot.refs:
        updates["blocked_reasons"] = []
        if shot.status == ShotStatus.blocked:
            updates["status"] = ShotStatus.ref_frame_pending
    return shot.model_copy(update=updates)


class StoryboardValidationError(ValueError):
    """A candidate failed deterministic or semantic acceptance before persistence."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = list(issues)
        super().__init__("storyboard validation failed: " + "; ".join(self.issues))


def _same_storyboard_definition(left: Shot, right: Shot) -> bool:
    """Whether re-saving ``right`` should retain ``left``'s generated work."""
    authored_fields = (
        "scene_id",
        "title",
        "script_beat",
        "shot_type",
        "camera_angle",
        "camera_motion",
        "composition",
        "duration_s",
        "dialogue",
    )
    return (
        all(getattr(left, field) == getattr(right, field) for field in authored_fields)
        and picture_ref_signature(left.refs) == picture_ref_signature(right.refs)
        and voice_ref_signature(left.voice_refs) == voice_ref_signature(right.voice_refs)
    )

_KIND_TO_MATCH_ROLE = {
    "actors": "actor",
    "scenes": "scene",
    "props": "prop",
    "costumes": "costume",
}


def _fallback_plan_drafts(
    script_text: str,
    inventory: list[dict[str, Any]],
) -> list[ShotDraft]:
    """Deterministic one-shot plan when the LLM output cannot be parsed."""
    beat = (script_text or "").strip() or "Action beat from project script."
    if len(beat) > 280:
        beat = beat[:277] + "..."
    title = beat.split("。")[0].split(".")[0].strip() or "Main shot"
    if len(title) > 48:
        title = title[:45] + "..."
    matches: list[AssetMatchDraft] = []
    used: set[str] = set()
    for item in inventory:
        kind = str(item.get("kind") or "")
        role = _KIND_TO_MATCH_ROLE.get(kind)
        aid = str(item.get("id") or "").strip()
        if not role or not aid or aid in used:
            continue
        # Prefer project-owned first; inventory is already roughly ordered by helper
        matches.append(AssetMatchDraft(role=role, asset_id=aid))
        used.add(aid)
    # One role per kind (first owned / first listed)
    by_role: dict[str, AssetMatchDraft] = {}
    for m in matches:
        by_role.setdefault(m.role, m)
    matches = list(by_role.values())
    return [
        ShotDraft(
            scene_id="sc01",
            title=title,
            script_beat=beat,
            shot_type="medium wide",
            camera_angle="eye level on the primary action axis",
            camera_motion="locked-off",
            composition="primary subjects and action readable in one coherent frame",
            duration_s=5.0,
            dialogue=[],
            asset_matches=matches,
        )
    ]


def _reference_frame_aspect_ratio(project: Project) -> str:
    text = f"{project.name}\n{project.script_text}".lower()
    if "9:16" in text or "9／16" in text or "竖屏" in text or "vertical" in text:
        return "9:16"
    return "16:9"


def _apply_source_audio_contract(
    sections: PromptSections,
    shot: Shot,
) -> PromptSections:
    """Keep Agent-authored visuals while making exact source audio immutable."""
    if not shot.source_audio_path:
        return sections
    audio_name = Path(shot.source_audio_path).name
    return sections.model_copy(
        update={
            "overall_soundscape": (
                f"The exact source recording {audio_name} is externally locked and immutable. "
                "Preserve its complete waveform, original lead vocal, instruments, mix, timing, "
                "and dynamics without regeneration or replacement. Use the supplied audio only "
                "as the synchronization guide for mouth, breath, guitar, body, wind, and camera motion."
            ),
            "non_diegetic_music": (
                f"Use the externally locked original song segment {audio_name} exactly as supplied. "
                "Do not generate, remove, remix, mute, fade, replace, or add any voice, music, ambience, "
                "sound effect, or silence."
            ),
        }
    )


def recast_shot_assets(
    project_id: str,
    shot: Shot,
    *,
    inventory: list[dict[str, Any]] | None = None,
    index: dict[str, LibraryAsset] | None = None,
    script_text: str = "",
    force: bool = False,
) -> Shot:
    """Compatibility wrapper preserving service-level catalog monkeypatches."""
    return _recast_shot_assets(
        project_id,
        shot,
        inventory=inventory,
        index=index,
        script_text=script_text,
        force=force,
        inventory_loader=_inventory,
        index_loader=_asset_index,
    )


def _shot_context_summary(shot: Shot) -> dict[str, Any]:
    return {
        "id": shot.id,
        "scene_id": shot.scene_id,
        "title": shot.title,
        "status": shot.status.value,
        "script_beat": shot.script_beat,
        "shot_type": shot.shot_type,
        "camera_angle": shot.camera_angle,
        "camera_motion": shot.camera_motion,
        "composition": shot.composition,
        "duration_s": shot.duration_s,
        "asset_ids": [ref.asset_id for ref in shot.refs],
        "blocked_reasons": list(shot.blocked_reasons),
        "dialogue": list(shot.dialogue),
    }


def _build_context(project: Project, shots: list[Shot], *, phase: str) -> AgentContext:
    return AgentContext(
        project_id=project.id,
        script_hash=_script_hash(project.script_text),
        last_phase=phase,
        models_used=[get_director_model()],
        shot_summaries=[_shot_context_summary(shot) for shot in shots],
    )


def _find_shot(shot_id: str) -> Shot | None:
    for project in list_projects():
        shot = load_shot(project.id, shot_id)
        if shot is not None:
            return shot
    return None


class DirectorService:
    """Orchestrates plan → context save → reference-frame jobs → prompt rewrite."""

    def __init__(
        self,
        *,
        plan_provider: PlanProvider,
        orchestrator: Any | None = None,
    ) -> None:
        self.plan_provider = plan_provider
        self.orchestrator = orchestrator if orchestrator is not None else get_orchestrator()

    def revise_shot(
        self,
        project_id: str,
        revision: ShotRevisionSubmission,
    ) -> list[Shot]:
        """Revise authored fields on one Shot without rebuilding the storyboard."""
        project = load_project(project_id)
        if project is None:
            raise ValueError(f"project not found: {project_id}")
        try:
            validated = (
                revision
                if isinstance(revision, ShotRevisionSubmission)
                else ShotRevisionSubmission.model_validate(revision)
            )
        except Exception as exc:
            raise ValueError(f"invalid shot revision: {exc}") from exc

        shot = load_shot(project_id, validated.shot_id)
        if shot is None or validated.shot_id not in project.shot_ids:
            raise ValueError(f"shot not found in project: {validated.shot_id}")

        authored_updates = validated.model_dump(
            exclude={"shot_id"},
            exclude_unset=True,
        )
        meta = dict(shot.meta or {})
        if shot.h3_job_id:
            superseded = list(meta.get("superseded_h3_job_ids") or [])
            if shot.h3_job_id not in superseded:
                superseded.append(shot.h3_job_id)
            meta["superseded_h3_job_ids"] = superseded
        meta["prompt_layout_signature"] = ""
        meta["prompt_picture_signature"] = ""
        meta["prompt_voice_signature"] = ""

        revised = shot.model_copy(
            update={
                **authored_updates,
                "prompt_sections": PromptSections(),
                "h3_job_id": None,
                "status": ShotStatus.needs_review,
                "meta": meta,
            }
        )
        save_shot(revised)

        persisted = list_shots(project_id)
        save_agent_context(
            project_id,
            _build_context(project, persisted, phase="planned"),
        )
        return persisted

    def patch_shot_refs(
        self,
        project_id: str,
        updates: list[ShotRefsPatch],
    ) -> list[Shot]:
        """Replace only Picture bindings on named shots, preserving every story field."""
        project = load_project(project_id)
        if project is None:
            raise ValueError(f"project not found: {project_id}")
        try:
            validated = [
                update
                if isinstance(update, ShotRefsPatch)
                else ShotRefsPatch.model_validate(update)
                for update in updates
            ]
        except Exception as exc:
            raise ValueError(f"invalid shot reference patch: {exc}") from exc
        if not validated:
            raise ValueError("reference patch must contain at least one shot")
        if len({update.shot_id for update in validated}) != len(validated):
            raise ValueError("each shot may appear only once in a reference patch")

        current = list_shots(project_id)
        by_id = {shot.id: shot for shot in current}
        missing = [update.shot_id for update in validated if update.shot_id not in by_id]
        if missing:
            raise ValueError(
                "reference patch names unknown project shot(s): " + ", ".join(missing)
            )

        inventory = _inventory(project_id)
        index = _asset_index(project_id)
        replacements: dict[str, Shot] = {}
        for update in validated:
            refs: list[ShotRef] = []
            for match in update.refs:
                role = role_to_ref_role(match.role)
                if role is None:
                    raise ValueError(
                        f"shot {update.shot_id} has invalid asset role {match.role!r}"
                    )
                asset = index.get(match.asset_id)
                if asset is None:
                    raise ValueError(
                        f"shot {update.shot_id} references unknown library asset "
                        f"{match.asset_id!r}"
                    )
                file_key = match.file_key or _default_file_key(role, asset)
                refs.append(
                    ShotRef(
                        role=role,
                        asset_id=asset.id,
                        picture_index=int(match.picture_index or 0),
                        file_key=file_key,
                        notes="agent-ref-patch",
                    )
                )
            original = by_id[update.shot_id]
            previous_refs = sorted(original.refs, key=lambda item: item.picture_index)
            current_refs = sorted(refs, key=lambda item: item.picture_index)

            def ref_identity(ref: ShotRef) -> tuple[str, str, str]:
                return (ref.role.value, ref.asset_id, str(ref.file_key or ""))

            def ref_payload(ref: ShotRef) -> dict[str, Any]:
                return {
                    "role": ref.role.value,
                    "asset_id": ref.asset_id,
                    "file_key": ref.file_key or "",
                    "picture_index": ref.picture_index,
                }

            previous_by_identity = {
                ref_identity(ref): ref for ref in previous_refs
            }
            current_by_identity = {ref_identity(ref): ref for ref in current_refs}
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
                    "from_picture_index": previous_by_identity[
                        ref_identity(ref)
                    ].picture_index,
                    "to_picture_index": ref.picture_index,
                }
                for ref in current_refs
                if ref_identity(ref) in previous_by_identity
                and previous_by_identity[ref_identity(ref)].picture_index
                != ref.picture_index
            ]
            meta = dict(original.meta or {})
            if added or removed or reordered:
                meta["prompt_picture_signature"] = ""
                meta["prompt_layout_signature"] = ""
                meta["material_review_pending"] = True
                meta["material_changes"] = {
                    "added": added,
                    "removed": removed,
                    "reordered": reordered,
                }
            replacements[update.shot_id] = original.model_copy(
                update={"refs": refs, "meta": meta}
            )

        candidate = [replacements.get(shot.id, shot) for shot in current]
        _validate_materialized_storyboard_bindings(
            candidate,
            inventory=inventory,
            index=index,
        )

        for shot in candidate:
            if shot.id in replacements:
                save_shot(shot)
        _claim_storyboard_assets(project_id, list(replacements.values()), index=index)
        persisted = list_shots(project_id)
        save_agent_context(
            project_id,
            _build_context(project, persisted, phase="planned"),
        )
        return persisted

    def set_shot_scene_ref(
        self,
        project_id: str,
        *,
        shot_id: str,
        scene_asset_id: str,
        file_key: str,
    ) -> list[Shot]:
        """Apply one exact scene binding without rebuilding any other shot data."""
        project = load_project(project_id)
        if project is None:
            raise ValueError(f"project not found: {project_id}")

        try:
            selection = ShotSceneRefSelection.model_validate(
                {
                    "shot_id": shot_id,
                    "scene_asset_id": scene_asset_id,
                    "file_key": file_key,
                }
            )
        except Exception as exc:
            raise ValueError(f"invalid exact scene selection: {exc}") from exc

        shot = load_shot(project_id, selection.shot_id)
        if shot is None or selection.shot_id not in project.shot_ids:
            raise ValueError(f"shot not found in project: {selection.shot_id}")

        index = _asset_index(project_id)
        asset = index.get(selection.scene_asset_id)
        if asset is None:
            raise ValueError(f"scene asset not found: {selection.scene_asset_id}")
        if asset.kind != "scenes":
            raise ValueError(
                f"scene_asset_id must be a scenes asset, got {asset.kind!r}"
            )
        if asset.project_id not in {None, project_id}:
            raise ValueError(
                f"scene asset belongs to another project: {selection.scene_asset_id}"
            )
        if not (asset.files or {}).get(selection.file_key):
            raise ValueError(
                f"invalid scene file_key {selection.file_key!r} for asset "
                f"{selection.scene_asset_id}"
            )

        scene_positions = [
            position
            for position, ref in enumerate(shot.refs)
            if ref.role == RefRole.scene
        ]
        if len(scene_positions) != 1:
            raise ValueError(
                f"shot {selection.shot_id} must contain exactly one scene ref; "
                f"found {len(scene_positions)}"
            )

        scene_position = scene_positions[0]
        refs = list(shot.refs)
        refs[scene_position] = refs[scene_position].model_copy(
            update={
                "asset_id": selection.scene_asset_id,
                "file_key": selection.file_key,
            }
        )
        save_shot(shot.model_copy(update={"refs": refs}))

        if not asset.project_id:
            from ...core.library.store import assign_asset_project

            try:
                assign_asset_project("scenes", asset.id, project_id)
            except Exception:
                logger.exception("assign_asset_project failed for %s", asset.id)

        persisted = list_shots(project_id)
        save_agent_context(
            project_id,
            _build_context(project, persisted, phase="planned"),
        )
        return persisted

    async def save_storyboard(
        self,
        project_id: str,
        drafts: list[ShotDraft],
        expected_script_hash: str,
        *,
        user_feedback: str = "",
        requested_minimum_duration_s: float = 0.0,
    ) -> list[Shot]:
        """Validate and persist an ordered Agent-authored storyboard transactionally."""
        project = load_project(project_id)
        if project is None:
            raise ValueError(f"project not found: {project_id}")
        current_hash = _script_hash(project.script_text)
        if (expected_script_hash or "").strip() != current_hash:
            raise ValueError(
                "script changed; storyboard candidate is stale "
                f"(expected {expected_script_hash!r}, current {current_hash})"
            )

        try:
            validated = [
                draft
                if isinstance(draft, ShotDraft)
                else ShotDraft.model_validate(draft)
                for draft in drafts
            ]
        except Exception as exc:
            raise ValueError(f"invalid storyboard draft: {exc}") from exc
        if not validated:
            raise ValueError("storyboard must contain at least one shot")

        existing_shots = list_shots(project_id)
        existing_by_id = {shot.id: shot for shot in existing_shots}
        requested_existing_ids = [
            draft.shot_id for draft in validated if draft.shot_id
        ]
        reserved_existing_ids = set(requested_existing_ids)
        if len(requested_existing_ids) != len(set(requested_existing_ids)):
            raise ValueError("storyboard contains duplicate existing shot_id values")
        unknown_ids = [
            shot_id for shot_id in requested_existing_ids if shot_id not in existing_by_id
        ]
        if unknown_ids:
            raise ValueError(
                "storyboard references shot_id values outside this project: "
                + ", ".join(unknown_ids)
            )

        minimum_duration = max(float(requested_minimum_duration_s or 0.0), 0.0)
        total_duration = sum(draft.duration_s for draft in validated)
        if total_duration + 1e-9 < minimum_duration:
            raise StoryboardValidationError(
                [
                    f"candidate total duration {total_duration:g}s is below the "
                    f"requested minimum {minimum_duration:g}s"
                ]
            )

        inventory = _inventory(project_id)
        index = _asset_index(project_id)
        _validate_storyboard_bindings(
            validated,
            inventory=inventory,
            index=index,
        )
        shots: list[Shot] = []
        retained_existing_ids: set[str] = set()
        for draft in validated:
            candidate = _shot_from_draft(
                project_id,
                draft,
                inventory=inventory,
                index=index,
                script_text=project.script_text or "",
            )
            existing = existing_by_id.get(draft.shot_id or "")
            if existing is None:
                # Compatibility for an older Agent/client that omitted shot_id:
                # an exact, unused storyboard definition is still the same Shot.
                matches = [
                    shot
                    for shot in existing_shots
                    if shot.id not in retained_existing_ids
                    and shot.id not in reserved_existing_ids
                    and _same_storyboard_definition(shot, candidate)
                ]
                if len(matches) == 1:
                    existing = matches[0]
            if existing is not None:
                retained_existing_ids.add(existing.id)
                if _same_storyboard_definition(existing, candidate):
                    candidate = existing
                else:
                    candidate = candidate.model_copy(update={"id": existing.id})
            shots.append(candidate)
        _validate_materialized_storyboard_bindings(
            shots,
            inventory=inventory,
            index=index,
        )
        candidate_json = json.dumps(
            [draft.model_dump(mode="json") for draft in validated],
            ensure_ascii=False,
            indent=2,
        )
        validation_user = prompt_text.STORYBOARD_VALIDATION_USER_TEMPLATE.format(
            script_text=project.script_text,
            user_feedback=user_feedback,
            requested_minimum_duration_s=minimum_duration,
            candidate_json=candidate_json,
        )
        keep = bool(getattr(settings, "llm_keep_loaded", True))
        async with self.orchestrator.llm_session(release_on_exit=not keep):
            await self.orchestrator.ensure_llm_ready()
            raw_validation = await self.plan_provider.complete(
                prompt_text.STORYBOARD_VALIDATION_SYSTEM,
                validation_user,
                guides=("storyboard-validation",),
            )
        try:
            validation = parse_storyboard_validation(raw_validation)
        except Exception as exc:
            raise StoryboardValidationError(
                [f"semantic validator returned an invalid structured verdict: {exc}"]
            ) from exc
        if not validation.valid:
            raise StoryboardValidationError(validation.issues)

        replace_project_shots(project_id, shots)
        _claim_storyboard_assets(project_id, shots, index=index)
        persisted = list_shots(project_id)
        refreshed = load_project(project_id) or project
        save_agent_context(
            project_id,
            _build_context(refreshed, persisted, phase="planned"),
        )
        return persisted

    async def plan_project(self, project_id: str) -> Project:
        project = load_project(project_id)
        if project is None:
            raise ValueError(f"project not found: {project_id}")

        inventory = _inventory(project_id)
        index = _asset_index(project_id)
        library_json = json.dumps(inventory, indent=2)

        # Plan path: queue for GPU, free Comfy image/video weights, then run Ollama.
        # release_comfy_models is also invoked inside llm_session; call once more
        # up-front for clear logging when users click Plan after a gen job.
        logger.info("plan_project %s: acquiring LLM GPU (will unload Comfy models)", project_id)
        keep = bool(getattr(settings, "llm_keep_loaded", True))
        async with self.orchestrator.llm_session(release_on_exit=not keep):
            free_stats = getattr(self.orchestrator, "last_comfy_free", None)
            logger.info("plan_project %s: comfy free stats=%s", project_id, free_stats)
            await self.orchestrator.ensure_llm_ready()
            system = prompt_text.PLAN_SYSTEM
            user = prompt_text.PLAN_USER_TEMPLATE.format(
                script_text=project.script_text,
                library_json=library_json,
                feedback_block="",
            )
            raw = await self.plan_provider.complete(
                system,
                user,
                guides=("script-planning",),
            )
            drafts: list[ShotDraft] | None = None
            try:
                drafts = parse_shot_drafts(raw)
            except Exception as first_err:
                logger.warning(
                    "plan parse failed for %s (will repair): %s | raw_len=%s head=%r",
                    project_id,
                    first_err,
                    len(raw or ""),
                    (raw or "")[:240],
                )
                repair_user = prompt_text.PLAN_REPAIR_USER_TEMPLATE.format(
                    error=str(first_err),
                    raw=raw,
                    library_json=library_json,
                )
                raw2 = await self.plan_provider.complete(
                    prompt_text.PLAN_REPAIR_SYSTEM,
                    repair_user,
                    guides=("script-planning",),
                )
                try:
                    drafts = parse_shot_drafts(raw2)
                except Exception as second_err:
                    logger.warning(
                        "plan parse failed after repair for %s: %s | raw2_len=%s head=%r",
                        project_id,
                        second_err,
                        len(raw2 or ""),
                        (raw2 or "")[:240],
                    )
                    raise ValueError(
                        "plan output was unusable after one repair attempt; "
                        "the existing storyboard was left unchanged"
                    ) from second_err

        if not drafts:
            ctx = _build_context(project, [], phase="planned")
            save_agent_context(project_id, ctx)
            raise ValueError(
                "plan produced no shots (model output unusable and inventory empty)"
            )

        shots: list[Shot] = []
        for draft in drafts:
            shot = _shot_from_draft(
                project_id,
                draft,
                inventory=inventory,
                index=index,
                script_text=project.script_text or "",
            )
            # Claim unassigned assets the agent cast onto this project
            for ref in shot.refs:
                kind = role_to_library_kind(ref.role.value)
                if not kind:
                    continue
                asset = index.get(ref.asset_id) or load_asset(kind, ref.asset_id)
                if asset and not asset.project_id:
                    try:
                        from ...core.library.store import assign_asset_project

                        assign_asset_project(kind, asset.id, project_id)
                    except Exception:
                        logger.exception("assign_asset_project failed for %s", asset.id)
            for voice_ref in shot.voice_refs:
                asset = index.get(voice_ref.asset_id) or load_asset(
                    "voices", voice_ref.asset_id
                )
                if asset and not asset.project_id:
                    try:
                        from ...core.library.store import assign_asset_project

                        assign_asset_project("voices", asset.id, project_id)
                    except Exception:
                        logger.exception("assign voice asset project failed for %s", asset.id)
            shots.append(shot)

        # New plan fully replaces old shots (delete orphan JSON + rewrite shot_ids)
        deleted = replace_project_shots(project_id, shots)
        if deleted:
            logger.info(
                "plan_project %s: removed %d old shot(s): %s",
                project_id,
                len(deleted),
                ", ".join(deleted[:12]) + ("…" if len(deleted) > 12 else ""),
            )

        project = load_project(project_id) or project
        ctx = _build_context(project, shots, phase="planned")
        save_agent_context(project_id, ctx)
        return project

    async def queue_ref_frames(
        self,
        project_id: str,
        shot_ids: list[str] | None = None,
        *,
        force: bool = False,
    ) -> list[Shot]:
        """Compatibility wrapper that queues one default Layout per target shot."""
        project = load_project(project_id)
        if project is None:
            raise ValueError(f"project not found: {project_id}")

        all_shots = list_shots(project_id)
        explicit = shot_ids is not None
        if explicit:
            # User/agent named shots → re-generate even if a layout is already in review.
            force = True
            wanted = set(shot_ids or [])
            targets = [s for s in all_shots if s.id in wanted]
        else:
            # Bulk: only shots that still need a first layout (unless force re-do).
            queueable = {
                ShotStatus.draft,
                ShotStatus.planning,
                ShotStatus.ref_frame_pending,
                ShotStatus.blocked,
                ShotStatus.failed,
            }
            if force:
                queueable |= {
                    ShotStatus.needs_review,
                    ShotStatus.needs_review,
                    ShotStatus.approved,
                    ShotStatus.succeeded,
                }
            targets = [s for s in all_shots if s.status in queueable]

        # Never steal GPU from an in-flight H3 job.
        hard_skip = {
            ShotStatus.queued,
            ShotStatus.running,
        }
        if not force:
            hard_skip |= {
                ShotStatus.needs_review,
                ShotStatus.needs_review,
                ShotStatus.approved,
                ShotStatus.succeeded,
            }
        targets = [s for s in targets if s.status not in hard_skip]

        # The compatibility action remains single-flight. Explicit Layout briefs
        # use queue_reference_frame directly and may intentionally create siblings.
        active_ref_frame_statuses = {
            JobStatus.queued,
            JobStatus.uploading,
            JobStatus.running,
        }
        targets = [
            shot
            for shot in targets
            if not (
                (layout_job_ids := {
                    item.job_id for item in shot.layout_refs if item.job_id
                } | ({shot.ref_frame_job_id} if shot.ref_frame_job_id else set()))
                and any(
                    (existing_job := load_job(job_id)) is not None
                    and existing_job.pipeline_id == "ref_frame"
                    and existing_job.status in active_ref_frame_statuses
                    for job_id in layout_job_ids
                )
            )
        ]
        if not targets:
            return []
        updated: list[Shot] = []
        for shot in targets:
            updated.append(
                await self.queue_reference_frame(shot.id, brief=None, force=force)
            )
        return updated

    async def queue_reference_frame(
        self,
        shot_id: str,
        *,
        brief: LayoutBrief | None = None,
        force: bool = False,
    ) -> Shot:
        """Queue one Layout whose activation mode is applied after success."""
        shot = _find_shot(shot_id)
        if shot is None:
            raise ValueError(f"shot not found: {shot_id}")
        project = load_project(shot.project_id)
        if project is None:
            raise ValueError(f"project not found: {shot.project_id}")
        if shot.status in {ShotStatus.queued, ShotStatus.running}:
            raise ValueError(f"shot {shot.id} has an active H3 job")

        compatibility_request = brief is None
        requested_brief = brief or LayoutBrief()
        uses_default_pack = not requested_brief.source_refs
        if uses_default_pack:
            shot = self._ensure_ref_frame_refs(project, shot)
            missing = self._ref_frame_missing_requirements(shot)
            if missing:
                unavailable = _record_layout_generation_issue(shot, missing)
                save_shot(unavailable)
                logger.warning(
                    "ref_frame unavailable for %s: %s", shot.id, "; ".join(missing)
                )
                return unavailable
            packed = self._collect_ref_frame_refs(shot)
            derived_sources = list(packed.get("source_refs") or [])
            effective_brief = requested_brief.model_copy(
                update={"source_refs": derived_sources}
            )
        else:
            packed = self._collect_layout_brief_refs(requested_brief)
            effective_brief = requested_brief

        images = packed["images"]
        if not 1 <= len(images) <= 3:
            raise ValueError("Qwen Layout generation requires 1 to 3 source images")
        ref_labels = list(packed["labels"])
        vision_captions = list(packed.get("image_labels") or [])
        if not vision_captions:
            vision_captions = [
                str(label)
                for label in ref_labels
                if str(label).lstrip().lower().startswith("image")
            ][: len(images)]

        # Save the current project context before giving the GPU to Comfy.
        all_shots = list_shots(project.id)
        ctx = load_agent_context(project.id) or _build_context(
            project, all_shots, phase="awaiting_ref_frame"
        )
        ctx = ctx.model_copy(
            update={
                "last_phase": "awaiting_ref_frame",
                "shot_summaries": _build_context(
                    project, all_shots, phase="awaiting_ref_frame"
                ).shot_summaries,
            }
        )
        save_agent_context(project.id, ctx)

        review_image: tuple[str, bytes] | None = None
        if uses_default_pack and (shot.feedback or "").strip() and shot.layout_asset_id:
            previous_layout = load_asset("layouts", shot.layout_asset_id)
            if previous_layout is not None:
                review_image = _read_asset_image_bytes(
                    previous_layout,
                    role="layout_ref_frame",
                    file_key="layout",
                )
            if review_image is None:
                logger.warning(
                    "ref_frame review image unavailable for shot %s layout %s",
                    shot.id,
                    shot.layout_asset_id,
                )

        tail_frame_redraw = (
            not uses_default_pack
            and bool(effective_brief.source_refs)
            and effective_brief.source_refs[0].role == RefRole.layout_ref_frame
        )
        direction_feedback = ""
        if review_image is not None or tail_frame_redraw:
            direction_feedback = shot.feedback or ""

        runtime_provider = getattr(self.orchestrator, "provider", None)
        model = (
            get_director_model(runtime_provider.provider_id)
            if runtime_provider is not None
            else get_director_model()
        )
        vision_client = (
            runtime_provider.client
            if runtime_provider is not None
            else getattr(self.plan_provider, "client", None)
        )
        try:
            async with self.orchestrator.llm_session(release_on_exit=False):
                await self.orchestrator.ensure_llm_ready()
                direction = await analyze_ref_frame(
                    shot,
                    images=images,
                    captions=vision_captions,
                    layout_brief=effective_brief,
                    review_image=review_image,
                    feedback=direction_feedback,
                    model=model,
                    ollama=vision_client,
                )
        except Exception as exc:
            await self.orchestrator.release_llm()
            reason = f"visual direction failed: {exc}"
            unavailable = _record_layout_generation_issue(shot, [reason])
            save_shot(unavailable)
            logger.exception("ref_frame visual direction failed for %s", shot.id)
            return unavailable

        await self.orchestrator.release_llm()
        layout_ref_id = f"lref_{uuid.uuid4().hex[:12]}"
        source_payload = [
            source.model_dump(mode="json") for source in effective_brief.source_refs
        ]
        job = create_job(
            pipeline_id="ref_frame",
            asset_kind="layouts",
            name=f"layout:{shot.title}",
            notes=shot.script_beat,
            params={
                "description": direction.compiled_prompt,
                "compiled_prompt": direction.compiled_prompt,
                "visual_director_model": model,
                "visual_brief": direction.brief.model_dump(),
                "selected_refs": direction.selected_refs,
                "vision_input_captions": direction.vision_input_captions,
                "review_image_used": direction.review_image_used,
                "review_feedback": direction.review_feedback,
                "previous_layout_asset_id": (
                    shot.layout_asset_id if direction.review_image_used else ""
                ),
                "shot_id": shot.id,
                "project_id": project.id,
                "layout_ref_id": layout_ref_id,
                **(
                    {"compatibility_primary_layout_id": layout_ref_id}
                    if compatibility_request
                    else {}
                ),
                "layout_brief": effective_brief.model_dump(mode="json"),
                "layout_source_refs": source_payload,
                "source_asset_ids": [
                    source.asset_id for source in effective_brief.source_refs
                ],
                "image_keys": list(images),
                "ref_labels": ref_labels,
                "aspect_ratio": _reference_frame_aspect_ratio(project),
                "output_prefix": (
                    f"director-studio/{project.id}/{shot.id}/ref_frame/{layout_ref_id}"
                ),
            },
            project_id=project.id,
        )
        layout_ref = LayoutReference(
            id=layout_ref_id,
            job_id=job.id,
            job_status=job.status,
            purpose=effective_brief.purpose,
            state_description=effective_brief.state_description,
            time_hint=effective_brief.time_hint,
            source_refs=list(effective_brief.source_refs),
            activation_mode=effective_brief.activation_mode,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        current = load_shot(project.id, shot.id) or shot
        updated = current.model_copy(
            update={
                "layout_refs": [*current.layout_refs, layout_ref],
                "status": ShotStatus.ref_frame_pending,
                "blocked_reasons": [],
            }
        )
        updated = mirror_legacy_layout_fields(
            updated,
            compatibility_primary_layout_id=(
                layout_ref_id if compatibility_request else None
            ),
        )
        save_shot(updated)

        await start_pipeline_job(job, images=images)
        save_agent_context(
            project.id,
            _build_context(project, list_shots(project.id), phase="awaiting_ref_frame"),
        )
        logger.info(
            "queued ref_frame job %s for shot %s Layout %s (force=%s)",
            job.id,
            shot.id,
            layout_ref_id,
            force,
        )
        return updated

    async def queue_gpt_reference_frame(
        self,
        shot_id: str,
        *,
        brief: GptLayoutBrief,
    ) -> Shot:
        """Queue one explicitly requested GPT Layout with ordered source bytes."""
        shot = _find_shot(shot_id)
        if shot is None:
            raise ValueError(f"shot not found: {shot_id}")
        project = load_project(shot.project_id)
        if project is None:
            raise ValueError(f"project not found: {shot.project_id}")
        if shot.status in {ShotStatus.queued, ShotStatus.running}:
            raise ValueError(f"shot {shot.id} has an active H3 job")

        packed = self._collect_gpt_layout_refs(brief)
        images: dict[str, tuple[str, bytes]] = packed["images"]
        resolved_sources: list[dict[str, Any]] = packed["resolved_sources"]
        per_file_limit = settings.gpt_bridge_max_file_mb * 1024 * 1024
        total_limit = settings.gpt_bridge_max_total_mb * 1024 * 1024
        total_bytes = 0
        for index, (_filename, data) in enumerate(images.values(), start=1):
            if len(data) > per_file_limit:
                raise ValueError(
                    f"Image{index} exceeds the configured GPT per-file upload limit"
                )
            total_bytes += len(data)
        if total_bytes > total_limit:
            raise ValueError(
                "The ordered GPT source pack exceeds the configured total upload limit"
            )

        generation_prompt = self._gpt_generation_prompt(brief)
        layout_ref_id = f"lref_{uuid.uuid4().hex[:12]}"
        job = create_job(
            pipeline_id="gpt_ref_frame",
            asset_kind="layouts",
            name=f"layout:{shot.title}",
            notes=shot.script_beat,
            params={
                "provider": LayoutProvider.gpt.value,
                "generation_prompt": generation_prompt,
                "requested_generation_prompt": brief.generation_prompt,
                "shot_id": shot.id,
                "project_id": project.id,
                "layout_ref_id": layout_ref_id,
                "layout_brief": brief.model_dump(mode="json"),
                "layout_source_refs": resolved_sources,
                "source_asset_ids": [
                    source.asset_id for source in brief.source_refs
                ],
                "image_keys": list(images),
                "source_total_bytes": total_bytes,
            },
            project_id=project.id,
        )
        layout_ref = LayoutReference(
            id=layout_ref_id,
            provider=LayoutProvider.gpt,
            job_id=job.id,
            job_status=job.status,
            purpose=brief.purpose,
            state_description=brief.state_description,
            time_hint=brief.time_hint,
            source_refs=list(brief.source_refs),
            activation_mode=brief.activation_mode,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        current = load_shot(project.id, shot.id) or shot
        updated = mirror_legacy_layout_fields(
            current.model_copy(
                update={
                    "layout_refs": [*current.layout_refs, layout_ref],
                    "status": ShotStatus.ref_frame_pending,
                    "blocked_reasons": [],
                }
            )
        )
        save_shot(updated)

        await start_pipeline_job(job, images=images)
        save_agent_context(
            project.id,
            _build_context(
                project,
                list_shots(project.id),
                phase="awaiting_ref_frame",
            ),
        )
        logger.info(
            "queued gpt_ref_frame job %s for shot %s Layout %s with %d sources",
            job.id,
            shot.id,
            layout_ref_id,
            len(images),
        )
        return updated

    def _gpt_generation_prompt(self, brief: GptLayoutBrief) -> str:
        """Make Actor references authoritative before sending the prompt to GPT."""
        actor_sources = [
            (index, source)
            for index, source in enumerate(brief.source_refs, start=1)
            if source.role == RefRole.actor
        ]
        if not actor_sources:
            return brief.generation_prompt

        costume_images = [
            f"Image{index}"
            for index, source in enumerate(brief.source_refs, start=1)
            if source.role == RefRole.costume
        ]
        lines = ["REFERENCE AUTHORITY - follow this before the shot request:"]
        for index, source in actor_sources:
            asset = self._load_layout_source_asset(source)
            image = f"Image{index}"
            name = (asset.name or asset.id).strip()
            lines.append(
                f"- {image} is the authoritative character reference for {name}; "
                "preserve the same exact person, including facial proportions, eye "
                "shape, nose, lips, jawline, skin tone, hairline, hairstyle, and body "
                "proportions. Do not recast, beautify, age-shift, or redesign them."
            )
            if costume_images:
                lines.append(
                    f"- {image} still controls identity when wardrobe is supplied by "
                    f"{', '.join(costume_images)}; change clothing only where the shot "
                    "request explicitly binds that costume reference to this character."
                )
            else:
                lines.append(
                    f"- Preserve the approved wardrobe shown in {image}. Do not replace "
                    "it with a later text-only garment description; a wardrobe change "
                    "requires an attached costume reference or an explicit user-requested "
                    "change recorded in the source note."
                )

        return "\n".join(lines) + "\n\nSHOT REQUEST:\n" + brief.generation_prompt

    def _collect_gpt_layout_refs(self, brief: GptLayoutBrief) -> dict[str, Any]:
        images: dict[str, tuple[str, bytes]] = {}
        resolved_sources: list[dict[str, Any]] = []
        for index, source in enumerate(brief.source_refs, start=1):
            asset = self._load_layout_source_asset(source)
            filename, data, used_key = self._read_layout_source(asset, source)
            images[f"ref_{index - 1}"] = (filename, data)
            resolved_sources.append(
                {
                    **source.model_dump(mode="json"),
                    "resolved_filename": filename,
                    "resolved_file_key": used_key,
                    "image_number": index,
                }
            )
        return {"images": images, "resolved_sources": resolved_sources}

    def _collect_layout_brief_refs(self, brief: LayoutBrief) -> dict[str, Any]:
        if not 1 <= len(brief.source_refs) <= 3:
            raise ValueError("Qwen Layout generation requires 1 to 3 source images")
        images: dict[str, tuple[str, bytes]] = {}
        labels: list[str] = []
        for index, source in enumerate(brief.source_refs, start=1):
            asset = self._load_layout_source_asset(source)
            filename, data, used_key = self._read_layout_source(asset, source)
            images[f"ref_{index - 1}"] = (filename, data)
            labels.append(self._layout_source_label(index, source, asset, used_key))
        return {"images": images, "image_labels": labels, "labels": labels}

    def _load_layout_source_asset(self, source: LayoutSourceRef) -> LibraryAsset:
        asset = _asset_index().get(source.asset_id)
        if asset is None:
            raise ValueError(
                f"Layout source asset not found: {source.role.value}:{source.asset_id}"
            )
        expected_kind = {
            RefRole.actor: "actors",
            RefRole.costume: "costumes",
            RefRole.scene: "scenes",
            RefRole.prop: "props",
            RefRole.layout_ref_frame: "layouts",
        }.get(source.role)
        if expected_kind is not None and asset.kind != expected_kind:
            raise ValueError(
                f"Layout source {source.asset_id} role {source.role.value} requires "
                f"a {expected_kind} asset, got {asset.kind}"
            )
        review_status = str((asset.meta or {}).get("review_status") or "").lower()
        if asset.kind == "layouts" and review_status in {"reject", "rejected"}:
            raise ValueError(
                f"rejected Layout source cannot be used for Qwen: {source.asset_id}"
            )
        return asset

    def _read_layout_source(
        self,
        asset: LibraryAsset,
        source: LayoutSourceRef,
    ) -> tuple[str, bytes, str]:
        from ...core.library.images import resolve_asset_image

        requested_key = source.file_key
        if requested_key and not (asset.files or {}).get(requested_key):
            raise ValueError(
                f"Layout source asset {asset.id} has no file key {requested_key}"
            )
        if source.role == RefRole.actor:
            packed = _actor_image_for_ref_frame(
                asset,
                preferred_key=requested_key,
            )
        elif source.role == RefRole.scene:
            packed = _scene_image_for_ref_frame(
                asset,
                preferred_key=requested_key,
            )
        else:
            packed = resolve_asset_image(
                asset,
                role=source.role.value,
                file_key=requested_key,
            )
        if packed is None:
            key_label = requested_key or "a usable image"
            raise ValueError(
                f"Layout source asset {asset.id} has no readable file for {key_label}"
            )
        filename, data, used_key = packed
        if requested_key and used_key != requested_key and not used_key.startswith(
            f"{requested_key}->"
        ):
            raise ValueError(
                f"Layout source asset {asset.id} has no readable file for key "
                f"{requested_key}"
            )
        return filename, data, used_key

    def _layout_source_label(
        self,
        index: int,
        source: LayoutSourceRef,
        asset: LibraryAsset,
        used_key: str,
    ) -> str:
        notes = (source.notes or "").strip() or "(none)"
        return (
            f"Image{index} {source.role.value} {asset.name or asset.id} "
            f"asset_id={asset.id} file_key={used_key} notes={notes}"
        )

    def _collect_ref_images(self, shot: Shot) -> dict[str, tuple[str, bytes]]:
        """Legacy helper — same bytes as reference-frame pack without labels."""
        return self._collect_ref_frame_refs(shot)["images"]

    def _ensure_ref_frame_refs(self, project: Project, shot: Shot) -> Shot:
        """
        Make sure shot.refs include scene + actors for reference-frame grounding.

        Agent casting: auto-attach scene + actor from library when missing
        (name match or unambiguous project inventory). Pin three-view / plate keys.
        """
        # Full recast when missing critical roles
        has_actor = any(r.role == RefRole.actor for r in shot.refs)
        has_scene = any(r.role == RefRole.scene for r in shot.refs)
        if not has_actor or not has_scene:
            recast = recast_shot_assets(
                project.id,
                shot,
                script_text=project.script_text or "",
                force=False,
            )
            if recast.refs != shot.refs or recast.blocked_reasons != shot.blocked_reasons:
                save_shot(recast)
                shot = recast

        refs = list(shot.refs)
        changed = False
        index = _asset_index(project.id)

        # Pin preferred file keys
        new_refs: list[ShotRef] = []
        for r in refs:
            asset = index.get(r.asset_id)
            preferred = _default_file_key(r.role, asset)
            if preferred and r.file_key != preferred:
                # Keep explicit file_key if it exists on asset
                if r.file_key and asset and asset.files and asset.files.get(r.file_key):
                    new_refs.append(r)
                else:
                    new_refs.append(r.model_copy(update={"file_key": preferred}))
                    changed = True
            else:
                new_refs.append(r)
        refs = new_refs

        has_scene = any(r.role == RefRole.scene for r in refs)
        if not has_scene:
            scene_asset = _match_library_scene(project, shot)
            if scene_asset is not None:
                _append_ref(
                    refs,
                    role=RefRole.scene,
                    asset_id=scene_asset.id,
                    index=index,
                    notes=f"auto scene:{scene_asset.name}",
                )
                changed = True

        has_actor = any(r.role == RefRole.actor for r in refs)
        if not has_actor:
            actor_asset = _match_library_actor(project, shot)
            if actor_asset is not None:
                _append_ref(
                    refs,
                    role=RefRole.actor,
                    asset_id=actor_asset.id,
                    index=index,
                    notes=f"auto actor:{actor_asset.name}",
                )
                changed = True

        if changed:
            shot = shot.model_copy(update={"refs": refs, "blocked_reasons": []})
            # Re-check after fill
            missing = self._ref_frame_missing_requirements(shot)
            if missing:
                shot = shot.model_copy(
                    update={"blocked_reasons": missing, "status": ShotStatus.blocked}
                )
            elif shot.status == ShotStatus.blocked:
                shot = shot.model_copy(update={"status": ShotStatus.ref_frame_pending})
            save_shot(shot)
        return shot

    def _ref_frame_missing_requirements(self, shot: Shot) -> list[str]:
        """Hard requirements: at least one actor three-view; scene strongly preferred."""
        missing: list[str] = []
        actors = [r for r in shot.refs if r.role == RefRole.actor]
        scenes = [r for r in shot.refs if r.role == RefRole.scene]
        if not actors:
            missing.append("ref_frame requires at least one actor ref (three-view)")
        else:
            for r in actors:
                kind = role_to_library_kind(r.role.value) or "actors"
                asset = load_asset(kind, r.asset_id)
                if not asset:
                    missing.append(f"missing actor asset {r.asset_id}")
                    continue
                pair = _read_asset_image_bytes(
                    asset, role="actor", file_key=r.file_key or "fullbody_threeview"
                )
                if not pair:
                    missing.append(
                        f"actor {r.asset_id} has no usable three-view/master image"
                    )
        if not scenes:
            missing.append(
                "ref_frame requires a scene library ref (generate Set Design plate first)"
            )
        else:
            for r in scenes:
                asset = load_asset("scenes", r.asset_id)
                if not asset:
                    missing.append(f"missing scene asset {r.asset_id}")
                    continue
                pair = _read_asset_image_bytes(
                    asset, role="scene", file_key=r.file_key or "angle_00"
                )
                if not pair:
                    missing.append(f"scene {r.asset_id} has no usable plate/angle image")
        return missing

    def _collect_ref_frame_refs(
        self, shot: Shot
    ) -> dict[str, Any]:
        """
        Build Comfy ref pack for layout generation from ``shot.refs``.

        **Fixed socket order for Qwen EditPlus** (do NOT follow agent picture_index):
        1. SCENE plate → Image1 (+ latent init) so the set fills the frame
        2. CHARACTER identity still(s) → Image2 / Image3
        3. A selected handled PROP may use the remaining slot; its catalog backdrop
           is explicitly discarded in the prompt.

        Agent ``picture_index`` still matters for H3 / Gate prompts later; only
        the layout-still packing reorders for generation quality.
        """
        from ...pipelines.ref_frame.workflow import MAX_REF_IMAGES

        # EditPlus graph only wires 3 image inputs.
        max_images = min(3, int(MAX_REF_IMAGES) if MAX_REF_IMAGES else 3)

        role_rank = {
            RefRole.scene: 0,
            RefRole.actor: 1,
            RefRole.costume: 2,
            RefRole.other: 3,
            # prop is last so scene + visible people keep priority
            RefRole.prop: 9,
        }
        candidates = [
            r
            for r in (shot.refs or [])
            if r.role not in (RefRole.layout_ref_frame,)
        ]
        candidates.sort(
            key=lambda r: (
                role_rank.get(r.role, 5),
                r.picture_index or 10**6,
            )
        )

        images: dict[str, tuple[str, bytes]] = {}
        labels: list[str] = []
        source_refs: list[LayoutSourceRef] = []
        skipped: list[str] = []
        scene_count = 0
        actor_count = 0
        sent_prop_ids: set[str] = set()

        for ref in candidates:
            if ref.role == RefRole.scene and scene_count >= 1:
                skipped.append(f"scene:{ref.asset_id}(extra scene skipped)")
                continue
            if ref.role == RefRole.actor and actor_count >= 2:
                skipped.append(f"actor:{ref.asset_id}(actor cap)")
                continue
            if len(images) >= max_images:
                skipped.append(f"{ref.role.value}:{ref.asset_id}(slot full)")
                continue

            kind = role_to_library_kind(ref.role.value)
            asset = None
            if kind:
                asset = load_asset(kind, ref.asset_id)
            if asset is None:
                for k in LIBRARY_KINDS:
                    asset = load_asset(k, ref.asset_id)
                    if asset:
                        break
            if not asset:
                skipped.append(f"{ref.role.value}:{ref.asset_id}(missing asset)")
                continue

            filename: str | None = None
            data: bytes | None = None
            used_key = ref.file_key or ""

            if ref.role == RefRole.actor:
                packed = _actor_image_for_ref_frame(
                    asset,
                    preferred_key=ref.file_key,
                )
                if packed:
                    filename, data, used_key = packed
            elif ref.role == RefRole.scene:
                packed_scene = _scene_image_for_ref_frame(
                    asset, preferred_key=ref.file_key
                )
                if packed_scene:
                    filename, data, used_key = packed_scene
            elif ref.file_key:
                pair = _read_asset_image_bytes(
                    asset,
                    role=ref.role.value,
                    file_key=ref.file_key,
                )
                if pair:
                    filename, data = pair
                    used_key = ref.file_key
            else:
                from ...core.library.images import resolve_asset_image

                packed_other = resolve_asset_image(
                    asset,
                    role=ref.role.value,
                    file_key=ref.file_key,
                )
                if packed_other:
                    filename, data, used_key = packed_other

            if not filename or data is None:
                skipped.append(f"{ref.role.value}:{ref.asset_id}(no image)")
                continue

            slot = f"ref_{len(images)}"
            images[slot] = (filename, data)
            source_refs.append(
                LayoutSourceRef(
                    role=ref.role,
                    asset_id=ref.asset_id,
                    file_key=used_key.split("->", 1)[0] if used_key else None,
                    notes=ref.notes,
                )
            )
            name = asset.name or asset.id
            if ref.role == RefRole.scene:
                labels.append(
                    f"Image1 SCENE environment ({name}, {used_key}) — "
                    f"FULL location must fill the frame (walls/floor/lights/furniture); "
                    f"composite character INTO this set; never pure black/gray studio void"
                )
                scene_count += 1
            elif ref.role == RefRole.actor:
                approved_appearance = str(
                    (asset.meta or {}).get("description") or ""
                ).strip()
                appearance_lock = (
                    f"APPROVED APPEARANCE for this exact character: "
                    f"{approved_appearance}. "
                    if approved_appearance
                    else ""
                )
                labels.append(
                    f"Image{len(images)} CHARACTER ({name}, {used_key}) — "
                    f"{appearance_lock}"
                    f"face/hair/outfit identity only; discard studio backdrop; "
                    f"place ONE full/3-quarter body person doing the blocking action IN the scene"
                )
                actor_count += 1
            else:
                labels.append(
                    f"Image{len(images)} {ref.role.value.upper()} ({name}, {used_key}) — "
                    "preserve the object's shape, color, and markings only; discard its "
                    "catalog backdrop and place exactly one according to the shot action"
                )
                if ref.role == RefRole.prop:
                    sent_prop_ids.add(ref.asset_id)

        if skipped:
            labels.append(
                "Cast but not sent as image: " + ", ".join(skipped)
            )

        # Props displaced by the three-socket limit remain explicit text guidance.
        for r in shot.refs or []:
            if r.role != RefRole.prop or r.asset_id in sent_prop_ids:
                continue
            pa = load_asset("props", r.asset_id)
            name = pa.name if pa and pa.name else r.asset_id
            labels.append(
                f"PROP text only (image slot unavailable): {name} — "
                f"appear naturally in-hand if the beat needs it; not product photography"
            )

        logger.info(
            "ref_frame refs for %s: %d image(s) scene-first (skipped=%s)",
            shot.id,
            len(images),
            skipped,
        )
        image_labels = [label for label in labels if label.startswith("Image")][
            : len(images)
        ]
        return {
            "images": images,
            "labels": labels,
            "image_labels": image_labels,
            "source_refs": source_refs,
        }

    async def write_prompts_after_layout(self, shot_id: str) -> Shot:
        """Wake LLM, reload context from disk, fill PromptSections for the shot."""
        shot = _find_shot(shot_id)
        if shot is None:
            raise ValueError(f"shot not found: {shot_id}")
        shot = sync_selected_layout_refs(shot)
        project = load_project(shot.project_id)
        if project is None:
            raise ValueError(f"project not found: {shot.project_id}")

        ctx = load_agent_context(shot.project_id)
        if ctx is not None:
            current_summary = _shot_context_summary(shot)
            refreshed_summaries: list[dict[str, Any]] = []
            refreshed_current_shot = False
            for summary in ctx.shot_summaries:
                if summary.get("id") == shot.id:
                    refreshed_summaries.append(current_summary)
                    refreshed_current_shot = True
                else:
                    refreshed_summaries.append(summary)
            if not refreshed_current_shot:
                refreshed_summaries.append(current_summary)
            ctx = ctx.model_copy(
                update={"shot_summaries": refreshed_summaries}
            )
        context_json = (
            ctx.model_dump_json(indent=2) if ctx is not None else "{}"
        )

        meta = dict(shot.meta or {})
        layout_visual_analyses = dict(meta.get("layout_visual_analyses") or {})
        complete_with_images = getattr(self.plan_provider, "complete_with_images", None)
        if callable(complete_with_images):
            from .vision import _read_layout_bytes, image_bytes_to_b64_jpeg

            for layout in shot.layout_refs:
                asset_id = str(layout.asset_id or "")
                if not layout.selected_for_h3 or not asset_id or asset_id in layout_visual_analyses:
                    continue
                pair = _read_layout_bytes(asset_id)
                encoded = image_bytes_to_b64_jpeg(pair[1]) if pair else None
                if not encoded:
                    continue
                analysis = await complete_with_images(
                    (
                        "Inspect one Director Studio Layout for H3 prompt grounding. "
                        "Describe only visible composition, blocking, scale, eyelines, set geometry, "
                        "handled props, and lighting. Do not invent story facts."
                    ),
                    (
                        f"Shot: {shot.title}\n"
                        f"Beat: {shot.script_beat}\n"
                        f"Layout purpose: {layout.purpose}\n"
                        "Return one concise paragraph describing what the Layout visibly establishes."
                    ),
                    images=[encoded],
                    guides=("h3-prompt-writing",),
                )
                cleaned = str(analysis or "").strip()
                if cleaned:
                    layout_visual_analyses[asset_id] = {"analysis": cleaned}
        meta["layout_visual_analyses"] = layout_visual_analyses

        prompt_refs: list[dict[str, Any]] = []
        for ref in sorted(shot.refs, key=lambda item: item.picture_index):
            kind = role_to_library_kind(ref.role.value)
            asset = load_asset(kind, ref.asset_id) if kind else None
            if asset is None and kind is None:
                for candidate_kind in LIBRARY_KINDS:
                    asset = load_asset(candidate_kind, ref.asset_id)
                    if asset is not None:
                        break
            prompt_refs.append(
                {
                    "role": ref.role.value,
                    "asset_id": ref.asset_id,
                    "asset_name": asset.name if asset else "",
                    "file_key": ref.file_key or "",
                    "picture_index": ref.picture_index,
                    "approved_notes": asset.notes if asset else "",
                    "approved_description": (
                        str((asset.meta or {}).get("description") or "")
                        if asset
                        else ""
                    ),
                    "visual_analysis": str(
                        (
                            layout_visual_analyses.get(ref.asset_id) or {}
                        ).get("analysis")
                        or ""
                    ),
                }
            )
        refs_json = json.dumps(prompt_refs, ensure_ascii=False)
        selected_layouts = selected_layout_prompt_context(shot)
        selected_layout_asset_id = (
            str(selected_layouts[0]["asset_id"]) if selected_layouts else ""
        )
        selected_layouts_json = json.dumps(
            selected_layouts,
            ensure_ascii=False,
        )
        prompt_voice_refs: list[dict[str, Any]] = []
        for ref in sorted(shot.voice_refs, key=lambda item: item.audio_index):
            asset = load_asset("voices", ref.asset_id)
            prompt_voice_refs.append(
                {
                    "asset_id": ref.asset_id,
                    "asset_name": asset.name if asset else "",
                    "file_key": ref.file_key,
                    "audio_index": ref.audio_index,
                    "speaker": ref.speaker,
                    "approved_notes": asset.notes if asset else "",
                    "approved_description": (
                        str((asset.meta or {}).get("description") or "")
                        if asset
                        else ""
                    ),
                    "duration_s": (
                        (asset.meta or {}).get("duration_s") if asset else None
                    ),
                }
            )
        voice_refs_json = json.dumps(prompt_voice_refs, ensure_ascii=False)

        keep = bool(getattr(settings, "llm_keep_loaded", True))
        async with self.orchestrator.llm_session(release_on_exit=not keep):
            await self.orchestrator.ensure_llm_ready()
            user = prompt_text.PROMPT_SECTIONS_USER_TEMPLATE.format(
                title=shot.title,
                scene_id=shot.scene_id,
                script_beat=shot.script_beat,
                shot_type=shot.shot_type,
                camera_angle=shot.camera_angle,
                camera_motion=shot.camera_motion,
                composition=shot.composition,
                duration_s=shot.duration_s,
                dialogue_json=json.dumps(shot.dialogue),
                refs_json=refs_json,
                selected_layouts_json=selected_layouts_json,
                voice_refs_json=voice_refs_json,
                layout_asset_id=selected_layout_asset_id,
                feedback=shot.feedback or "",
                context_json=context_json,
            )
            raw = await self.plan_provider.complete(
                prompt_text.H3_PROMPT_INSTRUCTIONS,
                user,
                guides=("h3-prompt-writing",),
            )
            required_layout_indices = [
                int(item["picture_index"]) for item in selected_layouts
            ]

            def parse_and_validate(value: str) -> PromptSections:
                parsed = PromptSections(**parse_prompt_sections_json(value))
                parsed = _apply_source_audio_contract(parsed, shot)
                ordered_text = parsed.as_ordered_text()
                validate_tail_frame_transition_prompt(parsed, selected_layouts)
                validate_required_picture_bindings(
                    ordered_text,
                    required_layout_indices,
                    submitted_picture_indices=(
                        ref.picture_index for ref in shot.refs
                    ),
                )
                return parsed

            try:
                prompt_sections = parse_and_validate(raw)
            except Exception as first_err:
                repair = (
                    f"Original shot/context request:\n{user}\n\n"
                    f"Previous prompt JSON failed: {first_err}\n"
                    f"Raw:\n{raw}\nReturn valid six-section JSON only."
                )
                raw2 = await self.plan_provider.complete(
                    prompt_text.H3_PROMPT_INSTRUCTIONS,
                    repair,
                    guides=("h3-prompt-writing",),
                )
                prompt_sections = parse_and_validate(raw2)

        layout_asset_ids = [
            str(item["asset_id"]) for item in selected_layouts
        ]
        meta["prompt_layout_asset_ids"] = layout_asset_ids
        meta["prompt_layout_asset_id"] = (
            layout_asset_ids[0] if layout_asset_ids else ""
        )
        meta["prompt_layout_signature"] = layout_prompt_signature(shot)
        meta["prompt_picture_signature"] = picture_ref_signature(shot.refs)
        meta["prompt_voice_signature"] = voice_ref_signature(shot.voice_refs)
        meta["material_review_pending"] = False
        meta.pop("material_changes", None)
        shot = shot.model_copy(
            update={
                "prompt_sections": prompt_sections,
                "meta": meta,
                **(
                    {
                        "status": ShotStatus.needs_review,
                        "blocked_reasons": [],
                    }
                    if shot.refs and shot.status == ShotStatus.blocked
                    else {}
                ),
            }
        )
        save_shot(shot)

        all_shots = list_shots(shot.project_id)
        save_agent_context(
            shot.project_id,
            _build_context(project, all_shots, phase="awaiting_h3"),
        )
        return shot
