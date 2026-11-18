//go:build linux || darwin

package platform

import (
	"errors"
	"fmt"
	"os"
	"strconv"
	"syscall"
)

func serviceIdentity() (string, error) { return strconv.Itoa(os.Getuid()), nil }

func protect(path string, info os.FileInfo, directory bool) error {
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != uint32(os.Geteuid()) {
		return fmt.Errorf("private storage must be owned by the current user: %s", path)
	}
	if !directory && stat.Nlink != 1 {
		return fmt.Errorf("private storage must not have hard links: %s", path)
	}
	if err := clearExtendedACL(path); err != nil {
		return err
	}
	mode := os.FileMode(0o600)
	if directory {
		mode = 0o700
	}
	return os.Chmod(path, mode)
}

func lockFile(file *os.File) error {
	err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
	if errors.Is(err, syscall.EWOULDBLOCK) || errors.Is(err, syscall.EAGAIN) {
		return ErrLocked
	}
	return err
}

func unlockFile(file *os.File) error {
	return syscall.Flock(int(file.Fd()), syscall.LOCK_UN)
}

func availableBytes(path string) (uint64, error) {
	var stat syscall.Statfs_t
	if err := syscall.Statfs(path, &stat); err != nil {
		return 0, err
	}
	if stat.Bsize <= 0 {
		return 0, errors.New("filesystem reported invalid block size")
	}
	return uint64(stat.Bavail) * uint64(stat.Bsize), nil
}
