from __future__ import annotations

import json

import pytest

from app.agents.director.chat import (
    DIRECTOR_TOOL_SCHEMAS,
    _IMAGE_TOOLS,
    _project_context_blob,
    _run_tools,
    handle_chat,
)
from app.agents.director.context_io import save_agent_context
from app.agents.director.service import _script_hash
from app.core.media.clip_generations import ClipGenerationAmbiguous
from app.core.projects.layouts import ClipTailFrameOrigin, LayoutReference
from app.core.projects.models import AgentContext, Shot, ShotStatus
from app.core.projects.store import (
    create_project,
    load_shot,
    save_project,
    save_shot,
)
from app.core.schemas import JobStatus


def _schema(name: str) -> dict:
    return next(
        item for item in DIRECTOR_TOOL_SCHEMAS if item["function"]["name"] == name
    )


def test_extract_clip_tail_frame_schema_exposes_exact_selectors():
    tool = _schema("extract_clip_tail_frame")
    parameters = tool["function"]["parameters"]
    props = parameters["properties"]

    assert parameters["required"] == ["source_shot_id", "target_shot_id"]
    assert parameters["additionalProperties"] is False
    assert set(props) == {
        "source_shot_id",
        "target_shot_id",
        "source_version",
        "source_job_id",
        "output_kind",
    }
    assert "all" not in props
    assert "shot_index" not in props
    assert props["source_shot_id"]["type"] == "string"
    assert props["target_shot_id"]["type"] == "string"
    assert props["source_version"]["type"] == "string"
    assert props["source_job_id"]["type"] == "string"
    assert props["output_kind"]["enum"] == ["enhanced", "raw"]


def test_extract_clip_tail_frame_is_an_image_tool():
    assert "extract_clip_tail_frame" in _IMAGE_TOOLS


def test_project_context_serializes_layout_origin(tmp_projects_dir, monkeypatch):
    project = create_project("Origin context", "Kai leaves the archive.")
    origin = ClipTailFrameOrigin(
        source_shot_id="sht_shot2",
        source_job_id="job_h3_v2",
        source_generation=2,
        output_kind="enhanced",
        output_key="video",
        source_filename="take_enhanced.mp4",
        source_duration_s=6.0,
        extracted_timestamp_s=5.96,
    )
    shot = Shot(
        id="sht_shot3",
        project_id=project.id,
        scene_id="sc01",
        title="Archive door",
        script_beat="Kai leaves the archive.",
        duration_s=5.0,
        status=ShotStatus.needs_review,
        layout_refs=[
            LayoutReference(
                id="lref_plain",
                asset_id="lay_plain",
                purpose="primary composition",
            ),
            LayoutReference(
                id="lref_tail",
                asset_id="lay_tail",
                purpose="cross-shot visual continuity from sht_shot2",
                origin=origin,
            ),
        ],
    )
    monkeypatch.setattr("app.agents.director.service._inventory", lambda _pid: {})
    monkeypatch.setattr(
        "app.agents.director.context_io.load_agent_context",
        lambda _pid: None,
    )

    context = json.loads(_project_context_blob(project, [shot]))
    refs = context["shots"][0]["layout_refs"]

    assert refs[0]["origin"] is None
    assert refs[1]["origin"] == {
        "kind": "clip_tail_frame",
        "source_shot_id": "sht_shot2",
        "source_job_id": "job_h3_v2",
        "source_generation": 2,
        "output_kind": "enhanced",
        "output_key": "video",
        "source_filename": "take_enhanced.mp4",
        "source_duration_s": 6.0,
        "extracted_timestamp_s": 5.96,
    }


@pytest.mark.asyncio
async def test_extract_clip_tail_frame_calls_service_with_resolved_shot_ids(
    tmp_projects_dir,
    monkeypatch,
):
    project = create_project("Tail extract", "INT. HALL")
    source = Shot(
        id="sht_source",
        project_id=project.id,
        scene_id="sc01",
        title="shot2",
        script_beat="Kai reaches the door.",
        duration_s=6.0,
        status=ShotStatus.succeeded,
    )
    target = Shot(
        id="sht_target",
        project_id=project.id,
        scene_id="sc02",
        title="shot3",
        script_beat="Kai opens the door.",
        duration_s=5.0,
        status=ShotStatus.draft,
    )
    save_shot(source)
    save_shot(target)
    save_project(project.model_copy(update={"shot_ids": [source.id, target.id]}))

    captured: list[dict] = []
    payload = {
        "source_shot_id": source.id,
        "target_shot_id": target.id,
        "source_job_id": "job_h3_ab12",
        "source_version": 2,
        "output_kind": "enhanced",
        "layout_ref_id": "lref_tail_new",
        "layout_asset_id": "lay_tail_new",
        "image_url": "/api/files/library/layouts/lay_tail_new/layout.png",
    }

    def _fake_extract(**kwargs):
        captured.append(kwargs)
        updated = load_shot(project.id, target.id)
        assert updated is not None
        save_shot(
            updated.model_copy(
                update={
                    "layout_asset_id": "lay_tail_new",
                    "layout_refs": [
                        LayoutReference(
                            id="lref_tail_new",
                            asset_id="lay_tail_new",
                            review_status="pending_review",
                            selected_for_h3=False,
                        )
                    ],
                }
            )
        )
        return payload

    monkeypatch.setattr(
        "app.core.media.tail_frame.extract_clip_tail_frame",
        _fake_extract,
    )

    actions: list[str] = []
    payloads: list[dict] = []
    notes, touched = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "extract_clip_tail_frame",
                "args": {
                    "source_shot_id": source.id,
                    "target_shot_id": target.id,
                    "source_version": "v2",
                    "source_job_id": None,
                    "output_kind": "enhanced",
                },
            }
        ],
        svc=object(),
        actions=actions,
        result_payloads=payloads,
    )

    assert captured == [
        {
            "project_id": project.id,
            "source_shot_id": source.id,
            "target_shot_id": target.id,
            "source_version": "v2",
            "source_job_id": None,
            "output_kind": "enhanced",
        }
    ]
    assert payloads[0]["source_shot_id"] == source.id
    assert payloads[0]["target_shot_id"] == target.id
    assert payloads[0]["source_version"] == 2
    assert payloads[0]["source_job_id"] == "job_h3_ab12"
    assert payloads[0]["output_kind"] == "enhanced"
    assert payloads[0]["layout_ref_id"] == "lref_tail_new"
    assert payloads[0]["image_url"].endswith("/lay_tail_new/layout.png")
    assert actions == [f"extract_clip_tail_frame:{target.id}"]
    assert touched == {target.id}
    joined = "\n".join(notes)
    assert "shot2" in joined
    assert source.id in joined
    assert "v2" in joined
    assert "job_h3_ab12" in joined
    assert "enhanced" in joined
    assert "shot3" in joined
    assert target.id in joined
    assert "lref_tail_new" in joined
    assert "pending" in joined.lower()
    saved = load_shot(project.id, target.id)
    assert saved is not None
    assert saved.layout_refs[0].id == "lref_tail_new"
    assert saved.layout_refs[0].selected_for_h3 is False


@pytest.mark.asyncio
async def test_extract_clip_tail_frame_ambiguity_clarifies_without_mutation(
    tmp_projects_dir,
    monkeypatch,
):
    project = create_project("Ambiguous latest", "INT. HALL")
    source = Shot(
        id="sht_source",
        project_id=project.id,
        scene_id="sc01",
        title="shot2",
        script_beat="Kai reaches the door.",
        duration_s=6.0,
        status=ShotStatus.succeeded,
    )
    target = Shot(
        id="sht_target",
        project_id=project.id,
        scene_id="sc02",
        title="shot3",
        script_beat="Kai opens the door.",
        duration_s=5.0,
        status=ShotStatus.draft,
        layout_refs=[
            LayoutReference(id="lref_existing", asset_id="lay_existing")
        ],
    )
    save_shot(source)
    save_shot(target)
    save_project(project.model_copy(update={"shot_ids": [source.id, target.id]}))

    def _ambiguous(**kwargs):
        raise ClipGenerationAmbiguous(
            "latest is ambiguous: a newer failed generation exists",
            latest_succeeded_job_id="job_ok",
            blocking_job_id="job_fail",
            blocking_status=JobStatus.failed,
        )

    monkeypatch.setattr(
        "app.core.media.tail_frame.extract_clip_tail_frame",
        _ambiguous,
    )

    actions: list[str] = []
    payloads: list[dict] = []
    notes, touched = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "extract_clip_tail_frame",
                "args": {
                    "source_shot_id": source.id,
                    "target_shot_id": target.id,
                    "source_version": "latest",
                },
            }
        ],
        svc=object(),
        actions=actions,
        result_payloads=payloads,
    )

    saved = load_shot(project.id, target.id)
    assert saved is not None
    assert [layout.id for layout in saved.layout_refs] == ["lref_existing"]
    assert touched == set()
    assert actions == []
    assert payloads[0]["ok"] is False
    assert payloads[0]["needs_clarification"] is True
    assert payloads[0]["latest_succeeded_job_id"] == "job_ok"
    assert payloads[0]["blocking_job_id"] == "job_fail"
    assert payloads[0]["blocking_status"] == "failed"
    joined = "\n".join(notes)
    assert "clarif" in joined.lower()
    assert "failed:" not in joined


@pytest.mark.asyncio
async def test_extract_clip_tail_frame_success_attaches_layout_chat_image(
    tmp_projects_dir,
    monkeypatch,
):
    project = create_project("Chat image", "INT. HALL")
    source = Shot(
        id="sht_source",
        project_id=project.id,
        scene_id="sc01",
        title="shot2",
        script_beat="Kai reaches the door.",
        duration_s=6.0,
        status=ShotStatus.succeeded,
    )
    target = Shot(
        id="sht_target",
        project_id=project.id,
        scene_id="sc02",
        title="shot3",
        script_beat="Kai opens the door.",
        duration_s=5.0,
        status=ShotStatus.draft,
    )
    save_shot(source)
    save_shot(target)
    save_project(project.model_copy(update={"shot_ids": [source.id, target.id]}))
    save_agent_context(
        project.id,
        AgentContext(
            project_id=project.id,
            script_hash=_script_hash(project.script_text),
            last_phase="awaiting_prompt",
            shot_summaries=[{"id": source.id}, {"id": target.id}],
        ),
    )

    image_url = "/api/files/library/layouts/lay_chat_tail/layout.png"

    def _fake_extract(**kwargs):
        updated = load_shot(project.id, target.id)
        assert updated is not None
        save_shot(
            updated.model_copy(
                update={
                    "layout_refs": [
                        *list(updated.layout_refs),
                        LayoutReference(
                            id="lref_chat_tail",
                            asset_id="lay_chat_tail",
                            review_status="pending_review",
                            selected_for_h3=False,
                        ),
                    ]
                }
            )
        )
        return {
            "source_shot_id": source.id,
            "target_shot_id": target.id,
            "source_job_id": "job_chat",
            "source_version": 1,
            "output_kind": "raw",
            "layout_ref_id": "lref_chat_tail",
            "layout_asset_id": "lay_chat_tail",
            "image_url": image_url,
        }

    monkeypatch.setattr(
        "app.core.media.tail_frame.extract_clip_tail_frame",
        _fake_extract,
    )

    calls: list[dict] = []

    async def chat_fn(system: str, user: str, **kwargs):
        calls.append({"system": system, "user": user, **kwargs})
        if len(calls) == 1:
            return {
                "content": "",
                "thinking": "Resolve shot2 and shot3, then extract the tail frame.",
                "tool_calls": [
                    {
                        "name": "extract_clip_tail_frame",
                        "arguments": {
                            "source_shot_id": source.id,
                            "target_shot_id": target.id,
                            "source_version": "latest",
                            "output_kind": "raw",
                        },
                    }
                ],
            }
        return {
            "content": "The tail frame is ready for review.",
            "thinking": "",
            "tool_calls": [],
        }

    result = await handle_chat(
        project_id=project.id,
        message="抽最新的 shot2 的尾帧，用于生成 shot3。",
        svc=object(),
        chat_fn=chat_fn,
    )

    offered = {tool["function"]["name"] for tool in calls[0]["tools"]}
    assert "extract_clip_tail_frame" in offered
    tool_messages = calls[1]["messages"]
    assert any(
        message.get("role") == "tool"
        and message.get("tool_name") == "extract_clip_tail_frame"
        and "job_chat" in message.get("content", "")
        and '"output_kind": "raw"' in message.get("content", "")
        and '"source_version": 1' in message.get("content", "")
        for message in tool_messages
    )
    assert any(image.url == image_url for image in result.images)
    assert any(image.shot_id == target.id for image in result.images)
    extracted = next(image for image in result.images if image.url == image_url)
    assert "shot2" in extracted.caption
    assert "v1" in extracted.caption
    assert "raw" in extracted.caption
    assert "shot3" in extracted.caption


@pytest.mark.asyncio
async def test_extract_clip_tail_frame_chat_image_is_new_asset_not_primary(
    tmp_projects_dir,
    monkeypatch,
):
    project = create_project("Existing layout", "INT. HALL")
    source = Shot(
        id="sht_source",
        project_id=project.id,
        scene_id="sc01",
        title="shot2",
        script_beat="Kai reaches the door.",
        duration_s=6.0,
        status=ShotStatus.succeeded,
    )
    target = Shot(
        id="sht_target",
        project_id=project.id,
        scene_id="sc02",
        title="shot3",
        script_beat="Kai opens the door.",
        duration_s=5.0,
        status=ShotStatus.needs_review,
        layout_asset_id="lay_primary",
        layout_review_status="approved",
        layout_refs=[
            LayoutReference(
                id="lref_primary",
                asset_id="lay_primary",
                purpose="primary composition",
                review_status="usable",
                selected_for_h3=True,
            )
        ],
    )
    save_shot(source)
    save_shot(target)
    save_project(project.model_copy(update={"shot_ids": [source.id, target.id]}))
    save_agent_context(
        project.id,
        AgentContext(
            project_id=project.id,
            script_hash=_script_hash(project.script_text),
            last_phase="awaiting_prompt",
            shot_summaries=[{"id": source.id}, {"id": target.id}],
        ),
    )

    extracted_url = "/api/files/library/layouts/lay_extracted/layout.png"
    primary_url = "/api/files/library/layouts/lay_primary/layout.png"

    def _fake_extract(**kwargs):
        updated = load_shot(project.id, target.id)
        assert updated is not None
        assert updated.layout_asset_id == "lay_primary"
        save_shot(
            updated.model_copy(
                update={
                    "layout_refs": [
                        *list(updated.layout_refs),
                        LayoutReference(
                            id="lref_extracted",
                            asset_id="lay_extracted",
                            review_status="pending_review",
                            selected_for_h3=False,
                        ),
                    ]
                }
            )
        )
        saved = load_shot(project.id, target.id)
        assert saved is not None
        assert saved.layout_asset_id == "lay_primary"
        return {
            "source_shot_id": source.id,
            "target_shot_id": target.id,
            "source_job_id": "job_extracted",
            "source_version": 2,
            "output_kind": "enhanced",
            "layout_ref_id": "lref_extracted",
            "layout_asset_id": "lay_extracted",
            "image_url": extracted_url,
        }

    monkeypatch.setattr(
        "app.core.media.tail_frame.extract_clip_tail_frame",
        _fake_extract,
    )

    calls: list[dict] = []

    async def chat_fn(system: str, user: str, **kwargs):
        calls.append({"system": system, "user": user, **kwargs})
        if len(calls) == 1:
            return {
                "content": "",
                "thinking": "Extract the tail frame onto the shot that already has a Layout.",
                "tool_calls": [
                    {
                        "name": "extract_clip_tail_frame",
                        "arguments": {
                            "source_shot_id": source.id,
                            "target_shot_id": target.id,
                            "source_version": "v2",
                        },
                    }
                ],
            }
        return {
            "content": "The extracted tail frame is ready for review.",
            "thinking": "",
            "tool_calls": [],
        }

    result = await handle_chat(
        project_id=project.id,
        message="用 shot2 的 v2 尾帧接 shot3。",
        svc=object(),
        chat_fn=chat_fn,
    )

    urls = [image.url for image in result.images]
    assert extracted_url in urls
    assert primary_url not in urls
    extracted = next(image for image in result.images if image.url == extracted_url)
    assert extracted.shot_id == target.id
    assert "shot2" in extracted.caption
    assert "v2" in extracted.caption
    assert "enhanced" in extracted.caption
    assert "shot3" in extracted.caption
    saved = load_shot(project.id, target.id)
    assert saved is not None
    assert saved.layout_asset_id == "lay_primary"
