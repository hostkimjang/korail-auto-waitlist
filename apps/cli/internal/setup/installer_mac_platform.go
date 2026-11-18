package setup

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// Team identifiers are published by the vendors:
// https://github.com/docker/for-mac/issues/7527
// https://tailscale.com/docs/integrations/mdm/mac
const dockerMacTeam = "9BNSXJN65R"
const tailscaleMacTeam = "W5364U7YZB"

type macInstallOperations struct {
	run     func(context.Context, string, ...string) ([]byte, error)
	present func(string) bool
}

func installMac(ctx context.Context, p InstallPlan, downloaded string, progress func(string)) (InstallResult, error) {
	return installMacWith(ctx, p, downloaded, progress, macInstallOperations{run: runMacInstallerCommand, present: dependencyPresent})
}

func installMacWith(ctx context.Context, p InstallPlan, downloaded string, progress func(string), ops macInstallOperations) (result InstallResult, err error) {
	if p.OS != "darwin" || (p.Tool != "docker" && p.Tool != "tailscale") {
		return result, errors.New("지원하지 않는 macOS 설치 파일입니다")
	}
	if progress == nil {
		progress = func(string) {}
	}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	if p.Tool == "tailscale" {
		return installMacTailscale(ctx, p, downloaded, progress, ops)
	}
	if _, err = macVerifyCommand(ctx, ops, "/usr/bin/hdiutil", "verify", downloaded); err != nil {
		return result, errors.New("Docker 디스크 이미지 검증 실패: 설치를 실행하지 않았습니다")
	}
	mount := filepath.Join(filepath.Dir(downloaded), "docker-volume")
	if err = os.Mkdir(mount, 0o700); err != nil {
		return result, errors.New("설치 디스크를 열 폴더를 만들지 못했습니다")
	}
	// Mount and detach finish their own bounded operations even after Ctrl+C.
	// Killing hdiutil mid-attach could leave an untracked mounted filesystem.
	if _, err = macVerifyCommand(context.WithoutCancel(ctx), ops, "/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen", "-mountpoint", mount, downloaded); err != nil {
		attachErr := errors.New("Docker 설치 디스크를 열지 못했습니다")
		// hdiutil can time out after attaching. Never recursively remove an
		// unconfirmed mount just because the attach process returned an error.
		if _, detachErr := macVerifyCommand(context.Background(), ops, "/usr/bin/hdiutil", "detach", mount); detachErr != nil {
			return result, &retainedInstallerError{path: filepath.Dir(downloaded), cause: attachErr}
		}
		return result, attachErr
	}
	defer func() {
		if _, detachErr := macVerifyCommand(context.Background(), ops, "/usr/bin/hdiutil", "detach", mount); detachErr != nil {
			err = &retainedInstallerError{path: filepath.Dir(downloaded), cause: errors.Join(err, errors.New("Docker 설치 디스크 연결 해제 실패"))}
		}
	}()
	if err = ctx.Err(); err != nil {
		return result, err
	}
	app := filepath.Join(mount, "Docker.app")
	if err = verifyMacApp(ctx, ops, app, dockerMacTeam, "com.docker.docker"); err != nil {
		return result, err
	}
	installer := filepath.Join(app, "Contents", "MacOS", "install")
	if _, err = macVerifyCommand(ctx, ops, "/usr/bin/codesign", "--verify", "--strict", "-R", macRequirement(dockerMacTeam, ""), installer); err != nil {
		return result, errors.New("Docker 설치 프로그램의 배포자 서명이 올바르지 않습니다")
	}
	if ops.present(p.Tool) {
		return existingDependencyResult(), nil
	}
	progress("Docker 공식 서명 확인 완료 · macOS 권한 승인 창에서 설치를 허용해 주세요. 중단하려면 시스템 창에서 취소해 주세요.")
	// The vendor installer performs its supported privileged installation. No
	// --accept-license flag: Docker shows its agreement when the app first starts.
	const script = `on run argv
do shell script quoted form of (item 1 of argv) with administrator privileges
end run`
	if err = runMacAuthorized(ctx, ops, script, installer); err != nil {
		return result, err
	}
	if err = verifyMacApp(ctx, ops, "/Applications/Docker.app", dockerMacTeam, "com.docker.docker"); err != nil {
		return result, err
	}
	result = InstallResult{Installed: true, NeedsUserAction: true, Message: "Docker 설치가 끝났습니다. Docker 창에서 약관과 초기 설정을 마치면 준비 상태를 다시 확인합니다."}
	if _, err = macVerifyCommand(ctx, ops, "/usr/bin/open", "-a", "/Applications/Docker.app"); err != nil {
		return result, errors.New("Docker 설치는 완료했지만 앱을 열지 못했습니다. 응용 프로그램에서 Docker를 열어 주세요")
	}
	return result, nil
}

func installMacTailscale(ctx context.Context, p InstallPlan, downloaded string, progress func(string), ops macInstallOperations) (InstallResult, error) {
	result := InstallResult{}
	out, err := macVerifyCommand(ctx, ops, "/usr/sbin/pkgutil", "--check-signature", downloaded)
	if err != nil || !tailscalePackageSigner(out) {
		return result, errors.New("Tailscale 설치 파일의 공식 배포자 서명이 올바르지 않습니다")
	}
	if _, err = macVerifyCommand(ctx, ops, "/usr/sbin/spctl", "--assess", "--type", "install", "--verbose=4", downloaded); err != nil {
		return result, errors.New("macOS가 Tailscale 설치 파일을 신뢰하지 않아 설치를 중단했습니다")
	}
	if ops.present(p.Tool) {
		return existingDependencyResult(), nil
	}
	progress("Tailscale 공식 서명 확인 완료 · macOS 권한 승인 창에서 설치를 허용해 주세요. 중단하려면 시스템 창에서 취소해 주세요.")
	const script = `on run argv
do shell script "/usr/sbin/installer -pkg " & quoted form of (item 1 of argv) & " -target /" with administrator privileges
end run`
	if err = runMacAuthorized(ctx, ops, script, downloaded); err != nil {
		return result, err
	}
	if err = verifyMacApp(ctx, ops, "/Applications/Tailscale.app", tailscaleMacTeam, "io.tailscale.ipn.macsys"); err != nil {
		return result, err
	}
	result = InstallResult{Installed: true, NeedsUserAction: true, Message: "Tailscale 설치가 끝났습니다. 시스템 설정의 VPN·확장 기능 허용과 Tailscale 로그인을 직접 마쳐 주세요."}
	if _, err = macVerifyCommand(ctx, ops, "/usr/bin/open", "-a", "/Applications/Tailscale.app"); err != nil {
		return result, errors.New("Tailscale 설치는 완료했지만 앱을 열지 못했습니다. 응용 프로그램에서 Tailscale을 열어 주세요")
	}
	return result, nil
}

func tailscalePackageSigner(output []byte) bool {
	// Match only the leaf installer certificate, not a team identifier anywhere
	// in the filename or chain. pkgutil exit status and Gatekeeper are checked too.
	return regexp.MustCompile(`(?m)^\s*1\. Developer ID Installer: Tailscale Inc\. \(W5364U7YZB\)\s*$`).Match(output)
}

func macRequirement(team, identifier string) string {
	// codesign interprets requirements as filenames unless prefixed with '='.
	requirement := `=anchor apple generic and certificate leaf[subject.OU] = "` + team + `"`
	if identifier != "" {
		requirement += ` and identifier "` + identifier + `"`
	}
	return requirement
}

func verifyMacApp(ctx context.Context, ops macInstallOperations, app, team, identifier string) error {
	if _, err := macVerifyCommand(ctx, ops, "/usr/bin/codesign", "--verify", "--deep", "--strict", "-R", macRequirement(team, identifier), app); err != nil {
		return errors.New("앱의 공식 배포자 서명 확인에 실패했습니다. 설치를 계속하지 않습니다")
	}
	if _, err := macVerifyCommand(ctx, ops, "/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=4", app); err != nil {
		return errors.New("macOS가 앱의 서명을 신뢰하지 않아 설치를 중단했습니다")
	}
	return nil
}

func macVerifyCommand(ctx context.Context, ops macInstallOperations, program string, args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	return ops.run(ctx, program, args...)
}

func runMacAuthorized(ctx context.Context, ops macInstallOperations, script, artifact string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	// Keep the package/mount until the elevated process has definitely exited.
	// The user can cancel in macOS's authorization/installer window.
	out, err := ops.run(context.WithoutCancel(ctx), "/usr/bin/osascript", "-e", script, "--", artifact)
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if err != nil {
		if strings.Contains(string(out), "(-128)") || strings.Contains(string(out), "User canceled") {
			return ErrCancelled
		}
		return errors.New("시스템 설치를 완료하지 못했습니다. 권한 승인과 설치 창의 안내를 확인해 주세요")
	}
	return nil
}

func runMacInstallerCommand(ctx context.Context, program string, args ...string) ([]byte, error) {
	cmd := exec.CommandContext(ctx, program, args...)
	cmd.Env = append(dockerProbeEnvironment(os.Environ()), "LC_ALL=C", "LANG=C")
	var output limitedOutput
	cmd.Stdout, cmd.Stderr = &output, &output
	if err := cmd.Run(); err != nil {
		return output.Bytes(), fmt.Errorf("macOS 설치 명령 실패: %w", err)
	}
	return output.Bytes(), nil
}
