"""Pure Director chat intent, capability, and selector decisions."""

from __future__ import annotations

import re
from typing import Any

from ...core.projects.models import Shot


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def layout_activation_mode(message: str) -> str:
    """Map explicit additive language to Layout append; default to replacement."""
    text = normalize_text(message)
    additive = bool(
        re.search(
            r"\b(?:add|keep)\b[^\n]{0,45}\b(?:another|additional|extra|second)\b"
            r"[^\n]{0,35}\b(?:layout|reference frame|composition)\b",
            text,
            flags=re.I,
        )
        or re.search(
            r"\b(?:another|additional|extra|second)\b[^\n]{0,35}"
            r"\b(?:layout|reference frame|composition)\b",
            text,
            flags=re.I,
        )
        or re.search(
            r"(?:再加|再来|补充|额外增加|保留[^。！？\n]{0,20}(?:再加|再来))"
            r"[^。！？\n]{0,30}(?:layout|布局|参考帧|构图)",
            text,
            flags=re.I,
        )
    )
    return "append" if additive else "replace"


def explicit_gpt_image_intent(message: str) -> bool:
    """Return whether this turn authorizes GPT/ChatGPT image generation."""
    text = normalize_text(message)
    provider = bool(
        re.search(r"\b(?:chatgpt|gpt(?:-[a-z0-9.]+)?)\b", text, flags=re.I)
        or re.search(r"\bimage\s*2\b", text, flags=re.I)
    )
    if not provider:
        return False
    generation = bool(
        re.search(
            r"\b(?:generate|create|render|make)\b[^\n]{0,50}"
            r"(?:image|layout|reference frame|frame)",
            text,
            flags=re.I,
        )
        or re.search(
            r"\buse\b[^\n]{0,30}\b(?:chatgpt|gpt|image\s*2)\b"
            r"[^\n]{0,50}\b(?:generate|create|render|image|layout)\b",
            text,
            flags=re.I,
        )
        or re.search(
            r"(?:生图|出图|生成[^。！？\n]{0,30}(?:图|layout|参考帧|构图))",
            text,
            flags=re.I,
        )
    )
    return generation


def actor_design_intent(message: str) -> bool:
    text = normalize_text(message)
    follows_existing_design = bool(
        re.search(
            r"(?:按|按照|根据|依照)\s*(?:现有|已有|当前|这个|该)?\s*"
            r"(?:人物|角色|演员)(?:的)?(?:设定|设计|定妆|造型)",
            text,
            flags=re.I,
        )
        or re.search(
            r"\b(?:follow|using|according to)\b[^\n]{0,24}"
            r"\b(?:existing\s+)?(?:character|actor)\s+"
            r"(?:design|reference|setting)\b",
            text,
            flags=re.I,
        )
    )
    if follows_existing_design:
        return False
    actor_words = bool(
        re.search(
            r"(?:人物|角色|演员|character|actor).{0,12}(?:设定|设计|定妆|造型|design|concept)",
            text,
            flags=re.I,
        )
        or re.search(
            r"(?:设定|设计|定妆|造型|design|concept).{0,12}(?:人物|角色|演员|character|actor)",
            text,
            flags=re.I,
        )
    )
    generation_words = bool(
        re.search(r"(?:生成|生图|画|做|create|generate|make)", text, flags=re.I)
    )
    return actor_words and generation_words


def actor_acceptance_intent(message: str) -> bool:
    return bool(
        re.search(
            r"(?:这张可以|这张行|就用这张|保存这张|接受这张|approve|accept|save this)",
            normalize_text(message),
            flags=re.I,
        )
    )


_GPT_COLLAGE_PHRASES = (
    "collage",
    "split screen",
    "contact sheet",
    "turnaround",
    "多宫格",
    "拼贴",
    "分屏",
)


def validate_gpt_generation_prompt(prompt: str, source_count: int) -> None:
    """Validate ImageN coverage without rewriting the Agent's creative prompt."""
    if source_count < 0:
        raise ValueError("source_count cannot be negative")
    found = {
        int(match)
        for match in re.findall(r"Image\s*([0-9]+)", prompt or "", flags=re.I)
    }
    expected = set(range(1, source_count + 1))
    missing = sorted(expected - found)
    if missing:
        labels = ", ".join(f"Image{index}" for index in missing)
        raise ValueError(f"generation_prompt must assign a visual job to {labels}")
    extra = sorted(found - expected)
    if extra:
        labels = ", ".join(f"Image{index}" for index in extra)
        raise ValueError(f"generation_prompt mentions unattached {labels}")
    screened = (prompt or "").lower()
    english_forbidden = "|".join(
        re.escape(phrase) for phrase in _GPT_COLLAGE_PHRASES if phrase.isascii()
    )
    screened = re.sub(
        rf"\b(?:no|without)\s+(?:an?\s+)?(?:{english_forbidden})"
        rf"(?:\s*,\s*(?:(?:no|without)\s+)?(?:an?\s+)?(?:{english_forbidden}))*"
        rf"(?:\s*,?\s*(?:and|or)\s+(?:(?:no|without)\s+)?"
        rf"(?:an?\s+)?(?:{english_forbidden}))?\b",
        "",
        screened,
    )
    for phrase in _GPT_COLLAGE_PHRASES:
        escaped = re.escape(phrase)
        screened = re.sub(
            rf"\b(?:no|without|not)\s+(?:an?\s+)?{escaped}\b", "", screened
        )
        screened = re.sub(
            rf"\b(?:do\s+not|don't)\s+(?:produce|create|return|make|output)\s+"
            rf"(?:an?\s+)?{escaped}\b",
            "",
            screened,
        )
        screened = re.sub(rf"(?:不要|不是|非)\s*{escaped}", "", screened)
    forbidden = None
    for phrase in _GPT_COLLAGE_PHRASES:
        escaped = re.escape(phrase)
        if phrase.isascii():
            requested = bool(
                re.search(
                    rf"\b(?:return|produce|create|generate|make|output|render|show)\s+"
                    rf"(?:(?:one|the|an?)\s+)?{escaped}\b",
                    screened,
                )
                or re.search(
                    rf"\b(?:format|compose|arrange|present)\b[^.\n]{{0,40}}"
                    rf"\b(?:as|in)\s+(?:an?\s+)?{escaped}\b",
                    screened,
                )
            )
        else:
            requested = bool(
                re.search(
                    rf"(?:生成|输出|返回|制作|做成)[^。！？\n]{{0,12}}{escaped}",
                    screened,
                )
            )
        if requested:
            forbidden = phrase
            break
    if forbidden:
        raise ValueError(
            f"generation_prompt must request one final image, not a {forbidden}"
        )


def looks_like_script(text: str) -> bool:
    value = text or ""
    if len(value) < 40:
        return False
    markers = (
        "fade in",
        "fade out",
        "ext.",
        "int.",
        "外景",
        "内景",
        "\n\n",
        "title:",
    )
    lowered = value.lower()
    hits = sum(1 for marker in markers if marker in lowered)
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if len(lines) >= 6 and hits >= 1:
        return True
    return hits >= 2 or (len(value) > 200 and hits >= 1)


def is_script_query(raw: str, body: str) -> bool:
    """Return whether the user is asking about rather than providing a script."""
    cleaned_body = (body or "").strip()
    cleaned_raw = (raw or "").strip()
    if not cleaned_body:
        return True
    if re.fullmatch(
        r"(啥|什么|什麼|啥呀|啥啊|什么啊|什么呀|呢|吗|麼|么|"
        r"什么内容|啥内容|哪[个段]?|怎么样|如何|怎样|"
        r"what|huh|\?|？)+",
        cleaned_body,
        flags=re.I,
    ):
        return True
    if re.search(r"[?？]\s*$", cleaned_raw) and len(cleaned_body) < 40:
        return True
    if (
        re.search(r"(啥|什么|什麼|吗|呢)\s*[?？]?\s*$", cleaned_body)
        and len(cleaned_body) < 24
    ):
        return True
    return False


def shot_ref(text: str, shots: list[Shot]) -> Shot | None:
    """Resolve 'shot 2', '第2镜', title substring, or short id."""
    if not shots:
        return None
    normalized = normalize_text(text)
    match = re.search(r"(?:shot|镜|#)\s*(\d+)", normalized)
    if not match:
        match = re.search(r"第\s*(\d+)\s*镜", normalized)
    if match:
        index = int(match.group(1)) - 1
        if 0 <= index < len(shots):
            return shots[index]
    for candidate in shots:
        if candidate.id.lower() in normalized or candidate.id[-6:].lower() in normalized:
            return candidate
    for candidate in shots:
        if candidate.title and normalize_text(candidate.title) in normalized:
            return candidate
    return None


def resolve_shot(
    shots: list[Shot],
    *,
    shot_id: str | None = None,
    shot_index: int | None = None,
    title: str | None = None,
) -> Shot | None:
    if shot_id:
        for candidate in shots:
            if (
                candidate.id == shot_id
                or candidate.id.endswith(shot_id)
                or shot_id in candidate.id
            ):
                return candidate
    if shot_index is not None:
        index = int(shot_index) - 1
        if 0 <= index < len(shots):
            return shots[index]
    if title:
        normalized_title = normalize_text(title)
        for candidate in shots:
            if candidate.title and normalized_title in normalize_text(candidate.title):
                return candidate
    return None


def detect_intent(
    message: str,
    shots: list[Shot],
    *,
    script_locked: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Resolve exact UI shortcuts; natural language remains Agent territory."""
    raw = (message or "").strip()
    normalized = normalize_text(raw)

    if not normalized:
        return "help", {}
    if normalized in {"help", "帮助", "?", "？", "你能做什么", "怎么用"}:
        return "help", {}
    if normalized in {"status", "状态", "进度"}:
        return "status", {}
    if normalized in {"拆镜", "分镜", "plan", "storyboard"}:
        return "plan", {}
    if normalized in {
        "全部首帧",
        "所有首帧",
        "生成全部首帧",
        "全部参考帧",
        "所有参考帧",
        "生成全部参考帧",
        "生成全部",
        "全部生成",
        "出参考帧",
        "生成参考帧",
        "reference frame all",
    }:
        return "ref_frame_all", {}
    if normalized in {"批准全部", "通过全部", "approve all"}:
        return "approve_all", {}
    write_prompt_match = re.fullmatch(
        r"(?:write|rewrite) (?:the )?h3 prompt for shot 0*(\d+)",
        normalized,
    )
    if write_prompt_match:
        shot_index = int(write_prompt_match.group(1)) - 1
        if 0 <= shot_index < len(shots):
            shot = shots[shot_index]
            if bool((shot.meta or {}).get("material_review_pending")):
                return "llm", {}
            return "write_prompt", {"shot_id": shot.id}
        return "write_prompt", {}
    ref_frame_match = re.fullmatch(
        r"(?:generate|regenerate|create|render|make) (?:the )?"
        r"(?:layout|reference frame|composition reference) for shot 0*(\d+)[.!]?",
        normalized,
    )
    if ref_frame_match:
        shot_index = int(ref_frame_match.group(1)) - 1
        if 0 <= shot_index < len(shots):
            return "ref_frame", {"shot_id": shots[shot_index].id}
        return "ref_frame", {}
    if not script_locked and not shots and looks_like_script(raw):
        return "set_script", {"script_text": raw}
    return "llm", {}
