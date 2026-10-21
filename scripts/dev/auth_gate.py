"""Dev/test helper: an auth-requiring HTTP+WS gate in front of a ComfyUI.

Simulates a tunnel such as Cloudflare Access: every request must carry the
configured header(s) or gets 401. Reuses DS's own RemoteComfyProxy.

    python scripts/dev/auth_gate.py --listen-port 18190 --upstream http://127.0.0.1:8199 \
        --require "Authorization: Bearer s3cret"
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.core.comfy.remote_proxy import RemoteComfyProxy  # noqa: E402
from app.core.net_headers import parse_headers  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--require", action="append", default=[])
    args = parser.parse_args()
    required = parse_headers("\n".join(args.require))
    gate = RemoteComfyProxy(remote_url=args.upstream, port=args.listen_port, require_headers=required)
    await gate.start()
    print(f"auth gate on {gate.url} -> {args.upstream}, requires {sorted(required)}", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
