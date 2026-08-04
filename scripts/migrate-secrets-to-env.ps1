[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$examplePath = Join-Path $root '.env.example'
$targetPath = Join-Path $root '.env'
$legacyDirectory = Join-Path $root 'secrets'

$targetExists = Test-Path -LiteralPath $targetPath
if ($targetExists -and -not $Force) {
    throw '.env가 이미 있습니다. 덮어쓰려면 -Force를 명시하세요.'
}
if (-not (Test-Path -LiteralPath $examplePath)) {
    throw '.env.example을 찾을 수 없습니다.'
}

$mapping = [ordered]@{
    POSTGRES_PASSWORD          = @{ File = 'postgres_password.txt'; Required = $true }
    SECRET_ENCRYPTION_KEY      = @{ File = 'rail_credential_key.txt'; Required = $true }
    AUTH_SESSION_SECRET        = @{ File = 'auth_session_key.txt'; Required = $true }
    TAGO_SERVICE_KEY           = @{ File = 'tago_service_key.txt'; Required = $false }
    WEBPUSH_VAPID_PRIVATE_KEY  = @{ File = 'webpush_vapid_private_key.txt'; Required = $false }
    WEBPUSH_VAPID_PUBLIC_KEY   = @{ File = 'webpush_vapid_public_key.txt'; Required = $false }
    GRAFANA_ADMIN_PASSWORD     = @{ File = 'grafana_admin_password.txt'; Required = $false }
    BACKUP_AGE_IDENTITY        = @{ File = 'backup_age_identity.txt'; Required = $false }
}

function ConvertTo-DotEnvValue {
    param([AllowEmptyString()][string]$Value)

    $normalized = $Value.TrimEnd("`r", "`n")
    if ([string]::IsNullOrEmpty($normalized)) {
        return ''
    }
    if ($normalized.Contains("`n") -or $normalized.Contains("`r")) {
        $escaped = $normalized.Replace('\', '\\').Replace('"', '\"')
        $escaped = $escaped.Replace("`r`n", '\n').Replace("`n", '\n').Replace("`r", '\n')
        return '"' + $escaped + '"'
    }
    if ($normalized.Contains("'") ) {
        throw '작은따옴표가 포함된 legacy secret은 자동 이전하지 않습니다. .env에 수동으로 안전하게 입력하세요.'
    }
    return "'$normalized'"
}

$contents = if ($targetExists) {
    [IO.File]::ReadAllText($targetPath)
}
else {
    [IO.File]::ReadAllText($examplePath)
}
foreach ($entry in $mapping.GetEnumerator()) {
    $sourcePath = Join-Path $legacyDirectory $entry.Value.File
    if (-not (Test-Path -LiteralPath $sourcePath)) {
        if (-not $entry.Value.Required) {
            continue
        }
        throw "필수 legacy secret 파일이 없습니다: $($entry.Value.File)"
    }
    $value = [IO.File]::ReadAllText($sourcePath)
    if ([string]::IsNullOrWhiteSpace($value)) {
        if (-not $entry.Value.Required) {
            continue
        }
        throw "필수 legacy secret 파일이 비어 있습니다: $($entry.Value.File)"
    }
    $encoded = ConvertTo-DotEnvValue $value
    $pattern = '(?m)^' + [regex]::Escape($entry.Key) + '=.*$'
    $replacement = $entry.Key + '=' + $encoded
    if ([regex]::IsMatch($contents, $pattern)) {
        $contents = [regex]::Replace($contents, $pattern, { param($match) $replacement }, 1)
    }
    else {
        $contents = $contents.TrimEnd("`r", "`n") + "`r`n$replacement`r`n"
    }
}

$temporaryPath = "$targetPath.tmp"
try {
    [IO.File]::WriteAllText($temporaryPath, $contents, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporaryPath -Destination $targetPath -Force
}
finally {
    if (Test-Path -LiteralPath $temporaryPath) {
        Remove-Item -LiteralPath $temporaryPath -Force
    }
}

Write-Host '.env 이전을 완료했습니다. 값은 출력하지 않았습니다.'
Write-Host 'BACKUP_AGE_RECIPIENT와 비어 있는 선택 기능 값을 확인한 뒤 ./scripts/ops.ps1 config를 실행하세요.'
