package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/manager"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

// holdServeLock stands in for a Windows manager that Task Scheduler's /End left running: the
// service is stopped, but the manager process keeps the serve lock.
func holdServeLock(t *testing.T, root string) *platform.Lock {
	t.Helper()
	lock, err := platform.AcquireLock(filepath.Join(manager.InstallationPaths(root).Root, "serve.lock"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = lock.Close() })
	return lock
}

func shortLeftoverGrace(t *testing.T) {
	t.Helper()
	previous := leftoverGrace
	leftoverGrace = 20 * time.Millisecond
	t.Cleanup(func() { leftoverGrace = previous })
}

// endedProcess records what an upgrader or uninstaller asked to end.
type endedProcess struct {
	pid        int
	executable string
	startedAt  time.Time
}

func TestUpgradeEndsTheManagerProcessTheServiceManagerLeftRunning(t *testing.T) {
	root, source, runner, upgrader := upgradeFixture(t, "1.4.6")
	shortLeftoverGrace(t)
	held := holdServeLock(t, root)
	beat, err := manager.ReadHeartbeat(root)
	if err != nil {
		t.Fatal(err)
	}
	upgrader.wait = 5 * time.Second
	var ended []endedProcess
	upgrader.endProcess = func(pid int, executable string, startedAt time.Time, _ time.Duration) error {
		ended = append(ended, endedProcess{pid, executable, startedAt})
		return held.Close()
	}
	var notices []string
	upgrader.notice = func(message string) { notices = append(notices, message) }
	result, err := upgrader.upgrade(context.Background(), root, source, "1.4.8", false)
	if err != nil || result.To != "1.4.8" {
		t.Fatalf("upgrade: %+v %v", result, err)
	}
	recorded, _ := time.Parse(time.RFC3339Nano, beat.StartedAt)
	if len(ended) != 1 || ended[0].pid != beat.PID || ended[0].executable != managerExecutablePath(root) || !ended[0].startedAt.Equal(recorded) {
		t.Fatalf("ended %+v, heartbeat %+v", ended, beat)
	}
	if len(notices) != 1 || !strings.Contains(notices[0], fmt.Sprintf("PID %d", beat.PID)) {
		t.Fatalf("notices: %q", notices)
	}
	if data, _ := os.ReadFile(managerExecutablePath(root)); string(data) != "new manager" {
		t.Fatal("manager executable not replaced")
	}
	if runner.count(runner.start) != 1 {
		t.Fatalf("commands: %v", runner.commands)
	}
}

func TestUpgradeKeepsAManagerProcessItCannotVerifyAndNamesIt(t *testing.T) {
	root, source, runner, upgrader := upgradeFixture(t, "1.4.6")
	shortLeftoverGrace(t)
	holdServeLock(t, root)
	upgrader.wait = 300 * time.Millisecond
	calls := 0
	upgrader.endProcess = func(int, string, time.Time, time.Duration) error {
		calls++
		return fmt.Errorf("%w: 실행 파일 C:\\Windows\\System32\\notepad.exe", platform.ErrNotManagerProcess)
	}
	_, err := upgrader.upgrade(context.Background(), root, source, "1.4.8", false)
	if err == nil || calls != 1 || !errors.Is(err, platform.ErrNotManagerProcess) {
		t.Fatalf("calls=%d err=%v", calls, err)
	}
	for _, part := range []string{"PID 1111", managerExecutablePath(root), "notepad.exe"} {
		if !strings.Contains(err.Error(), part) {
			t.Fatalf("error does not name %q: %v", part, err)
		}
	}
	if data, _ := os.ReadFile(managerExecutablePath(root)); string(data) != "old manager" {
		t.Fatal("replaced the manager while its old process still ran")
	}
	if runner.count(runner.start) != 1 {
		t.Fatalf("the old manager must be started again: %v", runner.commands)
	}
}

func TestUpgradeTimeoutWhereTheServiceManagerStopsManagersItself(t *testing.T) {
	root, source, _, upgrader := upgradeFixture(t, "1.4.6")
	shortLeftoverGrace(t)
	holdServeLock(t, root)
	upgrader.wait = 200 * time.Millisecond
	upgrader.endProcess = func(int, string, time.Time, time.Duration) error { return errors.ErrUnsupported }
	_, err := upgrader.upgrade(context.Background(), root, source, "1.4.8", false)
	if err == nil || !strings.Contains(err.Error(), "멈추지 않았습니다") || strings.Contains(err.Error(), "PID") {
		t.Fatalf("err=%v", err)
	}
}

func writeManagerHeartbeat(t *testing.T, root string, pid int) {
	t.Helper()
	if _, err := manager.Prepare(root); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	if err := manager.WriteStatusFile(filepath.Join(manager.InstallationPaths(root).Status, "manager.json"), manager.Heartbeat{Schema: 1, State: "ready", PID: pid, StartedAt: now, HeartbeatAt: now}); err != nil {
		t.Fatal(err)
	}
}

func TestUninstallEndsAManagerProcessLeftRunningAndRemovesContainers(t *testing.T) {
	test := newUninstallTest(t, "", false)
	shortLeftoverGrace(t)
	writeManagerHeartbeat(t, test.root, 2222)
	held := holdServeLock(t, test.root)
	test.u.stopWait = 5 * time.Second
	var ended []endedProcess
	test.u.endProcess = func(pid int, executable string, startedAt time.Time, _ time.Duration) error {
		ended = append(ended, endedProcess{pid, executable, startedAt})
		return held.Close()
	}
	if err := test.u.run(context.Background(), uninstallOptions{Path: test.root, Yes: true}); err != nil {
		t.Fatal(err)
	}
	if len(ended) != 1 || ended[0].pid != 2222 || ended[0].executable != managerExecutablePath(test.root) {
		t.Fatalf("ended: %+v", ended)
	}
	if !strings.Contains(test.out.String(), "PID 2222") {
		t.Fatalf("output does not report the ended process: %s", test.out.String())
	}
	if test.events.index(" down ") < 0 {
		t.Fatalf("containers were not removed: %v", test.events.lines)
	}
}

func TestUninstallNamesAManagerProcessItDidNotEndAndKeepsYes(t *testing.T) {
	test := newUninstallTest(t, "", false)
	shortLeftoverGrace(t)
	writeManagerHeartbeat(t, test.root, 2222)
	holdServeLock(t, test.root)
	test.u.stopWait = 300 * time.Millisecond
	test.u.endProcess = func(int, string, time.Time, time.Duration) error {
		return fmt.Errorf("%w: 실행 파일 C:\\Windows\\System32\\notepad.exe", platform.ErrNotManagerProcess)
	}
	err := test.u.run(context.Background(), uninstallOptions{Path: test.root, DeleteData: true, Yes: true})
	if err == nil {
		t.Fatal("uninstall went on while the manager process still ran")
	}
	for _, part := range []string{"PID 2222", managerExecutablePath(test.root) + " 를 실행하는 프로세스를 끝낸 뒤", "--delete-data", "--yes"} {
		if !strings.Contains(err.Error(), part) {
			t.Fatalf("error does not name %q: %v", part, err)
		}
	}
	if strings.Contains(err.Error(), "잠시 뒤") || strings.Contains(err.Error(), "PID 2222(") {
		t.Fatalf("must not suggest waiting or ending a PID that failed the check: %v", err)
	}
	if test.events.index(" down ") >= 0 {
		t.Fatalf("removed containers while the manager still ran: %v", test.events.lines)
	}
	if _, statErr := os.Stat(test.root); statErr != nil {
		t.Fatalf("installation folder: %v", statErr)
	}
}

func TestUninstallNamesTheManagerPIDWhenItCouldNotBeEnded(t *testing.T) {
	test := newUninstallTest(t, "", false)
	shortLeftoverGrace(t)
	writeManagerHeartbeat(t, test.root, 2222)
	holdServeLock(t, test.root)
	test.u.stopWait = 300 * time.Millisecond
	test.u.endProcess = func(int, string, time.Time, time.Duration) error {
		return errors.New("프로세스를 끝내지 못했습니다: Access is denied.")
	}
	err := test.u.run(context.Background(), uninstallOptions{Path: test.root, Yes: true})
	if err == nil || !strings.Contains(err.Error(), "PID 2222("+managerExecutablePath(test.root)+")를 끝낸 뒤") || !strings.Contains(err.Error(), "Access is denied") {
		t.Fatalf("err=%v", err)
	}
}
