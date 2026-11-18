// Package platform owns operating-system-specific storage protection and process locking.
package platform

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sync"
)

// ErrLocked means another process currently owns the installation lock.
var ErrLocked = errors.New("another railwait operation is running")

type Info struct {
	OS   string `json:"os"`
	Arch string `json:"arch"`
}

func Detect() (Info, error) {
	info := Info{OS: runtime.GOOS, Arch: runtime.GOARCH}
	if !SupportedTarget(info.OS, info.Arch) {
		return info, fmt.Errorf("unsupported platform: %s/%s", info.OS, info.Arch)
	}
	return info, nil
}

func SupportedTarget(goos, goarch string) bool {
	return (goos == "linux" || goos == "darwin" || goos == "windows") &&
		(goarch == "amd64" || goarch == "arm64")
}

func ExecutableName() string {
	if runtime.GOOS == "windows" {
		return "railwait.exe"
	}
	return "railwait"
}

// DefaultInstallDir uses user-owned application data and never requires elevation.
func DefaultInstallDir() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return installDir(runtime.GOOS, home, os.Getenv), nil
}

func installDir(goos, home string, getenv func(string) string) string {
	switch goos {
	case "windows":
		if local := getenv("LOCALAPPDATA"); filepath.IsAbs(local) {
			return filepath.Join(local, "Railwait")
		}
		return filepath.Join(home, "AppData", "Local", "Railwait")
	case "darwin":
		return filepath.Join(home, "Library", "Application Support", "Railwait")
	default:
		if data := getenv("XDG_DATA_HOME"); filepath.IsAbs(data) {
			return filepath.Join(data, "railwait")
		}
		return filepath.Join(home, ".local", "share", "railwait")
	}
}

// SecureDir creates a private directory, or protects an existing directory.
// It does not recursively alter existing children. Protect files before writing secrets.
func SecureDir(path string) error {
	if err := os.MkdirAll(path, 0o700); err != nil {
		return err
	}
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("private storage must be a real directory: %s", path)
	}
	return protect(path, info, true)
}

// SecureFile refuses symlinks and special files; it never creates or truncates content.
func SecureFile(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf("private storage must be a regular file: %s", path)
	}
	return protect(path, info, false)
}

// Lock is a kernel-held lock. Its file remains after Close so waiters cannot lock a
// different inode. Process exit releases the lock without stale PID recovery.
type Lock struct {
	file *os.File
	mu   sync.Mutex
}

func AcquireLock(path string) (*Lock, error) {
	if err := SecureDir(filepath.Dir(path)); err != nil {
		return nil, err
	}
	if info, err := os.Lstat(path); err == nil && !info.Mode().IsRegular() {
		return nil, fmt.Errorf("lock must be a regular file")
	} else if err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	if err = SecureFile(path); err != nil {
		_ = file.Close()
		return nil, err
	}
	if err = lockFile(file); err != nil {
		_ = file.Close()
		return nil, err
	}
	return &Lock{file: file}, nil
}

func (lock *Lock) Close() error {
	lock.mu.Lock()
	defer lock.mu.Unlock()
	if lock.file == nil {
		return nil
	}
	err := errors.Join(unlockFile(lock.file), lock.file.Close())
	lock.file = nil
	return err
}

func PrerequisiteHint() string {
	switch runtime.GOOS {
	case "windows":
		return "Docker Desktop을 Linux containers 모드로 시작한 뒤 docker info와 docker compose version을 확인하세요. https://docs.docker.com/desktop/setup/install/windows-install/"
	case "darwin":
		return "CPU에 맞는 Docker Desktop을 설치·시작한 뒤 docker info와 docker compose version을 확인하세요. https://docs.docker.com/desktop/setup/install/mac-install/"
	default:
		return "Docker Engine과 Compose plugin을 설치하고 현재 사용자가 docker info를 실행할 수 있는지 확인하세요. Docker 그룹 권한은 호스트 관리 권한과 같으며 CLI는 그룹이나 권한을 자동 변경하지 않습니다. https://docs.docker.com/engine/install/"
	}
}

// AvailableBytes returns bytes available to this user on the nearest existing
// ancestor's filesystem, so a not-yet-created installation can be checked.
func AvailableBytes(path string) (uint64, error) {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return 0, err
	}
	for {
		_, err := os.Stat(absolute)
		if err == nil {
			return availableBytes(absolute)
		}
		if !errors.Is(err, os.ErrNotExist) {
			return 0, err
		}
		parent := filepath.Dir(absolute)
		if parent == absolute {
			return 0, err
		}
		absolute = parent
	}
}
