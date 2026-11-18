package setup

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

// InstallPlan describes a vendor-owned artifact. URLs are selected internally;
// neither command-line URLs nor publisher overrides are accepted.
type InstallPlan struct {
	Tool, OS, Arch, URL, Filename, Publisher string
	MaxBytes                                 int64
}

// Installed means the native installer succeeded or an existing installation
// was found. It does not mean Docker is ready or Tailscale is authenticated.
// RebootRequired pauses setup; NeedsUserAction covers license, VPN and login UI.
type InstallResult struct {
	Installed       bool
	RebootRequired  bool
	NeedsUserAction bool
	Message         string
}

func DependencyPlan(tool, goos, arch string) (InstallPlan, error) {
	p := InstallPlan{Tool: tool, OS: goos, Arch: arch}
	if (arch != "arm64" && arch != "amd64") || (goos != "darwin" && goos != "windows") {
		return p, errors.New("이 운영체제에서는 공식 설치 안내로 필수 프로그램을 준비해 주세요")
	}
	switch tool {
	case "docker":
		p.Publisher, p.MaxBytes = "Docker Inc", 2<<30
		if goos == "darwin" {
			p.Filename = "Docker.dmg"
			p.URL = "https://desktop.docker.com/mac/main/" + arch + "/Docker.dmg"
		} else {
			p.Filename = "Docker Desktop Installer.exe"
			p.URL = "https://desktop.docker.com/win/main/" + arch + "/Docker%20Desktop%20Installer.exe"
		}
	case "tailscale":
		p.Publisher, p.MaxBytes = "Tailscale Inc.", 256<<20
		if goos == "darwin" {
			p.Filename, p.URL = "Tailscale.pkg", "https://pkgs.tailscale.com/stable/Tailscale-latest-macos.pkg"
		} else {
			p.Filename = "tailscale-setup.msi"
			p.URL = "https://pkgs.tailscale.com/stable/tailscale-setup-latest-" + arch + ".msi"
		}
	default:
		return p, errors.New("지원하지 않는 필수 프로그램입니다")
	}
	return p, nil
}

// CanInstallDependency is intentionally missing-only. A stopped or partially
// installed native application requires repair/start, not an automatic overwrite.
func CanInstallDependency(tool string) bool {
	_, err := DependencyPlan(tool, runtime.GOOS, runtime.GOARCH)
	return err == nil && !dependencyPresent(tool)
}

func dependencyPresent(tool string) bool {
	if _, err := Executable(tool); err == nil {
		return true
	}
	return nativeDependencyPresent(runtime.GOOS, tool, os.Getenv, func(path string) bool {
		_, err := os.Lstat(path)
		return !os.IsNotExist(err)
	})
}

func nativeDependencyPresent(goos, tool string, getenv func(string) string, exists func(string) bool) bool {
	if goos == "darwin" {
		name := map[string]string{"docker": "Docker.app", "tailscale": "Tailscale.app"}[tool]
		if name == "" {
			return false
		}
		if exists(filepath.Join("/Applications", name)) {
			return true
		}
		if home := getenv("HOME"); filepath.IsAbs(home) && exists(filepath.Join(home, "Applications", name)) {
			return true
		}
	}
	if goos == "windows" {
		if tool == "docker" {
			if root := windowsRoot(getenv("LOCALAPPDATA")); root != "" && exists(root+`\Programs\DockerDesktop`) {
				return true
			}
			if root := windowsRoot(getenv("ProgramFiles")); root != "" && exists(root+`\Docker\Docker`) {
				return true
			}
			return false
		}
		if root := windowsRoot(getenv("ProgramFiles")); root != "" && exists(root+`\Tailscale`) {
			return true
		}
	}
	return false
}

type dependencyInstallOperations struct {
	present  func(string) bool
	download func(context.Context, InstallPlan, string, func(string)) error
	install  func(context.Context, InstallPlan, string, func(string)) (InstallResult, error)
	makeTemp func() (string, error)
}

// InstallDependency downloads and verifies the vendor package before launching
// its native installer. Once native authorization starts, cancellation waits for
// that system window to finish so files cannot be deleted underneath an installer.
// The caller must recheck readiness and let the user finish OS consent/login.
func InstallDependency(ctx context.Context, tool string, progress func(string)) (InstallResult, error) {
	p, err := DependencyPlan(tool, runtime.GOOS, runtime.GOARCH)
	if err != nil {
		return InstallResult{}, err
	}
	return installDependency(ctx, p, progress, dependencyInstallOperations{
		present: dependencyPresent, download: downloadDependency, install: installNative,
		makeTemp: func() (string, error) { return os.MkdirTemp("", "railwait-prerequisite-") },
	})
}

func installNative(ctx context.Context, p InstallPlan, downloaded string, progress func(string)) (InstallResult, error) {
	switch p.OS {
	case "darwin":
		return installMac(ctx, p, downloaded, progress)
	case "windows":
		return installWindows(ctx, p, downloaded, progress)
	default:
		return InstallResult{}, errors.New("이 운영체제에서는 자동 설치를 지원하지 않습니다")
	}
}

// A mounted installer must never be recursively removed if detach failed.
type retainedInstallerError struct {
	path  string
	cause error
}

func (e *retainedInstallerError) Error() string {
	return fmt.Sprintf("설치 임시 자원을 안전하게 해제하지 못했습니다. 보존 위치: %s: %v", e.path, e.cause)
}
func (e *retainedInstallerError) Unwrap() error { return e.cause }

func installDependency(ctx context.Context, p InstallPlan, progress func(string), ops dependencyInstallOperations) (result InstallResult, err error) {
	if err := ctx.Err(); err != nil {
		return result, err
	}
	if progress == nil {
		progress = func(string) {}
	}
	if ops.present(p.Tool) {
		return existingDependencyResult(), nil
	}
	dir, err := ops.makeTemp()
	if err != nil {
		return result, errors.New("설치 파일을 저장할 임시 폴더를 만들지 못했습니다")
	}
	defer func() {
		var retained *retainedInstallerError
		if errors.As(err, &retained) {
			return
		}
		if cleanupErr := os.RemoveAll(dir); cleanupErr != nil {
			err = errors.Join(err, fmt.Errorf("설치 임시 폴더 정리 실패 (%s): %w", dir, cleanupErr))
		}
	}()
	if err = platform.SecureDir(dir); err != nil {
		return result, fmt.Errorf("설치 폴더 접근 권한 설정 실패: %w", err)
	}
	if p.OS == "windows" && p.Arch == "arm64" && p.Tool == "docker" {
		progress("Windows ARM용 Docker Desktop은 공식 Early Access 배포판입니다.")
	}
	path := filepath.Join(dir, p.Filename)
	if err = ops.download(ctx, p, path, progress); err != nil {
		return result, err
	}
	if err = ctx.Err(); err != nil {
		return result, err
	}
	// Another setup may have completed while the large package was downloading.
	if ops.present(p.Tool) {
		return existingDependencyResult(), nil
	}
	return ops.install(ctx, p, path, progress)
}

func existingDependencyResult() InstallResult {
	return InstallResult{Installed: true, NeedsUserAction: true, Message: "기존 프로그램을 유지합니다. 앱을 열어 준비 상태를 확인해 주세요."}
}
