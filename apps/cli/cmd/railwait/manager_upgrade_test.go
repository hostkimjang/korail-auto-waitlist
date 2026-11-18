package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/manager"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

// upgradeServiceRunner stands in for launchctl, systemctl and schtasks. Starting the
// service writes a heartbeat as a new manager process would, unless startsDead is set.
type upgradeServiceRunner struct {
	root       string
	start      platform.ServiceCommand
	stop       platform.ServiceCommand
	commands   []string
	startsDead int
	starts     int
}

func (r *upgradeServiceRunner) Run(_ context.Context, command platform.ServiceCommand) error {
	r.commands = append(r.commands, command.Program+" "+strings.Join(command.Args, " "))
	if command.Program == "launchctl" && len(command.Args) > 0 && command.Args[0] == "print" {
		return errors.New("not registered")
	}
	if reflect.DeepEqual(command, r.start) {
		r.starts++
		if r.starts <= r.startsDead {
			return nil
		}
		now := time.Now().UTC().Format(time.RFC3339Nano)
		return manager.WriteStatusFile(filepath.Join(r.root, ".railwait", "manager", "status", "manager.json"), manager.Heartbeat{Schema: 1, State: "ready", PID: 4242, StartedAt: now, HeartbeatAt: now})
	}
	return nil
}

func (r *upgradeServiceRunner) count(command platform.ServiceCommand) int {
	total := 0
	for _, item := range r.commands {
		if item == command.Program+" "+strings.Join(command.Args, " ") {
			total++
		}
	}
	return total
}

func upgradeFixture(t *testing.T, installedVersion string) (string, string, *upgradeServiceRunner, managerUpgrader) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	root := filepath.Join(t.TempDir(), "installation")
	if err := platform.SecureDir(filepath.Join(root, ".railwait")); err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Prepare(root); err != nil {
		t.Fatal(err)
	}
	if err := platform.SecureDir(filepath.Join(root, ".railwait", "bin")); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(managerExecutablePath(root), []byte("old manager"), 0o700); err != nil {
		t.Fatal(err)
	}
	old := time.Now().UTC().Add(-time.Hour).Format(time.RFC3339Nano)
	if err := manager.WriteStatusFile(filepath.Join(root, ".railwait", "manager", "status", "manager.json"), manager.Heartbeat{Schema: 1, State: "ready", PID: 1111, StartedAt: old, HeartbeatAt: time.Now().UTC().Format(time.RFC3339Nano)}); err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(t.TempDir(), "railwait-new")
	if err := os.WriteFile(source, []byte("new manager"), 0o700); err != nil {
		t.Fatal(err)
	}
	plan, err := platform.CurrentServicePlan(managerExecutablePath(root), root)
	if err != nil {
		t.Fatal(err)
	}
	runner := &upgradeServiceRunner{root: root, start: plan.Start, stop: plan.Stop}
	upgrader := managerUpgrader{runner: runner, wait: 500 * time.Millisecond, version: func(context.Context, string) (string, error) {
		if installedVersion == "" {
			return "", errors.New("no version output")
		}
		return installedVersion, nil
	}}
	return root, source, runner, upgrader
}

func TestReleaseVersionsCompareOnlyOfficialBuilds(t *testing.T) {
	for _, test := range []struct {
		candidate, installed string
		newer                bool
	}{
		{"1.4.8", "1.4.6", true},
		{"1.10.0", "1.9.9", true},
		{"1.4.8", "1.4.8", false},
		{"1.4.6", "1.4.8", false},
		{"dev", "1.4.6", false},
		{"1.4.8", "dev", false},
		{"01.4.8", "1.4.6", false},
		{"1.4", "1.3.9", false},
	} {
		if newerRelease(test.candidate, test.installed) != test.newer {
			t.Fatalf("%s vs %s", test.candidate, test.installed)
		}
	}
}

func TestUpgradeReplacesAnOlderManagerAndWaitsForTheNewProcess(t *testing.T) {
	root, source, runner, upgrader := upgradeFixture(t, "1.4.6")
	result, err := upgrader.upgrade(context.Background(), root, source, "1.4.8", false)
	if err != nil {
		t.Fatal(err)
	}
	if result.From != "1.4.6" || result.To != "1.4.8" {
		t.Fatalf("result: %+v", result)
	}
	data, _ := os.ReadFile(managerExecutablePath(root))
	if string(data) != "new manager" {
		t.Fatal("manager executable not replaced")
	}
	if runner.count(runner.stop) != 1 || runner.count(runner.start) != 1 {
		t.Fatalf("commands: %v", runner.commands)
	}
	if !manager.Available(root, time.Now()) {
		t.Fatal("new manager heartbeat not observed")
	}
}

func TestUpgradeLeavesAManagerThatIsNotOlder(t *testing.T) {
	for _, installed := range []string{"1.4.8", "1.4.9", ""} {
		root, source, runner, upgrader := upgradeFixture(t, installed)
		result, err := upgrader.upgrade(context.Background(), root, source, "1.4.8", false)
		if err != nil || result.To != "" || len(runner.commands) != 0 {
			t.Fatalf("installed %q: result=%+v err=%v commands=%v", installed, result, err, runner.commands)
		}
		data, _ := os.ReadFile(managerExecutablePath(root))
		if string(data) != "old manager" {
			t.Fatal("manager replaced without a newer release")
		}
	}
	root, source, _, upgrader := upgradeFixture(t, "")
	if result, err := upgrader.upgrade(context.Background(), root, source, "dev", true); err != nil || result.To != "dev" {
		t.Fatalf("forced development upgrade: %+v %v", result, err)
	}
}

func TestUpgradeRestoresThePreviousManagerWhenTheNewOneDoesNotStart(t *testing.T) {
	root, source, runner, upgrader := upgradeFixture(t, "1.4.6")
	runner.startsDead = 1
	_, err := upgrader.upgrade(context.Background(), root, source, "1.4.8", false)
	if err == nil || !strings.Contains(err.Error(), "되돌렸습니다") {
		t.Fatalf("error: %v", err)
	}
	data, _ := os.ReadFile(managerExecutablePath(root))
	if string(data) != "old manager" {
		t.Fatal("previous manager not restored")
	}
	if runner.count(runner.start) != 2 {
		t.Fatalf("previous manager not started again: %v", runner.commands)
	}
}

func TestUpgradeWaitsWhileAnUpdateOrSettingsJobHoldsTheInstallLock(t *testing.T) {
	root, source, runner, upgrader := upgradeFixture(t, "1.4.6")
	lock, err := platform.AcquireLock(filepath.Join(root, ".railwait", "install.lock"))
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	if _, err = upgrader.upgrade(context.Background(), root, source, "1.4.8", false); err == nil || !strings.Contains(err.Error(), "진행 중인") {
		t.Fatalf("error: %v", err)
	}
	data, _ := os.ReadFile(managerExecutablePath(root))
	if string(data) != "old manager" || len(runner.commands) != 0 {
		t.Fatal("manager touched while a job held the install lock")
	}
}
