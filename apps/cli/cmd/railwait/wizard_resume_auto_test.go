package main

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

type fakeWizardResumeRegistry struct {
	values  map[string]string
	failSet bool
	deletes int
	onSet   func()
}

func (r *fakeWizardResumeRegistry) Get(name string) (string, error) { return r.values[name], nil }
func (r *fakeWizardResumeRegistry) Set(name, value string) error {
	if r.failSet {
		return errors.New("fixture")
	}
	r.values[name] = value
	if r.onSet != nil {
		r.onSet()
	}
	return nil
}
func (r *fakeWizardResumeRegistry) Delete(name string) error {
	delete(r.values, name)
	r.deletes++
	return nil
}
func TestWizardResumePreservesOptionsAndConsumesOnce(t *testing.T) {
	dir := resumeTestDirectory(t)
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	o := wizardOptions{Path: filepath.Join(t.TempDir(), "app path & space"), DockerContext: "desktop-linux", Package: filepath.Join(t.TempDir(), "package"), TrustKey: filepath.Join(t.TempDir(), "trust.pub"), Access: "tailscale", Port: 18080, HTTPSPort: 8443, Plain: true, Yes: true, Open: true, Custom: true}
	registry := &fakeWizardResumeRegistry{values: map[string]string{}}
	if err := scheduleWizardResumeWith(context.Background(), o, exe, dir, registry); err != nil {
		t.Fatal(err)
	}
	statePath := filepath.Join(dir, resumeName(o.Path)+".json")
	if len(registry.values) != 1 {
		t.Fatal("resume not registered exactly once")
	}
	command := registry.values[resumeName(o.Path)]
	if !strings.Contains(command, "--resume-setup") || strings.Contains(command, "desktop-linux") || strings.Contains(command, "tailscale") {
		t.Fatal("options exposed in registry")
	}
	next, err := consumeWizardResumeWith(statePath, dir, registry)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(next, wizardResumeArgs(o)) {
		t.Fatal("options changed")
	}
	if len(registry.values) != 0 {
		t.Fatal("persistent login entry retained")
	}
	if _, err := os.Stat(statePath); !os.IsNotExist(err) {
		t.Fatal("state retained")
	}
	if _, err := consumeWizardResumeWith(statePath, dir, registry); err == nil {
		t.Fatal("state replayed")
	}
	entries, err := os.ReadDir(dir)
	if err != nil || !onlyWizardResumeLock(entries) {
		t.Fatal("temporary resume files retained")
	}
}
func TestWizardResumeRejectsStdinUnsafeAndLongCommands(t *testing.T) {
	registry := &fakeWizardResumeRegistry{values: map[string]string{}}
	if err := scheduleWizardResumeWith(context.Background(), wizardOptions{Input: "-"}, "", t.TempDir(), registry); err == nil {
		t.Fatal("stdin persisted")
	}
	for _, path := range []string{"relative.exe", filepath.Join(t.TempDir(), "injected\".exe"), filepath.Join(t.TempDir(), strings.Repeat("a", 270)+".exe")} {
		if _, err := resumeCommand(path, filepath.Join(t.TempDir(), "resume.json")); err == nil {
			t.Fatal("unsafe command accepted")
		}
	}
	if len(registry.values) != 0 {
		t.Fatal("invalid request registered")
	}
}
func TestWizardResumeRegistrationFailureCleansState(t *testing.T) {
	dir := resumeTestDirectory(t)
	exe, _ := os.Executable()
	registry := &fakeWizardResumeRegistry{values: map[string]string{}, failSet: true}
	if err := scheduleWizardResumeWith(context.Background(), wizardOptions{Path: t.TempDir()}, exe, dir, registry); err == nil {
		t.Fatal("registration failure hidden")
	}
	entries, err := os.ReadDir(dir)
	if err != nil || !onlyWizardResumeLock(entries) {
		t.Fatal("failed registration retained state")
	}
}
func TestWizardResumeRejectsForgedStateAndPreservesUnrelatedRegistration(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "RailwaitSetup-0123456789abcdef.json")
	registry := &fakeWizardResumeRegistry{values: map[string]string{"RailwaitSetup-0123456789abcdef": "fixture"}}
	if err := os.WriteFile(path, []byte(`{"schema":1,"options":{"Path":"forged"}}`), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := consumeWizardResumeWith(path, dir, registry); err == nil {
		t.Fatal("forged state accepted")
	}
	if len(registry.values) != 1 {
		t.Fatal("unrelated registration changed")
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatal("unrelated state removed")
	}
	outside := filepath.Join(t.TempDir(), "RailwaitSetup-0123456789abcdef.json")
	os.WriteFile(outside, []byte("preserve"), 0600)
	if _, err := consumeWizardResumeWith(outside, dir, registry); err == nil {
		t.Fatal("external path accepted")
	}
	if _, err := os.Stat(outside); err != nil {
		t.Fatal("external file touched")
	}
}

func TestWizardResumeKeepsProtectedInputOutOfStateAndRegistry(t *testing.T) {
	dir := resumeTestDirectory(t)
	exe, _ := os.Executable()
	input := filepath.Join(t.TempDir(), "input.json")
	if err := os.WriteFile(input, []byte(`{"username":"fixture","password":"resume-secret-canary","tago_service_key":"external-secret-canary"}`), 0600); err != nil {
		t.Fatal(err)
	}
	o := wizardOptions{Path: t.TempDir(), Input: input, Access: "local"}
	registry := &fakeWizardResumeRegistry{values: map[string]string{}}
	if err := scheduleWizardResumeWith(context.Background(), o, exe, dir, registry); err != nil {
		t.Fatal(err)
	}
	statePath := filepath.Join(dir, resumeName(o.Path)+".json")
	state, err := os.ReadFile(statePath)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(state), "secret-canary") || strings.Contains(registry.values[resumeName(o.Path)], "secret-canary") {
		t.Fatal("secret persisted")
	}
	if _, err := consumeWizardResumeWith(statePath, dir, registry); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(input); err != nil {
		t.Fatal("original protected input removed")
	}
}

func TestWizardShellCommandEscapesForPowerShellAndPOSIX(t *testing.T) {
	command, err := wizardShellCommand("windows", `C:\Users\O'Brien\railwait.exe`, "install", "--path", `C:\App & Data\It's here`)
	if err != nil || command != `& 'C:\Users\O''Brien\railwait.exe' 'install' '--path' 'C:\App & Data\It''s here'` {
		t.Fatal("PowerShell quoting changed")
	}
	command, err = wizardShellCommand("darwin", "/Users/O'Brien/railwait", "install", "--path", "/App & Data/It's here")
	if err != nil || command != `'/Users/O'"'"'Brien/railwait' 'install' '--path' '/App & Data/It'"'"'s here'` {
		t.Fatal("POSIX quoting changed")
	}
	for _, value := range []string{"line\nbreak", "line\rbreak", "escape\x1b", "format\u202e", string([]byte{0xff})} {
		if _, err := wizardShellCommand("windows", "railwait", "--path", value); err == nil {
			t.Fatal("unsafe display command accepted")
		}
	}
}

func resumeTestDirectory(t *testing.T) string {
	t.Helper()
	dir, err := os.MkdirTemp("", "railwait-resume-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.RemoveAll(dir); err != nil {
			t.Errorf("resume test cleanup: %v", err)
		}
	})
	return dir
}

func TestWizardResumeCancellationNeverLeavesLoginAction(t *testing.T) {
	for _, duringSet := range []bool{false, true} {
		t.Run(map[bool]string{false: "already-canceled", true: "during-registration"}[duringSet], func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			dir := resumeTestDirectory(t)
			exe, _ := os.Executable()
			registry := &fakeWizardResumeRegistry{values: map[string]string{}}
			if duringSet {
				registry.onSet = cancel
			} else {
				cancel()
			}
			err := scheduleWizardResumeWith(ctx, wizardOptions{Path: t.TempDir()}, exe, dir, registry)
			if !errors.Is(err, context.Canceled) {
				t.Fatal("cancellation lost", err)
			}
			if len(registry.values) != 0 {
				t.Fatal("canceled install retained login action")
			}
			entries, err := os.ReadDir(dir)
			if err != nil || !onlyWizardResumeLock(entries) {
				t.Fatal("canceled install retained state")
			}
		})
	}
}

type cancelBeforeResumeRegistration struct {
	context.Context
	checks           int
	preparedPath     string
	sawPreparedState bool
}

func (c *cancelBeforeResumeRegistration) Err() error {
	c.checks++
	if c.checks >= 3 {
		if _, err := os.Stat(c.preparedPath); err == nil {
			c.sawPreparedState = true
		}
		return context.Canceled
	}
	return nil
}
func TestWizardResumeCancellationBeforeRegistrySetRemovesPreparedState(t *testing.T) {
	ctx := &cancelBeforeResumeRegistration{Context: context.Background()}
	dir := resumeTestDirectory(t)
	exe, _ := os.Executable()
	registry := &fakeWizardResumeRegistry{values: map[string]string{}}
	o := wizardOptions{Path: t.TempDir()}
	ctx.preparedPath = filepath.Join(dir, resumeName(o.Path)+".json")
	err := scheduleWizardResumeWith(ctx, o, exe, dir, registry)
	if !ctx.sawPreparedState {
		t.Fatal("test did not cancel after state preparation")
	}
	if !errors.Is(err, context.Canceled) || len(registry.values) != 0 || registry.deletes != 0 {
		t.Fatal("pre-registration cancellation changed registry", err)
	}
	entries, err := os.ReadDir(dir)
	if err != nil || !onlyWizardResumeLock(entries) {
		t.Fatal("prepared state retained after cancellation")
	}
}

func onlyWizardResumeLock(entries []os.DirEntry) bool {
	return len(entries) == 0 || len(entries) == 1 && entries[0].Name() == "resume.lock"
}
func TestWizardResumePreservesUnrelatedStateAndExistingReservation(t *testing.T) {
	dir := resumeTestDirectory(t)
	exe, _ := os.Executable()
	o := wizardOptions{Path: t.TempDir(), Access: "local"}
	path := filepath.Join(dir, resumeName(o.Path)+".json")
	registry := &fakeWizardResumeRegistry{values: map[string]string{}}
	unrelated := []byte("unrelated existing file")
	if err := os.WriteFile(path, unrelated, 0600); err != nil {
		t.Fatal(err)
	}
	if err := scheduleWizardResumeWith(context.Background(), o, exe, dir, registry); err == nil {
		t.Fatal("unrelated state overwritten")
	}
	after, _ := os.ReadFile(path)
	if string(after) != string(unrelated) {
		t.Fatal("unrelated state changed")
	}
	valid, _ := json.Marshal(wizardResumeState{Schema: 1, Options: o})
	if err := os.WriteFile(path, valid, 0600); err != nil {
		t.Fatal(err)
	}
	registry.failSet = true
	if err := scheduleWizardResumeWith(context.Background(), o, exe, dir, registry); err == nil {
		t.Fatal("registration failure hidden")
	}
	after, _ = os.ReadFile(path)
	if string(after) != string(valid) {
		t.Fatal("preexisting valid state removed after Set failure")
	}
	registry.failSet = false
	if err := scheduleWizardResumeWith(context.Background(), o, exe, dir, registry); err != nil {
		t.Fatal(err)
	}
	command := registry.values[resumeName(o.Path)]
	registry.failSet = true // Idempotent calls must not rewrite an existing login entry.
	if err := scheduleWizardResumeWith(context.Background(), o, exe, dir, registry); err != nil {
		t.Fatal("existing reservation not reused", err)
	}
	changed := o
	changed.Port = 19090
	if err := scheduleWizardResumeWith(context.Background(), changed, exe, dir, registry); err == nil {
		t.Fatal("existing options replaced")
	}
	if registry.values[resumeName(o.Path)] != command {
		t.Fatal("existing login command changed")
	}
	after, _ = os.ReadFile(path)
	if string(after) != string(valid) {
		t.Fatal("existing reservation changed")
	}
	registry.values[resumeName(o.Path)] = "unrelated command"
	if err := scheduleWizardResumeWith(context.Background(), o, exe, dir, registry); err == nil {
		t.Fatal("registry collision overwritten")
	}
	if registry.values[resumeName(o.Path)] != "unrelated command" {
		t.Fatal("registry collision changed")
	}
}
func TestWizardResumeSharedLockPreventsCompetingScheduleAndConsume(t *testing.T) {
	dir := resumeTestDirectory(t)
	exe, _ := os.Executable()
	o := wizardOptions{Path: t.TempDir(), Access: "local"}
	registry := &fakeWizardResumeRegistry{values: map[string]string{}}
	lock, err := platform.AcquireLock(filepath.Join(dir, "resume.lock"))
	if err != nil {
		t.Fatal(err)
	}
	if err := scheduleWizardResumeWith(context.Background(), o, exe, dir, registry); !errors.Is(err, platform.ErrLocked) {
		t.Fatal("schedule ignored shared lock", err)
	}
	path := filepath.Join(dir, resumeName(o.Path)+".json")
	if _, err := consumeWizardResumeWith(path, dir, registry); !errors.Is(err, platform.ErrLocked) {
		t.Fatal("consume ignored shared lock", err)
	}
	if len(registry.values) != 0 {
		t.Fatal("blocked scheduler changed registry")
	}
	if err := lock.Close(); err != nil {
		t.Fatal(err)
	}
	if err := scheduleWizardResumeWith(context.Background(), o, exe, dir, registry); err != nil {
		t.Fatal("schedule failed after release", err)
	}
	if _, err := consumeWizardResumeWith(path, dir, registry); err != nil {
		t.Fatal("consume failed after release", err)
	}
}

func TestWizardResumeConsumesMatchingLoginActionButPreservesMalformedState(t *testing.T) {
	dir := resumeTestDirectory(t)
	exe, _ := os.Executable()
	name := resumeName(t.TempDir())
	path := filepath.Join(dir, name+".json")
	malformed := []byte("unrelated or malformed state")
	if err := os.WriteFile(path, malformed, 0600); err != nil {
		t.Fatal(err)
	}
	command, err := resumeCommand(exe, path)
	if err != nil {
		t.Fatal(err)
	}
	registry := &fakeWizardResumeRegistry{values: map[string]string{name: command}}
	if _, err := consumeWizardResumeWith(path, dir, registry); err == nil {
		t.Fatal("malformed state accepted")
	}
	if len(registry.values) != 0 {
		t.Fatal("matching action retained and will repeat on login")
	}
	after, err := os.ReadFile(path)
	if err != nil || string(after) != string(malformed) {
		t.Fatal("malformed original state changed")
	}
}

func TestWizardResumeRejectsOversizedPayloadBeforeRegistration(t *testing.T) {
	dir := resumeTestDirectory(t)
	exe, _ := os.Executable()
	registry := &fakeWizardResumeRegistry{values: map[string]string{}}
	o := wizardOptions{Path: t.TempDir(), DockerContext: strings.Repeat("x", 32768)}
	if err := scheduleWizardResumeWith(context.Background(), o, exe, dir, registry); err == nil {
		t.Fatal("unreadable oversized payload registered")
	}
	if len(registry.values) != 0 || registry.deletes != 0 {
		t.Fatal("oversized payload changed registry")
	}
	entries, err := os.ReadDir(dir)
	if err != nil || !onlyWizardResumeLock(entries) {
		t.Fatal("oversized payload wrote resume state")
	}
}
