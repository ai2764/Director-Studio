"""Selected Library descriptions plus byte hashes. Never decodes images for the LLM."""
import hashlib
from ..core.library.store import load_asset
from ..core.library.images import resolve_asset_image
from ..core.projects.dialogue import digest
from ..core.projects.store import load_project, load_shot
from ..core.projects.video_context import video_context_prompt_signature, video_context_status

KINDS = {"actor": "actors", "costume": "costumes", "scene": "scenes", "prop": "props", "layout_ref_frame": "layouts"}


def read_asset_description(project_id, asset_id, file_key=None):
    for kind in KINDS.values():
        asset = load_asset(kind, asset_id)
        if asset is not None:
            if asset.project_id not in (None, "", project_id):
                raise ValueError("The selected reference belongs to another project.")
            return {"asset_id": asset.id, "kind": asset.kind, "name": asset.name,
                    "description": asset.notes or "", "file_key": file_key}
    raise ValueError("A selected reference is missing from the Library.")


def selected_references(shot):
    result = []
    for ref in sorted(shot.refs, key=lambda value: value.picture_index):
        asset = load_asset(KINDS.get(ref.role.value, "props"), ref.asset_id)
        if asset is None:
            raise ValueError(f"Reference P{ref.picture_index} is missing from the Library.")
        entry = read_asset_description(shot.project_id, ref.asset_id, ref.file_key)
        hit = resolve_asset_image(asset, role=ref.role.value, file_key=ref.file_key)
        if not hit or (ref.file_key and hit[2] != ref.file_key):
            raise ValueError(f"The exact image for P{ref.picture_index} is unavailable. Relink it.")
        filename, data, key = hit
        result.append({**entry, "role": ref.role.value, "file_key": key, "filename": filename,
            "picture_index": ref.picture_index, "binding_notes": ref.notes,
            "content_sha256": hashlib.sha256(data).hexdigest(), "evidence_mode": "saved_library_description"})
    return result


def continuation_intent(shot):
    """Text-writing inputs, independent of the selected generated variation."""
    config = shot.video_context
    if not config or config.mode == "off":
        return {"mode": "off"}
    source_id = config.source_shot_id
    if config.mode == "previous_shot" and not source_id:
        project = load_project(shot.project_id)
        if project and shot.id in project.shot_ids:
            index = project.shot_ids.index(shot.id)
            source_id = project.shot_ids[index - 1] if index else None
    source = load_shot(shot.project_id, source_id) if source_id else None
    return {"mode": config.mode, "source_shot_id": source_id,
        "source_description": {"title": source.title, "description": source.script_beat} if source else None,
        "context_frames": config.context_frames, "audio_context_frames": config.audio_context_frames,
        "carry_audio": config.carry_audio}


def continuation_intent_signature(shot):
    return digest(continuation_intent(shot))


def snapshot(shot):
    project = load_project(shot.project_id)
    references = selected_references(shot)
    voices = []
    if shot.voice_refs:
        from ..api.projects import _validate_voice_refs
        voices = [{**ref.model_dump(mode="json"), "name": asset.name, "description": asset.notes or "",
            "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for ref, asset, path in _validate_voice_refs(shot)]
    continuation = None
    if shot.video_context and shot.video_context.mode != "off":
        status = video_context_status(shot)
        source_id = shot.video_context.source_shot_id
        if not source_id and shot.video_context.mode == "previous_shot" and project:
            index = project.shot_ids.index(shot.id)
            source_id = project.shot_ids[index - 1] if index else None
        source = load_shot(shot.project_id, source_id) if source_id else None
        continuation = {**shot.video_context.model_dump(mode="json"), "ready": status.get("ready"),
            "source_state": status.get("state"), "context_frames": status.get("context_frames"),
            "carry_audio": status.get("carry_audio"),
            "source_description": ({"title": source.title, "description": source.script_beat} if source else None),
            "evidence_mode": "authored_text_not_observed_video_ending"}
    facts = {"shot": shot.model_dump(mode="json", exclude={"meta", "h3_job_id", "status", "blocked_reasons", "prompt_sections", "video_context"}),
             "references": references, "continuation": continuation_intent(shot),
             "reference_note": shot.meta.get("workspace_reference_note", ""),
             "authoring_mode": project.authoring_mode if project else None}
    if voices:
        facts["voices"] = voices
    return {"version": digest(facts), "shot_id": shot.id, "description": shot.script_beat,
            "references": references, "voices": voices, "continuation": continuation}


def reference_certificate(project, shot):
    return {"version": "shot-workspace-v1", "signature": digest([selected_references(shot), shot.prompt_sections.model_dump(mode="json")])}


def legacy_prompt_evidence_matches(shot):
    """Accept only a verifiable old certificate with unchanged selected evidence."""
    certificate = shot.meta.get("prompt_reference_contract") or {}
    review = shot.meta.get("material_review") or {}
    records = review.get("references", [])
    if certificate.get("version") != 2 or shot.meta.get("material_review_pending"):
        return False
    if not review.get("facts_signature") or not review.get("intent_signature"):
        return False
    expected = digest([review["facts_signature"], review["intent_signature"], records, shot.prompt_sections.model_dump(mode="json")])
    if certificate.get("signature") != expected:
        return False
    current = selected_references(shot)
    if len(records) != len(current):
        return False
    return all(all(old.get(key) == new.get(key) for key in ("asset_id", "role", "file_key", "picture_index", "content_sha256"))
        and old.get("description") == new["description"]
        and old.get("asset_name") == new["name"]
        and old.get("reference_notes") == new["binding_notes"]
        for old, new in zip(records, current))
