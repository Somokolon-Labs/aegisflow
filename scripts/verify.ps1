# Contract check: hits every endpoint the console uses and prints status codes.
#   .\scripts\verify.ps1
param(
    [string]$Gateway = "http://127.0.0.1:8000",
    [string]$Key = "demo-key-aegisflow",
    [string]$AdminKey = "admin-key-aegisflow"
)

$checks = @(
    @{ m = "GET"; p = "/health" },
    @{ m = "GET"; p = "/health/ready" },
    @{ m = "GET"; p = "/metrics" },
    @{ m = "GET"; p = "/v1/stats" },
    @{ m = "GET"; p = "/v1/models" },
    @{ m = "GET"; p = "/v1/jobs?limit=3" },
    @{ m = "GET"; p = "/v1/dlq" },
    @{ m = "GET"; p = "/v1/chaos" },
    @{ m = "GET"; p = "/v1/lab/scenarios" },
    @{ m = "GET"; p = "/v1/lab/loadtest" },
    @{ m = "GET"; p = "/v1/lab/report" },
    @{ m = "POST"; p = "/v1/predict"; body = '{"model":"sentiment-v1","input":{"text":"delivery was quick and the fabric feels great"},"wait_ms":4000}' },
    @{ m = "POST"; p = "/v1/predict"; body = '{"model":"sentiment-v1","input":{"text":""}}'; expect = 422 },
    @{ m = "POST"; p = "/v1/predict"; body = '{"model":"nope-v9","input":{"text":"hello"}}'; expect = 422 },
    @{ m = "POST"; p = "/v1/chaos"; body = '{"target":"model","mode":"error","probability":0.5,"latency_ms":0,"ttl_s":5}'; admin = $true },
    @{ m = "DELETE"; p = "/v1/chaos"; admin = $true }
)

$failures = 0
foreach ($check in $checks) {
    $headers = @{ "X-API-Key" = if ($check.admin) { $AdminKey } else { $Key } }
    $expected = if ($check.expect) { $check.expect } else { @(200, 202) }
    try {
        $params = @{ Uri = "$Gateway$($check.p)"; Method = $check.m; Headers = $headers; TimeoutSec = 20; UseBasicParsing = $true }
        if ($check.body) { $params.Body = $check.body; $params.ContentType = "application/json" }
        $response = Invoke-WebRequest @params
        $code = [int]$response.StatusCode
    }
    catch {
        $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
    }
    $ok = $expected -contains $code
    if (-not $ok) { $failures++ }
    $colour = if ($ok) { "Green" } else { "Red" }
    Write-Host ("{0,-6} {1,-26} {2}" -f $check.m, $check.p, $code) -ForegroundColor $colour
}

Write-Host ""
if ($failures -eq 0) { Write-Host "all endpoints healthy" -ForegroundColor Green }
else { Write-Host "$failures endpoint(s) failed" -ForegroundColor Red; exit 1 }
