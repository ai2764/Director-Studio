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

function Normalize-ArchivePath([string]$Path) {
    return ($Path.Replace("\", "/") -replace "/+", "/")
}

function Get-NormalizedArchiveEntries([string[]]$Lines) {
    return @(
        foreach ($line in $Lines) {
            $normalizedLine = (Normalize-ArchivePath $line).Trim()
            if ($normalizedLine -match "'(?<path>[^']+)'\s*$") {
                $Matches["path"]
            }
            elseif ($normalizedLine -match "^[^,\s]+$") {
                $normalizedLine.Trim("'", '"')
            }
        }
    )
}

$archiveEntries = Get-NormalizedArchiveEntries $archiveListing

function Assert-ArchiveContains([string]$Path) {
    $normalizedPath = Normalize-ArchivePath $Path
    if ($archiveEntries -notcontains $normalizedPath) {
        throw "Packaged executable is missing workflow at runtime path: $normalizedPath"
    }
}

function Assert-ArchiveDoesNotContain([string]$Path) {
    $normalizedPath = Normalize-ArchivePath $Path
    $normalizedArchive = Normalize-ArchivePath $archiveText
    if ($normalizedArchive.Contains($normalizedPath)) {
        throw "Packaged executable must not contain external runtime state: $normalizedPath"
    }
}

$requiredWorkflowAssets = @(
    "qwen_actor_asset_workbench.api.json",
    "qwen_prop_master.api.json",
    "QwenEdit2511_MultiAngle_SceneRef.api.json",
    "ref_frame_layout.api.json",
    "h3_ref2va.api.json"
)
foreach ($filename in $requiredWorkflowAssets) {
    Assert-ArchiveContains "workflows/$filename"
}

Assert-ArchiveContains "workflows/h3_ref2va.api.json"
Assert-ArchiveDoesNotContain "workflow_profiles/h3/imports"
Assert-ArchiveDoesNotContain "workflow_profiles/h3/profiles"
Assert-ArchiveDoesNotContain "active.json"

"Packaged workflow asset test passed."
