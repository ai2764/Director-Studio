"""Append is an isolated write, not a full storyboard replacement."""
import json

import pytest

from app.config import settings
from app.core.projects.models import Shot
from app.core.projects.store import create_project, list_shots, load_project, save_project, save_shot, shots_dir
from app.agents.director.service import DirectorService, _script_hash
from test_harness_integration import real_sidecar


@pytest.fixture
def board(tmp_path, monkeypatch):
    for setting in ("projects_dir", "jobs_dir", "library_root"):
        path = tmp_path / setting
        path.mkdir()
        monkeypatch.setattr(settings, setting, path)
    project = create_project("append test", "The traveler reaches the shore.")
    for i in range(4):
        shot = Shot(id=f"old_{i}", project_id=project.id, scene_id="shore",
                    title=f"Old {i}", script_beat="Keep this beat", duration_s=8,
                    refs=[{"role": "actor", "asset_id": "actor_old", "picture_index": 1}],
                    voice_refs=[{"asset_id": "voice_old", "audio_index": 1}],
                    layout_asset_id="layout_old", layout_review_status="approved",
                    h3_job_id="video_old", ref_frame_job_id="frame_old",
                    prompt_sections={"integrated_multimodal_description": "Keep this prompt"},
                    meta={"keep": [1, 2, 3]})
        save_shot(shot)
        project.shot_ids.append(shot.id)
    save_project(project)
    return project, DirectorService(plan_provider=None, orchestrator=object())


def payload(project):
    return {"expected_script_hash": _script_hash(project.script_text),
            "expected_last_shot_id": project.shot_ids[-1] if project.shot_ids else None,
            "shot": {"scene_id": "shore", "title": "Final wave", "script_beat": "A wave breaks.",
                     "shot_type": "wide", "camera_angle": "eye level",
                     "camera_motion": "locked-off", "composition": "empty shore", "duration_s": "7"}}


def snapshot(project):
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in shots_dir(project.id).glob("*.json")}


def test_append_preserves_every_old_file_and_rejects_replay(board):
    project, svc = board
    before = snapshot(project)
    request = payload(project)
    new = svc.append_shot(project.id, request)
    assert new.id not in project.shot_ids
    assert new.duration_s == 7
    assert load_project(project.id).shot_ids == project.shot_ids + [new.id]
    assert {k: snapshot(project)[k] for k in before} == before
    with pytest.raises(ValueError, match="tail|last Shot"):
        svc.append_shot(project.id, request)
    assert len(list_shots(project.id)) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["native", "harness"])
@pytest.mark.parametrize("status_first", [False, True])
async def test_real_node_append_roundtrip(board, real_sidecar, monkeypatch, runtime, status_first):
    from unittest.mock import AsyncMock
    from app.agents.director.harness_runtime import handle_harness_chat
    from app.agents.director.chat import handle_chat
    project, svc = board
    monkeypatch.setattr(settings, "harness_base_url", real_sidecar[0])
    monkeypatch.setattr(settings, "harness_internal_token", real_sidecar[1])
    monkeypatch.setattr(settings, "director_agent_runtime", "legacy")
    before = snapshot(project)
    svc.plan_project = AsyncMock(side_effect=AssertionError("append must not replan"))
    calls = 0

    async def inference(system, user, **kwargs):
        nonlocal calls
        calls += 1
        if calls == (2 if status_first else 1):
            assert "append_shot" in system
            return {"content": "", "tool_calls": [{"name": "append_shot", "arguments": payload(project)}]}
        if calls == (1 if status_first else 2):
            return {"content": "", "tool_calls": [{"name": "get_status", "arguments": {}}]}
        assert calls == 3
        results = [json.loads(m["content"]) for m in kwargs["messages"] if m["role"] == "tool"]
        assert results
        return {"content": "Created 1 shot.", "tool_calls": []}

    handler = handle_harness_chat if runtime == "harness" else handle_chat
    result = await handler(project_id=project.id, message="Add one final wave shot.", svc=svc, chat_fn=inference)
    assert calls == 3
    svc.plan_project.assert_not_called()
    assert "append_shot" in result.actions and "plan_shots" not in result.actions
    assert "not saved" not in result.reply
    assert len(result.shots) == 5
    assert {k: snapshot(project)[k] for k in before} == before


@pytest.mark.parametrize("bad", ["hash", "tail", "old_id", "unknown_field", "asset", "missing_tail", "bool", "nan", "infinity", "negative", "null"])
def test_bad_append_is_write_free(board, bad):
    project, svc = board
    request = payload(project)
    if bad == "hash": request["expected_script_hash"] = "outdated"
    elif bad == "tail": request["expected_last_shot_id"] = "old_0"
    elif bad == "old_id": request["shot"]["shot_id"] = "old_0"
    elif bad == "unknown_field": request["shot"]["h3_job_id"] = "overwrite"
    elif bad == "asset": request["shot"]["asset_matches"] = [{"role": "actor", "asset_id": "missing"}]
    elif bad == "missing_tail": del request["expected_last_shot_id"]
    else: request["shot"]["duration_s"] = {"bool": True, "nan": "NaN", "infinity": "Infinity", "negative": -1, "null": None}[bad]
    before = snapshot(project)
    with pytest.raises(ValueError):
        svc.append_shot(project.id, request)
    assert snapshot(project) == before
    assert load_project(project.id) == project


def test_append_empty_board_and_id_collision(board, monkeypatch):
    from app.agents.director import casting_service
    project, svc = board
    monkeypatch.setattr(casting_service, "new_shot_id", lambda: "old_0")
    before = snapshot(project)
    with pytest.raises(ValueError, match="id|ID"):
        svc.append_shot(project.id, payload(project))
    assert snapshot(project) == before
    empty = create_project("empty", "Shore")
    new = svc.append_shot(empty.id, payload(empty))
    assert load_project(empty.id).shot_ids == [new.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["native", "harness"])
async def test_append_tool_compact_result_and_no_injected_replan(board, runtime):
    from app.agents.director.chat import _run_tools
    from app.agents.director.chat_orchestrator import sanitize_tools_for_pipeline
    from app.agents.director.harness_runtime import BackendTurn
    from app.agents.director.chat_context import project_context_blob
    project, svc = board
    before = snapshot(project)
    state = json.loads(project_context_blob(project, list_shots(project.id)))
    assert state["last_shot_id"] == "old_3"
    requested = {"name": "append_shot", "args": payload(project)}
    safe, _ = sanitize_tools_for_pipeline([requested], project=project, shots=list_shots(project.id))
    assert safe == [requested]
    if runtime == "native":
        actions, results = [], []
        await _run_tools(project_id=project.id, tools=safe, svc=svc, actions=actions, result_payloads=results)
        assert actions == ["append_shot"]
        assert results[0]["ok"] is True
        assert "storyboard" not in results[0]
        assert results[0]["shot"]["title"] == "Final wave"
    else:
        turn = BackendTurn(project.id, "在最后加一个镜头，海浪拍岸", svc, None)
        await turn.dispatch("context", {})
        result = await turn.dispatch("tool", {"name": "append_shot", "arguments": payload(project), "call_id": "a"})
        assert result["ok"] is True, result
        assert turn.actions == ["append_shot"]
    assert len(list_shots(project.id)) == 5
    assert {k: snapshot(project)[k] for k in before} == before
