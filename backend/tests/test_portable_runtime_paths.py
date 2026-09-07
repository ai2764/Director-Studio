from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_clean_portable_resolves_builtin_official_h3_from_bundle(tmp_path: Path):
    bundle_root = tmp_path / "bundle"
    install_root = tmp_path / "clean-extraction"
    shutil.copytree(REPO_ROOT / "backend" / "app", bundle_root / "app")
    (bundle_root / "workflows").mkdir()
    shutil.copy2(
        REPO_ROOT / "backend" / "workflows" / "h3_ref2va.api.json",
        bundle_root / "workflows" / "h3_ref2va.api.json",
    )
    install_root.mkdir()
    assert not (install_root / "data").exists()

    probe = f"""
import json
import sys
sys.path.insert(0, {str(bundle_root)!r})
sys.frozen = True
sys._MEIPASS = {str(bundle_root)!r}
sys.executable = {str(install_root / "DirectorStudio.exe")!r}
from app.runtime_paths import runtime_paths
from app.config import settings
from app.workflow_profiles.h3.store import H3ProfileStore
from app.workflow_profiles.h3.models import H3WorkflowProfile
resolved = H3ProfileStore().resolve_active()
metadata = H3WorkflowProfile(
    id=resolved.profile_id,
    workflow_sha256=resolved.workflow_sha256,
    mapping=resolved.mapping,
    status='active',
)
print(json.dumps({{
    'bundle_root': str(runtime_paths.bundle_root),
    'data_root': str(runtime_paths.data_root),
    'workflows_dir': str(settings.workflows_dir),
    'profile_id': resolved.profile_id,
    'source': resolved.source,
    'contract_version': metadata.contract_version,
    'h3_node_id': resolved.mapping.inputs.h3_node_id,
    'output_node_id': resolved.mapping.output.node_id,
}}))
"""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("DS_") and key != "PYTHONPATH"
    }
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "bundle_root": str(bundle_root),
        "data_root": str(install_root / "data"),
        "workflows_dir": str(bundle_root / "workflows"),
        "profile_id": "builtin-official-h3",
        "source": "builtin",
        "contract_version": 2,
        "h3_node_id": "136",
        "output_node_id": "92",
    }
    assert not (
        install_root / "data" / "workflow_profiles" / "h3" / "active.json"
    ).exists()
