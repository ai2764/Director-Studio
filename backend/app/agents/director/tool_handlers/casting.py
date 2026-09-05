"""Shot asset casting and recasting tools."""

from __future__ import annotations

from typing import Any

from ....core.library.store import load_asset
from ....core.projects.models import RefRole, Shot, ShotRef, ShotStatus
from ....core.projects.store import save_shot
from ..asset_catalog import _asset_index, _inventory
from ..casting_service import recast_shot_assets
from ..intent import resolve_shot


async def handle_casting_tool(
    *,
    name: str,
    args: dict[str, Any],
    project_id: str,
    project_script: str,
    shots: list[Shot],
    actions: list[str],
    notes: list[str],
    touched: set[str],
) -> bool:
    if name not in {"recast_assets", "assign_assets", "match_assets", "cast"}:
        return False
    actions.append("recast_assets")
    inventory = _inventory(project_id)
    index = _asset_index(project_id)
    if args.get("all"):
        targets = list(shots)
    else:
        shot = resolve_shot(
            shots,
            shot_id=args.get("shot_id"),
            shot_index=args.get("shot_index") or args.get("index"),
            title=args.get("title"),
        )
        targets = [shot] if shot else []
    if not targets:
        notes.append("recast_assets: specify a shot or use all")
        return True

    actor_id = args.get("actor_id") or args.get("actor")
    scene_id = args.get("scene_id") or args.get("scene")
    force = bool(args.get("force", True))
    for shot in targets:
        if actor_id or scene_id:
            refs = [] if force else list(shot.refs)
            if force:
                refs = [ref for ref in shot.refs if ref.role == RefRole.layout_ref_frame]
            if actor_id:
                asset = index.get(str(actor_id)) or load_asset("actors", str(actor_id))
                if not asset:
                    notes.append(f"Actor not found: {actor_id}")
                    continue
                refs = [ref for ref in refs if ref.role != RefRole.actor]
                used = {ref.picture_index for ref in refs}
                picture_index = 1
                while picture_index in used:
                    picture_index += 1
                refs.append(
                    ShotRef(
                        role=RefRole.actor,
                        asset_id=asset.id,
                        picture_index=picture_index,
                        file_key="fullbody_threeview",
                        notes="chat-cast:actor",
                    )
                )
            if scene_id:
                asset = index.get(str(scene_id)) or load_asset("scenes", str(scene_id))
                if not asset:
                    notes.append(f"Scene not found: {scene_id}")
                    continue
                refs = [ref for ref in refs if ref.role != RefRole.scene]
                used = {ref.picture_index for ref in refs}
                picture_index = 1
                while picture_index in used:
                    picture_index += 1
                refs.append(
                    ShotRef(
                        role=RefRole.scene,
                        asset_id=asset.id,
                        picture_index=picture_index,
                        file_key="angle_00",
                        notes="chat-cast:scene",
                    )
                )
            updated = shot.model_copy(
                update={
                    "refs": refs,
                    "blocked_reasons": [],
                    "status": (
                        ShotStatus.ref_frame_pending
                        if shot.status == ShotStatus.blocked
                        else shot.status
                    ),
                }
            )
            updated = recast_shot_assets(
                project_id,
                updated,
                inventory=inventory,
                index=index,
                script_text=project_script,
                force=False,
            )
        else:
            updated = recast_shot_assets(
                project_id,
                shot,
                inventory=inventory,
                index=index,
                script_text=project_script,
                force=force,
            )
        save_shot(updated)
        ref_desc = ", ".join(
            f"{ref.role.value}:{ref.asset_id}" for ref in updated.refs
        )
        notes.append(
            f"Cast assets for **{updated.title}**: {ref_desc or '(none)'}"
            + (
                f" · blocked: {'; '.join(updated.blocked_reasons)}"
                if updated.blocked_reasons
                else ""
            )
        )
        touched.add(updated.id)
    return True
