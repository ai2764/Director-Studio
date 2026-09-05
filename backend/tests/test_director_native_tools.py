from __future__ import annotations

import json

import pytest

from app.agents.director.chat import (
    ACTOR_DESIGN_TOOL,
    DIRECTOR_CHAT_SYSTEM,
    DIRECTOR_TOOL_SCHEMAS,
    GPT_REF_FRAME_TOOL,
    _gpt_generation_context_blob,
    _parse_tools_from_llm,
    _project_context_blob,
    _run_tools,
    handle_chat,
    sanitize_tools_for_pipeline,
)
from app.agents.director.service import _script_hash
from app.agents.director.context_io import save_agent_context
from app.core.projects import (
    LayoutReference,
    LayoutReviewStatus,
    LayoutSourceRef,
    RefRole,
)
from app.core.projects.models import (
    AgentContext,
    PromptSections,
    Shot,
    ShotRef,
    ShotStatus,
)
from app.core.schemas import JobRecord, JobStatus, LibraryAsset
from app.core.schemas import OutputSlot
from app.core.projects.store import (
    create_project,
    list_shots,
    load_project,
    load_shot,
    save_project,
    save_shot,
)


CAMERA_DRAFT = {
    "shot_type": "medium shot",
    "camera_angle": "eye level on the action axis",
    "camera_motion": "locked-off",
    "composition": "primary subject and action readable in one frame",
}


def test_queue_ref_frame_tool_accepts_layout_brief():
    tool = next(
        item
        for item in DIRECTOR_TOOL_SCHEMAS
        if item["function"]["name"] == "queue_ref_frame"
    )
    props = tool["function"]["parameters"]["properties"]

    assert "purpose" in props
    assert "state_description" in props
    assert "time_hint" in props
    assert props["activation_mode"] == {
        "type": "string",
        "enum": ["replace", "append"],
        "default": "replace",
        "description": (
            "replace regenerates the active composition set; append keeps "
            "existing active Layouts and adds a compatible state."
        ),
    }
    assert props["source_refs"]["maxItems"] == 3
    source_schema = props["source_refs"]["items"]
    assert source_schema["required"] == ["role", "asset_id"]
    assert set(source_schema["properties"]) == {
        "role",
        "asset_id",
        "file_key",
        "notes",
    }


def test_gpt_ref_frame_tool_allows_an_empty_or_unbounded_source_array():
    parameters = GPT_REF_FRAME_TOOL["function"]["parameters"]

    assert parameters["required"] == [
        "shot_id",
        "purpose",
        "state_description",
        "source_refs",
        "generation_prompt",
    ]
    assert "minItems" not in parameters["properties"]["source_refs"]
    assert "maxItems" not in parameters["properties"]["source_refs"]


def test_actor_design_tool_defaults_to_local_and_requires_identity_description():
    parameters = ACTOR_DESIGN_TOOL["function"]["parameters"]

    assert parameters["required"] == ["name", "description", "generation_prompt"]
    assert parameters["properties"]["provider"]["default"] == "local"
    assert parameters["properties"]["provider"]["enum"] == ["gpt", "local"]


def test_actor_design_does_not_inject_storyboarding_for_an_unplanned_script(
    tmp_projects_dir,
):
    project = create_project("Casting before shots", "A detective enters.")

    tools, notes = sanitize_tools_for_pipeline(
        [{"name": "queue_actor_design", "args": {"name": "Mara"}}],
        project=project,
        shots=[],
    )

    assert tools == [{"name": "queue_actor_design", "args": {"name": "Mara"}}]
    assert notes == []


@pytest.mark.asyncio
async def test_actor_design_tool_ignores_unrequested_gpt_and_queues_local_job(
    tmp_projects_dir, monkeypatch
):
    import app.agents.director.chat as chat_module

    project = create_project("Mobile casting", "A detective enters.")
    queued = JobRecord(
        id="job_local_actor",
        pipeline_id="actor",
        asset_kind="actors",
        status=JobStatus.queued,
        name="Mara",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        project_id=project.id,
    )
    terminal = queued.model_copy(
        update={
            "status": JobStatus.succeeded,
            "outputs": {
                "master": OutputSlot(
                    key="master",
                    label="Master",
                    filename="master.png",
                    url="/api/files/jobs/job_local_actor/outputs/master.png",
                )
            },
        }
    )
    created = []

    def fake_create_job(**kwargs):
        created.append(kwargs)
        return queued

    async def fake_start(job, *, images=None):
        assert job.id == queued.id
        assert images == {}
        return job

    monkeypatch.setattr(chat_module, "create_job", fake_create_job)
    monkeypatch.setattr(chat_module, "start_pipeline_job", fake_start)
    monkeypatch.setattr(chat_module, "await_pipeline_job", lambda _job_id: _async_value(terminal))
    attached = []
    actions = []

    notes, touched = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "queue_actor_design",
                "args": {
                    "name": "Mara",
                    "description": "A tired municipal detective in her forties.",
                    "provider": "gpt",
                    "generation_prompt": "Create one full-body cinematic character design.",
                },
            }
        ],
        svc=object(),
        actions=actions,
        user_feedback="生成人物设定",
        images=attached,
    )

    assert touched == set()
    assert created[0]["pipeline_id"] == "actor"
    assert created[0]["params"]["provider"] == "local"
    assert actions == ["actor_design:job_local_actor"]
    assert attached[0].url.endswith("/jobs/job_local_actor/outputs/master.png")
    assert "job_local_actor" in notes[0]


@pytest.mark.asyncio
async def test_accept_actor_design_saves_succeeded_job_to_actor_library(
    tmp_projects_dir, monkeypatch
):
    import app.agents.director.chat as chat_module

    project = create_project("Accept casting", "A detective enters.")
    terminal = JobRecord(
        id="job_accept_actor",
        pipeline_id="gpt_actor",
        asset_kind="actors",
        status=JobStatus.succeeded,
        name="Mara",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        project_id=project.id,
    )
    asset = LibraryAsset(
        id="act_mara",
        kind="actors",
        name="Mara",
        pipeline_id="gpt_actor",
        job_id=terminal.id,
        created_at="2026-01-01T00:00:00+00:00",
        files={"master": "master.png"},
        urls={"master": "/api/files/library/actors/act_mara/master.png"},
        project_id=project.id,
    )

    class Pipeline:
        def save_to_library(self, job, **kwargs):
            assert job.id == terminal.id
            assert kwargs["project_id"] == project.id
            return asset

    monkeypatch.setattr(chat_module, "load_job", lambda _job_id: terminal)
    monkeypatch.setattr(chat_module, "get_pipeline", lambda _pipeline_id: Pipeline())
    attached = []
    actions = []

    notes, _ = await _run_tools(
        project_id=project.id,
        tools=[{"name": "accept_actor_design", "args": {"job_id": terminal.id}}],
        svc=object(),
        actions=actions,
        user_feedback="这张可以",
        images=attached,
    )

    assert actions == ["accept_actor_design:act_mara"]
    assert attached[0].url == asset.urls["master"]
    assert "Actor act_mara" in notes[0]


def test_gpt_generation_context_keeps_only_the_exact_requested_shot(
    tmp_projects_dir,
):
    project = create_project("Compact GPT context", "A short locked-room mystery.")
    target = Shot(
        id="shot_target_gpt",
        project_id=project.id,
        scene_id="scene_01",
        title="Door reveal",
        script_beat="Lu watches the marked door open.",
        duration_s=8,
        refs=[
            ShotRef(
                role=RefRole.scene,
                asset_id="scene_01",
                picture_index=1,
                file_key="master",
            )
        ],
    )
    unrelated = Shot(
        id="shot_unrelated",
        project_id=project.id,
        scene_id="scene_02",
        title="Unrelated",
        script_beat="UNRELATED_SENTINEL " * 500,
        duration_s=8,
    )

    blob = _gpt_generation_context_blob(
        project,
        [target, unrelated],
        "Use GPT to generate shot shot_target_gpt",
    )

    assert "shot_target_gpt" in blob
    assert "Lu watches the marked door open." in blob
    assert "shot_unrelated" not in blob
    assert "UNRELATED_SENTINEL" not in blob
    assert len(blob) < 20_000


def test_natural_shot_context_keeps_target_detail_and_compacts_other_shots(
    tmp_projects_dir,
):
    project = create_project("Scoped natural context", "A short interview.")
    target = Shot(
        id="shot_target_context",
        project_id=project.id,
        scene_id="scene_01",
        title="Target exchange",
        script_beat="TARGET_BEAT Mia slides the screenplay.",
        duration_s=6,
    )
    unrelated = Shot(
        id="shot_unrelated_context",
        project_id=project.id,
        scene_id="scene_01",
        title="Other exchange",
        script_beat="UNRELATED_VERBOSE_BEAT " * 500,
        duration_s=6,
    )

    blob = _project_context_blob(
        project,
        [target, unrelated],
        message="How should we revise shot 1's Layout?",
    )

    assert "TARGET_BEAT" in blob
    assert "shot_unrelated_context" in blob
    assert "UNRELATED_VERBOSE_BEAT" not in blob
    assert len(blob) < 12_000


def test_project_wide_context_keeps_current_shot_data_without_layout_history(
    tmp_projects_dir,
    monkeypatch,
):
    project = create_project("Bounded project context", "A twelve-shot interview.")
    shots = []
    for index in range(1, 13):
        active = LayoutReference(
            id=f"lref_active_{index}",
            asset_id=f"lay_active_{index}",
            purpose=f"ACTIVE_PURPOSE_{index}",
            state_description="Current composition state. " * 30,
            review_status=LayoutReviewStatus.usable,
            selected_for_h3=True,
        )
        rejected = LayoutReference(
            id=f"lref_rejected_{index}",
            asset_id=f"lay_rejected_{index}",
            purpose="FULL_HISTORY_SENTINEL " * 100,
            state_description="Rejected history must not fill a project-wide turn. " * 40,
            review_status=LayoutReviewStatus.reject,
            selected_for_h3=False,
        )
        shots.append(
            Shot(
                id=f"sht_context_{index}",
                project_id=project.id,
                scene_id="sc01",
                title=f"Shot {index}",
                script_beat=f"BEAT_{index} Mia and the Agent hold their positions.",
                duration_s=6,
                camera_angle=f"ANGLE_{index}",
                refs=[
                    ShotRef(
                        role=RefRole.scene,
                        asset_id="scn_room",
                        file_key=f"SCENE_KEY_{index}",
                        picture_index=1,
                    ),
                    ShotRef(
                        role=RefRole.layout_ref_frame,
                        asset_id=active.asset_id,
                        file_key="layout",
                        picture_index=2,
                    ),
                ],
                layout_asset_id=active.asset_id,
                layout_refs=[rejected, active],
            )
        )
    monkeypatch.setattr("app.agents.director.service._inventory", lambda _pid: {})
    monkeypatch.setattr(
        "app.agents.director.context_io.load_agent_context",
        lambda _pid: None,
    )

    blob = _project_context_blob(
        project,
        shots,
        message="Review reference continuity across every shot.",
    )

    assert "BEAT_1" in blob
    assert "BEAT_12" in blob
    assert "SCENE_KEY_1" in blob
    assert "SCENE_KEY_12" in blob
    assert "ACTIVE_PURPOSE_1" in blob
    assert "ACTIVE_PURPOSE_12" in blob
    assert "FULL_HISTORY_SENTINEL" not in blob
    assert len(blob) < 30_000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            {
                "content": "",
                "thinking": "I need to choose the correct tool.",
                "tool_calls": [],
                "done_reason": "length",
            },
            "context window",
        ),
        (
            {"content": "", "thinking": "", "tool_calls": []},
            "returned no answer",
        ),
    ],
)
async def test_empty_native_model_response_is_reported_instead_of_project_status(
    tmp_projects_dir,
    response,
    expected,
):
    project = create_project("Empty model response", "A door opens.")

    async def chat_fn(*_args, **_kwargs):
        return response

    result = await handle_chat(
        project_id=project.id,
        message="Please improve the composition.",
        svc=object(),
        chat_fn=chat_fn,
    )

    assert expected in result.reply.lower()
    assert "0 shots" not in result.reply.lower()
    if response.get("done_reason") == "length":
        assert "new chat" not in result.reply.lower()


def test_parse_tools_accepts_bare_single_tool_schema_output():
    reply, tools = _parse_tools_from_llm(
        '{"tool":"queue_gpt_ref_frame","params":{"shot_id":"shot_1"}}'
    )

    assert reply == ""
    assert tools == [
        {
            "name": "queue_gpt_ref_frame",
            "args": {"shot_id": "shot_1"},
        }
    ]


@pytest.mark.asyncio
async def test_native_dict_executes_offered_tool_printed_as_fenced_json(
    tmp_projects_dir,
):
    project = create_project("Printed native tool", "A locked room at night.")
    calls = []

    async def chat_fn(system, user, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "content": (
                    "```json\n"
                    '{"tool":"get_status","params":{}}\n'
                    "```"
                ),
                "thinking": "",
                "tool_calls": [],
            }
        assert kwargs["messages"][-1]["role"] == "tool"
        assert kwargs["messages"][-1]["tool_name"] == "get_status"
        return {
            "content": "Status checked through the offered tool.",
            "thinking": "",
            "tool_calls": [],
        }

    result = await handle_chat(
        project_id=project.id,
        message="Please check project status through the tool.",
        svc=object(),
        chat_fn=chat_fn,
    )

    assert len(calls) == 2
    assert "status" in result.actions
    assert result.reply == "Status checked through the offered tool."


@pytest.mark.asyncio
async def test_gpt_tool_execution_does_not_reparse_user_language(
    tmp_projects_dir, monkeypatch
):
    from app.config import settings

    project = create_project("GPT stale guard", "A door opens.")
    shot = Shot(
        id="shot_gpt_stale",
        project_id=project.id,
        scene_id="scene_01",
        title="Door",
        script_beat="The door opens.",
        duration_s=6,
        status=ShotStatus.ref_frame_pending,
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    save_agent_context(
        project.id,
        AgentContext(
            project_id=project.id,
            script_hash=_script_hash(project.script_text),
            last_phase="awaiting_ref_frame",
            shot_summaries=[{"id": shot.id}],
        ),
    )
    monkeypatch.setattr(settings, "gpt_bridge_base_url", "http://127.0.0.1:8080")
    monkeypatch.setattr(settings, "gpt_bridge_env_file", settings.project_root / "bridge.env")

    called = False

    class Service:
        async def queue_gpt_reference_frame(self, *args, **kwargs):
            nonlocal called
            called = True
            raise RuntimeError("generation sentinel")

    payloads = []
    notes, touched = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "queue_gpt_ref_frame",
                "args": {
                    "shot_id": shot.id,
                    "purpose": "door reveal",
                    "state_description": "door open",
                    "source_refs": [
                        {"role": "scene", "asset_id": "scene_01", "file_key": "master"}
                    ],
                    "generation_prompt": "Image1 controls the set. Return one image.",
                },
            }
        ],
        svc=Service(),
        actions=[],
        result_payloads=payloads,
        user_feedback="GPTでこの参考フレームを生成して",
    )

    assert touched == set()
    assert called is True
    assert payloads[0]["ok"] is False
    assert payloads[0]["provider"] == "gpt"
    assert payloads[0]["stage"] == "generation"
    assert "generation sentinel" in notes[0]


@pytest.mark.asyncio
async def test_explicit_gpt_tool_waits_and_attaches_normal_layout_image(
    tmp_projects_dir, monkeypatch
):
    import app.agents.director.chat as chat_module
    from app.config import settings

    project = create_project("GPT chat image", "Chen enters while Lu hides a recorder.")
    shot = Shot(
        id="shot_gpt_chat",
        project_id=project.id,
        scene_id="scene_01",
        title="Door reveal",
        script_beat="Chen enters behind Lu.",
        duration_s=8,
        status=ShotStatus.ref_frame_pending,
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    save_agent_context(
        project.id,
        AgentContext(
            project_id=project.id,
            script_hash=_script_hash(project.script_text),
            last_phase="awaiting_ref_frame",
            shot_summaries=[{"id": shot.id}],
        ),
    )
    monkeypatch.setattr(settings, "gpt_bridge_base_url", "http://127.0.0.1:8080")
    monkeypatch.setattr(settings, "gpt_bridge_env_file", settings.project_root / "bridge.env")

    terminal = JobRecord(
        id="job_gpt_chat",
        pipeline_id="gpt_ref_frame",
        asset_kind="layouts",
        status=JobStatus.succeeded,
        name="layout:Door reveal",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        project_id=project.id,
    )
    monkeypatch.setattr(chat_module, "await_pipeline_job", lambda _job_id: _async_value(terminal))

    class Service:
        async def queue_gpt_reference_frame(self, shot_id, *, brief):
            assert shot_id == shot.id
            assert len(brief.source_refs) == 4
            current = load_shot(project.id, shot.id)
            assert current is not None
            layout = LayoutReference(
                id="lref_gpt_chat",
                provider="gpt",
                asset_id="lay_gpt_chat",
                job_id=terminal.id,
                job_status=JobStatus.succeeded,
                purpose=brief.purpose,
                state_description=brief.state_description,
                time_hint=brief.time_hint,
                source_refs=brief.source_refs,
                review_status=LayoutReviewStatus.pending_review,
            )
            updated = current.model_copy(
                update={
                    "layout_refs": [layout],
                    "layout_asset_id": layout.asset_id,
                    "ref_frame_job_id": terminal.id,
                    "layout_review_status": "pending_review",
                    "status": ShotStatus.needs_review,
                }
            )
            save_shot(updated)
            return updated

    calls = []
    source_refs = [
        {"role": "scene", "asset_id": "scene_archive", "file_key": "angle_00"},
        {"role": "actor", "asset_id": "actor_lu", "file_key": "master"},
        {"role": "actor", "asset_id": "actor_chen", "file_key": "master"},
        {"role": "prop", "asset_id": "prop_recorder", "file_key": "master"},
    ]

    async def chat_fn(system, user, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            assert "reference-frame-generation" in kwargs["guides"]
            offered = {item["function"]["name"] for item in kwargs["tools"]}
            assert "queue_gpt_ref_frame" in offered
            return {
                "content": "",
                "thinking": "Use four distinct continuity references.",
                "tool_calls": [
                    {
                        "name": "queue_gpt_ref_frame",
                        "arguments": {
                            "shot_id": shot.id,
                            "purpose": "newcomer and recorder continuity",
                            "state_description": "Chen enters behind Lu with the recorder visible",
                            "time_hint": "after the door opens",
                            "source_refs": source_refs,
                            "generation_prompt": (
                                "Image1 controls the archive. Image2 controls Lu. "
                                "Image3 controls Chen. Image4 controls the recorder. "
                                "Return one cinematic image."
                            ),
                        },
                    }
                ],
            }
        raise AssertionError("a completed GPT image tool must be a terminal agent turn")

    result = await handle_chat(
        project_id=project.id,
        message="Use GPT to generate this shot's Layout with the set, both actors, and recorder.",
        svc=Service(),
        chat_fn=chat_fn,
    )

    assert len(calls) == 1
    assert result.reply == (
        "GPT Layout lref_gpt_chat is ready for human review on shot shot_gpt_chat."
    )
    assert f"gpt_ref_frame:{shot.id}" in result.actions
    assert [image.url for image in result.images] == [
        "/api/files/library/layouts/lay_gpt_chat/layout.png"
    ]


@pytest.mark.asyncio
async def test_gpt_tool_attaches_new_sibling_layout_instead_of_legacy_primary(
    tmp_projects_dir, monkeypatch
):
    import app.agents.director.chat as chat_module
    from app.config import settings

    project = create_project("GPT sibling image", "A second visual state is needed.")
    old = LayoutReference(
        id="lref_old",
        provider="comfy",
        asset_id="lay_old",
        job_id="job_old",
        job_status=JobStatus.succeeded,
        purpose="old state",
        review_status=LayoutReviewStatus.usable,
    )
    shot = Shot(
        id="shot_gpt_sibling",
        project_id=project.id,
        scene_id="scene_01",
        title="Second state",
        script_beat="A newcomer appears.",
        duration_s=6,
        status=ShotStatus.needs_review,
        layout_refs=[old],
        layout_asset_id=old.asset_id,
        ref_frame_job_id=old.job_id,
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    monkeypatch.setattr(settings, "gpt_bridge_base_url", "http://127.0.0.1:8080")
    monkeypatch.setattr(settings, "gpt_bridge_env_file", settings.project_root / "bridge.env")
    terminal = JobRecord(
        id="job_gpt_sibling",
        pipeline_id="gpt_ref_frame",
        asset_kind="layouts",
        status=JobStatus.succeeded,
        name="layout:Second state",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        project_id=project.id,
    )
    monkeypatch.setattr(chat_module, "await_pipeline_job", lambda _job_id: _async_value(terminal))

    class Service:
        async def queue_gpt_reference_frame(self, shot_id, *, brief):
            new = LayoutReference(
                id="lref_new_gpt",
                provider="gpt",
                asset_id="lay_new_gpt",
                job_id=terminal.id,
                job_status=JobStatus.succeeded,
                purpose=brief.purpose,
                state_description=brief.state_description,
                source_refs=brief.source_refs,
                review_status=LayoutReviewStatus.pending_review,
            )
            updated = shot.model_copy(update={"layout_refs": [old, new]})
            save_shot(updated)
            return updated

    attached = []
    payloads = []
    notes, touched = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "queue_gpt_ref_frame",
                "args": {
                    "shot_id": shot.id,
                    "purpose": "newcomer state",
                    "state_description": "the newcomer is visible",
                    "source_refs": [
                        {"role": "scene", "asset_id": "scene_01", "file_key": "master"}
                    ],
                    "generation_prompt": "Image1 controls the set. Return one cinematic image.",
                },
            }
        ],
        svc=Service(),
        actions=[],
        result_payloads=payloads,
        user_feedback="Use GPT to generate the newcomer Layout",
        images=attached,
    )

    assert touched == {shot.id}
    assert notes
    assert [image.url for image in attached] == [
        "/api/files/library/layouts/lay_new_gpt/layout.png"
    ]


async def _async_value(value):
    return value


def test_revise_ref_frame_tool_requires_exact_layout_and_feedback():
    tool = next(
        item
        for item in DIRECTOR_TOOL_SCHEMAS
        if item["function"]["name"] == "revise_ref_frame"
    )
    parameters = tool["function"]["parameters"]

    assert parameters["required"] == ["layout_ref_id", "feedback"]
    assert "shot_id" in parameters["properties"]
    assert parameters["properties"]["feedback"]["minLength"] == 1


def test_save_storyboard_tool_exposes_the_complete_typed_shot_draft_shape():
    tool = next(
        item
        for item in DIRECTOR_TOOL_SCHEMAS
        if item["function"]["name"] == "save_storyboard"
    )
    parameters = tool["function"]["parameters"]
    shot_schema = parameters["$defs"]["ShotDraft"]

    assert parameters["required"] == ["expected_script_hash", "shots"]
    assert parameters["properties"]["shots"]["minItems"] == 1
    assert set(shot_schema["properties"]) == {
        "scene_id",
        "shot_id",
        "title",
        "script_beat",
        "shot_type",
        "camera_angle",
        "camera_motion",
        "composition",
        "duration_s",
        "dialogue",
        "asset_matches",
        "voice_matches",
    }
    assert "shot_id" not in shot_schema["required"]
    assert shot_schema["required"] == [
        "scene_id",
        "title",
        "script_beat",
        "shot_type",
        "camera_angle",
        "camera_motion",
        "composition",
    ]
    assert "framing" in shot_schema["properties"]["shot_type"]["description"]
    assert "height" in shot_schema["properties"]["camera_angle"]["description"]
    assert "movement" in shot_schema["properties"]["camera_motion"]["description"]
    assert "screen positions" in shot_schema["properties"]["composition"]["description"]
    assert set(parameters["$defs"]["AssetMatchDraft"]["properties"]) == {
        "role",
        "asset_id",
        "file_key",
        "picture_index",
    }
    assert set(parameters["$defs"]["VoiceMatchDraft"]["properties"]) == {
        "asset_id",
        "audio_index",
        "file_key",
        "speaker",
        "reason",
    }


def test_patch_shot_refs_tool_only_accepts_exact_reference_updates():
    tool = next(
        item
        for item in DIRECTOR_TOOL_SCHEMAS
        if item["function"]["name"] == "patch_shot_refs"
    )
    parameters = tool["function"]["parameters"]
    patch_schema = parameters["$defs"]["ShotRefsPatch"]

    assert parameters["required"] == ["updates"]
    assert parameters["additionalProperties"] is False
    assert set(patch_schema["properties"]) == {"shot_id", "refs"}
    assert patch_schema["required"] == ["shot_id", "refs"]
    assert patch_schema["additionalProperties"] is False
    assert set(parameters["$defs"]["AssetMatchDraft"]["properties"]) == {
        "role",
        "asset_id",
        "file_key",
        "picture_index",
    }


def test_revise_shot_tool_only_accepts_partial_authored_fields():
    tool = next(
        item
        for item in DIRECTOR_TOOL_SCHEMAS
        if item["function"]["name"] == "revise_shot"
    )
    parameters = tool["function"]["parameters"]

    assert parameters["required"] == ["shot_id"]
    assert parameters["additionalProperties"] is False
    assert set(parameters["properties"]) == {
        "shot_id",
        "scene_id",
        "title",
        "script_beat",
        "shot_type",
        "camera_angle",
        "camera_motion",
        "composition",
        "duration_s",
        "dialogue",
    }
    assert "refs" not in parameters["properties"]
    assert "layout_refs" not in parameters["properties"]


@pytest.mark.asyncio
async def test_native_revise_shot_returns_refreshed_storyboard_snapshot(
    tmp_projects_dir,
):
    project = create_project("Revise one", "Mia turns toward camera.")
    shot = Shot(
        id="sht_revise_native",
        project_id=project.id,
        scene_id="sc01",
        title="Mia",
        script_beat="Mia waits.",
        duration_s=5.0,
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    class Service:
        def revise_shot(self, project_id, revision):
            assert project_id == project.id
            assert revision.shot_id == shot.id
            assert revision.script_beat == "Mia faces camera."
            revised = shot.model_copy(update={"script_beat": revision.script_beat})
            save_shot(revised)
            return list_shots(project.id)

    actions: list[str] = []
    payloads: list[dict] = []
    notes, _ = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "revise_shot",
                "args": {
                    "shot_id": shot.id,
                    "script_beat": "Mia faces camera.",
                },
            }
        ],
        svc=Service(),
        actions=actions,
        result_payloads=payloads,
    )

    assert actions == ["revise_shot"]
    assert payloads[0]["storyboard"]["shots"][0]["script_beat"] == (
        "Mia faces camera."
    )
    assert "one shot" in notes[0].lower()


def test_set_shot_scene_ref_tool_accepts_only_an_exact_scene_selection():
    tool = next(
        item
        for item in DIRECTOR_TOOL_SCHEMAS
        if item["function"]["name"] == "set_shot_scene_ref"
    )
    parameters = tool["function"]["parameters"]

    assert parameters["required"] == ["shot_id", "scene_asset_id", "file_key"]
    assert parameters["additionalProperties"] is False
    assert set(parameters["properties"]) == {
        "shot_id",
        "scene_asset_id",
        "file_key",
    }
    assert "exact" in tool["function"]["description"].lower()
    assert "reinterpret" in tool["function"]["description"].lower()
    assert "set_shot_scene_ref" in DIRECTOR_CHAT_SYSTEM
    assert "human-specified file_key is authoritative" in DIRECTOR_CHAT_SYSTEM


def test_model_is_not_offered_legacy_lossy_recast_tool():
    offered = {tool["function"]["name"] for tool in DIRECTOR_TOOL_SCHEMAS}

    assert "patch_shot_refs" in offered
    assert "recast_assets" not in offered


def test_review_asset_coverage_tool_accepts_advisory_recommendations():
    tool = next(
        item
        for item in DIRECTOR_TOOL_SCHEMAS
        if item["function"]["name"] == "review_asset_coverage"
    )
    parameters = tool["function"]["parameters"]
    recommendation = parameters["$defs"]["AssetCoverageRecommendation"]

    assert parameters["required"] == ["expected_script_hash", "status"]
    assert parameters["properties"]["status"]["enum"] == ["reviewed", "skipped"]
    assert set(recommendation["properties"]) == {
        "kind",
        "asset_id",
        "needed_variant",
        "reason",
        "shot_ids",
        "priority",
        "resolution",
    }


@pytest.mark.asyncio
async def test_asset_coverage_review_persists_without_blocking_storyboard(
    tmp_projects_dir,
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.core.library.store import write_asset

    library_root = tmp_path / "library"
    library_root.mkdir()
    monkeypatch.setattr(settings, "library_root", library_root)
    actor = write_asset(
        LibraryAsset(
            id="act_coverage_kai",
            kind="actors",
            name="Kai",
            pipeline_id="external",
            job_id="job_coverage_kai",
            created_at="2026-01-01T00:00:00+00:00",
            files={
                "master": "kai.png",
                "fullbody_threeview": "kai-threeview.png",
            },
        )
    )
    project = create_project(
        "Coverage review",
        "INT. ARCHIVE - NIGHT\nKai enters carrying a brass key.",
    )

    before = json.loads(_project_context_blob(project, []))
    assert before["recommended_next_step"] == "review_asset_coverage"
    assert before["asset_coverage_review"] is None
    inventory_actor = next(
        item for item in before["library_inventory"] if item["id"] == actor.id
    )
    assert inventory_actor["file_keys"] == ["master", "fullbody_threeview"]
    assert "save_storyboard" in {
        tool["function"]["name"] for tool in DIRECTOR_TOOL_SCHEMAS
    }

    actions: list[str] = []
    payloads: list[dict] = []
    notes, _ = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "review_asset_coverage",
                "args": {
                    "expected_script_hash": _script_hash(project.script_text),
                    "status": "reviewed",
                    "notes": "The scene master is adequate, but the entrance needs a prop reference.",
                    "recommendations": [
                        {
                            "kind": "prop",
                            "needed_variant": "brass key hero view",
                            "reason": "The key appears for the first time and drives the reveal.",
                            "shot_ids": ["entrance"],
                            "priority": "high",
                        }
                    ],
                },
            }
        ],
        svc=object(),
        actions=actions,
        result_payloads=payloads,
    )

    stored = load_project(project.id)
    assert stored is not None
    assert stored.asset_coverage_review is not None
    assert stored.asset_coverage_review.script_hash == _script_hash(project.script_text)
    assert stored.asset_coverage_review.status == "reviewed"
    assert stored.asset_coverage_review.recommendations[0].needed_variant == (
        "brass key hero view"
    )
    assert actions == ["review_asset_coverage"]
    assert payloads[0]["asset_coverage_review"]["status"] == "reviewed"
    assert "does not block storyboard authoring" in notes[0]

    after = json.loads(_project_context_blob(stored, []))
    assert after["recommended_next_step"] == "save_storyboard"
    assert after["asset_coverage_review"]["recommendations"][0]["resolution"] == (
        "pending"
    )


@pytest.mark.asyncio
async def test_asset_coverage_review_rejects_a_stale_script_hash(tmp_projects_dir):
    project = create_project("Stale coverage", "INT. ROOM - NIGHT\nA door opens.")
    actions: list[str] = []
    payloads: list[dict] = []

    notes, _ = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "review_asset_coverage",
                "args": {
                    "expected_script_hash": "obsolete",
                    "status": "skipped",
                },
            }
        ],
        svc=object(),
        actions=actions,
        result_payloads=payloads,
    )

    stored = load_project(project.id)
    assert stored is not None
    assert stored.asset_coverage_review is None
    assert actions == []
    assert payloads == [{"ok": False, "error": notes[0].split(": ", 1)[1]}]
    assert "script changed" in notes[0].lower()


@pytest.mark.asyncio
async def test_native_patch_shot_refs_preserves_story_fields_and_returns_snapshot(
    tmp_projects_dir,
    tmp_path,
    monkeypatch,
):
    from app.agents.director.service import DirectorService
    from app.config import settings
    from app.core.library.store import write_asset

    library_root = tmp_path / "library"
    library_root.mkdir()
    monkeypatch.setattr(settings, "library_root", library_root)
    lu = write_asset(
        LibraryAsset(
            id="act_patch_lu",
            kind="actors",
            name="Lu",
            pipeline_id="external",
            job_id="job_lu",
            created_at="2026-01-01T00:00:00+00:00",
            files={"master": "lu.png"},
        )
    )
    kai = write_asset(
        LibraryAsset(
            id="act_patch_kai",
            kind="actors",
            name="Kai",
            pipeline_id="external",
            job_id="job_kai",
            created_at="2026-01-01T00:00:00+00:00",
            files={"master": "kai.png"},
        )
    )
    scene = write_asset(
        LibraryAsset(
            id="scn_patch_archive",
            kind="scenes",
            name="Archive",
            pipeline_id="external",
            job_id="job_scene",
            created_at="2026-01-01T00:00:00+00:00",
            files={"master": "archive.png"},
        )
    )
    project = create_project("Reference patch", "Approved locked screenplay.")
    original = Shot(
        id="sht_patch_refs",
        project_id=project.id,
        scene_id="sc05",
        title="Kai enters",
        script_beat="Kai enters while Lu protects the recorder.",
        duration_s=10.0,
        dialogue=["Routine inspection."],
        refs=[
            ShotRef(
                role=RefRole.actor,
                asset_id=lu.id,
                picture_index=1,
                file_key="master",
            ),
            ShotRef(
                role=RefRole.scene,
                asset_id=scene.id,
                picture_index=2,
                file_key="master",
            ),
        ],
        feedback="Keep the entrance restrained.",
        meta={"continuity": "door 7"},
    )
    save_shot(original)
    save_project(project.model_copy(update={"shot_ids": [original.id]}))
    save_agent_context(
        project.id,
        AgentContext(
            project_id=project.id,
            script_hash=_script_hash(project.script_text),
            last_phase="planned",
            shot_summaries=[],
        ),
    )

    class _NoInferenceProvider:
        async def complete(self, *args, **kwargs):
            raise AssertionError("reference-only patches must not invoke inference")

    class _NoInferenceOrchestrator:
        pass

    svc = DirectorService(
        plan_provider=_NoInferenceProvider(),
        orchestrator=_NoInferenceOrchestrator(),
    )
    captured_followup: dict = {}
    calls = 0

    async def chat_fn(system: str, user: str, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "content": "",
                "thinking": "Bind the newly approved Kai asset only.",
                "tool_calls": [
                    {
                        "name": "patch_shot_refs",
                        "arguments": {
                            "updates": [
                                {
                                    "shot_id": original.id,
                                    "refs": [
                                        {
                                            "role": "actor",
                                            "asset_id": lu.id,
                                            "file_key": "master",
                                            "picture_index": 1,
                                        },
                                        {
                                            "role": "actor",
                                            "asset_id": kai.id,
                                            "file_key": "master",
                                            "picture_index": 2,
                                        },
                                        {
                                            "role": "scene",
                                            "asset_id": scene.id,
                                            "file_key": "master",
                                            "picture_index": 3,
                                        },
                                    ],
                                }
                            ]
                        },
                    }
                ],
            }
        captured_followup.update(kwargs)
        return {"content": "Kai is bound.", "thinking": "", "tool_calls": []}

    result = await handle_chat(
        project_id=project.id,
        message="Add the approved Kai reference without rewriting the storyboard.",
        svc=svc,
        chat_fn=chat_fn,
    )

    stored = load_shot(project.id, original.id)
    assert stored is not None
    before = original.model_dump(exclude={"refs", "meta"})
    after = stored.model_dump(exclude={"refs", "meta"})
    assert after == before
    assert [
        (ref.role.value, ref.asset_id, ref.file_key, ref.picture_index)
        for ref in stored.refs
    ] == [
        ("actor", lu.id, "master", 1),
        ("actor", kai.id, "master", 2),
        ("scene", scene.id, "master", 3),
    ]
    assert stored.meta["continuity"] == "door 7"
    assert stored.meta["material_review_pending"] is True
    assert stored.meta["prompt_picture_signature"] == ""
    assert stored.meta["prompt_layout_signature"] == ""
    assert stored.meta["material_changes"] == {
        "added": [
            {
                "role": "actor",
                "asset_id": kai.id,
                "file_key": "master",
                "picture_index": 2,
            }
        ],
        "removed": [],
        "reordered": [
            {
                "role": "scene",
                "asset_id": scene.id,
                "file_key": "master",
                "from_picture_index": 2,
                "to_picture_index": 3,
            }
        ],
    }
    assert result.actions == ["llm", "patch_shot_refs"]
    tool_message = next(
        message
        for message in captured_followup["messages"]
        if message.get("role") == "tool"
        and message.get("tool_name") == "patch_shot_refs"
    )
    payload = json.loads(tool_message["content"])
    assert payload["ok"] is True
    assert payload["storyboard"]["shots"][0]["script_beat"] == original.script_beat
    assert payload["storyboard"]["shots"][0]["dialogue"] == original.dialogue


@pytest.mark.asyncio
async def test_native_set_shot_scene_ref_replaces_only_the_target_scene_binding(
    tmp_projects_dir,
    tmp_path,
    monkeypatch,
):
    """A precise human scene choice must not rebuild refs or validate other shots."""
    from app.agents.director.service import DirectorService
    from app.config import settings
    from app.core.library.store import write_asset

    library_root = tmp_path / "library"
    library_root.mkdir()
    monkeypatch.setattr(settings, "library_root", library_root)
    selected_scene = write_asset(
        LibraryAsset(
            id="scn_selected_interview",
            kind="scenes",
            name="Interview room",
            pipeline_id="external",
            job_id="job_selected_scene",
            created_at="2026-01-01T00:00:00+00:00",
            files={
                "Interview-room_04_front_left_view_h315_v0": "h315.png",
            },
        )
    )
    project = create_project("Precise scene override", "Mia takes the chair.")
    unrelated = Shot(
        id="sht_unrelated_broken_layout",
        project_id=project.id,
        scene_id="sc01",
        title="Unrelated",
        script_beat="An unrelated approved composition remains on another shot.",
        duration_s=4.0,
        refs=[
            ShotRef(
                role=RefRole.layout_ref_frame,
                asset_id="lay_inaccessible",
                picture_index=1,
                file_key="layout",
            )
        ],
    )
    target = Shot(
        id="sht_scene_override",
        project_id=project.id,
        scene_id="sc03",
        title="Taking the Chair",
        script_beat="Mia places the screenplay down and takes the chair.",
        shot_type="medium",
        camera_angle="eye-level from the room axis",
        camera_motion="locked-off",
        composition="Mia centered; Agent soft at frame edge.",
        duration_s=6.0,
        dialogue=["No dialogue."],
        refs=[
            ShotRef(
                role=RefRole.actor,
                asset_id="act_mia",
                picture_index=1,
                file_key="bust_threeview",
                notes="preserve actor",
            ),
            ShotRef(
                role=RefRole.prop,
                asset_id="prp_screenplay",
                picture_index=2,
                file_key="master",
                notes="preserve prop",
            ),
            ShotRef(
                role=RefRole.scene,
                asset_id="scn_previous_interview",
                picture_index=3,
                file_key="Interview-room_03_right_side_view_h90_v0",
                notes="preserve scene note",
            ),
            ShotRef(
                role=RefRole.layout_ref_frame,
                asset_id="lay_target",
                picture_index=4,
                file_key="layout",
                notes="preserve layout",
            ),
        ],
        feedback="Keep the entrance restrained.",
        meta={"continuity": "approved"},
    )
    save_shot(unrelated)
    save_shot(target)
    save_project(project.model_copy(update={"shot_ids": [unrelated.id, target.id]}))

    svc = DirectorService(plan_provider=object(), orchestrator=object())
    actions: list[str] = []
    payloads: list[dict] = []
    notes, _ = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "set_shot_scene_ref",
                "args": {
                    "shot_id": target.id,
                    "scene_asset_id": selected_scene.id,
                    "file_key": "Interview-room_04_front_left_view_h315_v0",
                },
            }
        ],
        svc=svc,
        actions=actions,
        result_payloads=payloads,
    )

    stored = load_shot(project.id, target.id)
    assert stored is not None
    assert stored.model_dump(exclude={"refs"}) == target.model_dump(exclude={"refs"})
    assert [ref.model_dump() for ref in stored.refs[:2]] == [
        ref.model_dump() for ref in target.refs[:2]
    ]
    assert stored.refs[2].model_dump() == {
        "role": "scene",
        "asset_id": selected_scene.id,
        "picture_index": 3,
        "notes": "preserve scene note",
        "file_key": "Interview-room_04_front_left_view_h315_v0",
    }
    assert stored.refs[3].model_dump() == target.refs[3].model_dump()
    assert load_shot(project.id, unrelated.id) == unrelated
    assert actions == ["set_shot_scene_ref"]
    assert "only the scene Picture binding" in notes[0]
    assert payloads[0]["storyboard"]["shots"][1]["refs"][2]["file_key"] == (
        "Interview-room_04_front_left_view_h315_v0"
    )


def test_set_shot_scene_ref_rejects_invalid_selection_without_writing(
    tmp_projects_dir,
    tmp_path,
    monkeypatch,
):
    from app.agents.director.service import DirectorService
    from app.config import settings
    from app.core.library.store import write_asset

    library_root = tmp_path / "library"
    library_root.mkdir()
    monkeypatch.setattr(settings, "library_root", library_root)
    scene = write_asset(
        LibraryAsset(
            id="scn_atomic_scene",
            kind="scenes",
            name="Interview room",
            pipeline_id="external",
            job_id="job_atomic_scene",
            created_at="2026-01-01T00:00:00+00:00",
            files={"front_left": "front-left.png"},
        )
    )
    actor = write_asset(
        LibraryAsset(
            id="act_not_a_scene",
            kind="actors",
            name="Mia",
            pipeline_id="external",
            job_id="job_atomic_actor",
            created_at="2026-01-01T00:00:00+00:00",
            files={"front_left": "mia.png"},
        )
    )
    project = create_project("Atomic scene override", "Mia waits.")
    original = Shot(
        id="sht_atomic_scene",
        project_id=project.id,
        scene_id="sc01",
        title="Mia waits",
        script_beat="Mia waits in the interview room.",
        duration_s=5.0,
        refs=[
            ShotRef(
                role=RefRole.scene,
                asset_id="scn_original",
                picture_index=1,
                file_key="master",
            ),
            ShotRef(
                role=RefRole.actor,
                asset_id="act_mia",
                picture_index=2,
                file_key="master",
            ),
        ],
    )
    save_shot(original)
    save_project(project.model_copy(update={"shot_ids": [original.id]}))
    svc = DirectorService(plan_provider=object(), orchestrator=object())

    invalid_cases = [
        ("sht_missing", scene.id, "front_left", "shot not found"),
        (original.id, "scn_missing", "front_left", "scene asset not found"),
        (original.id, actor.id, "front_left", "must be a scenes asset"),
        (original.id, scene.id, "wrong_key", "invalid scene file_key"),
    ]
    for shot_id, scene_asset_id, file_key, expected_error in invalid_cases:
        with pytest.raises(ValueError, match=expected_error):
            svc.set_shot_scene_ref(
                project.id,
                shot_id=shot_id,
                scene_asset_id=scene_asset_id,
                file_key=file_key,
            )
        assert load_shot(project.id, original.id) == original


@pytest.mark.asyncio
async def test_native_save_storyboard_real_service_returns_the_stored_snapshot(
    tmp_projects_dir,
    tmp_path,
    monkeypatch,
):
    from app.agents.director.service import DirectorService
    from app.config import settings
    from app.core.library.store import write_asset

    library_root = tmp_path / "library"
    library_root.mkdir()
    monkeypatch.setattr(settings, "library_root", library_root)
    actor = write_asset(
        LibraryAsset(
            id="act_native_real",
            kind="actors",
            name="Mara",
            pipeline_id="external",
            job_id="job_actor",
            created_at="2026-01-01T00:00:00+00:00",
            files={"master": "mara.png"},
        )
    )
    scene = write_asset(
        LibraryAsset(
            id="scn_native_real",
            kind="scenes",
            name="Archive",
            pipeline_id="external",
            job_id="job_scene",
            created_at="2026-01-01T00:00:00+00:00",
            files={"angle_00": "archive.png"},
        )
    )
    voice = write_asset(
        LibraryAsset(
            id="voi_native_real",
            kind="voices",
            name="Mara whisper",
            pipeline_id="external",
            job_id="job_voice",
            created_at="2026-01-01T00:00:00+00:00",
            files={"reference": "mara.wav"},
            meta={"h3_ready": True},
        )
    )
    project = create_project(
        "Real native storyboard",
        "INT. ARCHIVE - NIGHT\nMara finds the recorder and whispers into it.",
    )
    save_project(project.model_copy(update={"script_locked": True}))

    class _ValidationOnlyProvider:
        calls = []

        async def complete(self, system, user, *, guides=()):
            self.calls.append((system, user, tuple(guides)))
            if tuple(guides) != ("storyboard-validation",):
                raise AssertionError("save_storyboard must not invoke legacy planning")
            return json.dumps({"valid": True, "issues": []})

    class _ValidationOrchestrator:
        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

        def llm_session(self, *, release_on_exit=True):
            return self._Session()

        async def ensure_llm_ready(self):
            return None

    provider = _ValidationOnlyProvider()
    svc = DirectorService(
        plan_provider=provider,
        orchestrator=_ValidationOrchestrator(),
    )
    captured_followup: dict = {}
    beat = (
        "Mara lifts the recorder from beneath the open ledger, watches its red diode "
        "pulse twice, then whispers the warning without breaking eye contact with the door."
    )
    calls = 0

    async def chat_fn(system: str, user: str, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "content": "",
                "thinking": "Saving the authored storyboard.",
                "tool_calls": [
                    {
                        "name": "save_storyboard",
                        "arguments": {
                            "expected_script_hash": _script_hash(project.script_text),
                            "shots": [
                                {
                                    **CAMERA_DRAFT,
                                    "scene_id": "sc01",
                                    "title": "Recorder warning",
                                    "script_beat": beat,
                                    "duration_s": 9.25,
                                    "dialogue": ["Do not open the archive door."],
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
                                            "file_key": "angle_00",
                                            "picture_index": 2,
                                        },
                                    ],
                                    "voice_matches": [
                                        {
                                            "asset_id": voice.id,
                                            "audio_index": 1,
                                            "file_key": "reference",
                                            "speaker": "Mara",
                                            "reason": "close whisper",
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                ],
            }
        captured_followup.update(kwargs)
        return {
            "content": "The stored storyboard is ready.",
            "thinking": "",
            "tool_calls": [],
        }

    result = await handle_chat(
        project_id=project.id,
        message="Author and save the storyboard without changing the script.",
        svc=svc,
        chat_fn=chat_fn,
    )

    stored = list_shots(project.id)
    assert len(stored) == 1
    assert stored[0].script_beat == beat
    assert stored[0].dialogue == ["Do not open the archive door."]
    assert len(provider.calls) == 1
    assert provider.calls[0][2] == ("storyboard-validation",)
    assert result.actions == ["llm", "save_storyboard"]
    assert result.reply == "Storyboard saved: 1 shot."
    assert [shot.id for shot in result.shots] == [shot.id for shot in stored]
    tool_message = next(
        message
        for message in captured_followup["messages"]
        if message.get("role") == "tool"
        and message.get("tool_name") == "save_storyboard"
    )
    payload = json.loads(tool_message["content"])
    assert payload["ok"] is True
    assert payload["storyboard"]["shots"] == [
        {
            "id": shot.id,
            **CAMERA_DRAFT,
            "scene_id": shot.scene_id,
            "title": shot.title,
            "duration_s": shot.duration_s,
            "script_beat": shot.script_beat,
            "dialogue": list(shot.dialogue),
            "refs": [ref.model_dump(mode="json") for ref in shot.refs],
            "voice_refs": [
                ref.model_dump(mode="json") for ref in shot.voice_refs
            ],
        }
        for shot in stored
    ]

@pytest.mark.asyncio
async def test_native_save_storyboard_rejects_stale_hash_as_a_tool_failure(
    tmp_projects_dir,
):
    project = create_project("Stale native save", "INT. ROOM - NIGHT\nApproved beat.")
    old = Shot(
        id="sht_native_stale_old",
        project_id=project.id,
        scene_id="sc01",
        title="Existing plan",
        script_beat="The existing shot remains untouched.",
        duration_s=5.0,
    )
    save_shot(old)
    save_project(project.model_copy(update={"shot_ids": [old.id]}))
    captured_followup: dict = {}

    class _Service:
        async def save_storyboard(
            self,
            project_id,
            drafts,
            expected_script_hash,
            **kwargs,
        ):
            raise ValueError("script changed; storyboard candidate is stale")

    calls = 0

    async def chat_fn(system: str, user: str, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "content": "",
                "thinking": "",
                "tool_calls": [
                    {
                        "name": "save_storyboard",
                        "arguments": {
                            "expected_script_hash": "obsolete",
                            "shots": [
                                {
                                    **CAMERA_DRAFT,
                                    "scene_id": "sc02",
                                    "title": "Stale replacement",
                                    "script_beat": "An obsolete beat.",
                                }
                            ],
                        },
                    }
                ],
            }
        captured_followup.update(kwargs)
        return {
            "content": "The save was rejected because the screenplay changed.",
            "thinking": "",
            "tool_calls": [],
        }

    result = await handle_chat(
        project_id=project.id,
        message="Save this storyboard.",
        svc=_Service(),
        chat_fn=chat_fn,
    )

    tool_message = next(
        message
        for message in captured_followup["messages"]
        if message.get("role") == "tool"
    )
    payload = json.loads(tool_message["content"])
    assert payload["ok"] is False
    assert "stale" in payload["error"]
    assert result.actions == ["llm"]
    assert result.reply.startswith(
        "Storyboard was not saved; existing project shots remain unchanged."
    )
    assert load_project(project.id).script_text == project.script_text
    assert load_shot(project.id, old.id).model_dump() == old.model_dump()


@pytest.mark.asyncio
async def test_failed_storyboard_save_blocks_later_layout_work_in_same_batch(
    tmp_projects_dir,
):
    project = create_project("Blocked downstream layout", "INT. ROOM - DAY\nMia waits.")
    old = Shot(
        id="sht_blocked_downstream",
        project_id=project.id,
        scene_id="sc01",
        title="Existing plan",
        script_beat="Mia waits.",
        duration_s=5.0,
        status=ShotStatus.ref_frame_pending,
    )
    save_shot(old)
    save_project(project.model_copy(update={"shot_ids": [old.id]}))

    class _Service:
        layout_calls = 0

        async def save_storyboard(self, *args, **kwargs):
            raise ValueError("candidate rejected")

        async def queue_reference_frame(self, *args, **kwargs):
            self.layout_calls += 1
            raise AssertionError("layout work must not run after a failed save")

    svc = _Service()
    notes, _ = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "save_storyboard",
                "args": {
                    "expected_script_hash": _script_hash(project.script_text),
                    "shots": [
                        {
                            **CAMERA_DRAFT,
                            "scene_id": "sc01",
                            "title": "Rejected replacement",
                            "script_beat": "Mia waits.",
                            "duration_s": 5.0,
                        }
                    ],
                },
            },
            {
                "name": "queue_ref_frame",
                "args": {
                    "shot_id": old.id,
                    "purpose": "composition",
                    "state_description": "Mia waits in the room.",
                    "source_refs": [],
                },
            },
        ],
        svc=svc,
        actions=[],
    )

    assert svc.layout_calls == 0
    assert any("queue_ref_frame blocked" in note for note in notes)


@pytest.mark.asyncio
async def test_native_semantic_rejection_returns_to_same_agent_for_a_repaired_save(
    tmp_projects_dir,
    tmp_path,
    monkeypatch,
):
    from app.agents.director.service import DirectorService
    from app.config import settings
    from app.core.library.store import write_asset

    library_root = tmp_path / "semantic-repair-library"
    library_root.mkdir()
    monkeypatch.setattr(settings, "library_root", library_root)
    actor = write_asset(
        LibraryAsset(
            id="act_semantic_repair",
            kind="actors",
            name="Mara",
            pipeline_id="external",
            job_id="job_actor",
            created_at="2026-01-01T00:00:00+00:00",
            files={"master": "mara.png"},
        )
    )
    scene = write_asset(
        LibraryAsset(
            id="scn_semantic_repair",
            kind="scenes",
            name="Archive",
            pipeline_id="external",
            job_id="job_scene",
            created_at="2026-01-01T00:00:00+00:00",
            files={"master": "archive.png"},
        )
    )
    project = create_project(
        "Semantic repair",
        "INT. ARCHIVE - NIGHT\nMara finds a recorder, hears her own warning, and backs away.",
    )
    old = Shot(
        id="sht_semantic_repair_old",
        project_id=project.id,
        scene_id="sc00",
        title="Old storyboard",
        script_beat="The old plan survives until a repair passes.",
        duration_s=12.0,
    )
    save_shot(old)
    save_project(
        project.model_copy(update={"script_locked": True, "shot_ids": [old.id]})
    )

    class _ValidationProvider:
        def __init__(self):
            self.calls = []
            self.responses = [
                json.dumps(
                    {
                        "valid": False,
                        "issues": [
                            "Shot 1 contradicts the screenplay by having Mara destroy the recorder."
                        ],
                    }
                ),
                json.dumps({"valid": True, "issues": []}),
            ]

        async def complete(self, system, user, *, guides=()):
            self.calls.append((system, user, tuple(guides)))
            return self.responses.pop(0)

    class _Orchestrator:
        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

        def llm_session(self, *, release_on_exit=True):
            return self._Session()

        async def ensure_llm_ready(self):
            return None

    provider = _ValidationProvider()
    svc = DirectorService(plan_provider=provider, orchestrator=_Orchestrator())
    calls: list[dict] = []
    first_candidate = {
        "expected_script_hash": _script_hash(project.script_text),
        "shots": [
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Destructive contradiction",
                "script_beat": "Mara destroys the recorder and leaves.",
                "duration_s": 12.0,
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
        ],
    }
    repaired_candidate = {
        "expected_script_hash": _script_hash(project.script_text),
        "shots": [
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Warning retreat",
                "script_beat": "Mara hears her own warning and backs away from the intact recorder.",
                "duration_s": 12.0,
                "asset_matches": first_candidate["shots"][0]["asset_matches"],
            }
        ],
    }

    async def chat_fn(system: str, user: str, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "content": "",
                "thinking": "",
                "tool_calls": [
                    {"name": "save_storyboard", "arguments": first_candidate}
                ],
            }
        if len(calls) == 2:
            tool_payload = json.loads(kwargs["messages"][-1]["content"])
            assert tool_payload["ok"] is False
            assert "contradicts the screenplay" in tool_payload["error"]
            assert tool_payload["issues"] == [
                "Shot 1 contradicts the screenplay by having Mara destroy the recorder."
            ]
            assert [shot.model_dump() for shot in list_shots(project.id)] == [
                old.model_dump()
            ]
            return {
                "content": "",
                "thinking": "I will repair my own candidate.",
                "tool_calls": [
                    {"name": "save_storyboard", "arguments": repaired_candidate}
                ],
            }
        tool_payload = json.loads(kwargs["messages"][-1]["content"])
        assert tool_payload["ok"] is True
        assert tool_payload["storyboard"]["shots"][0]["title"] == "Warning retreat"
        return {
            "content": "The repaired storyboard was validated and saved.",
            "thinking": "",
            "tool_calls": [],
        }

    message = "Save a storyboard of at least 12 seconds without changing the screenplay."
    result = await handle_chat(
        project_id=project.id,
        message=message,
        svc=svc,
        chat_fn=chat_fn,
    )

    assert result.reply == "Storyboard saved: 1 shot."
    assert result.actions == ["llm", "save_storyboard"]
    assert [shot.title for shot in result.shots] == ["Warning retreat"]
    assert len(provider.calls) == 2
    assert all(message in call[1] for call in provider.calls)
    assert all("12.0" in call[1] for call in provider.calls)


@pytest.mark.asyncio
async def test_failed_storyboard_save_promise_forces_an_actual_resubmission(
    tmp_projects_dir,
):
    project = create_project("Retry promise", "INT. ROOM - DAY\nMia waits.")

    class _Service:
        def __init__(self):
            self.calls = 0

        async def save_storyboard(self, project_id, drafts, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ValueError("Shot 6 contains two camera positions in one clip")
            shot = Shot(
                id="sht_corrected_retry",
                project_id=project_id,
                scene_id="sc01",
                title=drafts[0].title,
                script_beat=drafts[0].script_beat,
                duration_s=drafts[0].duration_s,
            )
            save_shot(shot)
            save_project(project.model_copy(update={"shot_ids": [shot.id]}))
            return [shot]

    svc = _Service()
    first = {
        "expected_script_hash": _script_hash(project.script_text),
        "shots": [{
            **CAMERA_DRAFT,
            "scene_id": "sc01",
            "title": "Invalid reverse pair",
            "script_beat": "Mia waits.",
            "duration_s": 8.0,
        }],
    }
    corrected = {
        **first,
        "shots": [{
            **first["shots"][0],
            "title": "Corrected single perspective",
            "camera_motion": "locked-off from Mia's side for one continuous take",
        }],
    }
    calls: list[dict] = []

    async def chat_fn(system: str, user: str, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "content": "",
                "thinking": "",
                "tool_calls": [{"name": "save_storyboard", "arguments": first}],
            }
        if len(calls) == 2:
            payload = json.loads(kwargs["messages"][-1]["content"])
            assert payload["ok"] is False
            return {
                "content": "Fixing Shot 6 and resubmitting now.",
                "thinking": "",
                "tool_calls": [],
            }
        if len(calls) == 3:
            assert kwargs["messages"][-1]["role"] == "user"
            assert "call save_storyboard" in kwargs["messages"][-1]["content"].lower()
            return {
                "content": "",
                "thinking": "",
                "tool_calls": [
                    {"name": "save_storyboard", "arguments": corrected}
                ],
            }
        return {
            "content": "Saved the corrected storyboard.",
            "thinking": "",
            "tool_calls": [],
        }

    result = await handle_chat(
        project_id=project.id,
        message="Replace the storyboard and save it.",
        svc=svc,
        chat_fn=chat_fn,
    )

    assert len(calls) == 4
    assert svc.calls == 2
    assert result.reply == "Storyboard saved: 1 shot."
    assert [shot.title for shot in result.shots] == [
        "Corrected single perspective"
    ]


@pytest.mark.asyncio
async def test_explicit_storyboard_save_thinking_only_response_gets_one_continuation(
    tmp_projects_dir,
):
    project = create_project("Thinking only", "INT. ROOM - DAY\nMia waits.")

    class _Service:
        def __init__(self):
            self.calls = 0

        async def save_storyboard(self, project_id, drafts, *args, **kwargs):
            self.calls += 1
            shot = Shot(
                id="sht_thinking_continuation",
                project_id=project_id,
                scene_id="sc01",
                title=drafts[0].title,
                script_beat=drafts[0].script_beat,
                duration_s=drafts[0].duration_s,
            )
            save_shot(shot)
            save_project(project.model_copy(update={"shot_ids": [shot.id]}))
            return [shot]

    svc = _Service()
    submission = {
        "expected_script_hash": _script_hash(project.script_text),
        "shots": [{
            **CAMERA_DRAFT,
            "scene_id": "sc01",
            "title": "Persisted after continuation",
            "script_beat": "Mia waits.",
            "duration_s": 8.0,
        }],
    }
    calls: list[dict] = []

    async def chat_fn(system: str, user: str, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "content": "",
                "thinking": "I have constructed the complete payload.",
                "tool_calls": [],
            }
        if len(calls) == 2:
            assert kwargs["messages"][-1]["role"] == "user"
            assert "call save_storyboard" in kwargs["messages"][-1]["content"].lower()
            return {
                "content": "",
                "thinking": "",
                "tool_calls": [
                    {"name": "save_storyboard", "arguments": submission}
                ],
            }
        return {
            "content": "Saved one complete storyboard.",
            "thinking": "",
            "tool_calls": [],
        }

    result = await handle_chat(
        project_id=project.id,
        message="Call save_storyboard now and persist the complete storyboard.",
        svc=svc,
        chat_fn=chat_fn,
    )

    assert len(calls) == 3
    assert svc.calls == 1
    assert result.reply == "Storyboard saved: 1 shot."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_verdict",
    [
        pytest.param(
            {"valid": "true", "issues": []},
            id="string-true",
        ),
        pytest.param(
            {"valid": 1, "issues": []},
            id="integer-one",
        ),
        pytest.param(
            {"valid": True, "issues": [], "replacement_shots": []},
            id="extra-replacement-key",
        ),
    ],
)
async def test_native_malformed_semantic_verdict_is_a_transactional_tool_failure(
    tmp_projects_dir,
    tmp_path,
    monkeypatch,
    malformed_verdict,
):
    from app.agents.director.service import DirectorService
    from app.config import settings
    from app.core.library.store import load_asset, write_asset

    library_root = tmp_path / "malformed-verdict-library"
    library_root.mkdir()
    monkeypatch.setattr(settings, "library_root", library_root)
    actor = write_asset(
        LibraryAsset(
            id="act_malformed_verdict",
            kind="actors",
            name="Mara",
            pipeline_id="external",
            job_id="job_actor",
            created_at="2026-01-01T00:00:00+00:00",
            files={"master": "mara.png"},
        )
    )
    scene = write_asset(
        LibraryAsset(
            id="scn_malformed_verdict",
            kind="scenes",
            name="Archive",
            pipeline_id="external",
            job_id="job_scene",
            created_at="2026-01-01T00:00:00+00:00",
            files={"master": "archive.png"},
        )
    )
    screenplay = "INT. ARCHIVE - NIGHT\nMara listens to the intact recorder."
    project = create_project("Malformed semantic verdict", screenplay)
    old = Shot(
        id="sht_malformed_verdict_old",
        project_id=project.id,
        scene_id="sc00",
        title="Existing grounded plan",
        script_beat="Mara watches the intact recorder from across the archive.",
        duration_s=8.0,
        refs=[
            ShotRef(
                role=RefRole.actor,
                asset_id=actor.id,
                file_key="master",
                picture_index=1,
                notes="existing-ref",
            )
        ],
    )
    save_shot(old)
    save_project(
        project.model_copy(
            update={"script_locked": True, "shot_ids": [old.id]}
        )
    )

    class _MalformedProvider:
        async def complete(self, *args, **kwargs):
            return json.dumps(malformed_verdict)

    class _Orchestrator:
        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

        def llm_session(self, *, release_on_exit=True):
            return self._Session()

        async def ensure_llm_ready(self):
            return None

    svc = DirectorService(
        plan_provider=_MalformedProvider(),
        orchestrator=_Orchestrator(),
    )
    captured_payload = {}
    calls = 0
    submission = {
        "expected_script_hash": _script_hash(screenplay),
        "shots": [
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Candidate replacement",
                "script_beat": "Mara crosses to the intact recorder.",
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
        ],
    }

    async def chat_fn(system: str, user: str, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "content": "",
                "thinking": "",
                "tool_calls": [
                    {"name": "save_storyboard", "arguments": submission}
                ],
            }
        captured_payload.update(json.loads(kwargs["messages"][-1]["content"]))
        return {
            "content": "The malformed validation verdict blocked the save.",
            "thinking": "",
            "tool_calls": [],
        }

    result = await handle_chat(
        project_id=project.id,
        message="Save this storyboard without changing the locked screenplay.",
        svc=svc,
        chat_fn=chat_fn,
    )

    assert captured_payload["ok"] is False
    assert "invalid structured verdict" in captured_payload["error"]
    assert result.actions == ["llm"]
    assert [shot.model_dump() for shot in list_shots(project.id)] == [old.model_dump()]
    persisted = load_project(project.id)
    assert persisted is not None
    assert persisted.script_text == screenplay
    assert persisted.script_locked is True
    assert persisted.shot_ids == [old.id]
    assert load_asset("actors", actor.id).project_id is None
    assert load_asset("scenes", scene.id).project_id is None


@pytest.mark.asyncio
async def test_native_storyboard_submission_budget_blocks_a_fourth_save(
    tmp_projects_dir,
):
    project = create_project(
        "Bounded repairs",
        "INT. ROOM - NIGHT\nMara listens to the intact recorder.",
    )
    old = Shot(
        id="sht_bounded_old",
        project_id=project.id,
        scene_id="sc00",
        title="Existing bounded plan",
        script_beat="This plan survives all rejected submissions.",
        duration_s=60.0,
    )
    save_shot(old)
    save_project(project.model_copy(update={"shot_ids": [old.id]}))

    class _Service:
        def __init__(self):
            self.calls = []

        async def save_storyboard(
            self,
            project_id,
            drafts,
            expected_script_hash,
            *,
            user_feedback,
            requested_minimum_duration_s,
        ):
            self.calls.append(
                (user_feedback, requested_minimum_duration_s, drafts[0].title)
            )
            raise ValueError(f"semantic issue on submission {len(self.calls)}")

    svc = _Service()
    calls: list[dict] = []
    submission = {
        "expected_script_hash": _script_hash(project.script_text),
        "shots": [
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Rejected candidate",
                "script_beat": "Mara listens.",
                "duration_s": 60.0,
            }
        ],
    }

    async def chat_fn(system: str, user: str, **kwargs):
        calls.append(kwargs)
        if len(calls) <= 3:
            if len(calls) > 1:
                previous = json.loads(kwargs["messages"][-1]["content"])
                assert previous["ok"] is False
            return {
                "content": "",
                "thinking": "",
                "tool_calls": [{"name": "save_storyboard", "arguments": submission}],
            }
        offered = {tool["function"]["name"] for tool in kwargs["tools"]}
        assert "save_storyboard" not in offered
        latest = json.loads(kwargs["messages"][-1]["content"])
        if len(calls) == 4:
            assert latest["ok"] is False
            assert latest["blocked"] is True
            assert latest["save_storyboard_submissions"] == 3
            assert "unresolved issues" in latest["error"].lower()
            assert "new user turn" in latest["error"].lower()
            assert "not persisted" in latest["error"].lower()
            return {
                "content": "",
                "thinking": "",
                "tool_calls": [
                    {"name": "save_storyboard", "arguments": submission}
                ],
            }
        assert latest["ok"] is False
        assert latest["tool_name"] == "save_storyboard"
        assert latest["save_storyboard_submissions"] == 3
        assert "unknown or unavailable tool" in latest["error"].lower()
        return {
            "content": (
                "The unresolved issues remain, and nothing was persisted. "
                "We can discuss another approach and continue in a new user turn."
            ),
            "thinking": "",
            "tool_calls": [],
        }

    message = "Save a storyboard that is at least 60 seconds long."
    result = await handle_chat(
        project_id=project.id,
        message=message,
        svc=svc,
        chat_fn=chat_fn,
    )

    assert len(calls) == 5
    assert len(svc.calls) == 3
    assert svc.calls == [
        (message, 60.0, "Rejected candidate"),
        (message, 60.0, "Rejected candidate"),
        (message, 60.0, "Rejected candidate"),
    ]
    assert result.actions == ["llm"]
    assert "unresolved issues" in result.reply.lower()
    assert "new user turn" in result.reply.lower()
    assert "nothing was persisted" in result.reply.lower()
    assert [shot.model_dump() for shot in list_shots(project.id)] == [old.model_dump()]
    persisted = load_project(project.id)
    assert persisted is not None
    assert persisted.script_text == project.script_text
    assert persisted.shot_ids == [old.id]


@pytest.mark.asyncio
async def test_native_safety_limit_rejects_mixed_prose_and_pending_storyboard_save(
    tmp_projects_dir,
):
    project = create_project(
        "Pending save at safety limit",
        "INT. ROOM - NIGHT\nMara watches the intact recorder.",
    )
    old = Shot(
        id="sht_safety_limit_old",
        project_id=project.id,
        scene_id="sc00",
        title="Existing safety-limit plan",
        script_beat="This approved plan remains stored.",
        duration_s=12.0,
    )
    save_shot(old)
    save_project(
        project.model_copy(update={"script_locked": True, "shot_ids": [old.id]})
    )
    save_agent_context(
        project.id,
        AgentContext(
            project_id=project.id,
            script_hash=_script_hash(project.script_text),
            shot_summaries=[{"id": old.id}],
        ),
    )
    persisted_before = load_project(project.id)
    assert persisted_before is not None

    class _Service:
        def __init__(self):
            self.save_calls = 0

        async def save_storyboard(self, *args, **kwargs):
            self.save_calls += 1
            raise AssertionError("the fifth tool call must not execute")

    svc = _Service()
    calls: list[dict] = []
    submission = {
        "expected_script_hash": _script_hash(project.script_text),
        "shots": [
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Unexecuted candidate",
                "script_beat": "Mara watches the intact recorder.",
                "duration_s": 12.0,
            }
        ],
    }

    async def chat_fn(system: str, user: str, **kwargs):
        calls.append(kwargs)
        if len(calls) <= 4:
            return {
                "content": "",
                "thinking": "",
                "tool_calls": [{"name": "get_status", "arguments": {}}],
            }
        return {
            "content": "The storyboard was saved.",
            "thinking": "",
            "tool_calls": [
                {"name": "save_storyboard", "arguments": submission}
            ],
        }

    result = await handle_chat(
        project_id=project.id,
        message="Continue evaluating the locked storyboard.",
        svc=svc,
        chat_fn=chat_fn,
    )

    assert len(calls) == 5
    assert svc.save_calls == 0
    assert result.actions == ["llm", "status", "status", "status", "status"]
    assert result.reply.startswith(
        "Storyboard was not saved; existing project shots remain unchanged."
    )
    assert "storyboard was saved" not in result.reply.lower()
    assert [shot.model_dump() for shot in list_shots(project.id)] == [old.model_dump()]
    persisted_after = load_project(project.id)
    assert persisted_after is not None
    assert persisted_after.model_dump() == persisted_before.model_dump()


@pytest.mark.asyncio
async def test_native_storyboard_submission_budget_rejects_a_fourth_save_in_one_batch(
    tmp_projects_dir,
):
    project = create_project("Batched budget", "INT. ROOM - NIGHT\nThe recorder waits.")

    class _Service:
        def __init__(self):
            self.calls = 0

        async def save_storyboard(self, *args, **kwargs):
            self.calls += 1
            raise ValueError(f"rejected candidate {self.calls}")

    svc = _Service()
    submission = {
        "expected_script_hash": _script_hash(project.script_text),
        "shots": [
            {
                **CAMERA_DRAFT,
                "scene_id": "sc01",
                "title": "Rejected batch candidate",
                "script_beat": "The recorder waits.",
                "duration_s": 5.0,
            }
        ],
    }
    calls = 0

    async def chat_fn(system: str, user: str, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "content": "",
                "thinking": "",
                "tool_calls": [
                    {"name": "save_storyboard", "arguments": submission}
                    for _ in range(4)
                ],
            }
        payloads = [
            json.loads(item["content"])
            for item in kwargs["messages"]
            if item.get("role") == "tool"
        ]
        assert len(payloads) == 4
        assert payloads[-1]["ok"] is False
        assert payloads[-1]["blocked"] is True
        assert payloads[-1]["save_storyboard_submissions"] == 3
        return {
            "content": "Storyboard save is blocked after three submissions.",
            "thinking": "",
            "tool_calls": [],
        }

    result = await handle_chat(
        project_id=project.id,
        message="Try these storyboard candidates.",
        svc=svc,
        chat_fn=chat_fn,
    )

    assert svc.calls == 3
    assert "blocked" in result.reply.lower()


@pytest.mark.asyncio
async def test_storyboard_submission_budget_resets_on_a_new_user_turn(
    tmp_projects_dir,
):
    project = create_project(
        "Fresh turn budget",
        "INT. ROOM - NIGHT\nThe approved recorder remains intact.",
    )
    old = Shot(
        id="sht_fresh_turn_old",
        project_id=project.id,
        scene_id="sc00",
        title="Existing fresh-turn plan",
        script_beat="Preserve this plan while candidates are discussed.",
        duration_s=12.0,
    )
    save_shot(old)
    save_project(
        project.model_copy(update={"script_locked": True, "shot_ids": [old.id]})
    )
    save_agent_context(
        project.id,
        AgentContext(
            project_id=project.id,
            script_hash=_script_hash(project.script_text),
            shot_summaries=[{"id": old.id}],
        ),
    )

    class _Service:
        def __init__(self):
            self.calls = 0

        async def save_storyboard(self, *args, **kwargs):
            self.calls += 1
            raise ValueError(f"unresolved candidate {self.calls}")

    svc = _Service()
    save_call = {
        "name": "save_storyboard",
        "args": {
            "expected_script_hash": _script_hash(project.script_text),
            "shots": [
                {
                    **CAMERA_DRAFT,
                    "scene_id": "sc01",
                    "title": "Discussed candidate",
                    "script_beat": "The recorder remains intact.",
                    "duration_s": 12.0,
                }
            ],
        },
    }

    async def first_chat_fn(system: str, user: str, **kwargs):
        return json.dumps({"tools": [save_call, save_call, save_call]})

    first = await handle_chat(
        project_id=project.id,
        message="Try these candidate revisions.",
        svc=svc,
        chat_fn=first_chat_fn,
    )

    assert svc.calls == 3
    assert "automatic storyboard revision budget reached" in first.reply.lower()
    assert "new user turn" in first.reply.lower()

    async def second_chat_fn(system: str, user: str, **kwargs):
        return json.dumps({"tools": [save_call]})

    second = await handle_chat(
        project_id=project.id,
        message="Let's discuss another approach and try one revised candidate.",
        history=[{"role": "assistant", "content": first.reply}],
        svc=svc,
        chat_fn=second_chat_fn,
    )

    assert svc.calls == 4
    assert "unresolved candidate 4" in second.reply.lower()
    assert "blocked" not in second.reply.lower()
    assert [shot.model_dump() for shot in list_shots(project.id)] == [old.model_dump()]
    persisted = load_project(project.id)
    assert persisted is not None
    assert persisted.script_text == project.script_text
    assert persisted.script_locked is True
    assert persisted.shot_ids == [old.id]


@pytest.mark.asyncio
async def test_storyboard_discussion_guidance_does_not_force_persistence(
    tmp_projects_dir,
):
    project = create_project("Storyboard discussion", "INT. ROOM - NIGHT")
    old = Shot(
        id="sht_discussion_old",
        project_id=project.id,
        scene_id="sc00",
        title="Existing discussion plan",
        script_beat="Keep the approved plan during discussion.",
        duration_s=8.0,
    )
    save_shot(old)
    save_project(
        project.model_copy(update={"script_locked": True, "shot_ids": [old.id]})
    )
    systems: list[str] = []

    async def chat_fn(system: str, user: str, **kwargs):
        systems.append(system)
        return {
            "content": "Here is a draft approach for us to discuss before saving.",
            "thinking": "",
            "tool_calls": [],
        }

    result = await handle_chat(
        project_id=project.id,
        message="Can we discuss a different edit before saving anything?",
        svc=object(),
        chat_fn=chat_fn,
    )

    guidance = systems[0].lower()
    assert "three automatic" in guidance
    assert "new user turn" in guidance
    assert "discussion" in guidance
    assert "only a validated save_storyboard" in guidance
    assert result.actions == ["llm"]
    assert [shot.model_dump() for shot in list_shots(project.id)] == [old.model_dump()]
    assert load_project(project.id).shot_ids == [old.id]


@pytest.mark.asyncio
async def test_textual_storyboard_batch_uses_the_same_three_submission_budget(
    tmp_projects_dir,
):
    project = create_project(
        "Textual storyboard budget",
        "INT. ROOM - NIGHT\nThe approved recorder remains intact.",
    )
    old = Shot(
        id="sht_textual_budget_old",
        project_id=project.id,
        scene_id="sc00",
        title="Existing textual plan",
        script_beat="Keep the approved recorder intact.",
        duration_s=12.0,
    )
    save_shot(old)
    save_project(
        project.model_copy(
            update={"script_locked": True, "shot_ids": [old.id]}
        )
    )

    class _Service:
        def __init__(self):
            self.calls = 0

        async def save_storyboard(self, *args, **kwargs):
            self.calls += 1
            raise ValueError(f"rejected textual candidate {self.calls}")

    svc = _Service()
    submission = {
        "name": "save_storyboard",
        "args": {
            "expected_script_hash": _script_hash(project.script_text),
            "shots": [
                {
                    **CAMERA_DRAFT,
                    "scene_id": "sc01",
                    "title": "Rejected textual candidate",
                    "script_beat": "The recorder remains intact.",
                    "duration_s": 12.0,
                }
            ],
        },
    }

    async def chat_fn(system: str, user: str, **kwargs):
        return (
            "Trying the submitted candidates.\n"
            + json.dumps({"tools": [submission for _ in range(4)]})
        )

    result = await handle_chat(
        project_id=project.id,
        message="Try these complete storyboard candidates.",
        svc=svc,
        chat_fn=chat_fn,
    )

    assert svc.calls == 3
    assert "blocked" in result.reply.lower()
    assert "three" in result.reply.lower() or "3" in result.reply
    assert result.actions == ["llm"]
    assert [shot.model_dump() for shot in list_shots(project.id)] == [old.model_dump()]
    persisted = load_project(project.id)
    assert persisted is not None
    assert persisted.script_text == project.script_text
    assert persisted.script_locked is True
    assert persisted.shot_ids == [old.id]


@pytest.mark.asyncio
async def test_native_unknown_tool_is_grounded_without_execution_or_budget_use(
    tmp_projects_dir,
):
    project = create_project(
        "Unknown native tool",
        "INT. ROOM - NIGHT\nThe approved recorder remains intact.",
    )
    old = Shot(
        id="sht_unknown_native_old",
        project_id=project.id,
        scene_id="sc00",
        title="Existing native plan",
        script_beat="The recorder remains intact.",
        duration_s=8.0,
    )
    save_shot(old)
    save_project(
        project.model_copy(update={"script_locked": True, "shot_ids": [old.id]})
    )
    save_agent_context(
        project.id,
        AgentContext(
            project_id=project.id,
            script_hash=_script_hash(project.script_text),
            shot_summaries=[{"id": old.id}],
        ),
    )
    calls: list[dict] = []
    progress_events: list[dict] = []
    unknown_result: dict = {}

    async def chat_fn(system: str, user: str, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "content": "",
                "thinking": "",
                "tool_calls": [
                    {
                        "name": "example_function_name",
                        "arguments": {"example_parameter_1": "value_1"},
                    }
                ],
            }
        unknown_result.update(json.loads(kwargs["messages"][-1]["content"]))
        return {
            "content": "The unavailable tool was rejected without changing the project.",
            "thinking": "",
            "tool_calls": [],
        }

    async def on_progress(event: dict):
        progress_events.append(event)

    result = await handle_chat(
        project_id=project.id,
        message="Keep the approved storyboard unchanged while checking available tools.",
        svc=object(),
        chat_fn=chat_fn,
        on_progress=on_progress,
    )

    assert unknown_result["ok"] is False
    assert unknown_result["tool_name"] == "example_function_name"
    assert unknown_result["save_storyboard_submissions"] == 0
    assert "unknown or unavailable tool" in unknown_result["error"].lower()
    assert "example_function_name" not in unknown_result["offered_tools"]
    assert not any(
        "example_function_name" in str(event.get("text") or "")
        for event in progress_events
    )
    assert result.actions == ["llm"]
    assert [shot.model_dump() for shot in list_shots(project.id)] == [old.model_dump()]
    persisted = load_project(project.id)
    assert persisted is not None
    assert persisted.script_text == project.script_text
    assert persisted.script_locked is True
    assert persisted.shot_ids == [old.id]


@pytest.mark.asyncio
async def test_textual_unoffered_tool_replay_is_filtered_before_execution(
    tmp_projects_dir,
):
    project = create_project("Textual locked replay", "INT. ROOM - NIGHT")
    old = Shot(
        id="sht_textual_replay_old",
        project_id=project.id,
        scene_id="sc00",
        title="Existing replay plan",
        script_beat="Preserve this approved state.",
        duration_s=8.0,
    )
    save_shot(old)
    save_project(
        project.model_copy(update={"script_locked": True, "shot_ids": [old.id]})
    )
    save_agent_context(
        project.id,
        AgentContext(
            project_id=project.id,
            script_hash=_script_hash(project.script_text),
            shot_summaries=[{"id": old.id}],
        ),
    )
    progress_events: list[dict] = []

    async def chat_fn(system: str, user: str, **kwargs):
        return json.dumps(
            {
                "tools": [
                    {
                        "name": "set_script",
                        "args": {"script": "EXT. STREET - DAY"},
                    },
                    {
                        "name": "example_function_name",
                        "args": {"example_parameter_1": "value_1"},
                    },
                ]
            }
        )

    async def on_progress(event: dict):
        progress_events.append(event)

    result = await handle_chat(
        project_id=project.id,
        message="Revise the storyboard without changing the approved screenplay.",
        svc=object(),
        chat_fn=chat_fn,
        on_progress=on_progress,
    )

    assert "unknown or unavailable tool: set_script" in result.reply.lower()
    assert "unknown or unavailable tool: example_function_name" in result.reply.lower()
    assert not any(
        tool_name in str(event.get("text") or "")
        for tool_name in ("set_script", "example_function_name")
        for event in progress_events
    )
    assert result.actions == ["llm"]
    assert [shot.model_dump() for shot in list_shots(project.id)] == [old.model_dump()]
    persisted = load_project(project.id)
    assert persisted is not None
    assert persisted.script_text == "INT. ROOM - NIGHT"
    assert persisted.script_locked is True
    assert persisted.shot_ids == [old.id]


@pytest.mark.asyncio
async def test_locked_project_omits_set_script_on_every_native_turn_and_rejects_replay(
    tmp_projects_dir,
):
    project = create_project("Approved", "INT. ROOM - NIGHT")
    old = Shot(
        id="sht_locked_replay_old",
        project_id=project.id,
        scene_id="sc00",
        title="Approved plan",
        script_beat="Preserve the locked plan.",
        duration_s=8.0,
    )
    save_shot(old)
    save_project(
        project.model_copy(update={"script_locked": True, "shot_ids": [old.id]})
    )
    save_agent_context(
        project.id,
        AgentContext(
            project_id=project.id,
            script_hash=_script_hash(project.script_text),
            shot_summaries=[{"id": old.id}],
        ),
    )
    calls: list[dict] = []
    replay_result: dict = {}

    async def chat_fn(system: str, user: str, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "content": "",
                "thinking": "Checking the approved project first.",
                "tool_calls": [{"name": "get_status", "arguments": {}}],
            }
        if len(calls) == 2:
            return {
                "content": "Trying a replayed tool call.",
                "thinking": "",
                "tool_calls": [
                    {
                        "name": "set_script",
                        "arguments": {"script": "EXT. STREET - DAY"},
                    }
                ],
            }
        replay_result.update(json.loads(kwargs["messages"][-1]["content"]))
        return {
            "content": "The unavailable screenplay tool was rejected.",
            "thinking": "",
            "tool_calls": [],
        }

    result = await handle_chat(
        project_id=project.id,
        message="Revise the storyboard without changing the approved screenplay.",
        svc=object(),
        chat_fn=chat_fn,
    )

    assert len(calls) == 3
    assert all(
        "set_script"
        not in {tool["function"]["name"] for tool in call["tools"]}
        for call in calls
    )
    assert all(call["guides"] == ("script-planning",) for call in calls)
    assert replay_result["ok"] is False
    assert replay_result["tool_name"] == "set_script"
    assert replay_result["save_storyboard_submissions"] == 0
    assert "unknown or unavailable tool" in replay_result["error"].lower()
    persisted = load_project(project.id)
    assert persisted is not None
    assert persisted.script_text == "INT. ROOM - NIGHT"
    assert persisted.script_locked is True
    assert "set_script" not in result.actions
    assert "unavailable screenplay tool was rejected" in result.reply.lower()


@pytest.mark.asyncio
async def test_native_set_script_executor_rejects_locked_project(tmp_projects_dir):
    project = create_project("Approved", "INT. ROOM - NIGHT")
    save_project(project.model_copy(update={"script_locked": True}))
    actions: list[str] = []

    notes, touched = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "set_script",
                "args": {"script": "EXT. STREET - DAY"},
            }
        ],
        svc=object(),
        actions=actions,
    )

    persisted = load_project(project.id)
    assert persisted is not None
    assert persisted.script_text == "INT. ROOM - NIGHT"
    assert persisted.script_locked is True
    assert actions == []
    assert touched == set()
    assert any("locked" in note.lower() for note in notes)


def test_project_context_serializes_complete_layout_reference_state(
    tmp_projects_dir,
    monkeypatch,
):
    project = create_project("Layout context", "A reveal changes the blocking.")
    shot = Shot(
        id="sht_layout_context",
        project_id=project.id,
        scene_id="sc01",
        title="Reveal",
        script_beat="The door opens and reveals Kai.",
        duration_s=6.0,
        status=ShotStatus.needs_review,
        refs=[
            ShotRef(
                role=RefRole.layout_ref_frame,
                asset_id="lay_reveal",
                picture_index=1,
                file_key="layout",
            )
        ],
        layout_asset_id="lay_reveal",
        layout_review_status="rejected",
        layout_refs=[
            LayoutReference(
                id="lref_reveal",
                asset_id="lay_reveal",
                job_id="job_reveal",
                job_status=JobStatus.failed,
                job_error="library write failed",
                purpose="post-door reveal",
                state_description="Kai is visible beyond the door.",
                time_hint="after the door opens",
                source_refs=[
                    LayoutSourceRef(
                        role=RefRole.scene,
                        asset_id="scn_room",
                        file_key="wide",
                        notes="door geometry",
                    )
                ],
                review_status="reject",
                review_feedback="wrong screen direction",
                selected_for_h3=False,
            )
        ],
    )
    monkeypatch.setattr("app.agents.director.service._inventory", lambda _pid: {})
    monkeypatch.setattr(
        "app.agents.director.context_io.load_agent_context",
        lambda _pid: None,
    )

    context = json.loads(_project_context_blob(project, [shot]))

    assert context["shots"][0]["layout_refs"] == [
            {
                "id": "lref_reveal",
                "provider": "comfy",
                "purpose": "post-door reveal",
            "state_description": "Kai is visible beyond the door.",
            "time_hint": "after the door opens",
            "source_refs": [
                {
                    "role": "scene",
                    "asset_id": "scn_room",
                    "file_key": "wide",
                    "notes": "door geometry",
                }
            ],
            "job_status": "failed",
            "job_error": "library write failed",
            "asset_id": "lay_reveal",
            "review_status": "reject",
            "review_feedback": "wrong screen direction",
            "feedback_source": "",
            "feedback_quote": "",
            "revision_of": None,
            "superseded_by": None,
            "selected_for_h3": False,
            "picture_index": 1,
            "origin": None,
        }
    ]


@pytest.mark.asyncio
async def test_queue_ref_frame_tool_queues_explicit_layout_brief_and_reports_identity(
    tmp_projects_dir,
):
    project = create_project("Purpose-built Layout", "A guard reaches the sealed door.")
    shot = Shot(
        id="sht_native_layout",
        project_id=project.id,
        scene_id="sc01",
        title="Door approach",
        script_beat="The guard reaches the sealed door.",
        duration_s=5.0,
        status=ShotStatus.ref_frame_pending,
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    class _Service:
        brief = None

        async def queue_reference_frame(self, shot_id: str, *, brief, force=False):
            assert shot_id == shot.id
            self.brief = brief
            layout = LayoutReference(
                id="lref_native_secondary",
                job_id="job_native_secondary",
                purpose=brief.purpose,
                state_description=brief.state_description,
                time_hint=brief.time_hint,
                source_refs=list(brief.source_refs),
            )
            updated = shot.model_copy(update={"layout_refs": [layout]})
            save_shot(updated)
            return updated

    svc = _Service()
    notes, touched = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "queue_ref_frame",
                "args": {
                    "shot_id": shot.id,
                    "purpose": "door-state continuity",
                    "state_description": "The seal is intact before the guard touches it.",
                    "time_hint": "00:04",
                    "source_refs": [
                        {
                            "role": "scene",
                            "asset_id": "scn_door",
                            "file_key": "wide",
                            "notes": "lock geometry",
                        },
                        {"role": "actor", "asset_id": "act_guard"},
                    ],
                },
            }
        ],
        svc=svc,
        actions=[],
    )

    assert svc.brief is not None
    assert svc.brief.purpose == "door-state continuity"
    assert svc.brief.state_description.startswith("The seal is intact")
    assert svc.brief.time_hint == "00:04"
    assert svc.brief.source_refs == [
        LayoutSourceRef(
            role=RefRole.scene,
            asset_id="scn_door",
            file_key="wide",
            notes="lock geometry",
        ),
        LayoutSourceRef(role=RefRole.actor, asset_id="act_guard"),
    ]
    assert touched == {shot.id}
    result = "\n".join(notes)
    assert "lref_native_secondary" in result
    assert "door-state continuity" in result
    assert "job_native_secondary" in result
    assert "2 source" in result


@pytest.mark.asyncio
async def test_agent_can_append_a_two_person_layout_to_the_same_shot(
    tmp_projects_dir,
):
    """An explicit 'add another' request stays in one Shot and preserves its first state."""
    project = create_project(
        "One-to-two person beat",
        "Mia waits alone. The Agent enters and joins her.",
    )
    shot = Shot(
        id="sht_one_to_two",
        project_id=project.id,
        scene_id="sc01",
        title="Mia is joined",
        script_beat="Mia starts alone; the Agent enters and sits opposite her.",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        layout_asset_id="lay_mia_alone",
        layout_refs=[
            LayoutReference(
                id="lref_mia_alone",
                asset_id="lay_mia_alone",
                purpose="Mia alone before the entrance",
                selected_for_h3=True,
            )
        ],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    class _Service:
        brief = None

        async def queue_reference_frame(self, shot_id: str, *, brief, force=False):
            assert shot_id == shot.id
            self.brief = brief
            added = LayoutReference(
                id="lref_two_people",
                job_id="job_two_people",
                purpose=brief.purpose,
                state_description=brief.state_description,
                time_hint=brief.time_hint,
                source_refs=list(brief.source_refs),
                activation_mode=brief.activation_mode,
            )
            updated = shot.model_copy(
                update={"layout_refs": [*shot.layout_refs, added]}
            )
            save_shot(updated)
            return updated

    svc = _Service()
    _notes, touched = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "queue_ref_frame",
                "args": {
                    "shot_id": shot.id,
                    "purpose": "two-person blocking after the entrance",
                    "state_description": (
                        "Mia and the Agent are both seated across the same table."
                    ),
                    "time_hint": "after the Agent enters in the latter half",
                    "source_refs": [
                        {"role": "actor", "asset_id": "act_mia"},
                        {"role": "actor", "asset_id": "act_agent"},
                        {"role": "scene", "asset_id": "scn_interview"},
                    ],
                },
            }
        ],
        svc=svc,
        actions=[],
        user_feedback=(
            "Keep this in the same shot and add another Layout for the later "
            "two-person composition."
        ),
    )

    assert touched == {shot.id}
    assert svc.brief is not None
    assert svc.brief.activation_mode == "append"
    assert svc.brief.purpose == "two-person blocking after the entrance"


@pytest.mark.asyncio
async def test_revise_ref_frame_records_chat_feedback_and_links_new_layout(
    tmp_projects_dir,
):
    project = create_project("Dialogue Layout Revision", "Lu faces door seven.")
    original = LayoutReference(
        id="lref_original",
        asset_id="lay_original",
        job_id="job_original",
        purpose="door seven composition",
        state_description="Lu stands beside the recorder.",
        time_hint="before playback",
        source_refs=[
            LayoutSourceRef(role=RefRole.scene, asset_id="scn_archive")
        ],
        review_status="usable",
        selected_for_h3=True,
    )
    shot = Shot(
        id="sht_dialogue_revision",
        project_id=project.id,
        scene_id="sc01",
        title="Door seven",
        script_beat="Lu faces door seven.",
        duration_s=6.0,
        status=ShotStatus.needs_review,
        layout_asset_id="lay_original",
        layout_review_status="approved",
        layout_refs=[original],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    class _Service:
        async def queue_reference_frame(self, shot_id: str, *, brief, force=False):
            current = load_shot(project.id, shot_id)
            assert current is not None
            revised = LayoutReference(
                id="lref_revised",
                job_id="job_revised",
                purpose=brief.purpose,
                state_description=brief.state_description,
                time_hint=brief.time_hint,
                source_refs=list(brief.source_refs),
            )
            updated = current.model_copy(
                update={"layout_refs": [*current.layout_refs, revised]}
            )
            save_shot(updated)
            return updated

    notes, touched = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "revise_ref_frame",
                "args": {
                    "shot_id": shot.id,
                    "layout_ref_id": original.id,
                    "feedback": "人物站位过近，7号门识别不足",
                },
            }
        ],
        svc=_Service(),
        actions=[],
        user_feedback="人物太靠前，7号门看不清，重新生成。",
    )

    saved = load_shot(project.id, shot.id)
    assert saved is not None
    old, new = saved.layout_refs
    assert old.review_status == LayoutReviewStatus.reject
    assert old.review_feedback == "人物站位过近，7号门识别不足"
    assert old.feedback_source == "director_chat"
    assert old.feedback_quote == "人物太靠前，7号门看不清，重新生成。"
    assert old.superseded_by == "lref_revised"
    assert new.revision_of == "lref_original"
    assert touched == {shot.id}
    assert any("Recorded dialogue feedback" in note for note in notes)


@pytest.mark.asyncio
async def test_accept_ref_frame_records_chat_decision_and_selects_layout(
    tmp_projects_dir,
):
    project = create_project("Dialogue Layout Acceptance", "Lu faces door seven.")
    layout = LayoutReference(
        id="lref_accept",
        asset_id="lay_accept",
        job_id="job_accept",
        purpose="door seven composition",
        review_status="pending_review",
        selected_for_h3=False,
    )
    shot = Shot(
        id="sht_dialogue_accept",
        project_id=project.id,
        scene_id="sc01",
        title="Door seven",
        script_beat="Lu faces door seven.",
        duration_s=6.0,
        status=ShotStatus.needs_review,
        layout_asset_id="lay_accept",
        layout_review_status="pending_review",
        layout_refs=[layout],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    notes, touched = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "accept_ref_frame",
                "args": {
                    "shot_id": shot.id,
                    "layout_ref_id": layout.id,
                    "feedback": "构图和人物位置符合要求",
                },
            }
        ],
        svc=object(),
        actions=[],
        user_feedback="这张可以，就用它。",
    )

    saved = load_shot(project.id, shot.id)
    assert saved is not None
    accepted = saved.layout_refs[0]
    assert accepted.review_status == LayoutReviewStatus.usable
    assert accepted.selected_for_h3 is True
    assert accepted.review_feedback == "构图和人物位置符合要求"
    assert accepted.feedback_source == "director_chat"
    assert accepted.feedback_quote == "这张可以，就用它。"
    assert any(
        ref.role == RefRole.layout_ref_frame and ref.asset_id == "lay_accept"
        for ref in saved.refs
    )
    assert touched == {shot.id}
    assert any("Selected Layout lref_accept for H3" in note for note in notes)


@pytest.mark.asyncio
async def test_accept_ref_frame_returns_authoritative_picture_order_without_llm_paraphrase(
    tmp_projects_dir,
):
    project = create_project("Accurate acceptance reply", "Mia appears on the TV.")
    layout = LayoutReference(
        id="lref_tail",
        asset_id="lay_tail",
        purpose="continuity from shot one",
        review_status="pending_review",
        selected_for_h3=False,
    )
    shot = Shot(
        id="sht_shot2",
        project_id=project.id,
        scene_id="sc01",
        title="Mia's face on the screen",
        script_beat="Mia appears on the TV.",
        duration_s=8.0,
        status=ShotStatus.needs_review,
        layout_refs=[layout],
        refs=[
            ShotRef(
                role=RefRole.actor,
                asset_id="act_mia",
                picture_index=1,
                file_key="bust_threeview",
            ),
            ShotRef(
                role=RefRole.prop,
                asset_id="prp_tv",
                picture_index=2,
                file_key="master",
            ),
        ],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    save_agent_context(
        project.id,
        AgentContext(
            project_id=project.id,
            script_hash=_script_hash(project.script_text),
            last_phase="awaiting_layout_review",
            shot_summaries=[{"id": shot.id}],
        ),
    )

    class _Service:
        async def write_prompts_after_layout(self, shot_id: str) -> Shot:
            current = load_shot(project.id, shot_id)
            assert current is not None
            save_shot(current)
            return current

    calls = 0

    async def chat_fn(_system: str, _user: str, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "content": "",
                "thinking": "Accept the requested frame.",
                "tool_calls": [
                    {
                        "name": "accept_ref_frame",
                        "arguments": {
                            "shot_id": shot.id,
                            "layout_ref_id": layout.id,
                        },
                    }
                ],
            }
        return {
            "content": (
                "Picture 1 is the tail frame, Picture 2 is Mia, "
                "and Picture 3 is the TV."
            ),
            "thinking": "",
            "tool_calls": [],
        }

    result = await handle_chat(
        project_id=project.id,
        message="Add the frame as a ref for shot 2.",
        svc=_Service(),
        chat_fn=chat_fn,
    )

    assert calls == 1
    assert "Picture 1 — Actor: act_mia (bust_threeview)" in result.reply
    assert "Picture 2 — Prop: prp_tv (master)" in result.reply
    assert "Picture 3 — Accepted Layout: lay_tail (layout)" in result.reply
    assert "Picture 1 is the tail frame" not in result.reply


@pytest.mark.asyncio
async def test_queue_ref_frame_tool_requires_purpose_for_an_additional_layout(
    tmp_projects_dir,
):
    project = create_project("Additional Layout", "A guard crosses the room.")
    shot = Shot(
        id="sht_native_second_layout",
        project_id=project.id,
        scene_id="sc01",
        title="Crossing",
        script_beat="The guard crosses the room.",
        duration_s=5.0,
        status=ShotStatus.ref_frame_pending,
        layout_refs=[
            LayoutReference(
                id="lref_primary",
                job_id="job_primary",
                purpose="primary composition",
            )
        ],
    )
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))

    class _Service:
        async def queue_reference_frame(self, *args, **kwargs):
            raise AssertionError("an unlabeled additional Layout must not be queued")

    notes, touched = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "queue_ref_frame",
                "args": {
                    "shot_id": shot.id,
                    "state_description": "The guard has reached the far wall.",
                },
            }
        ],
        svc=_Service(),
        actions=[],
    )

    assert touched == set()
    assert notes == [
        "queue_ref_frame failed: an additional Layout requires a distinct purpose"
    ]


@pytest.mark.asyncio
async def test_queue_ref_frame_tool_applies_explicit_brief_to_all_selected_shots(
    tmp_projects_dir,
):
    project = create_project("Bulk explicit Layout", "Two guards hold two doors.")
    shots = [
        Shot(
            id=f"sht_bulk_layout_{index}",
            project_id=project.id,
            scene_id=f"sc0{index}",
            title=f"Door {index}",
            script_beat=f"Guard {index} holds position.",
            duration_s=5.0,
            status=ShotStatus.ref_frame_pending,
        )
        for index in (1, 2)
    ]
    for shot in shots:
        save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id for shot in shots]}))

    class _Service:
        calls = []

        async def queue_reference_frame(self, shot_id: str, *, brief, force=False):
            self.calls.append((shot_id, brief, force))
            shot = load_shot(project.id, shot_id)
            assert shot is not None
            layout = LayoutReference(
                id=f"lref_{shot_id}",
                job_id=f"job_{shot_id}",
                purpose=brief.purpose,
                source_refs=list(brief.source_refs),
            )
            updated = shot.model_copy(update={"layout_refs": [layout]})
            save_shot(updated)
            return updated

    svc = _Service()
    notes, touched = await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "queue_ref_frame",
                "args": {
                    "all": True,
                    "purpose": "matching doorway geography",
                    "source_refs": [
                        {"role": "scene", "asset_id": "scn_shared_door"}
                    ],
                },
            }
        ],
        svc=svc,
        actions=[],
    )

    assert [call[0] for call in svc.calls] == [shot.id for shot in shots]
    assert all(call[1].purpose == "matching doorway geography" for call in svc.calls)
    assert touched == {shot.id for shot in shots}
    assert len(notes) == 2
    assert all("1 source" in note for note in notes)


@pytest.mark.asyncio
async def test_native_write_prompt_tool_is_executed_and_result_returns_to_model(
    tmp_projects_dir,
):
    project = create_project("Native tools", "An actor enters the casting hallway.")
    shot = Shot(
        id="sht_native_tool",
        project_id=project.id,
        scene_id="sc01",
        title="Corridor walk-in",
        script_beat="The actor enters and reads the sign.",
        duration_s=6.0,
        status=ShotStatus.needs_review,
        layout_asset_id="lay_native",
        layout_review_status="approved",
    )
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)
    save_agent_context(
        project.id,
        AgentContext(
            project_id=project.id,
            script_hash=_script_hash(project.script_text),
            last_phase="awaiting_prompt",
            shot_summaries=[{"id": shot.id}],
        ),
    )

    class _Service:
        async def write_prompts_after_layout(self, shot_id: str) -> Shot:
            current = load_shot(project.id, shot_id)
            assert current is not None
            updated = current.model_copy(
                update={
                    "prompt_sections": PromptSections(
                        subject_definitions="S1 is the actor.",
                        summary="The actor enters the hallway.",
                        retention_analysis="Keep the approved layout.",
                        detailed_description="A medium-wide entrance and sign read.",
                        overall_soundscape="Quiet hallway room tone.",
                        non_diegetic_music="None.",
                    )
                }
            )
            save_shot(updated)
            return updated

    calls: list[dict] = []

    async def chat_fn(system: str, user: str, **kwargs):
        calls.append({"system": system, "user": user, **kwargs})
        if len(calls) == 1:
            return {
                "content": "",
                "thinking": "The approved shot needs its six-section prompt.",
                "tool_calls": [
                    {
                        "name": "write_prompt",
                        "arguments": {"shot_id": shot.id},
                    }
                ],
            }
        return {
            "content": "The six-section prompt is now written.",
            "thinking": "",
            "tool_calls": [],
        }

    result = await handle_chat(
        project_id=project.id,
        message="Please write the production prompt for the approved first shot.",
        svc=_Service(),
        chat_fn=chat_fn,
    )

    assert result.reply == "The six-section prompt is now written."
    assert "Reply in English" in calls[0]["system"]
    assert not any("\u3400" <= char <= "\u9fff" for char in calls[0]["system"])
    assert f"write_prompt:{shot.id}" in result.actions
    assert load_shot(project.id, shot.id).prompt_sections.summary
    assert calls[0]["tools"]
    assert any(t["function"]["name"] == "write_prompt" for t in calls[0]["tools"])
    offered_tools = {t["function"]["name"] for t in calls[0]["tools"]}
    assert "queue_ref_frame" in offered_tools
    assert "approve_layout" not in offered_tools
    assert "reject_layout" not in offered_tools
    tool_messages = calls[1]["messages"]
    assert any(
        message.get("role") == "tool"
        and message.get("tool_name") == "write_prompt"
        and "prompt" in message.get("content", "").lower()
        for message in tool_messages
    )


@pytest.mark.asyncio
async def test_director_fast_path_status_is_visible_in_english(tmp_projects_dir):
    project = create_project("English status", "An actor enters the hallway.")

    result = await handle_chat(
        project_id=project.id,
        message="status",
        svc=object(),
    )

    visible = "\n".join([result.reply, *result.steps])
    assert "English status" in visible
    assert "shot" in visible.lower()
    assert not any("\u3400" <= char <= "\u9fff" for char in visible)


@pytest.mark.asyncio
async def test_llm_chat_does_not_persist_predicted_runtime_as_business_steps(tmp_projects_dir):
    project = create_project("Runtime separation", "An actor enters the hallway.")
    events: list[dict] = []

    async def chat_fn(*_args, **_kwargs):
        return "I would begin by establishing the location."

    async def on_progress(event: dict):
        events.append(event)

    result = await handle_chat(
        project_id=project.id,
        message="How should we approach this scene?",
        svc=object(),
        chat_fn=chat_fn,
        on_progress=on_progress,
    )

    visible = "\n".join([*result.steps, *(str(event.get("text") or "") for event in events)])
    assert "intent: llm" not in visible
    assert "queue GPU" not in visible
    assert "unload Comfy" not in visible
    assert result.steps == []


@pytest.mark.asyncio
async def test_ollama_chat_response_passes_native_tools_and_json_schema(monkeypatch):
    from app.core.vram import ollama_client as module

    requests: list[dict] = []

    class _Message:
        content = "done"
        thinking = "checked"
        tool_calls = [
            type(
                "ToolCall",
                (),
                {
                    "function": type(
                        "Function",
                        (),
                        {"name": "get_status", "arguments": {}},
                    )()
                },
            )()
        ]

    class _Response:
        message = _Message()

    class _AsyncClient:
        def __init__(self, **kwargs):
            requests.append({"client": kwargs})

        async def chat(self, **kwargs):
            requests.append(kwargs)
            return _Response()

    monkeypatch.setattr(module, "AsyncClient", _AsyncClient)
    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    }
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_status",
                "description": "Read project status",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    result = await module.OllamaClient().chat_response(
        "qwen3.6:27b",
        messages=[{"role": "user", "content": "status"}],
        tools=tools,
        format=schema,
    )

    assert result == {
        "content": "done",
        "thinking": "checked",
        "tool_calls": [{"name": "get_status", "arguments": {}}],
    }
    assert requests[1]["tools"] == tools
    assert requests[1]["format"] == schema
    assert requests[1]["options"] == {
        "num_gpu": 999,
        "num_ctx": 32_768,
        "num_predict": 4_096,
    }
