import pytest

from app.agents.director.intent import actor_design_intent, layout_activation_mode

from app.agents.director.chat import (
    _director_tool_schemas,
    _execute_intent,
    _explicit_gpt_image_intent,
    detect_intent,
    handle_chat,
    _looks_like_script,
    _parse_tools_from_llm,
    _validate_gpt_generation_prompt,
)
from app.config import settings
from app.core.projects.models import PromptSections, Shot, ShotStatus
from app.core.projects.store import (
    create_project,
    load_project,
    save_project,
    save_shot,
)


def _shots():
    return [
        Shot(
            id="sht_aaa",
            project_id="p",
            scene_id="sc01",
            title="Maya Battles Wind",
            script_beat="beat",
            duration_s=6,
            status=ShotStatus.needs_review,
            prompt_sections=PromptSections(),
        ),
        Shot(
            id="sht_bbb",
            project_id="p",
            scene_id="sc01",
            title="Jon Approaches",
            script_beat="beat",
            duration_s=6,
            status=ShotStatus.ref_frame_pending,
            prompt_sections=PromptSections(),
        ),
    ]


def test_detect_plan_short_command():
    intent, _ = detect_intent("拆镜", _shots())
    assert intent == "plan"


def test_natural_language_goes_to_llm():
    intent, _ = detect_intent("帮我按剧本把镜头拆一下吧，节奏紧一点", _shots())
    assert intent == "llm"


def test_natural_ref_frame_goes_to_llm_not_hard_intent():
    """Natural phrases are agent territory — model emits queue_ref_frame tools."""
    intent, _ = detect_intent("第2镜首帧", _shots())
    assert intent == "llm"


@pytest.mark.parametrize(
    "message",
    [
        "Add another Layout for the later two-person composition.",
        "Keep the first one and add an additional reference frame.",
        "再加一张两个人的 layout，前一张保留。",
        "给这个镜头补充一个后半段双人构图。",
    ],
)
def test_explicit_additional_layout_intent_uses_append_mode(message):
    assert layout_activation_mode(message) == "append"


@pytest.mark.parametrize(
    "message",
    [
        "Regenerate this Layout.",
        "Replace the current reference frame.",
        "这张不行，重新生成。",
    ],
)
def test_regeneration_intent_keeps_replace_mode(message):
    assert layout_activation_mode(message) == "replace"


@pytest.mark.parametrize(
    "message",
    [
        "Use GPT to generate the Layout for shot 3",
        "请用 ChatGPT image 生成第三镜参考帧",
        "这一张明确走 image2/GPT 生图",
    ],
)
def test_explicit_gpt_image_intent(message):
    assert _explicit_gpt_image_intent(message)


@pytest.mark.parametrize(
    "message",
    [
        "generate a reference frame",
        "GPT可以支持更多参考图吗？",
        "ChatGPT Bridge 当前连着吗？",
    ],
)
def test_non_authorizing_gpt_messages_do_not_grant_image_tool(message):
    assert not _explicit_gpt_image_intent(message)


def test_gpt_prompt_must_name_every_attached_image():
    with pytest.raises(ValueError, match="Image4"):
        _validate_gpt_generation_prompt(
            "Image1 controls set. Image2 and Image3 control actors. Return one image.",
            4,
        )


def test_gpt_prompt_allows_text_only_generation_without_image_labels():
    _validate_gpt_generation_prompt(
        "Create one cinematic establishing plate of an empty review room.",
        0,
    )


def test_gpt_prompt_rejects_unknown_image_before_collage_language():
    with pytest.raises(ValueError, match="unattached Image5"):
        _validate_gpt_generation_prompt(
            "Image1 controls set. Image2 controls Lu. Image3 controls Chen. "
            "Image4 controls the prop. Image5 is extra. Return a contact sheet.",
            4,
        )


def test_gpt_prompt_rejects_collage_even_when_image_bindings_are_complete():
    with pytest.raises(ValueError, match="contact sheet"):
        _validate_gpt_generation_prompt(
            "Image1 controls set. Image2 controls Lu. Return a contact sheet.",
            2,
        )


def test_gpt_prompt_allows_explicit_negative_collage_constraints():
    _validate_gpt_generation_prompt(
        "Image1 controls set. Image2 controls Lu. Return one single image only. "
        "No collage, no split screen, no contact sheet, no turnaround.",
        2,
    )


def test_gpt_prompt_allows_compact_negative_collage_list():
    _validate_gpt_generation_prompt(
        "Image1 controls set. Image2 controls Lu. Return one single image only. "
        "No collage, split screen, contact sheet, or turnaround.",
        2,
    )


def test_gpt_prompt_allows_turnaround_as_reference_description():
    _validate_gpt_generation_prompt(
        "Image1 controls set. Image2 controls identity from the actor turnaround "
        "reference. Return one final cinematic image.",
        2,
    )


def test_configured_gpt_and_local_reference_tools_are_both_offered(
    tmp_projects_dir, monkeypatch
):
    project = create_project("Unified reference tools", "A door opens.")
    monkeypatch.setattr(settings, "gpt_bridge_base_url", "http://127.0.0.1:8080")
    monkeypatch.setattr(settings, "gpt_bridge_env_file", settings.project_root / "bridge.env")

    generic = _director_tool_schemas(
        project,
        current_message="生成第三镜参考帧",
    )
    explicit = _director_tool_schemas(
        project,
        current_message="Use GPT to generate the third shot Layout",
    )

    generic_names = {item["function"]["name"] for item in generic}
    explicit_names = {item["function"]["name"] for item in explicit}

    assert {"queue_ref_frame", "queue_gpt_ref_frame"} <= generic_names
    assert {"queue_ref_frame", "queue_gpt_ref_frame"} <= explicit_names


def test_actor_design_intent_offers_only_the_actor_generation_tool(
    tmp_projects_dir, monkeypatch
):
    project = create_project("Mobile Actor", "A detective enters.")
    monkeypatch.setattr(settings, "gpt_bridge_base_url", "http://127.0.0.1:8080")
    monkeypatch.setattr(settings, "gpt_bridge_env_file", settings.project_root / "bridge.env")

    tools = _director_tool_schemas(
        project,
        current_message="用 GPT 生成人物设定，四十岁的女侦探，黑色风衣",
    )

    assert [item["function"]["name"] for item in tools] == [
        "queue_actor_design"
    ]


def test_follow_existing_character_setting_keeps_gpt_layout_tools_available(
    tmp_projects_dir, monkeypatch
):
    project = create_project("Existing Mia", "Mia stands in the room.")
    monkeypatch.setattr(settings, "gpt_bridge_base_url", "http://127.0.0.1:8080")
    monkeypatch.setattr(settings, "gpt_bridge_env_file", settings.project_root / "bridge.env")

    message = "让 gpt 按照人物设定生成"
    tools = _director_tool_schemas(project, current_message=message)
    names = {item["function"]["name"] for item in tools}

    assert actor_design_intent(message) is False
    assert "queue_gpt_ref_frame" in names
    assert names != {"queue_actor_design"}


@pytest.mark.asyncio
async def test_explicit_gpt_request_without_config_stops_before_inference(
    tmp_projects_dir, monkeypatch
):
    project = create_project("Missing GPT config", "A door opens.")
    monkeypatch.setattr(settings, "gpt_bridge_base_url", None)
    monkeypatch.setattr(settings, "gpt_bridge_env_file", None)
    inference_called = False

    async def chat_fn(*args, **kwargs):
        nonlocal inference_called
        inference_called = True
        raise AssertionError("inference must not run without Bridge configuration")

    result = await handle_chat(
        project_id=project.id,
        message="请用 GPT 生图第三镜",
        svc=object(),
        chat_fn=chat_fn,
    )

    assert "DS_GPT_BRIDGE_BASE_URL" in result.reply
    assert "Comfy" in result.reply
    assert result.actions == []
    assert inference_called is False
    intent, _ = detect_intent("能先给镜头出构图参考帧吗", _shots())
    assert intent == "llm"
    intent, _ = detect_intent("帮我出第2镜的构图参考帧", _shots())
    assert intent == "llm"


def test_exact_chip_still_fast_path():
    intent, _ = detect_intent("生成全部参考帧", _shots())
    assert intent == "ref_frame_all"
    intent, _ = detect_intent("生成全部", _shots())
    assert intent == "ref_frame_all"
    intent, _ = detect_intent("全部生成", _shots())
    assert intent == "ref_frame_all"
    intent, _ = detect_intent("拆镜", _shots())
    assert intent == "plan"


def test_exact_write_h3_prompt_button_resolves_the_shot_without_llm_routing():
    intent, params = detect_intent("Write the H3 prompt for shot 1", _shots())

    assert intent == "write_prompt"
    assert params == {"shot_id": "sht_aaa"}


def test_exact_write_h3_prompt_routes_through_agent_when_material_review_is_pending():
    shots = _shots()
    shots[0] = shots[0].model_copy(
        update={"meta": {"material_review_pending": True}}
    )

    intent, params = detect_intent("Write the H3 prompt for shot 1", shots)

    assert intent == "llm"
    assert params == {}


@pytest.mark.asyncio
async def test_shot_specific_chat_returns_only_the_target_shot_layout(
    tmp_projects_dir,
):
    project = create_project("Scoped chat images", "The Agent waits. Mia enters.")
    shot_one = Shot(
        id="sht_scope_one",
        project_id=project.id,
        scene_id="sc01",
        title="The Wait",
        script_beat="The Agent waits alone.",
        duration_s=6,
        status=ShotStatus.succeeded,
        layout_asset_id="lay_scope_one",
        meta={
            "material_review_pending": True,
            "material_changes": {
                "removed": [
                    {
                        "role": "actor",
                        "asset_id": "act_agent",
                        "file_key": "master",
                        "picture_index": 2,
                    }
                ],
                "added": [],
                "reordered": [],
            },
        },
    )
    shot_two = Shot(
        id="sht_scope_two",
        project_id=project.id,
        scene_id="sc02",
        title="Mia Enters",
        script_beat="Mia enters.",
        duration_s=6,
        status=ShotStatus.succeeded,
        layout_asset_id="lay_scope_two",
    )
    save_shot(shot_one)
    save_shot(shot_two)
    save_project(project.model_copy(update={"shot_ids": [shot_one.id, shot_two.id]}))
    prompts: list[str] = []

    async def chat_fn(system: str, user: str, **kwargs):
        prompts.append(user)
        return "The Agent reference is missing. Should I add its master reference?"

    result = await handle_chat(
        project_id=project.id,
        message="检查 Shot 1 当前 refs。",
        svc=object(),
        chat_fn=chat_fn,
    )

    assert [image.shot_id for image in result.images] == [shot_one.id]
    assert '"material_review_pending":true' in prompts[0]
    assert '"asset_id":"act_agent"' in prompts[0]


@pytest.mark.parametrize(
    "message",
    [
        "Generate layout for shot 2",
        "Generate the reference frame for shot 2",
        "Regenerate layout for shot 2",
    ],
)
def test_explicit_single_shot_layout_command_bypasses_llm(message):
    intent, params = detect_intent(message, _shots())

    assert intent == "ref_frame"
    assert params == {"shot_id": "sht_bbb"}


@pytest.mark.parametrize(
    "message",
    [
        "What is wrong with the Layout for shot 2?",
        "Check Shot 1 current refs. If critical material is missing, ask me.",
        "检查 Shot 1 当前素材绑定；如果缺关键 reference 就问我。",
    ],
)
def test_shot_material_discussion_offers_only_relevant_tools(
    tmp_projects_dir,
    message,
):
    project = create_project("Scoped tools", "A door opens.")

    tools = _director_tool_schemas(
        project,
        current_message=message,
    )
    names = {item["function"]["name"] for item in tools}

    assert {
        "patch_shot_refs",
        "queue_ref_frame",
        "accept_ref_frame",
        "revise_ref_frame",
        "write_prompt",
        "get_status",
    } <= names
    assert names.isdisjoint(
        {"set_script", "save_storyboard", "plan_shots", "queue_actor_design"}
    )


def test_shot_layout_review_still_offers_exact_scene_override_tool(tmp_projects_dir):
    project = create_project("Scoped scene override", "A door opens.")

    tools = _director_tool_schemas(
        project,
        current_message=(
            "The Layout for Shot 3 uses the wrong room angle. Change its scene to "
            "Interview-room_04_front_left_view_h315_v0 exactly."
        ),
    )
    names = {item["function"]["name"] for item in tools}

    assert "set_shot_scene_ref" in names


def test_chat_guidance_routes_single_shot_authored_edits_safely():
    from app.agents.director.chat import DIRECTOR_CHAT_SYSTEM

    guidance = DIRECTOR_CHAT_SYSTEM.lower()
    assert "exactly one shot" in guidance
    assert "revise_shot" in guidance
    assert "multi-shot" in guidance
    assert "revise_shot then write_prompt" in guidance


def test_look_at_reference_is_llm():
    intent, _ = detect_intent("帮我看一下参考帧", _shots())
    assert intent == "llm"


def test_natural_approve_goes_to_llm():
    intent, _ = detect_intent("批准第1镜", _shots())
    assert intent == "llm"


def test_detect_script():
    script = "EXT. METRO - NIGHT\n\nMAYA stands in the rain.\n\nMAYA\nOf course.\n"
    assert _looks_like_script(script)
    intent, params = detect_intent(script, [])
    assert intent == "set_script"
    assert "METRO" in params["script_text"]


def test_multiline_shot_continuity_review_does_not_replace_existing_script():
    message = (
        "Review the current coverage continuity before changing anything.\n"
        "1 The Empty Room | wide establishing | h45\n"
        "2 Mia Enters | medium wide | h270\n"
        "3 Taking the Chair | medium | h90\n"
        "4 You Want the Job | OTS MCU on Mia | h90\n"
        "5 Then Earn It | two-shot | h315\n\n"
        "Only propose scene plate changes. Do not modify the script or shot refs."
    )

    assert _looks_like_script(message)
    intent, params = detect_intent(message, _shots())

    assert intent == "llm"
    assert "script_text" not in params


def test_locked_script_disables_multiline_fast_path():
    replacement = (
        "Keep the approved ending, but make the buildup quieter.\n\n"
        "The performer crosses the empty room.\n\n"
        "Do not replace the screenplay while revising the storyboard."
    )

    intent, params = detect_intent(replacement, [], script_locked=True)

    assert intent == "llm"
    assert "script_text" not in params


@pytest.mark.asyncio
async def test_fast_set_script_executor_rejects_locked_project(tmp_projects_dir):
    project = create_project("Approved", "INT. ROOM - NIGHT")
    save_project(project.model_copy(update={"script_locked": True}))

    reply, actions, _ = await _execute_intent(
        intent="set_script",
        params={"script_text": "EXT. STREET - DAY"},
        project_id=project.id,
        message="EXT. STREET - DAY",
        svc=object(),
    )

    persisted = load_project(project.id)
    assert persisted is not None
    assert persisted.script_text == "INT. ROOM - NIGHT"
    assert persisted.script_locked is True
    assert actions == []
    assert "locked" in reply.lower()


def test_script_is_what_is_query_not_set_script():
    """「剧本是啥？」must NOT overwrite project script."""
    for msg in (
        "剧本是啥？",
        "剧本是什么",
        "剧本是什么？",
        "剧本是啥",
        "script?",
        "剧本是什么内容",
    ):
        intent, params = detect_intent(msg, _shots())
        assert intent == "llm", msg
        assert "script_text" not in params


def test_script_is_prefix_goes_to_llm_agent():
    """Natural '剧本是…' is handled by the agent (set_script tool), not hard intent."""
    intent, params = detect_intent(
        "剧本是一个女孩在走廊里停下脚步，拿起除臭剂对着椅子喷了几下。",
        _shots(),
    )
    assert intent == "llm"
    assert "script_text" not in params


def test_parse_tools_fence():
    text = (
        "好的，我来拆镜头。\n\n"
        "```json\n"
        '{"tools":[{"name":"plan_shots","args":{}}]}\n'
        "```"
    )
    reply, tools = _parse_tools_from_llm(text)
    assert "拆镜头" in reply
    assert tools[0]["name"] == "plan_shots"


def test_sanitize_tools_blocks_image_after_set_script():
    from app.agents.director.chat import sanitize_tools_for_pipeline
    from app.core.projects.models import Project

    project = Project(
        id="prj_x",
        name="t",
        script_text="old script",
        created_at="t",
        updated_at="t",
        shot_ids=["sht_aaa"],
    )
    tools = [
        {"name": "set_script", "args": {"script": "new story about chair spray"}},
        {"name": "queue_ref_frame", "args": {"all": True}},
    ]
    out, notes = sanitize_tools_for_pipeline(tools, project=project, shots=_shots())
    names = [t["name"] for t in out]
    assert "set_script" in names
    assert "plan_shots" in names
    assert "queue_ref_frame" not in names
    assert any("plan_shots" in n or "拆镜" in n for n in notes)


def test_sanitize_tools_stale_shots_forces_plan_not_image():
    from app.agents.director.chat import sanitize_tools_for_pipeline
    from app.core.projects.models import Project

    # Project script differs from empty planned hash → stale
    project = Project(
        id="prj_stale",
        name="t",
        script_text="brand new script that was never planned",
        created_at="t",
        updated_at="t",
        shot_ids=["sht_aaa"],
    )
    tools = [{"name": "queue_ref_frame", "args": {"shot_index": 1}}]
    out, notes = sanitize_tools_for_pipeline(tools, project=project, shots=_shots())
    names = [t["name"] for t in out]
    assert "queue_ref_frame" not in names
    assert "plan_shots" in names
    assert notes


def test_split_thinking():
    from app.agents.director.chat import split_thinking

    think, visible = split_thinking(
        "<think>先看库存有没有 Mia</think>\n好的，我用 mia 和 beach。"
    )
    assert "Mia" in think or "库存" in think
    assert "beach" in visible
    assert "<think>" not in visible
