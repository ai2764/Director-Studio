"""Compare metadata-only logs from equivalent runs: python scripts/llm_performance_report.py before.jsonl after.jsonl."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import mean


def read_groups(path):
    groups = defaultdict(list)
    skipped = 0
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        try:
            row = json.loads(line)
            if row.get("kind") not in {"llm_call", "image_context", "phase"}:
                continue
            if not isinstance(row.get("elapsed_ms"), (int, float)):
                raise ValueError("Missing timing")
            groups[(row.get("model", "-"), row["phase"], row["path"])].append(row)
        except (ValueError, TypeError, KeyError, AttributeError):
            skipped += 1
    return groups, skipped


def token_sum(rows, key):
    values = [row.get(key) for row in rows]
    return str(sum(values)) if values and all(isinstance(v, int) for v in values) else "unknown"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", type=Path, nargs="+", help="One log for a report, two logs for before/after comparison")
    args = parser.parse_args()
    if len(args.logs) > 2:
        parser.error("Supply one or two logs")
    datasets = []
    for path in args.logs:
        groups, skipped = read_groups(path)
        datasets.append(groups)
        print(f"\n{path.name} (skipped malformed records: {skipped})")
        print("model | phase | request path | samples | total s | mean s | images | reused | input tokens | output tokens | failed")
        for (model, phase, request_path), rows in sorted(groups.items()):
            elapsed = [row["elapsed_ms"] / 1000 for row in rows]
            print(f"{model} | {phase} | {request_path} | {len(rows)} | {sum(elapsed):.2f} | {mean(elapsed):.2f} | "
                  f"{sum(row.get('image_count', 0) for row in rows)} | {sum(row.get('reused_image_count', 0) for row in rows)} | "
                  f"{token_sum(rows, 'input_tokens')} | {token_sum(rows, 'output_tokens')} | "
                  f"{sum(row.get('status') not in {None, 'completed'} for row in rows)}")
    if len(datasets) == 2:
        print("\nComparison: positive reduction means less total time. Compare the same workload, model and warm/cold state.")
        for key in sorted(datasets[0].keys() | datasets[1].keys()):
            before, after = datasets[0].get(key, []), datasets[1].get(key, [])
            if not before or not after:
                print(f"{' / '.join(key)}: no matching records in one run; reduction unknown")
                continue
            old, new = sum(r["elapsed_ms"] for r in before), sum(r["elapsed_ms"] for r in after)
            delta = f"{(old - new) / old * 100:.1f}%" if old else "unknown"
            print(f"{' / '.join(key)}: {old / 1000:.2f}s -> {new / 1000:.2f}s; reduction {delta}; calls {len(before)} -> {len(after)}")
    print("\nTimings include adapter/preflight/network wait; they are not isolated GPU prefill or decode times.")
    print("Overlapping calls must not be summed as wall-clock turn time. Unknown token counts are never estimated.")


if __name__ == "__main__":
    main()
