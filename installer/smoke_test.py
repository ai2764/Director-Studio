#!/usr/bin/env python3
"""Smoke-test an extracted installer package: start the launcher, wait for /api/health, stop it.

    python installer/smoke_test.py <package root> [--port 18790] [--timeout 240]

Uses a throw-away DS_HOME so no user config is read or written.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--port", type=int, default=18790)
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    root = args.root.resolve()
    home = Path(tempfile.mkdtemp(prefix="ds-smoke-"))
    env = {**os.environ, "DS_HOME": str(home)}
    log = home / "launcher.log"
    url = f"http://127.0.0.1:{args.port}/api/health"
    with open(log, "wb") as out:
        proc = subprocess.Popen([str(root / "director-studio"), "run", "--no-open", "--port", str(args.port)],
                                env=env, stdout=out, stderr=subprocess.STDOUT)
        body = None
        deadline = time.monotonic() + args.timeout
        try:
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    with urllib.request.urlopen(url, timeout=5) as resp:
                        if resp.status == 200:
                            body = json.loads(resp.read())
                            break
                except OSError:
                    pass
                time.sleep(2)
        finally:
            subprocess.run([str(root / "director-studio"), "stop"], env=env, timeout=60, check=False)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
    if body is None:
        sys.stdout.write(log.read_text(errors="replace")[-8000:])
        print(f"smoke test FAILED: {url} not healthy (launcher exit {proc.returncode})", file=sys.stderr)
        return 1
    print("smoke test OK:", json.dumps(body)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
