"""Dev e2e: switch DS to remote ComfyUI mode, run a tiny prompt through DS's
loopback proxy over HTTP + WebSocket, drive the same proxy with comfy-mcp
(validate_workflow / run_workflow / job wait / fetch_outputs, exactly what DS jobs
use), then restore the previous mode.  Requires a running DS backend.

    # behind an auth gate (scripts/dev/auth_gate.py), bearer token
    python scripts/dev/e2e_remote_check.py --ds http://127.0.0.1:8890 \
        --remote http://127.0.0.1:18190 --auth bearer --token s3cret
    # plain http on a non-loopback IP (LAN / Tailscale-style), no auth
    python scripts/dev/e2e_remote_check.py --remote http://192.0.2.10:8199 --auth none

scripts/dev/remote_comfy_e2e.sh runs both scenarios with temporary services.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import uuid

import httpx
from websockets.asyncio.client import connect

ADMIN = {"X-DS-Admin": "1"}


async def put(client: httpx.AsyncClient, ds: str, changes: dict) -> dict:
    cfg = (await client.get(f"{ds}/api/config", headers=ADMIN)).json()
    response = await client.put(f"{ds}/api/config", headers=ADMIN,
                                json={"revision": cfg["revision"], "changes": changes})
    response.raise_for_status()
    return response.json()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ds", default="http://127.0.0.1:8890")
    parser.add_argument("--remote", required=True)
    parser.add_argument("--auth", choices=("none", "bearer"), default="bearer")
    parser.add_argument("--token", default="")
    parser.add_argument("--restore-mode", default="managed")
    parser.add_argument("--skip-mcp", action="store_true")
    args = parser.parse_args()
    changes: dict = {"DS_COMFY_MODE": "remote", "DS_COMFY_REMOTE_URL": args.remote,
                     "DS_COMFY_REMOTE_AUTH_TYPE": args.auth}
    if args.auth == "bearer":
        changes["DS_COMFY_REMOTE_AUTH"] = {"secret": "set", "value": args.token}
    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
        try:
            await _scenario(client, args, changes)
        finally:
            print("PUT restore:", await put(client, args.ds, {"DS_COMFY_MODE": args.restore_mode}))


async def _scenario(client: httpx.AsyncClient, args: argparse.Namespace, changes: dict) -> None:
    print("PUT remote:", await put(client, args.ds, changes))
    runtime = (await client.get(f"{args.ds}/api/comfy/runtime", headers=ADMIN)).json()
    proxy = runtime["proxy"]["url"]
    print("proxy:", proxy, "->", runtime["proxy"]["remote"])
    health = (await client.get(f"{args.ds}/api/health")).json()
    print("DS /api/health comfy_reachable:", health["comfy_reachable"], health["details"]["comfy_runtime"])
    blocked = await client.get(f"{proxy}/manager/queue/status")
    print("GET /manager/... via proxy ->", blocked.status_code)
    cfg = (await client.get(f"{args.ds}/api/config", headers=ADMIN)).json()
    secret = next(f for f in cfg["fields"] if f["key"] == "DS_COMFY_REMOTE_AUTH")
    print("secret in GET /api/config:", secret["secret"],
          "| token leaked:", bool(args.token) and args.token in json.dumps(cfg))

    client_id = uuid.uuid4().hex
    ws_url = proxy.replace("http://", "ws://") + f"/ws?clientId={client_id}"
    text_msgs = binary_msgs = 0
    async with connect(ws_url) as ws:
        prompt = {
            "1": {"class_type": "EmptyImage", "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 255}},
            "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "ds_proxy_e2e"}},
        }
        queued = await client.post(f"{proxy}/prompt", json={"prompt": prompt, "client_id": client_id})
        prompt_id = queued.json()["prompt_id"]
        print("queued via proxy:", queued.status_code, prompt_id)
        while True:
            message = await asyncio.wait_for(ws.recv(), 60)
            if isinstance(message, bytes):
                binary_msgs += 1
                continue
            text_msgs += 1
            data = json.loads(message)
            if data.get("type") in {"execution_success", "execution_error"} and data["data"].get("prompt_id") == prompt_id:
                print("ws event:", data["type"])
                break
    history = (await client.get(f"{proxy}/history/{prompt_id}")).json()
    images = history[prompt_id]["outputs"]["2"]["images"]
    view = await client.get(f"{proxy}/view", params={"filename": images[0]["filename"], "type": "output"})
    print(f"ws text={text_msgs} binary={binary_msgs}; /view -> {view.status_code} {len(view.content)} bytes")
    assert view.status_code == 200 and view.content[:8] == b"\x89PNG\r\n\x1a\n"
    if not args.skip_mcp:
        fields = {f["key"]: f.get("value") for f in cfg["fields"]}
        await _mcp_roundtrip(proxy, str(fields.get("DS_COMFY_MCP_COMMAND") or "comfy-mcp"),
                             str(fields.get("DS_COMFY_MCP_COMFY_BIN") or "comfy"), prompt)
    print("E2E OK")


async def _mcp_roundtrip(proxy: str, command: str, comfy_bin: str, prompt: dict) -> None:
    """Run the prompt with DS's own comfy-mcp client against DS's loopback proxy."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from app.config import settings
    from app.integrations.comfy_mcp import ComfyMcpClient

    settings.comfy_base_url = proxy
    settings.comfy_mcp_command = command
    settings.comfy_mcp_comfy_bin = comfy_bin
    mcp = ComfyMcpClient(job_timeout_sec=120)
    try:
        info = await mcp.call_tool("server_info", {})
        print("comfy-mcp server_info via proxy:", {k: info.get(k) for k in list(info)[:4]} if isinstance(info, dict) else info)
        graph = json.loads(json.dumps(prompt))
        graph["2"]["inputs"]["filename_prefix"] = "ds_mcp_e2e"
        prompt_id = await mcp.submit_workflow(graph)
        status = await mcp.wait_for_completion(prompt_id, cancel_event=asyncio.Event())
        outputs = await mcp.fetch_outputs(prompt_id)
        print(f"comfy-mcp run_workflow {prompt_id}: {status.get('status')} -> "
              f"{[(o.filename, len(o.data)) for o in outputs]}")
        assert outputs and outputs[0].data[:4] == b"\x89PNG"
    finally:
        await mcp.aclose()


if __name__ == "__main__":
    asyncio.run(main())
