// Package setup discovers prerequisites and installs verified official packages
// through native OS authorization. It never changes an existing Docker context
// or accepts licenses and account permissions on the user's behalf.
package setup

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"runtime"
	"strings"
)

// Executable finds an existing CLI without requiring the user to edit PATH or
// reinstall an application whose command-line launcher was not configured.
func Executable(name string) (string, error) {
	if name != "docker" && name != "tailscale" {
		return "", fmt.Errorf("unsupported prerequisite: %s", name)
	}
	if path, err := exec.LookPath(name); err == nil && filepath.IsAbs(path) {
		return path, nil
	}
	home, _ := os.UserHomeDir()
	for _, path := range executableCandidates(runtime.GOOS, name, home, os.Getenv) {
		if executableFile(path, runtime.GOOS) {
			return path, nil
		}
	}
	return "", errors.New(name + " 실행 파일을 찾지 못했습니다")
}

func executableFile(path, goos string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular() && (goos == "windows" || info.Mode().Perm()&0o111 != 0)
}

func executableCandidates(goos, name, home string, getenv func(string) string) []string {
	switch goos {
	case "darwin":
		// macOS candidates use "/" regardless of the OS that evaluates this table.
		if name == "docker" {
			result := []string{"/Applications/Docker.app/Contents/Resources/bin/docker", "/usr/local/bin/docker", "/opt/homebrew/bin/docker"}
			if strings.HasPrefix(home, "/") {
				result = append([]string{path.Join(home, ".docker", "bin", "docker")}, result...)
			}
			return result
		}
		return []string{"/usr/local/bin/tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale", "/opt/homebrew/bin/tailscale"}
	case "windows":
		var result []string
		if name == "docker" {
			if root := windowsRoot(getenv("LOCALAPPDATA")); root != "" {
				result = append(result, root+`\Programs\DockerDesktop\resources\bin\docker.exe`)
			}
		}
		if root := windowsRoot(getenv("ProgramFiles")); root != "" {
			if name == "docker" {
				result = append(result, root+`\Docker\Docker\resources\bin\docker.exe`)
			} else {
				result = append(result, root+`\Tailscale\tailscale.exe`)
			}
		}
		return result
	default:
		return []string{"/usr/local/bin/" + name, "/usr/bin/" + name, "/bin/" + name, "/snap/bin/" + name}
	}
}

func windowsRoot(value string) string {
	if len(value) < 3 || value[1] != ':' || (value[2] != '\\' && value[2] != '/') || strings.ContainsAny(value, "\x00\r\n") {
		return ""
	}
	return strings.TrimRight(strings.ReplaceAll(value, "/", `\`), `\`)
}
