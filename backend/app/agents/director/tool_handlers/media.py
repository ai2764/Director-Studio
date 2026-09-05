"""Cross-shot media extraction and project status tools."""

from __future__ import annotations

from typing import Any

from ....core.media.clip_generations import ClipGenerationAmbiguous
from ....core.media import tail_frame
from ....core.projects.models import Project, Shot
from ....core.projects.store import load_shot


async def handle_media_tool(
    *,
    name: str,
    args: dict[str, Any],
    project_id: str,
    project: Project,
    shots: list[Shot],
    runtime: Any,
    actions: list[str],
    notes: list[str],
    touched: set[str],
    result_payloads: list[dict[str, Any]] | None,
    images: list[Any] | None,
) -> bool:
    if name in {"get_status", "status"}:
        actions.append("status")
        notes.append(runtime.status_summary(project, shots))
        return True
    if name != "extract_clip_tail_frame":
        return False

    source_shot_id = str(args.get("source_shot_id") or "").strip()
    target_shot_id = str(args.get("target_shot_id") or "").strip()
    if not source_shot_id or not target_shot_id:
        notes.append("extract_clip_tail_frame: specify source_shot_id and target_shot_id")
        return True

    def optional_selector(key: str) -> str | None:
        value = args.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    try:
        extracted = tail_frame.extract_clip_tail_frame(
            project_id=project_id,
            source_shot_id=source_shot_id,
            target_shot_id=target_shot_id,
            source_version=optional_selector("source_version"),
            source_job_id=optional_selector("source_job_id"),
            output_kind=optional_selector("output_kind"),
        )
    except ClipGenerationAmbiguous as exc:
        blocking = (
            exc.blocking_status.value
            if hasattr(exc.blocking_status, "value")
            else str(exc.blocking_status)
        )
        if result_payloads is not None:
            result_payloads.append(
                {
                    "ok": False,
                    "needs_clarification": True,
                    "error": str(exc),
                    "latest_succeeded_job_id": exc.latest_succeeded_job_id,
                    "blocking_job_id": exc.blocking_job_id,
                    "blocking_status": blocking,
                }
            )
        notes.append(
            "extract_clip_tail_frame needs clarification: "
            f"{exc}. Latest completed job is {exc.latest_succeeded_job_id}; "
            f"a newer job {exc.blocking_job_id} is {blocking}. Ask whether to "
            "use the latest completed generation."
        )
        return True

    actions.append(f"extract_clip_tail_frame:{target_shot_id}")
    touched.add(target_shot_id)
    if result_payloads is not None:
        result_payloads.append(extracted)
    source_shot = load_shot(project_id, source_shot_id)
    target_shot = load_shot(project_id, target_shot_id)
    source_title = source_shot.title if source_shot else extracted["source_shot_id"]
    target_title = target_shot.title if target_shot else extracted["target_shot_id"]
    candidate = runtime.extracted_tail_frame_image(
        extracted,
        source_title=source_title,
        target_title=target_title,
    )
    if candidate is not None and images is not None:
        images.append(candidate)
    notes.append(
        f"Extracted the tail frame of **{source_title}** "
        f"(`{extracted['source_shot_id']}`) v{extracted['source_version']} "
        f"(job {extracted['source_job_id']}, {extracted['output_kind']}) "
        f"for **{target_title}** (`{extracted['target_shot_id']}`). "
        f"LayoutReference {extracted['layout_ref_id']} is pending human review "
        "and is not yet in the H3 Picture pack."
    )
    return True
