"""Opt-in local inference evaluation on an isolated project copy; never submits video.

Run from backend with PYTHONPATH=. and the same DS_LLM_* settings as the service.
Raw drafts and verdicts are written only to the specified local diagnostic report.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from app.agents.director.llm_plan_provider import DirectorLLMPlanProvider
from app.agents.director.planner import role_to_library_kind
from app.agents.director.service import DirectorService
from app.config import settings
from app.core.paths import find_asset_dir
from app.core.projects.models import ShotStatus
from app.core.projects.store import load_project, load_shot, save_project, save_shot


class PassiveSession:
    @asynccontextmanager
    async def llm_session(self, **kwargs):
        yield self

    async def ensure_llm_ready(self):
        pass


class MeasuredProvider:
    def __init__(self, model):
        self.delegate = DirectorLLMPlanProvider(model=model)
        self.model = model
        self.client = self.delegate.client
        self.calls = []

    async def complete(self, system, user, *, guides=()):
        return await self.complete_bounded(system, user, max_tokens=6144, guides=guides)

    async def complete_bounded(self, system, user, *, max_tokens, guides=(), schema=None):
        started = time.monotonic()
        raw = await self.delegate.complete_bounded(system, user, max_tokens=max_tokens, guides=guides, schema=schema)
        stage = "review" if system.startswith("Review a Director Studio tail-frame") else "draft"
        self.calls.append({"stage": stage, "seconds": round(time.monotonic() - started, 2),
                           "system": system, "user": user, "raw": raw})
        print(json.dumps({k: self.calls[-1][k] for k in ("stage", "seconds")}), flush=True)
        return raw

    async def complete_with_images(self, system, user, *, images, guides=()):
        started = time.monotonic()
        raw = await self.delegate.complete_with_images(system, user, images=images, guides=guides)
        self.calls.append({"stage": "vision", "seconds": round(time.monotonic() - started, 2),
                           "user": user, "raw": raw})
        print(json.dumps({k: self.calls[-1][k] for k in ("stage", "seconds")}), flush=True)
        return raw


async def run(args):
    import httpx
    async with httpx.AsyncClient() as client:
        queue = (await client.get(settings.comfy_base_url.rstrip("/") + "/queue")).json()
    if queue.get("queue_running") or queue.get("queue_pending"):
        raise RuntimeError("Comfy is busy; defer local inference evaluation")
    original_root = settings.projects_dir
    project = load_project(args.project_id)
    shot = load_shot(args.project_id, args.shot_id)
    if project is None or shot is None:
        raise ValueError("Project or Shot not found")
    fingerprint = hashlib.sha256(shot.model_dump_json().encode()).hexdigest()
    assets = []
    for ref in shot.refs:
        kind = role_to_library_kind(ref.role.value)
        source = find_asset_dir(kind, ref.asset_id)
        if source is None:
            raise ValueError(f"Missing source asset: {ref.asset_id}")
        assets.append((kind, ref.asset_id, source))
    provider = MeasuredProvider(args.model)
    report = {"source_project_id": project.id, "source_shot_id": shot.id,
              "model": args.model, "runs": [], "calls": provider.calls}
    try:
        with tempfile.TemporaryDirectory(prefix="ds-tail-evaluation-") as temporary:
            settings.projects_dir = Path(temporary) / "projects"
            save_project(project.model_copy(update={"shot_ids": [shot.id]}))
            save_shot(shot.model_copy(update={"h3_job_id": None, "status": ShotStatus.needs_review}))
            for kind, asset_id, source in assets:
                shutil.copytree(source, settings.projects_dir / project.id / "library" / kind / asset_id)
            for index in range(args.runs):
                before = len(provider.calls)
                started = time.monotonic()
                try:
                    updated = await DirectorService(plan_provider=provider, orchestrator=PassiveSession()).write_prompts_after_layout(
                        shot.id, revision_request=args.request)
                    report["runs"].append({"ok": True, "shot": updated.model_dump(mode="json")})
                except Exception as exc:  # noqa: BLE001 - preserve raw diagnostics for any failed inference
                    report["runs"].append({"ok": False, "error": str(exc)})
                report["runs"][-1].update(seconds=round(time.monotonic() - started, 2),
                                          calls=len(provider.calls) - before)
                print(json.dumps({"run": index + 1, **{k: v for k, v in report["runs"][-1].items() if k != "shot"}}), flush=True)
            if args.check_original:
                from app.agents.director.planner import _extract_json_payload
                from app.agents.director.tail_prompt_review import (
                    REVIEW_INSTRUCTIONS,
                    PromptVerdict,
                )
                audits = [call for call in provider.calls if call["stage"] == "review"]
                if audits:
                    request = json.loads(audits[-1]["user"])
                    request["candidate_shot"] = {key: getattr(shot, key) for key in request["candidate_shot"]}
                    request["candidate_prompt"] = shot.prompt_sections.model_dump()
                    raw = await provider.complete_bounded(REVIEW_INSTRUCTIONS,
                        json.dumps(request, ensure_ascii=False), max_tokens=1024, schema=PromptVerdict.model_json_schema())
                    report["original_verdict"] = PromptVerdict.model_validate(_extract_json_payload(raw)).model_dump()
                    print(json.dumps({"original_verdict": report["original_verdict"]}), flush=True)
    finally:
        settings.projects_dir = original_root
        report["source_unchanged"] = fingerprint == hashlib.sha256(load_shot(project.id, shot.id).model_dump_json().encode()).hexdigest()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not report["source_unchanged"]:
        raise RuntimeError("Source Shot changed during evaluation; inspect before comparing results")
    if not all(item["ok"] for item in report["runs"]):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--shot-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--runs", type=int, default=1, choices=(1, 2, 3))
    parser.add_argument("--check-original", action="store_true", help="Also review the original saved prompt against the same correction")
    asyncio.run(run(parser.parse_args()))
