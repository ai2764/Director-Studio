"""Launcher identity for the isolated video-context instance. Starts no servers."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.api.health import router as health_router
from app.config import Settings

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
LAUNCHER = ROOT / "scripts" / "video-context-launcher.ps1"


def run_ps(code: str) -> str:
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def ps_path(path: Path | str) -> str:
    return str(path).replace("'", "''")


class _Comfy:
    async def health(self):
        return {"system": {"comfyui_version": "test"}}


class _Llm:
    async def health(self):
        return True


class _Provider:
    provider_id = "test"
    client = _Llm()


def _client():
    app = FastAPI()
    api = APIRouter(prefix="/api")
    api.include_router(health_router)
    app.include_router(api)
    return TestClient(app)


@pytest.fixture
def quiet_health(monkeypatch):
    monkeypatch.setattr("app.api.health.ComfyClient", lambda: _Comfy())
    monkeypatch.setattr("app.api.health.get_llm_provider", lambda: _Provider())


def test_video_context_stays_off_unless_configured(monkeypatch):
    monkeypatch.delenv("DS_VIDEO_CONTEXT_ENABLED", raising=False)
    assert Settings(_env_file=None).video_context_enabled is False


def test_default_health_omits_experiment_identity(quiet_health, monkeypatch):
    monkeypatch.setattr("app.api.health.settings.video_context_enabled", False)
    body = _client().get("/api/health").json()
    assert body["ok"] is True
    assert "instance" not in body
    assert "capabilities" not in body
    assert "comfy_reachable" in body


def test_enabled_health_names_the_experiment_without_secrets(quiet_health, monkeypatch):
    monkeypatch.setattr("app.api.health.settings.video_context_enabled", True)
    body = _client().get("/api/health").json()
    assert body["instance"] == "video-context"
    assert body["capabilities"] == {"video_context": True}
    assert set(body["capabilities"]) == {"video_context"}


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_layout_uses_separate_dirs_and_tokens(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    output = run_ps(f"""
        $ErrorActionPreference='Stop'
        . '{ps_path(ROOT / 'scripts/harness-launcher.ps1')}'
        . '{ps_path(LAUNCHER)}'
        $a = Get-VideoContextLayout '{ps_path(first)}'
        $b = Get-VideoContextLayout '{ps_path(second)}'
        New-Item -ItemType Directory -Force -Path $a.RunDir, $b.RunDir | Out-Null
        $t1 = Get-HarnessToken $a.RunDir
        $t2 = Get-HarnessToken $b.RunDir
        $dirs = @($a.DataDir, $a.JobsDir, $a.ProjectsDir, $a.LibraryRoot, $a.LibraryDir, $a.ProfilesDir)
        @{{
            unique=@($dirs | Select-Object -Unique).Count
            dataDistinct=$a.DataDir -ne $a.JobsDir -and $a.JobsDir -ne $a.ProjectsDir
            tokenDistinct=$t1 -ne $t2
            tokenBytes=$t1.Length
            tokenLivesInRun=(Test-Path (Join-Path $a.RunDir 'harness.token'))
            ports=@($a.BackendPort, $a.FrontendPort, $a.HarnessPort)
        }} | ConvertTo-Json -Compress
    """)
    parsed = json.loads(output.splitlines()[-1])
    assert parsed["unique"] == 6
    assert parsed["dataDistinct"] is True
    assert parsed["tokenDistinct"] is True
    assert parsed["tokenBytes"] == 64
    assert parsed["tokenLivesInRun"] is True
    assert parsed["ports"] == [8792, 5174, 8793]


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_foreign_listener_is_rejected_and_owned_listener_is_reused():
    output = run_ps(f"""
        $ErrorActionPreference='Stop'
        . '{ps_path(LAUNCHER)}'
        $root = 'C:\\video-context-instance'
        function Get-ListenerProcessId {{ param($Port) return 4242 }}
        function Get-ProcessIdentityText {{ param($ProcessId) return 'C:\\other\\python.exe -m uvicorn --port 8792' }}
        $rejected = $false
        try {{ Assert-VideoContextPort -Port 8792 -Root $root -Role backend }} catch {{ $rejected = $true }}
        function Get-ProcessIdentityText {{ param($ProcessId) return ($root + '\\backend uvicorn --port 8792') }}
        $owned = Assert-VideoContextPort -Port 8792 -Root $root -Role backend
        @{{rejected=$rejected; owned=$owned}} | ConvertTo-Json -Compress
    """)
    assert json.loads(output.splitlines()[-1]) == {"rejected": True, "owned": "reuse"}


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_stop_refuses_reused_pid_and_stops_owned_pid(tmp_path):
    pid_file = tmp_path / "backend.pid"
    pid_file.write_text("4242", encoding="ascii")
    output = run_ps(f"""
        $ErrorActionPreference='Stop'
        . '{ps_path(LAUNCHER)}'
        $root = '{ps_path(tmp_path)}'
        $script:stopped = $false
        function Stop-ProcessTree {{ param($ProcessId) $script:stopped = $true }}
        function Get-ProcessIdentityText {{ param($ProcessId) return 'C:\\Windows\\System32\\notepad.exe' }}
        $refused = $false
        try {{ Stop-RecordedProcess -PidFile '{ps_path(pid_file)}' -Root $root -Role backend }} catch {{ $refused = $true }}
        $stillThere = Test-Path -LiteralPath '{ps_path(pid_file)}'
        function Get-ProcessIdentityText {{ param($ProcessId) return ($root + '\\backend uvicorn --port 8792') }}
        $result = Stop-RecordedProcess -PidFile '{ps_path(pid_file)}' -Root $root -Role backend
        @{{refused=$refused; stillThere=$stillThere; stoppedEarly=$script:stopped; result=$result; removed=(-not (Test-Path -LiteralPath '{ps_path(pid_file)}'))}} | ConvertTo-Json -Compress
    """)
    parsed = json.loads(output.splitlines()[-1])
    assert parsed["refused"] is True
    assert parsed["stillThere"] is True
    assert parsed["result"] == "stopped"
    assert parsed["removed"] is True


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_start_sets_env_before_the_shared_launcher(tmp_path):
    output = run_ps(f"""
        $ErrorActionPreference='Stop'
        . '{ps_path(LAUNCHER)}'
        Remove-Item Env:DS_VIDEO_CONTEXT_ENABLED -ErrorAction SilentlyContinue
        function Assert-VideoContextPort {{ param($Port, $Root, $Role) return 'free' }}
        function Invoke-DirectorStudioStart {{
            param($Root, $Layout)
            $script:seen = @{{
                enabled=$env:DS_VIDEO_CONTEXT_ENABLED
                url=$env:DS_BACKEND_URL
                backend=$Layout.BackendPort
                frontend=$Layout.FrontendPort
                harness=$Layout.HarnessPort
                run=$Layout.RunDir
                data=$env:DS_DATA_DIR
                jobs=$env:DS_JOBS_DIR
                projects=$env:DS_PROJECTS_DIR
                library=$env:DS_LIBRARY_ROOT
                actors=$env:DS_LIBRARY_DIR
                profiles=$env:DS_WORKFLOW_PROFILES_DIR
                directorSkill=$env:DS_DIRECTOR_SKILL_PATH
            }}
        }}
        Start-VideoContextInstance -Root '{ps_path(tmp_path)}' | Out-Null
        $seen = $script:seen
        $paths = @($seen.data, $seen.jobs, $seen.projects, $seen.library, $seen.actors, $seen.profiles)
        $seen.unique = @($paths | Select-Object -Unique).Count
        $seen | ConvertTo-Json -Compress
    """)
    parsed = json.loads(output.splitlines()[-1])
    assert parsed["enabled"] == "true"
    assert parsed["url"] == "http://127.0.0.1:8792"
    assert [parsed["backend"], parsed["frontend"], parsed["harness"]] == [8792, 5174, 8793]
    assert parsed["unique"] == 6
    assert Path(parsed["directorSkill"]) == tmp_path / "backend/app/agents/director/DIRECTOR_SKILL.md"
    assert str(parsed["run"]).endswith(".run\\video-context") or str(parsed["run"]).endswith(".run/video-context")


def test_stop_script_leaves_shared_gpu_services_alone():
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "scripts" / "stop-video-context.ps1",
            ROOT / "scripts" / "video-context-launcher.ps1",
        )
    )
    assert "kill.ps1" not in text
    assert "8188" not in text
    assert "11435" not in text
    assert "start-llama-swap.ps1" not in text
    assert "llama-swap.pid" not in text


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_experiment_launcher_disables_development_reload(tmp_path):
    (tmp_path / "start.ps1").write_text('''param([int]$BackendPort,[int]$FrontendPort,[int]$HarnessPort,[string]$RunDir,[switch]$NoReload)
@{no_reload=[bool]$NoReload;backend=$BackendPort} | ConvertTo-Json -Compress
''', encoding="utf8")
    result = json.loads(run_ps(f". '{ps_path(LAUNCHER)}'; Invoke-DirectorStudioStart -Root '{ps_path(tmp_path)}' -Layout (Get-VideoContextLayout '{ps_path(tmp_path)}')"))
    assert result["no_reload"] is True
    assert result["backend"] == 8792
