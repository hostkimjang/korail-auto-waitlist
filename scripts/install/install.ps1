<#
.SYNOPSIS
Installs the Railwait installer CLI from the official GitHub release and starts "railwait install".

.DESCRIPTION
Usage (Windows PowerShell 5.1 or PowerShell 7):

    irm https://github.com/hostkimjang/korail-auto-waitlist/releases/latest/download/install.ps1 | iex

The script downloads railwait-windows-<arch>.exe and railwait-windows-<arch>.sha256, checks the
SHA-256 value, installs the verified executable to %LOCALAPPDATA%\Programs\Railwait\railwait.exe
(kept apart from the app folder %LOCALAPPDATA%\Railwait) and runs "railwait install" when the
console is interactive. Otherwise it prints the command to run next. It never edits PATH.
Docker preparation, release signature checks and administrator input stay in "railwait install".

Optional environment variables:

    RAILWAIT_VERSION        Pins only the installer CLI executable (for example 1.4.4).
                            "railwait install" always installs the latest stable app release.
    RAILWAIT_INSTALL_ARGS   Arguments for "railwait install", for example: --custom --port 9090
                            Wrap a value that contains spaces in double or single quotes.
    RAILWAIT_BIN_DIR        Absolute folder for railwait.exe. Default: %LOCALAPPDATA%\Programs\Railwait
    RAILWAIT_DOWNLOAD_BASE  Asset folder URL for tests: https, or http on 127.0.0.1/localhost only.
                            Takes precedence over RAILWAIT_VERSION.

Example with arguments:

    $env:RAILWAIT_INSTALL_ARGS = '--custom --port 9090'
    irm https://github.com/hostkimjang/korail-auto-waitlist/releases/latest/download/install.ps1 | iex

.NOTES
Keep this file pure ASCII. "irm | iex" may decode it as ISO-8859-1 on Windows PowerShell 5.1, so
Korean messages are stored as \uXXXX escapes and restored at run time with [regex]::Unescape.
Everything lives inside Install-Railwait, which is called once on the last line, so a partial
download cannot run any step. Errors are thrown instead of calling exit, which would close the
user's PowerShell window when the script runs through iex.
#>

function Install-Railwait {
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '', Justification = 'Messages are for the interactive console, not the pipeline.')]
    param()

    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    Set-StrictMode -Version 2.0

    $repositoryUrl = 'https://github.com/hostkimjang/korail-auto-waitlist'
    $messages = @{
        NotWindows          = 'install.ps1\uC740 Windows \uC804\uC6A9\uC785\uB2C8\uB2E4. macOS\u00B7Linux\uC5D0\uC11C\uB294 \uD130\uBBF8\uB110\uC5D0\uC11C \uB2E4\uC74C \uBA85\uB839\uC744 \uC2E4\uD589\uD558\uC138\uC694: curl -fsSL {0}/releases/latest/download/install.sh | sh'
        UnsupportedArch     = '\uC9C0\uC6D0\uD558\uC9C0 \uC54A\uB294 CPU\uC785\uB2C8\uB2E4({0}). Windows\uB294 AMD64(x64)\uC640 ARM64\uB9CC \uC9C0\uC6D0\uD569\uB2C8\uB2E4.'
        InvalidDownloadBase = 'RAILWAIT_DOWNLOAD_BASE\uB294 https \uC8FC\uC18C\uC774\uAC70\uB098 127.0.0.1\u00B7localhost\uC758 http \uC8FC\uC18C\uC5EC\uC57C \uD569\uB2C8\uB2E4.'
        InvalidVersion      = 'RAILWAIT_VERSION \uD615\uC2DD\uC774 \uC62C\uBC14\uB974\uC9C0 \uC54A\uC2B5\uB2C8\uB2E4(\uC608: 1.4.4).'
        NoLocalAppData      = 'LOCALAPPDATA \uD658\uACBD\uBCC0\uC218\uB97C \uD655\uC778\uD558\uC9C0 \uBABB\uD588\uC2B5\uB2C8\uB2E4.'
        BinDirNotAbsolute   = 'RAILWAIT_BIN_DIR\uB294 \uC808\uB300\uACBD\uB85C\uC5EC\uC57C \uD569\uB2C8\uB2E4.'
        BinDirInsideApp     = '\uC124\uCE58 CLI\uB97C \uC571 \uC124\uCE58 \uD3F4\uB354({0}) \uC548\uC5D0 \uB458 \uC218 \uC5C6\uC2B5\uB2C8\uB2E4. RAILWAIT_BIN_DIR\uB97C \uBC14\uAFD4 \uC8FC\uC138\uC694.'
        InvalidInstallArgs  = 'RAILWAIT_INSTALL_ARGS\uC758 \uB530\uC634\uD45C \uC9DD\uC774 \uB9DE\uC9C0 \uC54A\uC2B5\uB2C8\uB2E4.'
        TargetIsDirectory   = '{0} \uACBD\uB85C\uAC00 \uD3F4\uB354\uB77C\uC11C \uC124\uCE58 CLI\uB85C \uAD50\uCCB4\uD560 \uC218 \uC5C6\uC2B5\uB2C8\uB2E4.'
        BaseOverride        = '\uB2E4\uC6B4\uB85C\uB4DC \uC704\uCE58\uB97C RAILWAIT_DOWNLOAD_BASE\uB85C \uC9C0\uC815\uD588\uC2B5\uB2C8\uB2E4: {0}'
        VersionPinned       = '\uC124\uCE58 CLI \uBC84\uC804\uC744 {0}(\uC73C)\uB85C \uACE0\uC815\uD588\uC2B5\uB2C8\uB2E4. \uC571\uC740 \uD56D\uC0C1 \uCD5C\uC2E0 stable \uB9B4\uB9AC\uC2A4\uB85C \uC124\uCE58\uD569\uB2C8\uB2E4.'
        Downloading         = '{0} \uC124\uCE58 CLI\uB97C \uB0B4\uB824\uBC1B\uB294 \uC911\uC785\uB2C8\uB2E4.'
        DownloadFailed      = '{0} \uD30C\uC77C\uC744 \uB0B4\uB824\uBC1B\uC9C0 \uBABB\uD588\uC2B5\uB2C8\uB2E4: {1}'
        ChecksumEntry       = '{0} \uD30C\uC77C\uC5D0\uC11C {1} \uD56D\uBAA9\uC744 \uC815\uD655\uD788 \uD558\uB098 \uCC3E\uC9C0 \uBABB\uD588\uC2B5\uB2C8\uB2E4.'
        ChecksumFormat      = '{0} \uD30C\uC77C\uC758 \uD574\uC2DC \uD615\uC2DD\uC774 \uC62C\uBC14\uB974\uC9C0 \uC54A\uC2B5\uB2C8\uB2E4.'
        HashMismatch        = 'SHA-256\uC774 \uC77C\uCE58\uD558\uC9C0 \uC54A\uC544 \uC124\uCE58\uB97C \uC911\uB2E8\uD588\uC2B5\uB2C8\uB2E4. \uAE30\uC874 \uC124\uCE58 CLI\uB294 \uBC14\uAFB8\uC9C0 \uC54A\uC558\uC2B5\uB2C8\uB2E4.'
        HashOk              = 'SHA-256 \uD655\uC778 \uC644\uB8CC.'
        ReplaceFailed       = '\uC124\uCE58 CLI\uB97C {0} \uC704\uCE58\uC5D0 \uC124\uCE58\uD558\uC9C0 \uBABB\uD588\uC2B5\uB2C8\uB2E4. \uC2E4\uD589 \uC911\uC778 railwait\uAC00 \uC788\uC73C\uBA74 \uB2EB\uACE0 \uB2E4\uC2DC \uC2DC\uB3C4\uD558\uC138\uC694.'
        Installed           = '\uC124\uCE58 CLI\uB97C \uC124\uCE58\uD588\uC2B5\uB2C8\uB2E4: {0}'
        PathMissing         = '\uCC38\uACE0: {0} \uD3F4\uB354\uAC00 PATH\uC5D0 \uC5C6\uC2B5\uB2C8\uB2E4. \uC774 \uC2A4\uD06C\uB9BD\uD2B8\uB294 PATH\uB97C \uBC14\uAFB8\uC9C0 \uC54A\uC2B5\uB2C8\uB2E4.'
        PathHow             = '''railwait'' \uC774\uB984\uB9CC\uC73C\uB85C \uC2E4\uD589\uD558\uB824\uBA74 PowerShell\uC5D0\uC11C \uB2E4\uC74C \uBA85\uB839\uC744 \uC9C1\uC811 \uC2E4\uD589\uD55C \uB4A4 \uC0C8 \uD130\uBBF8\uB110\uC744 \uC5EC\uC138\uC694.'
        PathFullCommand     = '\uADF8 \uC804\uAE4C\uC9C0\uB294 \uC804\uCCB4 \uACBD\uB85C\uB85C \uC2E4\uD589\uD558\uC138\uC694: {0}'
        NoConsole           = '\uB300\uD654\uD615 \uCF58\uC194\uC774 \uC5C6\uC5B4 \uC124\uCE58 \uB9C8\uBC95\uC0AC\uB97C \uC2DC\uC791\uD558\uC9C0 \uC54A\uC558\uC2B5\uB2C8\uB2E4. PowerShell\uC5D0\uC11C \uB2E4\uC74C \uBA85\uB839\uC744 \uC2E4\uD589\uD558\uC138\uC694.'
        Starting            = '\uC124\uCE58 \uB9C8\uBC95\uC0AC\uB97C \uC2DC\uC791\uD569\uB2C8\uB2E4: {0}'
    }

    function Get-RailwaitText {
        param([string]$Name, [object[]]$Arguments)
        $template = [regex]::Unescape($messages[$Name])
        if ($Arguments -and $Arguments.Count -gt 0) {
            return $template -f $Arguments
        }
        return $template
    }

    function Write-RailwaitText {
        [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '', Justification = 'Messages are for the interactive console, not the pipeline.')]
        param([string]$Name, [object[]]$Arguments)
        Write-Host (Get-RailwaitText $Name $Arguments)
    }

    # PROCESSOR_ARCHITEW6432 covers 32-bit PowerShell on 64-bit Windows. The registry value is the
    # native CPU, so x64 PowerShell emulated on ARM64 Windows still receives the arm64 executable.
    function Get-RailwaitArch {
        $arch = $env:PROCESSOR_ARCHITEW6432
        if (-not $arch) {
            $arch = $env:PROCESSOR_ARCHITECTURE
        }
        try {
            $key = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
            $native = (Get-ItemProperty -LiteralPath $key -Name PROCESSOR_ARCHITECTURE -ErrorAction Stop).PROCESSOR_ARCHITECTURE
            if ($native) {
                $arch = $native
            }
        } catch {
            Write-Verbose 'Native CPU registry value is unavailable; using the process environment.'
        }
        switch ($arch) {
            'AMD64' { return 'amd64' }
            'ARM64' { return 'arm64' }
        }
        if (-not $arch) {
            $arch = '?'
        }
        throw (Get-RailwaitText 'UnsupportedArch' @($arch))
    }

    function Get-RailwaitBaseUrl {
        param([string]$Repository)
        $override = $env:RAILWAIT_DOWNLOAD_BASE
        if ($override) {
            if ($override -match '@' -or $override -notmatch '^(https://.+|http://(127\.0\.0\.1|localhost)[:/].*)\z') {
                throw (Get-RailwaitText 'InvalidDownloadBase')
            }
            return $override.TrimEnd('/')
        }
        $version = $env:RAILWAIT_VERSION
        if ($version) {
            $plain = $version -replace '^v', ''
            if ($plain -notmatch '^[0-9]+\.[0-9]+\.[0-9]+\z') {
                throw (Get-RailwaitText 'InvalidVersion')
            }
            return "$Repository/releases/download/v$plain"
        }
        return "$Repository/releases/latest/download"
    }

    # Mirrors platform.installDir in the CLI: the app lives in %LOCALAPPDATA%\Railwait, so the
    # executable must stay outside of it.
    function Get-RailwaitBinDir {
        $local = $env:LOCALAPPDATA
        if (-not $local -or -not [IO.Path]::IsPathRooted($local)) {
            throw (Get-RailwaitText 'NoLocalAppData')
        }
        $separator = [IO.Path]::DirectorySeparatorChar
        $appDir = [IO.Path]::GetFullPath((Join-Path $local 'Railwait')).TrimEnd('\', '/')
        $dir = $env:RAILWAIT_BIN_DIR
        if ($dir) {
            if (-not [IO.Path]::IsPathRooted($dir)) {
                throw (Get-RailwaitText 'BinDirNotAbsolute')
            }
        } else {
            $dir = Join-Path (Join-Path $local 'Programs') 'Railwait'
        }
        $full = [IO.Path]::GetFullPath($dir).TrimEnd('\', '/')
        if (($full + $separator).StartsWith($appDir + $separator, [StringComparison]::OrdinalIgnoreCase)) {
            throw (Get-RailwaitText 'BinDirInsideApp' @($appDir))
        }
        return $full
    }

    # Splits RAILWAIT_INSTALL_ARGS on whitespace. Double or single quotes group a value with spaces,
    # also inside a word such as --path="C:\My Rail". Unbalanced quotes are rejected.
    function ConvertTo-RailwaitArgumentList {
        param([string]$Text)
        $list = New-Object System.Collections.Generic.List[string]
        if ([string]::IsNullOrWhiteSpace($Text)) {
            return , $list.ToArray()
        }
        $word = '(?:"[^"]*"|''[^'']*''|[^\s"'']+)+'
        if ([regex]::Replace($Text, $word, '').Trim().Length -ne 0) {
            throw (Get-RailwaitText 'InvalidInstallArgs')
        }
        foreach ($match in [regex]::Matches($Text, $word)) {
            $value = [regex]::Replace($match.Value, '"([^"]*)"|''([^'']*)''', { param($quoted) $quoted.Groups[1].Value + $quoted.Groups[2].Value })
            $list.Add($value)
        }
        return , $list.ToArray()
    }

    # Uses the hash only when exactly one line names the asset, in sha256sum text or binary form.
    function Get-RailwaitExpectedHash {
        param([string]$SumFile, [string]$AssetName)
        $sumName = Split-Path -Leaf $SumFile
        $found = @()
        foreach ($line in [IO.File]::ReadAllLines($SumFile)) {
            if ($line -cmatch '^([^ ]*)(  | \*)(.*)\z' -and $Matches[3] -ceq $AssetName) {
                $found += $Matches[1]
            }
        }
        if ($found.Count -ne 1) {
            throw (Get-RailwaitText 'ChecksumEntry' @($sumName, $AssetName))
        }
        if ($found[0] -cnotmatch '^[0-9a-f]{64}\z') {
            throw (Get-RailwaitText 'ChecksumFormat' @($sumName))
        }
        return $found[0]
    }

    function Format-RailwaitCommand {
        param([string]$Path, [string[]]$Arguments)
        $all = @($Path)
        if ($Arguments) {
            $all += $Arguments
        }
        $quoted = foreach ($item in $all) { "'" + ($item -replace "'", "''") + "'" }
        return '& ' + ($quoted -join ' ')
    }

    function Test-RailwaitOnPath {
        param([string]$Dir)
        $entries = @([Environment]::GetEnvironmentVariable('PATH'))
        if ([IO.Path]::DirectorySeparatorChar -eq '\') {
            $entries += [Environment]::GetEnvironmentVariable('Path', 'User')
        }
        $wanted = $Dir.TrimEnd('\', '/')
        foreach ($entry in (($entries -join [IO.Path]::PathSeparator) -split [regex]::Escape([string][IO.Path]::PathSeparator))) {
            if ($entry -and $entry.Trim().TrimEnd('\', '/') -ieq $wanted) {
                return $true
            }
        }
        return $false
    }

    if ($env:OS -ne 'Windows_NT') {
        throw (Get-RailwaitText 'NotWindows' @($repositoryUrl))
    }
    # Old .NET Framework defaults may lack TLS 1.2. SystemDefault (0) already lets the OS decide.
    $protocol = [Net.ServicePointManager]::SecurityProtocol
    if ([int]$protocol -ne 0 -and ($protocol -band [Net.SecurityProtocolType]::Tls12) -eq 0) {
        [Net.ServicePointManager]::SecurityProtocol = $protocol -bor [Net.SecurityProtocolType]::Tls12
    }

    $arch = Get-RailwaitArch
    $asset = "railwait-windows-$arch.exe"
    $sumAsset = "railwait-windows-$arch.sha256"
    $base = Get-RailwaitBaseUrl $repositoryUrl
    $binDir = Get-RailwaitBinDir
    $installArgs = ConvertTo-RailwaitArgumentList $env:RAILWAIT_INSTALL_ARGS
    $target = Join-Path $binDir 'railwait.exe'
    if (Test-Path -LiteralPath $target -PathType Container) {
        throw (Get-RailwaitText 'TargetIsDirectory' @($target))
    }

    if ($env:RAILWAIT_DOWNLOAD_BASE) {
        Write-RailwaitText 'BaseOverride' @($base)
    } elseif ($env:RAILWAIT_VERSION) {
        Write-RailwaitText 'VersionPinned' @($env:RAILWAIT_VERSION -replace '^v', '')
    }

    $work = Join-Path ([IO.Path]::GetTempPath()) ('railwait-install-' + [guid]::NewGuid().ToString('N'))
    $staged = $null
    New-Item -ItemType Directory -Path $work | Out-Null
    try {
        Write-RailwaitText 'Downloading' @($asset)
        $exePath = Join-Path $work $asset
        $sumPath = Join-Path $work $sumAsset
        foreach ($download in @(@($asset, $exePath), @($sumAsset, $sumPath))) {
            $url = $base + '/' + $download[0]
            try {
                Invoke-WebRequest -Uri $url -OutFile $download[1] -UseBasicParsing
            } catch {
                throw (Get-RailwaitText 'DownloadFailed' @($download[0], $url))
            }
        }

        $expected = Get-RailwaitExpectedHash $sumPath $asset
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $exePath).Hash.ToLowerInvariant()
        if ($actual -cne $expected) {
            throw (Get-RailwaitText 'HashMismatch')
        }
        Write-RailwaitText 'HashOk'

        # Stage next to the target and swap it in one step so an interrupted run keeps the old file.
        New-Item -ItemType Directory -Force -Path $binDir | Out-Null
        $staged = Join-Path $binDir ('.railwait-install-' + [guid]::NewGuid().ToString('N') + '.exe')
        Copy-Item -LiteralPath $exePath -Destination $staged
        try {
            if (Test-Path -LiteralPath $target -PathType Leaf) {
                [IO.File]::Replace($staged, $target, [Management.Automation.Language.NullString]::Value)
            } else {
                [IO.File]::Move($staged, $target)
            }
        } catch {
            throw (Get-RailwaitText 'ReplaceFailed' @($target))
        }
        $staged = $null
    } finally {
        if ($staged -and (Test-Path -LiteralPath $staged)) {
            Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue
        }
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-RailwaitText 'Installed' @($target)
    if (-not (Test-RailwaitOnPath $binDir)) {
        Write-Host ''
        Write-RailwaitText 'PathMissing' @($binDir)
        Write-RailwaitText 'PathHow'
        Write-Host ("    [Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path', 'User') + ';{0}', 'User')" -f ($binDir -replace "'", "''"))
        Write-RailwaitText 'PathFullCommand' @(Format-RailwaitCommand $target @())
    }

    $next = Format-RailwaitCommand $target (@('install') + $installArgs)
    $interactive = [Environment]::UserInteractive
    if ($interactive) {
        try {
            $interactive = -not [Console]::IsInputRedirected
        } catch {
            $interactive = $false
        }
    }
    Write-Host ''
    if (-not $interactive) {
        Write-RailwaitText 'NoConsole'
        Write-Host ('  ' + $next)
        return
    }
    Write-RailwaitText 'Starting' @($next)
    & $target install @installArgs
}

Install-Railwait
