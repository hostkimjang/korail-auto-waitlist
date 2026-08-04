[CmdletBinding()]
param(
    [string]$EnvFile = (Join-Path (Split-Path -Parent $PSScriptRoot) '.env'),
    [switch]$IncludeSrtProviderAdapter
)

$ErrorActionPreference = 'Stop'

function Set-DotEnvValue {
    param(
        [System.Collections.Generic.List[string]]$Lines,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $pattern = '^\s*' + [regex]::Escape($Name) + '\s*='
    for ($index = 0; $index -lt $Lines.Count; $index += 1) {
        if ($Lines[$index] -match $pattern) {
            $Lines[$index] = "$Name=$Value"
            return
        }
    }
    $Lines.Add("$Name=$Value")
}

function Get-DotEnvValue {
    param(
        [System.Collections.Generic.List[string]]$Lines,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $pattern = '^\s*' + [regex]::Escape($Name) + '\s*=\s*(.*)$'
    foreach ($line in $Lines) {
        if ($line -match $pattern) {
            return $matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function New-InternalAdapterToken {
    $bytes = [byte[]]::new(48)
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw '.env가 없습니다. 먼저 .env.example을 복사하고 필수 값을 설정하세요.'
}

$lines = [System.Collections.Generic.List[string]]::new()
foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) {
    $lines.Add($line)
}

$token = Get-DotEnvValue -Lines $lines -Name 'KORAIL_BROWSER_ADAPTER_TOKEN'
if ([string]::IsNullOrWhiteSpace($token) -or $token.Length -lt 32) {
    $token = New-InternalAdapterToken
}

Set-DotEnvValue -Lines $lines -Name 'EXPERIMENTAL_RAIL_ENABLED' -Value 'true'
Set-DotEnvValue -Lines $lines -Name 'KORAIL_BROWSER_ADAPTER_ENABLED' -Value 'true'
Set-DotEnvValue -Lines $lines -Name 'KORAIL_SEAT_MONITORING_ENABLED' -Value 'true'
Set-DotEnvValue -Lines $lines -Name 'KORAIL_BROWSER_ADAPTER_TOKEN' -Value $token

if ($IncludeSrtProviderAdapter) {
    $srtToken = Get-DotEnvValue -Lines $lines -Name 'SRT_PROVIDER_ADAPTER_TOKEN'
    if ([string]::IsNullOrWhiteSpace($srtToken) -or $srtToken.Length -lt 32) {
        $srtToken = New-InternalAdapterToken
    }

    Set-DotEnvValue -Lines $lines -Name 'SRT_PROVIDER_ADAPTER_ENABLED' -Value 'true'
    Set-DotEnvValue -Lines $lines -Name 'SRT_SEAT_STATUS_ENABLED' -Value 'true'
    Set-DotEnvValue -Lines $lines -Name 'SRT_SEAT_MONITORING_ENABLED' -Value 'true'
    Set-DotEnvValue -Lines $lines -Name 'SRT_PROVIDER_ADAPTER_TOKEN' -Value $srtToken
}

$utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllLines($EnvFile, $lines, $utf8WithoutBom)

$configuredProviders = if ($IncludeSrtProviderAdapter) { 'KORAIL·SRT' } else { 'KORAIL' }
Write-Host "서버 관리형 $configuredProviders 어댑터 설정을 .env에 적용했습니다. 비밀값은 출력하지 않았습니다."
