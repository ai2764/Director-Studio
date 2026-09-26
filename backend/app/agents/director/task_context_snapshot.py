"""Read authoritative sources, retaining content identities and stable reads."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ...core.projects.store import load_project, list_shots
from ...core.projects.dialogue import digest
from ...core.projects.chat_history import load_chat_history
from ...core.projects.layouts import selected_layout_prompt_context, sync_selected_layout_refs
from ...core.library.store import load_asset, asset_dir
from ...core.jobs.store import load_job
from .brief import directing_request_sources
from .material_review import capture_references
from .planner import role_to_library_kind
from .prompt_retry import authored_payload
from .task_context_models import TaskSnapshot, SourceRecord


class ContextChanged(ValueError):
    code = "CONTEXT_CHANGED"

    def __init__(self, source_key="snapshot"):
        super().__init__(f"CONTEXT_CHANGED: {source_key}; reload current evidence")


def _file_identity(path):
    if path is None or not path.is_file():
        return {"missing": True}
    with path.open("rb") as stream:
        signature = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"filename": path.name, "content_sha256": signature}


def _capture_once(project_id):
    project = load_project(project_id)
    if project is None:
        raise ValueError("CONTEXT_SOURCE_NOT_ALLOWED: project")
    shots = [s for s in list_shots(project_id) if s.project_id == project_id]
    sources = {}

    def add(key, payload, trust="authored"):
        sources[key] = SourceRecord(key=key, version=digest(payload), trust=trust, payload=payload)
        return key

    project_data = project.model_dump(mode="json", exclude={"updated_at"})
    requirements = directing_request_sources(project)
    # Shot IDs are catalog navigation, not a giant embedded prompt manifest.
    project_facts = {k: v for k, v in project_data.items() if k not in {"script_text", "shot_ids"}}
    add(f"project:{project_id}", {**project_facts, "requirements": requirements})
    add(f"script:{project_id}", {"text": project.script_text}, "user")
    messages = load_chat_history(project_id)
    for message in messages:
        add(f"message:{message.id}", message.model_dump(mode="json"),
            "user" if message.role == "user" else "derived")
    add("catalog:messages", {"items": [{"id": m.id, "role": m.role,
        "preview": m.content[:120], "truncated": len(m.content) > 120} for m in messages]}, "derived")
    add("catalog:shots", {"items": [{"id": s.id, "title": s.title} for s in shots]}, "derived")
    shot_data = {}
    for persisted in shots:
        shot = sync_selected_layout_refs(persisted)
        from .chat_context import verified_dialogue_lines
        lines = verified_dialogue_lines(project, shot)
        evidence = [line.model_dump(mode="json") for line in lines] if lines else []
        selected = selected_layout_prompt_context(shot)
        origins = {layout.asset_id: layout.origin.model_dump(mode="json")
                   for layout in shot.layout_refs if layout.selected_for_h3 and layout.origin}
        selected = [{**item, **({"origin": origins[item["asset_id"]]}
                     if item["asset_id"] in origins else {})} for item in selected]
        data = {**authored_payload(shot), "prompt_sections": shot.prompt_sections.model_dump(mode="json"),
            "prompt_revision_request": shot.meta.get("prompt_revision_request"),
            "prompt_revision_requests": shot.meta.get("prompt_revision_requests", []),
            "dialogue_sources": evidence, "selected_layouts": selected}
        refs, missing, keys = [], [], []
        if shot.refs:
            try:
                refs = capture_references(shot)[0]
            except (OSError, ValueError) as exc:
                missing.append({"code": "CONTEXT_SOURCE_MISSING", "source_key": f"shot:{shot.id}",
                                "detail": str(exc)})
        for ref, record in zip(sorted(shot.refs, key=lambda r: r.picture_index), refs):
            kind = role_to_library_kind(ref.role.value) or "other"
            key = add(f"asset:{kind}:{ref.asset_id}:{record['file_key']}",
                {k: v for k, v in record.items() if k not in {"picture_index", "reference_notes"}})
            keys.append(key)
        voices = []
        for voice in shot.voice_refs:
            asset = load_asset("voices", voice.asset_id)
            payload = {"asset_id": voice.asset_id, "file_key": voice.file_key, "missing": True}
            if asset is not None:
                filename = asset.files.get(voice.file_key)
                root = asset_dir("voices", asset.id).resolve()
                path = (root / filename).resolve() if filename else None
                if path is not None and not path.is_relative_to(root):
                    path = None
                payload = {"asset_id": asset.id, "file_key": voice.file_key, "asset_name": asset.name,
                           "notes": asset.notes, "meta": asset.meta, **_file_identity(path)}
            key = add(f"asset:voices:{voice.asset_id}:{voice.file_key}", payload)
            keys.append(key)
            voices.append({**payload, **voice.model_dump(mode="json")})
            if payload.get("missing"):
                missing.append({"code": "CONTEXT_SOURCE_MISSING", "source_key": key})
        audio = None
        if shot.source_audio_path:
            audio = _file_identity(Path(shot.source_audio_path))
            key = add(f"audio:{shot.id}", audio)
            keys.append(key)
            if audio.get("missing"):
                missing.append({"code": "CONTEXT_SOURCE_MISSING", "source_key": key})
        job_ids = {job_id for job_id in (shot.h3_job_id, shot.ref_frame_job_id) if job_id}
        for layout in shot.layout_refs:
            if layout.selected_for_h3 and layout.origin:
                job_ids.add(layout.origin.source_job_id)
        for job_id in sorted(job_ids):
            job = load_job(job_id)
            # A foreign job ID never grants cross-project read access.
            if job and job.project_id == project_id:
                add(f"job:{job_id}", job.model_dump(mode="json"), "execution")
        add(f"shot:{shot.id}", data)
        shot_data[shot.id] = {**data, "references": refs, "voice_refs": voices,
                             "source_audio": audio, "source_keys": keys, "missing": missing}
    snapshot = TaskSnapshot(project_id=project_id, project=project_data, shots=shot_data, sources=sources)
    stable = digest([project_data, [s.model_dump(mode="json") for s in shots], shot_data,
                     {k: v.model_dump(mode="json") for k, v in sources.items()}])
    return snapshot, stable


def capture_task_snapshot(project_id: str) -> TaskSnapshot:
    for _ in range(3):
        first, signature = _capture_once(project_id)
        _, second = _capture_once(project_id)
        if signature == second:
            return first
    raise ContextChanged()


def assert_packet_current(packet) -> None:
    current = capture_task_snapshot(packet.project_id)
    for key, version in packet.source_versions.items():
        record = current.sources.get(key)
        if record is None or record.version != version:
            raise ContextChanged(key)
