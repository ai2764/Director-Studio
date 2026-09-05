"""Deterministic Director casting and storyboard-binding rules."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from ...core.library.store import load_asset
from ...core.projects.models import PromptSections, RefRole, Shot, ShotRef, ShotVoiceRef, ShotStatus
from ...core.projects.store import new_shot_id
from ...core.schemas import LibraryAsset
from .asset_catalog import _asset_index, _default_file_key, _inventory, _repair_unique_file_key_typo
from .planner import ShotDraft, role_to_library_kind, role_to_ref_role

logger = logging.getLogger("director_studio.director.casting")


def _append_ref(
    refs: list[ShotRef],
    *,
    role: RefRole,
    asset_id: str,
    index: dict[str, LibraryAsset],
    notes: str = "",
    file_key: str | None = None,
    picture_index: int | None = None,
) -> None:
    used_pic = {r.picture_index for r in refs}
    if picture_index is not None:
        picture = int(picture_index)
        if picture in used_pic:
            raise ValueError(f"duplicate picture_index: {picture}")
    else:
        picture = 1
        while picture in used_pic:
            picture += 1
    asset = index.get(asset_id)
    refs.append(
        ShotRef(
            role=role,
            asset_id=asset_id,
            picture_index=picture,
            file_key=file_key or _default_file_key(role, asset),
            notes=notes,
        )
    )


def _heuristic_match(
    draft: ShotDraft,
    inventory: list[dict[str, Any]],
    index: dict[str, LibraryAsset],
) -> tuple[list[ShotRef], list[str]]:
    """Validate LLM asset matches; simple name/tag heuristic fill; collect block reasons."""
    refs: list[ShotRef] = []
    blocked: list[str] = []
    used_refs: set[tuple[str, str]] = set()

    for match in draft.asset_matches:
        ref_role = role_to_ref_role(match.role)
        if ref_role is None or ref_role == RefRole.layout_ref_frame:
            # Layout is produced later; ignore suggested layout ids at plan time
            if ref_role == RefRole.layout_ref_frame:
                continue
            blocked.append(f"unknown asset role: {match.role}")
            continue
        asset_id = match.asset_id
        if asset_id not in index:
            # name/tag heuristic fallback within kind
            kind = role_to_library_kind(match.role)
            alt = _find_by_name_or_tag(draft, kind, inventory) if kind else None
            if alt and not any(aid == alt for aid, _key in used_refs):
                asset_id = alt
            else:
                blocked.append(f"missing library asset: {match.asset_id} (role={match.role})")
                continue
        asset = index.get(asset_id)
        requested_key = _repair_unique_file_key_typo(match.file_key, asset)
        ref_identity = (asset_id, requested_key or "")
        if ref_identity in used_refs:
            continue
        used_refs.add(ref_identity)
        if requested_key and (
            asset is None
            or not asset.files
            or not asset.files.get(requested_key)
        ):
            blocked.append(
                f"invalid file_key {requested_key!r} for asset {asset_id}"
            )
        _append_ref(
            refs,
            role=ref_role,
            asset_id=asset_id,
            index=index,
            notes="llm-cast" if match.asset_id == asset_id else "llm-cast-fallback",
            file_key=requested_key,
            picture_index=match.picture_index,
        )

    return refs, blocked


def _resolve_voice_matches(
    draft: ShotDraft,
    inventory: list[dict[str, Any]],
    index: dict[str, LibraryAsset],
) -> list[ShotVoiceRef]:
    allowed = {
        str(item.get("id") or "")
        for item in inventory
        if item.get("kind") == "voices" and item.get("h3_ready")
    }
    resolved: list[ShotVoiceRef] = []
    for match in draft.voice_matches:
        if match.asset_id not in allowed:
            continue
        asset = index.get(match.asset_id)
        if asset is None or asset.kind != "voices":
            continue
        if not (asset.files or {}).get(match.file_key):
            continue
        resolved.append(
            ShotVoiceRef(
                asset_id=match.asset_id,
                audio_index=match.audio_index,
                file_key=match.file_key,
                speaker=match.speaker,
                notes=match.reason,
            )
        )
    return resolved


def _kind_candidates(
    kind: str,
    inventory: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    owned = [i for i in inventory if i.get("kind") == kind and i.get("owned_by_project")]
    if owned:
        return owned
    return [i for i in inventory if i.get("kind") == kind]


def _find_by_name_or_tag(
    draft: ShotDraft,
    kind: str,
    inventory: list[dict[str, Any]],
    *,
    extra_hay: str = "",
) -> str | None:
    hay = " ".join(
        [
            draft.title,
            draft.script_beat,
            draft.scene_id,
            *draft.dialogue,
            extra_hay,
        ]
    ).lower()
    best_id: str | None = None
    best_score = 0
    for item in _kind_candidates(kind, inventory):
        score = 0
        name = str(item.get("name") or "").lower()
        notes = str(item.get("notes") or "").lower()
        desc = str(item.get("description") or "").lower()
        tags = [str(t).lower() for t in (item.get("tags") or [])]
        # External packs: match on source filename / stored filenames
        fnames = " ".join(
            str(x).lower()
            for x in (
                [item.get("source_filename") or ""]
                + list(item.get("filenames") or [])
            )
            if x
        )
        if item.get("owned_by_project"):
            score += 1  # slight bias
        if name and name in hay:
            score += 5
        if name and any(w and w in name for w in hay.replace(",", " ").split() if len(w) > 2):
            score += 2
        for t in tags:
            if t and t in hay:
                score += 3
        for word in name.replace("_", " ").replace("-", " ").split():
            if len(word) > 2 and word in hay:
                score += 2
        # Filename tokens (e.g. 面试间-面试官side, mia.png, 门外走廊)
        for token in re.findall(r"[\w\u4e00-\u9fff]+", fnames):
            if len(token) > 1 and token in hay:
                score += 4
            if len(token) > 2 and any(
                token in w or w in token for w in hay.replace(",", " ").split() if len(w) > 2
            ):
                score += 2
        for blob in (notes, desc):
            for word in blob.replace("_", " ").split():
                if len(word) > 3 and word in hay:
                    score += 1
        # Prefer three-view actors / multi-angle scenes when present
        fkeys = [str(k).lower() for k in (item.get("file_keys") or [])]
        if kind == "actors" and any("threeview" in k or "three_view" in k for k in fkeys):
            score += 1
        if kind == "scenes" and fkeys:
            score += 1
        if score > best_score:
            best_score = score
            best_id = str(item["id"])
    return best_id if best_score > 0 else None


def _default_asset_id(kind: str, inventory: list[dict[str, Any]]) -> str | None:
    """When casting is unambiguous (1 candidate), pick it; else first project-owned."""
    cands = _kind_candidates(kind, inventory)
    if not cands:
        return None
    if len(cands) == 1:
        return str(cands[0]["id"])
    # Multiple: still pick best-equipped (threeview / any plate)
    ranked: list[tuple[int, str]] = []
    for item in cands:
        score = 2 if item.get("owned_by_project") else 0
        fkeys = [str(k).lower() for k in (item.get("file_keys") or [])]
        if kind == "actors" and any("threeview" in k for k in fkeys):
            score += 3
        if kind == "scenes" and fkeys:
            score += 2
        ranked.append((score, str(item["id"])))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    # Only auto-default when top score clearly production-ready or single project-owned
    if ranked[0][0] >= 3:
        return ranked[0][1]
    owned = [i for i in cands if i.get("owned_by_project")]
    if len(owned) == 1:
        return str(owned[0]["id"])
    return None


def _complete_refs_from_inventory(
    refs: list[ShotRef],
    draft: ShotDraft,
    inventory: list[dict[str, Any]],
    index: dict[str, LibraryAsset],
    *,
    script_text: str = "",
) -> tuple[list[ShotRef], list[str]]:
    """
    Agent casting completion: fill missing actor/scene from inventory when LLM
    omitted them or only partial matches. Prefers name match, then unambiguous default.
    """
    refs = list(refs)
    used = {r.asset_id for r in refs}
    notes_out: list[str] = []

    has_actor = any(r.role == RefRole.actor for r in refs)
    has_scene = any(r.role == RefRole.scene for r in refs)

    if not has_actor:
        aid = _find_by_name_or_tag(
            draft, "actors", inventory, extra_hay=script_text[:500]
        ) or _default_asset_id("actors", inventory)
        if aid and aid not in used and aid in index:
            _append_ref(
                refs,
                role=RefRole.actor,
                asset_id=aid,
                index=index,
                notes="auto-cast:actor",
            )
            used.add(aid)
            notes_out.append(f"auto-cast actor {aid}")
        elif not _kind_candidates("actors", inventory):
            notes_out.append("no actor assets in inventory to cast")
        else:
            notes_out.append(
                "could not auto-cast actor — ambiguous inventory; name a character or cast in chat"
            )

    if not has_scene:
        aid = _find_by_name_or_tag(
            draft, "scenes", inventory, extra_hay=script_text[:500]
        ) or _default_asset_id("scenes", inventory)
        if aid and aid not in used and aid in index:
            _append_ref(
                refs,
                role=RefRole.scene,
                asset_id=aid,
                index=index,
                notes="auto-cast:scene",
            )
            used.add(aid)
            notes_out.append(f"auto-cast scene {aid}")
        elif not _kind_candidates("scenes", inventory):
            notes_out.append("no scene assets in inventory to cast")
        else:
            notes_out.append(
                "could not auto-cast scene — generate Set Design or name the location"
            )

    # Recompute hard blocks for reference-frame readiness
    blocked: list[str] = []
    if not any(r.role == RefRole.actor for r in refs):
        blocked.append("ref_frame requires at least one actor ref (three-view)")
    if not any(r.role == RefRole.scene for r in refs):
        blocked.append(
            "ref_frame requires a scene library ref (generate Set Design plate first)"
        )
    # Soft notes as blocked only when hard missing; else store auto notes in ref.notes
    if blocked and notes_out:
        for n in notes_out:
            if n not in blocked:
                blocked.append(n)
    return refs, blocked


def _shot_from_draft(
    project_id: str,
    draft: ShotDraft,
    *,
    inventory: list[dict[str, Any]],
    index: dict[str, LibraryAsset],
    script_text: str = "",
) -> Shot:
    refs, match_blocked = _heuristic_match(draft, inventory, index)
    voice_refs = _resolve_voice_matches(draft, inventory, index)
    refs, fill_blocked = _complete_refs_from_inventory(
        refs, draft, inventory, index, script_text=script_text
    )
    # Prefer fill_blocked (authoritative casting state); keep unresolved LLM id errors
    blocked = list(fill_blocked)
    for b in match_blocked:
        if b not in blocked and "missing library asset" in b and not refs:
            blocked.append(b)
        elif b not in blocked and "unknown asset role" in b:
            blocked.append(b)
        elif b not in blocked and "exceeded" in b:
            blocked.append(b)
        elif b not in blocked and "invalid file_key" in b:
            blocked.append(b)

    # If we successfully cast actor+scene, clear casting blocks even if LLM id was wrong
    if any(r.role == RefRole.actor for r in refs) and any(
        r.role == RefRole.scene for r in refs
    ):
        blocked = [b for b in blocked if "missing library asset" not in b]

    status = ShotStatus.blocked if blocked else ShotStatus.ref_frame_pending
    if not blocked:
        status = ShotStatus.ref_frame_pending
    return Shot(
        id=new_shot_id(),
        project_id=project_id,
        scene_id=draft.scene_id,
        title=draft.title,
        script_beat=draft.script_beat,
        shot_type=draft.shot_type,
        camera_angle=draft.camera_angle,
        camera_motion=draft.camera_motion,
        composition=draft.composition,
        duration_s=draft.duration_s,
        status=status,
        refs=refs,
        voice_refs=voice_refs,
        dialogue=list(draft.dialogue),
        blocked_reasons=blocked,
        prompt_sections=PromptSections(),
    )


def _validate_storyboard_bindings(
    drafts: list[ShotDraft],
    *,
    inventory: list[dict[str, Any]],
    index: dict[str, LibraryAsset],
) -> None:
    """Reject explicit Agent bindings that cannot be materialized exactly."""
    available = {str(item.get("id") or "") for item in inventory}
    expected_kinds = {
        RefRole.actor: "actors",
        RefRole.costume: "costumes",
        RefRole.scene: "scenes",
        RefRole.prop: "props",
    }
    for shot_index, draft in enumerate(drafts, start=1):
        seen: set[tuple[str, str, str]] = set()
        for match in draft.asset_matches:
            role = role_to_ref_role(match.role)
            if role is None or role == RefRole.layout_ref_frame:
                raise ValueError(
                    f"shot {shot_index} has invalid asset role {match.role!r}"
                )
            asset = index.get(match.asset_id)
            if match.asset_id not in available or asset is None:
                raise ValueError(
                    f"shot {shot_index} references unknown library asset "
                    f"{match.asset_id!r}"
                )
            expected_kind = expected_kinds.get(role)
            if expected_kind is not None and asset.kind != expected_kind:
                raise ValueError(
                    f"shot {shot_index} binds {match.asset_id!r} as {role.value}, "
                    f"but it is a {asset.kind} asset"
                )
            file_key = match.file_key or _default_file_key(role, asset)
            if not file_key or not (asset.files or {}).get(file_key):
                raise ValueError(
                    f"shot {shot_index} has invalid file_key {file_key!r} "
                    f"for asset {match.asset_id}"
                )
            identity = (role.value, match.asset_id, file_key)
            if identity in seen:
                raise ValueError(
                    f"shot {shot_index} repeats asset binding "
                    f"{role.value}:{match.asset_id}:{file_key}"
                )
            seen.add(identity)

        for match in draft.voice_matches:
            asset = index.get(match.asset_id)
            inventory_item = next(
                (
                    item
                    for item in inventory
                    if str(item.get("id") or "") == match.asset_id
                ),
                None,
            )
            if (
                asset is None
                or inventory_item is None
                or asset.kind != "voices"
                or not inventory_item.get("h3_ready")
            ):
                raise ValueError(
                    f"shot {shot_index} references unknown or non-H3-ready voice asset "
                    f"{match.asset_id!r}"
                )
            if not (asset.files or {}).get(match.file_key):
                raise ValueError(
                    f"shot {shot_index} has invalid voice file_key "
                    f"{match.file_key!r} for asset {match.asset_id}"
                )


def _validate_materialized_storyboard_bindings(
    shots: list[Shot],
    *,
    inventory: list[dict[str, Any]],
    index: dict[str, LibraryAsset],
) -> None:
    """Validate every stored binding, including references added by auto-casting."""
    inventory_by_id = {
        str(item.get("id") or ""): item for item in inventory
    }
    image_kinds_by_role = {
        RefRole.layout_ref_frame: {"layouts"},
        RefRole.actor: {"actors"},
        RefRole.costume: {"costumes"},
        RefRole.scene: {"scenes"},
        RefRole.prop: {"props"},
        RefRole.other: {"actors", "costumes", "scenes", "props"},
    }
    for shot_index, shot in enumerate(shots, start=1):
        for ref in shot.refs:
            asset = index.get(ref.asset_id)
            inventory_item = inventory_by_id.get(ref.asset_id)
            if asset is None or inventory_item is None:
                raise ValueError(
                    f"shot {shot_index} materialized inaccessible image asset "
                    f"{ref.asset_id!r}"
                )
            allowed_kinds = image_kinds_by_role.get(ref.role)
            if allowed_kinds is None or asset.kind not in allowed_kinds:
                raise ValueError(
                    f"shot {shot_index} materialized invalid image binding "
                    f"{ref.role.value}:{ref.asset_id}; asset kind is {asset.kind}"
                )
            if not ref.file_key or not (asset.files or {}).get(ref.file_key):
                raise ValueError(
                    f"shot {shot_index} materialized invalid file_key "
                    f"{ref.file_key!r} for image asset {ref.asset_id}"
                )

        for ref in shot.voice_refs:
            asset = index.get(ref.asset_id)
            inventory_item = inventory_by_id.get(ref.asset_id)
            if (
                asset is None
                or inventory_item is None
                or asset.kind != "voices"
                or not inventory_item.get("h3_ready")
            ):
                raise ValueError(
                    f"shot {shot_index} materialized invalid voice binding "
                    f"{ref.asset_id!r}"
                )
            if not ref.file_key or not (asset.files or {}).get(ref.file_key):
                raise ValueError(
                    f"shot {shot_index} materialized invalid voice file_key "
                    f"{ref.file_key!r} for asset {ref.asset_id}"
                )


def _claim_storyboard_assets(
    project_id: str,
    shots: list[Shot],
    *,
    index: dict[str, LibraryAsset],
) -> None:
    """Apply the existing planner ownership rule to a saved storyboard."""
    from ...core.library.store import assign_asset_project

    for shot in shots:
        for ref in shot.refs:
            kind = role_to_library_kind(ref.role.value)
            if not kind:
                continue
            asset = index.get(ref.asset_id) or load_asset(kind, ref.asset_id)
            if asset and not asset.project_id:
                try:
                    assign_asset_project(kind, asset.id, project_id)
                except Exception:
                    logger.exception("assign_asset_project failed for %s", asset.id)
        for voice_ref in shot.voice_refs:
            asset = index.get(voice_ref.asset_id) or load_asset(
                "voices", voice_ref.asset_id
            )
            if asset and not asset.project_id:
                try:
                    assign_asset_project("voices", asset.id, project_id)
                except Exception:
                    logger.exception(
                        "assign voice asset project failed for %s", asset.id
                    )


def recast_shot_assets(
    project_id: str,
    shot: Shot,
    *,
    inventory: list[dict[str, Any]] | None = None,
    index: dict[str, LibraryAsset] | None = None,
    script_text: str = "",
    force: bool = False,
    inventory_loader: Callable[[str | None], list[dict[str, Any]]] = _inventory,
    index_loader: Callable[[str | None], dict[str, LibraryAsset]] = _asset_index,
) -> Shot:
    """Re-run agent casting for an existing shot (chat / reference-frame prep)."""
    inv = inventory if inventory is not None else inventory_loader(project_id)
    idx = index if index is not None else index_loader(project_id)
    draft = ShotDraft(
        scene_id=shot.scene_id or "sc01",
        title=shot.title or "shot",
        script_beat=shot.script_beat or shot.title or "action",
        duration_s=shot.duration_s or 8.0,
        dialogue=list(shot.dialogue or []),
        asset_matches=[],
    )
    refs = [] if force else list(shot.refs)
    # Drop empty / layout-only when force
    if force:
        refs = []
    else:
        refs = [r for r in refs if r.role != RefRole.layout_ref_frame or r.asset_id]
    refs, blocked = _complete_refs_from_inventory(
        refs, draft, inv, idx, script_text=script_text
    )
    status = shot.status
    if blocked:
        status = ShotStatus.blocked
    elif shot.status == ShotStatus.blocked and not blocked:
        status = ShotStatus.ref_frame_pending
    return shot.model_copy(
        update={
            "refs": refs,
            "blocked_reasons": blocked,
            "status": status,
        }
    )
