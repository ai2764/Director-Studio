import json
from pathlib import Path
import subprocess
import sys


def test_architectural_benchmark_compares_isolated_prepared_shots(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts" / "director_execution_benchmark.py"
    output = tmp_path / "benchmark.json"
    result = subprocess.run([sys.executable, str(script), "--trials", "1", "--output", str(output)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["inference"] == "simulated"
    assert data["conditions"]["baseline"]["warm"]["text_calls_median"] == 2
    assert data["conditions"]["focused"]["warm"]["text_calls_median"] == 1
    assert data["conditions"]["focused"]["warm"]["input_chars_median"] < data["conditions"]["baseline"]["warm"]["input_chars_median"]
    assert data["live_latency_verified"] is False
    assert data["creative_quality_verified"] is False
