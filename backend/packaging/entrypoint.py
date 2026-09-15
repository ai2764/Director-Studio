from __future__ import annotations

import os
import sys

import uvicorn

from app.portable_comfy import PortableComfyError, prepare_portable_comfy_defaults
from app.portable_harness import (
    HarnessStartupError,
    read_portable_harness_settings,
    start_managed_harness,
)
from app.runtime_paths import runtime_paths


_MISSING = object()


def _restore_environment(name: str, previous: object) -> None:
    if previous is _MISSING:
        os.environ.pop(name, None)
    else:
        os.environ[name] = str(previous)


def main() -> None:
    handle = None
    comfy_defaults: dict[str, str] = {}
    previous_url = os.environ.get("DS_HARNESS_BASE_URL", _MISSING)
    previous_token = os.environ.get("DS_HARNESS_INTERNAL_TOKEN", _MISSING)
    try:
        comfy_defaults = prepare_portable_comfy_defaults(
            runtime_paths.install_root,
            runtime_paths.data_root,
            runtime_paths.env_file,
            os.environ,
        )
        launch = read_portable_harness_settings(runtime_paths.env_file, os.environ)
        if launch.runtime == "harness" and launch.managed:
            handle = start_managed_harness(
                runtime_paths.install_root,
                runtime_paths.data_root,
            )
            os.environ["DS_HARNESS_BASE_URL"] = handle.base_url
            os.environ["DS_HARNESS_INTERNAL_TOKEN"] = handle.token

        # Managed launch state must be present before this singleton is created.
        from app.config import settings
        from app.main import create_app

        if (
            launch.runtime == "harness"
            and not launch.managed
            and not settings.harness_internal_token.strip()
        ):
            raise HarnessStartupError(
                "configuration",
                runtime_paths.data_root / "logs" / "harness-sidecar.log",
                "external Harness token is required when management is disabled",
            )

        uvicorn.run(
            create_app(),
            host=settings.host,
            port=settings.port,
            reload=False,
        )
    finally:
        if handle is not None:
            handle.stop()
        _restore_environment("DS_HARNESS_BASE_URL", previous_url)
        _restore_environment("DS_HARNESS_INTERNAL_TOKEN", previous_token)
        for name in comfy_defaults:
            os.environ.pop(name, None)


def _report_startup_error(message: str) -> None:
    print(message, file=sys.stderr, flush=True)
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            message,
            "Director Studio setup failed",
            0x10,
        )
    except (AttributeError, OSError):
        pass


def run() -> None:
    try:
        main()
    except PortableComfyError as exc:
        _report_startup_error(f"Comfy tool setup failed: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    run()
