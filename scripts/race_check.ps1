# Reproduces the concurrent-schema-creation race: wipes the database, starts
# every service at the same instant, and asserts they all come up.
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
Remove-Item (Join-Path $root 'data') -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path (Join-Path $root 'data\logs') | Out-Null

$specs = @(
    @{ n = 'gateway';  a = '-m uvicorn services.gateway.main:app --host 127.0.0.1 --port 8000'; e = @{} },
    @{ n = 'worker-a'; a = '-m services.worker.main'; e = @{ WORKER_ID = 'race-a'; METRICS_PORT = '9101' } },
    @{ n = 'worker-b'; a = '-m services.worker.main'; e = @{ WORKER_ID = 'race-b'; METRICS_PORT = '9103' } },
    @{ n = 'relay';    a = '-m services.relay.main'; e = @{ METRICS_PORT = '9102' } },
    @{ n = 'lab';      a = '-m uvicorn services.lab.main:app --host 127.0.0.1 --port 8100'; e = @{} }
)

$procs = @()
foreach ($s in $specs) {
    foreach ($k in $s.e.Keys) { Set-Item -Path "Env:$k" -Value $s.e[$k] }
    $log = Join-Path $root "data\logs\$($s.n).log"
    $procs += @{ name = $s.n; p = Start-Process -FilePath $python -ArgumentList $s.a -WorkingDirectory $root -PassThru -WindowStyle Hidden -RedirectStandardError $log -RedirectStandardOutput "$log.out" }
}
Write-Host "started $($procs.Count) services simultaneously" -ForegroundColor Cyan
Start-Sleep -Seconds 5

$failures = 0
foreach ($entry in $procs) {
    if ($entry.p.HasExited) {
        Write-Host "  DIED  $($entry.name) (exit $($entry.p.ExitCode))" -ForegroundColor Red
        Get-Content (Join-Path $root "data\logs\$($entry.name).log") -Tail 6 -ErrorAction SilentlyContinue | ForEach-Object { "        $_" }
        $failures++
    }
    else {
        Write-Host "  alive $($entry.name)" -ForegroundColor Green
    }
}

# Poll rather than sleep a fixed amount: loading the model artifacts takes a
# few seconds, and much longer on a loaded machine.
foreach ($url in @('http://127.0.0.1:8000/health/ready', 'http://127.0.0.1:8100/health', 'http://127.0.0.1:9101/health', 'http://127.0.0.1:9102/health')) {
    $ok = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try { Invoke-RestMethod -Uri $url -TimeoutSec 4 | Out-Null; $ok = $true; break } catch { Start-Sleep -Seconds 1 }
    }
    if ($ok) { Write-Host "  OK    $url" -ForegroundColor Green }
    else { Write-Host "  DOWN  $url (60s)" -ForegroundColor Red; $failures++ }
}

foreach ($entry in $procs) { if (-not $entry.p.HasExited) { Stop-Process -Id $entry.p.Id -Force -ErrorAction SilentlyContinue } }

if ($failures) { Write-Host "`nrace check FAILED ($failures problems)" -ForegroundColor Red; exit 1 }
Write-Host "`nrace check passed - concurrent schema creation is safe" -ForegroundColor Green
