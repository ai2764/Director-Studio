$ErrorActionPreference = "Stop"

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "director-studio-verifier-test-" + [Guid]::NewGuid().ToString("N")
)
$packageRoot = Join-Path $testRoot "package"
$exePath = Join-Path $packageRoot "DirectorStudio.exe"
$listener = [System.Net.Sockets.TcpListener]::new(
    [System.Net.IPAddress]::Loopback,
    0
)
$listener.Start()
$port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()

$source = @'
using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

public static class SlowHealthServer
{
    public static void Main()
    {
        int port = int.Parse(Environment.GetEnvironmentVariable("DS_PORT"));
        TcpListener listener = new TcpListener(IPAddress.Loopback, port);
        listener.Start();
        while (true)
        {
            using (TcpClient client = listener.AcceptTcpClient())
            using (NetworkStream stream = client.GetStream())
            {
                StreamReader reader = new StreamReader(
                stream,
                Encoding.ASCII,
                false,
                1024
                );
                string requestLine = reader.ReadLine() ?? "";
                string header;
                while (!string.IsNullOrEmpty(header = reader.ReadLine())) { }

                bool isHealth = requestLine.Contains(" /api/health ");
                if (isHealth)
                {
                    Thread.Sleep(2500);
                }
                string body = isHealth
                    ? "{\"ok\":true,\"comfy_reachable\":false}"
                    : "<div id=\"root\"></div>";
                byte[] payload = Encoding.UTF8.GetBytes(body);
                string response =
                    "HTTP/1.1 200 OK\r\n" +
                    "Content-Type: " + (isHealth ? "application/json" : "text/html") + "\r\n" +
                    "Content-Length: " + payload.Length + "\r\n" +
                    "Connection: close\r\n\r\n";
                byte[] headers = Encoding.ASCII.GetBytes(response);
                try
                {
                    stream.Write(headers, 0, headers.Length);
                    stream.Write(payload, 0, payload.Length);
                }
                catch (IOException)
                {
                    // The old verifier cancels each request before this response.
                }
            }
        }
    }
}
'@

try {
    New-Item -ItemType Directory -Path (Join-Path $packageRoot "data") -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $packageRoot ".env") -Value "# test"
    Set-Content -LiteralPath (Join-Path $packageRoot "README.md") -Value "test package"
    Set-Content -LiteralPath (Join-Path $packageRoot "Install-Tools.cmd") -Value "@echo off"
    Set-Content -LiteralPath (Join-Path $packageRoot "Install-Tools.py") -Value "# test"
    Set-Content `
        -LiteralPath (Join-Path $packageRoot "portable-tools-requirements.txt") `
        -Value "# test"
    $sourcePath = Join-Path $testRoot "SlowHealthServer.cs"
    Set-Content -LiteralPath $sourcePath -Value $source
    $compiler = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
    & $compiler /nologo /target:exe "/out:$exePath" $sourcePath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to compile the slow-health test server"
    }

    & (Join-Path $PSScriptRoot "verify-legacy-portable.ps1") `
        -PackageRoot $packageRoot `
        -Port $port `
        -StartupTimeoutSec 8
    if ($LASTEXITCODE -ne 0) {
        throw "Portable verifier rejected a healthy endpoint that responds in 2.5 seconds"
    }

    Set-Content `
        -LiteralPath (Join-Path $packageRoot ".env") `
        -Value "DS_H3_MINIMAX_API_KEY=do-not-ship"
    $secretRejected = $false
    try {
        & (Join-Path $PSScriptRoot "verify-legacy-portable.ps1") `
            -PackageRoot $packageRoot `
            -Port $port `
            -StartupTimeoutSec 8
    }
    catch {
        if ($_.Exception.Message -match "secret") {
            $secretRejected = $true
        }
        else {
            throw
        }
    }
    if (-not $secretRejected) {
        throw "Portable verifier accepted an active secret in the packaged .env"
    }

    Set-Content `
        -LiteralPath (Join-Path $packageRoot ".env") `
        -Value "# DS_GPT_BRIDGE_BASE_URL=http://127.0.0.1:8080"
    $bridgeConfigRejected = $false
    try {
        & (Join-Path $PSScriptRoot "verify-legacy-portable.ps1") `
            -PackageRoot $packageRoot `
            -Port $port `
            -StartupTimeoutSec 8
    }
    catch {
        if ($_.Exception.Message -match "GPT Bridge") {
            $bridgeConfigRejected = $true
        }
        else {
            throw
        }
    }
    if (-not $bridgeConfigRejected) {
        throw "Portable verifier accepted GPT Bridge configuration in the packaged .env"
    }
    "Portable verifier slow-health test passed."
}
finally {
    if (Test-Path -LiteralPath $testRoot) {
        $resolvedTestRoot = [System.IO.Path]::GetFullPath($testRoot)
        $resolvedTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        if (-not $resolvedTestRoot.StartsWith(
            $resolvedTemp,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Refusing to remove test directory outside system temp: $resolvedTestRoot"
        }
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}
