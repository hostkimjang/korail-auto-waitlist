package platform

import (
	"bufio"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// managerHelperLock names the lock TestManagerProcessHelper holds in a child process.
const managerHelperLock = "RAILWAIT_TEST_MANAGER_LOCK"

// TestManagerProcessHelper is not a test of its own. Started by
// TestEndManagerProcessEndsOnlyThisInstallationsManager with managerHelperLock set, it holds
// that lock the way a manager left running by Task Scheduler's /End does.
func TestManagerProcessHelper(t *testing.T) {
	path := os.Getenv(managerHelperLock)
	if path == "" {
		return
	}
	lock, err := AcquireLock(path)
	if err != nil {
		fmt.Println("lock:", err)
		os.Exit(2)
	}
	defer lock.Close()
	fmt.Println("ready")
	time.Sleep(2 * time.Minute)
	os.Exit(0)
}

func TestEndManagerProcessEndsOnlyThisInstallationsManager(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(self)
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	bin := filepath.Join(root, ".railwait", "bin")
	if err = os.MkdirAll(bin, 0o700); err != nil {
		t.Fatal(err)
	}
	executable := filepath.Join(bin, "railwait.exe")
	if err = os.WriteFile(executable, data, 0o700); err != nil {
		t.Fatal(err)
	}
	lockPath := filepath.Join(root, "serve.lock")
	cmd := exec.Command(executable, "-test.run=^TestManagerProcessHelper$", "-test.count=1")
	cmd.Env = append(os.Environ(), managerHelperLock+"="+lockPath)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err = cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
	})
	line, err := bufio.NewReader(stdout).ReadString('\n')
	if err != nil || strings.TrimSpace(line) != "ready" {
		t.Fatalf("helper did not take the lock: %q %v", line, err)
	}
	started := time.Now()
	pid := cmd.Process.Pid

	if err = EndManagerProcess(pid, self, started, 5*time.Second); !errors.Is(err, ErrNotManagerProcess) {
		t.Fatalf("another executable: %v", err)
	}
	if err = EndManagerProcess(pid, executable, started.Add(-time.Hour), 5*time.Second); !errors.Is(err, ErrNotManagerProcess) {
		t.Fatalf("a heartbeat start recorded before the process existed: %v", err)
	}
	if err = EndManagerProcess(pid, executable, started.Add(time.Hour), 5*time.Second); !errors.Is(err, ErrNotManagerProcess) {
		t.Fatalf("a heartbeat start recorded long after the process started: %v", err)
	}
	if lock, lockErr := AcquireLock(lockPath); !errors.Is(lockErr, ErrLocked) {
		if lockErr == nil {
			lock.Close()
		}
		t.Fatalf("a process that was not verified must keep running: %v", lockErr)
	}

	if err = EndManagerProcess(pid, executable, started, 10*time.Second); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(10 * time.Second)
	for {
		lock, lockErr := AcquireLock(lockPath)
		if lockErr == nil {
			lock.Close()
			break
		}
		if !errors.Is(lockErr, ErrLocked) || time.Now().After(deadline) {
			t.Fatalf("the lock stayed after the manager ended: %v", lockErr)
		}
		time.Sleep(100 * time.Millisecond)
	}
	if err = EndManagerProcess(pid, executable, started, time.Second); err != nil {
		t.Fatalf("a manager that has already exited: %v", err)
	}
}
