"""Wake the active Director LLM after Comfy work (reload model + optional context).

Usage (from backend/):
  python -m app.scripts.wake_agent
  python -m app.scripts.wake_agent --project prj_xxx
  python -m app.scripts.wake_agent --release   # load then unload (smoke)
  python -m app.scripts.wake_agent --keep      # load and leave resident

Comfy jobs call release_llm before GPU work. Call this (or POST /api/director/wake)
before the next plan / prompt-rewrite turn so the LLM is ready and context is reloaded.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


async def _run(project_id: str | None, *, release: bool, keep: bool) -> int:
    from app.agents.director.context_io import load_agent_context
    from app.core.vram import get_orchestrator

    orch = get_orchestrator()
    provider = orch.provider
    model = str(provider.model_status().get("model") or "").strip()
    print(f"wake: provider={provider.provider_id} model={model}", flush=True)
    if orch.owner == "comfy":
        print(
            f"wake: WARNING GPU owner=comfy pipeline={orch.comfy_pipeline}. "
            "Wait for Comfy job to finish (exclusive VRAM) or wake will be slow/timeout.",
            flush=True,
        )

    # llm_session frees Comfy before preparing the active provider when needed.
    print("wake: preparing active LLM…", flush=True)
    async with orch.llm_session(release_on_exit=not keep and not release):
        await orch.ensure_llm_ready()
        print("wake: LLM ready", flush=True)


        if project_id:
            ctx = load_agent_context(project_id)
            if ctx is None:
                print(f"wake: no context for project {project_id}", flush=True)
            else:
                print(
                    f"wake: loaded context project={ctx.project_id} "
                    f"phase={ctx.last_phase} shots={len(ctx.shot_summaries)} "
                    f"models={ctx.models_used}",
                    flush=True,
                )
                # Touch generate with a tiny context-bound prompt so the model
                # reloads conversational context without inventing a plan.
                summary = json.dumps(
                    {
                        "project_id": ctx.project_id,
                        "last_phase": ctx.last_phase,
                        "shot_ids": [s.get("id") for s in ctx.shot_summaries[:12]],
                    },
                    ensure_ascii=False,
                )
                text = await provider.client.generate(
                    model,
                    "You are the Director Studio local agent. "
                    "Acknowledge context reload in one short sentence.\n"
                    f"CONTEXT:\n{summary}\n",
                )
                print(f"wake: agent reply: {text.strip()[:300]}", flush=True)

        if release:
            await orch.release_llm()
            print("wake: released LLM (VRAM free for Comfy)", flush=True)
        elif keep:
            print("wake: keeping LLM resident (owner=llm until next release)", flush=True)
        else:
            # default: session exit releases
            print("wake: session end will release LLM", flush=True)

    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Wake Director LLM agent + reload context")
    p.add_argument("--project", help="Project id to reload agent/context.json for")
    p.add_argument(
        "--release",
        action="store_true",
        help="Unload model after wake (smoke / free VRAM)",
    )
    p.add_argument(
        "--keep",
        action="store_true",
        help="Leave model loaded after wake (do not unload on exit)",
    )
    args = p.parse_args(argv)
    if args.release and args.keep:
        print("error: use only one of --release / --keep", file=sys.stderr)
        return 2
    return asyncio.run(_run(args.project, release=args.release, keep=args.keep))


if __name__ == "__main__":
    raise SystemExit(main())
