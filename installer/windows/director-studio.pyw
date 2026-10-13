"""Director Studio Windows entry point (installed package).

Shortcuts run ``runtime\\python\\pythonw.exe director-studio.pyw`` so no console
window appears; ``DirectorStudio.cmd`` runs the same file with python.exe for a
console with live logs.

    director-studio.pyw [run] [--no-open] | stop | status

The launcher itself (``backend/app/launcher.py``) contains its children in a
kill-on-close Job Object, starts them with CREATE_NO_WINDOW and opens the
browser outside the job.  Config + data live in %LOCALAPPDATA%\\DirectorStudio
(or DS_HOME) and survive upgrades and uninstall.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def data_home(environ=os.environ) -> Path:
    if environ.get("DS_HOME"):
        return Path(environ["DS_HOME"]).expanduser()
    base = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "DirectorStudio"


def launcher_argv(argv: list[str]) -> list[str]:
    """Map the shortcut-friendly CLI to ``python -m app.launcher`` arguments."""
    command = "run"
    rest: list[str] = []
    open_browser = True
    for arg in argv:
        if arg in ("run", "stop", "status"):
            command = arg
        elif arg == "--no-open":
            open_browser = False
        else:
            rest.append(arg)
    if command == "run" and open_browser and "--open" not in rest:
        rest.insert(0, "--open")
    return [command, *rest]


def _redirect_missing_streams(home: Path) -> None:
    """pythonw has no stdout/stderr; keep launcher output in <data>/logs/launcher.log."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "launcher.log"
    try:
        if log_path.stat().st_size > 5 * 1024 * 1024:
            os.replace(log_path, log_path.with_suffix(".log.1"))
    except OSError:
        pass
    stream = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream


def prepare_environment(root: Path = ROOT, environ=os.environ) -> None:
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        environ.pop(key, None)
    environ["PYTHONNOUSERSITE"] = "1"
    backend = root / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    os.chdir(backend)


def main(argv: list[str] | None = None) -> int:
    home = data_home()
    _redirect_missing_streams(home)
    if not (ROOT / "ds-install.json").is_file():
        print(f"Director Studio: {ROOT / 'ds-install.json'} missing; reinstall the application.", file=sys.stderr)
        return 1
    prepare_environment()
    from app.launcher import main as launcher_main

    return launcher_main(launcher_argv(list(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
    sys.exit(main())
