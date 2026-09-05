"""Multimodal shot analysis and deterministic reference-frame prompt compilation."""

from __future__ import annotations

import base64
import io
import json
import re
from typing import Any, Protocol

from PIL import Image
from pydantic import BaseModel, Field, field_validator

from ...core.projects.layouts import LayoutBrief, RefRole
from ...core.projects.models import Shot
from .skill_loader import with_director_skill


class VisualCharacter(BaseModel):
    reference_image: str = Field(min_length=1)
    frame_position: str = Field(min_length=1)
    body_angle: str = Field(min_length=1)
    head_direction: str = Field(min_length=1)
    pose: str = Field(min_length=1)
    interaction: str = Field(min_length=1)
    identity_lock: list[str] = Field(min_length=1)
    wardrobe_lock: list[str] = Field(min_length=1)

    @field_validator("identity_lock", "wardrobe_lock")
    @classmethod
    def _non_empty_items(cls, value: list[str]) -> list[str]:
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if not cleaned:
            raise ValueError("lock must contain at least one non-empty item")
        return cleaned


class VisualBrief(BaseModel):
    shot_type: str = Field(min_length=1)
    camera: str = Field(min_length=1)
    scene_lock: list[str] = Field(min_length=1)
    characters: list[VisualCharacter] = Field(default_factory=list)
    forbidden: list[str] = Field(min_length=1)
    generation_prompt: str | None = None

    @field_validator("scene_lock", "forbidden")
    @classmethod
    def _non_empty_items(cls, value: list[str]) -> list[str]:
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if not cleaned:
            raise ValueError("list must contain at least one non-empty item")
        return cleaned


class VisualDirectionResult(BaseModel):
    brief: VisualBrief
    compiled_prompt: str
    selected_refs: list[dict[str, str]]
    vision_input_captions: list[str]
    vision_images_b64: list[str] = Field(exclude=True)
    review_image_used: bool = False
    review_feedback: str = ""


class GenerationPromptRepair(BaseModel):
    generation_prompt: str = Field(min_length=1)


class _VisionClient(Protocol):
    async def chat(self, model: str, prompt: str, **kwargs: Any) -> str: ...


def _json_object(text: str) -> str:
    raw = re.sub(r"<think>.*?</think>", "", text or "", flags=re.I | re.S).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.I | re.S)
    if fenced:
        return fenced.group(1)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("visual brief response does not contain a JSON object")
    return raw[start : end + 1]


def parse_visual_brief(text: str) -> VisualBrief:
    try:
        payload = json.loads(_json_object(text))
    except json.JSONDecodeError as exc:
        raise ValueError(f"visual brief is not valid JSON: {exc.msg}") from exc
    try:
        return VisualBrief.model_validate(payload)
    except Exception as exc:
        raise ValueError(f"invalid visual brief: {exc}") from exc


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _missing_generation_refs(prompt: str | None, captions: list[str]) -> list[str]:
    attached_refs = [
        match.group(1)
        for caption in captions
        if (match := re.match(r"^\s*(Image\d+)\b", caption, flags=re.I))
    ]
    mentioned_refs = {
        match.group(0).lower()
        for match in re.finditer(r"\bImage\d+\b", prompt or "", flags=re.I)
    }
    return [ref for ref in attached_refs if ref.lower() not in mentioned_refs]


def compile_visual_prompt(
    shot: Shot,
    brief: VisualBrief,
    *,
    captions: list[str],
) -> str:
    character_refs: set[str] = set()
    for caption in captions:
        match = re.match(
            r"^\s*(Image\d+)\s*(?:=\s*)?(?:ACTOR|CHARACTER)\b",
            caption,
            flags=re.I,
        )
        if match:
            character_refs.add(match.group(1).lower())

    for character in brief.characters:
        reference = character.reference_image.strip().lower()
        if reference not in character_refs:
            raise ValueError(
                f"visual brief character {character.reference_image!r} must point "
                "to an attached CHARACTER reference"
            )

    described_refs = [
        character.reference_image.strip().lower() for character in brief.characters
    ]
    if len(described_refs) != len(set(described_refs)) or set(described_refs) != character_refs:
        raise ValueError(
            "visual brief must describe every attached CHARACTER reference exactly once"
        )

    generation_prompt = (brief.generation_prompt or "").strip()
    if generation_prompt and not _missing_generation_refs(generation_prompt, captions):
        return generation_prompt

    character_sections: list[str] = []
    for index, character in enumerate(brief.characters, start=1):
        character_sections.append(
            f"CHARACTER {index}\n"
            f"Reference: {character.reference_image}\n"
            f"Position: {character.frame_position}\n"
            f"Body angle: {character.body_angle}\n"
            f"Head direction: {character.head_direction}\n"
            f"Pose: {character.pose}\n"
            f"Interaction: {character.interaction}\n"
            f"Identity lock:\n{_bullets(character.identity_lock)}\n"
            f"Wardrobe lock:\n{_bullets(character.wardrobe_lock)}"
        )

    return (
        "Create one photoreal cinematic production still. One continuous frame; "
        "no collage, panels, character sheet, or repeated person.\n\n"
        "SHOT\n"
        f"Title: {shot.title}\n"
        f"Action: {shot.script_beat}\n"
        f"Framing: {brief.shot_type}\n"
        f"Camera: {brief.camera}\n\n"
        "REFERENCE MAP\n"
        f"{_bullets(captions)}\n\n"
        "SCENE LOCK\n"
        f"{_bullets(brief.scene_lock)}\n\n"
        f"{'\n\n'.join(character_sections)}\n\n"
        "FORBIDDEN CHANGES\n"
        f"{_bullets(brief.forbidden)}"
    )


def _vision_jpeg(data: bytes, *, max_side: int = 1024) -> str:
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = source.convert("RGB")
            if max(image.size) > max_side:
                image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=92, optimize=True)
    except Exception as exc:
        raise ValueError(f"failed to encode vision image: {exc}") from exc
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _analysis_prompt(
    shot: Shot,
    captions: list[str],
    *,
    layout_brief: LayoutBrief | None = None,
    review_image_used: bool = False,
    feedback: str = "",
) -> str:
    character_count = sum(
        bool(re.match(r"^\s*Image\d+\s*(?:=\s*)?(?:ACTOR|CHARACTER)\b", caption, re.I))
        for caption in captions
    )
    review_block = ""
    layout_block = ""
    if layout_brief is not None:
        layout_block = (
            "\nLAYOUT PURPOSE:\n"
            f"Purpose: {layout_brief.purpose}\n"
            f"State: {layout_brief.state_description or '(derive from shot action)'}\n"
            f"Time hint: {layout_brief.time_hint or '(none)'}\n"
        )
    if review_image_used:
        review_block = (
            "\n\nREPAIR REVIEW:\n"
            "The final attachment is PreviousResult, a rejected output for visual diagnosis "
            "only. It is not a generation reference and must never be named ImageN in "
            "generation_prompt. Preserve what already works and rewrite the prompt to fix "
            "the human feedback.\n"
            f"HUMAN FEEDBACK: {feedback.strip()}"
        )
    elif feedback.strip() and layout_brief is not None:
        first = layout_brief.source_refs[0] if layout_brief.source_refs else None
        if first is not None and first.role == RefRole.layout_ref_frame:
            review_block = (
                "\n\nCONTINUITY REDRAW:\n"
                "Image1 is the extracted continuity frame. Preserve blocking, wardrobe "
                "state, geography, and lighting. Apply only the user's stated changes. "
                "Do not recreate a contact sheet, introduce a new camera angle without "
                "instruction, or retain motion blur and compression artifacts as "
                "identity features.\n"
                f"HUMAN FEEDBACK: {feedback.strip()}"
            )
    characters_schema = "[]"
    if character_count:
        characters_schema = (
            '[{"reference_image":"Image2","frame_position":"...",'
            '"body_angle":"...","head_direction":"...","pose":"...",'
            '"interaction":"none or explicit object interaction",'
            '"identity_lock":["..."],"wardrobe_lock":["..."]}]'
        )
    return (
        "You are the visual director for one image-generation shot. Inspect every attached "
        "image before answering. Image captions are ordered exactly like the attachments.\n\n"
        f"SHOT TITLE: {shot.title}\n"
        f"SHOT ACTION: {shot.script_beat}\n"
        f"PLANNED SHOT TYPE: {shot.shot_type or '(not specified)'}\n"
        f"PLANNED CAMERA ANGLE: {shot.camera_angle or '(not specified)'}\n"
        f"PLANNED CAMERA MOTION: {shot.camera_motion or '(not specified)'}\n"
        f"PLANNED COMPOSITION: {shot.composition or '(not specified)'}\n"
        f"DURATION: {shot.duration_s:.2f} seconds\n"
        f"{layout_block}\n"
        "ATTACHMENTS:\n"
        f"{_bullets(captions)}"
        f"{review_block}\n\n"
        "Treat this planned camera brief as authoritative when choosing the representative "
        "composition; the generated image is a static composition reference, not a promise "
        "that it is the first frame. Return only one JSON object. Preserve visible scene architecture and extract visible "
        "actor identity, hairstyle, body build, and every garment including footwear. "
        f"The characters array MUST contain exactly {character_count} distinct entries: "
        "one for every attached ACTOR/CHARACTER image, each referenced exactly once. Design "
        "a shot-specific frame position, body angle, head direction, pose, and interaction. "
        "Do not invent information from a screenplay. Write generation_prompt as the final "
        "English positive prompt that Qwen Image Edit should receive verbatim: lead with the "
        "shot action and composition, assign every attached ImageN exactly one visual job, "
        "then state only the identity, wardrobe, environment, prop, and spatial details needed "
        "for this shot. Keep it concise and coherent. Do not put headings, JSON, a generic "
        "quality preamble, or negative instructions inside generation_prompt. Use exactly this schema:\n"
        '{"shot_type":"...","camera":"...","scene_lock":["..."],'
        f'"characters":{characters_schema},'
        '"forbidden":["..."],"generation_prompt":"..."}'
    )


def _generation_prompt_repair_prompt(
    brief: VisualBrief,
    captions: list[str],
    missing_refs: list[str],
) -> str:
    return (
        "Repair only the reference labels in an image-generation prompt. Preserve the "
        "existing composition, action, identity, wardrobe, environment, and wording as much "
        "as possible. Insert every missing token next to the subject or object it controls. "
        "Do not add headings, negative instructions, or a quality preamble. Return only JSON "
        'with schema {"generation_prompt":"..."}.\n\n'
        f"MISSING TOKENS: {', '.join(missing_refs)}\n"
        f"REFERENCE MAP:\n{_bullets(captions)}\n\n"
        f"VISUAL BRIEF:\n{brief.model_dump_json()}\n\n"
        f"ORIGINAL GENERATION PROMPT:\n{brief.generation_prompt or ''}"
    )


async def analyze_ref_frame(
    shot: Shot,
    *,
    images: dict[str, tuple[str, bytes]],
    captions: list[str],
    layout_brief: LayoutBrief | None = None,
    review_image: tuple[str, bytes] | None = None,
    feedback: str = "",
    model: str,
    ollama: _VisionClient,
) -> VisualDirectionResult:
    ordered = list(images.items())
    if not ordered:
        raise ValueError("visual direction requires at least one image")
    if len(ordered) != len(captions):
        raise ValueError("vision image and caption counts must match")

    vision_images = [_vision_jpeg(value[1][1]) for value in ordered]
    ollama_images = list(vision_images)
    if review_image is not None:
        ollama_images.append(_vision_jpeg(review_image[1]))
    response = await ollama.chat(
        model,
        with_director_skill(
            _analysis_prompt(
                shot,
                captions,
                layout_brief=layout_brief,
                review_image_used=review_image is not None,
                feedback=feedback,
            ),
            guides=("reference-strategy", "reference-frame-generation"),
        ),
        images=ollama_images,
        require_vision=True,
        keep_alive="10m",
        options={"temperature": 0.1},
        format=VisualBrief.model_json_schema(),
    )
    brief = parse_visual_brief(response)
    missing_refs = _missing_generation_refs(brief.generation_prompt, captions)
    if brief.generation_prompt and missing_refs:
        try:
            repaired_response = await ollama.chat(
                model,
                with_director_skill(
                    _generation_prompt_repair_prompt(brief, captions, missing_refs),
                    guides=("reference-strategy", "reference-frame-generation"),
                ),
                keep_alive="10m",
                options={"temperature": 0.0},
                format=GenerationPromptRepair.model_json_schema(),
            )
            repaired_payload = json.loads(_json_object(repaired_response))
            repaired = GenerationPromptRepair.model_validate(repaired_payload)
            brief = brief.model_copy(
                update={"generation_prompt": repaired.generation_prompt.strip()}
            )
        except Exception:
            pass
    compiled = compile_visual_prompt(shot, brief, captions=captions)
    selected = [
        {"image": f"Image{index}", "file_key": key, "caption": captions[index - 1]}
        for index, (key, _value) in enumerate(ordered, start=1)
    ]
    return VisualDirectionResult(
        brief=brief,
        compiled_prompt=compiled,
        selected_refs=selected,
        vision_input_captions=list(captions),
        vision_images_b64=vision_images,
        review_image_used=review_image is not None,
        review_feedback=feedback.strip(),
    )
