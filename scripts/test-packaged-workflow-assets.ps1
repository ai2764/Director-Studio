param(
    [Parameter(Mandatory = $true)]
    [string]$ExecutablePath
)

$ErrorActionPreference = "Stop"
$resolvedExecutable = [System.IO.Path]::GetFullPath($ExecutablePath)
if (-not (Test-Path -LiteralPath $resolvedExecutable -PathType Leaf)) {
    throw "Packaged executable does not exist: $resolvedExecutable"
}

$archiveListing = @(
    py -m PyInstaller.utils.cliutils.archive_viewer -l $resolvedExecutable
)
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect packaged executable: $resolvedExecutable"
}
$archiveText = $archiveListing -join "`n"

$requiredWorkflowAssets = @(
    "qwen_actor_asset_workbench.api.json",
    "qwen_prop_master.api.json",
    "QwenEdit2511_MultiAngle_SceneRef.api.json",
    "ref_frame_layout.api.json",
    "h3_ref2va.api.json"
)
foreach ($filename in $requiredWorkflowAssets) {
    # archive_viewer emits Python repr strings, so each path separator is escaped.
    $expectedPath = "workflows\\$filename"
    if (-not $archiveText.Contains("'$expectedPath'")) {
        throw "Packaged executable is missing workflow at runtime path: $expectedPath"
    }
}

"Packaged workflow asset test passed."
