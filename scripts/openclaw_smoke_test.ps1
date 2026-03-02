param(
    [string]$BaseUrl = "http://127.0.0.1:18789",
    [string]$Token = $env:OPENCLAW_GATEWAY_TOKEN,
    [string]$AgentId = "main"
)

if (-not $Token) {
    Write-Error "Missing token. Pass -Token or set OPENCLAW_GATEWAY_TOKEN."
    exit 1
}

$endpoint = "$($BaseUrl.TrimEnd('/'))/v1/chat/completions"
$headers = @{
    Authorization         = "Bearer $Token"
    "x-openclaw-agent-id" = $AgentId
    "Content-Type"        = "application/json"
}

$payload = @{
    model       = "openclaw"
    user        = "smoke-test-user"
    temperature = 0.2
    max_tokens  = 200
    messages    = @(
        @{ role = "system"; content = "You are a concise assistant." },
        @{ role = "user"; content = "Say only: OPENCLAW_OK" }
    )
} | ConvertTo-Json -Depth 8

try {
    $resp = Invoke-RestMethod -Method Post -Uri $endpoint -Headers $headers -Body $payload -TimeoutSec 30
    $text = $resp.choices[0].message.content
    Write-Host "Endpoint: $endpoint"
    Write-Host "Reply: $text"
}
catch {
    Write-Error "Smoke test failed: $($_.Exception.Message)"
    exit 2
}
