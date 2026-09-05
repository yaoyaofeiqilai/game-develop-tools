param(
    [int]$Port = 7865,
    [string]$Proxy = "",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Project Python environment was not found: $pythonPath"
}

if ($Proxy) {
    $env:HTTP_PROXY = $Proxy
    $env:HTTPS_PROXY = $Proxy
}

$address = "http://127.0.0.1:$Port"
$healthAddress = "$address/api/health"
$serverReady = $false
try {
    $health = Invoke-RestMethod -Uri $healthAddress -TimeoutSec 1
    $serverReady = $health.status -eq "ok"
} catch {
    $serverReady = $false
}

if (-not $serverReady) {
    $server = Start-Process -FilePath $pythonPath `
        -ArgumentList @("-m", "uvicorn", "gui_server:app", "--host", "127.0.0.1", "--port", $Port, "--app-dir", $projectRoot) `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -PassThru
    $pidPath = Join-Path $projectRoot "workspace\gui-server.pid"
    Set-Content -LiteralPath $pidPath -Value $server.Id -Encoding ascii
    for ($attempt = 0; $attempt -lt 50; $attempt++) {
        Start-Sleep -Milliseconds 200
        try {
            $health = Invoke-RestMethod -Uri $healthAddress -TimeoutSec 1
            if ($health.status -eq "ok") {
                $serverReady = $true
                break
            }
        } catch {
            $serverReady = $false
        }
    }
}

if (-not $serverReady) {
    throw "GUI server failed to start. Run .\.venv\Scripts\python.exe -m uvicorn gui_server:app --port $Port to inspect the error."
}

if (-not $NoBrowser) {
    Start-Process $address
}
Write-Host "Sprite Intelligence Workbench is ready: $address"
