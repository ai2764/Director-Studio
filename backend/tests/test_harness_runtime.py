from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from jsonschema import Draft202012Validator

from app.config import Settings, settings
from app.core.projects.models import LayoutReference, Shot
from app.core.projects.store import (
    create_project,
    load_project,
    load_shot,
    save_project,
    save_shot,
)


def test_runtime_defaults_and_loopback_validation():
    assert Settings(_env_file=None).director_agent_runtime == "legacy"
    for url in ("http://example.com:8791", "http://127.0.0.1.evil:8791", "http://user@127.0.0.1:8791", "http://127.0.0.1:8791/path"):
        with pytest.raises(ValueError):
            Settings(_env_file=None, harness_base_url=url)


def test_harness_never_reports_uncreated_layout_job(tmp_projects_dir):
    from app.agents.director.harness_runtime import BackendTurn
    from app.core.jobs.store import create_job

    project = create_project("missing layout job", "An empty hallway.")
    turn = BackendTurn(project.id, "需要换layout", None, None)
    result = turn.finish({
        "reply": "Shot 6 的新 Layout 已提交生成（job_000000000001，lay_000000000001）。",
    })
    assert "已提交生成" not in result.reply
    assert "job_000000000001" not in result.reply
    assert "不存在" in result.reply

    another = BackendTurn(project.id, "清理地面的纸片", None, None)
    result = another.finish({
        "reply": "Shot 6 的 Layout 已重新生成（job_000000000002，lay_000000000002）。",
    })
    assert "已重新生成" not in result.reply
    assert "job_000000000002" not in result.reply

    third = BackendTurn(project.id, "走廊", None, None)
    result = third.finish({"reply": "请看 job_000000000003 的布局。"})
    assert "job_000000000003" not in result.reply

    real = create_job(pipeline_id="qwen21_layout", asset_kind="layouts",
                      name="test", project_id=project.id)
    without_receipt = BackendTurn(project.id, "走廊", None, None)
    assert real.id not in without_receipt.finish({"reply": f"{real.id} 可以看了。"}).reply
    with_receipt = BackendTurn(project.id, "走廊", None, None)
    with_receipt.tool_exposed_job_ids.add(real.id)
    assert real.id in with_receipt.finish({"reply": f"{real.id} 可以看了。"}).reply


@pytest.mark.asyncio
async def test_already_running_h3_receipt_is_valid_for_the_final_reply(tmp_projects_dir, monkeypatch):
    from types import SimpleNamespace
    from app.agents.director.harness_runtime import BackendTurn
    from app.core.jobs.store import create_job
    project = create_project("Existing H3", "A road.")
    shot = Shot(id="sht_existing", project_id=project.id, scene_id="road",
                title="Road", script_beat="A road.", duration_s=4)
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="Road",
                     project_id=project.id)
    run = SimpleNamespace(current_index=0, steps=[SimpleNamespace(shot_id=shot.id)],
                          current_job_id=job.id)
    monkeypatch.setattr("app.core.managed_runs.store.active_run_for_project", lambda _: run)
    turn = BackendTurn(project.id, "Start this Shot", None, None)
    monkeypatch.setattr(turn, "context", lambda: {"tools": [{"function": {
        "name": "start_h3_video", "parameters": {"type": "object", "properties": {
            "shot_id": {"type": "string"}}, "required": ["shot_id"]},
    }}]})
    result = await turn.dispatch("tool", {"name": "start_h3_video",
        "arguments": {"shot_id": shot.id}, "call_id": "existing-job"})
    assert result["already_started"]
    assert job.id in turn.finish({"reply": f"Existing job: {job.id}."}).reply


def test_agent_history_does_not_reseed_phantom_job_claims(tmp_projects_dir):
    from app.core.projects.chat_history import agent_history, append_chat_message, load_chat_history

    project = create_project("phantom job history", "An empty hallway.")
    append_chat_message(project.id, role="user", content="重新设计空走廊")
    append_chat_message(project.id, role="assistant", content="已提交 job_000000000003。")
    append_chat_message(project.id, role="assistant", content="Earlier assistant reply cited a Job with no matching project record. No task can be inferred from that reply.")
    visible = load_chat_history(project.id)
    assert "job_000000000003" in visible[-2].content
    seeded = agent_history(visible, project_id=project.id)
    assert seeded == [{"role": "user", "content": "重新设计空走廊"}]


def test_harness_reseeds_only_recent_chat_messages(tmp_projects_dir):
    from app.agents.director.harness_runtime import BackendTurn, MAX_HARNESS_SEED_MESSAGES

    project = create_project("recent chat", "A hallway.")
    history = [{"role": "user", "content": str(i)} for i in range(40)]
    turn = BackendTurn(project.id, "continue", None, None, history=history)
    assert len(turn.seed_history) == MAX_HARNESS_SEED_MESSAGES
    assert turn.seed_history[0]["content"] == "16"
    assert turn.seed_history[-1]["content"] == "39"


@pytest.mark.asyncio
async def test_harness_exposes_new_layout_job_from_tool_receipt(tmp_projects_dir, monkeypatch):
    from app.agents.director import chat
    from app.agents.director.harness_runtime import BackendTurn
    from app.core.jobs.store import create_job

    project = create_project("layout receipt", "An empty hallway.")
    shot = Shot(id="sht_receipt", project_id=project.id, scene_id="sc01",
                title="Hallway", script_beat="Empty floor", duration_s=4)
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    made = []

    async def queue_layout(**kwargs):
        job = create_job(pipeline_id="qwen21_layout", asset_kind="layouts",
                         name="layout", project_id=project.id)
        made.append(job.id)
        current = load_shot(project.id, shot.id)
        save_shot(current.model_copy(update={
            "layout_refs": [LayoutReference(id="lref_receipt", job_id=job.id)],
            "ref_frame_job_id": job.id,
        }))
        kwargs["actions"].append(f"ref_frame:{shot.id}")
        return ["Queued Layout"], {shot.id}

    monkeypatch.setattr(chat, "_run_tools", queue_layout)
    turn = BackendTurn(project.id, "Generate a Layout", None, None)
    result = await turn.dispatch("tool", {
        "name": "queue_ref_frame", "arguments": {"shot_id": shot.id}, "call_id": "queue-1",
    })
    assert result["ok"] is True
    assert result["created_job_ids"] == made
    assert made[0] in turn.finish({"reply": f"Queued {made[0]}."}).reply


@pytest.mark.parametrize("limit", [0, -1, 65])
def test_tool_budget_rejects_unbounded_configuration(limit):
    with pytest.raises(ValueError):
        Settings(_env_file=None, harness_max_tool_calls=limit)


def test_tool_budget_can_be_configured_independently(monkeypatch):
    monkeypatch.setenv("DS_HARNESS_MAX_TOOL_CALLS", "24")
    configured = Settings(_env_file=None, harness_max_steps=2)
    assert configured.harness_max_tool_calls == 24
    assert configured.harness_max_steps == 2


@pytest.mark.asyncio
async def test_harness_reserves_model_output_tokens_from_pressure_window(
    tmp_projects_dir,
    monkeypatch,
):
    from app.agents.director import harness_runtime

    project = create_project("prompt pressure budget", "A cat waits.")
    captured = {}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, body, dispatch, on_progress):
            captured.update(body)
            return {"reply": "done", "thinking": ""}

    monkeypatch.setattr(harness_runtime, "HarnessClient", Client)
    monkeypatch.setattr(harness_runtime.settings, "director_num_predict", 4_000)

    await harness_runtime.handle_harness_chat(
        project_id=project.id,
        message="hello",
        svc=None,
        chat_fn=None,
        context_capacity=32_000,
    )

    assert captured["context_window"] == 28_000


def test_harness_input_budget_uses_provider_capacity_and_image_reserve(monkeypatch):
    from app.agents.director import harness_runtime

    monkeypatch.setattr(harness_runtime.settings, "director_num_predict", 4096)

    assert harness_runtime.harness_input_budget(131072, image_count=2) == 122880


def test_material_review_turn_only_offers_prompt_rewrite_for_exact_shot(
    tmp_projects_dir,
):
    from app.agents.director.tool_schema import director_tool_schemas

    project = create_project("material review boundary", "A cat waits.")
    target = Shot(
        id="sht_review_target",
        project_id=project.id,
        scene_id="sc01",
        title="Target",
        script_beat="A cat waits.",
        duration_s=5,
    )
    save_shot(target)
    save_project(project.model_copy(update={"shot_ids": [target.id]}))
    message = (
        f'Shot 01 references changed for “Target” ({target.id}). '
        "Review the current materials and rewrite its H3 prompt. "
        "If a critical reference is missing or conflicting, ask one concrete question instead."
    )

    tools = director_tool_schemas(project, current_message=message)

    assert [tool["function"]["name"] for tool in tools] == ["write_prompt"]
    validator = Draft202012Validator(tools[0]["function"]["parameters"])
    assert not list(validator.iter_errors({"shot_id": target.id}))
    assert list(validator.iter_errors({"shot_id": "sht_wrong"}))
    assert list(
        validator.iter_errors({"shot_id": target.id, "shot_index": 1})
    )


def test_material_review_note_can_offer_layout_generation_for_only_changed_shot(
    tmp_projects_dir,
):
    from app.agents.director.tool_schema import director_tool_schemas

    project = create_project("material review layout intent", "A cat waits.")
    target = Shot(
        id="sht_review_target",
        project_id=project.id,
        scene_id="sc01",
        title="Target",
        script_beat="A cat waits.",
        duration_s=5,
    )
    save_shot(target)
    save_project(project.model_copy(update={"shot_ids": [target.id]}))
    message = (
        f'Shot 01 references changed for “Target” ({target.id}). '
        "Review every current Picture reference and decide the next step. "
        'Saved reference delta: {"added":[],"removed":[],"reordered":[]}. '
        "User note: Generate a new Layout using these references."
    )

    tools = director_tool_schemas(project, current_message=message)

    assert [tool["function"]["name"] for tool in tools] == [
        "write_prompt",
        "queue_ref_frame",
    ]
    queue_schema = tools[1]["function"]["parameters"]
    validator = Draft202012Validator(queue_schema)
    assert not list(
        validator.iter_errors(
            {
                "shot_id": target.id,
                "purpose": "New entrance composition",
                "state_description": "The actor enters from frame left.",
                "source_refs": [],
            }
        )
    )
    assert list(
        validator.iter_errors(
            {
                "shot_id": "sht_wrong",
                "purpose": "Wrong shot",
                "state_description": "Wrong target.",
                "source_refs": [],
            }
        )
    )


def test_material_review_note_can_offer_tail_frame_for_only_changed_shot(
    tmp_projects_dir,
):
    from app.agents.director.tool_schema import director_tool_schemas

    project = create_project("material review tail intent", "A cat waits.")
    message = (
        "Shot 02 references changed for “Target” (sht_review_target). "
        "Review every current Picture reference and decide the next step. "
        "User note: 抽 Shot 1 的尾帧给这个镜头。"
    )

    tools = director_tool_schemas(project, current_message=message)

    assert [tool["function"]["name"] for tool in tools] == [
        "write_prompt",
        "extract_clip_tail_frame",
    ]
    extract_schema = tools[1]["function"]["parameters"]
    assert extract_schema["properties"]["target_shot_id"]["const"] == "sht_review_target"


def test_layout_generation_tool_is_visible_even_without_explicit_request(
    tmp_projects_dir,
):
    from app.agents.director.tool_schema import director_tool_schemas

    project = create_project("layout consent boundary", "")
    discussion = (
        'I want to discuss adding another reference frame for shot "Target" '
        "(sht_target). First explain whether another Layout is useful. "
        "Do not queue generation yet; start the discussion with me."
    )
    explicit = "Generate a Layout for Shot 1 (sht_target)."

    discussion_names = {
        tool["function"]["name"]
        for tool in director_tool_schemas(project, current_message=discussion)
    }
    explicit_names = {
        tool["function"]["name"]
        for tool in director_tool_schemas(project, current_message=explicit)
    }

    assert "queue_ref_frame" in discussion_names
    assert "revise_ref_frame" in discussion_names
    assert "queue_ref_frame" in explicit_names
    assert "revise_ref_frame" in explicit_names


def test_tail_frame_request_keeps_full_layout_tool_catalog(
    tmp_projects_dir,
):
    from app.agents.director.tool_schema import director_tool_schemas

    project = create_project("tail frame boundary", "")
    names = {
        tool["function"]["name"]
        for tool in director_tool_schemas(
            project,
            current_message="抽最新的 shot2 的尾帧，用于生成 shot3。",
        )
    }

    assert "extract_clip_tail_frame" in names
    assert "queue_ref_frame" in names


@pytest.mark.asyncio
async def test_material_review_execution_rejects_a_different_shot(
    tmp_projects_dir,
):
    from app.agents.director.chat import _run_tools

    project = create_project("material review execution", "A cat waits.")
    target = Shot(
        id="sht_review_target",
        project_id=project.id,
        scene_id="sc01",
        title="Target",
        script_beat="A cat waits.",
        duration_s=5,
    )
    other = target.model_copy(update={"id": "sht_other", "title": "Other"})
    for shot in (target, other):
        save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [target.id, other.id]}))
    message = (
        f'Shot 01 references changed for “Target” ({target.id}). '
        "Review the current materials and rewrite its H3 prompt. "
        "If a critical reference is missing or conflicting, ask one concrete question instead."
    )

    class Service:
        calls: list[str] = []

        async def write_prompts_after_layout(self, shot_id, *, revision_request=""):
            self.calls.append(shot_id)
            return load_shot(project.id, shot_id)

    service = Service()
    actions: list[str] = []
    payloads: list[dict] = []
    notes, _ = await _run_tools(
        project_id=project.id,
        tools=[{"name": "write_prompt", "args": {"shot_id": other.id}}],
        svc=service,
        actions=actions,
        result_payloads=payloads,
        user_feedback=message,
    )

    assert service.calls == []
    assert actions == []
    assert payloads and payloads[0]["ok"] is False
    assert target.id in payloads[0]["error"]
    assert any(target.id in note for note in notes)


@pytest.mark.asyncio
async def test_material_review_layout_execution_rejects_a_different_shot(
    tmp_projects_dir,
):
    from app.agents.director.chat import _run_tools

    project = create_project("material review layout execution", "A cat waits.")
    target = Shot(
        id="sht_review_target",
        project_id=project.id,
        scene_id="sc01",
        title="Target",
        script_beat="A cat waits.",
        duration_s=5,
    )
    other = target.model_copy(update={"id": "sht_other", "title": "Other"})
    for shot in (target, other):
        save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [target.id, other.id]}))
    message = (
        f'Shot 01 references changed for “Target” ({target.id}). '
        "Review every current Picture reference and decide the next step. "
        "User note: Generate a Layout from the new references."
    )

    class Service:
        calls: list[str] = []

        async def queue_reference_frame(self, shot_id, *, brief, force=False, directing_request=""):
            self.calls.append(shot_id)
            return load_shot(project.id, shot_id)

    service = Service()
    actions: list[str] = []
    payloads: list[dict] = []
    notes, _ = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "queue_ref_frame",
                "args": {
                    "shot_id": other.id,
                    "purpose": "Wrong shot",
                    "state_description": "Wrong target.",
                    "source_refs": [],
                },
            }
        ],
        svc=service,
        actions=actions,
        result_payloads=payloads,
        user_feedback=message,
    )

    assert service.calls == []
    assert actions == []
    assert payloads and payloads[0]["ok"] is False
    assert target.id in payloads[0]["error"]
    assert any(target.id in note for note in notes)


@pytest.mark.asyncio
async def test_material_review_tail_execution_rejects_a_different_target(
    tmp_projects_dir,
    monkeypatch,
):
    from app.agents.director.chat import _run_tools
    from app.agents.director.tool_handlers import media

    project = create_project("material review tail execution", "A cat waits.")
    target = Shot(
        id="sht_review_target",
        project_id=project.id,
        scene_id="sc01",
        title="Target",
        script_beat="A cat waits.",
        duration_s=5,
    )
    source = target.model_copy(update={"id": "sht_source", "title": "Source"})
    other = target.model_copy(update={"id": "sht_other", "title": "Other"})
    for shot in (target, source, other):
        save_shot(shot)
    save_project(
        project.model_copy(
            update={"shot_ids": [source.id, target.id, other.id]}
        )
    )
    message = (
        f'Shot 02 references changed for “Target” ({target.id}). '
        "Review every current Picture reference and decide the next step. "
        "User note: 抽 Shot 1 的尾帧给这个镜头。"
    )
    extraction_calls: list[str] = []

    def unexpected_extract(**kwargs):
        extraction_calls.append(kwargs["target_shot_id"])
        raise AssertionError("wrong target must be rejected before extraction")

    monkeypatch.setattr(media.tail_frame, "extract_clip_tail_frame", unexpected_extract)
    payloads: list[dict] = []
    actions: list[str] = []
    notes, _ = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "extract_clip_tail_frame",
                "args": {
                    "source_shot_id": source.id,
                    "target_shot_id": other.id,
                },
            }
        ],
        svc=object(),
        actions=actions,
        result_payloads=payloads,
        user_feedback=message,
    )

    assert extraction_calls == []
    assert actions == []
    assert payloads and payloads[0]["ok"] is False
    assert target.id in payloads[0]["error"]
    assert any(target.id in note for note in notes)


@pytest.mark.asyncio
async def test_successful_local_layout_queue_returns_receipt_when_comfy_blocks_wrapup(
    tmp_projects_dir,
):
    from app.agents.director.harness_runtime import BackendTurn
    from app.core.vram.orchestrator import (
        GenerationActiveError,
        GenerationReservation,
    )

    project = create_project("local layout receipt", "")
    shot = Shot(
        id="sht_layout_target",
        project_id=project.id,
        scene_id="sc01",
        title="Target",
        script_beat="A cat waits.",
        duration_s=5,
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    queued = shot.model_copy(
        update={
            "layout_refs": [
                LayoutReference(
                    id="lref_queued",
                    job_id="job_queued",
                    purpose="Establish composition",
                    state_description="The cat waits by the window.",
                )
            ]
        }
    )

    class Service:
        async def queue_reference_frame(self, shot_id, *, brief=None, force=False, directing_request=""):
            assert shot_id == shot.id
            return queued

    reservation = GenerationReservation(
        job_id="job_queued",
        pipeline_id="ref_frame",
        kind="image",
        status="running",
        phase="generating",
        queued_at="2026-09-12T00:00:00+00:00",
    )

    async def blocked_wrapup(*args, **kwargs):
        raise GenerationActiveError([reservation])

    turn = BackendTurn(
        project.id,
        "Generate a Layout for Shot 1.",
        Service(),
        blocked_wrapup,
    )
    await turn.dispatch("context", {})
    tool_result = await turn.dispatch(
        "tool",
        {
            "name": "queue_ref_frame",
            "arguments": {
                "shot_id": shot.id,
                "purpose": "Establish composition",
                "state_description": "The cat waits by the window.",
                "source_refs": [],
            },
            "call_id": "queue-layout",
        },
    )

    assert tool_result["ok"] is True
    result = await turn.dispatch(
        "llm",
        {"messages": [{"role": "user", "content": "Generate it."}]},
    )
    assert result["tool_calls"] == []
    assert "Queued Layout lref_queued" in result["content"]


@pytest.mark.asyncio
async def test_invalid_calls_consume_tool_budget_even_after_fresh_inference(tmp_projects_dir, monkeypatch):
    from types import SimpleNamespace
    from app.agents.director import harness_runtime

    monkeypatch.setattr(harness_runtime, "settings", SimpleNamespace(harness_max_steps=12, harness_max_tool_calls=2))
    project = create_project("bounded repair", "original")

    async def inference(*args, **kwargs):
        return {"content": "", "tool_calls": []}

    turn = harness_runtime.BackendTurn(project.id, "Edit script", None, inference)
    await turn.dispatch("context", {})
    for index in range(2):
        invalid = await turn.dispatch("tool", {"name": "set_script", "arguments": {}, "call_id": f"bad-{index}"})
        assert invalid["ok"] is False
    await turn.dispatch("llm", {"messages": [{"role": "user", "content": "Try again"}]})
    rejected = await turn.dispatch("tool", {"name": "set_script", "arguments": {"script": "must not save"}, "call_id": "valid"})
    assert rejected["ok"] is False and "limit" in rejected["error"].lower()
    assert load_project(project.id).script_text == "original"
    assert turn.actions == []


@pytest.mark.asyncio
async def test_shot_status_exposes_clip_generations_and_can_be_read_again(
    tmp_projects_dir, monkeypatch
):
    from app.agents.director.harness_runtime import BackendTurn
    from app.core.jobs.store import create_job, save_job
    from app.core.schemas import JobStatus

    jobs = tmp_projects_dir.parent / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "jobs_dir", jobs)

    project = create_project("tail continuity", "A cat waits.")
    shot = Shot(
        id="sht_read_once", project_id=project.id, scene_id="sc01",
        title="Source", script_beat="A cat waits.", duration_s=5,
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    first_job = create_job(
        pipeline_id="h3_ref2va", asset_kind="productions", name="first",
        project_id=project.id, params={"shot_id": shot.id, "width": 768, "height": 1376},
    )
    first_job.created_at = "2026-09-21T10:00:00Z"
    first_job.status = JobStatus.succeeded
    save_job(first_job)
    second_job = create_job(
        pipeline_id="h3_ref2va", asset_kind="productions", name="second",
        project_id=project.id, params={"shot_id": shot.id, "width": 864, "height": 480},
    )
    second_job.created_at = "2026-09-21T11:00:00Z"
    second_job.status = JobStatus.failed
    save_job(second_job)
    turn = BackendTurn(project.id, "Read Shot 1", None, None)
    await turn.dispatch("context", {})

    first = await turn.dispatch("tool", {
        "name": "get_status", "arguments": {"shot_id": shot.id}, "call_id": "read-1",
    })
    repeated = await turn.dispatch("tool", {
        "name": "get_status", "arguments": {"shot_id": shot.id}, "call_id": "read-2",
    })

    assert first["shot"]["id"] == shot.id
    expected = [
        {"version": "v1", "job_id": first_job.id, "status": "succeeded", "width": 768, "height": 1376},
        {"version": "v2", "job_id": second_job.id, "status": "failed", "width": 864, "height": 480},
    ]
    assert first["h3_generations"] == expected
    assert repeated["ok"] is True
    assert repeated["shot"]["id"] == shot.id
    assert repeated["h3_generations"] == expected
    status_schema = next(
        tool["function"]["parameters"] for tool in turn.context()["tools"]
        if tool["function"]["name"] == "get_status"
    )
    assert not list(Draft202012Validator(status_schema).iter_errors({"shot_id": shot.id}))


@pytest.mark.asyncio
async def test_shot_status_omits_large_internal_material_review(tmp_projects_dir):
    from app.agents.director.harness_runtime import BackendTurn

    project = create_project("bounded status", "A wall rises from a flat floor.")
    shot = Shot(
        id="sht_bounded", project_id=project.id, scene_id="sc01",
        title="Empty hallway", script_beat="The wall rises.", duration_s=5,
        meta={"material_review": {"references": [{"sources": [{"text": "archive " * 16000}]}]},
              "material_review_pending": True, "material_changes": {"changed": ["layout"]}},
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    turn = BackendTurn(project.id, "Read the hallway shot", None, None)
    await turn.dispatch("context", {})

    result = await turn.dispatch("tool", {
        "name": "get_status", "arguments": {"shot_id": shot.id}, "call_id": "bounded-read",
    })

    assert result["ok"] is True
    assert result["shot"]["script_beat"] == "The wall rises."
    assert result["shot"]["meta"]["material_review_pending"] is True
    assert "material_review" not in result["shot"]["meta"]
    assert len(json.dumps(result, ensure_ascii=False)) < 12000


def test_harness_refreshes_layout_acceptance_after_extraction(tmp_projects_dir):
    from app.agents.director.harness_runtime import BackendTurn

    project = create_project("tail acceptance", "")
    shot = Shot(
        id="sht_target", project_id=project.id, scene_id="sc01",
        title="Target", script_beat="A cat waits.", duration_s=5,
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    turn = BackendTurn(project.id, "抽尾帧后直接 approve", None, None)

    before = {tool["function"]["name"] for tool in turn.context()["tools"]}
    assert "accept_ref_frame" not in before

    save_shot(shot.model_copy(update={
        "layout_refs": [LayoutReference(id="lref_real", asset_id="lay_real")],
    }))
    after = next(
        tool["function"] for tool in turn.context()["tools"]
        if tool["function"]["name"] == "accept_ref_frame"
    )
    validator = Draft202012Validator(after["parameters"])
    assert not list(validator.iter_errors({"shot_id": shot.id, "layout_ref_id": "lref_real"}))
    assert list(validator.iter_errors({"shot_id": shot.id, "layout_ref_id": "lref_invented"}))


@pytest.mark.asyncio
async def test_explicit_runtime_dispatch(monkeypatch):
    from app.agents.director import chat, harness_runtime

    async def legacy(**kwargs):
        return "legacy"

    async def harness(**kwargs):
        return "harness"

    monkeypatch.setattr(chat, "orchestrate_chat", legacy)
    monkeypatch.setattr(harness_runtime, "handle_harness_chat", harness)
    monkeypatch.setattr(settings, "director_agent_runtime", "legacy")
    assert await chat.handle_chat(project_id="p", message="hello", svc=None) == "legacy"
    monkeypatch.setattr(settings, "director_agent_runtime", "harness")
    assert await chat.handle_chat(project_id="p", message="hello", svc=None) == "harness"


@pytest.mark.asyncio
async def test_driver_dispatches_requests_and_never_accepts_domain_state():
    from app.agents.director.harness_client import HarnessClient

    received = []
    lines = [
        {"type": "request", "id": "r1", "method": "context", "params": {}},
        {"type": "result", "reply": "done", "thinking": "", "project": {"name": "forged"}},
    ]

    async def transport(req):
        received.append(req)
        if req.url.path.endswith("responses/r1"):
            assert json.loads(req.content) == {"ok": True, "data": {"state": "real"}}
            return httpx.Response(200, json={"ok": True})
        if req.method == "DELETE":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, text="\n".join(json.dumps(x) for x in lines))

    async def dispatch(method, params):
        assert method == "context"
        return {"state": "real"}

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        client = HarnessClient("http://127.0.0.1:8791", "secret", http=http)
        result = await client.run({"message": "hi"}, dispatch)
    assert result == {"reply": "done", "thinking": ""}
    assert all(r.headers["authorization"] == "Bearer secret" for r in received)


@pytest.mark.asyncio
async def test_driver_eof_after_tool_is_interrupted_and_not_replayed():
    from app.agents.director.harness_client import HarnessClient, HarnessError

    calls = []
    executed = []

    async def transport(req):
        calls.append(req.method)
        if "/responses/" in req.url.path or req.method == "DELETE":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, text=json.dumps({"type": "request", "id": "r", "method": "tool", "params": {}}))

    async def dispatch(method, params):
        executed.append(method)
        return {"ok": True}

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        with pytest.raises(HarnessError, match="interrupted"):
            await HarnessClient("http://127.0.0.1:8791", "secret", http=http).run({}, dispatch)
    assert executed == ["tool"]
    assert calls == ["POST", "POST", "DELETE"]


@pytest.mark.asyncio
async def test_cancel_closes_sidecar_without_retrying_dispatch():
    from app.agents.director.harness_client import HarnessClient

    cancelled = asyncio.Event()
    started = asyncio.Event()

    async def transport(req):
        if req.method == "DELETE":
            cancelled.set()
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, text=json.dumps({"type": "request", "id": "r", "method": "llm", "params": {}}))

    async def dispatch(method, params):
        started.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        task = asyncio.create_task(HarnessClient("http://127.0.0.1:8791", "secret", http=http).run({}, dispatch))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_sidecar_disconnect_cancels_active_inference():
    from app.agents.director.harness_client import HarnessClient, HarnessError

    started, disconnected, released = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield (json.dumps({"type": "request", "id": "req", "method": "llm", "params": {}}) + "\n").encode()
            await disconnected.wait()

    async def transport(req):
        return httpx.Response(204) if req.method == "DELETE" else httpx.Response(200, stream=Stream())

    async def dispatch(method, params):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        task = asyncio.create_task(HarnessClient("http://127.0.0.1:8791", "secret", http=http).run({}, dispatch))
        try:
            await asyncio.wait_for(started.wait(), 1)
            disconnected.set()
            with pytest.raises(HarnessError, match="interrupted"):
                await asyncio.wait_for(asyncio.shield(task), 1)
            assert released.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_backend_tools_validate_refresh_and_deduplicate(tmp_projects_dir):
    from app.agents.director.harness_runtime import BackendTurn

    project = create_project("Harness fixture", "")
    turn = BackendTurn(project.id, "Please help edit my screenplay", None, None)
    await turn.dispatch("context", {})
    invalid = await turn.dispatch("tool", {"name": "set_script", "arguments": {}, "call_id": "bad"})
    assert invalid["ok"] is False
    assert load_project(project.id).script_text == ""
    valid = {"name": "set_script", "arguments": {"script": "A cat opens a box."}, "call_id": "good"}
    assert (await turn.dispatch("tool", valid))["ok"] is True
    assert load_project(project.id).script_text == "A cat opens a box."
    assert (await turn.dispatch("tool", {**valid, "call_id": "again"}))["ok"] is True
    unchanged_replay = await turn.dispatch("tool", {**valid, "call_id": "third"})
    assert unchanged_replay["ok"] is False
    assert turn.actions == ["set_script", "set_script"]
    save_project(load_project(project.id).model_copy(update={"script_locked": True}))
    blocked = await turn.dispatch("tool", {"name": "set_script", "arguments": {"script": "wrong"}, "call_id": "blocked"})
    assert blocked["ok"] is False
    assert load_project(project.id).script_text == "A cat opens a box."


def test_stale_script_keeps_local_prompt_tools_available(
    tmp_projects_dir,
):
    from app.agents.director.harness_runtime import BackendTurn

    project = create_project("stale tool catalog", "A cat waits.")
    shot = Shot(
        id="sht_stale_catalog",
        project_id=project.id,
        scene_id="sc01",
        title="Old plan",
        script_beat="An older beat.",
        duration_s=5,
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    context = BackendTurn(project.id, "Write Shot 1's prompt", None, None).context()
    names = {tool["function"]["name"] for tool in context["tools"]}

    assert "save_storyboard" in names
    assert "write_prompt" in names
    assert "queue_ref_frame" in names
    # Staleness is reported, but does not mandate full-board replacement.
    assert json.loads(context["state"])["recommended_next_step"] == "review_existing_shots"


@pytest.mark.asyncio
async def test_failed_prompt_write_makes_remainder_of_turn_explain_only(
    tmp_projects_dir,
):
    from app.agents.director.harness_runtime import BackendTurn

    project = create_project("terminal prompt failure", "")
    shot = Shot(
        id="sht_prompt_failure",
        project_id=project.id,
        scene_id="sc01",
        title="Greeting",
        script_beat="A greeting.",
        duration_s=5,
        meta={"material_review_pending": True},
        dialogue=['MIA: "Hello."'],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    class Service:
        async def write_prompts_after_layout(self, shot_id, *, revision_request=""):
            raise ValueError("dialogue validation failed")

    turn = BackendTurn(project.id, "Write Shot 1's prompt", Service(), None)
    await turn.dispatch("context", {})
    result = await turn.dispatch(
        "tool",
        {
            "name": "write_prompt",
            "arguments": {"shot_id": shot.id},
            "call_id": "write-1",
        },
    )

    assert result["ok"] is False
    assert result["retryable"] is False
    refreshed = turn.context()
    assert refreshed["tools"] == []
    assert "explain" in refreshed["system"].lower()

    finished = turn.finish(
        {
            "reply": (
                "The prompt could not be saved.\n\n"
                "<tool_call><function=write_prompt>fake</function></tool_call>"
            ),
            "thinking": "",
        }
    )
    assert "<tool_call>" not in finished.reply
    assert "not executed" in finished.reply.lower()


@pytest.mark.asyncio
async def test_successful_prompt_write_is_idempotent_for_remainder_of_turn(
    tmp_projects_dir,
):
    from app.agents.director.harness_runtime import BackendTurn
    from app.agents.director.context_io import save_agent_context
    from app.agents.director.service import _script_hash
    from app.core.projects.models import AgentContext

    project = create_project("single prompt mutation", "A greeting.")
    shot = Shot(
        id="sht_prompt_once",
        project_id=project.id,
        scene_id="sc01",
        title="Greeting",
        script_beat="A greeting.",
        duration_s=5,
        meta={"material_review_pending": True},
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    save_agent_context(
        project.id,
        AgentContext(project_id=project.id, script_hash=_script_hash(project.script_text)),
    )

    class Service:
        calls = 0

        async def write_prompts_after_layout(self, shot_id, *, revision_request=""):
            self.calls += 1
            current = load_shot(project.id, shot_id)
            assert current is not None
            updated = current.model_copy(
                update={"meta": {**current.meta, "prompt_write_count": self.calls}}
            )
            save_shot(updated)
            return updated

    service = Service()
    turn = BackendTurn(project.id, "Write Shot 1's prompt", service, None)
    await turn.dispatch("context", {})
    first = await turn.dispatch("tool", {
        "name": "write_prompt", "arguments": {"shot_id": shot.id}, "call_id": "write-1",
    })
    second = await turn.dispatch("tool", {
        "name": "write_prompt", "arguments": {"shot_id": shot.id}, "call_id": "write-2",
    })

    assert first["ok"] is True, first
    assert second["ok"] is True
    assert second["already_saved"] is True
    assert "already saved" in " ".join(second["notes"]).lower()
    assert service.calls == 1


def test_missing_optional_layout_recommends_prompt_not_layout(tmp_projects_dir):
    from app.agents.director.chat_context import project_context_blob
    from app.agents.director.context_io import save_agent_context
    from app.agents.director.service import _script_hash
    from app.core.projects.models import AgentContext

    project = create_project("optional layout", "A cat waits.")
    shot = Shot(
        id="sht_without_layout",
        project_id=project.id,
        scene_id="sc01",
        title="Direct prompt",
        script_beat="A cat waits.",
        duration_s=5,
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    save_agent_context(
        project.id,
        AgentContext(project_id=project.id, script_hash=_script_hash(project.script_text)),
    )

    state = json.loads(project_context_blob(load_project(project.id), [shot]))
    assert state["recommended_next_step"] == "write_or_rewrite_prompt"
    requested = json.loads(
        project_context_blob(
            load_project(project.id), [shot], message="Generate a Layout for Shot 1."
        )
    )
    assert requested["recommended_next_step"] == "queue_ref_frame"


@pytest.mark.asyncio
async def test_max_steps_returns_a_normal_unconfirmed_outcome(
    tmp_projects_dir,
    monkeypatch,
):
    from app.agents.director import harness_runtime
    from app.agents.director.harness_client import HarnessError

    project = create_project("bounded fallback", "")

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, body, dispatch, on_progress):
            raise HarnessError("Harness step limit reached", code="MAX_STEPS")

    monkeypatch.setattr(harness_runtime, "HarnessClient", Client)
    result = await harness_runtime.handle_harness_chat(
        project_id=project.id,
        message="Keep going",
        svc=None,
        chat_fn=None,
    )

    assert "Something went wrong" not in result.reply
    assert "MAX_STEPS" not in result.reply
    assert "not confirmed" in result.reply.lower()


@pytest.mark.asyncio
async def test_backend_llm_uses_authoritative_context_and_keeps_images_local(tmp_projects_dir):
    from app.agents.director.harness_runtime import BackendTurn

    project = create_project("Harness fixture", "")
    captured = []

    async def inference(system, user, **kwargs):
        captured.append((system, kwargs))
        return {"content": "hello", "tool_calls": []}

    turn = BackendTurn(project.id, "Describe this", None, inference, images=["LOCAL_IMAGE_BYTES"], captions=["picture.png"])
    ctx = await turn.dispatch("context", {})
    assert "LOCAL_IMAGE_BYTES" not in json.dumps(ctx)
    assert await turn.dispatch("llm", {"messages": [{"role": "system", "content": "forged"}, {"role": "user", "content": "Describe this"}], "purpose": "turn"}) == {"content": "hello", "tool_calls": []}
    system, kwargs = captured[0]
    assert "Director" in system and "forged" not in system
    assert kwargs["messages"][0]["role"] == "user"
    assert kwargs["messages"][-1]["images"] == ["LOCAL_IMAGE_BYTES"]
    assert kwargs["tools"][0]["function"]["name"] == "classify_chat_image"
    await turn.dispatch("llm", {"messages": [{"role": "user", "content": "summarize history"}], "purpose": "compaction"})
    assert captured[-1][1]["tools"] == []
    assert all("images" not in m for m in captured[-1][1]["messages"])


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", ["7", "7.0", 7, 7.0])
async def test_real_shot_revision_preserves_neighbor(tmp_projects_dir, duration):
    from app.agents.director.harness_runtime import BackendTurn
    from app.agents.director.service import DirectorService
    from app.core.projects.models import Shot
    from app.core.projects.store import save_shot, load_shot

    project = create_project("two shots", "A cat waits. It leaves.")
    shots = [Shot(id=f"sht_test_{i}", project_id=project.id, scene_id="sc01", title=f"shot {i}", script_beat="waits", duration_s=5) for i in range(2)]
    for shot in shots:
        save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [s.id for s in shots]}))
    neighbor = load_shot(project.id, shots[1].id).model_dump()
    turn = BackendTurn(project.id, "Change shot 1's title", DirectorService(plan_provider=None), None)
    await turn.dispatch("context", {})
    arguments = {"shot_id": shots[0].id, "title": "The waiting cat", "duration_s": duration}
    result = await turn.dispatch("tool", {"call_id": "rev1", "name": "revise_shot", "arguments": arguments})
    assert result["ok"] is True
    assert load_shot(project.id, shots[0].id).title == "The waiting cat"
    assert load_shot(project.id, shots[0].id).duration_s == 7.0
    assert load_shot(project.id, shots[0].id).script_beat == "waits"
    assert arguments["duration_s"] == duration  # Input records are not mutated.
    assert load_shot(project.id, shots[1].id).model_dump() == neighbor
    finished = turn.finish({"reply": "saved", "thinking": ""})
    assert finished.shots[0].title == "The waiting cat"
    for index, equivalent in enumerate(["7", "7.0", 7, 7.0]):
        replay = await turn.dispatch("tool", {"call_id": f"replay-{index}", "name": "revise_shot", "arguments": {**arguments, "duration_s": equivalent}})
        if index == 0:
            assert replay["ok"] is True
        else:
            assert replay["ok"] is False and "Repeated" in replay["error"]
    assert turn.actions == ["revise_shot", "revise_shot"]


@pytest.mark.asyncio
@pytest.mark.parametrize("update", [
    {"duration_s": "七秒左右"}, {"duration_s": "0"}, {"duration_s": "-1"},
    {"duration_s": "NaN"}, {"duration_s": "Infinity"}, {"duration_s": True},
    {"duration_s": None}, {"duration_s": "7", "refs": []},
])
async def test_shot_revision_normalization_preserves_rejections(tmp_projects_dir, update):
    from app.agents.director.harness_runtime import BackendTurn
    from app.agents.director.service import DirectorService
    from app.core.projects.models import Shot
    from app.core.projects.store import save_shot, load_shot

    project = create_project("invalid revision", "A cat waits.")
    shot = Shot(id="sht_invalid_numeric", project_id=project.id, scene_id="sc01", script_beat="cat waits", title="Original", duration_s=5)
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    before = load_shot(project.id, shot.id).model_dump()
    turn = BackendTurn(project.id, "Edit the shot duration", DirectorService(plan_provider=None), None)
    await turn.dispatch("context", {})
    result = await turn.dispatch("tool", {"call_id": "invalid", "name": "revise_shot", "arguments": {"shot_id": shot.id, **update}})
    assert result["ok"] is False
    assert turn.actions == []
    assert load_shot(project.id, shot.id).model_dump() == before


@pytest.mark.asyncio
async def test_stale_inference_cannot_modify_newer_project(tmp_projects_dir):
    from app.agents.director.harness_runtime import BackendTurn

    project = create_project("stale fixture", "original")
    turn = BackendTurn(project.id, "Edit script", None, None)
    await turn.dispatch("context", {})
    save_project(project.model_copy(update={"script_text": "human edit"}))
    await turn.dispatch("context", {})  # Tool-catalog refresh must not renew stale inference authority.
    result = await turn.dispatch("tool", {"name": "set_script", "arguments": {"script": "stale replacement"}, "call_id": "stale"})
    assert not result["ok"] and "changed" in result["error"]
    second = await turn.dispatch("tool", {"name": "set_script", "arguments": {"script": "another stale replacement"}, "call_id": "stale2"})
    assert not second["ok"] and "changed" in second["error"]
    assert load_project(project.id).script_text == "human edit"


@pytest.mark.asyncio
async def test_long_history_and_compaction_do_not_renew_tool_authority(tmp_projects_dir):
    from app.agents.director.harness_runtime import BackendTurn

    project = create_project("long history", "original")
    captured = []

    async def inference(system, user, **kwargs):
        captured.append(kwargs["messages"])
        return {"content": "summary", "tool_calls": []}

    turn = BackendTurn(project.id, "Edit script", None, inference)
    await turn.dispatch("context", {})
    save_project(project.model_copy(update={"script_text": "human edit"}))
    rows = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"short {i}"} for i in range(300)]
    await turn.dispatch("llm", {"purpose": "compaction", "messages": rows})
    assert len(captured[0]) == 300
    result = await turn.dispatch("tool", {"name": "set_script", "arguments": {"script": "stale"}, "call_id": "stale"})
    assert not result["ok"] and "changed" in result["error"]
    await turn.dispatch("llm", {"purpose": "turn", "messages": rows})
    result = await turn.dispatch("tool", {"name": "set_script", "arguments": {"script": "stale"}, "call_id": "fresh"})
    assert result["ok"]


@pytest.mark.asyncio
async def test_failed_legacy_planning_tool_is_not_reported_as_success(tmp_projects_dir):
    from app.agents.director.harness_runtime import BackendTurn

    class Service:
        async def plan_project(self, project_id):
            raise ValueError("fixture planner rejected output")

    project = create_project("failed planner", "A cat opens a box.")
    turn = BackendTurn(project.id, "Plan shots", Service(), None)
    await turn.dispatch("context", {})
    result = await turn.dispatch("tool", {"name": "plan_shots", "arguments": {}, "call_id": "plan"})
    assert not result["ok"]
    assert "plan" not in turn.actions


@pytest.mark.asyncio
async def test_nonstream_chat_reserves_project_and_cancels(tmp_projects_dir, monkeypatch):
    from app.api import projects as api
    from app.agents.director import chat
    from app.core.projects.chat_sessions import DirectorChatSessionRegistry
    from fastapi import HTTPException

    project = create_project("concurrent", "")
    registry = DirectorChatSessionRegistry()
    monkeypatch.setattr(api, "director_chat_sessions", registry)
    started = asyncio.Event()

    async def available():
        return None

    async def make_chat(**kwargs):
        return None

    async def blocked(**kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(api, "_assert_chat_available", available)
    monkeypatch.setattr(api, "_make_chat_fn", make_chat)
    monkeypatch.setattr(chat, "handle_chat", blocked)
    task = asyncio.create_task(api.project_chat_endpoint(project.id, api.ChatBody(message="hello"), None))
    try:
        await started.wait()
        assert (await registry.snapshot(project.id)).active
        with pytest.raises(HTTPException) as error:
            await api.project_chat_endpoint(project.id, api.ChatBody(message="again"), None)
        assert error.value.status_code == 409
        await registry.cancel(project.id)
        assert task.cancelled()
        assert not (await registry.snapshot(project.id)).active
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
