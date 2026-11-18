package manager

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

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

func TestServiceDefinitionReplacesOnlyKnownLegacyRendering(t *testing.T) {
	path := filepath.Join(t.TempDir(), "service", "railwait-manager.xml")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	legacy := []byte("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<Task>released</Task>\n")
	if err := os.WriteFile(path, legacy, 0o600); err != nil {
		t.Fatal(err)
	}
	plan := platform.ServicePlan{Definition: path, Content: []byte("\xff\xfecurrent"), Legacy: [][]byte{legacy}}
	if found, err := HasLegacyDefinition(plan); err != nil || !found {
		t.Fatalf("legacy definition not recognized: %v %v", found, err)
	}
	if err := WriteServiceDefinition(plan); err != nil {
		t.Fatal("known earlier rendering was not migrated", err)
	}
	if data, _ := os.ReadFile(path); string(data) != string(plan.Content) {
		t.Fatal("legacy definition not replaced")
	}
	if found, err := HasLegacyDefinition(plan); err != nil || found {
		t.Fatalf("current definition reported as legacy: %v %v", found, err)
	}
	edited := []byte("<Task>edited by the user</Task>")
	if err := os.WriteFile(path, edited, 0o600); err != nil {
		t.Fatal(err)
	}
	if found, err := HasLegacyDefinition(plan); err != nil || found {
		t.Fatalf("edited definition reported as legacy: %v %v", found, err)
	}
	if err := WriteServiceDefinition(plan); err == nil {
		t.Fatal("replaced a definition that is not a known Railwait rendering")
	}
	if data, _ := os.ReadFile(path); string(data) != string(edited) {
		t.Fatal("unknown definition modified")
	}
	plan.Definition = filepath.Join(filepath.Dir(path), "missing.xml")
	if found, err := HasLegacyDefinition(plan); err != nil || found {
		t.Fatalf("missing definition reported as legacy: %v %v", found, err)
	}
}

// TestServiceCommandHelper is not a test: ExecServiceRunner runs this binary as a
// stand-in service manager that prints the way schtasks does and fails.
func TestServiceCommandHelper(t *testing.T) {
	if os.Getenv("RAILWAIT_SERVICE_HELPER") != "1" {
		return
	}
	fmt.Fprint(os.Stderr, "ERROR: The task XML is malformed.\r\n\r\n(1,40)::\xbf\xc0\xb7\xf9: \x1b[31mencoding\r\n")
	os.Exit(1)
}

func TestExecServiceRunnerReportsServiceManagerMessage(t *testing.T) {
	t.Setenv("RAILWAIT_SERVICE_HELPER", "1")
	err := ExecServiceRunner{}.Run(context.Background(), platform.ServiceCommand{Program: os.Args[0], Args: []string{"-test.run=^TestServiceCommandHelper$"}})
	if err == nil {
		t.Fatal("failing service manager reported success")
	}
	message := err.Error()
	if !strings.Contains(message, "exit status 1") || !strings.Contains(message, "ERROR: The task XML is malformed. (1,40)::") {
		t.Fatalf("service manager message missing: %q", message)
	}
	if !utf8.ValidString(message) || strings.ContainsAny(message, "\r\n\x1b") {
		t.Fatalf("message must be one printable line: %q", message)
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
