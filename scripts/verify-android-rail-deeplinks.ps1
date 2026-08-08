[CmdletBinding()]
param(
    [string]$DeviceSerial,
    [ValidateSet("KORAIL", "SRT")]
    [string]$Provider = "KORAIL",
    [ValidateSet("all", "booking", "ticket", "main")]
    [string]$Destination = "all",
    [switch]$Launch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$KorailPackage = "com.korail.talk"
$SrtPackage = "kr.co.srail.newapp"
$KorailRoutes = @(
    [pscustomobject]@{
        Id = "booking"
        Name = "예매"
        Uri = "korailtalk://navigation?view=booking"
        StringExtras = @{}
    },
    [pscustomobject]@{
        Id = "ticket"
        Name = "예약 승차권 조회 · 취소"
        Uri = "korailtalk://navigation?view=bookedTicket"
        StringExtras = @{}
    }
)
$SrtRoutes = @(
    [pscustomobject]@{
        Id = "main"
        Name = "예매 홈"
        Uri = "srapp://main"
        StringExtras = @{}
    },
    [pscustomobject]@{
        Id = "ticket"
        Name = "승차권 확인"
        Uri = "srapp://main"
        StringExtras = @{ btnNo = "2" }
    }
)

if ($Provider -eq "KORAIL" -and $Destination -eq "main") {
    throw "KORAIL은 -Destination booking, ticket 또는 all을 사용하세요."
}
if ($Provider -eq "SRT" -and $Destination -notin @("all", "main", "ticket")) {
    throw "SRT는 -Destination main, ticket 또는 all을 사용하세요."
}
if ($Launch -and $Destination -eq "all") {
    throw "실화면 확인 시간이 필요하므로 -Launch에는 목적지 하나를 지정하세요."
}

$SelectedRoutes = if ($Provider -eq "SRT") {
    if ($Destination -eq "all") { $SrtRoutes } else { @($SrtRoutes | Where-Object { $_.Id -eq $Destination }) }
} elseif ($Destination -eq "all") {
    $KorailRoutes
} else {
    @($KorailRoutes | Where-Object { $_.Id -eq $Destination })
}
$SelectedPackage = if ($Provider -eq "SRT") { $SrtPackage } else { $KorailPackage }
$ProviderLabel = if ($Provider -eq "SRT") { "SRT" } else { "코레일+" }

function Resolve-AdbPath {
    $candidates = @()
    if ($env:ANDROID_SDK_ROOT) {
        $candidates += Join-Path $env:ANDROID_SDK_ROOT "platform-tools\adb.exe"
    }
    if ($env:ANDROID_HOME) {
        $candidates += Join-Path $env:ANDROID_HOME "platform-tools\adb.exe"
    }
    if ($env:LOCALAPPDATA) {
        $candidates += Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
    }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $command = Get-Command adb -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    throw "adb를 찾을 수 없습니다. Android SDK platform-tools를 설치하세요."
}

function Invoke-Adb {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments,
        [switch]$AllowFailure
    )

    $deviceArguments = if ($script:SelectedDevice) {
        @("-s", $script:SelectedDevice)
    } else {
        @()
    }
    $output = @(& $script:AdbPath @deviceArguments @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "adb 명령이 실패했습니다 ($exitCode): $($output -join [Environment]::NewLine)"
    }
    return [pscustomobject]@{
        ExitCode = $exitCode
        Lines = @($output | ForEach-Object { "$_" })
    }
}

function Test-PackageInstalled {
    param([Parameter(Mandatory)][string]$PackageName)

    $result = Invoke-Adb -Arguments @("shell", "pm", "path", $PackageName) -AllowFailure
    return $result.ExitCode -eq 0 -and ($result.Lines -match "^package:").Count -gt 0
}

function Get-PackageVersion {
    param([Parameter(Mandatory)][string]$PackageName)

    $result = Invoke-Adb -Arguments @("shell", "dumpsys", "package", $PackageName) -AllowFailure
    $versionName = $result.Lines | Where-Object { $_ -match "versionName=" } | Select-Object -First 1
    $versionCode = $result.Lines | Where-Object { $_ -match "versionCode=" } | Select-Object -First 1
    return @($versionName, $versionCode) | Where-Object { $_ } | ForEach-Object { $_.Trim() }
}

$script:AdbPath = Resolve-AdbPath
$script:SelectedDevice = ""

$devicesResult = Invoke-Adb -Arguments @("devices")
$connectedDevices = @(
    $devicesResult.Lines |
        Where-Object { $_ -match "^(\S+)\s+device$" } |
        ForEach-Object { [regex]::Match($_, "^(\S+)").Groups[1].Value }
)

if ($DeviceSerial) {
    if ($connectedDevices -notcontains $DeviceSerial) {
        throw "연결된 device 상태 목록에서 '$DeviceSerial'을 찾지 못했습니다: $($connectedDevices -join ', ')"
    }
    $script:SelectedDevice = $DeviceSerial
} elseif ($connectedDevices.Count -eq 1) {
    $script:SelectedDevice = $connectedDevices[0]
} elseif ($connectedDevices.Count -eq 0) {
    throw "ADB device가 없습니다. USB 디버깅을 허용하거나 에뮬레이터를 실행하세요."
} else {
    throw "ADB device가 여러 대입니다. -DeviceSerial 값으로 하나를 선택하세요: $($connectedDevices -join ', ')"
}

Write-Host "검증 기기: $script:SelectedDevice"
Write-Host "ADB: $script:AdbPath"

$korailInstalled = Test-PackageInstalled -PackageName $KorailPackage
$srtInstalled = Test-PackageInstalled -PackageName $SrtPackage
Write-Host "코레일+ ($KorailPackage): $(if ($korailInstalled) { '설치됨' } else { '미설치' })"
if ($korailInstalled) {
    Get-PackageVersion -PackageName $KorailPackage | ForEach-Object { Write-Host "  $_" }
}
Write-Host "SRT ($SrtPackage): $(if ($srtInstalled) { '설치됨' } else { '미설치' })"
if ($srtInstalled) {
    Get-PackageVersion -PackageName $SrtPackage | ForEach-Object { Write-Host "  $_" }
}
Write-Host "SRT 2.0.41은 srapp://main과 고정 문자열 extra btnNo=2 승차권 경로를 검증합니다."

if (-not (Test-PackageInstalled -PackageName $SelectedPackage)) {
    throw "$ProviderLabel 앱이 설치되지 않아 선택한 BROWSABLE 경로를 검증할 수 없습니다."
}

$allResolved = $true
foreach ($route in $SelectedRoutes) {
    $query = Invoke-Adb -Arguments @(
        "shell", "cmd", "package", "query-activities",
        "--brief", "--components",
        "-a", "android.intent.action.VIEW",
        "-c", "android.intent.category.BROWSABLE",
        "-d", $route.Uri,
        "-p", $SelectedPackage
    ) -AllowFailure
    $resolvedComponents = @(
        $query.Lines | Where-Object {
            $_ -and $_ -notmatch "No activities found" -and $_ -match "^$([regex]::Escape($SelectedPackage))/"
        }
    )
    $resolved = $query.ExitCode -eq 0 -and $resolvedComponents.Count -gt 0
    Write-Host "$ProviderLabel $($route.Name) BROWSABLE: $(if ($resolved) { '해결됨' } else { '해결 안 됨' })"
    if ($resolved) {
        $resolvedComponents | ForEach-Object { Write-Host "  $_" }
    } else {
        $allResolved = $false
        $query.Lines | ForEach-Object { Write-Host "  $_" }
    }

    if ($Launch -and $resolved) {
        Write-Host "  실화면 실행: $($route.Uri)"
        $launchArguments = @(
            "shell", "am", "start", "-W",
            "-a", "android.intent.action.VIEW",
            "-c", "android.intent.category.BROWSABLE",
            "-p", $SelectedPackage,
            "-d", $route.Uri
        )
        foreach ($extra in $route.StringExtras.GetEnumerator()) {
            $launchArguments += @("--es", $extra.Key, $extra.Value)
        }
        $launchResult = Invoke-Adb -Arguments $launchArguments -AllowFailure
        $launchResult.Lines | ForEach-Object { Write-Host "    $_" }
        $hasOkStatus = ($launchResult.Lines -match "Status:\s+ok").Count -gt 0
        $launchSucceeded = $launchResult.ExitCode -eq 0 -and $hasOkStatus
        if (-not $launchSucceeded) {
            $allResolved = $false
        }
    }
}

if (-not $allResolved) {
    throw "$ProviderLabel 앱의 선택한 외부 진입 검증이 통과하지 않았습니다. 기능 플래그를 켜지 마세요."
}

Write-Host "선택한 경로의 Android resolver 검증이 통과했습니다."
if ($Launch) {
    Write-Host "화면이 실제 목적지인지와 뒤로가기를 사람이 확인하세요. 코레일+ ticket은 '예약 승차권 조회 · 취소' 제목과 예약목록을 확인해야 합니다."
} elseif ($Provider -eq "SRT") {
    Write-Host "다음 단계: -Provider SRT -Destination main -Launch와 -Destination ticket -Launch를 따로 실행해 목적 화면을 확인하세요."
} else {
    Write-Host "다음 단계: -Destination booking -Launch와 -Destination ticket -Launch를 따로 실행해 실제 도착 화면을 확인하세요."
}
