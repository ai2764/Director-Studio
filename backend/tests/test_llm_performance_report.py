"""Exercise the report command on measured metadata, without a live model."""
import json
from pathlib import Path
import subprocess
import sys


def test_report_compares_phases_without_inventing_unknown_tokens(tmp_path):
    before = tmp_path / "before.jsonl"
    after = tmp_path / "after.jsonl"
    before.write_text(json.dumps({"kind": "llm_call", "phase": "chat.turn", "model": "local",
        "path": "chat.turn", "elapsed_ms": 10000, "image_count": 4, "status": "completed"}) + "\n", encoding="utf-8")
    after.write_text(json.dumps({"kind": "llm_call", "phase": "chat.turn", "model": "local",
        "path": "chat.turn", "elapsed_ms": 5000, "image_count": 0, "status": "completed"}) + "\n", encoding="utf-8")
    command = Path(__file__).resolve().parents[2] / "scripts" / "llm_performance_report.py"
    result = subprocess.run([sys.executable, str(command), str(before), str(after)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "chat.turn" in result.stdout
    assert "50.0%" in result.stdout
    assert "10.00" in result.stdout
    assert "5.00" in result.stdout
    assert "unknown" in result.stdout
