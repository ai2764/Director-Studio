"""Prepared-shot architectural comparison, with simulated inference and real contracts.

No live model, existing project, GPU or video-generation service is used.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from statistics import median
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from PIL import Image
from app.config import settings
from app.core.projects.store import create_project, save_project, save_shot
from app.core.projects.models import Shot, ShotRef, RefRole
from app.core.schemas import LibraryAsset
from app.agents.director.service import DirectorService, _build_context
from app.agents.director.context_io import save_agent_context
from app.agents.director.llm_plan_provider import DirectorLLMPlanProvider
from app.agents.director.reference_facts import reference_contract_current


class SimulatedClient:
    def __init__(self):
        self.calls = []

    def answer(self, prompt):
        if "reference review decision" in prompt:
            return json.dumps({"brief": None, "rewrite_prompt": True,
                "reason": "The observed gear supports the saved shot.",
                "blocking_question": None, "configuration_issues": []})
        return json.dumps({"subject_definitions": "<Picture 1> defines the silver gear.",
            "summary": "A camera pans across the stationary gear.",
            "retention_analysis": "Preserve the gear from <Picture 1>.",
            "detailed_description": "0-6 seconds: a slow leftward pan across the gear.",
            "overall_soundscape": "Quiet workshop ambience.", "non_diegetic_music": "None."})

    async def generate(self, model, prompt):
        self.calls.append({"images": 0, "chars": len(prompt)})
        return self.answer(prompt)

    async def chat(self, model, prompt, *, images, **kwargs):
        self.calls.append({"images": len(images), "chars": len(prompt)})
        return json.dumps({"readable": True, "description": "A stationary silver gear.",
                           "concerns": [], "conflicts": [], "uncertainties": []})

    async def chat_response(self, model, *, messages, **kwargs):
        prompt = messages[0]["content"]
        self.calls.append({"images": 0, "chars": len(prompt)})
        return {"content": self.answer(prompt), "finish_reason": "stop"}


class NoGPU:
    @asynccontextmanager
    async def llm_session(self, **kwargs):
        yield

    async def ensure_llm_ready(self):
        pass


async def trial(root, focused):
    old = {name: getattr(settings, name) for name in (
        "data_dir", "projects_dir", "library_root", "library_dir", "jobs_dir",
        "director_focused_execution", "director_visual_facts_cache",
        "director_compact_vision_prompt", "director_vision_base_url",
        "director_vision_model", "director_task_context_mode")}
    try:
        settings.data_dir = root
        settings.projects_dir = root / "projects"
        settings.library_root = root / "library"
        settings.library_dir = root / "library" / "actors"
        settings.jobs_dir = root / "jobs"
        settings.director_focused_execution = focused
        settings.director_visual_facts_cache = True
        settings.director_compact_vision_prompt = False
        settings.director_vision_base_url = ""
        settings.director_vision_model = ""
        settings.director_task_context_mode = "off"
        project = create_project("Synthetic gear film", "Every shot keeps the same silver gear. No dialogue.")
        folder = settings.library_root / "props" / "prop_gear"
        folder.mkdir(parents=True)
        Image.effect_noise((256, 256), 30).convert("RGB").save(folder / "gear.png")
        asset = LibraryAsset(id="prop_gear", kind="props", name="Gear",
            pipeline_id="external", job_id="synthetic", created_at="2026-11-03T00:00:00Z",
            files={"selected": "gear.png"})
        (folder / "asset.json").write_text(asset.model_dump_json(), encoding="utf-8")
        shots = [Shot(id=f"sht_{index}", project_id=project.id, scene_id="scene",
            title=f"Shot {index}", script_beat="The camera pans across a stationary silver gear.",
            duration_s=6, camera_motion="slow pan left",
            refs=[ShotRef(role=RefRole.prop, asset_id=asset.id, file_key="selected", picture_index=1)])
            for index in range(24)]
        for shot in shots:
            save_shot(shot)
        project.shot_ids = [shot.id for shot in shots]
        save_project(project)
        save_agent_context(project.id, _build_context(project, shots, phase="planned"))
        client = SimulatedClient()
        provider = DirectorLLMPlanProvider(SimpleNamespace(client=client), model="simulated")
        service = DirectorService(plan_provider=provider, orchestrator=NoGPU())
        target = shots[12]
        first = await service.write_prompts_after_layout(target.id)
        cold = list(client.calls)
        client.calls.clear()
        first.camera_motion = "very slow pan left"
        save_shot(first)
        rewritten = await service.write_prompts_after_layout(target.id)
        assert reference_contract_current(project, rewritten)
        return {"cold": cold, "warm": list(client.calls)}
    finally:
        for name, value in old.items():
            setattr(settings, name, value)


async def run(trials):
    conditions = {}
    with tempfile.TemporaryDirectory(prefix="director-execution-benchmark-") as directory:
        for name, focused in (("baseline", False), ("focused", True)):
            samples = [await trial(Path(directory) / name / str(index), focused) for index in range(trials)]
            conditions[name] = {phase: {
                "trials": trials,
                "text_calls_median": median(sum(call["images"] == 0 for call in item[phase]) for item in samples),
                "vision_calls_median": median(sum(call["images"] > 0 for call in item[phase]) for item in samples),
                "input_chars_median": median(sum(call["chars"] for call in item[phase]) for item in samples),
            } for phase in ("cold", "warm")}
    return {"inference": "simulated", "scenario": "prepared ordinary shot; camera-speed-only edit",
            "live_latency_verified": False, "creative_quality_verified": False,
            "autonomous_planning_benchmarked": False, "conditions": conditions}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.trials <= 20:
        parser.error("--trials must be between 1 and 20")
    result = asyncio.run(run(args.trials))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
