"""Actor design generation and acceptance tools."""

from __future__ import annotations

import re
from typing import Any

from ....config import settings
from ....core.library.store import load_asset
from ....core.schemas import JobStatus
from ..intent import (
    actor_acceptance_intent,
    actor_design_intent,
    explicit_gpt_image_intent,
    normalize_text,
)


async def handle_actor_tool(
    *,
    name: str,
    args: dict[str, Any],
    project_id: str,
    user_feedback: str,
    runtime: Any,
    actions: list[str],
    notes: list[str],
    result_payloads: list[dict[str, Any]] | None,
    images: list[Any] | None,
) -> bool:
    """Handle Actor-domain tools and return whether the name was recognized."""
    if name == "queue_actor_design":
        actor_name = str(args.get("name") or "").strip()
        description = str(args.get("description") or "").strip()
        generation_prompt = str(args.get("generation_prompt") or "").strip()
        requested_provider = str(args.get("provider") or "local").strip().lower()
        explicitly_local = bool(
            re.search(
                r"(?:本地|local).{0,20}(?:生成|生图|generator)|"
                r"(?:生成|生图|use).{0,20}(?:本地|local)",
                normalize_text(user_feedback),
                flags=re.I,
            )
        )
        explicitly_gpt = bool(
            explicit_gpt_image_intent(user_feedback)
            or (
                actor_design_intent(user_feedback)
                and re.search(
                    r"\b(?:chatgpt|gpt(?:-[a-z0-9.]+)?)\b",
                    normalize_text(user_feedback),
                    flags=re.I,
                )
            )
        )
        provider = "gpt" if explicitly_gpt and not explicitly_local else "local"
        if not actor_name or not description or not generation_prompt:
            raise ValueError(
                "queue_actor_design requires name, description, and generation_prompt"
            )
        if requested_provider not in {"gpt", "local"}:
            raise ValueError("queue_actor_design provider must be gpt or local")
        if provider == "gpt" and not settings.gpt_bridge_configured:
            raise runtime.gpt_tool_error(
                "configuration",
                "Configure the local ChatGPT Bridge before generating a GPT Actor design",
            )
        pipeline_id = "gpt_actor" if provider == "gpt" else "actor"
        job = runtime.create_job(
            pipeline_id=pipeline_id,
            asset_kind="actors",
            name=actor_name,
            notes=description,
            params={
                "description": description,
                "body_description": str(args.get("body_description") or "").strip(),
                "hair_description": str(args.get("hair_description") or "").strip(),
                "wardrobe_description": str(
                    args.get("wardrobe_description") or ""
                ).strip(),
                "generation_prompt": generation_prompt,
                "has_actor_ref": False,
                "has_wardrobe_ref": False,
                "mode": "text",
                "provider": provider,
            },
            project_id=project_id,
        )
        await runtime.start_pipeline_job(job, images={})
        terminal = await runtime.await_pipeline_job(job.id)
        if terminal is None:
            raise ValueError(f"Actor design job disappeared: {job.id}")
        if terminal.status != JobStatus.succeeded:
            raise ValueError(
                str(
                    terminal.error
                    or f"Actor design job status is {terminal.status.value}"
                )
            )
        preview = next(
            (
                terminal.outputs[key]
                for key in (
                    "master",
                    "fullbody_threeview",
                    "asset_sheet",
                    "bust_threeview",
                )
                if key in terminal.outputs and terminal.outputs[key].url
            ),
            None,
        )
        if preview is None or not preview.url:
            raise ValueError("Actor design completed without a preview image")
        actions.append(f"actor_design:{terminal.id}")
        if images is not None:
            images.append(
                runtime.chat_image_factory(
                    url=preview.url,
                    caption=f"Actor design · {actor_name} · {terminal.id}",
                )
            )
        if result_payloads is not None:
            result_payloads.append(
                {
                    "ok": True,
                    "provider": provider,
                    "job_id": terminal.id,
                    "review_status": "pending_review",
                }
            )
        notes.append(
            f"Actor design job {terminal.id} is ready for review. "
            "Say ‘这张可以’ to save it to the Actor library."
        )
        return True

    if name == "accept_actor_design":
        if not actor_acceptance_intent(user_feedback):
            raise ValueError(
                "accept_actor_design requires explicit acceptance in the current user turn"
            )
        job_id = str(args.get("job_id") or "").strip()
        job = runtime.load_job(job_id)
        if (
            job is None
            or job.pipeline_id not in {"actor", "gpt_actor"}
            or job.asset_kind != "actors"
            or (job.project_id or None) != project_id
        ):
            raise ValueError(f"Actor design job not found in this project: {job_id}")
        if job.status != JobStatus.succeeded:
            raise ValueError(
                f"Actor design job status is {job.status.value}; it cannot be saved"
            )
        if job.library_asset_id:
            actor_asset = load_asset("actors", job.library_asset_id)
            if actor_asset is None:
                raise ValueError(f"Saved Actor asset is missing: {job.library_asset_id}")
        else:
            actor_asset = runtime.get_pipeline(job.pipeline_id).save_to_library(
                job,
                name=str(args.get("name") or "").strip() or None,
                notes=str(args.get("notes") or "").strip() or None,
                project_id=project_id,
            )
        actions.append(f"accept_actor_design:{actor_asset.id}")
        preview_url = next(
            (
                actor_asset.urls.get(key)
                for key in (
                    "master",
                    "fullbody_threeview",
                    "asset_sheet",
                    "bust_threeview",
                )
                if actor_asset.urls.get(key)
            ),
            None,
        )
        if images is not None and preview_url:
            images.append(
                runtime.chat_image_factory(
                    url=preview_url,
                    caption=f"Actor · {actor_asset.name}",
                )
            )
        notes.append(
            f"Saved Actor {actor_asset.id} ({actor_asset.name}) to this project's library."
        )
        return True

    return False
