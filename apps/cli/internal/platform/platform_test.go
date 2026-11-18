package platform

import (
	"bufio"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
)

func TestSupportedTargets(t *testing.T) {
	for _, goos := range []string{"linux", "darwin", "windows"} {
		for _, arch := range []string{"amd64", "arm64"} {
			if !SupportedTarget(goos, arch) {
				t.Fatalf("expected support: %s/%s", goos, arch)
			}
		}
	}
	if SupportedTarget("freebsd", "amd64") || SupportedTarget("linux", "386") {
		t.Fatal("unsupported target accepted")
	}
	info, err := Detect()
	if err != nil || info.OS != runtime.GOOS || info.Arch != runtime.GOARCH {
		t.Fatalf("wrong native target: %+v %v", info, err)
	}
}

func TestAvailableBytesBeforeDirectoryCreation(t *testing.T) {
	dir := t.TempDir()
	available, err := AvailableBytes(filepath.Join(dir, "future", "installation"))
	if err != nil || available == 0 {
		t.Fatalf("cannot query installation filesystem: %d %v", available, err)
	}
	if _, err := os.Stat(filepath.Join(dir, "future")); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("space query created storage: %v", err)
	}
	if _, err := AvailableBytes("bad\x00path"); err == nil {
		t.Fatal("invalid path must fail closed")
	}
}

func TestDefaultInstallDir(t *testing.T) {
	home := t.TempDir()
	env := func(string) string { return "" }
	for _, tt := range []struct{ os, suffix string }{
		{"linux", filepath.Join(".local", "share", "railwait")},
		{"darwin", filepath.Join("Library", "Application Support", "Railwait")},
		{"windows", filepath.Join("AppData", "Local", "Railwait")},
	} {
		if got := installDir(tt.os, home, env); got != filepath.Join(home, tt.suffix) {
			t.Errorf("%s path = %s", tt.os, got)
		}
	}
	data := t.TempDir()
	if got := installDir("linux", home, func(string) string { return data }); got != filepath.Join(data, "railwait") {
		t.Fatalf("absolute XDG path ignored: %s", got)
	}
	if got := installDir("linux", home, func(string) string { return "relative" }); got != filepath.Join(home, ".local", "share", "railwait") {
		t.Fatalf("relative XDG path trusted: %s", got)
	}
}

func TestPrivateStorage(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "installation")
	if err := SecureDir(dir); err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(dir, ".env")
	if err := os.WriteFile(file, []byte("fixture"), 0o666); err != nil {
		t.Fatal(err)
	}
	if err := SecureFile(file); err != nil {
		t.Fatal(err)
	}
	if data, err := os.ReadFile(file); err != nil || string(data) != "fixture" {
		t.Fatalf("file content changed: %v", err)
	}
	assertPrivate(t, dir, true)
	assertPrivate(t, file, false)
	if err := SecureFile(dir); err == nil {
		t.Fatal("directory accepted as secret file")
	}
	if err := SecureDir(file); err == nil {
		t.Fatal("regular file accepted as directory")
	}
	link := filepath.Join(t.TempDir(), "link")
	if err := os.Symlink(file, link); err == nil {
		if err := SecureFile(link); err == nil {
			t.Fatal("symlink accepted as secret file")
		}
	}
}

func TestLockContentionAndRelease(t *testing.T) {
	path := filepath.Join(t.TempDir(), "install.lock")
	first, err := AcquireLock(path)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	if second, err := AcquireLock(path); !errors.Is(err, ErrLocked) {
		if second != nil {
			_ = second.Close()
		}
		t.Fatalf("second operation must be blocked: %v", err)
	}
	if err := first.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("lock file must persist: %v", err)
	}
	next, err := AcquireLock(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := next.Close(); err != nil {
		t.Fatal(err)
	}
	if err := next.Close(); err != nil {
		t.Fatalf("Close must be idempotent: %v", err)
	}
}

// A killed owner must never leave a stale lock that blocks installation resume.
func TestLockReleasedAfterProcessExit(t *testing.T) {
	path := filepath.Join(t.TempDir(), "install.lock")
	child := exec.Command(os.Args[0], "-test.run=^TestLockChild$")
	child.Env = append(os.Environ(), "RAILWAIT_LOCK_CHILD="+path)
	stdout, err := child.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdin, err := child.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = stdin.Close(); _ = child.Process.Kill(); _ = child.Wait() }()
	ready, err := bufio.NewReader(stdout).ReadString('\n')
	if err != nil || ready != "locked\n" {
		t.Fatalf("child did not lock: %q %v", ready, err)
	}
	if lock, err := AcquireLock(path); !errors.Is(err, ErrLocked) {
		if lock != nil {
			_ = lock.Close()
		}
		t.Fatalf("child lock not exclusive: %v", err)
	}
	if err := child.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	_ = child.Wait()
	lock, err := AcquireLock(path)
	if err != nil {
		t.Fatalf("lock not released after owner exit: %v", err)
	}
	_ = lock.Close()
}

func TestLockChild(t *testing.T) {
	path := os.Getenv("RAILWAIT_LOCK_CHILD")
	if path == "" {
		return
	}
	lock, err := AcquireLock(path)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	fmt.Println("locked")
	_, _ = os.Stdin.Read(make([]byte, 1))
}
