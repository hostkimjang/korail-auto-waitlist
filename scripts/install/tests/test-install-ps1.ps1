# Tests for scripts/install/install.ps1.
# Runs on Windows PowerShell 5.1 and PowerShell 7 (Windows, Linux, macOS). On Linux and macOS it fakes the
# Windows environment variables that install.ps1 reads. Fake release assets are served by a local TCP
# listener, and the installer runs through "irm | iex" in a child PowerShell process whose standard input
# is redirected, so the downloaded railwait.exe is never executed. Temporary files are removed at the end.
# Keep this file pure ASCII as well: Windows PowerShell 5.1 reads a script without a BOM as ANSI.
#
# Usage: pwsh -NoProfile -File scripts/install/tests/test-install-ps1.ps1
#        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install\tests\test-install-ps1.ps1
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '', Justification = 'Test progress is written for the console.')]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '', Justification = 'Helpers only change environment variables of this test process.')]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingPositionalParameters', '', Justification = 'Assertions read better with positional arguments.')]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$installer = [IO.Path]::GetFullPath((Join-Path (Join-Path $PSScriptRoot '..') 'install.ps1'))
$onWindows = [IO.Path]::DirectorySeparatorChar -eq '\'
$repositoryUrl = 'https://github.com/hostkimjang/korail-auto-waitlist'
$script:passed = 0

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw "FAIL: $Message"
    }
}

function Assert-Throw {
    param([scriptblock]$Block, [string]$Message)
    $threw = $false
    try {
        & $Block | Out-Null
    } catch {
        $threw = $true
    }
    Assert-True $threw "$Message should fail"
}

function Assert-List {
    param([object[]]$Actual, [string[]]$Expected, [string]$Message)
    $items = @($Actual)
    Assert-True ($items.Count -eq $Expected.Count) "$Message returned $($items.Count) items instead of $($Expected.Count)"
    for ($i = 0; $i -lt $Expected.Count; $i++) {
        Assert-True ($items[$i] -ceq $Expected[$i]) "$Message item $i was '$($items[$i])' instead of '$($Expected[$i])'"
    }
}

function Complete-Check {
    param([string]$Name)
    $script:passed++
    Write-Host "ok - $Name"
}

$environmentNames = @('OS', 'PROCESSOR_ARCHITECTURE', 'PROCESSOR_ARCHITEW6432', 'LOCALAPPDATA', 'TEMP', 'TMP', 'TMPDIR',
    'RAILWAIT_VERSION', 'RAILWAIT_INSTALL_ARGS', 'RAILWAIT_BIN_DIR', 'RAILWAIT_DOWNLOAD_BASE')
$savedEnvironment = @{}
foreach ($name in $environmentNames) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name)
}

function Set-TestEnvironment {
    param([hashtable]$Values)
    foreach ($name in $Values.Keys) {
        [Environment]::SetEnvironmentVariable($name, $Values[$name])
    }
}

function Restore-TestEnvironment {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name])
    }
}

$work = Join-Path ([IO.Path]::GetTempPath()) ('railwait-install-ps1-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$listener = $null
$server = $null
try {
    # --- Encoding and structure --------------------------------------------------------------------
    foreach ($file in @($installer, $PSCommandPath)) {
        $bytes = [IO.File]::ReadAllBytes($file)
        $unexpected = 0
        foreach ($byte in $bytes) {
            if ($byte -gt 0x7E -or ($byte -lt 0x20 -and $byte -ne 9 -and $byte -ne 10 -and $byte -ne 13)) {
                $unexpected++
            }
        }
        Assert-True ($unexpected -eq 0) "$file contains $unexpected non-ASCII bytes"
        $latin1 = [Text.Encoding]::GetEncoding('ISO-8859-1').GetString($bytes)
        $utf8 = [Text.Encoding]::UTF8.GetString($bytes)
        Assert-True ($latin1 -ceq $utf8) "$file decodes differently as ISO-8859-1 and UTF-8"
    }
    Complete-Check 'install.ps1 and this test are pure ASCII and decode the same as ISO-8859-1 and UTF-8'

    $tokens = $null
    $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$errors)
    Assert-True ($errors.Count -eq 0) ("install.ps1 has parse errors: " + (($errors | ForEach-Object { $_.Message }) -join '; '))
    Complete-Check 'install.ps1 has no parse errors'

    $statements = @($ast.EndBlock.Statements)
    Assert-True ($statements.Count -eq 2) 'install.ps1 must contain only Install-Railwait and its final call'
    Assert-True ($statements[0] -is [Management.Automation.Language.FunctionDefinitionAst] -and $statements[0].Name -eq 'Install-Railwait') 'first statement must define Install-Railwait'
    Assert-True ($statements[1].Extent.Text -ceq 'Install-Railwait') 'last statement must call Install-Railwait'
    $source = [IO.File]::ReadAllText($installer)
    $lineEnds = [regex]::Matches($source, "\n")
    foreach ($lineEnd in $lineEnds) {
        $prefix = $source.Substring(0, $lineEnd.Index + 1)
        if ($prefix.TrimEnd() -ceq $source.TrimEnd()) {
            continue
        }
        $partialTokens = $null
        $partialErrors = $null
        $partial = [Management.Automation.Language.Parser]::ParseInput($prefix, [ref]$partialTokens, [ref]$partialErrors)
        if ($partialErrors.Count -gt 0) {
            continue
        }
        $calls = @($partial.EndBlock.Statements | Where-Object { $_.Extent.Text -ceq 'Install-Railwait' })
        Assert-True ($calls.Count -eq 0) "a download cut after offset $($lineEnd.Index) would still run Install-Railwait"
    }
    Complete-Check 'a partial download either fails to parse or never calls Install-Railwait'

    # --- Helpers extracted from the installer ------------------------------------------------------
    $main = $statements[0]
    $assignment = $main.Body.Find({
            param($node)
            $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$messages'
        }, $true)
    . ([scriptblock]::Create($assignment.Extent.Text))
    foreach ($helper in $main.Body.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
        . ([scriptblock]::Create($helper.Extent.Text))
    }

    foreach ($key in $messages.Keys) {
        $text = [regex]::Unescape($messages[$key])
        Assert-True ($text -notmatch '\\') "message $key keeps a backslash after unescaping"
        Assert-True ($text -match '[\uAC00-\uD7A3]') "message $key has no Korean text"
        $null = $text -f 'A', 'B'
    }
    $used = $main.Body.FindAll({
            param($node)
            $node -is [Management.Automation.Language.CommandAst] -and @('Get-RailwaitText', 'Write-RailwaitText') -contains $node.GetCommandName()
        }, $true)
    foreach ($call in $used) {
        $key = $call.CommandElements[1]
        if ($key -is [Management.Automation.Language.VariableExpressionAst] -and $key.VariablePath.UserPath -eq 'Name') {
            continue
        }
        Assert-True ($key -is [Management.Automation.Language.StringConstantExpressionAst]) "message key must be a literal: $($call.Extent.Text)"
        Assert-True ($messages.ContainsKey($key.Value)) "message key $($key.Value) is missing"
    }
    Assert-True ((Get-RailwaitText 'VersionPinned' @('1.4.4')) -match 'stable') 'version pin message must mention the stable app release'
    Complete-Check "all $($messages.Count) messages decode to Korean text and every used key exists"

    Assert-List (ConvertTo-RailwaitArgumentList '') @() 'empty arguments'
    Assert-List (ConvertTo-RailwaitArgumentList '   ') @() 'blank arguments'
    Assert-List (ConvertTo-RailwaitArgumentList '--custom --port 9090') @('--custom', '--port', '9090') 'plain arguments'
    Assert-List (ConvertTo-RailwaitArgumentList '--path "C:\My Rail" --access local') @('--path', 'C:\My Rail', '--access', 'local') 'double quotes'
    Assert-List (ConvertTo-RailwaitArgumentList "--path='C:\a b' --plain") @('--path=C:\a b', '--plain') 'single quotes inside a word'
    Assert-List (ConvertTo-RailwaitArgumentList '--path=""') @('--path=') 'empty quoted value'
    Assert-Throw { ConvertTo-RailwaitArgumentList '--path "C:\a b' } 'unbalanced quote'
    Complete-Check 'RAILWAIT_INSTALL_ARGS splitting'

    Set-TestEnvironment @{ RAILWAIT_VERSION = $null; RAILWAIT_DOWNLOAD_BASE = $null }
    Assert-True ((Get-RailwaitBaseUrl $repositoryUrl) -ceq "$repositoryUrl/releases/latest/download") 'default URL'
    foreach ($pinned in @('1.4.4', 'v1.4.4')) {
        Set-TestEnvironment @{ RAILWAIT_VERSION = $pinned }
        Assert-True ((Get-RailwaitBaseUrl $repositoryUrl) -ceq "$repositoryUrl/releases/download/v1.4.4") "pinned URL for $pinned"
    }
    foreach ($invalid in @('1.4', '1.4.4.1', '1.x.4', '.1.4')) {
        Set-TestEnvironment @{ RAILWAIT_VERSION = $invalid }
        Assert-Throw { Get-RailwaitBaseUrl $repositoryUrl } "version $invalid"
    }
    Set-TestEnvironment @{ RAILWAIT_VERSION = '1.4.4'; RAILWAIT_DOWNLOAD_BASE = 'https://example.com/assets/' }
    Assert-True ((Get-RailwaitBaseUrl $repositoryUrl) -ceq 'https://example.com/assets') 'download base wins over version'
    foreach ($allowed in @('http://127.0.0.1:8080/a', 'http://localhost/x')) {
        Set-TestEnvironment @{ RAILWAIT_DOWNLOAD_BASE = $allowed }
        Assert-True ((Get-RailwaitBaseUrl $repositoryUrl) -ceq $allowed) "allowed base $allowed"
    }
    foreach ($refused in @('http://example.com/a', 'http://localhost:1@evil.example/', 'ftp://127.0.0.1/a', 'http://127.0.0.1')) {
        Set-TestEnvironment @{ RAILWAIT_DOWNLOAD_BASE = $refused }
        Assert-Throw { Get-RailwaitBaseUrl $repositoryUrl } "base $refused"
    }
    Restore-TestEnvironment
    Complete-Check 'download URL selection and RAILWAIT_VERSION validation'

    $sums = Join-Path $work 'sums'
    New-Item -ItemType Directory -Path $sums | Out-Null
    $hash = 'a' * 64
    $other = 'b' * 64
    $name = 'railwait-windows-amd64.exe'
    $cases = @{
        'plain.sha256'  = "$hash  $name\n"
        'binary.sha256' = "$hash *$name\r\n"
        'extra.sha256'  = "$other  railwait-windows-arm64.exe\n$hash  $name\n"
    }
    foreach ($case in $cases.Keys) {
        [IO.File]::WriteAllText((Join-Path $sums $case), [regex]::Unescape($cases[$case]))
        Assert-True ((Get-RailwaitExpectedHash (Join-Path $sums $case) $name) -ceq $hash) "checksum file $case"
    }
    $broken = @{
        'none.sha256'     = "$hash  railwait-windows-arm64.exe\n"
        'twice.sha256'    = "$hash  $name\n$hash  $name\n"
        'upper.sha256'    = ($hash.ToUpperInvariant()) + "  $name\n"
        'short.sha256'    = "abc  $name\n"
        'prefixed.sha256' = "$hash  x$name\n"
        'onespace.sha256' = "$hash $name\n"
    }
    foreach ($case in $broken.Keys) {
        [IO.File]::WriteAllText((Join-Path $sums $case), [regex]::Unescape($broken[$case]))
        Assert-Throw { Get-RailwaitExpectedHash (Join-Path $sums $case) $name } "checksum file $case"
    }
    Complete-Check 'checksum files need exactly one well-formed line for the asset'

    $formatted = Format-RailwaitCommand 'C:\x y\railwait.exe' @('install', '--path', "C:\it's")
    Assert-True ($formatted -ceq "& 'C:\x y\railwait.exe' 'install' '--path' 'C:\it''s'") "formatted command was $formatted"
    Assert-True ((Format-RailwaitCommand 'C:\r.exe' @()) -ceq "& 'C:\r.exe'") 'formatted command without arguments'
    Complete-Check 'next command is a literal PowerShell command'

    $local = Join-Path $work 'local'
    Set-TestEnvironment @{ LOCALAPPDATA = $local; RAILWAIT_BIN_DIR = $null }
    $expectedBin = [IO.Path]::GetFullPath((Join-Path (Join-Path $local 'Programs') 'Railwait'))
    Assert-True ((Get-RailwaitBinDir) -ceq $expectedBin) 'default bin folder is LOCALAPPDATA\Programs\Railwait'
    Set-TestEnvironment @{ RAILWAIT_BIN_DIR = (Join-Path $local 'RailwaitTools') }
    Assert-True ((Get-RailwaitBinDir) -ceq [IO.Path]::GetFullPath((Join-Path $local 'RailwaitTools'))) 'sibling folder with a shared prefix is allowed'
    foreach ($inside in @((Join-Path $local 'Railwait'), (Join-Path (Join-Path $local 'Railwait') 'bin'), (Join-Path (Join-Path $local 'RAILWAIT') 'bin'))) {
        Set-TestEnvironment @{ RAILWAIT_BIN_DIR = $inside }
        Assert-Throw { Get-RailwaitBinDir } "bin folder $inside inside the app folder"
    }
    Set-TestEnvironment @{ RAILWAIT_BIN_DIR = 'relative\bin' }
    Assert-Throw { Get-RailwaitBinDir } 'relative bin folder'
    Set-TestEnvironment @{ RAILWAIT_BIN_DIR = $null; LOCALAPPDATA = $null }
    Assert-Throw { Get-RailwaitBinDir } 'missing LOCALAPPDATA'
    Restore-TestEnvironment
    Complete-Check 'bin folder defaults and app folder separation'

    if ($onWindows) {
        $nativeArch = Get-RailwaitArch
        Assert-True (@('amd64', 'arm64') -contains $nativeArch) "native architecture $nativeArch"
    } else {
        $nativeArch = 'amd64'
        $archCases = @(
            @{ Arch = 'AMD64'; Wow = $null; Expected = 'amd64' },
            @{ Arch = 'ARM64'; Wow = $null; Expected = 'arm64' },
            @{ Arch = 'x86'; Wow = 'AMD64'; Expected = 'amd64' },
            @{ Arch = 'x86'; Wow = 'ARM64'; Expected = 'arm64' }
        )
        foreach ($case in $archCases) {
            Set-TestEnvironment @{ PROCESSOR_ARCHITECTURE = $case.Arch; PROCESSOR_ARCHITEW6432 = $case.Wow }
            Assert-True ((Get-RailwaitArch) -ceq $case.Expected) "architecture $($case.Arch)/$($case.Wow)"
        }
        foreach ($unsupported in @('x86', 'IA64', $null)) {
            Set-TestEnvironment @{ PROCESSOR_ARCHITECTURE = $unsupported; PROCESSOR_ARCHITEW6432 = $null }
            Assert-Throw { Get-RailwaitArch } "architecture $unsupported"
        }
        Restore-TestEnvironment
    }
    Complete-Check "CPU detection (native: $nativeArch)"

    # --- End to end through irm | iex ---------------------------------------------------------------
    $www = Join-Path $work 'www'
    foreach ($folder in @('good', 'badhash', 'twolines', 'missing')) {
        New-Item -ItemType Directory -Path (Join-Path $www $folder) | Out-Null
    }
    Copy-Item -LiteralPath $installer -Destination (Join-Path $www 'install.ps1')
    $newline = [string][char]10
    foreach ($arch in @('amd64', 'arm64')) {
        $asset = "railwait-windows-$arch.exe"
        $sum = "railwait-windows-$arch.sha256"
        $assetPath = Join-Path (Join-Path $www 'good') $asset
        [IO.File]::WriteAllText($assetPath, "fake $asset")
        $digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $assetPath).Hash.ToLowerInvariant()
        [IO.File]::WriteAllText((Join-Path (Join-Path $www 'good') $sum), "$digest  $asset" + $newline)
        foreach ($folder in @('badhash', 'twolines')) {
            Copy-Item -LiteralPath $assetPath -Destination (Join-Path (Join-Path $www $folder) $asset)
        }
        [IO.File]::WriteAllText((Join-Path (Join-Path $www 'badhash') $sum), ('0' * 64) + "  $asset" + $newline)
        [IO.File]::WriteAllText((Join-Path (Join-Path $www 'twolines') $sum), ("$digest  $asset" + $newline) * 2)
    }

    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $serverUrl = 'http://127.0.0.1:' + $listener.LocalEndpoint.Port
    $serverScript = {
        param($Listener, $Root)
        while ($true) {
            try {
                $client = $Listener.AcceptTcpClient()
            } catch {
                break
            }
            try {
                $stream = $client.GetStream()
                $reader = New-Object System.IO.StreamReader($stream, [Text.Encoding]::ASCII, $false, 4096, $true)
                $request = $reader.ReadLine()
                while ($true) {
                    $header = $reader.ReadLine()
                    if ($null -eq $header -or $header -eq '') {
                        break
                    }
                }
                $relative = [Uri]::UnescapeDataString(($request -split ' ')[1].TrimStart('/'))
                $file = Join-Path $Root $relative
                if ($relative -notmatch '\.\.' -and [IO.File]::Exists($file)) {
                    $body = [IO.File]::ReadAllBytes($file)
                    $status = '200 OK'
                } else {
                    $body = [Text.Encoding]::ASCII.GetBytes('not found')
                    $status = '404 Not Found'
                }
                $crlf = [string][char]13 + [char]10
                $head = 'HTTP/1.1 ' + $status + $crlf + 'Content-Type: application/octet-stream' + $crlf +
                    'Content-Length: ' + $body.Length + $crlf + 'Connection: close' + $crlf + $crlf
                $headBytes = [Text.Encoding]::ASCII.GetBytes($head)
                $stream.Write($headBytes, 0, $headBytes.Length)
                $stream.Write($body, 0, $body.Length)
                $stream.Flush()
            } catch {
                $null = $_
            } finally {
                $client.Close()
            }
        }
    }
    $server = [PowerShell]::Create()
    $null = $server.AddScript($serverScript.ToString()).AddArgument($listener).AddArgument($www)
    $null = $server.BeginInvoke()

    $shell = (Get-Process -Id $PID).Path
    $emptyInput = Join-Path $work 'stdin.txt'
    [IO.File]::WriteAllText($emptyInput, '')

    function Invoke-Installer {
        param([string]$Case, [hashtable]$Overrides)
        $caseDir = Join-Path (Join-Path $work 'cases') $Case
        $tmp = Join-Path $caseDir 'tmp'
        New-Item -ItemType Directory -Path $tmp | Out-Null
        $values = @{
            LOCALAPPDATA = (Join-Path $caseDir 'local'); TEMP = $tmp; TMP = $tmp; TMPDIR = $tmp
            RAILWAIT_VERSION = $null; RAILWAIT_INSTALL_ARGS = $null; RAILWAIT_BIN_DIR = $null
            RAILWAIT_DOWNLOAD_BASE = "$serverUrl/good"
        }
        if (-not $onWindows) {
            $values.OS = 'Windows_NT'
            $values.PROCESSOR_ARCHITECTURE = 'AMD64'
            $values.PROCESSOR_ARCHITEW6432 = $null
        }
        foreach ($key in $Overrides.Keys) {
            $values[$key] = $Overrides[$key]
        }
        $stdout = Join-Path $caseDir 'stdout.txt'
        $stderr = Join-Path $caseDir 'stderr.txt'
        $arguments = '-NoProfile -NonInteractive '
        if ($onWindows) {
            $arguments += '-ExecutionPolicy Bypass '
        }
        $arguments += '-Command "irm ''' + $serverUrl + '/install.ps1'' | iex"'
        $start = @{
            FilePath               = $shell
            ArgumentList           = $arguments
            NoNewWindow            = $true
            Wait                   = $true
            PassThru               = $true
            RedirectStandardInput  = $emptyInput
            RedirectStandardOutput = $stdout
            RedirectStandardError  = $stderr
        }
        Set-TestEnvironment $values
        try {
            $process = Start-Process @start
        } finally {
            Restore-TestEnvironment
        }
        $leftovers = @(Get-ChildItem -LiteralPath $tmp -Force -Filter 'railwait-install-*')
        return @{
            Status    = $process.ExitCode
            Output    = [IO.File]::ReadAllText($stdout) + [IO.File]::ReadAllText($stderr)
            Local     = $values.LOCALAPPDATA
            Leftovers = $leftovers.Count
        }
    }

    function Assert-NoStaged {
        param([string]$Dir)
        if (Test-Path -LiteralPath $Dir) {
            $staged = @(Get-ChildItem -LiteralPath $Dir -Force -Filter '.railwait-install-*')
            Assert-True ($staged.Count -eq 0) "staged files remain in $Dir"
        }
    }

    function Assert-SameFile {
        param([string]$Actual, [string]$Expected)
        Assert-True (Test-Path -LiteralPath $Actual -PathType Leaf) "$Actual is missing"
        $a = (Get-FileHash -Algorithm SHA256 -LiteralPath $Actual).Hash
        $b = (Get-FileHash -Algorithm SHA256 -LiteralPath $Expected).Hash
        Assert-True ($a -eq $b) "$Actual differs from $Expected"
    }

    $nativeAsset = "railwait-windows-$nativeArch.exe"
    $result = Invoke-Installer 'default-location' @{ RAILWAIT_INSTALL_ARGS = '--custom --port 9090 --path "C:\My Rail"' }
    Assert-True ($result.Status -eq 0) ("installer failed: " + $result.Output)
    $target = Join-Path (Join-Path (Join-Path $result.Local 'Programs') 'Railwait') 'railwait.exe'
    Assert-SameFile $target (Join-Path (Join-Path $www 'good') $nativeAsset)
    Assert-True ($result.Output.Contains("& '$target' 'install' '--custom' '--port' '9090' '--path' 'C:\My Rail'")) ("next command missing: " + $result.Output)
    Assert-True ($result.Output.Contains('SetEnvironmentVariable')) ("PATH hint missing: " + $result.Output)
    Assert-True ($result.Leftovers -eq 0) 'temporary download folder remains'
    Assert-NoStaged (Split-Path -Parent $target)
    Complete-Check 'irm | iex installs the verified CLI to LOCALAPPDATA\Programs\Railwait and prints the next command'

    $bin = Join-Path $work 'existing-bin'
    New-Item -ItemType Directory -Path $bin | Out-Null
    [IO.File]::WriteAllText((Join-Path $bin 'railwait.exe'), 'old')
    $result = Invoke-Installer 'replace-existing' @{ RAILWAIT_BIN_DIR = $bin }
    Assert-True ($result.Status -eq 0) ("installer failed: " + $result.Output)
    Assert-SameFile (Join-Path $bin 'railwait.exe') (Join-Path (Join-Path $www 'good') $nativeAsset)
    Assert-NoStaged $bin
    Complete-Check 'an existing railwait.exe is replaced in place'

    if (-not $onWindows) {
        $result = Invoke-Installer 'arm64' @{ PROCESSOR_ARCHITECTURE = 'ARM64'; RAILWAIT_BIN_DIR = (Join-Path $work 'arm64-bin') }
        Assert-True ($result.Status -eq 0) ("installer failed: " + $result.Output)
        Assert-SameFile (Join-Path (Join-Path $work 'arm64-bin') 'railwait.exe') (Join-Path (Join-Path $www 'good') 'railwait-windows-arm64.exe')
        Complete-Check 'ARM64 Windows receives the arm64 executable'
    }

    $bin = Join-Path $work 'mismatch-bin'
    New-Item -ItemType Directory -Path $bin | Out-Null
    [IO.File]::WriteAllText((Join-Path $bin 'railwait.exe'), 'old')
    $result = Invoke-Installer 'hash-mismatch' @{ RAILWAIT_BIN_DIR = $bin; RAILWAIT_DOWNLOAD_BASE = "$serverUrl/badhash" }
    Assert-True ($result.Status -ne 0) 'checksum mismatch must fail'
    Assert-True ($result.Output.Contains('SHA-256')) ("checksum message missing: " + $result.Output)
    Assert-True ([IO.File]::ReadAllText((Join-Path $bin 'railwait.exe')) -ceq 'old') 'existing CLI changed after a checksum mismatch'
    Assert-True ($result.Leftovers -eq 0) 'temporary download folder remains after a checksum mismatch'
    Assert-NoStaged $bin
    Complete-Check 'a checksum mismatch stops before replacing the existing CLI'

    $result = Invoke-Installer 'two-lines' @{ RAILWAIT_BIN_DIR = (Join-Path $work 'two-lines-bin'); RAILWAIT_DOWNLOAD_BASE = "$serverUrl/twolines" }
    Assert-True ($result.Status -ne 0) 'duplicate checksum lines must fail'
    Assert-True ($result.Output.Contains($nativeAsset)) ("asset name missing: " + $result.Output)
    Assert-True (-not (Test-Path -LiteralPath (Join-Path (Join-Path $work 'two-lines-bin') 'railwait.exe'))) 'installed despite duplicate checksum lines'
    Complete-Check 'duplicate checksum lines stop the installation'

    $result = Invoke-Installer 'missing-asset' @{ RAILWAIT_BIN_DIR = (Join-Path $work 'missing-bin'); RAILWAIT_DOWNLOAD_BASE = "$serverUrl/missing" }
    Assert-True ($result.Status -ne 0) 'missing asset must fail'
    Assert-True ($result.Output.Contains("$serverUrl/missing/$nativeAsset")) ("download URL missing: " + $result.Output)
    Assert-True ($result.Leftovers -eq 0) 'temporary download folder remains after a failed download'
    Complete-Check 'a failed download stops and names the URL'

    $result = Invoke-Installer 'inside-app' @{ RAILWAIT_BIN_DIR = (Join-Path (Join-Path (Join-Path $work 'inside-local') 'Railwait') 'bin'); LOCALAPPDATA = (Join-Path $work 'inside-local') }
    Assert-True ($result.Status -ne 0) 'bin folder inside the app folder must fail'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $work 'inside-local'))) 'created folders inside the app folder'
    Complete-Check 'the CLI is never placed inside the app folder'

    $result = Invoke-Installer 'bad-arguments' @{ RAILWAIT_BIN_DIR = (Join-Path $work 'bad-arguments-bin'); RAILWAIT_INSTALL_ARGS = '--path "C:\a b' }
    Assert-True ($result.Status -ne 0) 'unbalanced RAILWAIT_INSTALL_ARGS must fail'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $work 'bad-arguments-bin'))) 'installed despite invalid arguments'
    Complete-Check 'invalid RAILWAIT_INSTALL_ARGS stop before downloading'

    if (-not $onWindows) {
        $result = Invoke-Installer 'x86' @{ PROCESSOR_ARCHITECTURE = 'x86'; RAILWAIT_BIN_DIR = (Join-Path $work 'x86-bin') }
        Assert-True ($result.Status -ne 0) 'x86 must fail'
        Assert-True ($result.Output.Contains('x86')) ("architecture missing: " + $result.Output)
        Complete-Check '32-bit Windows is refused'

        $result = Invoke-Installer 'not-windows' @{ OS = $null; RAILWAIT_BIN_DIR = (Join-Path $work 'not-windows-bin') }
        Assert-True ($result.Status -ne 0) 'non-Windows must fail'
        Assert-True ($result.Output.Contains('install.sh')) ("install.sh hint missing: " + $result.Output)
        Assert-True (-not (Test-Path -LiteralPath (Join-Path $work 'not-windows-bin'))) 'installed on a non-Windows system'
        Complete-Check 'macOS and Linux are pointed to install.sh'
    }

    Write-Host ("PASS: {0} checks on PowerShell {1} ({2})" -f $script:passed, $PSVersionTable.PSVersion, [Environment]::OSVersion.VersionString)
} finally {
    Restore-TestEnvironment
    if ($listener) {
        $listener.Stop()
    }
    if ($server) {
        $server.Stop()
        $server.Dispose()
    }
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
