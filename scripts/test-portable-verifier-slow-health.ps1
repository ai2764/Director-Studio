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
                bool isRuntime = requestLine.Contains(" /api/director/runtime?check_sidecar=true ");
                if (isHealth)
                {
                    Thread.Sleep(2500);
                }
                string body = isHealth
                    ? "{\"ok\":true,\"comfy_reachable\":false}"
                    : isRuntime
                        ? "{\"runtime\":\"harness\",\"sidecar_ready\":true}"
                        : "<div id=\"root\"></div>";
                byte[] payload = Encoding.UTF8.GetBytes(body);
                string response =
                    "HTTP/1.1 200 OK\r\n" +
                    "Content-Type: " + (isHealth || isRuntime ? "application/json" : "text/html") + "\r\n" +
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
    New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $packageRoot ".env") -Value "# test"
    Copy-Item `
        -LiteralPath (Join-Path $PSScriptRoot "../packaging/windows-portable-readme.md") `
        -Destination (Join-Path $packageRoot "README.md")
    $nodeRoot = Join-Path $packageRoot "runtime/node"
    $harnessRoot = Join-Path $packageRoot "harness"
    $koffiRoot = Join-Path $harnessRoot "node_modules/@koromix/koffi-win32-x64/win32_x64"
    $dependencyTests = Join-Path $harnessRoot "node_modules/example-runtime/src/tests"
    New-Item -ItemType Directory -Path $nodeRoot, (Join-Path $harnessRoot "dist"), $koffiRoot, $dependencyTests -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $nodeRoot "node.exe") -Value "fixture"
    Set-Content -LiteralPath (Join-Path $nodeRoot "LICENSE") -Value "fixture"
    Set-Content -LiteralPath (Join-Path $harnessRoot "dist/server.js") -Value "fixture"
    Set-Content -LiteralPath (Join-Path $koffiRoot "koffi.node") -Value "fixture"
    Set-Content -LiteralPath (Join-Path $dependencyTests "fixture.js") -Value "fixture"
    Set-Content -LiteralPath (Join-Path $harnessRoot "THIRD_PARTY_LICENSES.json") -Value "[]"
    @{name="director-studio-harness-sidecar"; version="0.1.0"} |
        ConvertTo-Json -Compress |
        Set-Content -LiteralPath (Join-Path $harnessRoot "package.json")
    $pythonFiles = @(
        "runtime/python/python.exe",
        "runtime/python/python3.dll",
        "runtime/python/python313.dll",
        "runtime/python/python313.zip",
        "runtime/python/python313._pth",
        "runtime/python/comfy.exe",
        "runtime/python/Lib/site-packages/pip/__init__.py",
        "runtime/python/Lib/site-packages/pip-25.1.1.dist-info/METADATA",
        "runtime/python/Lib/site-packages/pip-25.1.1.dist-info/licenses/LICENSE.txt",
        "runtime/python/Lib/site-packages/sitecustomize.py",
        "runtime/comfy-bootstrap.json",
        "runtime/comfy-requirements.lock",
        "THIRD_PARTY_LICENSES/python.txt",
        "THIRD_PARTY_LICENSES/pip.txt"
    )
    foreach ($relative in $pythonFiles) {
        $path = Join-Path $packageRoot $relative
        New-Item -ItemType Directory -Path (Split-Path $path -Parent) -Force | Out-Null
        Set-Content -LiteralPath $path -Value "fixture"
    }
    $lockHash = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot "../harness/package-lock.json") -Algorithm SHA256).Hash.ToLowerInvariant()
    [ordered]@{
        entrypoint="harness/dist/server.js"
        format=1
        harness=[ordered]@{package_lock_sha256=$lockHash; version="0.1.0"}
        node=[ordered]@{archive_sha256="1177b4137ba5adaa56354ae40f1080c7450e8ae09cecb47da459d1c52ac99f97"; version="22.23.2"}
        python=[ordered]@{archive_sha256="90b4e5b9898b72d744650524bff92377c367f44bd5fbd09e3148656c080ad907"; version="3.13.14"}
        comfy_bootstrap=[ordered]@{
            packages=[ordered]@{"comfy-cli"="1.20.0"; "comfy-mcp"="0.10.0"}
            pip_version="25.1.1"
            pip_wheel_sha256="2913a38a2abf4ea6b64ab507bd9e967f3b53dc1ede74b01b0931e1ce548751af"
            requirements_lock_sha256=(Get-FileHash -LiteralPath (Join-Path $PSScriptRoot "../packaging/windows-comfy-requirements.lock") -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        platform="win-x64"
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $packageRoot "portable-manifest.json")
    $sourcePath = Join-Path $testRoot "SlowHealthServer.cs"
    Set-Content -LiteralPath $sourcePath -Value $source
    $compiler = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
    & $compiler /nologo /target:exe "/out:$exePath" $sourcePath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to compile the slow-health test server"
    }

    & (Join-Path $PSScriptRoot "verify-windows-portable.ps1") `
        -PackageRoot $packageRoot `
        -Port $port `
        -StartupTimeoutSec 8 `
        -SkipOfflineHarnessCheck `
        -SkipOfflineComfyCheck
    if ($LASTEXITCODE -ne 0) {
        throw "Portable verifier rejected a healthy endpoint that responds in 2.5 seconds"
    }

    Set-Content `
        -LiteralPath (Join-Path $packageRoot ".env") `
        -Value "DS_H3_MINIMAX_API_KEY=do-not-ship"
    $secretRejected = $false
    try {
        & (Join-Path $PSScriptRoot "verify-windows-portable.ps1") `
            -PackageRoot $packageRoot `
            -Port $port `
            -StartupTimeoutSec 8 `
            -SkipOfflineHarnessCheck `
            -SkipOfflineComfyCheck
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
        & (Join-Path $PSScriptRoot "verify-windows-portable.ps1") `
            -PackageRoot $packageRoot `
            -Port $port `
            -StartupTimeoutSec 8 `
            -SkipOfflineHarnessCheck `
            -SkipOfflineComfyCheck
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
