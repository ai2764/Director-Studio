"""Execute launcher helpers with mocked processes; never start a real app here."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def run_ps(code):
    result = subprocess.run([POWERSHELL, "-NoProfile", "-Command", code], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_runtime_resolution_and_token_reuse(tmp_path):
    config = tmp_path / ".env"
    config.write_text('DS_DIRECTOR_AGENT_RUNTIME="harness"\n')
    path = str(tmp_path).replace("'", "''")
    helper = str(ROOT / "scripts/harness-launcher.ps1").replace("'", "''")
    output = run_ps(f"""
        $ErrorActionPreference='Stop'
        . '{helper}'
        Remove-Item Env:DS_DIRECTOR_AGENT_RUNTIME -ErrorAction SilentlyContinue
        $a = Resolve-AgentRuntime '' '{path}/.env'
        $b = Resolve-AgentRuntime 'legacy' '{path}/.env'
        $c = Resolve-AgentRuntime '' '{path}/missing.env'
        $t1 = Get-HarnessToken '{path}'
        $t2 = Get-HarnessToken '{path}'
        @{{runtime=$a; override=$b; default=$c; reused=($t1 -eq $t2); size=$t1.Length}} | ConvertTo-Json -Compress
    """)
    assert json.loads(output) == {"runtime": "harness", "override": "legacy", "default": "harness", "reused": True, "size": 64}


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_sidecar_identity_rejects_other_listener():
    helper = str(ROOT / "scripts/harness-launcher.ps1").replace("'", "''")
    output = run_ps(f"""
        $ErrorActionPreference='Stop'
        . '{helper}'
        function Invoke-RestMethod {{ return @{{ok=$true; service='unrelated'; protocol=1}} }}
        $bad = Test-HarnessIdentity 8791 'secret'
        function Invoke-RestMethod {{ return @{{ok=$true; service='director-studio-harness'; protocol=1}} }}
        $good = Test-HarnessIdentity 8791 'secret'
        @{{bad=$bad; good=$good}} | ConvertTo-Json -Compress
    """)
    assert json.loads(output) == {"bad": False, "good": True}


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_sidecar_launch_passes_only_allowlisted_environment():
    helper = str(ROOT / "scripts/harness-launcher.ps1").replace("'", "''")
    output = run_ps(f"""
        $ErrorActionPreference='Stop'
        . '{helper}'
        $Root='{str(ROOT).replace("'", "''")}'
        $RunDir='unused-test-run'
        $HarnessPort=8791
        $env:DS_HARNESS_INTERNAL_TOKEN='test-token'
        $env:DS_HARNESS_PORT='8791'
        $env:DS_LLM_API_KEY='fake-provider-secret'
        $env:DS_LLM_BASE_URL='https://invalid.example'
        $env:NODE_OPTIONS='--inspect'
        function Test-PortInUse {{ return $false }}
        function Test-Path {{ return $true }}
        function Get-NodeCmd {{ return 'node.exe' }}
        function Test-HarnessIdentity {{ return $true }}
        function Start-DetachedProcess {{
            param($FilePath, $ArgumentList, $WorkingDirectory, $LogOut, $LogErr, $PidFile, $ChildEnvironment)
            $script:captured=$ChildEnvironment
            return 1234
        }}
        Start-Harness | Out-Null
        @{{secret=$captured.ContainsKey('DS_LLM_API_KEY'); url=$captured.ContainsKey('DS_LLM_BASE_URL'); nodeOptions=$captured.ContainsKey('NODE_OPTIONS'); token=$captured.DS_HARNESS_INTERNAL_TOKEN; pid=$script:StartedHarnessProcessId}} | ConvertTo-Json -Compress
    """)
    # Write-Host emits readiness before the JSON record.
    assert json.loads(output.splitlines()[-1]) == {"secret": False, "url": False, "nodeOptions": False, "token": "test-token", "pid": 1234}


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_backend_readiness_requires_its_own_authenticated_sidecar():
    helper = str(ROOT / "scripts/harness-launcher.ps1").replace("'", "''")
    output = run_ps(f"""
        $ErrorActionPreference='Stop'
        . '{helper}'
        $BackendPort=8790
        $HarnessPort=8791
        function Invoke-RestMethod {{ return @{{runtime='harness'; harness_port=8791; sidecar_ready=$false}} }}
        $rejected=$false
        try {{ Wait-HarnessBackend }} catch {{ $rejected=$true }}
        function Invoke-RestMethod {{ return @{{runtime='harness'; harness_port=8791; sidecar_ready=$true}} }}
        Wait-HarnessBackend
        @{{rejected=$rejected; accepted=$true}} | ConvertTo-Json -Compress
    """)
    assert json.loads(output) == {"rejected": True, "accepted": True}


@pytest.mark.skipif(
    sys.platform != "win32" or not POWERSHELL or not shutil.which("node"),
    reason="Windows launcher prerequisites unavailable",
)
def test_hidden_child_has_sanitized_environment_in_real_process(tmp_path):
    probe = tmp_path / "probe.cjs"
    probe.write_text('console.log(JSON.stringify({secret:!!process.env.DS_LLM_API_KEY,port:process.env.DS_HARNESS_PORT}));')
    path = str(tmp_path).replace("'", "''")
    root = str(ROOT).replace("'", "''")
    output = run_ps(f"""
        $ErrorActionPreference='Stop'
        . '{root}/scripts/harness-launcher.ps1'
        $tokens=$null
        $parseErrors=$null
        $ast=[System.Management.Automation.Language.Parser]::ParseFile('{root}/start.ps1', [ref]$tokens, [ref]$parseErrors)
        if ($parseErrors.Count) {{ throw 'start.ps1 parse failed' }}
        $definition=$ast.Find({{param($item) $item -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $item.Name -eq 'Start-DetachedProcess'}}, $true)
        Invoke-Expression $definition.Extent.Text
        $env:DS_LLM_API_KEY='fixture-secret'
        $env:DS_HARNESS_PORT='9876'
        $childId=Start-DetachedProcess -FilePath '{shutil.which("node")}' -ArgumentList @('{path}/probe.cjs') -WorkingDirectory '{path}' -LogOut '{path}/out.log' -LogErr '{path}/err.log' -PidFile '{path}/child.pid' -ChildEnvironment (Get-HarnessEnvironment)
        Wait-Process -Id $childId -Timeout 5 -ErrorAction SilentlyContinue
        Get-Content -LiteralPath '{path}/out.log' -Raw
    """)
    assert json.loads(output) == {"secret": False, "port": "9876"}
