import json
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
