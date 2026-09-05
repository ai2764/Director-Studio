param(
    [Parameter(Mandatory = $true)]
    [string]$ExecutablePath,

    [Parameter(Mandatory = $true)]
    [string]$ZipPath
)

$ErrorActionPreference = "Stop"
$resolvedExecutable = [System.IO.Path]::GetFullPath($ExecutablePath)
$resolvedZip = [System.IO.Path]::GetFullPath($ZipPath)
if (-not (Test-Path -LiteralPath $resolvedExecutable -PathType Leaf)) {
    throw "Packaged executable does not exist: $resolvedExecutable"
}
if (-not (Test-Path -LiteralPath $resolvedZip -PathType Leaf)) {
    throw "Portable zip does not exist: $resolvedZip"
}

function Get-ExecutableArchiveText([string]$Path) {
    $listing = @(
        py -m PyInstaller.utils.cliutils.archive_viewer -l $Path
    )
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect packaged executable: $Path"
    }
    return ((($listing -join "`n").Replace("\", "/")) -replace "/+", "/")
}

function Assert-OfficialOnlyExecutable([string]$Path, [string]$Label) {
    $listing = Get-ExecutableArchiveText $Path
    if (-not $listing.Contains("workflows/h3_ref2va.api.json")) {
        throw "$Label is missing workflows/h3_ref2va.api.json"
    }
    $forbiddenPatterns = @(
        "(?m)(^|[^A-Za-z0-9_.-])workflow_profiles/h3/imports(/|$)",
        "(?m)(^|[^A-Za-z0-9_.-])workflow_profiles/h3/profiles(/|$)",
        "(?m)(^|[^A-Za-z0-9_.-])active\.json([^A-Za-z0-9_.-]|$)",
        "(?m)(^|[^A-Za-z0-9_.-])data(/|$)",
        "(?m)(^|[^A-Za-z0-9_.-])jobs(/|$)",
        "(?m)(^|[^A-Za-z0-9_.-])projects(/|$)",
        "(?m)(^|[^A-Za-z0-9_.-])tests?(/|$)",
        "(?m)(^|[^A-Za-z0-9_.-])outputs(/|$)",
        "(?m)(^|[^A-Za-z0-9_.-])test_[^/]*$",
        "(?m)(^|[^A-Za-z0-9_.-])[^/]*\.test\.[^/]*$"
    )
    foreach ($pattern in $forbiddenPatterns) {
        if ($listing -match $pattern) {
            throw "$Label contains forbidden external or test state: $($Matches[0])"
        }
    }
}

Assert-OfficialOnlyExecutable $resolvedExecutable "Built executable"

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($resolvedZip)
$inspectionRoot = $null
try {
    $entries = @($zip.Entries | ForEach-Object { $_.FullName.Replace("\", "/") })
    $embeddedExecutables = @(
        $zip.Entries | Where-Object {
            $_.FullName.Replace("\", "/") -match "(^|/)DirectorStudio\.exe$"
        }
    )
    if ($embeddedExecutables.Count -ne 1) {
        throw "Portable zip must contain exactly one DirectorStudio.exe"
    }

    $forbiddenZipPatterns = @(
        "(^|/)data(/|$)",
        "(^|/)workflow_profiles(/|$)",
        "(^|/)jobs(/|$)",
        "(^|/)projects(/|$)",
        "(^|/)outputs(/|$)",
        "(^|/)tests?(/|$)",
        "(^|/)active\.json$",
        "(^|/)test_[^/]*$",
        "(^|/)[^/]*\.test\.[^/]*$"
    )
    foreach ($entry in $entries) {
        foreach ($pattern in $forbiddenZipPatterns) {
            if ($entry -match $pattern) {
                throw "Portable zip contains forbidden external or test state: $entry"
            }
        }
    }

    $inspectionRoot = [System.IO.Path]::GetFullPath(
        (Join-Path ([System.IO.Path]::GetTempPath()) ("director-studio-h3-package-" + [guid]::NewGuid().ToString("N")))
    )
    New-Item -ItemType Directory -Path $inspectionRoot | Out-Null
    $embeddedExecutable = Join-Path $inspectionRoot "DirectorStudio.exe"
    $inputStream = $embeddedExecutables[0].Open()
    $outputStream = [System.IO.File]::Create($embeddedExecutable)
    try {
        $inputStream.CopyTo($outputStream)
    }
    finally {
        $outputStream.Dispose()
        $inputStream.Dispose()
    }

    if ((Get-FileHash -LiteralPath $embeddedExecutable -Algorithm SHA256).Hash -ne
        (Get-FileHash -LiteralPath $resolvedExecutable -Algorithm SHA256).Hash) {
        throw "Portable zip contains a different DirectorStudio.exe than the inspected build"
    }
    Assert-OfficialOnlyExecutable $embeddedExecutable "Portable zip executable"
}
finally {
    $zip.Dispose()
    if ($inspectionRoot) {
        $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        if (-not $inspectionRoot.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove inspection directory outside the temporary root: $inspectionRoot"
        }
        if (Test-Path -LiteralPath $inspectionRoot) {
            Remove-Item -LiteralPath $inspectionRoot -Recurse -Force
        }
    }
}

"Portable official H3-only packaging test passed."
