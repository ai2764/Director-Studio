# Stop only this experiment's recorded processes after command and directory checks.
# Shared ComfyUI and llama-swap stay up.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'video-context-launcher.ps1')

if ($MyInvocation.InvocationName -ne '.') {
    Stop-VideoContextInstance -Root (Split-Path -Parent $PSScriptRoot)
}
