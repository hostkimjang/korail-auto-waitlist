[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('config', 'build', 'up', 'down', 'status', 'logs', 'drain-status', 'migrate', 'configure-browser', 'experimental', 'monitoring', 'ntfy', 'backup', 'restore', 'verify', 'verify-api', 'verify-browser', 'verify-web')]
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
        & uv run --python 3.12 --frozen --extra test --extra browser pytest
        if ($LASTEXITCODE -ne 0) {
            throw "API pytest failed with exit code $LASTEXITCODE"
        }
        & uvx --from ruff==0.12.12 ruff check --select E,F,I .
        if ($LASTEXITCODE -ne 0) {
            throw "API Ruff check failed with exit code $LASTEXITCODE"
        }
        & uv run --python 3.12 --frozen --extra test python scripts/check_ruff_format_ratchet.py
        if ($LASTEXITCODE -ne 0) {
            throw "API Ruff format ratchet failed with exit code $LASTEXITCODE"
        }
        & uv run --python 3.12 --frozen --extra test --extra browser mypy
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

function Get-RunningComposeServices {
    $runningServices = @(& docker @compose --profile experimental-rail ps --status running --services)
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose ps failed with exit code $LASTEXITCODE"
    }
    return $runningServices
}

function Show-CeleryActiveTaskSummary {
    $runningServices = @(Get-RunningComposeServices)
    $probeService = @('worker', 'experimental-rail', 'notification-worker', 'maintenance-worker') |
        Where-Object { $runningServices -contains $_ } |
        Select-Object -First 1
    if ($null -eq $probeService) {
        Write-Host '실행 중인 Celery worker가 없습니다.'
        return
    }

    $rawSnapshot = @(
        & docker @compose --profile experimental-rail exec -T $probeService `
            celery -A rail_waitlist.worker.celery_app inspect active --json --timeout 5
    )
    if ($LASTEXITCODE -ne 0) {
        throw "Celery active-task inspection failed with exit code $LASTEXITCODE"
    }

    $snapshot = ($rawSnapshot -join "`n") | ConvertFrom-Json
    $nodes = @($snapshot.PSObject.Properties)
    if ($nodes.Count -eq 0) {
        Write-Host '응답한 Celery worker가 없습니다.'
        return
    }

    foreach ($node in $nodes) {
        $tasks = @($node.Value)
        $taskNames = @($tasks | ForEach-Object { $_.name } | Sort-Object -Unique)
        $nameSummary = if ($taskNames.Count -eq 0) { '없음' } else { $taskNames -join ', ' }
        Write-Host ("{0}: 진행 중 {1}건 ({2})" -f $node.Name, $tasks.Count, $nameSummary)
    }
}

function Stop-ServicesForGracefulRecreate {
    param(
        [string[]]$ProfileArguments = @(),
        [string[]]$RunningServices = @(),
        [switch]$IncludeExperimentalRail
    )

    if ($RunningServices.Count -eq 0) {
        $RunningServices = @(Get-RunningComposeServices)
    }
    if (-not $IncludeExperimentalRail) {
        $experimentalServices = @(
            'experimental-rail',
            'korail-browser-adapter',
            'srt-provider-adapter'
        )
        $runningExperimentalServices = @(
            $experimentalServices | Where-Object { $RunningServices -contains $_ }
        )
        if ($runningExperimentalServices.Count -gt 0) {
            throw '실험 철도 서비스가 실행 중입니다. 전체 revision을 안전하게 맞추려면 experimental 명령을 사용하세요.'
        }
    }

    $unsafeMaintenanceServices = @('backup', 'restore') |
        Where-Object { $RunningServices -contains $_ }
    if (@($unsafeMaintenanceServices).Count -gt 0) {
        throw '백업 또는 복원이 실행 중입니다. 완료를 확인한 뒤 재배포하세요.'
    }

    # 새 외부 요청과 새 주기 작업을 먼저 막고, sidecar는 worker/API drain이 끝날 때까지 유지합니다.
    $stopStages = @(
        @('proxy'),
        @('scheduler'),
        @('worker', 'experimental-rail', 'notification-worker', 'maintenance-worker'),
        @('api')
    )
    foreach ($stage in $stopStages) {
        $servicesToStop = @($stage | Where-Object { $RunningServices -contains $_ })
        if ($servicesToStop.Count -gt 0) {
            # 기존 컨테이너에 stop_grace_period가 아직 반영되지 않은 첫 배포도 5분을 보장합니다.
            Invoke-Compose @ProfileArguments stop --timeout 300 @servicesToStop
        }
    }
}

function Invoke-GracefulRecreate {
    param(
        [string[]]$ProfileArguments = @(),
        [switch]$IncludeExperimentalRail
    )

    Show-CeleryActiveTaskSummary
    $runningServices = @(Get-RunningComposeServices)
    try {
        Stop-ServicesForGracefulRecreate `
            -ProfileArguments $ProfileArguments `
            -RunningServices $runningServices `
            -IncludeExperimentalRail:$IncludeExperimentalRail
    }
    catch {
        $drainError = $_
        $drainedServiceOrder = @(
            'proxy',
            'scheduler',
            'worker',
            'experimental-rail',
            'notification-worker',
            'maintenance-worker',
            'api'
        )
        $servicesToRestore = @(
            $drainedServiceOrder | Where-Object { $runningServices -contains $_ }
        )
        if ($servicesToRestore.Count -gt 0) {
            Write-Warning '단계적 종료가 완료되지 않아 기존 컨테이너를 다시 시작합니다.'
            try {
                Invoke-Compose @ProfileArguments start @servicesToRestore
            }
            catch {
                Write-Warning ("기존 서비스 자동 복구도 실패했습니다: {0}" -f $_.Exception.Message)
            }
        }
        throw $drainError
    }
    Invoke-Compose @ProfileArguments up --detach --force-recreate
}

switch ($Command) {
    'config'       { Invoke-Compose config --quiet }
    'verify'       { Invoke-Compose config --quiet; Invoke-BrowserAdapterVerification; Invoke-ApiVerification; Invoke-WebVerification }
    'verify-api'   { Invoke-ApiVerification }
    'verify-browser' { Invoke-BrowserAdapterVerification }
    'verify-web'   { Invoke-WebVerification }
    'build'        { Invoke-Compose build }
    'up' {
        Invoke-Compose config --quiet
        Invoke-Compose build
        Invoke-GracefulRecreate
    }
    'down'         { Invoke-Compose down }
    'status'       { Invoke-Compose ps }
    'logs'         { Invoke-Compose logs -f --tail=200 }
    'drain-status' { Show-CeleryActiveTaskSummary }
    'migrate'      { Invoke-Compose run --rm migration }
    'configure-browser' { Enable-BrowserAdapterConfig }
    'experimental' {
        Enable-BrowserAdapterConfig -IncludeSrtProviderAdapter
        # profile 전체를 같은 revision으로 빌드·재생성합니다. migration은 새 image로
        # head를 적용하고, 두 sidecar와 API/worker/web/proxy가 구버전으로 남지 않습니다.
        Invoke-Compose --profile experimental-rail config --quiet
        Invoke-Compose --profile experimental-rail build
        Invoke-GracefulRecreate `
            -ProfileArguments @('--profile', 'experimental-rail') `
            -IncludeExperimentalRail
    }
    'monitoring'   { Invoke-Compose --profile monitoring up --detach prometheus grafana }
    'ntfy'         { Invoke-Compose --profile ntfy up --detach ntfy }
    'backup'       { Invoke-Compose --profile backup run --rm backup once }
    'restore' {
        if ([string]::IsNullOrWhiteSpace($BackupFile)) {
            throw '복원할 /backups/<파일>.dump.age 경로를 지정하세요.'
        }
        $maintenanceServices = @('proxy', 'api', 'worker', 'notification-worker', 'maintenance-worker', 'scheduler', 'experimental-rail')
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
