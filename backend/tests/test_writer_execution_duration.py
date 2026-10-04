"""Execution duration is projected only onto the current writer target."""
from copy import deepcopy

import pytest

from app.agents.director.writer_context import project_writer_context


@pytest.mark.parametrize("context", [
    {"shot_summaries": [{"id": "target", "duration_s": 8}, {"id": "neighbor", "duration_s": 12}]},
    {"facts": {"target": {"id": "target", "duration_s": 8}},
     "history": [{"id": "target", "duration_s": 99}]},
])
def test_writer_uses_submit_duration_without_mutating_authored_or_historical_timing(context):
    original = deepcopy(context)
    intent = {"current_shot": {"id": "target"}, "execution_duration_s": 7}
    projected, _ = project_writer_context(context, intent, [])
    target = (projected["shot_summaries"][0] if "shot_summaries" in projected
              else projected["facts"]["target"])
    assert target["duration_s"] == 7
    assert target["storyboard_duration_s"] == 8
    assert context == original
    if "history" in projected:
        assert projected["history"] == original["history"]
    else:
        assert projected["shot_summaries"][1] == original["shot_summaries"][1]
