"""Replay fixture inference with redundant storyboard snapshots removed.

No tool execution: this isolates an Ollama prompt-size failure from mutations.
Only loopback model inference is performed. Original recordings stay untouched.
"""
import argparse
import copy
import json
from pathlib import Path
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
parser = argparse.ArgumentParser()
parser.add_argument("request", type=Path)
parser.add_argument("--original", action="store_true", help="Reproduce the unchanged failing request through the same HTTP client")
args = parser.parse_args()
original = json.loads(args.request.read_text(encoding="utf-8"))
request = copy.deepcopy(original)
removed = 0
for message in request["messages"]:
    if args.original or message["role"] != "tool":
        continue
    payload = json.loads(message["content"])
    if "storyboard" in payload:
        removed += 1
        del payload["storyboard"]
    message["content"] = json.dumps(payload, ensure_ascii=False)
output = args.request.parent / ("original-tool-result-probe.json" if args.original else "compact-tool-result-probe.json")
if output.exists():
    raise RuntimeError("Probe output already exists; preserve the earlier result")
record = {"source": str(args.request), "original_chars": len(json.dumps(original, ensure_ascii=False)), "compact_chars": len(json.dumps(request, ensure_ascii=False)), "snapshots_removed": removed, "tools_executed": 0}
started = time.perf_counter()
with httpx.Client(timeout=180, trust_env=False) as client:
    response = client.post("http://127.0.0.1:11434/api/chat", json=request)
    record["status"] = response.status_code
    record["response"] = response.json()
record["seconds"] = round(time.perf_counter()-started, 3)
output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: v for k, v in record.items() if k != "response"}, ensure_ascii=False))
print(json.dumps({k: record["response"].get(k) for k in ("error", "prompt_eval_count", "eval_count", "done_reason", "message")}, ensure_ascii=False))
