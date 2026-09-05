$ErrorActionPreference = "Stop"
$helperPath = Join-Path $PSScriptRoot "portable-processes.ps1"
. $helperPath

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("director-studio-process-test-" + [Guid]::NewGuid().ToString("N"))
$workerPath = Join-Path $testRoot "PackagedWorker.exe"
$worker = $null
try {
    New-Item -ItemType Directory -Path $testRoot | Out-Null
    Copy-Item -LiteralPath $env:ComSpec -Destination $workerPath
    $worker = Start-Process -FilePath $workerPath -ArgumentList "/d", "/c", "ping 127.0.0.1 -n 30 > nul" -PassThru

    Stop-ProcessesByExecutablePath -ExecutablePath $workerPath

    $remaining = Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and
        [System.IO.Path]::GetFullPath($_.ExecutablePath).Equals(
            [System.IO.Path]::GetFullPath($workerPath),
            [System.StringComparison]::OrdinalIgnoreCase
        )
    }
    if ($remaining) {
        throw "Process cleanup left a process running from $workerPath"
    }
    "Portable process cleanup test passed."
}
finally {
    if ($null -ne $worker -and -not $worker.HasExited) {
        Stop-Process -Id $worker.Id -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $testRoot) {
        $resolvedTestRoot = [System.IO.Path]::GetFullPath($testRoot)
        $resolvedTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        if (-not $resolvedTestRoot.StartsWith($resolvedTemp, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove test directory outside the system temp directory: $resolvedTestRoot"
        }
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}
