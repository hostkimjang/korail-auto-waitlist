package setup

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type windowsInstallerCommand struct {
	Purpose   string
	Program   string
	Script    string
	Variables map[string]string
}

type windowsInstallerRunner func(context.Context, windowsInstallerCommand) ([]byte, error)

func installWindows(ctx context.Context, plan InstallPlan, downloaded string, progress func(string)) (InstallResult, error) {
	directory, err := installerWindowsSystemDirectory()
	if err != nil {
		return InstallResult{}, err
	}
	return installWindowsWith(ctx, plan, downloaded, directory, progress, runWindowsInstallerCommand)
}

func installWindowsWith(ctx context.Context, plan InstallPlan, downloaded, systemDirectory string, progress func(string), run windowsInstallerRunner) (InstallResult, error) {
	if err := ctx.Err(); err != nil {
		return InstallResult{}, err
	}
	expectedPublisher, extension := "", ""
	switch plan.Tool {
	case "docker":
		expectedPublisher, extension = "Docker Inc", ".exe"
	case "tailscale":
		expectedPublisher, extension = "Tailscale Inc.", ".msi"
	default:
		return InstallResult{}, errors.New("지원하지 않는 Windows 설치 프로그램입니다")
	}
	if plan.OS != "windows" || (plan.Arch != "amd64" && plan.Arch != "arm64") || plan.Publisher != expectedPublisher || !strings.EqualFold(filepath.Ext(downloaded), extension) || strings.ContainsAny(downloaded, "\x00\r\n\"") {
		return InstallResult{}, errors.New("Windows 설치 프로그램 계약이 일치하지 않습니다")
	}
	file, err := os.Open(downloaded)
	if err != nil {
		return InstallResult{}, err
	}
	info, statErr := os.Lstat(downloaded)
	if statErr != nil || !info.Mode().IsRegular() {
		file.Close()
		return InstallResult{}, errors.New("설치 파일은 일반 파일이어야 합니다")
	}
	digest := sha256.New()
	_, copyErr := io.Copy(digest, file)
	if err = errors.Join(copyErr, file.Close()); err != nil {
		return InstallResult{}, err
	}
	variables := map[string]string{
		"RAILWAIT_INSTALLER_FILE":      downloaded,
		"RAILWAIT_INSTALLER_PUBLISHER": expectedPublisher,
		"RAILWAIT_INSTALLER_SHA256":    hex.EncodeToString(digest.Sum(nil)),
		"RAILWAIT_INSTALLER_TOOL":      plan.Tool,
		"RAILWAIT_SYSTEM_DIRECTORY":    systemDirectory,
	}
	program := strings.TrimRight(systemDirectory, `\/`) + `\WindowsPowerShell\v1.0\powershell.exe`
	command := func(purpose, script string) windowsInstallerCommand {
		return windowsInstallerCommand{Purpose: purpose, Program: program, Script: script, Variables: variables}
	}
	if progress != nil {
		progress("Windows 설치 파일의 서명과 게시자 확인")
	}
	verifyCtx, cancel := context.WithTimeout(ctx, 90*time.Second)
	output, err := run(verifyCtx, command("signature", windowsSignatureScript))
	cancel()
	if err != nil {
		return InstallResult{}, fmt.Errorf("Windows 설치 파일 서명을 확인하지 못했습니다: %w", err)
	}
	var signature struct {
		Status    string `json:"status"`
		Publisher string `json:"publisher"`
		SHA256    string `json:"sha256"`
	}
	if decodeWindowsResult(output, &signature) != nil || signature.Status != "Valid" || signature.Publisher != expectedPublisher || signature.SHA256 != variables["RAILWAIT_INSTALLER_SHA256"] {
		return InstallResult{}, errors.New("설치 파일의 유효한 서명·공식 게시자·파일 해시를 확인하지 못했습니다")
	}
	if plan.Tool == "docker" {
		result, ready, prepareErr := prepareWindowsWSL(ctx, command, progress, run)
		if prepareErr != nil || !ready {
			return result, prepareErr
		}
	}
	if err = ctx.Err(); err != nil {
		return InstallResult{}, err
	}
	if progress != nil {
		progress("Windows 설치 시작 · 권한과 약관은 설치 창에서 확인합니다")
	}
	// Killing this waiter would leave an elevated installer running while common
	// cleanup removes its input. Once launched, wait for OS-side completion/cancel.
	output, err = run(context.WithoutCancel(ctx), command("install", windowsInstallScript))
	if err != nil {
		return InstallResult{NeedsUserAction: true}, fmt.Errorf("Windows 설치 결과를 확인하지 못했습니다. 시스템 설치 창을 확인하세요: %w", err)
	}
	var result windowsProcessResult
	if decodeWindowsResult(output, &result) != nil || !result.Verified {
		return InstallResult{}, errors.New("설치 직전 서명 확인 또는 설치 결과 형식이 잘못되었습니다")
	}
	installed, err := windowsInstallExit(result.ExitCode)
	if ctx.Err() != nil && err == nil {
		installed.NeedsUserAction = true
		return installed, fmt.Errorf("시스템 설치 창이 종료되었습니다. 취소한 CLI 대신 같은 명령을 다시 실행해 상태를 확인하세요: %w", ctx.Err())
	}
	return installed, err
}

type windowsProcessResult struct {
	ExitCode int  `json:"exit_code"`
	Verified bool `json:"verified"`
}

type windowsWSLProbe struct {
	Available     bool   `json:"available"`
	Version       string `json:"version"`
	VersionExit   int    `json:"version_exit"`
	StatusExit    int    `json:"status_exit"`
	RebootPending bool   `json:"reboot_pending"`
}

func prepareWindowsWSL(ctx context.Context, command func(string, string) windowsInstallerCommand, progress func(string), run windowsInstallerRunner) (InstallResult, bool, error) {
	probe := func() (windowsWSLProbe, error) {
		probeCtx, cancel := context.WithTimeout(ctx, 45*time.Second)
		defer cancel()
		output, err := run(probeCtx, command("wsl_probe", windowsWSLProbeScript))
		var result windowsWSLProbe
		if err != nil || decodeWindowsResult(output, &result) != nil {
			return result, errors.New("WSL 준비 상태를 확인하지 못했습니다")
		}
		return result, nil
	}
	if progress != nil {
		progress("Windows의 WSL 버전과 실행 준비 확인")
	}
	before, err := probe()
	if err != nil {
		return InstallResult{}, false, err
	}
	if !before.Available {
		return InstallResult{NeedsUserAction: true}, false, errors.New("Windows의 WSL 실행 파일이 없습니다. 지원되는 Windows 버전과 업데이트 상태를 확인하세요")
	}
	if before.VersionExit == 0 && wslVersionReady(before.Version) && before.StatusExit == 0 {
		return InstallResult{}, true, nil
	}
	action := "install"
	if before.VersionExit == 0 && before.Version != "" && !wslVersionReady(before.Version) {
		action = "update"
	}
	change := command("wsl_"+action, windowsWSLChangeScript)
	change.Variables = cloneWindowsVariables(change.Variables)
	change.Variables["RAILWAIT_WSL_ACTION"] = action
	if progress != nil {
		progress("WSL 준비 시작 · Windows 권한 요청을 승인해 주세요. 자동으로 재부팅하지 않습니다")
	}
	if err = ctx.Err(); err != nil {
		return InstallResult{}, false, err
	}
	output, err := run(context.WithoutCancel(ctx), change)
	if err != nil {
		return InstallResult{NeedsUserAction: true}, false, errors.New("WSL 설치 또는 업데이트 결과를 확인하지 못했습니다. 시스템 설치 창을 확인하세요")
	}
	var changed windowsProcessResult
	if decodeWindowsResult(output, &changed) != nil || !changed.Verified {
		return InstallResult{}, false, errors.New("WSL 준비 명령의 결과가 잘못되었습니다")
	}
	if changed.ExitCode == 3010 || changed.ExitCode == 1641 {
		return InstallResult{RebootRequired: true, NeedsUserAction: true, Message: "WSL 준비 후 Windows 재시작이 필요합니다. 재시작한 뒤 같은 설치 명령을 실행하세요."}, false, nil
	}
	if changed.ExitCode != 0 {
		_, installErr := windowsInstallExit(changed.ExitCode)
		return InstallResult{NeedsUserAction: true}, false, fmt.Errorf("WSL 준비를 마치지 못했습니다: %w", installErr)
	}
	if err = ctx.Err(); err != nil {
		return InstallResult{NeedsUserAction: true}, false, err
	}
	after, err := probe()
	if err != nil {
		return InstallResult{NeedsUserAction: true}, false, err
	}
	if after.RebootPending {
		return InstallResult{RebootRequired: true, NeedsUserAction: true, Message: "Windows가 재시작 대기 상태입니다. 재시작한 뒤 같은 설치 명령으로 이어가세요."}, false, nil
	}
	if after.Available && after.VersionExit == 0 && wslVersionReady(after.Version) && after.StatusExit == 0 {
		return InstallResult{}, true, nil
	}
	return InstallResult{NeedsUserAction: true}, false, errors.New("WSL 명령은 끝났지만 실행 준비를 확인하지 못했습니다. Windows의 WSL·가상화 설정을 확인한 뒤 다시 실행하세요")
}

func wslVersionReady(version string) bool {
	parts := strings.Split(version, ".")
	if len(parts) < 3 || len(parts) > 4 {
		return false
	}
	values := make([]int, len(parts))
	for n, part := range parts {
		value, err := strconv.Atoi(part)
		if err != nil || value < 0 {
			return false
		}
		values[n] = value
	}
	return values[0] > 2 || values[0] == 2 && (values[1] > 1 || values[1] == 1 && values[2] >= 5)
}

func windowsInstallExit(code int) (InstallResult, error) {
	switch code {
	case 0:
		return InstallResult{Installed: true, Message: "설치 프로그램이 종료되었습니다. 앱 실행 상태를 이어서 확인합니다."}, nil
	case 3010, 1641:
		return InstallResult{Installed: true, RebootRequired: true, NeedsUserAction: true, Message: "Windows 재시작이 필요합니다. 재시작한 뒤 같은 설치 명령으로 이어가세요."}, nil
	case 1602, 1223:
		return InstallResult{NeedsUserAction: true}, errors.New("Windows 설치 또는 권한 승인이 취소되었습니다. 같은 설치 명령으로 다시 시작할 수 있습니다")
	default:
		return InstallResult{NeedsUserAction: true}, fmt.Errorf("Windows 설치 프로그램이 오류 코드 %d로 종료되었습니다", code)
	}
}

func decodeWindowsResult(data []byte, value any) error {
	if len(data) == 0 || len(data) > 16<<10 {
		return errors.New("invalid Windows installer result size")
	}
	data = bytes.TrimPrefix(data, []byte{0xef, 0xbb, 0xbf})
	var required []string
	switch value.(type) {
	case *windowsProcessResult:
		required = []string{"exit_code", "verified"}
	case *windowsWSLProbe:
		required = []string{"available", "version", "version_exit", "status_exit", "reboot_pending"}
	}
	if len(required) != 0 {
		var fields map[string]json.RawMessage
		if json.Unmarshal(data, &fields) != nil || len(fields) != len(required) {
			return errors.New("missing Windows installer result fields")
		}
		for _, name := range required {
			if raw, ok := fields[name]; !ok || bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
				return errors.New("missing Windows installer result field")
			}
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(value); err != nil {
		return err
	}
	if decoder.Decode(new(any)) != io.EOF {
		return errors.New("unexpected trailing Windows installer output")
	}
	return nil
}

func cloneWindowsVariables(values map[string]string) map[string]string {
	cloned := make(map[string]string, len(values))
	for key, value := range values {
		cloned[key] = value
	}
	return cloned
}

func runWindowsInstallerCommand(ctx context.Context, command windowsInstallerCommand) ([]byte, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	cmd := exec.CommandContext(ctx, command.Program, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command.Script)
	for _, entry := range os.Environ() {
		name, _, _ := strings.Cut(entry, "=")
		if !strings.HasPrefix(strings.ToUpper(name), "RAILWAIT_INSTALLER_") && !strings.EqualFold(name, "RAILWAIT_WSL_ACTION") && !strings.EqualFold(name, "RAILWAIT_SYSTEM_DIRECTORY") {
			cmd.Env = append(cmd.Env, entry)
		}
	}
	for key, value := range command.Variables {
		cmd.Env = append(cmd.Env, key+"="+value)
	}
	var output bytes.Buffer
	cmd.Stdout = &output
	cmd.Stderr = io.Discard
	if err := cmd.Run(); err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, errors.New("Windows 시스템 명령 실행을 완료하지 못했습니다")
	}
	return output.Bytes(), nil
}

const windowsSignatureScript = `
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
  $signature = Get-AuthenticodeSignature -LiteralPath $env:RAILWAIT_INSTALLER_FILE
  $publisher = ''
  if ($null -ne $signature.SignerCertificate) {
    $publisher = $signature.SignerCertificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
  }
  $hash = (Get-FileHash -LiteralPath $env:RAILWAIT_INSTALLER_FILE -Algorithm SHA256).Hash.ToLowerInvariant()
  @{status=$signature.Status.ToString(); publisher=$publisher; sha256=$hash} | ConvertTo-Json -Compress
} catch { exit 17 }
`

const windowsInstallScript = `
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
  $file = $env:RAILWAIT_INSTALLER_FILE
  $signature = Get-AuthenticodeSignature -LiteralPath $file
  if ($signature.Status.ToString() -cne 'Valid' -or $null -eq $signature.SignerCertificate) { throw 'signature' }
  $publisher = $signature.SignerCertificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
  $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($publisher -cne $env:RAILWAIT_INSTALLER_PUBLISHER -or $hash -cne $env:RAILWAIT_INSTALLER_SHA256) { throw 'identity' }
  if ($env:RAILWAIT_INSTALLER_TOOL -ceq 'docker') {
    $process = Start-Process -FilePath $file -ArgumentList @('install', '--user', '--backend=wsl-2') -Wait -PassThru
  } elseif ($env:RAILWAIT_INSTALLER_TOOL -ceq 'tailscale') {
    $msiexec = Join-Path $env:RAILWAIT_SYSTEM_DIRECTORY 'msiexec.exe'
    $quoted = '"' + $file + '"'
    $process = Start-Process -FilePath $msiexec -ArgumentList @('/i', $quoted, '/norestart') -Verb RunAs -Wait -PassThru
  } else { throw 'tool' }
  @{exit_code=$process.ExitCode; verified=$true} | ConvertTo-Json -Compress
} catch {
  if ($_.Exception.NativeErrorCode -eq 1223) {
    @{exit_code=1223; verified=$true} | ConvertTo-Json -Compress
  } else { exit 18 }
}
`

const windowsWSLProbeScript = `
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
  $wsl = Join-Path $env:RAILWAIT_SYSTEM_DIRECTORY 'wsl.exe'
  $available = Test-Path -LiteralPath $wsl -PathType Leaf
  $version = ''; $versionExit = -1; $statusExit = -1
  if ($available) {
    # Inbox WSL reports an unsupported --version on stderr. Treat its exit
    # status as a missing prerequisite, not as a PowerShell script failure.
    $ErrorActionPreference = 'Continue'
    $versionText = (& $wsl --version 2>$null | Out-String).Replace([string][char]0, '')
    $versionExit = $LASTEXITCODE
    if ($versionText -match '\b([0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?)\b') { $version = $Matches[1] }
    $null = & $wsl --status 2>$null
    $statusExit = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
  }
  $reboot = (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')
  @{available=$available; version=$version; version_exit=$versionExit; status_exit=$statusExit; reboot_pending=$reboot} | ConvertTo-Json -Compress
} catch { exit 19 }
`

const windowsWSLChangeScript = `
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
  $wsl = Join-Path $env:RAILWAIT_SYSTEM_DIRECTORY 'wsl.exe'
  if ($env:RAILWAIT_WSL_ACTION -ceq 'install') { $arguments = @('--install', '--no-distribution') }
  elseif ($env:RAILWAIT_WSL_ACTION -ceq 'update') { $arguments = @('--update') }
  else { throw 'action' }
  $process = Start-Process -FilePath $wsl -ArgumentList $arguments -Verb RunAs -Wait -PassThru
  @{exit_code=$process.ExitCode; verified=$true} | ConvertTo-Json -Compress
} catch {
  if ($_.Exception.NativeErrorCode -eq 1223) {
    @{exit_code=1223; verified=$true} | ConvertTo-Json -Compress
  } else { exit 20 }
}
`
