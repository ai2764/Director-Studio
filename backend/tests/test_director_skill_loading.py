from __future__ import annotations

import pytest

from app.api import projects as projects_api
from app.agents.director import skill_loader, stage_guides
from app.agents.director.tool_schema import director_chat_guides
from app.config import settings
from app.core.projects.models import Project, ProjectMode


def _write_skill(path, token: str) -> None:
    path.write_text(
        f"---\nname: director\ndescription: Use when directing H3 Ref2AV.\n---\n\n"
        f"DIRECTOR CONTRACT {token}\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("selection", ["personal", "explicit"])
@pytest.mark.asyncio
async def test_harness_inference_keeps_runtime_contract_with_old_custom_skill(
    tmp_path, tmp_projects_dir, monkeypatch, selection
):
    from pathlib import Path
    from app.agents.director.harness_runtime import BackendTurn
    from app.core.projects.store import create_project

    custom = tmp_path / ".codex" / "skills" / "director" / "SKILL.md"
    custom.parent.mkdir(parents=True)
    _write_skill(custom, "PERSONAL_CREATIVE_DIRECTION")
    monkeypatch.delenv("DS_DIRECTOR_SKILL_PATH", raising=False)
    if selection == "explicit":
        monkeypatch.setenv("DS_DIRECTOR_SKILL_PATH", str(custom))
    else:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    project = create_project("Continuation", "")
    prompts = []

    async def provider(system, user, **kwargs):
        prompts.append(system)
        return {"content": "Which shot should continue?", "tool_calls": []}

    turn = BackendTurn(project.id, "Continue Shot 2 from Shot 1", None, provider)
    await turn.dispatch("context", {})
    await turn.infer({"messages": [{"role": "user", "content": turn.message}]})
    prompt = prompts[0]
    # A stale personal skill must not remove the runtime's tool-use contract
    # from the actual provider request. Keep customization as separate guidance.
    assert "Save the dependency now with `configure_video_context`" in prompt
    assert "say it is set only after the tool returns ok" in prompt
    assert "PERSONAL_CREATIVE_DIRECTION" in prompt
    assert prompt.count("<DIRECTOR_SKILL>") == 1
    assert "<DIRECTOR_CUSTOM_GUIDANCE>" in prompt


def test_bundled_skill_is_not_duplicated_when_no_custom_skill_exists(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.delenv("DS_DIRECTOR_SKILL_PATH", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    prompt = skill_loader.with_director_skill("TASK")
    assert prompt.count("# Director") == 1
    assert "<DIRECTOR_CUSTOM_GUIDANCE>" not in prompt


def test_duplicate_custom_rules_do_not_expand_provider_prompt(tmp_path, monkeypatch):
    from pathlib import Path

    bundled = skill_loader._read_skill(Path(skill_loader.__file__).with_name("DIRECTOR_SKILL.md"))
    custom = tmp_path / "SKILL.md"
    # Different whitespace and a distinct instruction in the same bullet list.
    shared = "- Use only real inventory IDs and file keys. Prefer the angle that supports the intended framing and screen direction."
    custom.write_text(shared.replace(" ", "  ") + "\n- Prefer quiet, patient camera movement.", encoding="utf-8")
    monkeypatch.setenv("DS_DIRECTOR_SKILL_PATH", str(custom))
    prompt = skill_loader.with_director_skill("TASK")
    assert " ".join(prompt.split()).count("Use only real inventory IDs") == 1
    assert "Prefer quiet, patient camera movement." in prompt
    custom.write_text(bundled, encoding="utf-8")
    prompt = skill_loader.with_director_skill("TASK")
    assert "<DIRECTOR_CUSTOM_GUIDANCE>" not in prompt
    writer_prompt = skill_loader.with_director_skill("TASK", writer_only=True)
    assert "<DIRECTOR_CUSTOM_GUIDANCE>" not in writer_prompt
    assert "queue_actor_design" not in writer_prompt
    assert "Save the dependency now" not in writer_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["complete", "complete_with_images", "complete_bounded"])
async def test_writer_provider_omits_unavailable_tool_rules_but_keeps_production_guides(
    tmp_path, monkeypatch, method
):
    from pathlib import Path
    from types import SimpleNamespace
    from app.agents.director.llm_plan_provider import DirectorLLMPlanProvider

    monkeypatch.delenv("DS_DIRECTOR_SKILL_PATH", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    prompts = []

    class Client:
        async def generate(self, model, prompt, **kwargs):
            prompts.append(prompt)
            return "ok"

        async def chat(self, model, prompt, **kwargs):
            prompts.append(prompt)
            return "ok"

        async def chat_response(self, model, *, messages, **kwargs):
            prompts.append(messages[0]["content"])
            return {"content": "ok", "tool_calls": []}

    provider = DirectorLLMPlanProvider(SimpleNamespace(client=Client()), model="fixture")
    kwargs = {"guides": ("h3-prompt-writing",)}
    if method == "complete_with_images":
        kwargs["images"] = ["fixture-image"]
    if method == "complete_bounded":
        kwargs["max_tokens"] = 1024
    await getattr(provider, method)("Write this shot", "Fixture evidence", **kwargs)
    prompt = prompts[0]
    assert "finished-video Motion Context" in prompt
    assert "Human-approved" in prompt or "human-approved" in prompt
    assert '<DIRECTOR_STAGE_GUIDE id="h3-prompt-writing">' in prompt
    assert "queue_actor_design" not in prompt
    assert "classify_chat_image" not in prompt
    assert "Save the dependency now" not in prompt
    assert len(prompt) < 9000


def test_with_director_skill_loads_only_requested_guides(tmp_path, monkeypatch):
    core = tmp_path / "DIRECTOR_SKILL.md"
    guides = tmp_path / "guides"
    guides.mkdir()
    core.write_text("CORE CONTRACT", encoding="utf-8")
    (guides / "reference-strategy.md").write_text(
        "REFERENCE STRATEGY", encoding="utf-8"
    )
    (guides / "h3-prompt-writing.md").write_text("H3 GUIDE", encoding="utf-8")
    monkeypatch.setattr(skill_loader, "_skill_path", lambda: core)
    monkeypatch.setattr(stage_guides, "_guides_dir", lambda: guides)

    prompt = skill_loader.with_director_skill(
        "TASK", guides=("reference-strategy",)
    )

    assert "CORE CONTRACT" in prompt
    assert "REFERENCE STRATEGY" in prompt
    assert "H3 GUIDE" not in prompt
    assert "TASK" in prompt


def test_unknown_stage_guide_fails_clearly():
    with pytest.raises(ValueError, match="unknown Director stage guide: bogus"):
        stage_guides.load_stage_guides(("bogus",))


@pytest.mark.parametrize("guide_id", ["script-planning", "storyboard-validation"])
def test_requested_stage_guide_loads_as_a_non_empty_block(guide_id):
    guide = stage_guides.load_stage_guides((guide_id,))

    assert guide.startswith(f'<DIRECTOR_STAGE_GUIDE id="{guide_id}">\n')
    assert guide.endswith("\n</DIRECTOR_STAGE_GUIDE>")
    assert len(guide.splitlines()) > 3


def test_mv_project_chat_always_loads_music_video_planning_guide():
    project = Project(
        id="prj_mv",
        name="Music video",
        script_text="",
        mode=ProjectMode.mv,
        created_at="2026-09-22T00:00:00+00:00",
        updated_at="2026-09-22T00:00:00+00:00",
    )

    guides = director_chat_guides(
        project,
        include_visual_qc=False,
        current_message="Plan the next lyric section",
    )

    assert guides == ("music-video-planning",)


def test_music_video_guide_teaches_job_time_song_segments():
    guide = (
        stage_guides._guides_dir() / "music-video-planning.md"
    ).read_text(encoding="utf-8")

    assert "music_segment" in guide
    assert "core_start_s/core_end_s" in guide
    assert "submit_start_s/submit_end_s" in guide
    assert "Audio 1" in guide
    assert "canonical H3 submit" in guide
    assert "Until the project exposes a real source-audio binding" not in guide


def test_stage_guide_registry_matches_non_empty_markdown_files():
    guide_dir = stage_guides._guides_dir()
    guide_files = {path.stem for path in guide_dir.glob("*.md")}

    assert guide_files == set(stage_guides.GUIDE_IDS)
    assert all(
        (guide_dir / f"{guide_id}.md").read_text(encoding="utf-8").strip()
        for guide_id in stage_guides.GUIDE_IDS
    )


def test_reference_frame_guidance_teaches_explicit_gpt_multi_image_prompting():
    core = (stage_guides._guides_dir().parent / "DIRECTOR_SKILL.md").read_text(
        encoding="utf-8"
    )
    guide = (stage_guides._guides_dir() / "reference-frame-generation.md").read_text(
        encoding="utf-8"
    )

    assert "queue_gpt_ref_frame" in core
    assert "explicitly" in core
    assert "Image1" in guide and "Image4" in guide
    assert "one final cinematic frame" in guide
    assert "rejected" in guide.lower()


@pytest.mark.asyncio
async def test_plan_provider_embeds_storyboard_validation_stage_guide():
    prompts: list[str] = []

    class _Client:
        async def generate(self, model: str, prompt: str) -> str:
            prompts.append(prompt)
            return '{"valid": true, "issues": []}'

    provider = projects_api.OllamaPlanProvider(model="qwen-test")
    provider.client = _Client()

    result = await provider.complete(
        "VALIDATE STORYBOARD",
        "CANDIDATE PAYLOAD",
        guides=("storyboard-validation",),
    )

    assert result == '{"valid": true, "issues": []}'
    assert len(prompts) == 1
    prompt = prompts[0]
    assert '<DIRECTOR_STAGE_GUIDE id="storyboard-validation">' in prompt
    assert "screenplay coverage" in prompt
    assert "Do not propose replacement shots" in prompt
    assert prompt.index("storyboard-validation") < prompt.index("VALIDATE STORYBOARD")


@pytest.mark.asyncio
async def test_plan_provider_loads_latest_director_skill_before_every_call(
    tmp_path, monkeypatch
):
    skill_path = tmp_path / "SKILL.md"
    _write_skill(skill_path, "VERSION_ONE")
    monkeypatch.setenv("DS_DIRECTOR_SKILL_PATH", str(skill_path))

    prompts: list[str] = []

    class _Client:
        async def generate(self, model: str, prompt: str) -> str:
            prompts.append(prompt)
            return "ok"

    provider = projects_api.OllamaPlanProvider(model="qwen-test")
    provider.client = _Client()

    await provider.complete("TASK SYSTEM", "TASK USER")
    _write_skill(skill_path, "VERSION_TWO")
    await provider.complete("TASK SYSTEM", "TASK USER")

    assert "DIRECTOR CONTRACT VERSION_ONE" in prompts[0]
    assert "VERSION_TWO" not in prompts[0]
    assert "DIRECTOR CONTRACT VERSION_TWO" in prompts[1]
    assert "VERSION_ONE" not in prompts[1]
    assert prompts[1].index("DIRECTOR CONTRACT") < prompts[1].index("TASK SYSTEM")


@pytest.mark.asyncio
async def test_project_chat_loads_director_skill_before_ollama(
    tmp_path, monkeypatch
):
    skill_path = tmp_path / "SKILL.md"
    _write_skill(skill_path, "CHAT_RULES")
    monkeypatch.setenv("DS_DIRECTOR_SKILL_PATH", str(skill_path))

    prompts: list[str] = []

    class _Ollama:
        async def generate(self, model: str, prompt: str) -> str:
            prompts.append(prompt)
            return "ok"

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class _Orchestrator:
        ollama = _Ollama()

        def llm_session(self, **kwargs):
            return _Session()

        async def ensure_llm_ready(self, **kwargs):
            return None

    import app.core.vram as vram_module
    import app.core.vram.director_model as model_module

    monkeypatch.setattr(vram_module, "get_orchestrator", lambda: _Orchestrator())
    monkeypatch.setattr(model_module, "get_director_model", lambda: "qwen-test")

    chat_fn = await projects_api._make_chat_fn()
    await chat_fn("CHAT SYSTEM", "CHAT USER")

    assert len(prompts) == 1
    assert "DIRECTOR CONTRACT CHAT_RULES" in prompts[0]
    assert prompts[0].index("DIRECTOR CONTRACT") < prompts[0].index("CHAT SYSTEM")
