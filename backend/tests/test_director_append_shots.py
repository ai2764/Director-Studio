"""Batch appends validate every new record before publishing the storyboard."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.config import settings
from app.core.projects.store import list_shots, load_project, project_dir
from test_director_append_shot import board, payload, snapshot
from test_harness_integration import real_sidecar


def batch(project):
    request = payload(project)
    draft = request.pop("shot")
    return {**request, "shots": [
        {**draft, "title": "Arrival"},
        {**draft, "title": "Continue", "video_context": {
            "mode": "previous_shot", "source_index": 1}},
        {**draft, "title": "Empty shore", "actor_presence": "none",
         "video_context": {"mode": "off"}},
    ]}


def test_batch_preserves_old_files_saves_dependencies_and_rejects_replay(board, monkeypatch):
    project, svc = board
    monkeypatch.setattr(settings, "video_context_enabled", True)
    before = snapshot(project)
    request = batch(project)
    shots = svc.append_shots(project.id, request)
    assert load_project(project.id).shot_ids == project.shot_ids + [s.id for s in shots]
    assert [s.title for s in shots] == ["Arrival", "Continue", "Empty shore"]
    assert shots[1].video_context.source_shot_id == shots[0].id
    assert shots[1].video_context.source_job_id is None
    assert shots[1].video_context.source_output_key is None
    assert shots[2].video_context.mode == "off"
    assert {k: snapshot(project)[k] for k in before} == before
    with pytest.raises(ValueError, match="tail"):
        svc.append_shots(project.id, request)
    assert len(list_shots(project.id)) == 7


@pytest.mark.parametrize("fault", ["last_asset", "duration", "self", "future", "foreign", "too_many", "disabled"])
def test_invalid_batch_writes_nothing(board, monkeypatch, fault):
    project, svc = board
    monkeypatch.setattr(settings, "video_context_enabled", fault != "disabled")
    request = batch(project)
    if fault == "last_asset": request["shots"][-1]["asset_matches"] = [{"role": "scene", "asset_id": "missing"}]
    elif fault == "duration": request["shots"][-1]["duration_s"] = True
    elif fault in {"self", "future"}: request["shots"][1]["video_context"]["source_index"] = 2 if fault == "self" else 3
    elif fault == "foreign": request["shots"][1]["video_context"] = {"mode": "previous_shot", "source_shot_id": "foreign"}
    elif fault == "too_many": request["shots"] *= 3
    before = snapshot(project)
    before_project = (project_dir(project.id) / "project.json").read_bytes()
    with pytest.raises(ValueError): svc.append_shots(project.id, request)
    assert snapshot(project) == before
    assert (project_dir(project.id) / "project.json").read_bytes() == before_project


def test_batch_rolls_back_new_files_if_index_save_fails(board, monkeypatch):
    from app.agents.director import service
    project, svc = board
    monkeypatch.setattr(settings, "video_context_enabled", True)
    before = snapshot(project)
    before_project = (project_dir(project.id) / "project.json").read_bytes()
    def fail(_project): raise OSError("fixture disk failure")
    monkeypatch.setattr(service, "save_project", fail)
    with pytest.raises(OSError, match="disk failure"): svc.append_shots(project.id, batch(project))
    assert snapshot(project) == before
    assert (project_dir(project.id) / "project.json").read_bytes() == before_project


def test_concurrent_batches_cannot_both_append_against_the_same_tail(board, monkeypatch):
    project, svc = board
    monkeypatch.setattr(settings, "video_context_enabled", True)
    request = batch(project)
    def append():
        try: return len(svc.append_shots(project.id, request))
        except ValueError: return 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: append(), range(2))) == [0, 3]
    assert len(list_shots(project.id)) == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["native", "harness"])
async def test_real_node_batch_tool_roundtrip(board, real_sidecar, monkeypatch, runtime):
    from app.agents.director.harness_runtime import handle_harness_chat
    from app.agents.director.chat import handle_chat
    project, svc = board
    monkeypatch.setattr(settings, "video_context_enabled", True)
    monkeypatch.setattr(settings, "harness_base_url", real_sidecar[0])
    monkeypatch.setattr(settings, "harness_internal_token", real_sidecar[1])
    monkeypatch.setattr(settings, "director_agent_runtime", "legacy")
    before = snapshot(project)
    calls = 0
    async def inference(system, user, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert "append_shots" in {t["function"]["name"] for t in kwargs["tools"]}
            return {"content": "", "tool_calls": [{"name": "append_shots", "arguments": batch(project)}]}
        results = [json.loads(m["content"]) for m in kwargs["messages"] if m["role"] == "tool"]
        assert results[-1]["ok"] is True
        assert results[-1]["created_count"] == 3
        return {"content": "Created the requested shots.", "tool_calls": []}
    handler = handle_harness_chat if runtime == "harness" else handle_chat
    result = await handler(project_id=project.id, message="Append three shots, second continues first; do not generate.", svc=svc, chat_fn=inference)
    assert calls == 2
    assert "append_shots:3" in result.actions
    assert "Appended 3 new shots" in result.reply
    assert len(result.shots) == 7
    assert {k: snapshot(project)[k] for k in before} == before
