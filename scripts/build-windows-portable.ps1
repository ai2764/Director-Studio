param(
    [int]$VerificationPort = 18790,
    [string]$NodeArchive = "",
    [string]$PythonArchive = "",
    [string]$PipWheel = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$frontendRoot = Join-Path $repoRoot "frontend"
$backendRoot = Join-Path $repoRoot "backend"
$buildRoot = Join-Path $repoRoot "build"
$pyinstallerRoot = Join-Path $buildRoot "pyinstaller"
$pyinstallerDist = Join-Path $buildRoot "pyinstaller-dist"
$distRoot = Join-Path $repoRoot "dist"
$packageName = "Director-Studio-Windows-x64"
$packageRoot = Join-Path $distRoot $packageName
$zipPath = Join-Path $distRoot "$packageName.zip"

function Remove-GeneratedDirectory([string]$Path) {
    $resolvedParent = [System.IO.Path]::GetFullPath((Split-Path $Path -Parent))
    if (-not $resolvedParent.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a directory outside the repository: $Path"
    }
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

function Clear-GeneratedDirectory([string]$Path) {
    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $resolvedPath.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clear a directory outside the repository: $Path"
    }
    if (Test-Path -LiteralPath $resolvedPath) {
        Get-ChildItem -LiteralPath $resolvedPath -Force | Remove-Item -Recurse -Force
    }
    else {
        New-Item -ItemType Directory -Path $resolvedPath -Force | Out-Null
    }
}

Push-Location $frontendRoot
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
    npm test -- --run
    if ($LASTEXITCODE -ne 0) { throw "frontend tests failed" }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
}
finally {
    Pop-Location
}

Push-Location $backendRoot
try {
    py -m pytest `
        tests/test_packaged_runtime.py `
        tests/test_projects_api.py `
        tests/test_portable_tools_installer.py `
        tests/test_director_model_runtime.py `
        tests/test_llm_provider.py `
        tests/test_packaged_runtime_paths.py `
        tests/test_portable_runtime_paths.py `
        tests/test_stage_windows_harness.py `
        tests/test_stage_windows_comfy.py `
        tests/test_portable_comfy.py `
        tests/test_verify_bundled_harness.py `
        tests/test_verify_bundled_comfy.py `
        -q
    if ($LASTEXITCODE -ne 0) { throw "backend packaging tests failed" }
    py -m PyInstaller --version
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller is unavailable; install backend/requirements-build.txt"
    }
}
finally {
    Pop-Location
}

pwsh -NoProfile -File (Join-Path $PSScriptRoot "test-portable-process-cleanup.ps1")
if ($LASTEXITCODE -ne 0) { throw "portable process cleanup test failed" }

pwsh -NoProfile -File (Join-Path $PSScriptRoot "test-portable-verifier-slow-health.ps1")
if ($LASTEXITCODE -ne 0) { throw "portable verifier slow-health test failed" }

Remove-GeneratedDirectory $buildRoot
Clear-GeneratedDirectory $packageRoot
if (Test-Path -LiteralPath $zipPath) {
    $resolvedZip = [System.IO.Path]::GetFullPath($zipPath)
    if (-not $resolvedZip.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove an archive outside the repository: $zipPath"
    }
    Remove-Item -LiteralPath $resolvedZip -Force
}
New-Item -ItemType Directory -Path $pyinstallerRoot -Force | Out-Null
New-Item -ItemType Directory -Path $pyinstallerDist -Force | Out-Null
New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null

$specPath = Join-Path $backendRoot "packaging/director-studio-legacy.spec"
py -m PyInstaller `
    --clean `
    --noconfirm `
    --workpath $pyinstallerRoot `
    --distpath $pyinstallerDist `
    $specPath
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

$builtExe = Join-Path $pyinstallerDist "DirectorStudio.exe"
if (-not (Test-Path -LiteralPath $builtExe -PathType Leaf)) {
    throw "PyInstaller did not produce DirectorStudio.exe"
}

pwsh -NoProfile -File (Join-Path $PSScriptRoot "test-packaged-workflow-assets.ps1") `
    -ExecutablePath $builtExe
if ($LASTEXITCODE -ne 0) { throw "packaged workflow asset test failed" }
pwsh -NoProfile -File (Join-Path $PSScriptRoot "test-packaged-director-guides.ps1") `
    -ExecutablePath $builtExe
if ($LASTEXITCODE -ne 0) { throw "packaged Director guide test failed" }

Copy-Item -LiteralPath $builtExe -Destination (Join-Path $packageRoot "DirectorStudio.exe")
Copy-Item -LiteralPath (Join-Path $backendRoot ".env.example") -Destination (Join-Path $packageRoot ".env")
Copy-Item -LiteralPath (Join-Path $repoRoot "packaging/windows-portable-readme.md") -Destination (Join-Path $packageRoot "README.md")

$stageArguments = @(
    (Join-Path $PSScriptRoot "stage_windows_harness.py"),
    "--repo-root", $repoRoot,
    "--destination", $packageRoot
)
if ($NodeArchive) {
    $stageArguments += @("--node-archive", $NodeArchive)
}
& py @stageArguments
if ($LASTEXITCODE -ne 0) { throw "Harness runtime staging failed" }

$comfyStageArguments = @(
    (Join-Path $PSScriptRoot "stage_windows_comfy.py"),
    "--repo-root", $repoRoot,
    "--destination", $packageRoot
)
if ($PythonArchive) {
    $comfyStageArguments += @("--python-archive", $PythonArchive)
}
if ($PipWheel) {
    $comfyStageArguments += @("--pip-wheel", $PipWheel)
}
& py @comfyStageArguments
if ($LASTEXITCODE -ne 0) { throw "Comfy MCP runtime staging failed" }

py (Join-Path $PSScriptRoot "verify_bundled_comfy.py") `
    --package-root $packageRoot
if ($LASTEXITCODE -ne 0) { throw "Comfy first-launch bootstrap verification failed" }

pwsh -NoProfile -File (Join-Path $PSScriptRoot "verify-windows-portable.ps1") `
    -PackageRoot $packageRoot `
    -Port $VerificationPort
if ($LASTEXITCODE -ne 0) { throw "Portable package verification failed" }

tar.exe -a -c -f $zipPath -C $distRoot $packageName
if ($LASTEXITCODE -ne 0) { throw "Portable zip creation failed" }

py (Join-Path $PSScriptRoot "verify_portable_contents.py") `
    --platform windows `
    --package-root $packageRoot `
    --executable $builtExe `
    --archive $zipPath
if ($LASTEXITCODE -ne 0) { throw "Cross-platform package policy verification failed" }

$archiveEntries = @(tar.exe -tf $zipPath)
if ($LASTEXITCODE -ne 0) { throw "Portable zip listing failed" }
$requiredArchiveEntries = @(
    "$packageName/DirectorStudio.exe",
    "$packageName/.env",
    "$packageName/README.md",
    "$packageName/runtime/node/node.exe",
    "$packageName/runtime/node/LICENSE",
    "$packageName/harness/dist/server.js",
    "$packageName/harness/package.json",
    "$packageName/harness/THIRD_PARTY_LICENSES.json",
    "$packageName/harness/node_modules/@koromix/koffi-win32-x64/win32_x64/koffi.node",
    "$packageName/portable-manifest.json",
    "$packageName/runtime/python/python.exe",
    "$packageName/runtime/python/python3.dll",
    "$packageName/runtime/python/python313.dll",
    "$packageName/runtime/python/python313.zip",
    "$packageName/runtime/python/python313._pth",
    "$packageName/runtime/python/comfy.exe",
    "$packageName/runtime/python/Lib/site-packages/pip/__init__.py",
    "$packageName/runtime/python/Lib/site-packages/pip-25.1.1.dist-info/METADATA",
    "$packageName/runtime/python/Lib/site-packages/pip-25.1.1.dist-info/licenses/LICENSE.txt",
    "$packageName/runtime/python/Lib/site-packages/sitecustomize.py",
    "$packageName/runtime/comfy-bootstrap.json",
    "$packageName/runtime/comfy-requirements.lock",
    "$packageName/THIRD_PARTY_LICENSES/python.txt",
    "$packageName/THIRD_PARTY_LICENSES/pip.txt"
)
foreach ($requiredEntry in $requiredArchiveEntries) {
    if ($archiveEntries -notcontains $requiredEntry) {
        throw "Portable zip is missing required entry: $requiredEntry"
    }
}

pwsh -NoProfile -File (Join-Path $PSScriptRoot "test-packaged-h3-profiles.ps1") `
    -ExecutablePath $builtExe `
    -ZipPath $zipPath
if ($LASTEXITCODE -ne 0) { throw "packaged H3 profile isolation test failed" }

$hash = Get-FileHash -LiteralPath $zipPath -Algorithm SHA256
$size = (Get-Item -LiteralPath $zipPath).Length

[ordered]@{
    package_root = $packageRoot
    zip = $zipPath
    sha256 = $hash.Hash
    bytes = $size
} | ConvertTo-Json
