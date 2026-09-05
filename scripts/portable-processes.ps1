function Stop-ProcessesByExecutablePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ExecutablePath,
        [int]$TimeoutSec = 10
    )

    $targetPath = [System.IO.Path]::GetFullPath($ExecutablePath)
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSec)
    do {
        $matches = Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -and
            [System.IO.Path]::GetFullPath($_.ExecutablePath).Equals(
                $targetPath,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        }
        foreach ($match in $matches) {
            Stop-Process -Id $match.ProcessId -Force -ErrorAction SilentlyContinue
        }
        if (-not $matches) {
            return
        }
        Start-Sleep -Milliseconds 100
    } while ([DateTimeOffset]::UtcNow -lt $deadline)

    throw "Processes launched from $targetPath did not exit within $TimeoutSec seconds"
}
