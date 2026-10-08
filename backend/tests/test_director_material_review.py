"""Single-shot material review: real files/storage, only inference is a fixture."""
import base64
import io
import json
from contextlib import asynccontextmanager

import pytest
from PIL import Image

from app.config import settings
from app.agents.director.material_review import observe_reference
from app.agents.director.service import DirectorService
from app.core.projects.models import (
    AssetCoverageRecommendation,
    AssetCoverageReview,
    PromptSections,
    RefRole,
    Shot,
    ShotRef,
)
from app.core.projects.layouts import ClipTailFrameOrigin, LayoutReference, LayoutReviewStatus
from app.core.projects.store import create_project, load_shot, save_project, save_shot
from app.core.schemas import LibraryAsset


class Orchestrator:
    active = False

    @asynccontextmanager
    async def llm_session(self, **kwargs):
        self.active = True
        try:
            yield self
        finally:
            self.active = False

    async def ensure_llm_ready(self):
        assert self.active


def sections(count=9):
    return dict(subject_definitions=" ".join(f"<Picture {i}> grounds object {i}." for i in range(1, count + 1)),
                summary="The watchmaker examines the gear.", retention_analysis="Keep the selected designs.",
                detailed_description="From 0-6 seconds the watchmaker examines the gear and (S1) says <d>[English] Hello.</d>",
                overall_soundscape="Quiet room tone.", non_diegetic_music="No music.")


class Provider:
    def __init__(self, orch, *, count=9, brief=None, rewrite=True, fault=None, mutate=None):
        self.orch, self.count = orch, count
        self.brief, self.rewrite, self.fault, self.mutate = brief, rewrite, fault, mutate
        self.visual = []
        self.text = []

    async def complete_with_images(self, system, user, *, images, guides=()):
        # Missing lease or accidentally resending a full pack is an observable boundary bug.
        assert self.orch.active
        assert len(images) == 1
        self.visual.append((user, images[0]))
        if self.mutate:
            self.mutate("vision", len(self.visual))
        if self.fault == "vision_error":
            raise RuntimeError("vision unavailable")
        if self.fault == "empty":
            return ""
        if self.fault == "truncated":
            return '{"description":"unfinished'
        return json.dumps({"readable": self.fault != "unreadable",
                           "description": f"Observed detail {((len(self.visual)-1) % self.count)+1}",
                           "concerns": []})

    async def complete(self, system, user, *, guides=()):
        assert self.orch.active
        self.text.append((system, user))
        if "reference review decision" in system.lower():
            return json.dumps({"brief": self.brief, "rewrite_prompt": self.rewrite,
                               "reason": "The current images support this decision.",
                               "blocking_question": "Which wardrobe should be retained?" if self.fault == "conflict" else None})
        if self.mutate:
            self.mutate("prompt", len(self.text))
        return json.dumps({"prompt_sections": sections(self.count), "dialogue_uses": [
            {"line_ids": ["l1"], "speaker_id": "char_1", "block_indexes": [0]}]})


class ObservationProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.systems = []

    async def complete_with_images(self, system, user, *, images, guides=()):
        assert len(images) == 1
        self.systems.append(system)
        return self.responses.pop(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [
    json.dumps([{
        "readable": True,
        "description": "Tao faces the camera in a centered head-and-shoulders portrait.",
        "concerns": ["Sunglasses obscure his eyes."],
    }]),
    json.dumps([{"text": json.dumps({
        "readable": True,
        "description": "Tao faces the camera in a centered head-and-shoulders portrait.",
        "concerns": ["Sunglasses obscure his eyes."],
    })}]),
])
async def test_reference_observation_accepts_safe_singleton_wrappers(raw):
    result = await observe_reference(
        ObservationProvider([raw]),
        {"picture_index": 2, "asset_id": "act_tao_face"},
        "encoded-image",
    )

    assert result["description"] == "Tao faces the camera in a centered head-and-shoulders portrait."
    assert result["concerns"] == ["Sunglasses obscure his eyes."]


@pytest.mark.asyncio
async def test_reference_observation_repairs_invalid_structure_once():
    provider = ObservationProvider([
        json.dumps([{"id": "img-001", "role": "assistant", "content": "analysis"}]),
        json.dumps({
            "readable": True,
            "description": "Tao is centered against a plain white background.",
            "concerns": [],
        }),
    ])

    result = await observe_reference(
        provider,
        {"picture_index": 2, "asset_id": "act_tao_face"},
        "encoded-image",
    )

    assert result["description"] == "Tao is centered against a plain white background."
    assert len(provider.systems) == 2
    assert "exactly one JSON object" in provider.systems[1]


@pytest.mark.asyncio
async def test_reference_observation_stops_after_one_structure_repair():
    malformed = json.dumps([{"id": "img-001", "role": "assistant", "content": "analysis"}])
    provider = ObservationProvider([malformed, malformed])

    with pytest.raises(ValueError, match="ReferenceObservation"):
        await observe_reference(
            provider,
            {"picture_index": 2, "asset_id": "act_tao_face"},
            "encoded-image",
        )

    assert len(provider.systems) == 2


class TailHandoffProvider(Provider):
    def __init__(self, orch, *, handoff="Begin from the visible waist-up standing pose; lower the camera as she bends into the floor move.", rewrite=True):
        super().__init__(orch, count=1, rewrite=rewrite)
        self.handoff = handoff

    async def complete_with_images(self, system, user, *, images, guides=()):
        assert self.orch.active
        self.visual.append((user, images[0]))
        return json.dumps({
            "readable": True,
            "description": "The dancer stands upright in a waist-up front view, arms lowered, facing the camera.",
            "concerns": [],
        })

    async def complete(self, system, user, *, guides=()):
        assert self.orch.active
        self.text.append((system, user))
        if "Review a Director Studio tail-frame prompt candidate" in system:
            return json.dumps({"valid": self.handoff is not None,
                               "tail_opening": "Waist-up front view.",
                               "candidate_opening": "Waist-up front view.",
                               "camera_path": self.handoff or "Absent.",
                               "field_checks": {key: {"compatible": True, "evidence": "Fixture field agrees with the evidence."} for key in ("script_beat", "shot_type", "camera_angle", "camera_motion", "composition", "subject_definitions", "summary", "retention_analysis", "detailed_description", "overall_soundscape", "non_diegetic_music")},
            "checks": {key: {"compatible": True, "evidence": "Fixture evidence agrees."}
                                              for key in ("opening_alignment", "transition_path", "reference_roles", "section_consistency")},
                                   "issues": [] if self.handoff else ["Missing credible tail-frame handoff"],
                               "blocking_question": None})
        if "reference review decision" in system.lower():
            decision = {
                "brief": None, "rewrite_prompt": self.rewrite,
                "reason": "The visible pose can lead into the low action with a camera move.",
                "blocking_question": None,
            }
            if self.handoff is not None:
                decision["tail_frame_handoff"] = self.handoff
            return json.dumps(decision)
        return json.dumps({"shot_patch": {}, "blocking_question": None,
                           "reason": "The camera move carries the visible source stance forward.", "prompt_sections": {
            "subject_definitions": "The dancer and studio are grounded by <Picture 1>.",
            "summary": "One continuous dance move in the same studio.",
            "retention_analysis": "The dancer and studio remain consistent.",
            "detailed_description": "0-2 seconds: She bends from standing as the camera lowers. 2-6 seconds: She completes the floor move.",
            "overall_soundscape": "Studio room tone.",
            "non_diegetic_music": "No music.",
        }})


@pytest.fixture
def tail_handoff_shot(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(settings, "library_root", tmp_path / "library")
    monkeypatch.setattr(settings, "jobs_dir", tmp_path / "jobs")
    project = create_project("Dance continuity", "The dancer carries her move into the next shot.")
    asset_dir = settings.library_root / "layouts" / "lay_previous_tail"
    asset_dir.mkdir(parents=True)
    Image.effect_noise((480, 640), 30).convert("RGB").save(asset_dir / "layout.png")
    asset = LibraryAsset(
        id="lay_previous_tail", kind="layouts", name="Previous shot tail",
        pipeline_id="external", job_id="fixture", created_at="2026-09-12T00:00:00Z",
        files={"layout": "layout.png"},
    )
    (asset_dir / "asset.json").write_text(asset.model_dump_json(), encoding="utf-8")
    layout = LayoutReference(
        id="lref_previous_tail", asset_id=asset.id,
        purpose="continue the visible action from the previous shot",
        review_status=LayoutReviewStatus.usable, selected_for_h3=True,
        origin=ClipTailFrameOrigin(
            source_shot_id="sht_previous", source_job_id="job_previous",
            source_generation=1, output_kind="enhanced", output_key="video",
            source_filename="video.mp4",
        ),
    )
    shot = Shot(
        id="sht_continuation", project_id=project.id, scene_id="sc01",
        title="Low-angle continuation", script_beat="Continue the visible stance into a low floor move.",
        camera_angle="low-angle 24mm", camera_motion="camera lowers into the move",
        duration_s=6, layout_refs=[layout],
        refs=[ShotRef(role=RefRole.layout_ref_frame, asset_id=asset.id,
                      file_key="layout", picture_index=1)],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    return project, shot


@pytest.mark.asyncio
async def test_tail_handoff_from_review_is_saved_and_grounded_into_prompt_request(tail_handoff_shot):
    project, shot = tail_handoff_shot
    orch = Orchestrator()
    provider = TailHandoffProvider(orch)

    updated = await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)

    review_request = json.loads(provider.text[0][1])
    assert review_request["selected_layouts"][0]["picture_index"] == 1
    assert review_request["references"][0]["description"].startswith(
        "The dancer stands upright in a waist-up front view"
    )
    assert updated.meta["material_review"]["prompt_review"]["valid"] is True
    assert "camera lowers" in provider.text[1][1]
    assert load_shot(project.id, shot.id) == updated
    assert len(provider.text) == 2  # draft and independent review


@pytest.mark.asyncio
async def test_tail_handoff_review_refreshes_when_shot_camera_changes(tail_handoff_shot):
    project, shot = tail_handoff_shot
    orch = Orchestrator()
    provider = TailHandoffProvider(orch)
    svc = DirectorService(plan_provider=provider, orchestrator=orch)
    first = await svc.write_prompts_after_layout(shot.id)
    save_shot(first.model_copy(update={"camera_angle": "overhead crane angle"}))

    await svc.write_prompts_after_layout(shot.id)

    review_requests = [json.loads(user) for system, user in provider.text
                       if "For this tail-frame continuation return a candidate envelope" in system]
    assert len(review_requests) == 2
    assert review_requests[-1]["original_shot"]["camera_angle"] == "overhead crane angle"
    assert len(provider.visual) == 1  # a camera change does not invalidate image facts


@pytest.mark.asyncio
async def test_tail_handoff_missing_from_review_does_not_save_prompt(tail_handoff_shot):
    project, shot = tail_handoff_shot
    provider = TailHandoffProvider(Orchestrator(), handoff=None)

    with pytest.raises(ValueError, match="tail-frame handoff"):
        await DirectorService(plan_provider=provider, orchestrator=provider.orch).write_prompts_after_layout(shot.id)

    stored = load_shot(project.id, shot.id)
    assert stored.prompt_sections == shot.prompt_sections
    assert stored.refs == shot.refs
    assert stored.meta["material_review_pending"] is True


@pytest.mark.asyncio
async def test_new_tail_handoff_rewrites_old_prompt_even_when_review_says_keep(tail_handoff_shot):
    _, shot = tail_handoff_shot
    shot = shot.model_copy(update={"prompt_sections": PromptSections(
        subject_definitions="<Picture 1> supplies wardrobe and hair.",
        summary="A dance move in the same studio.",
        retention_analysis="The wardrobe stays the same.",
        detailed_description="0-6 seconds: The dancer starts already crouching and rises.",
        overall_soundscape="Studio room tone.", non_diegetic_music="No music.",
    )})
    save_shot(shot)
    orch = Orchestrator()
    provider = TailHandoffProvider(orch, rewrite=False)

    await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)

    assert len(provider.text) == 2
    assert "waist-up front view" in provider.text[1][1]


@pytest.fixture
def material_shot(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(settings, "library_root", tmp_path / "library")
    monkeypatch.setattr(settings, "jobs_dir", tmp_path / "jobs")
    project = create_project("Material review", "The watchmaker examines the gear.")
    refs, files = [], []
    for i in range(1, 10):
        adir = settings.library_root / "props" / f"prop_review_{i}"
        adir.mkdir(parents=True)
        path = adir / "selected.png"
        Image.effect_noise((640, 480), 30).convert("RGB").save(path)
        # A valid alternative must never substitute for a missing explicit file key.
        Image.effect_noise((640, 480), 60).convert("RGB").save(adir / "other.png")
        asset = LibraryAsset(id=f"prop_review_{i}", kind="props", name=f"Gear {i}",
                             pipeline_id="external", job_id="fixture", created_at="2026-09-12T00:00:00Z",
                             files={"master": "other.png", "selected": "selected.png"},
                             notes="Approved gear design")
        (adir / "asset.json").write_text(asset.model_dump_json(), encoding="utf-8")
        refs.append(ShotRef(role=RefRole.prop, asset_id=asset.id, file_key="selected", picture_index=i))
        files.append(path)
    shot = Shot(id="sht_review", project_id=project.id, scene_id="sc01", title="Inspect",
                script_beat="The watchmaker examines the gear.", duration_s=6, dialogue=["Hello."],
                refs=refs, prompt_sections=PromptSections(**sections()),
                meta={"material_review_pending": True, "material_changes": {"reordered": [1]}})
    from app.core.projects.dialogue import apply_dialogue_update
    from app.core.h3.dialogue_binding import DialoguePromptDraft, annotate_speakers
    from app.agents.director.dialogue_preflight import prompt_dialogue_record
    from test_director_dialogue_attribution import line_payload
    payload = {**line_payload(), "speaker_name": "watchmaker"}
    shot = apply_dialogue_update(shot, {"dialogue_lines": [payload]})
    draft = DialoguePromptDraft(prompt_sections=shot.prompt_sections, dialogue_uses=[
        {"line_ids": ["l1"], "speaker_id": "char_1", "block_indexes": [0]}])
    shot = shot.model_copy(update={"prompt_sections": annotate_speakers(draft, shot.dialogue_lines)})
    record = prompt_dialogue_record(project, shot, shot.dialogue_lines, draft)
    shot = shot.model_copy(update={"meta": {**shot.meta, "prompt_dialogue_contract": record}})
    neighbor = shot.model_copy(update={"id": "sht_neighbor", "title": "Untouched"}, deep=True)
    save_shot(shot)
    save_shot(neighbor)
    project = project.model_copy(update={"shot_ids": [shot.id, neighbor.id]})
    save_project(project)
    return project, shot, neighbor, files


@pytest.mark.asyncio
async def test_material_review_cannot_rewrite_authored_brief(material_shot):
    project, shot, neighbor, files = material_shot
    orch = Orchestrator()
    provider = Provider(orch, brief="The watchmaker examines the brass gear on the table.")
    updated = await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    assert len(provider.visual) == 9
    for i, (user, encoded) in enumerate(provider.visual, 1):
        assert f"Picture {i}" in user
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as thumbnail:
            assert max(thumbnail.size) <= 768
    assert all(f"Observed detail {i}" in provider.text[0][1] for i in range(1, 10))
    assert "brass gear" not in provider.text[-1][1]
    assert updated.script_beat == shot.script_beat
    assert updated.meta["material_review"]["decision"]["brief"] is None
    assert updated.meta["material_review_pending"] is False
    assert len(updated.meta["material_review"]["references"]) == 9
    assert updated.refs == shot.refs and updated.dialogue == shot.dialogue
    assert load_shot(project.id, neighbor.id) == neighbor
    assert load_shot(project.id, shot.id) == updated
    assert not orch.active


@pytest.mark.asyncio
async def test_review_and_writer_receive_shot_authoring_source_and_current_request(material_shot):
    from app.core.projects.chat_history import append_chat_message
    project, shot, _, _ = material_shot
    message = append_chat_message(project.id, role="user", content="Add a shot of the creature jumping around the lounge.")
    shot = shot.model_copy(update={"script_beat": "The creature jumps around the lounge.",
        "meta": {**shot.meta, "dialogue_authoring": {"user_message_id": message.id,
            "user_message": message.content}}})
    save_shot(shot)
    orch = Orchestrator()
    provider = Provider(orch)
    await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(
        shot.id, revision_request="Keep the creature's identity; write its prompt.")
    request = json.loads(provider.text[0][1])
    assert request["intent"]["authoring_request"] == {
        "source_message_id": message.id, "text": message.content}
    assert request["intent"]["current_request"] == "Keep the creature's identity; write its prompt."
    assert request["shot"]["brief"] == "The creature jumps around the lounge."
    assert message.content in provider.text[-1][1]


@pytest.mark.asyncio
async def test_review_does_not_promote_unverified_authoring_metadata(material_shot):
    project, shot, _, _ = material_shot
    shot.meta["dialogue_authoring"] = {"user_message_id": "missing", "user_message": "Invented approval"}
    save_shot(shot)
    orch = Orchestrator()
    provider = Provider(orch)
    await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    request = json.loads(provider.text[0][1])
    assert request["intent"]["authoring_request"] is None
    assert "Invented approval" not in provider.text[0][1]


@pytest.mark.asyncio
async def test_prompt_progress_is_visible_before_inference_and_reports_elapsed(material_shot):
    _, shot, _, _ = material_shot
    events = []
    async def progress(event):
        events.append(event)
    orch = Orchestrator()
    provider = Provider(orch)
    original = provider.complete_with_images
    async def inspect(*args, **kwargs):
        assert events[-1]["phase"] == "reference_observation"
        assert events[-1]["state"] == "started"
        return await original(*args, **kwargs)
    provider.complete_with_images = inspect
    await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(
        shot.id, on_progress=progress)
    completed = [e for e in events if e.get("state") == "completed"]
    assert [e["phase"] for e in completed] == ["reference_observation"] * 9 + ["material_review", "prompt_writing"]
    assert all(e["elapsed_s"] >= 0 and "s" in e["text"] for e in completed)


@pytest.mark.asyncio
async def test_harness_forwards_material_phase_events_to_chat(material_shot):
    from app.agents.director.harness_runtime import BackendTurn
    from app.agents.director.context_io import save_agent_context
    from app.agents.director.service import _script_hash
    from app.core.projects.models import AgentContext
    project, shot, _, _ = material_shot
    save_agent_context(project.id, AgentContext(project_id=project.id, script_hash=_script_hash(project.script_text)))
    events = []
    async def progress(event):
        events.append(event)
    orch = Orchestrator()
    turn = BackendTurn(project.id, "Write the prompt", DirectorService(plan_provider=Provider(orch),
        orchestrator=orch), None, on_progress=progress)
    await turn.dispatch("context", {})
    result = await turn.dispatch("tool", {"call_id": "write", "name": "write_prompt", "arguments": {"shot_id": shot.id}})
    assert result["ok"], result
    assert any(e.get("phase") == "reference_observation" for e in events)
    assert any(e.get("phase") == "prompt_writing" and e["state"] == "completed" for e in events)


@pytest.mark.asyncio
async def test_material_review_receives_current_confirmed_project_decisions(material_shot):
    from app.agents.director.service import _script_hash

    project, shot, _, _ = material_shot
    review = AssetCoverageReview(
        script_hash=_script_hash(project.script_text),
        status="reviewed",
        notes="The reference images are authoritative; use the bright coastal scene.",
        recommendations=[
            AssetCoverageRecommendation(
                kind="scene",
                needed_variant="bright coastal scene",
                reason="User confirmed this look.",
                resolution="accepted",
            ),
            AssetCoverageRecommendation(
                kind="prop",
                needed_variant="optional spare gear",
                reason="Not decided yet.",
                resolution="pending",
            ),
        ],
    )
    save_project(project.model_copy(update={"asset_coverage_review": review}))
    orch = Orchestrator()
    provider = Provider(orch)

    await DirectorService(
        plan_provider=provider, orchestrator=orch
    ).write_prompts_after_layout(shot.id)

    system, user = provider.text[0]
    payload = json.loads(user)
    assert payload["confirmed_project_review"]["notes"] == review.notes
    assert payload["confirmed_project_review"]["recommendations"] == [
        review.recommendations[0].model_dump(mode="json")
    ]
    assert "do not reopen" in system.lower()


@pytest.mark.asyncio
async def test_first_prompt_reviews_refs_without_pending_flag_and_reuses_evidence(material_shot):
    project, shot, _, _ = material_shot
    shot = shot.model_copy(update={"meta": {}, "prompt_sections": PromptSections()})
    save_shot(shot)
    orch = Orchestrator()
    provider = Provider(orch)
    svc = DirectorService(plan_provider=provider, orchestrator=orch)
    first = await svc.write_prompts_after_layout(shot.id)
    assert len(first.meta["material_review"]["references"]) == 9
    await svc.write_prompts_after_layout(shot.id)
    assert len(provider.visual) == 9


@pytest.mark.asyncio
async def test_legacy_material_decision_rechecks_configuration_once(material_shot):
    project, shot, _, _ = material_shot
    orch = Orchestrator()
    provider = Provider(orch, rewrite=False)
    svc = DirectorService(plan_provider=provider, orchestrator=orch)
    current = await svc.write_prompts_after_layout(shot.id)
    current.meta["material_review"]["decision"].pop("configuration_issues")
    save_shot(current)
    def review_calls():
        return sum("reference review decision" in system.lower() for system, _ in provider.text)
    assert review_calls() == 1
    refreshed = await svc.write_prompts_after_layout(shot.id)
    assert refreshed.meta["material_review"]["decision"]["configuration_issues"] == []
    assert review_calls() == 2
    await svc.write_prompts_after_layout(shot.id)
    assert review_calls() == 2
    assert len(provider.visual) == 9


@pytest.mark.asyncio
async def test_inspect_library_asset_before_planning_is_read_only(material_shot):
    project, shot, _, _ = material_shot
    empty_project = create_project("No storyboard yet", "")
    orch = Orchestrator()
    provider = Provider(orch)
    svc = DirectorService(plan_provider=provider, orchestrator=orch)
    from app.agents.director.harness_runtime import BackendTurn
    turn = BackendTurn(empty_project.id, "Inspect the gear before planning", svc, None)
    await turn.dispatch("context", {})
    result = await turn.dispatch("tool", {"call_id": "inspect", "name": "inspect_asset",
        "arguments": {"asset_id": "prop_review_1", "file_key": "selected"}})
    assert result["ok"], result
    assert result["observation"]["description"] == "Observed detail 1"
    assert result["observation"]["file_key"] == "selected"
    assert result["observation"]["content_sha256"]
    assert load_shot(project.id, shot.id) == shot
    from app.core.projects.store import list_shots, load_project
    assert list_shots(empty_project.id) == []
    assert load_project(empty_project.id) == empty_project
    missing = await turn.dispatch("tool", {"call_id": "missing", "name": "inspect_asset",
        "arguments": {"asset_id": "prop_review_1", "file_key": "missing"}})
    assert not missing["ok"]


@pytest.mark.asyncio
@pytest.mark.parametrize("external", [False, True])
async def test_inspect_layout_reads_actual_image_without_mutating_shot(material_shot, external):
    import hashlib
    from app.core.library.store import write_asset
    project, shot, _, files = material_shot
    asset = write_asset(LibraryAsset(
        id="lay_current", kind="layouts", name="Current Layout", project_id=project.id,
        pipeline_id="external" if external else "qwen21_layout", job_id="fixture",
        created_at="2026-10-03T00:00:00Z", files={"layout": str(files[0])},
        meta={"external": external},
    ))
    orch = Orchestrator()
    svc = DirectorService(plan_provider=Provider(orch), orchestrator=orch)
    observation = await svc.inspect_asset(project.id, asset.id, "layout")
    assert observation["asset_id"] == "lay_current"
    assert observation["file_key"] == "layout"
    assert observation["content_sha256"] == hashlib.sha256(files[0].read_bytes()).hexdigest()
    assert observation["description"] == "Observed detail 1"
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_inspect_layout_rejects_another_projects_image(material_shot):
    from app.core.library.store import write_asset
    project, _, _, files = material_shot
    foreign = create_project("Foreign", "Unrelated")
    write_asset(LibraryAsset(
        id="lay_foreign", kind="layouts", name="Foreign Layout", project_id=foreign.id,
        pipeline_id="external", job_id="fixture", created_at="2026-10-03T00:00:00Z",
        files={"layout": str(files[0])}, meta={"external": True},
    ))
    orch = Orchestrator()
    svc = DirectorService(plan_provider=Provider(orch), orchestrator=orch)
    with pytest.raises(ValueError, match="not in this project's inventory"):
        await svc.inspect_asset(project.id, "lay_foreign", "layout")


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["empty", "truncated", "unreadable", "vision_error", "conflict", "missing_file"])
async def test_incomplete_review_preserves_old_brief_prompt_and_pending(material_shot, fault):
    project, shot, _, files = material_shot
    if fault == "missing_file":
        files[-1].unlink()
    orch = Orchestrator()
    provider = Provider(orch, fault=fault)
    with pytest.raises((ValueError, RuntimeError)):
        await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    assert load_shot(project.id, shot.id) == shot
    assert not orch.active


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_during_inspection", [False, True])
async def test_inspection_can_read_same_file_again_after_content_change(material_shot, changed_during_inspection):
    project, _, _, files = material_shot
    from app.agents.director.harness_runtime import BackendTurn
    def change(*_):
        Image.effect_noise((640, 480), 75).convert("RGB").save(files[0])
    orch = Orchestrator()
    provider = Provider(orch, mutate=change if changed_during_inspection else None)
    turn = BackendTurn(project.id, "Inspect current image", DirectorService(plan_provider=provider, orchestrator=orch), None)
    await turn.dispatch("context", {})
    args = {"asset_id": "prop_review_1", "file_key": "selected"}
    first = await turn.dispatch("tool", {"call_id": "first", "name": "inspect_asset", "arguments": args})
    assert first["ok"] is not changed_during_inspection
    provider.mutate = None
    change()
    second = await turn.dispatch("tool", {"call_id": "second", "name": "inspect_asset", "arguments": args})
    assert second["ok"], second
    assert len(provider.visual) == 2
    replay = await turn.dispatch("tool", {"call_id": "second", "name": "inspect_asset", "arguments": args})
    assert not replay["ok"]


@pytest.mark.asyncio
async def test_review_can_preserve_brief_and_valid_prompt(material_shot):
    project, shot, _, _ = material_shot
    orch = Orchestrator()
    provider = Provider(orch, rewrite=False)
    updated = await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    assert len(provider.visual) == 9
    assert len(provider.text) == 1  # review decision only; no needless regeneration
    assert updated.script_beat == shot.script_beat
    assert updated.prompt_sections == shot.prompt_sections
    assert not updated.meta["material_review_pending"]


def music_review_inputs(material_shot, monkeypatch):
    from app.core.projects.models import ProjectMode, ProjectMusicMaster, ShotMusicSegment
    from app.core.projects.song_segments import SongSegment, SongSegmentsDocument
    from app.core.media import music_segments
    project, shot, _, _ = material_shot
    project = project.model_copy(update={"mode": ProjectMode.mv, "music_master": ProjectMusicMaster(
        filename="song.wav", relative_path="music/master.wav", duration_s=30,
        content_sha256="a" * 64, source_format="wav")})
    save_project(project)
    shot = shot.model_copy(update={"dialogue_lines": None, "music_segment": ShotMusicSegment(
        core_start_s=0, core_end_s=6, submit_start_s=0, submit_end_s=6)})
    save_shot(shot)
    document = SongSegmentsDocument(revision=1, master_sha256="a" * 64, segments=[
        SongSegment(id="line", start_s=0, end_s=5, text="Hello.")])
    monkeypatch.setattr(music_segments, "load_segments", lambda _: document)
    return project, shot, document


class MusicReviewProvider(Provider):
    def __init__(self, orch, document):
        super().__init__(orch, rewrite=False)
        self.document = document
        self.edit_during_write = False

    async def complete(self, system, user, *, guides=()):
        if "reference review decision" in system.lower():
            return await super().complete(system, user, guides=guides)
        self.text.append((system, user))
        result = sections()
        start = self.document.segments[0].start_s
        result["detailed_description"] = f"From {start}-5 seconds the watchmaker sings <d>[English] Hello.</d> in sync with <Audio 1>."
        result["overall_soundscape"] = "Original song from <Audio 1>."
        if self.edit_during_write:
            self.document.segments[0].start_s = 0.5
        return json.dumps(result)


@pytest.mark.asyncio
async def test_changed_lyric_timing_overrides_review_preserve_advice(material_shot, monkeypatch):
    from app.core.media.music_segments import music_prompt_signature
    project, shot, document = music_review_inputs(material_shot, monkeypatch)
    provider = MusicReviewProvider(Orchestrator(), document)
    svc = DirectorService(plan_provider=provider, orchestrator=provider.orch)
    first = await svc.write_prompts_after_layout(shot.id)
    count = len(provider.text)
    document.segments[0].start_s = 0.5
    updated = await svc.write_prompts_after_layout(shot.id)
    assert len(provider.text) == count + 1
    assert '"clip_start_s": 0.5' in provider.text[-1][1]
    assert "0.5-5 seconds" in updated.prompt_sections.detailed_description
    assert updated.meta["prompt_music_signature"] == music_prompt_signature(project, updated)
    assert updated.prompt_sections != first.prompt_sections


@pytest.mark.asyncio
async def test_lyric_edit_during_writing_does_not_certify_stale_prompt(material_shot, monkeypatch):
    _, shot, document = music_review_inputs(material_shot, monkeypatch)
    provider = MusicReviewProvider(Orchestrator(), document)
    svc = DirectorService(plan_provider=provider, orchestrator=provider.orch)
    first = await svc.write_prompts_after_layout(shot.id)
    provider.edit_during_write = True
    with pytest.raises(ValueError, match="[Ss]ong|[Mm]usic"):
        await svc.write_prompts_after_layout(shot.id, revision_request="Use a steady camera.")
    assert load_shot(shot.project_id, shot.id).prompt_sections == first.prompt_sections


@pytest.mark.asyncio
async def test_tail_lyric_edit_during_writing_does_not_certify_stale_prompt(tail_handoff_shot, monkeypatch):
    from app.core.projects.models import ProjectMode, ProjectMusicMaster, ShotMusicSegment
    from app.core.projects.song_segments import SongSegment, SongSegmentsDocument
    from app.core.media import music_segments
    project, shot = tail_handoff_shot
    project = project.model_copy(update={"mode": ProjectMode.mv, "music_master": ProjectMusicMaster(
        filename="song.wav", relative_path="music/master.wav", duration_s=30,
        content_sha256="a" * 64, source_format="wav")})
    save_project(project)
    shot = shot.model_copy(update={"music_segment": ShotMusicSegment(
        core_start_s=0, core_end_s=6, submit_start_s=0, submit_end_s=6)})
    save_shot(shot)
    document = SongSegmentsDocument(revision=1, master_sha256="a" * 64, segments=[
        SongSegment(id="line", start_s=0, end_s=5, text="Hello.")])
    monkeypatch.setattr(music_segments, "load_segments", lambda _: document)
    class EditingProvider(TailHandoffProvider):
        async def complete(self, *args, **kwargs):
            raw = await super().complete(*args, **kwargs)
            payload = json.loads(raw)
            if "prompt_sections" in payload:
                payload["prompt_sections"]["overall_soundscape"] = "Original music from <Audio 1>."
                document.segments[0].start_s = 0.5
            return json.dumps(payload)
    provider = EditingProvider(Orchestrator())
    with pytest.raises(ValueError, match="[Ss]ong|[Mm]usic"):
        await DirectorService(plan_provider=provider, orchestrator=provider.orch).write_prompts_after_layout(shot.id)
    assert load_shot(project.id, shot.id).prompt_sections == shot.prompt_sections


@pytest.mark.asyncio
async def test_explicit_prompt_revision_overrides_review_preserve_advice(material_shot):
    _, shot, _, _ = material_shot
    orch = Orchestrator()
    class RevisionProvider(Provider):
        async def complete(self, system, user, *, guides=()):
            response = await super().complete(system, user, guides=guides)
            payload = json.loads(response)
            if "prompt_sections" in payload:
                payload["prompt_sections"]["summary"] = "The gear turns slowly in the watchmaker's hands."
            return json.dumps(payload)
    provider = RevisionProvider(orch, rewrite=False)
    updated = await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(
        shot.id, revision_request="Make the gear turn slowly."
    )
    assert updated.prompt_sections.summary == "The gear turns slowly in the watchmaker's hands."
    assert updated.script_beat == shot.script_beat


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", [False, True])
async def test_storyboard_validator_receives_only_current_visual_evidence(material_shot, stale):
    _, _, _, files = material_shot
    from app.agents.director.service import _script_hash
    project = create_project("Observed props", "A gear rests on a table.")
    orch = Orchestrator()
    provider = Provider(orch)
    svc = DirectorService(plan_provider=provider, orchestrator=orch)
    await svc.inspect_asset(project.id, "prop_review_1", "selected")
    if stale:
        Image.effect_noise((640, 480), 75).convert("RGB").save(files[0])
    async def validate(system, user, **kwargs):
        provider.text.append((system, user))
        return '{"valid":true,"issues":[]}'
    provider.complete = validate
    draft = dict(scene_id="room", title="Gear", script_beat=project.script_text,
                 shot_type="close-up", camera_angle="eye level", camera_motion="locked-off",
                 composition="gear centered", duration_s=6, dialogue=[],
                 asset_matches=[dict(role="prop", asset_id="prop_review_1", file_key="selected", picture_index=1)])
    await svc.save_storyboard(project.id, [draft], _script_hash(project.script_text))
    assert ("Observed detail 1" in provider.text[-1][1]) is not stale


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["vision", "prompt"])
async def test_ref_change_during_review_or_prompt_never_overwrites_user_edit(material_shot, when):
    project, shot, _, _ = material_shot
    edited = shot.model_copy(update={"refs": shot.refs[:-1], "feedback": "User changed refs again"})
    def mutate(phase, count):
        if phase == when:
            save_shot(edited)
    orch = Orchestrator()
    provider = Provider(orch, mutate=mutate)
    with pytest.raises(ValueError, match="changed"):
        await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    assert load_shot(project.id, shot.id) == edited


@pytest.mark.asyncio
async def test_confirmed_choices_changed_during_review_rejects_stale_prompt(material_shot):
    from app.agents.director.service import _script_hash
    project, shot, _, _ = material_shot
    def mutate(phase, count):
        if phase == "prompt":
            save_project(project.model_copy(update={"asset_coverage_review": AssetCoverageReview(
                script_hash=_script_hash(project.script_text), status="reviewed", notes="Use the new exterior scene.")}))
    orch = Orchestrator()
    with pytest.raises(ValueError, match="changed"):
        await DirectorService(plan_provider=Provider(orch, mutate=mutate), orchestrator=orch).write_prompts_after_layout(shot.id)
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_same_asset_file_replacement_invalidates_review(material_shot):
    project, shot, _, files = material_shot
    orch = Orchestrator()
    provider = Provider(orch)
    svc = DirectorService(plan_provider=provider, orchestrator=orch)
    first = await svc.write_prompts_after_layout(shot.id)
    Image.effect_noise((640, 480), 70).convert("RGB").save(files[0])
    second = await svc.write_prompts_after_layout(shot.id)
    assert len(provider.visual) == 10  # Only changed bytes need new vision; decision still sees all nine.
    assert "Picture 1" in provider.visual[-1][0]
    assert first.meta["material_review"]["signature"] != second.meta["material_review"]["signature"]


@pytest.mark.asyncio
async def test_file_replaced_during_prompt_is_not_marked_reviewed(material_shot):
    project, shot, _, files = material_shot
    def mutate(phase, count):
        if phase == "prompt":
            Image.effect_noise((640, 480), 75).convert("RGB").save(files[0])
    orch = Orchestrator()
    provider = Provider(orch, mutate=mutate)
    with pytest.raises(ValueError, match="changed"):
        await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_harness_receives_review_failure_not_success(material_shot):
    from app.agents.director.harness_runtime import BackendTurn
    from app.agents.director.context_io import save_agent_context
    from app.agents.director.service import _script_hash
    from app.core.projects.models import AgentContext
    project, shot, _, _ = material_shot
    save_agent_context(project.id, AgentContext(project_id=project.id, script_hash=_script_hash(project.script_text)))
    orch = Orchestrator()
    svc = DirectorService(plan_provider=Provider(orch, fault="unreadable"), orchestrator=orch)
    turn = BackendTurn(project.id, "Review shot 1 refs and rewrite its prompt", svc, None)
    await turn.dispatch("context", {})
    result = await turn.dispatch("tool", {"call_id": "review", "name": "write_prompt", "arguments": {"shot_id": shot.id}})
    assert result["ok"] is False
    assert "Picture 1" in result["error"]
    assert not turn.actions
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_failed_prompt_preserves_both_raw_drafts_and_validation_errors(material_shot):
    project, shot, _, _ = material_shot
    orch = Orchestrator()
    provider = Provider(orch)
    original_complete = provider.complete
    drafts = ['{"subject_definitions":"initial draft"}', '{"subject_definitions":"repair draft"}']

    async def fail_prompt(system, user, *, guides=()):
        if "reference review decision" in system.lower():
            return await original_complete(system, user, guides=guides)
        return drafts.pop(0)

    provider.complete = fail_prompt
    with pytest.raises(ValueError, match="prompt section 'summary' missing or empty"):
        await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)

    debug_dir = settings.projects_dir / project.id / "agent" / "prompt_failures"
    records = list(debug_dir.glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["project_id"] == project.id
    assert record["shot_id"] == shot.id
    assert record["attempts"] == [
        {"stage": "initial", "raw": '{"subject_definitions":"initial draft"}',
         "error": "prompt section 'summary' missing or empty"},
        {"stage": "repair", "raw": '{"subject_definitions":"repair draft"}',
         "error": "prompt section 'summary' missing or empty"},
    ]
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_retry_repairs_saved_candidate_without_losing_other_sections(material_shot):
    project, shot, _, _ = material_shot
    orch = Orchestrator()
    provider = Provider(orch)
    original_complete = provider.complete
    broken = sections()
    broken["detailed_description"] = "0-6 seconds: Waves without speaking."
    uses = [{"line_ids": ["l1"], "speaker_id": "char_1", "block_indexes": [0]}]
    drafts = [json.dumps({"prompt_sections": broken, "dialogue_uses": uses}),
              json.dumps({"prompt_sections": {"detailed_description": "0-6 seconds: Still silent."}, "dialogue_uses": uses})]

    async def fail(system, user, *, guides=()):
        if "reference review decision" in system.lower():
            return await original_complete(system, user, guides=guides)
        return drafts.pop(0)

    provider.complete = fail
    with pytest.raises(ValueError, match="dialogue"):
        await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)

    seen = []
    async def repair(system, user, *, guides=()):
        if "reference review decision" in system.lower():
            return await original_complete(system, user, guides=guides)
        seen.append(user)
        assert "Still silent" in user
        return json.dumps({"prompt_sections": {"detailed_description": sections()["detailed_description"]}, "dialogue_uses": uses})

    provider.complete = repair
    updated = await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    assert len(seen) == 1
    assert updated.prompt_sections.subject_definitions == (
        broken["subject_definitions"] + "\nSpeaker identities: S1 is watchmaker.")
    assert "the watchmaker examines the gear" in updated.prompt_sections.detailed_description
    assert updated.prompt_sections.detailed_description.endswith("<d>[English] Hello.</d>")
    assert load_shot(project.id, shot.id) == updated


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unreadable", "missing", "cached_midflight"])
async def test_replacement_failure_reinstates_pending_flag(material_shot, failure):
    project, shot, _, files = material_shot
    orch = Orchestrator()
    provider = Provider(orch)
    svc = DirectorService(plan_provider=provider, orchestrator=orch)
    first = await svc.write_prompts_after_layout(shot.id)
    if failure == "missing":
        files[0].unlink()
    elif failure == "cached_midflight":
        def mutate(phase, count):
            if phase == "prompt":
                Image.effect_noise((640, 480), 75).convert("RGB").save(files[0])
        provider.mutate = mutate
    else:
        Image.effect_noise((640, 480), 75).convert("RGB").save(files[0])
        provider.fault = "unreadable"
    with pytest.raises(ValueError):
        await svc.write_prompts_after_layout(shot.id)
    stored = load_shot(project.id, shot.id)
    assert stored.meta["material_review_pending"] is True
    assert stored.script_beat == first.script_beat and stored.prompt_sections == first.prompt_sections


@pytest.mark.asyncio
async def test_missing_binding_forces_rewrite_even_when_model_says_keep(material_shot):
    _, shot, _, _ = material_shot
    shot.prompt_sections.subject_definitions = "Only <Picture 1> is mentioned."
    save_shot(shot)
    orch = Orchestrator()
    provider = Provider(orch, rewrite=False)
    updated = await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    assert "<Picture 9>" in updated.prompt_sections.subject_definitions
    assert len(provider.text) == 2


@pytest.mark.asyncio
async def test_material_review_cannot_retire_video_by_proposing_a_different_brief(material_shot):
    from app.core.projects.models import ShotStatus
    _, shot, _, _ = material_shot
    shot = shot.model_copy(update={"status": ShotStatus.succeeded, "h3_job_id": "old_completed"})
    save_shot(shot)
    orch = Orchestrator()
    provider = Provider(orch, brief="The watchmaker examines the brass gear.")
    updated = await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    assert updated.script_beat == shot.script_beat
    assert updated.status == ShotStatus.succeeded
    assert updated.h3_job_id == "old_completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "running"])
async def test_does_not_rewrite_brief_while_video_job_active(material_shot, status):
    from app.core.projects.models import ShotStatus
    project, shot, _, _ = material_shot
    shot = shot.model_copy(update={"status": ShotStatus(status), "h3_job_id": "active_video"})
    save_shot(shot)
    orch = Orchestrator()
    provider = Provider(orch, brief="The watchmaker examines the brass gear.")
    with pytest.raises(ValueError, match="active"):
        await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    assert load_shot(project.id, shot.id) == shot
