# Start the isolated H3 video-context experiment.
# Frontend 5174, backend 8792, Harness 8793. Does not stop ComfyUI or llama-swap.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'video-context-launcher.ps1')

if ($MyInvocation.InvocationName -ne '.') {
    Start-VideoContextInstance -Root (Split-Path -Parent $PSScriptRoot)
}
