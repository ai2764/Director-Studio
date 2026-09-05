"""Static check: pure H3 Ref2AV fill never binds I2V first/last frame sockets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import settings
from app.pipelines.h3_ref2va.workflow import (
    H3_I2V_NODE,
    H3_REF_NODE,
    fill_ref2va_graph,
    minimal_graph,
)

VALID_PROMPT = (
    "subject_definitions:\nA\n"
    "summary:\nB\n"
    "retention_analysis:\nC\n"
    "detailed_description:\nD\n"
    "overall_soundscape:\nE\n"
    "non_diegetic_music:\nF"
)


def _assert_no_i2v(graph: dict) -> None:
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        assert node.get("class_type") != H3_I2V_NODE
        assert node.get("class_type") != "ImageToVideo"
        inputs = node.get("inputs") or {}
        assert "ref_frame" not in inputs
        assert "last_frame" not in inputs


def test_fill_graph_has_no_i2v_ref_frame_bindings():
    """Filled minimal graph is pure MiniMaxH3ReferenceToVideo (no I2V sockets)."""
    job = {
        "prompt": VALID_PROMPT,
        "dialogue": [],
        "images": ["layout.png", "actor.png"],
        "frames": 294,
        "seed": 1,
        "width": 864,
        "height": 480,
        "output_prefix": "director-studio/test/h3",
    }
    filled = fill_ref2va_graph(minimal_graph(), job)
    _assert_no_i2v(filled)

    h3 = next(
        n for n in filled.values() if isinstance(n, dict) and n.get("class_type") == H3_REF_NODE
    )
    ref_keys = [k for k in (h3.get("inputs") or {}) if k.startswith("ref_images.")]
    assert len(ref_keys) == 2


def test_fill_rejects_i2v_sockets_in_contaminated_base():
    """The public workflow contract rejects rather than repairs a non-Ref2AV graph."""
    graph = minimal_graph()
    graph["99"] = {
        "class_type": H3_I2V_NODE,
        "inputs": {"ref_frame": ["1", 0], "last_frame": ["1", 0]},
    }
    graph["10"]["inputs"]["ref_frame"] = ["1", 0]
    graph["10"]["inputs"]["last_frame"] = ["1", 0]

    with pytest.raises(ValueError, match="not allowed|sockets"):
        fill_ref2va_graph(
            graph,
            {
                "prompt": VALID_PROMPT,
                "images": ["a.png"],
                "frames": 124,
                "seed": 0,
            },
        )


def test_production_workflow_json_is_pure_ref2va():
    """Checked-in production API graph must not use MiniMaxH3ImageToVideo."""
    path = settings.workflows_dir / "h3_ref2va.api.json"
    assert path.is_file(), f"missing workflow: {path}"
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    has_ref = False
    for node in data.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type")
        assert ct != H3_I2V_NODE
        assert ct != "ImageToVideo"
        if ct == H3_REF_NODE:
            has_ref = True
            inputs = node.get("inputs") or {}
            assert "ref_frame" not in inputs
            assert "last_frame" not in inputs
    assert has_ref, f"workflow missing {H3_REF_NODE}"
