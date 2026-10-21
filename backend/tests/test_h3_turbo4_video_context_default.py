import json
from pathlib import Path

from app.config import Settings
from app.pipelines.h3_ref2va.video_context import strip_motion_context
from app.workflow_profiles.h3.store import TURBO4_VIDEO_CONTEXT_WORKFLOW, builtin_mapping

WF = Path(__file__).resolve().parents[1] / "workflows" / TURBO4_VIDEO_CONTEXT_WORKFLOW


def test_default_builtin_is_turbo4_video_context(monkeypatch):
    monkeypatch.delenv("DS_H3_BUILTIN_WORKFLOW", raising=False)
    assert Settings.model_fields["h3_builtin_workflow"].default == TURBO4_VIDEO_CONTEXT_WORKFLOW


def test_builtin_mapping_maps_context_video():
    m = builtin_mapping(TURBO4_VIDEO_CONTEXT_WORKFLOW)
    assert (m.context_video.node_id, m.context_video.input_name) == ("142", "file")
    assert builtin_mapping("h3_ref2va_fast4.api.json").context_video is None


def test_strip_motion_context_yields_plain_ref2va():
    g = json.loads(WF.read_text())
    out = strip_motion_context(g)
    classes = {n["class_type"] for n in out.values()}
    assert not classes & {"MiniMaxH3MotionContext", "MiniMaxH3MotionContextTrim", "LoadVideo",
                          "GetVideoComponents", "ImageFromBatch", "TrimAudioDuration"}
    assert out["126"]["inputs"]["conditioning"] == ["136", 0]
    assert out["130"]["inputs"]["images"] == ["122", 0]
    assert out["130"]["inputs"]["audio"] == ["121", 0]
    assert "144" in g  # input not mutated
