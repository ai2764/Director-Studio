from __future__ import annotations

import json

import pytest

from app.agents.director.segment_import import parse_segment_text


class FakePlanProvider:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    async def complete_bounded(self, system: str, user: str, *, max_tokens: int, guides=(), schema=None) -> str:
        self.calls.append((system, user))
        return self.reply


@pytest.mark.asyncio
async def test_imports_whisper_sentence_rows_without_words_or_model_call() -> None:
    provider = FakePlanProvider("should not be used")
    source = json.dumps({"duration": 325.12, "segments": [
        {"start": 4.54, "end": 13.08, "text": "Yodelie", "words": [
            {"start": 4.54, "end": 5.1, "word": "Yodelie"},
        ]},
    ]})

    preview = await parse_segment_text(source, provider=provider)

    assert [(row.start_s, row.end_s, row.text) for row in preview.rows] == [(4.54, 13.08, "Yodelie")]
    assert provider.calls == []


@pytest.mark.asyncio
async def test_free_text_uses_configured_model_and_leaves_unknown_time_unresolved() -> None:
    provider = FakePlanProvider(json.dumps({"segments": [
        {"start_s": 0, "end_s": 3.5, "text": "First line"},
        {"start_s": None, "end_s": None, "text": "Later line"},
    ], "unresolved": ["Later line has no time"]}))

    preview = await parse_segment_text("0-3.5 First line\nLater line", provider=provider)

    assert [row.text for row in preview.rows] == ["First line", "Later line"]
    assert preview.rows[1].start_s is None
    assert preview.unresolved == [
        "Later line has no time", "Segment 2 needs a start and end time",
    ]
    assert "0-3.5 First line" in provider.calls[0][1]


@pytest.mark.asyncio
async def test_failed_parse_does_not_invent_segments() -> None:
    provider = FakePlanProvider("not JSON")
    with pytest.raises(ValueError, match="valid segment JSON"):
        await parse_segment_text("chorus around one minute", provider=provider)


@pytest.mark.asyncio
async def test_every_untimed_row_is_reported() -> None:
    preview = await parse_segment_text(json.dumps({"segments": [
        {"text": "First"}, {"text": "Second"},
    ]}))
    assert preview.unresolved == [
        "Segment 1 needs a start and end time",
        "Segment 2 needs a start and end time",
    ]
