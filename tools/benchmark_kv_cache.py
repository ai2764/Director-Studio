"""Read-only KV-cache prefix reuse benchmark for Director chat prompt assembly.

Compares three prompt-assembly strategies over an identical multi-turn,
multi-tool Director Studio conversation against any OpenAI-compatible provider
that reports cached prompt tokens (such as DeepSeek, OpenAI, or vLLM/SGLang):

  A_baseline               PROJECT_STATE appended inside messages[0] (system prompt),
                           with dynamic tool schema changes when a Layout appears.
  B_ephemeral_tail         Static messages[0] system prompt + ephemeral PROJECT_STATE
                           appended as the final user message (never stored in history).
  C_tail_plus_static_tools Same as B, plus a static accept_ref_frame tool schema
                           (without dynamic layout_ref_id enum churn).

Reads DS_LLM_BASE_URL and DS_LLM_API_KEY from the environment or backend/.env
(or explicit CLI flags). Never prints credentials or mutates real project data.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

from openai import AsyncOpenAI

from app.agents.director.chat_context import project_context_blob
from app.agents.director.chat_orchestrator import DIRECTOR_CHAT_SYSTEM
from app.agents.director.skill_loader import with_director_skill
from app.agents.director.tool_schema import DIRECTOR_TOOL_SCHEMAS, director_tool_schemas
from app.config import settings
from app.core.projects.layouts import LayoutBrief, LayoutReference
from app.core.projects.models import Shot


def _extract_usage(resp: Any) -> dict[str, Any]:
    usage = resp.usage
    if usage is None:
        return {
            "prompt_tokens": 0,
            "cache_hit": 0,
            "cache_miss": 0,
            "completion_tokens": 0,
            "hit_rate": 0.0,
        }
    raw = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
    hit = raw.get("prompt_cache_hit_tokens")
    if hit is None:
        details = raw.get("prompt_tokens_details") or {}
        hit = details.get("cached_tokens")
    prompt_tokens = int(raw.get("prompt_tokens") or 0)
    miss = raw.get("prompt_cache_miss_tokens")
    if miss is None and hit is not None:
        miss = max(0, prompt_tokens - int(hit))
    hit_int = int(hit) if hit is not None else 0
    miss_int = int(miss) if miss is not None else prompt_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "cache_hit": hit_int,
        "cache_miss": miss_int,
        "completion_tokens": int(raw.get("completion_tokens") or 0),
        "hit_rate": (hit_int / prompt_tokens * 100.0) if prompt_tokens else 0.0,
    }


def _prior_history(nonce: str) -> list[dict[str, Any]]:
    """Synthetic multi-turn Director conversation representing an active session."""
    return [
        {
            "role": "user",
            "content": (
                f"[session:{nonce}] Let's design a 30-second noir sci-fi short film called Rain Over Neon Station. "
                "Tone: wet concrete, sodium vapor lamps, subtle handheld tension, realistic clothing, no cyberpunk cliches. "
                "Characters: Maya (30s investigative archivist, dark wool trench coat, brass compass) and Kai (courier, faded olive rain jacket). "
                "We will shoot 4-5 connected shots moving from the underground subway platform up to the rainy rooftop overpass."
            ),
        },
        {
            "role": "assistant",
            "content": (
                "Great premise and visual palette. Keeping the lighting grounded in practical sodium-vapor overhead fixtures "
                "and cold cyan train spill will make Ref2AV consistency much easier across shots. Let's structure this in four beats:\n"
                "1. Platform arrival — establish Maya's tension, the cracked brass compass in her gloved hand, and the passing train light sweep.\n"
                "2. Escalator ascent — transitional movement from cold fluorescent underground into misting rain.\n"
                "3. Overpass lookout — establish Kai checking his watch beneath a buzzing streetlamp, wet asphalt reflections.\n"
                "4. Rendezvous handoff — Maya emerges from the stairwell, compass exchanged before the station shutter drops.\n\n"
                "When you're ready, paste the exact screenplay lines and dialogue beats you want locked in, and confirm which Library "
                "actor/scene angles we should anchor for Maya, Kai, the platform, and the overpass."
            ),
        },
        {
            "role": "user",
            "content": (
                "Let's refine the visual continuity notes before locking the screenplay: "
                "1) Maya always keeps her hair tied in a low messy bun with loose rain-damp strands; never change her wool trench coat. "
                "2) The brass compass has a diagonal hairline crack across the glass and a slightly bent hinge—keep it recognizable in close-ups. "
                "3) For sound design, keep ambient rumble continuous: distant subway braking hum, rain pelting corrugated awning, "
                "quiet footsteps on wet tile, and subtle low cello drone only when the shutter starts descending. "
                "4) Dialogue must stay concise and whispered/urgent so each 5-6s H3 clip breathes naturally without rushed lip-sync. "
                "5) Keep camera movement motivated: slow push-in on the platform, locked tripod or gentle tracking on the overpass, no whip pans. "
                "Please acknowledge these continuity rules and summarize how they map to H3 Ref2AV constraints."
            ),
        },
        {
            "role": "assistant",
            "content": (
                "Understood—continuity rules noted and mapped cleanly to H3 Ref2AV constraints:\n"
                "- Character & wardrobe lock: Maya's low damp bun and dark wool trench coat remain constant; we will rely on authoritative "
                "Actor references (and keep incompatible wardrobe out of every shot's Picture pack since every Picture conditions the whole clip).\n"
                "- Prop consistency: the diagonally cracked brass compass with the bent hinge should be cast via a dedicated Prop reference in any "
                "close-up or handoff shot rather than hoping a wide Layout preserves fine detail.\n"
                "- Audio & dialogue pacing: short whispered/urgent cues fit 5-6s Ref2AV windows cleanly without rushed articulation; "
                "we'll keep ambient subway hum, rain on corrugated metal, and wet footsteps in the soundscape section and reserve the subtle "
                "low cello drone for non-diegetic music in the final beat.\n"
                "- Camera language: motivated push-ins and locked/gentle tracking prevent motion artifacts and make video continuation seams cleaner.\n\n"
                "Send over the script text whenever you're ready for me to save it."
            ),
        },
        {
            "role": "user",
            "content": (
                "One more visual note for the platform and overpass environments so we don't forget during shot revisions: "
                "The subway platform has yellowed mosaic tiles, scuffed yellow tactile paving along the edge, rusted blue steel pillars "
                "with faded station numbers '04', and flickering ceiling tube lights near the tunnel mouth. "
                "The rooftop overpass has dark wet asphalt, chain-link fencing with rain droplets clinging to the wire, "
                "a single warm sodium streetlamp casting a tight cone of amber light, and distant out-of-focus apartment windows in cool teal. "
                "For Shot 1, Maya enters looking left-to-right toward the tunnel mouth; for Shot 2, Kai stands facing slightly screen-left "
                "so eyelines match across the cut. Keep this exact screen-direction geometry consistent through all subsequent revisions."
            ),
        },
        {
            "role": "assistant",
            "content": (
                "Locked into our continuity notes:\n"
                "- Platform geography: yellowed mosaic tiles, worn yellow tactile edge, rusted blue steel pillars marked '04', "
                "flickering overhead tubes toward the tunnel mouth; Maya oriented screen-left looking screen-right.\n"
                "- Overpass geography: wet dark asphalt, rain-beaded chain-link fence, single warm amber sodium streetlamp cone, "
                "soft teal apartment bokeh in the background; Kai oriented slightly screen-left to preserve eyeline continuity across the cut.\n\n"
                "Ready whenever you want to save the script and start building shots."
            ),
        },
    ]


def _build_fixture_states(tmpdir: Path, tag: str):
    """Build realistic progressive PROJECT_STATE JSON snapshots in an isolated temp store."""
    prev_projects = settings.projects_dir
    prev_library = settings.library_root
    prev_jobs = settings.jobs_dir
    try:
        settings.projects_dir = tmpdir / "projects"
        settings.library_root = tmpdir / "library"
        settings.jobs_dir = tmpdir / "jobs"
        settings.projects_dir.mkdir(parents=True, exist_ok=True)
        settings.library_root.mkdir(parents=True, exist_ok=True)
        settings.jobs_dir.mkdir(parents=True, exist_ok=True)

        from app.core.projects.store import create_project, save_project, save_shot

        project = create_project(f"KV Bench {tag}", "")
        s1 = project_context_blob(project, [], message="Save this script", focused=True)
        tools_before_layout = director_tool_schemas(project, current_message="", shots=[])

        script = (
            f"TITLE: Rain Over Neon Station ({tag})\n\n"
            "INT. SUBWAY PLATFORM - NIGHT\n"
            "Maya stands beside the yellow safety line holding a cracked brass compass. "
            "A commuter train rushes past, casting flickering cyan light across her face.\n"
            "MAYA: We are already two minutes late.\n\n"
            "EXT. ROOFTOP OVERPASS - NIGHT\n"
            "Rain pours down as Kai checks his watch under a buzzing streetlamp and waves Maya forward.\n"
            "KAI: Take the stairs before the shutter closes!\n"
        )
        project = project.model_copy(update={"script_text": script})
        save_project(project)
        s2 = project_context_blob(project, [], message="Save this script", focused=True)

        shot1 = Shot(
            id="sht_bench_01",
            project_id=project.id,
            scene_id="sc01",
            title="Platform Compass",
            script_beat="Maya waits by the yellow line holding a cracked brass compass as a train rushes past.",
            dialogue=["Maya: We are already two minutes late."],
            shot_type="Medium close-up",
            camera_angle="Eye level",
            camera_motion="Slow push-in",
            composition="Maya left-of-frame, blurred train lights sweeping right",
            duration_s=6.0,
        )
        shot2 = Shot(
            id="sht_bench_02",
            project_id=project.id,
            scene_id="sc02",
            title="Overpass Rain Signal",
            script_beat="Kai checks his watch under a buzzing streetlamp in heavy rain and waves Maya forward.",
            dialogue=["Kai: Take the stairs before the shutter closes!"],
            shot_type="Medium wide",
            camera_angle="Slightly low angle",
            camera_motion="Handheld tracking",
            composition="Kai centered under streetlamp cone, wet asphalt foreground",
            duration_s=5.0,
        )
        save_shot(shot1)
        save_shot(shot2)
        project = project.model_copy(update={"shot_ids": [shot1.id, shot2.id]})
        save_project(project)
        s3 = project_context_blob(
            project, [shot1, shot2], message="Revise shot 1 camera motion", focused=True
        )

        layout_ref = LayoutReference(
            id="lref_bench_01",
            brief=LayoutBrief(
                purpose="Opening platform composition",
                state_description="Maya left of frame by yellow tactile line with compass",
            ),
            asset_id="ast_layout_01",
        )
        shot1_v2 = shot1.model_copy(
            update={
                "camera_motion": "Locked static tripod shot",
                "layout_refs": [layout_ref],
                "layout_asset_id": "ast_layout_01",
            }
        )
        save_shot(shot1_v2)
        s4 = project_context_blob(
            project, [shot1_v2, shot2], message="Confirm shot 1 details", focused=True
        )
        tools_after_layout = director_tool_schemas(
            project, current_message="", shots=[shot1_v2, shot2]
        )
        static_tools = copy.deepcopy(DIRECTOR_TOOL_SCHEMAS)
        static_system = with_director_skill(DIRECTOR_CHAT_SYSTEM)
        return static_system, [s1, s2, s3, s4], tools_before_layout, tools_after_layout, static_tools
    finally:
        settings.projects_dir = prev_projects
        settings.library_root = prev_library
        settings.jobs_dir = prev_jobs


async def _run_mode(
    client: AsyncOpenAI,
    *,
    model: str,
    mode: str,
    static_system: str,
    states: list[str],
    tools_before_layout: list[dict[str, Any]],
    tools_after_layout: list[dict[str, Any]],
    static_tools: list[dict[str, Any]],
    nonce: str,
    disable_thinking: bool,
) -> list[dict[str, Any]]:
    s1, s2, s3, s4 = states
    history = _prior_history(nonce)
    rows: list[dict[str, Any]] = []

    async def infer(step_label: str, state_json: str, *, has_layout: bool) -> Any:
        if mode == "A_baseline":
            tools = tools_after_layout if has_layout else tools_before_layout
            wire_messages = [
                {"role": "system", "content": f"{static_system}\n\nPROJECT_STATE:\n{state_json}"},
                *history,
            ]
        elif mode == "B_ephemeral_tail":
            tools = tools_after_layout if has_layout else tools_before_layout
            wire_messages = [
                {"role": "system", "content": static_system},
                *history,
                {"role": "user", "content": f"PROJECT_STATE:\n{state_json}"},
            ]
        elif mode == "C_tail_plus_static_tools":
            tools = static_tools
            wire_messages = [
                {"role": "system", "content": static_system},
                *history,
                {"role": "user", "content": f"PROJECT_STATE:\n{state_json}"},
            ]
        else:
            raise ValueError(f"Unknown mode: {mode}")

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": wire_messages,
            "tools": tools,
            "temperature": 0.0,
            "max_tokens": 160,
        }
        if disable_thinking:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

        t0 = time.monotonic()
        resp = await client.chat.completions.create(**kwargs)
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        usage = _extract_usage(resp)
        msg = resp.choices[0].message
        tool_names = [tc.function.name for tc in (msg.tool_calls or [])]
        row = {
            "step": step_label,
            "elapsed_ms": elapsed_ms,
            "tools_called": tool_names,
            "reply_preview": (msg.content or "").strip().replace("\n", " ")[:80],
            **usage,
        }
        rows.append(row)
        print(
            f"  [{mode:24s}] {step_label:24s} | "
            f"in={usage['prompt_tokens']:5d}  hit={usage['cache_hit']:5d}  miss={usage['cache_miss']:5d}  "
            f"rate={usage['hit_rate']:5.1f}% | {elapsed_ms:4d}ms | calls={tool_names}"
        )
        return msg

    history.append({
        "role": "user",
        "content": (
            "Please save this screenplay now using set_script:\n"
            "INT. SUBWAY PLATFORM - NIGHT. Maya holds a cracked brass compass as a train rushes past. "
            "MAYA: We are already two minutes late. "
            "EXT. ROOFTOP OVERPASS - NIGHT. Rain pours as Kai waves Maya forward. "
            "KAI: Take the stairs before the shutter closes!"
        ),
    })
    await infer("S1 (cold, state=s1)", s1, has_layout=False)

    history.append({
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "call_b1",
            "type": "function",
            "function": {"name": "set_script", "arguments": '{"script":"INT. SUBWAY PLATFORM - NIGHT..."}'},
        }],
    })
    history.append({"role": "tool", "tool_call_id": "call_b1", "content": '{"ok":true,"script_chars":368}'})
    msg = await infer("S2 (after set_script->s2)", s2, has_layout=False)
    history.append({"role": "assistant", "content": msg.content or "Screenplay saved."})

    history.append({
        "role": "user",
        "content": "The 2-shot storyboard is saved. Please update shot 1's camera_motion to 'Locked static tripod shot' using revise_shot.",
    })
    await infer("S3 (new turn, state=s3)", s3, has_layout=False)

    history.append({
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "call_b2",
            "type": "function",
            "function": {
                "name": "revise_shot",
                "arguments": '{"shot_id":"sht_bench_01","camera_motion":"Locked static tripod shot"}',
            },
        }],
    })
    history.append({
        "role": "tool",
        "tool_call_id": "call_b2",
        "content": '{"ok":true,"shot_id":"sht_bench_01","updated":["camera_motion"]}',
    })
    msg2 = await infer("S4 (s4 + layout enum)", s4, has_layout=True)
    history.append({"role": "assistant", "content": msg2.content or "Updated shot 1 camera motion."})

    history.append({
        "role": "user",
        "content": "Without calling tools, confirm in one short sentence Shot 1's current camera_motion, duration_s, and layout_asset_id from PROJECT_STATE.",
    })
    await infer("S5 (readback check)", s4, has_layout=True)

    return rows


async def _run(args: argparse.Namespace) -> int:
    if load_dotenv is not None and (BACKEND_DIR / ".env").exists():
        load_dotenv(BACKEND_DIR / ".env", override=False)

    base_url = (args.base_url or os.environ.get("DS_LLM_BASE_URL") or "https://api.deepseek.com/v1").strip()
    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        print(f"ERROR: environment variable {args.api_key_env} is empty.", file=sys.stderr)
        return 2

    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=120.0)
    models = await client.models.list()
    available = [m.id for m in models.data]
    model = args.model or (available[0] if available else "")
    if not model:
        print("ERROR: provider returned no models; pass --model explicitly.", file=sys.stderr)
        return 2

    disable_thinking = (
        args.disable_thinking
        if args.disable_thinking is not None
        else ("deepseek" in base_url.lower() or "deepseek" in model.lower())
    )
    print(f"Provider: {base_url} | model={model} | disable_thinking={disable_thinking}\n")

    nonce = str(int(time.time()))
    modes = ("A_baseline", "B_ephemeral_tail", "C_tail_plus_static_tools")
    results: dict[str, list[dict[str, Any]]] = {}

    for mode in modes:
        print(f"=== {mode} ===")
        with tempfile.TemporaryDirectory() as tmp:
            static_sys, states, t_before, t_after, t_static = _build_fixture_states(
                Path(tmp), f"{mode}-{nonce}"
            )
            # Distinct prefix tag per mode guarantees each run starts 100% cold on S1.
            isolated_sys = f"[bench-isolation:{mode}-{nonce}]\n{static_sys}"
            results[mode] = await _run_mode(
                client,
                model=model,
                mode=mode,
                static_system=isolated_sys,
                states=states,
                tools_before_layout=t_before,
                tools_after_layout=t_after,
                static_tools=t_static,
                nonce=f"{mode}-{nonce}",
                disable_thinking=disable_thinking,
            )
        print()

    summary: dict[str, dict[str, Any]] = {}
    print("=== WARM STEPS SUMMARY (S2..S5, excluding cold S1) ===")
    for mode, rows in results.items():
        warm = rows[1:]
        tot_in = sum(r["prompt_tokens"] for r in warm)
        tot_hit = sum(r["cache_hit"] for r in warm)
        tot_miss = sum(r["cache_miss"] for r in warm)
        rate = (tot_hit / tot_in * 100.0) if tot_in else 0.0
        avg_ms = sum(r["elapsed_ms"] for r in warm) / len(warm)
        summary[mode] = {
            "warm_prompt_tokens": tot_in,
            "warm_cache_hit_tokens": tot_hit,
            "warm_cache_miss_tokens": tot_miss,
            "warm_hit_rate_pct": round(rate, 2),
            "warm_avg_latency_ms": round(avg_ms),
            "readback_preview": rows[-1]["reply_preview"],
        }
        print(
            f"  {mode:24s} | warm_in={tot_in:6d}  hit={tot_hit:6d}  miss={tot_miss:6d}  "
            f"hit_rate={rate:5.1f}% | avg={avg_ms:5.0f}ms"
        )
        print(f"     S5 readback: {rows[-1]['reply_preview']}")

    if args.output:
        payload = {
            "base_url": base_url,
            "model": model,
            "disable_thinking": disable_thinking,
            "summary": summary,
            "steps": results,
        }
        args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nWrote JSON report to {args.output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="", help="OpenAI-compatible /v1 base URL (defaults to DS_LLM_BASE_URL)")
    parser.add_argument("--model", default="", help="Model ID (defaults to first model from /v1/models)")
    parser.add_argument("--api-key-env", default="DS_LLM_API_KEY", help="Env var holding the API key")
    parser.add_argument(
        "--disable-thinking",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Send thinking.type=disabled in extra_body (auto-enabled for DeepSeek)",
    )
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
