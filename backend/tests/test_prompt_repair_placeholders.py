"""A fresh placeholder draft replaces, rather than inherits, rejected binding claims."""
import json
import pytest
from app.agents.director.prompt_repair import merge_repair


@pytest.mark.parametrize("bare_patch", [False, True])
def test_fresh_same_placeholder_detail_drops_stale_binding_claim(bare_patch):
    detail = "She whispers {{speech:line-1}}"
    base = {"prompt_sections": {"subject_definitions": "<Picture 1>", "detailed_description": detail},
            "dialogue_uses": [{"line_id": "line-1", "speaker_id": "wrong", "block_indexes": [0]}]}
    fields = {"detailed_description": detail}
    patch = fields if bare_patch else {"prompt_sections": fields}
    merged = json.loads(merge_repair(json.dumps(patch), json.dumps(base)))
    assert "dialogue_uses" not in merged
    assert merged["prompt_sections"]["subject_definitions"] == "<Picture 1>"


def test_supplied_binding_claim_is_not_silently_discarded():
    detail = "She whispers {{speech:line-1}}"
    claim = [{"line_ids": ["line-1"], "speaker_id": "wrong", "block_indexes": [0]}]
    base = {"prompt_sections": {"detailed_description": detail}}
    patch = {"prompt_sections": {"detailed_description": detail}, "dialogue_uses": claim}
    assert json.loads(merge_repair(json.dumps(patch), json.dumps(base)))["dialogue_uses"] == claim
