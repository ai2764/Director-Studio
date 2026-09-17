"""Single-shot material review: real files/storage, only inference is a fixture."""
import base64
import io
import json
from contextlib import asynccontextmanager

import pytest
from PIL import Image

from app.config import settings
from app.agents.director.service import DirectorService
from app.core.projects.models import (
    AssetCoverageRecommendation,
    AssetCoverageReview,
    PromptSections,
    RefRole,
    Shot,
    ShotRef,
)
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
        return json.dumps(sections(self.count))


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
    neighbor = shot.model_copy(update={"id": "sht_neighbor", "title": "Untouched"}, deep=True)
    save_shot(shot)
    save_shot(neighbor)
    save_project(project.model_copy(update={"shot_ids": [shot.id, neighbor.id]}))
    return project, shot, neighbor, files


@pytest.mark.asyncio
async def test_all_nine_refs_reviewed_before_brief_and_prompt_save(material_shot):
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
    assert "brass gear" in provider.text[-1][1]
    assert updated.script_beat.endswith("brass gear on the table.")
    assert updated.meta["material_review_pending"] is False
    assert len(updated.meta["material_review"]["references"]) == 9
    assert updated.refs == shot.refs and updated.dialogue == shot.dialogue
    assert load_shot(project.id, neighbor.id) == neighbor
    assert load_shot(project.id, shot.id) == updated
    assert not orch.active


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
async def test_same_asset_file_replacement_invalidates_review(material_shot):
    project, shot, _, files = material_shot
    orch = Orchestrator()
    provider = Provider(orch)
    svc = DirectorService(plan_provider=provider, orchestrator=orch)
    first = await svc.write_prompts_after_layout(shot.id)
    Image.effect_noise((640, 480), 70).convert("RGB").save(files[0])
    second = await svc.write_prompts_after_layout(shot.id)
    assert len(provider.visual) == 18  # whole current pack, not just the changed image
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
async def test_changed_brief_archives_previous_completed_video(material_shot):
    from app.core.projects.models import ShotStatus
    _, shot, _, _ = material_shot
    shot = shot.model_copy(update={"status": ShotStatus.succeeded, "h3_job_id": "old_completed"})
    save_shot(shot)
    orch = Orchestrator()
    provider = Provider(orch, brief="The watchmaker examines the brass gear.")
    updated = await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    assert updated.status == ShotStatus.needs_review
    assert updated.h3_job_id is None
    assert "old_completed" in updated.meta["superseded_h3_job_ids"]


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
