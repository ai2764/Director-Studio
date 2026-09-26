"""Multi-step pilot behavior; fixture inference, real temporary project storage."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from task_context_fixtures import context_case
from test_director_material_review import material_shot, tail_handoff_shot, Orchestrator
from test_task_context_writers import enable
from app.config import settings
from app.core.projects.store import save_project, save_shot, load_shot, create_project
from app.agents.director.task_context_models import TaskRequest, TaskContextState, ContextRead
from app.agents.director.task_context_query import set_task_context, read_task_context
from app.agents.director.task_context_snapshot import capture_task_snapshot, assert_packet_current, ContextChanged
from app.agents.director.task_context_builder import build_task_packet, ContextRequired
from app.agents.director.task_context_runtime import task_context_scope, build_writer_packet


def test_wrong_focus_can_be_corrected_without_changing_authored_state(context_case):
    project, target, neighbor = context_case
    before = capture_task_snapshot(project.id)
    state = TaskContextState(project_id=project.id, request=TaskRequest(kind="shot_prompt",
        target_shot_id=neighbor.id, objective="Rewrite the requested target prompt only."), retrieved_versions={})
    read_task_context(state, ContextRead(source_key=f"shot:{target.id}"))
    set_task_context(state, TaskRequest(kind="shot_prompt", target_shot_id=target.id))
    packet = build_task_packet(capture_task_snapshot(project.id), state.request,
        authority={"allowed_mutations": ["write_prompt"]}, max_chars=50000,
        extra_sources=tuple(state.retrieved_versions))
    assert packet.task.target_shot_id == target.id
    assert packet.task.objective == "Rewrite the requested target prompt only."
    assert capture_task_snapshot(project.id) == before


@pytest.mark.asyncio
async def test_pilot_dialogue_recovery_camera_edit_rewrite(material_shot, monkeypatch):
    from test_dialogue_camera_revision import test_retry_then_camera_edit_then_retry_preserves_verified_speakers
    enable(monkeypatch, material_shot[0])
    await test_retry_then_camera_edit_then_retry_preserves_verified_speakers(material_shot)


def test_cached_wardrobe_does_not_replace_current_reference(material_shot):
    from app.agents.director.context_io import save_agent_context
    from app.agents.director.service import _build_context
    from app.core.library.store import load_asset, _write_asset
    project, shot, _, files = material_shot
    ctx = _build_context(project, [shot], phase="planned")
    save_agent_context(project.id, ctx.model_copy(update={"shot_summaries": [
        {"id": shot.id, "script_beat": "OBSOLETE: denim trousers"}]}))
    ref = shot.refs[0]
    asset = load_asset("props", ref.asset_id)
    _write_asset(asset.model_copy(update={"notes": "Current reference: beige formal outfit"}))
    Image.new("RGB", (400, 400), "beige").save(files[0], compress_level=0)
    packet = build_writer_packet(project, shot.id)
    serialized = packet.model_dump_json()
    assert "beige formal outfit" in serialized and "OBSOLETE" not in serialized
    assert packet.facts["references"][0]["content_sha256"] == hashlib.sha256(files[0].read_bytes()).hexdigest()


def test_read_then_ui_reference_change_cannot_be_silently_adopted(material_shot, monkeypatch):
    project, shot, _, files = material_shot
    enable(monkeypatch, project)
    state = TaskContextState(project_id=project.id, request=TaskRequest(kind="shot_prompt", target_shot_id=shot.id), retrieved_versions={})
    with task_context_scope(state):
        read_task_context(state, ContextRead(source_key=f"asset:props:{shot.refs[0].asset_id}:selected"))
        packet = build_writer_packet(project, shot.id)
        Image.new("RGB", (400, 400), "blue").save(files[0], compress_level=0)
        with pytest.raises(ContextChanged):
            assert_packet_current(packet)
        with pytest.raises(ContextChanged):
            build_writer_packet(project, shot.id)
    assert Image.open(files[0]).getpixel((0, 0)) == (0, 0, 255)
    assert load_shot(project.id, shot.id).prompt_sections == shot.prompt_sections


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["harness", "legacy"])
async def test_stale_read_preflight_refreshes_without_generation_failure(material_shot, monkeypatch, runtime):
    from app.agents.director.harness_runtime import BackendTurn
    from app.agents.director.chat import handle_chat
    from app.agents.director.service import DirectorService
    from test_director_material_review import Provider
    project, shot, _, files = material_shot
    enable(monkeypatch, project)
    monkeypatch.setattr(settings, "director_agent_runtime", runtime)
    orch = Orchestrator()
    provider = Provider(orch)
    svc = DirectorService(plan_provider=provider, orchestrator=orch)
    source_key = f"asset:props:{shot.refs[0].asset_id}:selected"
    state = TaskContextState(project_id=project.id, request=TaskRequest(objective="Rewrite this prompt only."), retrieved_versions={})
    operations = [("read_task_context", {"source_key": source_key}),
        ("write_prompt", {"shot_id": shot.id}),
        ("read_task_context", {"source_key": source_key}),
        ("write_prompt", {"shot_id": shot.id})]
    def replace_reference():
        Image.new("RGB", (400, 400), "blue").save(files[0], compress_level=0)
    def assert_gap(result):
        assert result.get("code") == "CONTEXT_REQUIRED", result
        assert result["concludes_turn"] is False
        assert result["missing"][0]["source_key"] == source_key
        assert provider.text == [] and provider.visual == []
        assert not list(settings.projects_dir.rglob("prompt_retry.json"))
        assert load_shot(project.id, shot.id) == shot
    with task_context_scope(state):
        if runtime == "harness":
            turn = BackendTurn(project.id, "Consider this prompt", svc, None)
            await turn.dispatch("context", {})
            for i, (name, args) in enumerate(operations):
                if i == 1:
                    replace_reference()
                result = await turn.dispatch("tool", {"name": name, "arguments": args, "call_id": str(i)})
                if i == 1:
                    assert_gap(result)
                    assert turn.terminal_failure is None
                else:
                    assert result["ok"], result
        else:
            calls = 0
            async def infer(system, user, **kwargs):
                nonlocal calls
                index = calls
                calls += 1
                if index == 1:
                    replace_reference()
                if index == 2:
                    assert_gap(json.loads(kwargs["messages"][-1]["content"]))
                if index == len(operations):
                    return {"content": "Prompt saved.", "tool_calls": []}
                name, args = operations[index]
                return {"content": "", "tool_calls": [{"id": str(index), "name": name, "arguments": args}]}
            result = await handle_chat(project_id=project.id, message="Please consider the current prompt.", svc=svc, chat_fn=infer)
            assert calls == 5 and result.failure_code == ""
            assert f"write_prompt:{shot.id}" in result.actions
        assert state.writer_receipts[shot.id]["status"] == "saved"
        before = (len(provider.text), len(provider.visual))
        await svc.write_prompts_after_layout(shot.id)
        assert (len(provider.text), len(provider.visual)) == before


def test_retry_view_switch_keeps_exact_authorization(context_case):
    from app.agents.director.task_context_runtime import present_task_tools
    project, target, _ = context_case
    permitted = [{"type": "function", "function": {"name": "write_prompt", "parameters": {
        "type": "object", "properties": {"shot_id": {"const": target.id}}, "required": ["shot_id"]}}}]
    state = TaskContextState(project_id=project.id, request=TaskRequest(kind="shot_prompt", target_shot_id=target.id), retrieved_versions={})
    for kind in ("overview", "shot_prompt", "overview"):
        set_task_context(state, TaskRequest(kind=kind, target_shot_id=target.id))
        tools = present_task_tools(permitted, state)
        assert {t["function"]["name"] for t in tools} == {"write_prompt", "set_task_context", "read_task_context"}
        assert tools[0] == permitted[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("motion", ["Slow lateral dolly while she gathers resolve.", "Gentle pullback while she relaxes her shoulders."])
async def test_distinct_creative_candidates_are_not_golden_prose_gated(tail_handoff_shot, monkeypatch, motion):
    from test_tail_prompt_review import Provider, candidate, verdict
    from app.agents.director.service import DirectorService
    project, shot = tail_handoff_shot
    enable(monkeypatch, project)
    draft = candidate(motion)
    draft["prompt_sections"]["summary"] = motion
    draft["prompt_sections"]["detailed_description"] = "0-6 seconds: Begin in the inherited waist-up standing view. " + motion
    saved = await DirectorService(plan_provider=Provider([draft, verdict()]), orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert saved.camera_motion == motion
    assert motion in saved.prompt_sections.detailed_description


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["legacy", "harness"])
async def test_chat_cancellation_does_not_cancel_independent_job(context_case, monkeypatch, runtime):
    from app.core.jobs.store import create_job, load_job
    from test_task_context_runtime import test_cancelling_runtime_cleans_scope
    project, target, _ = context_case
    job = create_job(pipeline_id="h3", asset_kind="clips", name="Already submitted", project_id=project.id)
    save_shot(target.model_copy(update={"h3_job_id": job.id}))
    await test_cancelling_runtime_cleans_scope(context_case, monkeypatch, runtime)
    assert load_job(job.id) == job


def test_foreign_job_pointer_does_not_grant_read(context_case):
    from app.core.jobs.store import create_job
    project, target, _ = context_case
    foreign = create_project("Other project", "Private script")
    job = create_job(pipeline_id="h3", asset_kind="clips", name="Private job", project_id=foreign.id)
    save_shot(target.model_copy(update={"h3_job_id": job.id}))
    state = TaskContextState(project_id=project.id, request=TaskRequest(), retrieved_versions={})
    with pytest.raises(ValueError, match="CONTEXT_SOURCE_NOT_ALLOWED"):
        read_task_context(state, ContextRead(source_key=f"job:{job.id}"))


@pytest.mark.asyncio
async def test_scoped_retry_context_gap_keeps_original_retry_receipt(material_shot, monkeypatch):
    from app.agents.director.prompt_retry import record_prompt_failure, run_prompt_retry, pending_prompt_retry
    from app.agents.director.service import DirectorService
    project, shot, _, _ = material_shot
    enable(monkeypatch, project)
    monkeypatch.setattr(settings, "director_num_ctx", 1000)
    request = record_prompt_failure(shot, "Keep the camera", ValueError("previous failure"))
    with pytest.raises(ContextRequired):
        await run_prompt_retry(project.id, request, DirectorService(plan_provider=None, orchestrator=Orchestrator()))
    assert pending_prompt_retry(project.id) == request
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_long_history_final_envelope_is_measured_honestly(context_case, monkeypatch):
    from app.agents.director.harness_runtime import BackendTurn
    from app.api.projects import _make_chat_fn
    from app.agents.director import context_metrics as metrics
    from test_active_llm_routing import FakeOrchestrator, RecordingProvider
    project, target, neighbor = context_case
    enable(monkeypatch, project)
    with task_context_scope(TaskContextState(project_id=project.id, request=TaskRequest(
            kind="shot_prompt", target_shot_id=target.id), retrieved_versions={})):
        small_state_chars = len(BackendTurn(project.id, "Consider the camera movement.", None, None).context()["state"])
    others = []
    for i in range(500):
        other = target.model_copy(update={"id": f"sht_extra_{i}", "script_beat": "UNRELATED_DETAIL" * 30})
        save_shot(other)
        others.append(other.id)
    save_project(project.model_copy(update={"shot_ids": [*others, neighbor.id, target.id]}))
    monkeypatch.setattr("app.core.vram.get_orchestrator", lambda: FakeOrchestrator())
    requests, shapes, rows = [], [], []
    async def response(model, **kwargs):
        requests.append(copy.deepcopy(kwargs))
        return {"content": "Consider a different camera movement.", "tool_calls": []}
    chat = await _make_chat_fn(provider=RecordingProvider(SimpleNamespace(chat_response=response)))
    monkeypatch.setattr(metrics, "record_request_shape", shapes.append)
    history = [{"role": "user" if i % 2 else "assistant", "content": "HISTORY " * 125} for i in range(20)]
    history.append({"role": "user", "content": "Consider the camera movement."})
    before = {p: p.read_bytes() for p in settings.projects_dir.rglob("*") if p.is_file()}
    for mode, allowed in (("off", True), ("shadow", True), ("pilot", False), ("pilot", True)):
        enable(monkeypatch, project)
        monkeypatch.setattr(settings, "director_task_context_mode", mode)
        if not allowed:
            monkeypatch.setattr(settings, "director_task_context_projects", [])
        state = TaskContextState(project_id=project.id, request=TaskRequest(kind="shot_prompt", target_shot_id=target.id), retrieved_versions={})
        with task_context_scope(state), metrics.metrics_scope(project.id):
            turn = BackendTurn(project.id, history[-1]["content"], None, chat, history=history[:-1])
            context = turn.context()
            await turn.infer({"messages": history})
        final = requests[-1]
        system = final["messages"][0]["content"]
        rows.append({"mode": mode, "allowed": allowed, "system_without_state_chars": len(context["system"]),
            "task_state_chars": len(context["state"]), "final_system_chars": len(system),
            "history_and_user_chars": sum(len(m["content"]) for m in final["messages"][1:]),
            "tool_schema_chars": len(json.dumps(final["tools"], ensure_ascii=False, sort_keys=True)), "image_count": 0})
        if mode == "pilot" and allowed:
            assert "UNRELATED_DETAIL" not in context["state"]
            # Runtime authority includes the original schemas; only catalog count grows.
            assert len(context["state"]) <= small_state_chars + 10
            assert json.loads(context["state"])["complete"]
    assert len(requests) == 4 and requests[0] == requests[1] == requests[2]
    assert all(row["history_and_user_chars"] == sum(len(m["content"]) for m in history) for row in rows)
    assert before == {p: p.read_bytes() for p in settings.projects_dir.rglob("*") if p.is_file()}
    final_shapes = [s for s in shapes if s["path"].startswith("chat.")]
    assert len(final_shapes) == 2
    assert final_shapes[-1]["text_chars"] == rows[-1]["final_system_chars"] + rows[-1]["history_and_user_chars"]
    print("ENVELOPE_MEASUREMENTS " + json.dumps(rows))
