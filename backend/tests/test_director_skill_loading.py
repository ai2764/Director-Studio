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
