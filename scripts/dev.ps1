# AegisFlow local stack (Windows / PowerShell) - no Docker required.
#
#   .\scripts\dev.ps1 setup     # venv + dependencies + train models
#   .\scripts\dev.ps1 up        # start gateway, 2 workers, relay, lab
#   .\scripts\dev.ps1 down      # stop everything
#   .\scripts\dev.ps1 status    # health of every service
#   .\scripts\dev.ps1 smoke     # submit one inference request
#   .\scripts\dev.ps1 drill     # run a chaos drill and print the verdict
#
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'up', 'down', 'status', 'smoke', 'drill', 'reset')]
    [string]$Command = 'up',
    [string]$Scenario = 'worker-loss',
    [int]$Rps = 25,
    [int]$Duration = 24
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$gateway = 'http://127.0.0.1:8000'
$lab = 'http://127.0.0.1:8100'
$pidFile = Join-Path $root '.dev-pids.json'

function Assert-Venv {
    if (-not (Test-Path $python)) { throw "virtualenv missing - run: .\scripts\dev.ps1 setup" }
}

function Start-Service([string]$Name, [string]$Arguments, [hashtable]$EnvVars) {
    foreach ($key in $EnvVars.Keys) { Set-Item -Path "Env:$key" -Value $EnvVars[$key] }
    $process = Start-Process -FilePath $python -ArgumentList $Arguments -WorkingDirectory $root -PassThru -WindowStyle Minimized
    Write-Host ("  {0,-10} pid {1}" -f $Name, $process.Id) -ForegroundColor DarkGray
    return $process.Id
}

switch ($Command) {
    'setup' {
        if (-not (Test-Path $python)) { python -m venv (Join-Path $root '.venv') }
        & $python -m pip install --upgrade pip
        & $python -m pip install -r (Join-Path $root 'requirements.txt')
        & $python (Join-Path $root 'ml\train.py')
        if (-not (Test-Path (Join-Path $root '.env'))) {
            Copy-Item (Join-Path $root '.env.example') (Join-Path $root '.env')
            Write-Host 'created .env from .env.example' -ForegroundColor Green
        }
        Write-Host 'setup complete - next: .\scripts\dev.ps1 up' -ForegroundColor Green
    }

    'up' {
        Assert-Venv
        Write-Host 'starting AegisFlow...' -ForegroundColor Cyan
        $pids = @{}
        $pids.gateway = Start-Service 'gateway' '-m uvicorn services.gateway.main:app --host 127.0.0.1 --port 8000' @{ SERVICE_NAME = 'gateway' }
        $pids.worker_a = Start-Service 'worker-a' '-m services.worker.main' @{ WORKER_ID = 'worker-a'; METRICS_PORT = '9101' }
        $pids.worker_b = Start-Service 'worker-b' '-m services.worker.main' @{ WORKER_ID = 'worker-b'; METRICS_PORT = '9103' }
        $pids.relay = Start-Service 'relay' '-m services.relay.main' @{ METRICS_PORT = '9102' }
        $pids.lab = Start-Service 'lab' '-m uvicorn services.lab.main:app --host 127.0.0.1 --port 8100' @{ SERVICE_NAME = 'lab' }
        $pids | ConvertTo-Json | Set-Content $pidFile
        Start-Sleep -Seconds 6
        Write-Host "`ngateway  $gateway/docs" -ForegroundColor Green
        Write-Host "lab      $lab/docs" -ForegroundColor Green
        Write-Host "console  cd web; npm run dev  ->  http://localhost:3000" -ForegroundColor Green
    }

    'down' {
        if (Test-Path $pidFile) {
            $pids = Get-Content $pidFile -Raw | ConvertFrom-Json
            foreach ($name in $pids.PSObject.Properties.Name) {
                $processId = $pids.$name
                try { Stop-Process -Id $processId -Force -ErrorAction Stop; Write-Host "stopped $name ($processId)" }
                catch { Write-Host "$name already stopped" -ForegroundColor DarkGray }
            }
            Remove-Item $pidFile -Force
        }
        else { Write-Host 'no tracked processes; nothing to stop' -ForegroundColor DarkGray }
    }

    'status' {
        foreach ($url in @("$gateway/health/ready", 'http://127.0.0.1:9101/health', 'http://127.0.0.1:9103/health', 'http://127.0.0.1:9102/health', "$lab/health")) {
            try {
                $response = Invoke-RestMethod -Uri $url -TimeoutSec 4
                Write-Host ("OK   {0,-42} {1}" -f $url, ($response.status)) -ForegroundColor Green
            }
            catch { Write-Host ("DOWN {0}" -f $url) -ForegroundColor Red }
        }
    }

    'smoke' {
        $body = @{ model = 'sentiment-v1'; input = @{ text = 'the courier arrived early and the fabric feels premium' }; wait_ms = 5000 } | ConvertTo-Json -Compress
        $response = Invoke-RestMethod -Uri "$gateway/v1/predict" -Method Post -ContentType 'application/json' -Headers @{ 'X-API-Key' = 'demo-key-aegisflow' } -Body $body
        $response.job | ConvertTo-Json -Depth 5
    }

    'drill' {
        $body = @{ scenario = $Scenario; rps = $Rps; duration_s = $Duration; fault_at_s = 6; fault_duration_s = 8; concurrency = 32 } | ConvertTo-Json -Compress
        $run = Invoke-RestMethod -Uri "$lab/v1/loadtest" -Method Post -ContentType 'application/json' -Body $body
        Write-Host "run $($run.id) started ($Scenario)" -ForegroundColor Cyan
        do {
            Start-Sleep -Seconds 5
            $detail = Invoke-RestMethod -Uri "$lab/v1/loadtest/$($run.id)"
            Write-Host "  status: $($detail.status)" -ForegroundColor DarkGray
        } while ($detail.status -eq 'running')
        Write-Host "`nmetrics" -ForegroundColor Cyan
        $detail.metrics | ConvertTo-Json -Depth 4
        Write-Host 'verdict' -ForegroundColor Cyan
        $detail.verdict | ConvertTo-Json -Depth 4
    }

    'reset' {
        & $PSCommandPath down
        Remove-Item (Join-Path $root 'data') -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host 'database wiped' -ForegroundColor Yellow
    }
}
