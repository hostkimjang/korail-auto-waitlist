package setup

import (
	"context"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"time"
)

type Action string

const (
	Retry     Action = "retry"
	OpenGuide Action = "open_guide"
	Start     Action = "start"
	Cancel    Action = "cancel"
)

// Need describes a single prerequisite that the user can resolve in the wizard.
// Detail contains a safe explanation, never raw process output or environment values.
type Need struct {
	Kind       string
	Title      string
	Detail     string
	URL        string
	CanStart   bool
	CanInstall bool
}

func Guide(name string) Need { return guide(runtime.GOOS, name) }

func guide(goos, name string) Need {
	if name == "tailscale" {
		platform := map[string]string{"darwin": "mac", "windows": "windows", "linux": "linux"}[goos]
		return Need{Kind: "tailscale", Title: "Tailscale 준비", Detail: "공식 설치 안내에서 설치한 뒤 로그인해 주세요. 운영체제의 VPN 허용과 계정 로그인은 직접 진행합니다.", URL: "https://tailscale.com/docs/install/" + platform}
	}
	platform := map[string]string{"darwin": "mac-install/", "windows": "windows-install/"}[goos]
	link := "https://docs.docker.com/engine/install/"
	detail := "Docker Engine과 Compose plugin을 설치하고 현재 사용자로 Docker를 실행할 수 있게 준비해 주세요."
	if platform != "" {
		link = "https://docs.docker.com/desktop/setup/install/" + platform
		detail = "공식 설치 안내에서 Docker Desktop을 설치하고 시작해 주세요. 약관 동의와 시스템 권한 요청은 설치 화면에서 직접 진행합니다."
	}
	if goos == "windows" {
		detail += " WSL 준비에 재부팅이 필요하면 이 설치 명령을 다시 실행해 이어갈 수 있습니다."
	}
	return Need{Kind: "docker", Title: "Docker 준비", Detail: detail, URL: link}
}

// OpenURL delegates a trusted link to the user's browser. Shell expansion is
// avoided on every platform; insecure URLs are restricted to the local app.
func OpenURL(ctx context.Context, address string) error {
	u, err := url.Parse(address)
	if err != nil || u.User != nil || u.Hostname() == "" || strings.ContainsAny(address, "\x00\r\n") {
		return fmt.Errorf("열 수 없는 주소입니다")
	}
	local := u.Hostname() == "127.0.0.1" || u.Hostname() == "localhost" || u.Hostname() == "::1"
	if u.Scheme != "https" && !(u.Scheme == "http" && local) {
		return fmt.Errorf("HTTPS 또는 로컬 앱 주소만 열 수 있습니다")
	}
	program, args := browserCommand(runtime.GOOS, address)
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, program, args...)
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("브라우저를 열지 못했습니다. 표시된 주소를 직접 열어 주세요")
	}
	return nil
}

func browserCommand(goos, address string) (string, []string) {
	switch goos {
	case "darwin":
		return "open", []string{address}
	case "windows":
		return "rundll32.exe", []string{"url.dll,FileProtocolHandler", address}
	default:
		return "xdg-open", []string{address}
	}
}

func desktopApplication(goos string, getenv func(string) string, exists func(string) bool) string {
	if goos == "darwin" && exists("/Applications/Docker.app/Contents/MacOS/Docker Desktop") {
		return "/Applications/Docker.app"
	}
	if goos == "darwin" && exists("/Applications/Docker.app/Contents/MacOS/Docker") {
		return "/Applications/Docker.app"
	}
	if goos == "windows" {
		for _, path := range []string{
			windowsRoot(getenv("LOCALAPPDATA")) + `\Programs\DockerDesktop\Docker Desktop.exe`,
			windowsRoot(getenv("ProgramFiles")) + `\Docker\Docker\Docker Desktop.exe`,
		} {
			if windowsRoot(path) != "" && exists(path) {
				return path
			}
		}
	}
	return ""
}

func installedDesktop() string {
	return desktopApplication(runtime.GOOS, os.Getenv, func(path string) bool {
		info, err := os.Stat(path)
		return err == nil && info.Mode().IsRegular()
	})
}

func startDesktop(ctx context.Context, application string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if runtime.GOOS == "darwin" {
		return exec.CommandContext(ctx, "open", "-a", application).Run()
	}
	// The Desktop app is intentionally long-lived. Do not kill it when the
	// prerequisite probe or the installer process exits.
	cmd := exec.Command(application)
	if err := cmd.Start(); err != nil {
		return err
	}
	return cmd.Process.Release()
}
