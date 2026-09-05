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

$requiredAssets = @(
    "app\\agents\\director\\DIRECTOR_SKILL.md",
    "app\\agents\\director\\guides\\script-planning.md",
    "app\\agents\\director\\guides\\storyboard-validation.md",
    "app\\agents\\director\\guides\\reference-strategy.md",
    "app\\agents\\director\\guides\\reference-frame-generation.md",
    "app\\agents\\director\\guides\\visual-qc.md",
    "app\\agents\\director\\guides\\h3-prompt-writing.md",
    "app\\agents\\director\\guides\\video-qc.md"
)
foreach ($expectedPath in $requiredAssets) {
    if (-not $archiveText.Contains("'$expectedPath'")) {
        throw "Packaged executable is missing Director guide: $expectedPath"
    }
}

"Packaged Director guide test passed."
