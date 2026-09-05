"""Director agent: plan, context persistence, reference-frame queue, prompt write."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, NamedTuple
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.core.projects import LayoutBrief, LayoutSourceRef, RefRole
from app.core.projects.models import AgentContext, PromptSections, ShotStatus
from app.core.projects.store import (
    create_project,
    list_shots,
    load_project,
    load_shot,
    save_project,
    save_shot,
)
from app.core.projects.models import Shot
from app.core.schemas import JobRecord, LibraryAsset


CAMERA_DRAFT = {
    "shot_type": "medium shot",
    "camera_angle": "eye level on the action axis",
    "camera_motion": "locked-off",
    "composition": "primary subject centered with clear action geography",
}


def _visual_result():
    from app.agents.director.visual_direction import (
        VisualBrief,
        VisualDirectionResult,
    )

    brief = VisualBrief.model_validate(
        {
            "shot_type": "wide shot",
            "camera": "eye level",
            "scene_lock": ["preserve corridor"],
            "characters": [
                {
                    "reference_image": "Image1",
                    "frame_position": "center",
                    "body_angle": "front three-quarter",
                    "head_direction": "toward the door",
                    "pose": "walking",
                    "interaction": "none",
                    "identity_lock": ["same face"],
                    "wardrobe_lock": ["same clothes"],
                }
            ],
            "forbidden": ["duplicate person"],
        }
    )
    return VisualDirectionResult(
        brief=brief,
        compiled_prompt="COMPILED VISUAL PROMPT",
        selected_refs=[
            {"image": "Image1", "file_key": "ref_0", "caption": "Image1 ACTOR master"}
        ],
        vision_input_captions=["Image1 ACTOR master"],
        vision_images_b64=["abc"],
    )


class PlanCall(NamedTuple):
    system: str
    user: str
    guides: tuple[str, ...]


class FakePlanProvider:
    """Injectable plan provider returning canned JSON."""

    def __init__(self, responses: list[str] | None = None, response: str | None = None) -> None:
        if responses is not None:
            self._responses = list(responses)
        elif response is not None:
            self._responses = [response]
        else:
            self._responses = []
        self.calls: list[PlanCall] = []

    async def complete(
        self,
        system: str,
        user: str,
        *,
        guides: Iterable[str] = (),
    ) -> str:
        self.calls.append(PlanCall(system, user, tuple(guides)))
        if not self._responses:
            raise RuntimeError("FakePlanProvider exhausted")
        return self._responses.pop(0)


class RecordingOrchestrator:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.owner = None
        self._llm_ready = False

    async def release_llm(self) -> None:
        self.calls.append(("release_llm",))
        self._llm_ready = False
        self.owner = None

    async def ensure_llm_ready(self) -> None:
        self.calls.append(("ensure_llm_ready",))
        self._llm_ready = True

    async def before_comfy_job(self, pipeline_id: str) -> None:
        self.calls.append(("before_comfy", pipeline_id))
        self.owner = "comfy"

    class _Sess:
        def __init__(self, orch: "RecordingOrchestrator", release_on_exit: bool) -> None:
            self.orch = orch
            self.release_on_exit = release_on_exit

        async def __aenter__(self):
            self.orch.calls.append(("llm_session_enter",))
            self.orch.owner = "llm"
            return self.orch

        async def __aexit__(self, *args):
            self.orch.calls.append(("llm_session_exit",))
            if self.release_on_exit:
                await self.orch.release_llm()

    def llm_session(self, *, release_on_exit: bool = True):
        return self._Sess(self, release_on_exit)


def _one_shot_plan_json(
    *,
    asset_id: str = "act_testasset01",
    title: str = "Cafe open",
) -> str:
    return json.dumps(
        [
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": title,
                "script_beat": "Actor walks into cafe",
                "duration_s": 8.0,
                "dialogue": ["Hello there."],
                "asset_matches": [{"role": "actor", "asset_id": asset_id}],
            }
        ]
    )


def _seed_actor_asset(library_root: Path, asset_id: str = "act_testasset01") -> LibraryAsset:
    adir = library_root / "actors" / asset_id
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "master.png").write_bytes(b"fake-png-bytes")
    asset = LibraryAsset(
        id=asset_id,
        kind="actors",
        name="Test Actor",
        notes="hero",
        pipeline_id="actor",
        job_id="job_seed",
        created_at="2026-01-01T00:00:00+00:00",
        files={"master": "master.png"},
        meta={"tags": ["hero", "cafe"]},
    )
    (adir / "asset.json").write_text(asset.model_dump_json(indent=2), encoding="utf-8")
    return asset


def _seed_scene_asset(library_root: Path, asset_id: str = "scn_testscene01") -> LibraryAsset:
    adir = library_root / "scenes" / asset_id
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "master.png").write_bytes(b"fake-scene-png")
    asset = LibraryAsset(
        id=asset_id,
        kind="scenes",
        name="Cafe",
        notes="interior cafe",
        pipeline_id="scene",
        job_id="job_scene",
        created_at="2026-01-01T00:00:00+00:00",
        files={"master": "master.png"},
        meta={"tags": ["cafe", "interior"]},
    )
    (adir / "asset.json").write_text(asset.model_dump_json(indent=2), encoding="utf-8")
    return asset


def _seed_voice_asset(
    library_root: Path,
    asset_id: str,
    *,
    h3_ready: bool,
) -> LibraryAsset:
    adir = library_root / "voices" / asset_id
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "reference.wav").write_bytes(b"fake-voice-bytes")
    asset = LibraryAsset(
        id=asset_id,
        kind="voices",
        name=f"Voice {asset_id}",
        notes="voice reference",
        pipeline_id="external",
        job_id="job_voice",
        created_at="2026-01-01T00:00:00+00:00",
        files={"reference": "reference.wav"},
        meta={"h3_ready": h3_ready},
    )
    (adir / "asset.json").write_text(
        asset.model_dump_json(indent=2), encoding="utf-8"
    )
    return asset


def _seed_empty_actor_asset(library_root: Path, asset_id: str) -> LibraryAsset:
    adir = library_root / "actors" / asset_id
    adir.mkdir(parents=True, exist_ok=True)
    asset = LibraryAsset(
        id=asset_id,
        kind="actors",
        name="Only Actor",
        notes="accessible but has no usable image",
        pipeline_id="external",
        job_id="job_empty_actor",
        created_at="2026-01-01T00:00:00+00:00",
        files={},
        meta={},
    )
    (adir / "asset.json").write_text(
        asset.model_dump_json(indent=2), encoding="utf-8"
    )
    return asset


def _seed_layout_source_asset(
    library_root: Path,
    *,
    kind: str,
    asset_id: str,
    name: str,
    file_key: str = "master",
    review_status: str | None = None,
) -> LibraryAsset:
    adir = library_root / kind / asset_id
    adir.mkdir(parents=True, exist_ok=True)
    filename = f"{file_key}.png"
    (adir / filename).write_bytes(f"{asset_id}-image".encode() * 400)
    meta = {"review_status": review_status} if review_status else {}
    asset = LibraryAsset(
        id=asset_id,
        kind=kind,
        name=name,
        notes=f"approved notes for {name}",
        pipeline_id="external",
        job_id="job_seed",
        created_at="2026-01-01T00:00:00+00:00",
        files={file_key: filename},
        meta=meta,
    )
    (adir / "asset.json").write_text(
        asset.model_dump_json(indent=2), encoding="utf-8"
    )
    return asset


def _make_layout_queue_fixture(director_dirs):
    from app.agents.director.service import DirectorService
    from app.core.projects.store import save_project

    scene = _seed_layout_source_asset(
        director_dirs["library"],
        kind="scenes",
        asset_id="scn_layout_scene",
        name="Duty room",
        file_key="angle_00",
    )
    lu = _seed_layout_source_asset(
        director_dirs["library"],
        kind="actors",
        asset_id="act_layout_lu",
        name="Lu",
    )
    recorder = _seed_layout_source_asset(
        director_dirs["library"],
        kind="props",
        asset_id="prop_layout_recorder",
        name="Recorder",
    )
    project = create_project("Layout packs", "Lu conceals the recorder before Chen enters")
    shot = Shot(
        id="sht_layout_packs",
        project_id=project.id,
        scene_id="sc01",
        title="Conceal recorder",
        script_beat="Lu hides the recorder as Chen arrives.",
        duration_s=8.0,
        status=ShotStatus.ref_frame_pending,
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    svc = DirectorService(
        plan_provider=FakePlanProvider(response="[]"),
        orchestrator=RecordingOrchestrator(),
    )
    sources = {
        "scene": LayoutSourceRef(
            role=RefRole.scene, asset_id=scene.id, file_key="angle_00", notes="set"
        ),
        "lu": LayoutSourceRef(
            role=RefRole.actor, asset_id=lu.id, file_key="master", notes="identity"
        ),
        "recorder": LayoutSourceRef(
            role=RefRole.prop,
            asset_id=recorder.id,
            file_key="master",
            notes="handled prop",
        ),
    }
    return svc, project, shot, sources


@pytest.fixture
def director_dirs(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    jobs = tmp_path / "jobs"
    library = tmp_path / "library"
    projects.mkdir()
    jobs.mkdir()
    library.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects)
    monkeypatch.setattr(settings, "jobs_dir", jobs)
    monkeypatch.setattr(settings, "library_root", library)
    return {"projects": projects, "jobs": jobs, "library": library}


def test_shot_revision_requires_an_authored_update_and_rejects_unknown_fields():
    from pydantic import ValidationError

    from app.agents.director.planner import ShotRevisionSubmission

    with pytest.raises(ValidationError, match="at least one authored shot field"):
        ShotRevisionSubmission(shot_id="sht_2")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ShotRevisionSubmission.model_validate(
            {"shot_id": "sht_2", "script_beat": "new beat", "refs": []}
        )


def test_revise_shot_preserves_neighbors_and_production_inputs(director_dirs):
    from app.agents.director.planner import ShotRevisionSubmission
    from app.agents.director.service import DirectorService
    from app.core.projects.layouts import LayoutReference, LayoutReviewStatus
    from app.core.projects.models import ShotRef, ShotVoiceRef

    project = create_project("Safe revision", "Three shots remain in order.")
    common_prompt = PromptSections(
        subject_definitions="subject",
        summary="summary",
        retention_analysis="retain",
        detailed_description="0-5 seconds: action",
        overall_soundscape="room tone",
        non_diegetic_music="none",
    )
    shots = [
        Shot(
            id=f"sht_{index}",
            project_id=project.id,
            scene_id="sc01",
            title=f"Shot {index}",
            script_beat=f"Beat {index}",
            shot_type="wide shot",
            camera_angle="eye level",
            camera_motion="locked-off",
            composition="subject centered",
            duration_s=5.0,
            status=ShotStatus.succeeded,
            refs=[
                ShotRef(
                    role=RefRole.actor,
                    asset_id=f"act_{index}",
                    picture_index=1,
                )
            ],
            voice_refs=[
                ShotVoiceRef(
                    asset_id=f"voice_{index}",
                    audio_index=1,
                    speaker="Mia",
                )
            ],
            layout_asset_id=f"lay_{index}",
            layout_review_status="approved",
            layout_refs=[
                LayoutReference(
                    id=f"lr_{index}",
                    asset_id=f"lay_{index}",
                    review_status=LayoutReviewStatus.usable,
                    selected_for_h3=True,
                )
            ],
            prompt_sections=common_prompt,
            h3_job_id=f"job_{index}",
            meta={
                "prompt_layout_signature": f"layout_sig_{index}",
                "prompt_picture_signature": f"picture_sig_{index}",
                "prompt_voice_signature": f"voice_sig_{index}",
                "keep": f"value_{index}",
            },
        )
        for index in range(1, 4)
    ]
    for shot in shots:
        save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id for shot in shots]}))
    before = {shot.id: shot.model_dump(mode="json") for shot in list_shots(project.id)}
    svc = DirectorService(
        plan_provider=FakePlanProvider(response="[]"),
        orchestrator=RecordingOrchestrator(),
    )

    updated = svc.revise_shot(
        project.id,
        ShotRevisionSubmission(
            shot_id="sht_2",
            script_beat="Only Mia remains in frame.",
            shot_type="close-up",
            camera_motion="slow push-in",
            composition="Mia's face fills the frame",
        ),
    )

    by_id = {shot.id: shot for shot in updated}
    assert by_id["sht_1"].model_dump(mode="json") == before["sht_1"]
    assert by_id["sht_3"].model_dump(mode="json") == before["sht_3"]
    revised = by_id["sht_2"]
    assert revised.script_beat == "Only Mia remains in frame."
    assert revised.shot_type == "close-up"
    assert revised.camera_motion == "slow push-in"
    assert revised.composition == "Mia's face fills the frame"
    assert revised.refs == shots[1].refs
    assert revised.voice_refs == shots[1].voice_refs
    assert revised.layout_refs == shots[1].layout_refs
    assert revised.layout_review_status == "approved"
    assert revised.prompt_sections == PromptSections()
    assert revised.h3_job_id is None
    assert revised.meta["superseded_h3_job_ids"] == ["job_2"]
    assert revised.meta["prompt_layout_signature"] == ""
    assert revised.meta["prompt_picture_signature"] == ""
    assert revised.meta["prompt_voice_signature"] == ""
    assert revised.meta["keep"] == "value_2"
    assert revised.status == ShotStatus.needs_review


@pytest.mark.asyncio
async def test_plan_writes_shots_and_context(director_dirs):
    from app.agents.director.context_io import load_agent_context
    from app.agents.director.service import DirectorService

    _seed_actor_asset(director_dirs["library"])
    _seed_scene_asset(director_dirs["library"])
    project = create_project("Demo", "INT. CAFE - DAY\nActor walks in.\nHello there.")
    provider = FakePlanProvider(response=_one_shot_plan_json())
    orch = RecordingOrchestrator()
    svc = DirectorService(plan_provider=provider, orchestrator=orch)

    result = await svc.plan_project(project.id)

    assert result.id == project.id
    shots = list_shots(project.id)
    assert len(shots) == 1
    shot = shots[0]
    assert shot.scene_id == "sc01"
    assert shot.title == "Cafe open"
    assert shot.status in (ShotStatus.planning, ShotStatus.ref_frame_pending)
    assert shot.dialogue == ["Hello there."]
    assert shot.shot_type == CAMERA_DRAFT["shot_type"]
    assert shot.camera_angle == CAMERA_DRAFT["camera_angle"]
    assert shot.camera_motion == CAMERA_DRAFT["camera_motion"]
    assert shot.composition == CAMERA_DRAFT["composition"]
    assert any(r.asset_id == "act_testasset01" for r in shot.refs)
    # Agent auto-casts scene when LLM only matched actor
    assert any(r.role.value == "scene" for r in shot.refs)

    ctx_path = director_dirs["projects"] / project.id / "agent" / "context.json"
    assert ctx_path.exists()
    ctx = load_agent_context(project.id)
    assert ctx is not None
    assert ctx.project_id == project.id
    assert ctx.last_phase == "planned"
    assert len(ctx.shot_summaries) == 1

    reloaded = load_project(project.id)
    assert reloaded is not None
    assert shot.id in reloaded.shot_ids


@pytest.mark.asyncio
async def test_plan_and_h3_writer_request_different_guides(director_dirs):
    from app.agents.director.service import DirectorService

    _seed_actor_asset(director_dirs["library"])
    _seed_scene_asset(director_dirs["library"])
    project = create_project("Stage guides", "INT. CAFE - DAY\nActor enters.")
    sections_json = json.dumps(
        {
            "subject_definitions": "S1 is the actor.",
            "summary": "The actor enters the cafe.",
            "retention_analysis": "Keep the planned blocking.",
            "detailed_description": "A measured entrance across the room.",
            "overall_soundscape": "Quiet cafe room tone.",
            "non_diegetic_music": "None.",
        }
    )
    provider = FakePlanProvider(
        responses=[_one_shot_plan_json(), sections_json]
    )
    svc = DirectorService(
        plan_provider=provider,
        orchestrator=RecordingOrchestrator(),
    )

    await svc.plan_project(project.id)
    shot = list_shots(project.id)[0]
    await svc.write_prompts_after_layout(shot.id)

    assert provider.calls[0].guides == ("script-planning",)
    assert provider.calls[-1].guides == ("h3-prompt-writing",)
    h3_user = provider.calls[-1].user
    assert f"- shot_type: {CAMERA_DRAFT['shot_type']}" in h3_user
    assert f"- camera_angle: {CAMERA_DRAFT['camera_angle']}" in h3_user
    assert f"- camera_motion: {CAMERA_DRAFT['camera_motion']}" in h3_user
    assert f"- composition: {CAMERA_DRAFT['composition']}" in h3_user


@pytest.mark.asyncio
async def test_visual_direction_loads_reference_guides(monkeypatch):
    from io import BytesIO

    from PIL import Image

    from app.agents.director import visual_direction

    captured: list[tuple[str, ...]] = []

    def capture_guides(task: str, *, guides=()):
        captured.append(tuple(guides))
        return task

    monkeypatch.setattr(visual_direction, "with_director_skill", capture_guides)

    payload = {
        "shot_type": "wide shot",
        "camera": "eye level",
        "scene_lock": ["preserve the doorway"],
        "characters": [],
        "forbidden": ["no collage"],
        "generation_prompt": "Use Image1 for the exact doorway geometry.",
    }

    class _VisionClient:
        async def chat(self, model, prompt, **kwargs):
            return json.dumps(payload)

    buffer = BytesIO()
    Image.new("RGB", (64, 64), "navy").save(buffer, format="PNG")
    shot = Shot(
        id="sht_visual_guides",
        project_id="prj_visual_guides",
        scene_id="sc01",
        title="Doorway",
        script_beat="The empty doorway holds.",
        duration_s=4.0,
    )

    await visual_direction.analyze_ref_frame(
        shot,
        images={"ref_0": ("door.png", buffer.getvalue())},
        captions=["Image1 SCENE doorway"],
        model="qwen-test",
        ollama=_VisionClient(),
    )

    assert captured == [
        ("reference-strategy", "reference-frame-generation")
    ]


@pytest.mark.asyncio
async def test_replan_deletes_all_previous_shots(director_dirs):
    """New plan must remove old shot JSON files, not leave orphans in the UI."""
    from app.agents.director.service import DirectorService
    from app.core.projects.store import list_shot_ids_on_disk, save_shot

    _seed_actor_asset(director_dirs["library"])
    _seed_scene_asset(director_dirs["library"])
    project = create_project("Replan", "Story A then story B.")
    provider = FakePlanProvider(
        responses=[
            _one_shot_plan_json(title="Shot A"),
            _one_shot_plan_json(title="Shot B"),
        ]
    )
    svc = DirectorService(
        plan_provider=provider, orchestrator=RecordingOrchestrator()
    )

    await svc.plan_project(project.id)
    first = list_shots(project.id)
    assert len(first) == 1
    old_id = first[0].id
    assert old_id in list_shot_ids_on_disk(project.id)

    # Plant an extra orphan file that is not in shot_ids (legacy bug)
    orphan = first[0].model_copy(update={"id": "sht_orphan_old01", "title": "Orphan"})
    save_shot(orphan)
    assert "sht_orphan_old01" in list_shot_ids_on_disk(project.id)

    await svc.plan_project(project.id)
    second = list_shots(project.id)
    assert len(second) == 1
    assert second[0].title == "Shot B"
    assert second[0].id != old_id
    on_disk = list_shot_ids_on_disk(project.id)
    assert on_disk == [second[0].id]
    assert old_id not in on_disk
    assert "sht_orphan_old01" not in on_disk
    proj = load_project(project.id)
    assert proj is not None
    assert proj.shot_ids == [second[0].id]


@pytest.mark.asyncio
async def test_save_storyboard_persists_exact_ordered_drafts_without_calling_planner(
    director_dirs,
):
    from app.agents.director.planner import ShotDraft
    from app.agents.director.service import DirectorService, _script_hash
    from app.core.projects.store import save_project

    actor = _seed_actor_asset(director_dirs["library"])
    scene = _seed_scene_asset(director_dirs["library"])
    project = create_project(
        "Agent storyboard",
        "INT. CAFE - NIGHT\nMara crosses the silent cafe and finds the recorder.",
    )
    old = Shot(
        id="sht_old_storyboard",
        project_id=project.id,
        scene_id="old",
        title="Old plan",
        script_beat="This plan must be replaced only after the whole candidate is valid.",
        duration_s=3.0,
    )
    save_shot(old)
    save_project(project.model_copy(update={"shot_ids": [old.id]}))
    provider = FakePlanProvider(
        response=json.dumps({"valid": True, "issues": []})
    )
    svc = DirectorService(
        plan_provider=provider,
        orchestrator=RecordingOrchestrator(),
    )
    long_beat = (
        "Mara crosses from the locked entrance to the last booth while the practical "
        "lights fail one by one; she stops only when the recorder clicks beneath the "
        "table, keeping the entire causal action and reveal in this exact authored beat."
    )
    drafts = [
        ShotDraft.model_validate(
            {
                **CAMERA_DRAFT,
                "scene_id": "sc02",
                "title": "Recorder reveal",
                "script_beat": long_beat,
                "duration_s": 7.25,
                "dialogue": ["Who left this running?", "Mara, don't touch it."],
                "asset_matches": [
                    {
                        "role": "scene",
                        "asset_id": scene.id,
                        "file_key": "master",
                        "picture_index": 1,
                    },
                    {
                        "role": "actor",
                        "asset_id": actor.id,
                        "file_key": "master",
                        "picture_index": 2,
                    },
                ],
            }
        ),
        ShotDraft.model_validate(
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Locked entrance",
                "script_beat": "Mara tests the locked cafe door before crossing the room.",
                "duration_s": 4.5,
                "dialogue": [],
                "asset_matches": [
                    {
                        "role": "actor",
                        "asset_id": actor.id,
                        "file_key": "master",
                        "picture_index": 1,
                    },
                    {
                        "role": "scene",
                        "asset_id": scene.id,
                        "file_key": "master",
                        "picture_index": 2,
                    },
                ],
            }
        ),
    ]

    saved = await svc.save_storyboard(
        project.id,
        drafts,
        _script_hash(project.script_text),
    )

    persisted = list_shots(project.id)
    assert [shot.id for shot in saved] == [shot.id for shot in persisted]
    assert [shot.title for shot in persisted] == [
        "Recorder reveal",
        "Locked entrance",
    ]
    assert [shot.scene_id for shot in persisted] == ["sc02", "sc01"]
    assert [shot.duration_s for shot in persisted] == [7.25, 4.5]
    assert persisted[0].script_beat == long_beat
    assert persisted[0].dialogue == [
        "Who left this running?",
        "Mara, don't touch it.",
    ]
    assert [
        (ref.role.value, ref.asset_id, ref.file_key, ref.picture_index)
        for ref in persisted[0].refs
    ] == [
        ("scene", scene.id, "master", 1),
        ("actor", actor.id, "master", 2),
    ]
    assert [
        (ref.role.value, ref.asset_id, ref.file_key, ref.picture_index)
        for ref in persisted[1].refs
    ] == [
        ("actor", actor.id, "master", 1),
        ("scene", scene.id, "master", 2),
    ]
    assert len(provider.calls) == 1
    assert provider.calls[0].guides == ("storyboard-validation",)
    assert not any(call.guides == ("script-planning",) for call in provider.calls)
    from app.core.library.store import load_asset

    assert load_asset("actors", actor.id).project_id == project.id
    assert load_asset("scenes", scene.id).project_id == project.id
    assert not (director_dirs["projects"] / project.id / "shots" / f"{old.id}.json").exists()


@pytest.mark.asyncio
async def test_save_storyboard_adds_one_shot_without_recreating_existing_shots(
    director_dirs,
):
    """Adding a shot must not discard completed work on unchanged shots."""
    from app.agents.director.planner import ShotDraft
    from app.agents.director.service import DirectorService, _script_hash
    from app.core.projects.models import PromptSections
    from app.core.projects.store import save_shot

    actor = _seed_actor_asset(director_dirs["library"])
    scene = _seed_scene_asset(director_dirs["library"])
    project = create_project(
        "Stable storyboard",
        "INT. ROOM - DAY\nMara enters. A second beat follows.",
    )
    provider = FakePlanProvider(
        responses=[
            json.dumps({"valid": True, "issues": []}),
            json.dumps({"valid": True, "issues": []}),
        ]
    )
    svc = DirectorService(
        plan_provider=provider,
        orchestrator=RecordingOrchestrator(),
    )
    first_draft = ShotDraft.model_validate(
        {
            **CAMERA_DRAFT,
            "scene_id": "sc01",
            "title": "Mara enters",
            "script_beat": "Mara enters the quiet room.",
            "duration_s": 5.0,
            "asset_matches": [
                {
                    "role": "actor",
                    "asset_id": actor.id,
                    "file_key": "master",
                    "picture_index": 1,
                },
                {
                    "role": "scene",
                    "asset_id": scene.id,
                    "file_key": "master",
                    "picture_index": 2,
                },
            ],
        }
    )
    initial = await svc.save_storyboard(
        project.id,
        [first_draft],
        _script_hash(project.script_text),
    )
    original = initial[0].model_copy(
        update={
            "h3_job_id": "job_existing_h3",
            "prompt_sections": PromptSections(
                subject_definitions="Existing prompt must survive.",
                summary="Existing summary.",
                retention_analysis="Existing retention analysis.",
                detailed_description="Existing detailed description.",
                overall_soundscape="Existing soundscape.",
                non_diegetic_music="Existing music direction.",
            ),
        }
    )
    save_shot(original)
    added_draft = ShotDraft.model_validate(
        {
            **CAMERA_DRAFT,
            "scene_id": "sc01",
            "title": "Mara listens",
            "script_beat": "Mara stops and listens to the second beat.",
            "duration_s": 4.0,
            "asset_matches": [
                {
                    "role": "actor",
                    "asset_id": actor.id,
                    "file_key": "master",
                    "picture_index": 1,
                },
                {
                    "role": "scene",
                    "asset_id": scene.id,
                    "file_key": "master",
                    "picture_index": 2,
                },
            ],
        }
    )

    saved = await svc.save_storyboard(
        project.id,
        [
            first_draft.model_copy(update={"shot_id": original.id}),
            added_draft,
        ],
        _script_hash(project.script_text),
    )

    assert [shot.title for shot in saved] == ["Mara enters", "Mara listens"]
    assert saved[0].id == original.id
    assert saved[0].h3_job_id == "job_existing_h3"
    assert saved[0].prompt_sections.subject_definitions == "Existing prompt must survive."
    assert saved[1].id != original.id


@pytest.mark.asyncio
async def test_save_storyboard_rejects_stale_script_hash_without_changing_old_shots(
    director_dirs,
):
    from app.agents.director.planner import ShotDraft
    from app.agents.director.service import DirectorService
    from app.core.projects.store import save_project

    project = create_project("Stale candidate", "INT. ROOM - NIGHT\nThe approved beat.")
    old = Shot(
        id="sht_stale_old",
        project_id=project.id,
        scene_id="sc01",
        title="Existing approved plan",
        script_beat="The existing beat remains.",
        duration_s=5.0,
    )
    save_shot(old)
    save_project(project.model_copy(update={"shot_ids": [old.id]}))
    svc = DirectorService(
        plan_provider=FakePlanProvider(responses=[]),
        orchestrator=RecordingOrchestrator(),
    )
    candidate = ShotDraft(
        **CAMERA_DRAFT,
        scene_id="sc02",
        title="Stale replacement",
        script_beat="This was authored from an obsolete screenplay.",
        duration_s=6.0,
    )

    with pytest.raises(ValueError, match="script.*changed|stale"):
        await svc.save_storyboard(project.id, [candidate], "obsolete-hash")

    assert [shot.model_dump() for shot in list_shots(project.id)] == [old.model_dump()]
    assert load_project(project.id).script_text == project.script_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("draft_payload", "error"),
    [
        pytest.param(
            {"scene_id": "sc01", "title": "Missing beat", "duration_s": 5},
            "script_beat",
            id="malformed-draft",
        ),
        pytest.param(
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Invented asset",
                "script_beat": "The Agent invents a library binding.",
                "duration_s": 5,
                "asset_matches": [
                    {"role": "actor", "asset_id": "act_not_real", "picture_index": 1}
                ],
            },
            "act_not_real",
            id="unknown-asset",
        ),
        pytest.param(
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Invented file key",
                "script_beat": "The Agent binds a file key absent from the asset.",
                "duration_s": 5,
                "asset_matches": [
                    {
                        "role": "actor",
                        "asset_id": "act_testasset01",
                        "file_key": "nonexistent_angle",
                        "picture_index": 1,
                    }
                ],
            },
            "nonexistent_angle",
            id="invalid-file-key",
        ),
        pytest.param(
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Cross-kind image",
                "script_beat": "A scene asset is incorrectly bound as an actor.",
                "duration_s": 5,
                "asset_matches": [
                    {
                        "role": "actor",
                        "asset_id": "scn_testscene01",
                        "file_key": "master",
                        "picture_index": 1,
                    }
                ],
            },
            "scn_testscene01",
            id="cross-kind-image",
        ),
        pytest.param(
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Other project's actor",
                "script_beat": "An inaccessible actor is requested.",
                "duration_s": 5,
                "asset_matches": [
                    {
                        "role": "actor",
                        "asset_id": "act_other_project",
                        "file_key": "master",
                        "picture_index": 1,
                    }
                ],
            },
            "act_other_project",
            id="inaccessible-project-asset",
        ),
        pytest.param(
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Voice masquerading as image",
                "script_beat": "A Voice asset is incorrectly sent as Picture 1.",
                "duration_s": 5,
                "asset_matches": [
                    {
                        "role": "other",
                        "asset_id": "voi_ready",
                        "file_key": "reference",
                        "picture_index": 1,
                    }
                ],
            },
            "voi_ready|voices|image",
            id="voice-as-image",
        ),
        pytest.param(
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Wrong-kind voice",
                "script_beat": "An Actor asset is incorrectly bound as a Voice.",
                "duration_s": 5,
                "voice_matches": [
                    {
                        "asset_id": "act_testasset01",
                        "audio_index": 1,
                        "file_key": "master",
                    }
                ],
            },
            "act_testasset01",
            id="wrong-kind-voice",
        ),
        pytest.param(
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Unready voice",
                "script_beat": "A non-H3-ready Voice is requested.",
                "duration_s": 5,
                "voice_matches": [
                    {
                        "asset_id": "voi_not_ready",
                        "audio_index": 1,
                        "file_key": "reference",
                    }
                ],
            },
            "voi_not_ready|non-H3-ready",
            id="non-ready-voice",
        ),
        pytest.param(
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Missing voice key",
                "script_beat": "A nonexistent Voice file key is requested.",
                "duration_s": 5,
                "voice_matches": [
                    {
                        "asset_id": "voi_ready",
                        "audio_index": 1,
                        "file_key": "missing_take",
                    }
                ],
            },
            "missing_take",
            id="invalid-voice-file-key",
        ),
    ],
)
async def test_save_storyboard_rejects_malformed_or_invalid_bindings_transactionally(
    director_dirs,
    draft_payload,
    error,
):
    from app.agents.director.service import DirectorService, _script_hash
    from app.core.library.store import assign_asset_project, load_asset
    from app.core.projects.store import save_project

    draft_payload = {**CAMERA_DRAFT, **draft_payload}

    _seed_actor_asset(director_dirs["library"])
    _seed_scene_asset(director_dirs["library"])
    _seed_voice_asset(director_dirs["library"], "voi_ready", h3_ready=True)
    _seed_voice_asset(
        director_dirs["library"], "voi_not_ready", h3_ready=False
    )
    _seed_actor_asset(director_dirs["library"], "act_other_project")
    other_project = create_project("Other owner", "Other screenplay")
    assign_asset_project("actors", "act_other_project", other_project.id)
    project = create_project("Invalid candidate", "INT. ROOM - NIGHT\nApproved action.")
    old = Shot(
        id="sht_invalid_old",
        project_id=project.id,
        scene_id="sc00",
        title="Old plan",
        script_beat="Keep this exact old shot.",
        duration_s=4.0,
    )
    save_shot(old)
    save_project(project.model_copy(update={"shot_ids": [old.id]}))
    svc = DirectorService(
        plan_provider=FakePlanProvider(responses=[]),
        orchestrator=RecordingOrchestrator(),
    )
    ownership_before = {
        (kind, asset_id): load_asset(kind, asset_id).project_id
        for kind, asset_id in (
            ("actors", "act_testasset01"),
            ("scenes", "scn_testscene01"),
            ("voices", "voi_ready"),
            ("voices", "voi_not_ready"),
            ("actors", "act_other_project"),
        )
    }

    with pytest.raises(ValueError, match=error):
        await svc.save_storyboard(
            project.id,
            [draft_payload],  # type: ignore[list-item]
            _script_hash(project.script_text),
        )

    assert [shot.model_dump() for shot in list_shots(project.id)] == [old.model_dump()]
    assert load_project(project.id).shot_ids == [old.id]
    assert {
        key: load_asset(*key).project_id for key in ownership_before
    } == ownership_before


@pytest.mark.asyncio
async def test_save_storyboard_rejects_invalid_heuristic_auto_cast_before_replace(
    director_dirs,
):
    from app.agents.director.planner import ShotDraft
    from app.agents.director.service import DirectorService, _script_hash
    from app.core.library.store import load_asset
    from app.core.projects.store import save_project

    empty_actor = _seed_empty_actor_asset(
        director_dirs["library"], "act_empty_auto_cast"
    )
    _seed_scene_asset(director_dirs["library"], "scn_valid_auto_cast")
    project = create_project(
        "Invalid auto-cast",
        "INT. CAFE - NIGHT\nThe only actor crosses the cafe.",
    )
    old = Shot(
        id="sht_auto_cast_old",
        project_id=project.id,
        scene_id="sc00",
        title="Existing plan",
        script_beat="This valid plan must survive failed heuristic casting.",
        duration_s=5.0,
    )
    save_shot(old)
    save_project(project.model_copy(update={"shot_ids": [old.id]}))
    svc = DirectorService(
        plan_provider=FakePlanProvider(responses=[]),
        orchestrator=RecordingOrchestrator(),
    )
    candidate = ShotDraft(
        **CAMERA_DRAFT,
        scene_id="sc01",
        title="Auto-cast candidate",
        script_beat="The only actor crosses the cafe.",
        duration_s=6.0,
    )

    with pytest.raises(
        ValueError,
        match="act_empty_auto_cast|fullbody_threeview|file_key",
    ):
        await svc.save_storyboard(
            project.id,
            [candidate],
            _script_hash(project.script_text),
        )

    assert [shot.model_dump() for shot in list_shots(project.id)] == [old.model_dump()]
    assert load_project(project.id).shot_ids == [old.id]
    assert load_asset("actors", empty_actor.id).project_id is None


@pytest.mark.asyncio
async def test_save_storyboard_semantic_rejection_receives_complete_grounding_and_is_transactional(
    director_dirs,
):
    from app.agents.director.planner import ShotDraft
    from app.agents.director.service import DirectorService, _script_hash
    from app.core.library.store import load_asset
    from app.core.projects.store import save_project

    actor = _seed_actor_asset(director_dirs["library"])
    scene = _seed_scene_asset(director_dirs["library"])
    screenplay = (
        "INT. ARCHIVE - NIGHT\n"
        + "Mara follows the numbered shelves without touching them.\n" * 180
        + "At the final shelf, Mara hears her own voice from the sealed recorder."
    )
    feedback = (
        "Keep the approved screenplay exact.\n"
        "Make the storyboard at least 12 seconds, but do not collapse the reveal."
    )
    project = create_project("Semantic rejection", screenplay)
    old = Shot(
        id="sht_semantic_old",
        project_id=project.id,
        scene_id="sc00",
        title="Existing plan",
        script_beat="The existing approved storyboard stays intact.",
        duration_s=12.0,
    )
    save_shot(old)
    save_project(project.model_copy(update={"shot_ids": [old.id]}))
    candidate = ShotDraft.model_validate(
        {
            **CAMERA_DRAFT,
            "scene_id": "sc01",
            "title": "Impossible compressed reveal",
            "script_beat": (
                "Mara crosses the archive, checks every shelf, opens the recorder, "
                "hears her own warning, understands who recorded it, and escapes."
            ),
            "duration_s": 12.0,
            "dialogue": ["Do not open it."],
            "asset_matches": [
                {
                    "role": "actor",
                    "asset_id": actor.id,
                    "file_key": "master",
                    "picture_index": 1,
                },
                {
                    "role": "scene",
                    "asset_id": scene.id,
                    "file_key": "master",
                    "picture_index": 2,
                },
            ],
            "voice_matches": [],
        }
    )
    provider = FakePlanProvider(
        response=json.dumps(
            {
                "valid": False,
                "issues": [
                    "Shot 1 contains excessive sequential state transitions for one H3 clip."
                ],
            }
        )
    )
    svc = DirectorService(
        plan_provider=provider,
        orchestrator=RecordingOrchestrator(),
    )
    ownership_before = {
        ("actors", actor.id): load_asset("actors", actor.id).project_id,
        ("scenes", scene.id): load_asset("scenes", scene.id).project_id,
    }

    with pytest.raises(ValueError, match="excessive sequential state transitions"):
        await svc.save_storyboard(
            project.id,
            [candidate],
            _script_hash(screenplay),
            user_feedback=feedback,
            requested_minimum_duration_s=12.0,
        )

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call.guides == ("storyboard-validation",)
    assert screenplay in call.user
    assert feedback in call.user
    assert "12.0" in call.user
    assert json.dumps(
        [candidate.model_dump(mode="json")],
        ensure_ascii=False,
        indent=2,
    ) in call.user
    assert "never propose replacement shots" in call.system.lower()
    assert "screenplay coverage" in call.system.lower()
    assert "causal/character contradiction" in call.system.lower()
    assert "excessive sequential action/state transitions" in call.system.lower()
    assert "model-infeasible motion" in call.system.lower()
    assert [shot.model_dump() for shot in list_shots(project.id)] == [old.model_dump()]
    persisted = load_project(project.id)
    assert persisted is not None
    assert persisted.script_text == screenplay
    assert persisted.shot_ids == [old.id]
    assert {
        key: load_asset(*key).project_id for key in ownership_before
    } == ownership_before


@pytest.mark.asyncio
async def test_save_storyboard_runs_real_provider_and_storyboard_validation_guide(
    director_dirs,
):
    from app.agents.director.planner import ShotDraft
    from app.agents.director.service import DirectorService, _script_hash
    from app.api.projects import OllamaPlanProvider

    actor = _seed_actor_asset(director_dirs["library"])
    scene = _seed_scene_asset(director_dirs["library"])
    screenplay = "INT. CAFE - NIGHT\nMara crosses the quiet cafe and finds the recorder."
    project = create_project("Real storyboard validation chain", screenplay)
    candidate = ShotDraft.model_validate(
        {
            **CAMERA_DRAFT,
            "scene_id": "sc01",
            "title": "Recorder discovery",
            "script_beat": "Mara crosses the quiet cafe and finds the recorder.",
            "duration_s": 8.0,
            "asset_matches": [
                {
                    "role": "actor",
                    "asset_id": actor.id,
                    "file_key": "master",
                    "picture_index": 1,
                },
                {
                    "role": "scene",
                    "asset_id": scene.id,
                    "file_key": "master",
                    "picture_index": 2,
                },
            ],
        }
    )
    prompts: list[str] = []

    class _Client:
        async def generate(self, model: str, prompt: str) -> str:
            prompts.append(prompt)
            return json.dumps({"valid": True, "issues": []})

    provider = OllamaPlanProvider(model="qwen-test")
    provider.client = _Client()
    svc = DirectorService(
        plan_provider=provider,
        orchestrator=RecordingOrchestrator(),
    )

    saved = await svc.save_storyboard(
        project.id,
        [candidate],
        _script_hash(screenplay),
        user_feedback="Keep the approved screenplay exact.",
        requested_minimum_duration_s=8.0,
    )

    assert len(saved) == 1
    assert saved == list_shots(project.id)
    assert saved[0].title == "Recorder discovery"
    assert len(prompts) == 1
    prompt = prompts[0]
    assert '<DIRECTOR_STAGE_GUIDE id="storyboard-validation">' in prompt
    assert "screenplay coverage" in prompt
    assert "Do not propose replacement shots" in prompt
    assert screenplay in prompt
    assert "Keep the approved screenplay exact." in prompt


@pytest.mark.asyncio
async def test_ollama_plan_provider_requires_real_vision_for_layout_analysis():
    from app.api.projects import OllamaPlanProvider

    calls: list[dict] = []

    class _Client:
        async def generate(self, *args, **kwargs):
            raise AssertionError("Layout analysis must not silently fall back to text generation")

        async def chat(self, model: str, prompt: str, **kwargs) -> str:
            calls.append({"model": model, "prompt": prompt, **kwargs})
            return "visible layout analysis"

    provider = OllamaPlanProvider(model="qwen-vision-test")
    provider.client = _Client()

    result = await provider.complete_with_images(
        "Inspect the Layout.",
        "Describe visible blocking.",
        images=["encoded-image"],
        guides=("h3-prompt-writing",),
    )

    assert result == "visible layout analysis"
    assert calls[0]["images"] == ["encoded-image"]
    assert calls[0]["require_vision"] is True


@pytest.mark.asyncio
async def test_save_storyboard_rejects_requested_minimum_duration_before_semantic_validation(
    director_dirs,
):
    from app.agents.director.planner import ShotDraft
    from app.agents.director.service import DirectorService, _script_hash
    from app.core.library.store import load_asset
    from app.core.projects.store import save_project

    actor = _seed_actor_asset(director_dirs["library"])
    scene = _seed_scene_asset(director_dirs["library"])
    project = create_project(
        "Duration rejection",
        "INT. CAFE - NIGHT\nMara enters, finds the recorder, and hears a warning.",
    )
    old = Shot(
        id="sht_duration_old",
        project_id=project.id,
        scene_id="sc00",
        title="Existing duration plan",
        script_beat="Preserve this shot when the candidate is too short.",
        duration_s=15.0,
    )
    save_shot(old)
    save_project(project.model_copy(update={"shot_ids": [old.id]}))
    candidate = ShotDraft.model_validate(
        {
            **CAMERA_DRAFT,
            "scene_id": "sc01",
            "title": "Too short",
            "script_beat": "Mara enters and finds the recorder.",
            "duration_s": 9.5,
            "asset_matches": [
                {
                    "role": "actor",
                    "asset_id": actor.id,
                    "file_key": "master",
                    "picture_index": 1,
                },
                {
                    "role": "scene",
                    "asset_id": scene.id,
                    "file_key": "master",
                    "picture_index": 2,
                },
            ],
        }
    )
    provider = FakePlanProvider(responses=[])
    svc = DirectorService(
        plan_provider=provider,
        orchestrator=RecordingOrchestrator(),
    )

    with pytest.raises(ValueError, match=r"9\.5.*minimum.*12"):
        await svc.save_storyboard(
            project.id,
            [candidate],
            _script_hash(project.script_text),
            user_feedback="At least 12 seconds.",
            requested_minimum_duration_s=12.0,
        )

    assert provider.calls == []
    assert [shot.model_dump() for shot in list_shots(project.id)] == [old.model_dump()]
    persisted = load_project(project.id)
    assert persisted is not None
    assert persisted.script_text == project.script_text
    assert persisted.shot_ids == [old.id]
    assert load_asset("actors", actor.id).project_id is None
    assert load_asset("scenes", scene.id).project_id is None


@pytest.mark.asyncio
async def test_queue_two_layout_briefs_for_one_shot(director_dirs, monkeypatch):
    import app.agents.director.service as service_module

    svc, project, shot, sources = _make_layout_queue_fixture(director_dirs)
    started: list[JobRecord] = []

    async def fake_start(job, *, images=None):
        saved = load_shot(project.id, shot.id)
        assert saved is not None
        assert any(item.id == job.params["layout_ref_id"] for item in saved.layout_refs)
        started.append(job)
        return job

    async def fake_analyze(*args, **kwargs):
        return _visual_result()

    monkeypatch.setattr(service_module, "start_pipeline_job", fake_start)
    monkeypatch.setattr(service_module, "analyze_ref_frame", fake_analyze)
    monkeypatch.setattr(service_module, "get_director_model", lambda: "qwen-test")

    first = LayoutBrief(
        purpose="before entry",
        state_description="Lu alone; doorway empty",
        source_refs=[sources["scene"], sources["lu"]],
    )
    second = LayoutBrief(
        purpose="post-entry prop concealment",
        state_description="Chen outside; Lu hides recorder",
        source_refs=[sources["scene"], sources["lu"], sources["recorder"]],
    )
    await svc.queue_reference_frame(shot.id, brief=first)
    await svc.queue_reference_frame(shot.id, brief=second)

    fresh = load_shot(project.id, shot.id)
    assert fresh is not None
    assert [item.purpose for item in fresh.layout_refs] == [
        "before entry",
        "post-entry prop concealment",
    ]
    assert fresh.layout_refs[0].job_id != fresh.layout_refs[1].job_id
    assert fresh.layout_refs[0].id.startswith("lref_")
    assert fresh.layout_refs[1].id.startswith("lref_")
    assert len(started[1].params["layout_source_refs"]) == 3
    assert started[1].params["layout_ref_id"] == fresh.layout_refs[1].id
    assert started[1].params["layout_brief"] == second.model_dump(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_names",
    [
        ("lu",),
        ("scene", "lu"),
        ("scene", "lu", "recorder"),
    ],
)
async def test_explicit_layout_pack_preserves_one_to_three_sources_in_order(
    director_dirs, monkeypatch, source_names
):
    import app.agents.director.service as service_module

    svc, _project, shot, sources = _make_layout_queue_fixture(director_dirs)
    started: list[tuple[JobRecord, dict]] = []

    async def fake_start(job, *, images=None):
        started.append((job, dict(images or {})))
        return job

    async def fake_analyze(*args, **kwargs):
        return _visual_result()

    monkeypatch.setattr(service_module, "start_pipeline_job", fake_start)
    monkeypatch.setattr(service_module, "analyze_ref_frame", fake_analyze)
    monkeypatch.setattr(service_module, "get_director_model", lambda: "qwen-test")

    ordered_sources = [sources[name] for name in source_names]
    await svc.queue_reference_frame(
        shot.id,
        brief=LayoutBrief(purpose="ordered pack", source_refs=ordered_sources),
    )

    assert len(started) == 1
    job, images = started[0]
    assert list(images) == [f"ref_{index}" for index in range(len(source_names))]
    assert [item["asset_id"] for item in job.params["layout_source_refs"]] == [
        source.asset_id for source in ordered_sources
    ]
    assert len(job.params["image_keys"]) == len(source_names)
    for index, source in enumerate(ordered_sources, start=1):
        label = job.params["ref_labels"][index - 1]
        assert f"Image{index}" in label
        assert source.role.value in label
        assert source.asset_id in label
        assert source.file_key in label
        assert source.notes in label


@pytest.mark.asyncio
async def test_rejected_layout_cannot_be_a_qwen_source(director_dirs, monkeypatch):
    import app.agents.director.service as service_module

    svc, _project, shot, _sources = _make_layout_queue_fixture(director_dirs)
    rejected = _seed_layout_source_asset(
        director_dirs["library"],
        kind="layouts",
        asset_id="lay_rejected_source",
        name="Rejected frame",
        file_key="layout",
        review_status="rejected",
    )
    started: list[JobRecord] = []

    async def fake_start(job, *, images=None):
        started.append(job)
        return job

    monkeypatch.setattr(service_module, "start_pipeline_job", fake_start)

    with pytest.raises(ValueError, match="rejected Layout.*lay_rejected_source"):
        await svc.queue_reference_frame(
            shot.id,
            brief=LayoutBrief(
                purpose="repair attempt",
                source_refs=[
                    LayoutSourceRef(
                        role=RefRole.layout_ref_frame,
                        asset_id=rejected.id,
                        file_key="layout",
                    )
                ],
            ),
        )

    assert started == []


@pytest.mark.asyncio
async def test_explicit_layout_file_key_does_not_fall_back(director_dirs, monkeypatch):
    import app.agents.director.service as service_module

    svc, _project, shot, sources = _make_layout_queue_fixture(director_dirs)
    started: list[JobRecord] = []

    async def fake_start(job, *, images=None):
        started.append(job)
        return job

    monkeypatch.setattr(service_module, "start_pipeline_job", fake_start)
    missing_key = sources["recorder"].model_copy(update={"file_key": "hero_closeup"})

    with pytest.raises(
        ValueError, match="prop_layout_recorder.*hero_closeup|hero_closeup.*prop_layout_recorder"
    ):
        await svc.queue_reference_frame(
            shot.id,
            brief=LayoutBrief(purpose="exact key", source_refs=[missing_key]),
        )

    assert started == []


@pytest.mark.asyncio
async def test_context_saved_before_comfy(director_dirs, monkeypatch):
    from app.agents.director.context_io import load_agent_context, save_agent_context
    from app.agents.director.service import DirectorService
    from app.core.jobs import runner as jobs_runner
    from app.core.projects.models import RefRole, ShotRef

    _seed_actor_asset(director_dirs["library"])
    project = create_project("Q", "script")
    shot = Shot(
        id="sht_queue000001",
        project_id=project.id,
        scene_id="sc01",
        title="Open",
        script_beat="walk in",
        duration_s=8.0,
        status=ShotStatus.planning,
        refs=[
            ShotRef(role=RefRole.actor, asset_id="act_testasset01", picture_index=1),
        ],
        dialogue=["Hi"],
    )
    save_shot(shot)
    project.shot_ids = [shot.id]
    from app.core.projects.store import save_project

    save_project(project)
    save_agent_context(
        project.id,
        AgentContext(
            project_id=project.id,
            script_hash="abc",
            last_phase="planned",
            shot_summaries=[{"id": shot.id, "status": shot.status.value}],
        ),
    )

    call_order: list[str] = []
    started_jobs: list[str] = []

    orch = RecordingOrchestrator()

    async def _release():
        call_order.append("release_llm")
        await RecordingOrchestrator.release_llm(orch)

    orch.release_llm = _release  # type: ignore[method-assign]

    original_save = None
    from app.agents.director import context_io as context_io_mod

    _real_save = context_io_mod.save_agent_context

    def tracking_save(project_id, ctx):
        call_order.append("save_context")
        return _real_save(project_id, ctx)

    monkeypatch.setattr(context_io_mod, "save_agent_context", tracking_save)
    # Also patch the service module's bound import if any
    import app.agents.director.service as service_mod

    monkeypatch.setattr(service_mod, "save_agent_context", tracking_save)

    async def fake_start(job, *, images=None):
        call_order.append("start_job")
        started_jobs.append(job.id)
        assert job.params["description"] == "COMPILED VISUAL PROMPT"
        assert job.params["visual_director_model"] == "qwen3.6:27b"
        assert job.params["visual_brief"]["shot_type"] == "wide shot"
        assert job.params["selected_refs"][0]["file_key"] == "ref_0"
        return job

    monkeypatch.setattr(service_mod, "start_pipeline_job", fake_start)
    monkeypatch.setattr(jobs_runner, "start_pipeline_job", fake_start)

    provider = FakePlanProvider(response="[]")
    svc = DirectorService(plan_provider=provider, orchestrator=orch)

    # Reference-frame now requires real scene + actor three-view bytes; stub for order test.
    monkeypatch.setattr(svc, "_ref_frame_missing_requirements", lambda _shot: [])
    monkeypatch.setattr(
        svc,
        "_collect_ref_frame_refs",
        lambda _shot: {
            "images": {"ref_0": ("a.png", b"fakepngbytes-xxx")},
            "labels": ["CHARACTER three-view (test)"],
        },
    )
    monkeypatch.setattr(svc, "_ensure_ref_frame_refs", lambda _project, s: s)
    async def fake_analyze(*args, **kwargs):
        call_order.append("analyze")
        return _visual_result()

    monkeypatch.setattr(service_mod, "analyze_ref_frame", fake_analyze)
    monkeypatch.setattr(service_mod, "get_director_model", lambda: "qwen3.6:27b")

    await svc.queue_ref_frames(project.id, shot_ids=[shot.id])

    assert "save_context" in call_order
    assert "release_llm" in call_order
    assert "start_job" in call_order
    assert call_order.index("save_context") < call_order.index("release_llm")
    assert call_order.index("analyze") < call_order.index("release_llm")
    assert call_order.index("release_llm") < call_order.index("start_job")
    assert len(started_jobs) == 1

    updated = load_shot(project.id, shot.id)
    assert updated is not None
    assert updated.status == ShotStatus.ref_frame_pending
    assert updated.ref_frame_job_id is not None

    # Must not auto-submit H3
    assert updated.h3_job_id is None
    assert updated.status != ShotStatus.queued


@pytest.mark.asyncio
async def test_visual_direction_failure_is_non_blocking_without_starting_job(
    director_dirs, monkeypatch
):
    import app.agents.director.service as service_mod
    from app.agents.director.service import DirectorService
    from app.core.projects.models import RefRole, ShotRef
    from app.core.projects.store import save_project

    project = create_project("Vision fail", "actor crosses the hallway")
    shot = Shot(
        id="sht_visionfail01",
        project_id=project.id,
        scene_id="sc01",
        title="Cross hallway",
        script_beat="The actor walks toward the door.",
        duration_s=5.0,
        status=ShotStatus.ref_frame_pending,
        refs=[ShotRef(role=RefRole.actor, asset_id="act_x", picture_index=1)],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    started: list[str] = []

    async def fake_start(job, *, images=None):
        started.append(job.id)

    async def fail_analyze(*args, **kwargs):
        raise ValueError("model returned invalid JSON")

    svc = DirectorService(
        plan_provider=FakePlanProvider(response="[]"),
        orchestrator=RecordingOrchestrator(),
    )
    monkeypatch.setattr(svc, "_ref_frame_missing_requirements", lambda _s: [])
    monkeypatch.setattr(svc, "_ensure_ref_frame_refs", lambda _p, s: s)
    monkeypatch.setattr(
        svc,
        "_collect_ref_frame_refs",
        lambda _s: {
            "images": {"ref_0": ("actor.png", b"valid-enough-test-bytes")},
            "labels": ["Image1 ACTOR master"],
            "image_labels": ["Image1 ACTOR master"],
        },
    )
    monkeypatch.setattr(service_mod, "analyze_ref_frame", fail_analyze)
    monkeypatch.setattr(service_mod, "start_pipeline_job", fake_start)
    monkeypatch.setattr(service_mod, "get_director_model", lambda: "qwen3.6:27b")

    result = await svc.queue_ref_frames(project.id, shot_ids=[shot.id])

    assert started == []
    assert len(result) == 1
    fresh = load_shot(project.id, shot.id)
    assert fresh is not None
    assert fresh.status == ShotStatus.ref_frame_pending
    assert fresh.ref_frame_job_id is None
    assert fresh.blocked_reasons == []
    issues = fresh.meta["layout_generation_issues"]
    assert "visual direction failed" in issues[0]
    assert "invalid JSON" in issues[0]
    assert ("release_llm",) in svc.orchestrator.calls


@pytest.mark.asyncio
async def test_queue_ref_frame_can_regen_from_review(director_dirs, monkeypatch):
    """Explicit shot_ids must re-queue even when layout is already in review."""
    from app.agents.director import service as service_mod
    from app.agents.director.service import DirectorService
    from app.core.jobs import load_job, save_job
    from app.core.jobs import runner as jobs_runner
    from app.core.projects.models import ShotRef, RefRole
    from app.core.schemas import JobStatus
    from PIL import Image

    layout_dir = director_dirs["library"] / "layouts" / "lay_old"
    layout_dir.mkdir(parents=True)
    failed_frame = Image.effect_noise((256, 256), 80).convert("RGB")
    failed_frame.save(layout_dir / "layout.png")
    old_layout = LibraryAsset(
        id="lay_old",
        kind="layouts",
        name="Rejected layout",
        notes="",
        pipeline_id="ref_frame",
        job_id="job_old",
        created_at="2026-01-01T00:00:00+00:00",
        files={"layout": "layout.png"},
        meta={},
    )
    (layout_dir / "asset.json").write_text(
        old_layout.model_dump_json(indent=2), encoding="utf-8"
    )

    project = create_project("Regen layout", "girl sprays deodorant in corridor")
    shot = Shot(
        id="sht_regen01",
        project_id=project.id,
        scene_id="sc01",
        title="corridor spray",
        script_beat="stop, spray, smile",
        duration_s=5.0,
        status=ShotStatus.needs_review,
        refs=[
            ShotRef(role=RefRole.actor, asset_id="act_x", picture_index=1),
            ShotRef(role=RefRole.scene, asset_id="scn_x", picture_index=2),
        ],
        layout_asset_id="lay_old",
        layout_review_status="pending_review",
        ref_frame_job_id="job_old",
        feedback="The hand touches the folder; preserve a visible air gap.",
    )
    save_shot(shot)
    project = project.model_copy(update={"shot_ids": [shot.id]})
    from app.core.projects.store import save_project

    save_project(project)

    orch = RecordingOrchestrator()
    started: list[str] = []
    started_images: list[dict] = []
    analyze_calls: list[dict] = []

    async def fake_start(job, *, images=None):
        started.append(job.id)
        started_images.append(dict(images or {}))
        return job

    monkeypatch.setattr(service_mod, "start_pipeline_job", fake_start)
    monkeypatch.setattr(jobs_runner, "start_pipeline_job", fake_start)

    svc = DirectorService(plan_provider=FakePlanProvider(response="[]"), orchestrator=orch)
    monkeypatch.setattr(svc, "_ref_frame_missing_requirements", lambda _s: [])
    monkeypatch.setattr(
        svc,
        "_collect_ref_frame_refs",
        lambda _s: {
            "images": {"ref_0": ("a.png", b"fakepngbytes-xxx")},
            "labels": ["actor"],
        },
    )
    monkeypatch.setattr(svc, "_ensure_ref_frame_refs", lambda _p, s: s)
    async def fake_analyze(*args, **kwargs):
        analyze_calls.append(kwargs)
        return _visual_result().model_copy(
            update={
                "review_image_used": kwargs.get("review_image") is not None,
                "review_feedback": kwargs.get("feedback", ""),
            }
        )

    monkeypatch.setattr(service_mod, "analyze_ref_frame", fake_analyze)
    monkeypatch.setattr(service_mod, "get_director_model", lambda: "qwen3.6:27b")

    # Bulk without force must skip review shots
    bulk = await svc.queue_ref_frames(project.id, shot_ids=None)
    assert bulk == []
    assert started == []

    # Bulk with force re-queues review shots
    bulk_force = await svc.queue_ref_frames(project.id, shot_ids=None, force=True)
    assert len(bulk_force) == 1
    assert len(started) == 1
    assert analyze_calls[0]["review_image"][0] == "layout.png"
    assert analyze_calls[0]["feedback"] == (
        "The hand touches the folder; preserve a visible air gap."
    )
    assert list(started_images[0]) == ["ref_0"]
    first_job = load_job(started[0])
    assert first_job is not None
    assert first_job.params["review_image_used"] is True
    assert first_job.params["previous_layout_asset_id"] == "lay_old"

    # Once the first regeneration is terminal, an explicit request may run again.
    completed = load_job(started[-1])
    assert completed is not None
    completed.status = JobStatus.succeeded
    save_job(completed)

    # Explicit shot re-queues again (new job id)
    updated_list = await svc.queue_ref_frames(project.id, shot_ids=[shot.id])
    assert len(updated_list) == 1
    assert len(started) == 2
    fresh = load_shot(project.id, shot.id)
    assert fresh is not None
    assert fresh.status == ShotStatus.ref_frame_pending
    assert fresh.ref_frame_job_id == started[-1]
    assert fresh.ref_frame_job_id != "job_old"
    # Compatibility projection follows the replacement job identity while it is pending.
    assert fresh.layout_asset_id is None
    assert [item.job_id for item in fresh.layout_refs] == [
        "job_old",
        started[0],
        started[1],
    ]


@pytest.mark.asyncio
async def test_compatibility_regeneration_projects_its_job_and_terminal_asset(
    director_dirs, monkeypatch
):
    import app.agents.director.service as service_mod
    from app.agents.director.service import DirectorService
    from app.core.jobs import load_job, save_job
    from app.core.jobs.shot_sync import on_pipeline_job_terminal
    from app.core.projects import LayoutReviewStatus
    from app.core.projects.store import save_project
    from app.core.schemas import JobStatus, OutputSlot

    project = create_project("Legacy approved replacement", "replace the approved frame")
    shot = Shot(
        id="sht_legacy_approved_regen",
        project_id=project.id,
        scene_id="sc01",
        title="Approved frame replacement",
        script_beat="Regenerate the primary composition.",
        duration_s=5.0,
        status=ShotStatus.succeeded,
        layout_asset_id="lay_approved_old",
        layout_review_status="approved",
        ref_frame_job_id="job_approved_old",
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    started: list[JobRecord] = []

    async def fake_start(job, *, images=None):
        started.append(job)
        return job

    async def fake_analyze(*args, **kwargs):
        return _visual_result()

    svc = DirectorService(
        plan_provider=FakePlanProvider(response="[]"),
        orchestrator=RecordingOrchestrator(),
    )
    monkeypatch.setattr(svc, "_ensure_ref_frame_refs", lambda _project, value: value)
    monkeypatch.setattr(svc, "_ref_frame_missing_requirements", lambda _shot: [])
    monkeypatch.setattr(
        svc,
        "_collect_ref_frame_refs",
        lambda _shot: {
            "images": {"ref_0": ("actor.png", b"valid-enough-test-bytes")},
            "labels": ["Image1 ACTOR master"],
            "image_labels": ["Image1 ACTOR master"],
        },
    )
    monkeypatch.setattr(service_mod, "start_pipeline_job", fake_start)
    monkeypatch.setattr(service_mod, "analyze_ref_frame", fake_analyze)
    monkeypatch.setattr(service_mod, "get_director_model", lambda: "qwen-test")

    queued = await svc.queue_ref_frames(project.id, shot_ids=[shot.id])
    assert len(queued) == 1
    assert len(started) == 1
    pending = load_shot(project.id, shot.id)
    assert pending is not None
    assert [item.job_id for item in pending.layout_refs] == [
        "job_approved_old",
        started[0].id,
    ]
    assert pending.layout_refs[0].review_status == LayoutReviewStatus.usable
    assert pending.layout_refs[0].selected_for_h3 is True
    assert pending.ref_frame_job_id == started[0].id
    assert pending.layout_asset_id is None

    output_path = director_dirs["jobs"] / "approved-replacement.png"
    output_path.write_bytes(b"new-layout-image")
    terminal = load_job(started[0].id)
    assert terminal is not None
    terminal.status = JobStatus.succeeded
    terminal.outputs = {
        "layout": OutputSlot(
            key="layout",
            label="layout",
            path=str(output_path),
        )
    }
    save_job(terminal)

    on_pipeline_job_terminal(terminal)

    synced = load_shot(project.id, shot.id)
    assert synced is not None
    assert [item.job_id for item in synced.layout_refs] == [
        "job_approved_old",
        started[0].id,
    ]
    assert synced.layout_refs[0].asset_id == "lay_approved_old"
    assert synced.layout_refs[0].review_status == LayoutReviewStatus.usable
    assert synced.layout_refs[1].asset_id is not None
    assert synced.layout_refs[1].review_status == LayoutReviewStatus.pending_review
    assert synced.ref_frame_job_id == started[0].id
    assert synced.layout_asset_id == synced.layout_refs[1].asset_id
    assert synced.layout_review_status == "pending_review"


@pytest.mark.asyncio
async def test_queue_ref_frame_does_not_replace_active_job(
    director_dirs, monkeypatch
):
    """A second click must not orphan the first in-flight layout result."""
    from app.agents.director import service as service_mod
    from app.agents.director.service import DirectorService
    from app.core.jobs.store import create_job
    from app.core.projects.models import RefRole, ShotRef
    from app.core.projects.store import save_project

    project = create_project("No duplicate layout", "actor enters hallway")
    active_job = create_job(
        pipeline_id="ref_frame",
        asset_kind="layouts",
        name="layout:entrance",
        params={"shot_id": "sht_no_duplicate", "project_id": project.id},
        project_id=project.id,
    )
    shot = Shot(
        id="sht_no_duplicate",
        project_id=project.id,
        scene_id="sc01",
        title="Entrance",
        script_beat="Actor enters the hallway.",
        duration_s=5.0,
        status=ShotStatus.ref_frame_pending,
        refs=[
            ShotRef(role=RefRole.actor, asset_id="act_x", picture_index=1),
            ShotRef(role=RefRole.scene, asset_id="scn_x", picture_index=2),
        ],
        ref_frame_job_id=active_job.id,
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    svc = DirectorService(
        plan_provider=FakePlanProvider(response="[]"),
        orchestrator=RecordingOrchestrator(),
    )
    monkeypatch.setattr(svc, "_ref_frame_missing_requirements", lambda _s: [])
    monkeypatch.setattr(svc, "_ensure_ref_frame_refs", lambda _p, s: s)
    monkeypatch.setattr(
        svc,
        "_collect_ref_frame_refs",
        lambda _s: {
            "images": {"ref_0": ("actor.png", b"valid-enough-test-bytes")},
            "labels": ["Image1 ACTOR master"],
            "image_labels": ["Image1 ACTOR master"],
        },
    )

    async def fake_analyze(*args, **kwargs):
        return _visual_result()

    async def fake_start(job, *, images=None):
        return job

    monkeypatch.setattr(service_mod, "analyze_ref_frame", fake_analyze)
    monkeypatch.setattr(service_mod, "start_pipeline_job", fake_start)

    result = await svc.queue_ref_frames(project.id, shot_ids=[shot.id])

    assert result == []
    fresh = load_shot(project.id, shot.id)
    assert fresh is not None
    assert fresh.ref_frame_job_id == active_job.id


@pytest.mark.asyncio
async def test_queue_ref_frames_with_no_targets_does_not_load_visual_model(
    director_dirs,
):
    from app.agents.director.service import DirectorService

    project = create_project("No targets", "empty project")
    orch = RecordingOrchestrator()
    svc = DirectorService(
        plan_provider=FakePlanProvider(response="[]"), orchestrator=orch
    )

    result = await svc.queue_ref_frames(project.id)

    assert result == []
    assert ("llm_session_enter",) not in orch.calls
    assert ("ensure_llm_ready",) not in orch.calls


@pytest.mark.asyncio
async def test_missing_asset_blocks_shot(director_dirs):
    from app.agents.director.service import DirectorService

    project = create_project("Blocked", "script with unknown asset")
    provider = FakePlanProvider(
        response=_one_shot_plan_json(asset_id="act_does_not_exist")
    )
    orch = RecordingOrchestrator()
    svc = DirectorService(plan_provider=provider, orchestrator=orch)

    await svc.plan_project(project.id)
    shots = list_shots(project.id)
    assert len(shots) == 1
    assert shots[0].status == ShotStatus.blocked
    reasons = " ".join(shots[0].blocked_reasons).lower()
    assert (
        "act_does_not_exist" in reasons
        or "missing" in reasons
        or "actor" in reasons
        or "scene" in reasons
    )


@pytest.mark.asyncio
async def test_plan_auto_casts_when_llm_omits_assets(director_dirs):
    """Agent must pick assets even if the plan JSON leaves asset_matches empty."""
    from app.agents.director.service import DirectorService
    from app.core.projects.models import RefRole

    _seed_actor_asset(director_dirs["library"], "act_mia00000001")
    # rename actor for name match
    from app.core.library.store import load_asset, asset_dir
    import json as _json

    adir = director_dirs["library"] / "actors" / "act_mia00000001"
    raw = _json.loads((adir / "asset.json").read_text(encoding="utf-8"))
    raw["name"] = "Mia"
    raw["notes"] = "young woman beach"
    (adir / "asset.json").write_text(_json.dumps(raw, indent=2), encoding="utf-8")

    _seed_scene_asset(director_dirs["library"], "scn_beach000001")
    sdir = director_dirs["library"] / "scenes" / "scn_beach000001"
    raw = _json.loads((sdir / "asset.json").read_text(encoding="utf-8"))
    raw["name"] = "beach"
    raw["notes"] = "sandy shore"
    (sdir / "asset.json").write_text(_json.dumps(raw, indent=2), encoding="utf-8")

    plan = _json.dumps(
        [
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Mia on the beach",
                "script_beat": "Mia walks along the shore",
                "duration_s": 8.0,
                "dialogue": [],
                "asset_matches": [],
            }
        ]
    )
    project = create_project("AutoCast", "Mia walks on the beach at sunset.")
    provider = FakePlanProvider(response=plan)
    svc = DirectorService(plan_provider=provider, orchestrator=RecordingOrchestrator())
    await svc.plan_project(project.id)
    shots = list_shots(project.id)
    assert len(shots) == 1
    roles = {r.role for r in shots[0].refs}
    assert RefRole.actor in roles
    assert RefRole.scene in roles
    assert shots[0].status == ShotStatus.ref_frame_pending


@pytest.mark.asyncio
async def test_planner_retries_on_bad_json_then_blocks(director_dirs):
    from app.agents.director.planner import parse_shot_drafts
    from app.agents.director.service import DirectorService

    project = create_project("Repair", "script")
    old = Shot(
        id="sht_existing_repair",
        project_id=project.id,
        scene_id="sc00",
        title="Existing plan",
        script_beat="Keep this plan when replacement planning fails.",
        duration_s=8.0,
    )
    save_shot(old)
    save_project(project.model_copy(update={"shot_ids": [old.id]}))
    # first response garbage, second still garbage → no shots or blocked handling
    provider = FakePlanProvider(responses=["NOT JSON {{{", "still not json"])
    orch = RecordingOrchestrator()
    svc = DirectorService(plan_provider=provider, orchestrator=orch)

    with pytest.raises(ValueError, match="unusable"):
        await svc.plan_project(project.id)
    # Two complete calls: initial + one repair
    assert len(provider.calls) == 2
    # A failed replacement is transactional: no synthetic one-shot fallback.
    shots = list_shots(project.id)
    assert [shot.model_dump() for shot in shots] == [old.model_dump()]


@pytest.mark.asyncio
async def test_write_prompts_after_layout(director_dirs):
    from app.agents.director.context_io import save_agent_context
    from app.agents.director.service import DirectorService
    from app.core.projects.models import RefRole, ShotRef
    from app.core.projects.store import save_project

    project = create_project("Prompt", "script")
    actor_dir = director_dirs["library"] / "actors" / "act_prompt_actor"
    actor_dir.mkdir(parents=True)
    actor_asset = LibraryAsset(
        id="act_prompt_actor",
        kind="actors",
        name="Lin Ya",
        notes="Human QC passed wardrobe",
        pipeline_id="actor",
        job_id="job_actor",
        created_at="2026-01-01T00:00:00+00:00",
        files={"fullbody_threeview": "threeview.png"},
        meta={"description": "beige trench coat over a charcoal top"},
    )
    (actor_dir / "threeview.png").write_bytes(b"actor-image" * 300)
    (actor_dir / "asset.json").write_text(
        actor_asset.model_dump_json(indent=2), encoding="utf-8"
    )

    shot = Shot(
        id="sht_prompt00001",
        project_id=project.id,
        scene_id="sc01",
        title="Open",
        script_beat="walk in",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        layout_asset_id="lay_approved01",
        layout_review_status="approved",
        refs=[
            ShotRef(
                role=RefRole.actor,
                asset_id="act_prompt_actor",
                file_key="fullbody_threeview",
                picture_index=1,
            ),
            ShotRef(
                role=RefRole.layout_ref_frame,
                asset_id="lay_approved01",
                picture_index=2,
            )
        ],
        dialogue=["Hello."],
    )
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)
    save_agent_context(
        project.id,
        AgentContext(
            project_id=project.id,
            script_hash="x",
            last_phase="awaiting_prompt",
            shot_summaries=[{"id": shot.id}],
        ),
    )

    sections_json = json.dumps(
        {
            "subject_definitions": (
                "S1 is the lead. <Picture 2> controls the spatial layout."
            ),
            "summary": "A short cafe walk-in.",
            "retention_analysis": "Retain the actor and Layout continuity.",
            "detailed_description": "Actor enters and says Hello.",
            "overall_soundscape": "Cafe ambience.",
            "non_diegetic_music": "Soft piano.",
        }
    )
    provider = FakePlanProvider(response=sections_json)
    orch = RecordingOrchestrator()
    svc = DirectorService(plan_provider=provider, orchestrator=orch)

    updated = await svc.write_prompts_after_layout(shot.id)
    assert updated.prompt_sections.subject_definitions.startswith("S1")
    assert "<Picture 2>" in updated.prompt_sections.subject_definitions
    assert updated.prompt_sections.summary
    assert updated.meta["prompt_layout_asset_id"] == "lay_approved01"
    prompt_user = provider.calls[0][1]
    assert "Lin Ya" in prompt_user
    assert "fullbody_threeview" in prompt_user
    assert "Human QC passed wardrobe" in prompt_user
    assert "beige trench coat over a charcoal top" in prompt_user
    # LLM session used
    assert ("llm_session_enter",) in orch.calls
    assert ("ensure_llm_ready",) in orch.calls


@pytest.mark.asyncio
async def test_write_prompts_visually_analyzes_a_new_layout_once(director_dirs):
    from PIL import Image

    from app.agents.director.service import DirectorService
    from app.core.projects.layouts import LayoutReference, LayoutReviewStatus
    from app.core.projects.models import RefRole
    from app.core.projects.store import save_project

    project = create_project("Visual Layout Prompt", "The Agent waits alone.")
    layout_dir = director_dirs["library"] / "layouts" / "lay_visual_prompt"
    layout_dir.mkdir(parents=True)
    Image.effect_noise((640, 360), 24).convert("RGB").save(layout_dir / "layout.png")
    layout_asset = LibraryAsset(
        id="lay_visual_prompt",
        kind="layouts",
        name="Waiting composition",
        notes="Agent left, empty chair right",
        pipeline_id="ref_frame",
        job_id="job_visual_prompt",
        created_at="2026-01-01T00:00:00+00:00",
        files={"layout": "layout.png"},
        meta={"review_status": "usable"},
    )
    (layout_dir / "asset.json").write_text(
        layout_asset.model_dump_json(indent=2),
        encoding="utf-8",
    )
    shot = Shot(
        id="sht_visual_prompt",
        project_id=project.id,
        scene_id="sc01",
        title="The wait",
        script_beat="The Agent waits alone opposite an empty chair.",
        duration_s=6,
        layout_refs=[
            LayoutReference(
                id="lref_visual_prompt",
                asset_id=layout_asset.id,
                purpose="waiting relationship",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
            )
        ],
        meta={
            "material_review_pending": True,
            "material_changes": {
                "added": [{"role": "layout_ref_frame", "asset_id": layout_asset.id}],
                "removed": [],
                "reordered": [],
            },
        },
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    sections_json = json.dumps(
        {
            "subject_definitions": "<Picture 1> controls the waiting composition.",
            "summary": "The Agent waits across from an empty chair.",
            "retention_analysis": "Retain the observed left-right blocking.",
            "detailed_description": "From 0-6 seconds, the room remains still.",
            "overall_soundscape": "Quiet room tone.",
            "non_diegetic_music": "No music.",
        }
    )

    class VisionPlanProvider(FakePlanProvider):
        def __init__(self) -> None:
            super().__init__(responses=[sections_json, sections_json])
            self.visual_calls: list[tuple[str, list[str]]] = []

        async def complete_with_images(
            self,
            system: str,
            user: str,
            *,
            images: list[str],
            guides: Iterable[str] = (),
        ) -> str:
            self.visual_calls.append((user, images))
            return (
                "Wide eye-level composition; Agent seated in the left third, "
                "empty ivory chair in the right third, interior window upper-right."
            )

    provider = VisionPlanProvider()
    svc = DirectorService(
        plan_provider=provider,
        orchestrator=RecordingOrchestrator(),
    )

    first = await svc.write_prompts_after_layout(shot.id)
    second = await svc.write_prompts_after_layout(shot.id)

    assert len(provider.visual_calls) == 1
    assert provider.visual_calls[0][1]
    assert "Agent seated in the left third" in provider.calls[0].user
    analysis = first.meta["layout_visual_analyses"][layout_asset.id]
    assert "empty ivory chair" in analysis["analysis"]
    assert first.meta["prompt_picture_signature"]
    assert first.meta["material_review_pending"] is False
    assert "material_changes" not in first.meta
    assert second.meta["layout_visual_analyses"] == first.meta["layout_visual_analyses"]


@pytest.mark.asyncio
async def test_write_prompts_grounds_explicit_active_layout_set_and_stores_signature(
    director_dirs,
):
    from app.agents.director.service import DirectorService
    from app.core.projects.layouts import LayoutReference, LayoutReviewStatus
    from app.core.projects.models import RefRole, ShotRef
    from app.core.projects.store import save_project

    project = create_project("Multi Layout Prompt", "Chen enters.")
    shot = Shot(
        id="sht_prompt_multi",
        project_id=project.id,
        scene_id="sc01",
        title="Door crossing",
        script_beat="Chen enters through the glass doorway.",
        duration_s=6.0,
        status=ShotStatus.needs_review,
        layout_asset_id="lay_before",
        refs=[
            ShotRef(
                role=RefRole.actor,
                asset_id="act_chen",
                picture_index=1,
            )
        ],
        layout_refs=[
            LayoutReference(
                id="lr_before",
                asset_id="lay_before",
                purpose="before entry",
                state_description="doorway empty",
                time_hint="before Chen enters",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
            ),
            LayoutReference(
                id="lr_after",
                asset_id="lay_after",
                purpose="post-entry blocking",
                state_description="Chen outside glass",
                time_hint="after Chen enters",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
            ),
        ],
    )
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)
    response = json.dumps(
        {
            "subject_definitions": (
                "<Picture 2> controls the empty doorway geography and composition; "
                "<Picture 3> controls Chen's compatible post-entry blocking."
            ),
            "summary": "Two compatible states in one coherent doorway composition.",
            "retention_analysis": "Keep the doorway and actor identity coherent.",
            "detailed_description": "From 0-6 seconds, Chen crosses the doorway.",
            "overall_soundscape": "Quiet room tone and footsteps.",
            "non_diegetic_music": "No non-diegetic music.",
        }
    )
    provider = FakePlanProvider(response=response)
    svc = DirectorService(
        plan_provider=provider,
        orchestrator=RecordingOrchestrator(),
    )

    updated = await svc.write_prompts_after_layout(shot.id)

    layout_refs = [
        ref for ref in updated.refs if ref.role == RefRole.layout_ref_frame
    ]
    assert [(ref.asset_id, ref.picture_index) for ref in layout_refs] == [
        ("lay_before", 2),
        ("lay_after", 3),
    ]
    prompt_user = provider.calls[0].user
    assert '"picture_index": 2' in prompt_user
    assert '"purpose": "before entry"' in prompt_user
    assert '"state_description": "doorway empty"' in prompt_user
    assert '"state_description": "Chen outside glass"' in prompt_user
    assert updated.meta["prompt_layout_asset_ids"] == [
        "lay_before",
        "lay_after",
    ]
    assert updated.meta["prompt_layout_asset_id"] == "lay_before"
    assert updated.meta["prompt_layout_signature"]


@pytest.mark.asyncio
async def test_write_prompts_rejects_missing_selected_layout_binding(director_dirs):
    from app.agents.director.service import DirectorService
    from app.core.projects.layouts import LayoutReference, LayoutReviewStatus
    from app.core.projects.store import save_project

    project = create_project("Missing Binding", "Chen enters.")
    shot = Shot(
        id="sht_prompt_missing_binding",
        project_id=project.id,
        scene_id="sc01",
        title="Door crossing",
        script_beat="Chen enters.",
        duration_s=6.0,
        layout_refs=[
            LayoutReference(
                id="lr_before",
                asset_id="lay_before",
                purpose="doorway geography",
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
            )
        ],
    )
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)
    invalid = json.dumps(
        {
            "subject_definitions": "Chen is the subject.",
            "summary": "Chen enters.",
            "retention_analysis": "Keep continuity.",
            "detailed_description": "From 0-6 seconds, Chen enters.",
            "overall_soundscape": "Room tone.",
            "non_diegetic_music": "No music.",
        }
    )
    provider = FakePlanProvider(responses=[invalid, invalid])
    svc = DirectorService(
        plan_provider=provider,
        orchestrator=RecordingOrchestrator(),
    )

    with pytest.raises(
        ValueError, match="missing selected Layout binding: <Picture 1>"
    ):
        await svc.write_prompts_after_layout(shot.id)
    assert "doorway geography" in provider.calls[1].user
    assert '"picture_index": 1' in provider.calls[1].user


def test_parse_shot_drafts_valid():
    from app.agents.director.planner import parse_shot_drafts

    drafts = parse_shot_drafts(_one_shot_plan_json())
    assert len(drafts) == 1
    assert drafts[0].scene_id == "sc01"
    assert drafts[0].asset_matches[0].role == "actor"


def test_parse_shot_drafts_preserves_agent_ref_file_key_and_picture_order():
    from app.agents.director.planner import parse_shot_drafts

    raw = json.dumps(
        [
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Reverse angle",
                "script_beat": "She looks across the table.",
                "duration_s": 6,
                "dialogue": [],
                "asset_matches": [
                    {
                        "role": "scene",
                        "asset_id": "scn_cafe",
                        "file_key": "right_view",
                        "picture_index": 1,
                    },
                    {
                        "role": "actor",
                        "asset_id": "act_lin",
                        "file_key": "fullbody_threeview",
                        "picture_index": 2,
                    },
                ],
            }
        ]
    )

    draft = parse_shot_drafts(raw)[0]
    assert draft.asset_matches[0].file_key == "right_view"
    assert draft.asset_matches[0].picture_index == 1
    assert draft.asset_matches[1].file_key == "fullbody_threeview"
    assert draft.asset_matches[1].picture_index == 2


@pytest.mark.parametrize(
    "picture_indices",
    [
        [1, 1],  # duplicate Picture number
        [1, 3],  # gap would make H3 Picture tags disagree with input order
        [0, 1],  # outside H3's 1..9 range
        list(range(1, 11)),  # H3 accepts at most nine image refs
    ],
)
def test_parse_shot_drafts_rejects_invalid_picture_order(picture_indices):
    from app.agents.director.planner import parse_shot_drafts

    matches = [
        {
            "role": "other",
            "asset_id": f"asset_{index}",
            "file_key": "master",
            "picture_index": picture,
        }
        for index, picture in enumerate(picture_indices)
    ]
    raw = json.dumps(
        [
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Bad refs",
                "script_beat": "Test invalid picture order.",
                "duration_s": 5,
                "dialogue": [],
                "asset_matches": matches,
            }
        ]
    )

    with pytest.raises(ValueError):
        parse_shot_drafts(raw)


def test_agent_cast_keeps_two_file_keys_from_the_same_asset():
    from app.agents.director.planner import ShotDraft
    from app.agents.director.service import _heuristic_match

    asset = LibraryAsset(
        id="scn_cafe",
        kind="scenes",
        name="Cafe",
        notes="multi-angle cafe",
        pipeline_id="scene",
        job_id="job_scene",
        created_at="2026-01-01T00:00:00+00:00",
        files={"left_view": "left.png", "right_view": "right.png"},
        meta={},
    )
    draft = ShotDraft.model_validate(
        {
            **CAMERA_DRAFT,
            "scene_id": "sc01",
            "title": "Cross coverage",
            "script_beat": "Use both sides of the cafe for continuity.",
            "duration_s": 6,
            "asset_matches": [
                {
                    "role": "scene",
                    "asset_id": asset.id,
                    "file_key": "left_view",
                    "picture_index": 1,
                },
                {
                    "role": "scene",
                    "asset_id": asset.id,
                    "file_key": "right_view",
                    "picture_index": 2,
                },
            ],
        }
    )

    refs, blocked = _heuristic_match(
        draft,
        inventory=[
            {
                "id": asset.id,
                "kind": asset.kind,
                "name": asset.name,
                "file_keys": list(asset.files),
            }
        ],
        index={asset.id: asset},
    )

    assert blocked == []
    assert [(r.file_key, r.picture_index) for r in refs] == [
        ("left_view", 1),
        ("right_view", 2),
    ]


def test_agent_cast_repairs_a_unique_one_character_file_key_typo():
    """A single Agent typo must not block an otherwise exact asset choice."""
    from app.agents.director.planner import ShotDraft
    from app.agents.director.service import _heuristic_match

    real_key = "风过千里_沙漠日落舞台_01_frontal_performance_direction"
    asset = LibraryAsset(
        id="scn_desert",
        kind="scenes",
        name="风过千里_沙漠日落舞台",
        notes="",
        pipeline_id="scene",
        job_id="job_scene",
        created_at="2026-01-01T00:00:00+00:00",
        files={real_key: "front.png"},
        meta={},
    )
    draft = ShotDraft.model_validate(
        {
            **CAMERA_DRAFT,
            "scene_id": "sc01",
            "title": "Desert performance",
            "script_beat": "The singer performs at sunset.",
            "duration_s": 6,
            "asset_matches": [
                {
                    "role": "scene",
                    "asset_id": asset.id,
                    "file_key": "风过千里_沙雕日落舞台_01_frontal_performance_direction",
                    "picture_index": 1,
                }
            ],
        }
    )

    refs, blocked = _heuristic_match(
        draft,
        inventory=[
            {
                "id": asset.id,
                "kind": asset.kind,
                "name": asset.name,
                "file_keys": [real_key],
            }
        ],
        index={asset.id: asset},
    )

    assert blocked == []
    assert refs[0].file_key == real_key


def test_agent_cast_does_not_guess_an_ambiguous_file_key_typo():
    """Two equally close real keys must remain blocked for human review."""
    from app.agents.director.planner import ShotDraft
    from app.agents.director.service import _heuristic_match

    asset = LibraryAsset(
        id="scn_angles",
        kind="scenes",
        name="Two angles",
        notes="",
        pipeline_id="scene",
        job_id="job_scene",
        created_at="2026-01-01T00:00:00+00:00",
        files={"angle_01": "left.png", "angle_02": "right.png"},
        meta={},
    )
    draft = ShotDraft.model_validate(
        {
            **CAMERA_DRAFT,
            "scene_id": "sc02",
            "title": "Ambiguous angle",
            "script_beat": "The requested angle is unclear.",
            "duration_s": 6,
            "asset_matches": [
                {
                    "role": "scene",
                    "asset_id": asset.id,
                    "file_key": "angle_00",
                    "picture_index": 1,
                }
            ],
        }
    )

    refs, blocked = _heuristic_match(
        draft,
        inventory=[
            {
                "id": asset.id,
                "kind": asset.kind,
                "name": asset.name,
                "file_keys": ["angle_01", "angle_02"],
            }
        ],
        index={asset.id: asset},
    )

    assert blocked == ["invalid file_key 'angle_00' for asset scn_angles"]
    assert refs[0].file_key == "angle_00"


def test_vertical_project_requests_portrait_reference_frames(director_dirs):
    from app.agents.director.service import _reference_frame_aspect_ratio

    project = create_project(
        "Vertical MV",
        "成片9:16，荒漠歌手演唱；原曲音频锁定。",
    )

    assert _reference_frame_aspect_ratio(project) == "9:16"


def test_native_audio_contract_replaces_agent_invented_soundtrack():
    from app.agents.director.service import _apply_source_audio_contract
    from app.core.projects.models import PromptSections, Shot

    shot = Shot(
        id="sht_audio",
        project_id="prj_audio",
        scene_id="sc01",
        title="Song",
        script_beat="sing",
        duration_s=9.06,
        source_audio_path=r"C:\audio\shot01.wav",
        prompt_sections=PromptSections(
            subject_definitions="subject",
            summary="summary",
            retention_analysis="retention",
            detailed_description="move on beat",
            overall_soundscape="Generate a new live vocal and guitar performance.",
            non_diegetic_music="None. End in complete silence.",
        ),
    )

    fixed = _apply_source_audio_contract(shot.prompt_sections, shot)

    assert "shot01.wav" in fixed.overall_soundscape
    assert "immutable" in fixed.overall_soundscape.lower()
    assert "Do not generate" in fixed.non_diegetic_music
    assert fixed.detailed_description == "move on beat"


def test_plan_prompt_distinguishes_h3_refs_from_three_image_edit_passes():
    from app.agents.director.prompts import PLAN_SYSTEM

    assert "H3 receives all 1–9 matches" in PLAN_SYSTEM
    assert "feeds up to 3 of these" not in PLAN_SYSTEM


def test_agent_cast_allows_exactly_nine_image_refs_without_blocking():
    from app.agents.director.planner import ShotDraft
    from app.agents.director.service import _heuristic_match

    assets = [
        LibraryAsset(
            id=f"asset_{index}",
            kind="actors",
            name=f"Actor {index}",
            notes="",
            pipeline_id="external",
            job_id="",
            created_at="2026-01-01T00:00:00+00:00",
            files={"master": f"actor_{index}.png"},
            meta={},
        )
        for index in range(1, 10)
    ]
    draft = ShotDraft.model_validate(
        {
            **CAMERA_DRAFT,
            "scene_id": "sc09",
            "title": "Nine-person tableau",
            "script_beat": "All nine characters remain visually distinct.",
            "duration_s": 6,
            "asset_matches": [
                {
                    "role": "actor",
                    "asset_id": asset.id,
                    "file_key": "master",
                    "picture_index": index,
                }
                for index, asset in enumerate(assets, start=1)
            ],
        }
    )

    refs, blocked = _heuristic_match(
        draft,
        inventory=[
            {
                "id": asset.id,
                "kind": asset.kind,
                "name": asset.name,
                "file_keys": list(asset.files),
            }
            for asset in assets
        ],
        index={asset.id: asset for asset in assets},
    )

    assert blocked == []
    assert [ref.picture_index for ref in refs] == list(range(1, 10))


def test_parse_shot_drafts_strips_think_block():
    from app.agents.director.planner import parse_shot_drafts

    wrapped = (
        "<think>I will output one continuous hallway spray shot.</think>\n"
        + _one_shot_plan_json()
    )
    drafts = parse_shot_drafts(wrapped)
    assert len(drafts) == 1
    assert drafts[0].title


@pytest.mark.asyncio
async def test_plan_project_garbage_does_not_persist_inventory_fallback(director_dirs):
    """LLM CoT-only output must fail explicitly without inventing one shot."""
    from app.agents.director.service import DirectorService
    from app.core.schemas import LibraryAsset

    project = create_project(
        "Fallback plan",
        "一个女孩在走廊里停下脚步，拿起除臭剂喷了喷，然后微笑。全程约5秒。",
    )
    lib = director_dirs["library"]
    # Project-owned assets under projects/{id}/library via paths — seed global
    # then assign project_id so inventory picks them up.
    for kind, aid, name, fname in (
        ("actors", "act_fb001", "girl", "master.png"),
        ("scenes", "scn_fb001", "走廊", "master.png"),
        ("props", "prp_fb001", "除臭剂", "master.jpg"),
    ):
        adir = lib / kind / aid
        adir.mkdir(parents=True, exist_ok=True)
        (adir / fname).write_bytes(b"fake")
        asset = LibraryAsset(
            id=aid,
            kind=kind,
            name=name,
            notes="",
            pipeline_id="external",
            job_id="",
            created_at="2026-01-01T00:00:00+00:00",
            files={"master": fname},
            meta={},
            project_id=project.id,
        )
        (adir / "asset.json").write_text(asset.model_dump_json(indent=2), encoding="utf-8")

    garbage = "<think>long reasoning without any json payload at all</think>\nnot json"
    provider = FakePlanProvider(responses=[garbage, garbage])
    orch = RecordingOrchestrator()
    svc = DirectorService(plan_provider=provider, orchestrator=orch)
    with pytest.raises(ValueError, match="unusable"):
        await svc.plan_project(project.id)
    assert list_shots(project.id) == []


def test_save_load_agent_context(director_dirs):
    from app.agents.director.context_io import load_agent_context, save_agent_context

    project = create_project("Ctx", "s")
    ctx = AgentContext(
        project_id=project.id,
        script_hash="hash1",
        last_phase="planned",
        models_used=["qwen-test"],
        shot_summaries=[{"id": "sht_1", "status": "planning"}],
    )
    save_agent_context(project.id, ctx)
    loaded = load_agent_context(project.id)
    assert loaded is not None
    assert loaded.script_hash == "hash1"
    assert loaded.models_used == ["qwen-test"]
    path = director_dirs["projects"] / project.id / "agent" / "context.json"
    assert path.is_file()
