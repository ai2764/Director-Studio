"""Constrained local-agent proposals for ambiguous H3 workflow mappings."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app
from app.workflow_profiles.h3 import (
    H3BoundaryMapping,
    H3NodeCandidate,
    H3ProfileStore,
    H3WorkflowAnalysis,
)


def _mapping(*, seed: str = "129", saver: str = "92") -> H3BoundaryMapping:
    return H3BoundaryMapping(
        h3_node_id="136",
        prompt_input="prompt",
        width_input="width",
        height_input="height",
        frames_input="length",
        picture_input_pattern="ref_images.ref_image_{index}",
        audio_input_pattern="ref_audios.ref_audio_{index}",
        seed_node_id=seed,
        seed_input="noise_seed",
        saver_node_id=saver,
        output_prefix_input="filename_prefix",
    )


def _analysis(
    *,
    compatibility: str = "needs_confirmation",
    mapping: H3BoundaryMapping | None = None,
) -> H3WorkflowAnalysis:
    return H3WorkflowAnalysis(
        compatibility=compatibility,
        mapping=mapping,
        seed_candidates=(H3NodeCandidate(node_id="129", class_type="RandomNoise"),),
        saver_candidates=(
            H3NodeCandidate(node_id="92", class_type="SaveVideo"),
            H3NodeCandidate(node_id="148", class_type="SaveVideo"),
        ),
        agent_manifest={
            "nodes": [
                {
                    "node_id": "129",
                    "class_type": "RandomNoise",
                    "title": "Seed",
                    "input_names": ["noise_seed"],
                    "reachable_outputs": ["92", "148"],
                    "candidate_roles": ["seed"],
                    "defaults": {"noise_seed": 42},
                },
                {
                    "node_id": "136",
                    "class_type": "MiniMaxH3ReferenceToVideo",
                    "title": "H3",
                    "input_names": [
                        "height",
                        "length",
                        "prompt",
                        "width",
                    ],
                    "reachable_outputs": ["92", "148"],
                    "candidate_roles": ["h3"],
                    "defaults": {},
                },
                {
                    "node_id": "92",
                    "class_type": "SaveVideo",
                    "title": "Primary",
                    "input_names": ["filename_prefix", "video"],
                    "reachable_outputs": ["92"],
                    "candidate_roles": ["saver"],
                    "defaults": {"filename_prefix": "[redacted]"},
                },
                {
                    "node_id": "148",
                    "class_type": "SaveVideo",
                    "title": "Alternate",
                    "input_names": ["filename_prefix", "video"],
                    "reachable_outputs": ["148"],
                    "candidate_roles": ["saver"],
                    "defaults": {"filename_prefix": "[redacted]"},
                },
            ]
        },
    )


class FakeOllama:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def chat_response(self, model: str, **kwargs):
        self.calls.append((model, kwargs))
        return self.response


class FakeOrchestrator:
    def __init__(self, ollama: FakeOllama) -> None:
        self.ollama = ollama
        self.sessions: list[dict[str, object]] = []

    @asynccontextmanager
    async def llm_session(self, **kwargs):
        self.sessions.append(kwargs)
        yield self


def _agent_payload(mapping: H3BoundaryMapping | None = None) -> dict[str, str]:
    selected = mapping or _mapping(saver="148")
    return {
        "content": json.dumps(
            {
                "mapping": selected.model_dump(mode="json"),
                "explanations": ["The alternate saver is the intended final output."],
            }
        ),
        "thinking": "",
    }


@pytest.mark.asyncio
async def test_agent_proposal_is_filtered_to_inspector_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflow_profiles.h3.agent import ContractError, H3MappingProposer

    fake_ollama = FakeOllama(
        {"content": json.dumps({"seed_node_id": "evil", "saver_node_id": "148"})}
    )
    orchestrator = FakeOrchestrator(fake_ollama)
    monkeypatch.setattr(
        "app.workflow_profiles.h3.agent.get_orchestrator", lambda: orchestrator
    )
    monkeypatch.setattr(
        "app.workflow_profiles.h3.agent.get_director_model", lambda: "qwen-test"
    )

    with pytest.raises(ContractError, match="not an inspected candidate"):
        await H3MappingProposer().propose(_analysis())


@pytest.mark.asyncio
async def test_agent_proposal_rejects_uninspected_mapping_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflow_profiles.h3.agent import ContractError, H3MappingProposer

    fake_ollama = FakeOllama(
        _agent_payload(_mapping().model_copy(update={"seed_input": "api_token"}))
    )
    orchestrator = FakeOrchestrator(fake_ollama)
    monkeypatch.setattr(
        "app.workflow_profiles.h3.agent.get_orchestrator", lambda: orchestrator
    )
    monkeypatch.setattr(
        "app.workflow_profiles.h3.agent.get_director_model", lambda: "qwen-test"
    )

    with pytest.raises(ContractError, match="not an inspected input"):
        await H3MappingProposer().propose(_analysis())


@pytest.mark.asyncio
async def test_auto_compatible_analysis_skips_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflow_profiles.h3.agent import H3MappingProposer

    fake_ollama = FakeOllama(_agent_payload())
    orchestrator = FakeOrchestrator(fake_ollama)
    monkeypatch.setattr(
        "app.workflow_profiles.h3.agent.get_orchestrator", lambda: orchestrator
    )
    monkeypatch.setattr(
        "app.workflow_profiles.h3.agent.get_director_model", lambda: "qwen-test"
    )
    analysis = _analysis(compatibility="auto_compatible", mapping=_mapping())

    proposal = await H3MappingProposer().propose(analysis)

    assert proposal.mapping == analysis.mapping
    assert fake_ollama.calls == []
    assert orchestrator.sessions == []


@pytest.mark.asyncio
async def test_bundled_auto_mapping_allows_canonical_dynamic_socket_patterns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflow_profiles.h3.agent import H3MappingProposer
    from app.workflow_profiles.h3.inspector import inspect_h3_workflow

    fake_ollama = FakeOllama(_agent_payload())
    monkeypatch.setattr(
        "app.workflow_profiles.h3.agent.get_orchestrator",
        lambda: FakeOrchestrator(fake_ollama),
    )
    graph = json.loads(
        (settings.workflows_dir / "h3_ref2va.api.json").read_text(encoding="utf-8")
    )
    analysis = inspect_h3_workflow(graph)

    proposal = await H3MappingProposer().propose(analysis)

    assert proposal.mapping == analysis.mapping
    assert fake_ollama.calls == []


@pytest.mark.asyncio
async def test_ambiguous_proposal_uses_selected_model_redacted_manifest_and_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflow_profiles.h3.agent import H3MappingProposer, MappingProposal

    fake_ollama = FakeOllama(
        {
            **_agent_payload(),
            "content": "<think>private reasoning</think>\n"
            + _agent_payload()["content"],
        }
    )
    orchestrator = FakeOrchestrator(fake_ollama)
    monkeypatch.setattr(
        "app.workflow_profiles.h3.agent.get_orchestrator", lambda: orchestrator
    )
    monkeypatch.setattr(
        "app.workflow_profiles.h3.agent.get_director_model", lambda: "qwen-test"
    )

    proposal = await H3MappingProposer().propose(_analysis())

    assert proposal.mapping.saver_node_id == "148"
    assert orchestrator.sessions == [{"on_status": None}]
    assert len(fake_ollama.calls) == 1
    model, request = fake_ollama.calls[0]
    assert model == "qwen-test"
    assert request["format"] == MappingProposal.model_json_schema()
    assert request["options"] == {"temperature": 0}
    assert request["messages"][0]["role"] == "system"
    assert "only" in request["messages"][0]["content"].lower()
    user_payload = json.loads(request["messages"][1]["content"])
    assert user_payload["manifest"] == _analysis().agent_manifest
    assert user_payload["seed_candidate_ids"] == ["129"]
    assert user_payload["saver_candidate_ids"] == ["92", "148"]
    assert "workflow" not in user_payload


@pytest.mark.asyncio
async def test_ambiguous_request_discloses_exact_dynamic_socket_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflow_profiles.h3.agent import H3MappingProposer

    fake_ollama = FakeOllama(_agent_payload())
    orchestrator = FakeOrchestrator(fake_ollama)
    monkeypatch.setattr(
        "app.workflow_profiles.h3.agent.get_orchestrator", lambda: orchestrator
    )
    monkeypatch.setattr(
        "app.workflow_profiles.h3.agent.get_director_model", lambda: "qwen-test"
    )

    await H3MappingProposer().propose(_analysis())

    request = fake_ollama.calls[0][1]
    system_prompt = request["messages"][0]["content"]
    user_payload = json.loads(request["messages"][1]["content"])
    schema_json = json.dumps(request["format"], sort_keys=True)
    assert "ref_images.ref_image_{index}" in system_prompt
    assert "ref_audios.ref_audio_{index}" in system_prompt
    assert user_payload["contract_v1_dynamic_socket_patterns"] == {
        "picture_input_pattern": "ref_images.ref_image_{index}",
        "audio_input_pattern": ["ref_audios.ref_audio_{index}", None],
    }
    assert "ref_images.ref_image_{index}" in schema_json
    assert "ref_audios.ref_audio_{index}" in schema_json


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_template",
    [
        pytest.param("prefix {json}", id="prefix"),
        pytest.param("{json} trailing", id="trailing"),
        pytest.param("<think>unterminated\n{json}", id="unterminated-think"),
        pytest.param("```json\n{json}\n```", id="markdown-fence"),
    ],
)
async def test_agent_rejects_any_non_json_around_the_proposal(
    monkeypatch: pytest.MonkeyPatch,
    content_template: str,
) -> None:
    from app.workflow_profiles.h3.agent import ContractError, H3MappingProposer

    content = content_template.format(json=_agent_payload()["content"])
    fake_ollama = FakeOllama({"content": content, "thinking": ""})
    orchestrator = FakeOrchestrator(fake_ollama)
    monkeypatch.setattr(
        "app.workflow_profiles.h3.agent.get_orchestrator", lambda: orchestrator
    )
    monkeypatch.setattr(
        "app.workflow_profiles.h3.agent.get_director_model", lambda: "qwen-test"
    )

    with pytest.raises(ContractError, match="invalid JSON"):
        await H3MappingProposer().propose(_analysis())


@pytest.mark.asyncio
async def test_agent_rejects_single_element_proposal_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflow_profiles.h3.agent import ContractError, H3MappingProposer

    proposal_object = json.loads(_agent_payload()["content"])
    fake_ollama = FakeOllama({"content": json.dumps([proposal_object]), "thinking": ""})
    orchestrator = FakeOrchestrator(fake_ollama)
    monkeypatch.setattr(
        "app.workflow_profiles.h3.agent.get_orchestrator", lambda: orchestrator
    )
    monkeypatch.setattr(
        "app.workflow_profiles.h3.agent.get_director_model", lambda: "qwen-test"
    )

    with pytest.raises(ContractError, match="one JSON object"):
        await H3MappingProposer().propose(_analysis())


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_model", ["  ", None])
async def test_agent_requires_a_selected_director_model(
    monkeypatch: pytest.MonkeyPatch,
    selected_model: str | None,
) -> None:
    from app.workflow_profiles.h3.agent import (
        AgentConfigurationError,
        H3MappingProposer,
    )

    fake_ollama = FakeOllama(_agent_payload())
    orchestrator = FakeOrchestrator(fake_ollama)
    monkeypatch.setattr(
        "app.workflow_profiles.h3.agent.get_orchestrator", lambda: orchestrator
    )
    monkeypatch.setattr(
        "app.workflow_profiles.h3.agent.get_director_model", lambda: selected_model
    )

    with pytest.raises(AgentConfigurationError, match="Director Ollama model"):
        await H3MappingProposer().propose(_analysis())

    assert fake_ollama.calls == []
    assert orchestrator.sessions == []


def test_propose_mapping_endpoint_does_not_accept_or_activate_proposal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflow_profiles.h3.agent import MappingProposal

    monkeypatch.setattr(settings, "workflow_profiles_dir", tmp_path / "profiles")
    store = H3ProfileStore()
    graph = json.loads(
        (settings.workflows_dir / "h3_ref2va.api.json").read_text(encoding="utf-8")
    )
    graph["148"] = {
        "class_type": "SaveVideo",
        "inputs": {
            "filename_prefix": "alternate",
            "video": ["136", 0],
        },
    }
    import_id = store.create_import(graph)
    proposed = MappingProposal(
        mapping=_mapping(saver="148"),
        explanations=("Use the alternate final saver.",),
    )

    async def fake_propose(requested_id: str) -> MappingProposal:
        assert requested_id == import_id
        return proposed

    monkeypatch.setattr(
        "app.api.h3_workflow_profiles._propose_h3_mapping", fake_propose
    )

    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/workflow-profiles/h3/imports/{import_id}/propose-mapping"
        )

    assert response.status_code == 200
    assert response.json()["mapping"]["saver_node_id"] == "148"
    assert store.load_import_mapping(import_id) is None
    assert store.resolve_active().source == "builtin"
