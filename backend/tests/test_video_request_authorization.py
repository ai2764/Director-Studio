import json
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import pytest
from app.agents.director.tool_handlers import video
from test_video_context_sources import _board


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [True, False])
async def test_video_tool_uses_semantic_user_authorization(monkeypatch, tmp_path, allowed):
    project, first, _ = _board(monkeypatch, tmp_path)
    requests = []
    class Provider:
        async def complete(self, system, user, **kwargs):
            requests.append(json.loads(user))
            return json.dumps({"authorized": allowed, "shot_id": first.id})
    async def start(project_id, shot_id, **kwargs):
        assert kwargs["one_off_authorized"] is allowed
        if not allowed:
            raise ValueError("No explicit video request")
        return {"ok":True,"shot_id":shot_id,"job_id":"job_actual"}
    monkeypatch.setattr(video, "start_h3_video", start)
    svc = SimpleNamespace(plan_provider=Provider())
    args={"shot_id":first.id,"resolution_preset":"landscape-480"}
    message="请更新第一镜的设计，然后生成第一镜视频。" if allowed else "第一镜生成视频的话，会有什么问题？"
    if allowed:
        await video.handle_video_tool(name="start_h3_video",args=args,project_id=project.id,
            svc=svc,actions=[],notes=[],result_payloads=[],user_feedback=message)
    else:
        with pytest.raises(ValueError,match="explicit video request"):
            await video.handle_video_tool(name="start_h3_video",args=args,project_id=project.id,
                svc=svc,actions=[],notes=[],result_payloads=[],user_feedback=message)
    assert requests[0]["user_request"] == message
    assert requests[0]["requested_shot_id"] == first.id


@pytest.mark.asyncio
async def test_two_authorized_shots_return_receipts_while_first_holds_gpu(monkeypatch, tmp_path):
    """A second submission must not need GPU inference after generation starts."""
    from app.agents.director.harness_runtime import BackendTurn
    from app.api import projects as projects_api
    from app.core.jobs.store import create_job
    from app.core.projects.store import load_shot, save_shot

    project, first, second = _board(monkeypatch, tmp_path)
    gpu_busy = False
    gpu_released = asyncio.Event()

    class Orchestrator:
        @asynccontextmanager
        async def llm_session(self):
            if gpu_busy:
                await gpu_released.wait()
            yield

    class Provider:
        async def complete(self, system, user, **kwargs):
            target = json.loads(user)["requested_shot_id"]
            return json.dumps({"authorized": True, "shot_id": target,
                               "authorized_shot_ids": [first.id, second.id]})

    async def submit(shot_id, svc, options):
        nonlocal gpu_busy
        gpu_busy = True
        job = create_job(pipeline_id="h3_ref2va", asset_kind="productions",
                         name="queued", project_id=project.id, params={"shot_id": shot_id})
        shot = load_shot(project.id, shot_id).model_copy(update={"h3_job_id": job.id})
        save_shot(shot)
        return shot

    monkeypatch.setattr(projects_api, "submit_shot_endpoint", submit)
    svc = SimpleNamespace(plan_provider=Provider(), orchestrator=Orchestrator())
    turn = BackendTurn(project.id, "现在生成第一镜和第二镜视频。", svc, None)
    receipts = []
    try:
        for index, shot in enumerate((first, second)):
            receipts.append(await asyncio.wait_for(turn.dispatch("tool", {
                "name": "start_h3_video", "arguments": {
                    "shot_id": shot.id, "resolution_preset": "landscape-480"},
                "call_id": f"pair-{index}"}), timeout=0.5))
    except TimeoutError:
        pytest.fail("Second authorized Shot waited for the first video to release the GPU")
    finally:
        gpu_released.set()
    assert all(result["ok"] for result in receipts)
    assert receipts[0]["job_id"] != receipts[1]["job_id"]
    assert [r["job_id"] for r in receipts] == [load_shot(project.id, s.id).h3_job_id
                                              for s in (first, second)]


@pytest.mark.asyncio
async def test_cached_authorization_rejects_other_targets_without_gpu_inference(monkeypatch, tmp_path):
    project, first, second = _board(monkeypatch, tmp_path)
    gpu_busy = False
    released = asyncio.Event()

    class Orchestrator:
        @asynccontextmanager
        async def llm_session(self):
            if gpu_busy:
                await released.wait()
            yield

    class Provider:
        async def complete(self, system, user, **kwargs):
            return json.dumps({"authorized_shot_ids": [first.id]})

    svc = SimpleNamespace(plan_provider=Provider(), orchestrator=Orchestrator())
    cache = {}
    message = "只生成第一镜，不生成第二镜。"
    assert await video._authorize_one_off_video(svc, project.id, first.id, message, "", cache)
    gpu_busy = True
    try:
        assert not await asyncio.wait_for(video._authorize_one_off_video(
            svc, project.id, second.id, message, "", cache), timeout=0.5)
        with pytest.raises(ValueError, match="explicit one-off video request"):
            await video.handle_video_tool(name="start_h3_video",
                args={"shot_id": second.id, "resolution_preset": "landscape-480"},
                project_id=project.id, svc=svc, actions=[], notes=[], result_payloads=[],
                user_feedback=message, video_authorization_cache=cache)
    finally:
        released.set()
    from app.core.projects.store import load_shot
    assert load_shot(project.id, second.id).h3_job_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", ["sht_a", [123], ["sht_unknown"], None])
async def test_malformed_or_unknown_authorization_targets_fail_closed(monkeypatch, tmp_path, ids):
    project, first, _ = _board(monkeypatch, tmp_path)
    class Provider:
        async def complete(self, system, user, **kwargs):
            return json.dumps({"authorized_shot_ids": ids})
    svc = SimpleNamespace(plan_provider=Provider())
    assert not await video._authorize_one_off_video(svc, project.id, first.id,
                                                   "生成第一镜", "", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["message", "previous_assistant", "title", "order"])
async def test_changed_request_or_target_mapping_rechecks_authorization(monkeypatch, tmp_path, change):
    from app.core.projects.store import save_project, save_shot
    project, first, second = _board(monkeypatch, tmp_path)
    decision = [first.id]
    class Provider:
        async def complete(self, system, user, **kwargs):
            return json.dumps({"authorized_shot_ids": decision})
    svc = SimpleNamespace(plan_provider=Provider())
    cache = {}
    message, previous = "生成第一镜。", ""
    assert await video._authorize_one_off_video(svc, project.id, first.id, message, previous, cache)
    decision = []
    if change == "message":
        message = "先别生成。"
    elif change == "previous_assistant":
        previous = "先讨论第二镜。"
    elif change == "title":
        save_shot(first.model_copy(update={"title": "Renamed shot"}))
    else:
        save_project(project.model_copy(update={"shot_ids": [second.id, first.id]}))
    assert not await video._authorize_one_off_video(svc, project.id, first.id, message, previous, cache)


@pytest.mark.asyncio
async def test_authorization_is_not_reused_by_a_new_backend_turn(monkeypatch, tmp_path):
    from app.agents.director.harness_runtime import BackendTurn
    from app.api import projects as projects_api
    from app.core.jobs.store import create_job
    from app.core.projects.store import load_shot, save_shot
    project, first, second = _board(monkeypatch, tmp_path)
    decision = [first.id, second.id]
    class Provider:
        async def complete(self, system, user, **kwargs):
            return json.dumps({"authorized_shot_ids": decision})
    async def submit(shot_id, svc, options):
        job = create_job(pipeline_id="h3_ref2va", asset_kind="productions",
                         name="queued", project_id=project.id, params={"shot_id": shot_id})
        shot = load_shot(project.id, shot_id).model_copy(update={"h3_job_id": job.id})
        save_shot(shot)
        return shot
    monkeypatch.setattr(projects_api, "submit_shot_endpoint", submit)
    svc = SimpleNamespace(plan_provider=Provider())
    message = "生成第一镜和第二镜视频。"
    old_turn = BackendTurn(project.id, message, svc, None)
    first_result = await old_turn.dispatch("tool", {"name": "start_h3_video", "arguments": {
        "shot_id": first.id, "resolution_preset": "landscape-480"}, "call_id": "old"})
    assert first_result["ok"]
    decision = []
    new_turn = BackendTurn(project.id, message, svc, None)
    second_result = await new_turn.dispatch("tool", {"name": "start_h3_video", "arguments": {
        "shot_id": second.id, "resolution_preset": "landscape-480"}, "call_id": "new"})
    assert second_result["ok"] is False
    assert load_shot(project.id, second.id).h3_job_id is None
