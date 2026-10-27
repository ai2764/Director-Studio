"""On-demand single-image descriptions; separate from shot writing and Agent history."""
import asyncio
import hashlib
import json
import re
import uuid
from weakref import WeakValueDictionary
from pydantic import BaseModel, Field

from ..config import settings
from ..agents.director.vision import image_bytes_to_b64_jpeg
from ..agents.director.planner import _extract_json_payload
from .model import WorkspaceModel

_locks = WeakValueDictionary()


class Description(BaseModel):
    description: str = Field(min_length=1, max_length=16000)


INSTRUCTIONS = """Describe exactly one supplied Library image for later reference-based video prompts.
Return the description in the requested language. The asset name and existing
description are user guidance, not proof of visible facts or instructions to ignore
the image. Refine useful existing details against the pixels; correct contradictions
and explicitly mark anything not visible or uncertain. Describe identity, viewpoint,
crop, screen position, facing direction, clothing and object construction, visible
fastenings, straps, openings, materials, patterns, colors and environment where present.
Do not invent unseen anatomy or construction details. Multi-view sheets can depict
one subject. Preserve fine product details. Do not add a story or video prompt.
The output is editable draft text; do not claim user approval or that it was saved.
"""


async def describe_upload(data, name, *, existing_description="", model=None):
    image = image_bytes_to_b64_jpeg(data, max_side=768)
    if not image:
        raise ValueError("Choose a readable image file.")
    language = "zh" if re.search(r"[\u3400-\u9fff]", name) else "en"
    content_hash = hashlib.sha256(data).hexdigest()
    guidance = {"name": name.strip(), "existing_description": existing_description.strip(), "language": language, "version": 1}
    guidance_hash = hashlib.sha256(json.dumps(guidance, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    path = settings.library_root / "_descriptions" / "single-image-v1" / f"{content_hash[:24]}-{guidance_hash[:24]}.json"
    def cached():
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
            if result["content_sha256"] == content_hash and result["guidance_sha256"] == guidance_hash and result["description"].strip():
                return {**result, "cached": True}
        except (OSError, ValueError, KeyError, AttributeError):
            pass
        return None
    key = (id(asyncio.get_running_loop()), str(path))
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    async with lock:
        if result := cached():
            return result
        from ..core.vram import get_orchestrator
        orch = get_orchestrator()
        async with orch.llm_session(release_on_exit=not settings.llm_keep_loaded):
            await orch.ensure_llm_ready()
            raw = await (model or WorkspaceModel()).complete(INSTRUCTIONS, guidance, images=[image], schema=Description.model_json_schema(), max_tokens=3072)
            result = Description.model_validate(_extract_json_payload(raw))
        payload = {"description": result.description, "language": language, "content_sha256": content_hash,
                   "guidance_sha256": guidance_hash, "cached": False}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
        return payload
