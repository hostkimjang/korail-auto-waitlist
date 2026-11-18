package manager

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

type fakeServiceRunner struct {
	commands []platform.ServiceCommand
	fail     bool
}

func (r *fakeServiceRunner) Run(_ context.Context, command platform.ServiceCommand) error {
	r.commands = append(r.commands, command)
	if r.fail {
		return errors.New("unavailable user service manager")
	}
	if command.Program == "launchctl" && len(command.Args) > 0 && command.Args[0] == "print" {
		return errors.New("not registered")
	}
	return nil
}

func TestServiceRegistrationUsesOnlyGeneratedUserCommands(t *testing.T) {
	root, _ := fixture(t)
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	exe := filepath.Join(t.TempDir(), "railwait executable")
	if err := os.WriteFile(exe, []byte("fixture executable"), 0o700); err != nil {
		t.Fatal(err)
	}
	runner := &fakeServiceRunner{}
	plan, err := InstallService(context.Background(), root, exe, runner)
	if err != nil {
		t.Fatal(err)
	}
	if len(runner.commands) < 2 {
		t.Fatal("service did not register and start")
	}
	last := runner.commands[len(runner.commands)-1]
	if last.Program != plan.Start.Program {
		t.Fatal("wrong service manager")
	}
	if _, err := os.Stat(plan.Definition); err != nil {
		t.Fatal(err)
	}
	if Available(root, time.Now()) {
		t.Fatal("registration falsely claimed live daemon")
	}
	runner.fail = true
	if err := StartService(context.Background(), root, exe, runner); err == nil {
		t.Fatal("unsupported service environment reported success")
	}
	if runtime.GOOS == "windows" && plan.Register[0].Program != "schtasks.exe" {
		t.Fatal("missing Windows supervisor")
	}
}

func TestServiceDefinitionPreservesExistingDifferingFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "services", "railwait.plist")
	plan := platform.ServicePlan{Definition: path, Content: []byte("first definition")}
	if err := WriteServiceDefinition(plan); err != nil {
		t.Fatal(err)
	}
	if err := WriteServiceDefinition(plan); err != nil {
		t.Fatal("matching definition not idempotent", err)
	}
	plan.Content = []byte("replacement")
	if err := WriteServiceDefinition(plan); err == nil {
		t.Fatal("overwrote unknown definition")
	}
	data, _ := os.ReadFile(path)
	if string(data) != "first definition" {
		t.Fatal("existing definition modified")
	}
}

func TestWaitAvailableFailsWithoutHeartbeat(t *testing.T) {
	root, _ := fixture(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := WaitAvailable(ctx, root); !errors.Is(err, context.Canceled) {
		t.Fatal("missing daemon claimed available", err)
	}
}
