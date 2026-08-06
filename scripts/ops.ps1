[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('config', 'build', 'up', 'down', 'status', 'logs', 'migrate', 'configure-browser', 'experimental', 'monitoring', 'ntfy', 'backup', 'restore', 'verify', 'verify-api', 'verify-browser', 'verify-web')]
    [string]$Command = 'status',

    [Parameter(Position = 1)]
    [string]$BackupFile
)

$ErrorActionPreference = 'Stop'
$compose = @('compose', '-f', 'compose.yml')

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & docker @compose @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code $LASTEXITCODE"
    }
}

function Invoke-WebVerification {
    Push-Location (Join-Path $PSScriptRoot '..\apps\web')
    try {
        & npm run verify
        if ($LASTEXITCODE -ne 0) {
            throw "web verification failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

function Invoke-ApiVerification {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'API verification requires uv. Install it from https://docs.astral.sh/uv/.'
    }
    Push-Location (Join-Path $PSScriptRoot '..\apps\api')
    try {
        & uv lock --check
        if ($LASTEXITCODE -ne 0) {
            throw "API lock check failed with exit code $LASTEXITCODE"
        }
        & uv run --extra test pytest
        if ($LASTEXITCODE -ne 0) {
            throw "API pytest failed with exit code $LASTEXITCODE"
        }
        & uvx --from ruff==0.12.12 ruff check .
        if ($LASTEXITCODE -ne 0) {
            throw "API Ruff check failed with exit code $LASTEXITCODE"
        }
        & uv run --frozen --extra test --extra browser mypy
        if ($LASTEXITCODE -ne 0) {
            throw "API mypy check failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

function Invoke-BrowserAdapterVerification {
    Invoke-Compose --profile test build korail-browser-adapter-test
    Invoke-Compose --profile test run --rm --no-deps korail-browser-adapter-test
}

function Enable-BrowserAdapterConfig {
    param([switch]$IncludeSrtProviderAdapter)

    if ($IncludeSrtProviderAdapter) {
        & (Join-Path $PSScriptRoot 'configure-browser-adapter.ps1') -IncludeSrtProviderAdapter
        return
    }
    & (Join-Path $PSScriptRoot 'configure-browser-adapter.ps1')
}

switch ($Command) {
    'config'       { Invoke-Compose config --quiet }
    'verify'       { Invoke-Compose config --quiet; Invoke-BrowserAdapterVerification; Invoke-ApiVerification; Invoke-WebVerification }
    'verify-api'   { Invoke-ApiVerification }
    'verify-browser' { Invoke-BrowserAdapterVerification }
    'verify-web'   { Invoke-WebVerification }
    'build'        { Invoke-Compose build }
    'up'           { Invoke-Compose up --detach --build }
    'down'         { Invoke-Compose down }
    'status'       { Invoke-Compose ps }
    'logs'         { Invoke-Compose logs -f --tail=200 }
    'migrate'      { Invoke-Compose run --rm migration }
    'configure-browser' { Enable-BrowserAdapterConfig }
    'experimental' {
        Enable-BrowserAdapterConfig -IncludeSrtProviderAdapter
        # profile 전체를 같은 revision으로 빌드·재생성합니다. migration은 새 image로
        # head를 적용하고, 두 sidecar와 API/worker/web/proxy가 구버전으로 남지 않습니다.
        Invoke-Compose --profile experimental-rail config --quiet
        Invoke-Compose --profile experimental-rail build
        Invoke-Compose --profile experimental-rail up --detach --force-recreate
    }
    'monitoring'   { Invoke-Compose --profile monitoring up --detach prometheus grafana }
    'ntfy'         { Invoke-Compose --profile ntfy up --detach ntfy }
    'backup'       { Invoke-Compose --profile backup run --rm backup once }
    'restore' {
        if ([string]::IsNullOrWhiteSpace($BackupFile)) {
            throw '복원할 /backups/<파일>.dump.age 경로를 지정하세요.'
        }
        $maintenanceServices = @('proxy', 'api', 'worker', 'notification-worker', 'scheduler', 'experimental-rail')
        $runningServices = @(& docker @compose --profile experimental-rail ps --status running --services)
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose ps failed with exit code $LASTEXITCODE"
        }
        $servicesToRestore = @($maintenanceServices | Where-Object { $runningServices -contains $_ })
        Write-Host '복원 중 외부 요청과 background write를 막기 위해 실행 중인 API/worker 계열을 정지합니다.'
        if ($servicesToRestore.Count -gt 0) {
            Invoke-Compose --profile experimental-rail stop @servicesToRestore
        }
        $restoreSucceeded = $false
        try {
            Invoke-Compose --profile restore run --rm -e RESTORE_CONFIRM=RESTORE -e "BACKUP_FILE=$BackupFile" restore
            Invoke-Compose run --rm migration
            $restoreSucceeded = $true
        }
        finally {
            if ($restoreSucceeded -and $servicesToRestore.Count -gt 0) {
                Invoke-Compose --profile experimental-rail up --detach @servicesToRestore
            }
            elseif (-not $restoreSucceeded) {
                Write-Warning '복원 또는 migration 검증이 실패해 서비스는 maintenance 상태로 유지됩니다. 원인을 해결한 뒤 수동으로 시작하세요.'
            }
        }
    }
}
