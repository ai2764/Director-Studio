"""Backend server entry used by the DS launcher (``python -m app.serve``).

Unlike ``uvicorn app.main:app`` this keeps a handle on the ``uvicorn.Server`` so
``POST /api/system/restart`` can stop it gracefully and exit with the restart
code the launcher understands (see ``app.core.system.control``).
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.serve")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    import uvicorn

    from .config import settings
    from .core.system import control
    from .main import app

    # The launcher may pick the port/host (e.g. DS_PORT unset, --port 18890); keep settings in
    # sync so same-origin admin checks and generated URLs use the port actually served.
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level=args.log_level,
        ws="websockets-sansio",
        # Open browser websockets/streams must not block a stop/restart request: give in-flight
        # requests a few seconds, then run the lifespan shutdown (stops ComfyUI/Ollama) anyway.
        timeout_graceful_shutdown=5,
    )
    server = uvicorn.Server(config)
    control.register_server(server)
    import os

    stop_file = os.environ.get("DS_STOP_FILE", "").strip()
    if stop_file:
        control.watch_stop_file(stop_file)
    server.run()
    if control.restart_requested():
        return control.RESTART_EXIT_CODE
    return 0 if server.started else 1


if __name__ == "__main__":
    sys.exit(main())
