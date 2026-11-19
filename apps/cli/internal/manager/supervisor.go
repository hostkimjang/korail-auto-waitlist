package manager

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
	"unicode"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

type ServiceRunner interface {
	Run(context.Context, platform.ServiceCommand) error
}
type ExecServiceRunner struct{}

// serviceOutputLimit bounds what a host service manager may print into memory.
// Only a short summary of it is shown; it never contains Railwait secrets.
const serviceOutputLimit = 4 << 10

func (ExecServiceRunner) Run(ctx context.Context, command platform.ServiceCommand) error {
	cmd := exec.CommandContext(ctx, command.Program, command.Args...)
	output := &boundedOutput{limit: serviceOutputLimit}
	cmd.Stdout = output
	cmd.Stderr = output
	if err := cmd.Run(); err != nil {
		// A bare "exit status 1" hid why schtasks rejected the Windows task. The
		// service manager's own message is the actionable part of the failure.
		if detail := serviceOutputSummary(output.Bytes()); detail != "" {
			return fmt.Errorf("user service command failed: %s: %w: %s", command.Program, err, detail)
		}
		return fmt.Errorf("user service command failed: %s: %w", command.Program, err)
	}
	return nil
}

type boundedOutput struct {
	bytes.Buffer
	limit int
}

func (b *boundedOutput) Write(p []byte) (int, error) {
	if room := b.limit - b.Len(); room > 0 {
		if len(p) > room {
			b.Buffer.Write(p[:room])
		} else {
			b.Buffer.Write(p)
		}
	}
	return len(p), nil
}

// serviceOutputSummary keeps one readable line. Windows tools print in the
// console code page, so bytes that are not UTF-8 become "?" instead of mojibake.
func serviceOutputSummary(data []byte) string {
	text := strings.ToValidUTF8(string(data), "?")
	text = strings.Map(func(r rune) rune {
		if unicode.IsControl(r) || unicode.Is(unicode.Cf, r) {
			return ' '
		}
		return r
	}, text)
	text = strings.Join(strings.Fields(text), " ")
	if runes := []rune(text); len(runes) > 240 {
		text = string(runes[:240]) + "…"
	}
	return text
}

// InstallService writes only this installation's deterministic service definition.
// Existing differing definitions require explicit review instead of silent replacement,
// except one that differs only in the PATH of the shell that wrote it: that one is
// registered and started as written. Success means registration/start commands
// succeeded; use Available to verify the daemon.
func InstallService(ctx context.Context, installation, executable string, runner ServiceRunner) (platform.ServicePlan, error) {
	if runner == nil {
		runner = ExecServiceRunner{}
	}
	abs, err := filepath.Abs(installation)
	if err != nil {
		return platform.ServicePlan{}, err
	}
	exe, err := filepath.Abs(executable)
	if err != nil {
		return platform.ServicePlan{}, err
	}
	if err := ensureRegular(exe); err != nil {
		return platform.ServicePlan{}, err
	}
	if _, err := Prepare(abs); err != nil {
		return platform.ServicePlan{}, err
	}
	plan, err := platform.CurrentServicePlan(exe, abs)
	if err != nil {
		return plan, err
	}
	state, err := InspectServiceDefinition(plan)
	if err != nil {
		return plan, err
	}
	// A definition written from another shell is kept as written. Rewriting it would also
	// replace the PATH the user's own terminal gave the manager, possibly with one that
	// names this process's temporary tool folders.
	if state != DefinitionOtherPath {
		if err := WriteServiceDefinition(plan); err != nil {
			return plan, err
		}
	}
	// launchctl bootstrap rejects an already-loaded label. A loaded agent can
	// still lack a live heartbeat, so kickstart remains a separate operation.
	registered := plan.OS == "darwin" && runner.Run(ctx, plan.Status) == nil
	if !registered {
		for _, command := range plan.Register {
			if err := runner.Run(ctx, command); err != nil {
				return plan, err
			}
		}
	}
	if err := runner.Run(ctx, plan.Start); err != nil {
		return plan, err
	}
	return plan, nil
}

// DefinitionState says how the service definition on disk relates to this CLI's rendering.
type DefinitionState int

const (
	DefinitionMissing DefinitionState = iota
	// DefinitionCurrent is identical to this CLI's rendering.
	DefinitionCurrent
	// DefinitionLegacy is an earlier Railwait rendering that the OS rejected.
	DefinitionLegacy
	// DefinitionOtherPath is this rendering with the PATH of another shell.
	DefinitionOtherPath
	// DefinitionUnknown was not written by this Railwait, for example after an edit by hand.
	DefinitionUnknown
)

// InspectServiceDefinition classifies the definition at plan.Definition. An installer
// replaces only missing and legacy definitions and never an unknown one.
func InspectServiceDefinition(plan platform.ServicePlan) (DefinitionState, error) {
	if plan.Definition == "" {
		return DefinitionUnknown, errors.New("service definition is required")
	}
	data, err := readBounded(plan.Definition, 64<<10)
	if errors.Is(err, os.ErrNotExist) {
		return DefinitionMissing, nil
	}
	if err != nil {
		return DefinitionUnknown, err
	}
	switch {
	case bytes.Equal(data, plan.Content):
		return DefinitionCurrent, nil
	case matchesLegacyDefinition(data, plan.Legacy):
		return DefinitionLegacy, nil
	case len(plan.PathFree) > 0 && bytes.Equal(platform.WithoutServicePath(plan.OS, data), plan.PathFree):
		return DefinitionOtherPath, nil
	}
	return DefinitionUnknown, nil
}

// WriteServiceDefinition is separate so installers can display/review a rendered
// plan and tests can validate it without touching real host service managers.
func WriteServiceDefinition(plan platform.ServicePlan) error {
	if len(plan.Content) == 0 || plan.Definition == "" {
		return errors.New("service definition is required")
	}
	if data, err := readBounded(plan.Definition, 64<<10); err == nil {
		if bytes.Equal(data, plan.Content) {
			return nil
		}
		if !matchesLegacyDefinition(data, plan.Legacy) {
			return errors.New("existing service definition differs; review it before replacing")
		}
		// An earlier Railwait rendering of this same service is replaced in place.
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(plan.Definition), 0o755); err != nil {
		return err
	}
	return writeFile(plan.Definition, plan.Content, 0o600)
}

func matchesLegacyDefinition(data []byte, legacy [][]byte) bool {
	for _, candidate := range legacy {
		if len(candidate) > 0 && bytes.Equal(data, candidate) {
			return true
		}
	}
	return false
}

// HasLegacyDefinition reports whether the definition on disk is an earlier Railwait
// rendering that the OS rejected, such as the 1.4.5 Windows task. The manager
// executable it names was then never started as a service.
func HasLegacyDefinition(plan platform.ServicePlan) (bool, error) {
	if plan.Definition == "" || len(plan.Legacy) == 0 {
		return false, nil
	}
	data, err := readBounded(plan.Definition, 64<<10)
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return matchesLegacyDefinition(data, plan.Legacy), nil
}

func StartService(ctx context.Context, installation, executable string, runner ServiceRunner) error {
	if runner == nil {
		runner = ExecServiceRunner{}
	}
	plan, err := platform.CurrentServicePlan(executable, installation)
	if err != nil {
		return err
	}
	return runner.Run(ctx, plan.Start)
}

func StopService(ctx context.Context, installation, executable string, runner ServiceRunner) error {
	if runner == nil {
		runner = ExecServiceRunner{}
	}
	plan, err := platform.CurrentServicePlan(executable, installation)
	if err != nil {
		return err
	}
	return runner.Run(ctx, plan.Stop)
}

// UnregisterService stops this installation's manager service and removes its registration
// and definition file, for uninstall. A service that is already gone is left alone, so an
// interrupted uninstall can run again. An unregister command may fail when part of the
// service is already gone; the service manager's status afterwards decides the outcome.
func UnregisterService(ctx context.Context, plan platform.ServicePlan, runner ServiceRunner) error {
	if runner == nil {
		runner = ExecServiceRunner{}
	}
	if plan.Definition == "" || len(plan.Unregister) == 0 {
		return errors.New("service unregistration is required")
	}
	_, statErr := os.Lstat(plan.Definition)
	if statErr != nil && !errors.Is(statErr, os.ErrNotExist) {
		return statErr
	}
	defined := statErr == nil
	if !defined && runner.Run(ctx, plan.Status) != nil {
		return nil
	}
	var failures []error
	for _, command := range plan.Unregister {
		if err := runner.Run(ctx, command); err != nil {
			failures = append(failures, err)
		}
	}
	if !waitUnregistered(ctx, runner, plan.Status, unregisterWait) {
		return errors.Join(append([]error{errors.New("관리 서비스가 아직 등록되어 있거나 실행 중입니다")}, failures...)...)
	}
	if defined {
		if err := ensureRegular(plan.Definition); err != nil {
			return err
		}
		if err := os.Remove(plan.Definition); err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
	}
	for _, command := range plan.AfterRemoval {
		if err := runner.Run(ctx, command); err != nil {
			return err
		}
	}
	return nil
}

// unregisterWait bounds how long a service manager may take to drop a service after the
// unregister commands return. launchctl bootout returns while launchd is still removing the
// job, so a status check right after it can still find the service.
var unregisterWait = 15 * time.Second

// unregisterPoll is how often the status is checked while waiting.
var unregisterPoll = 200 * time.Millisecond

// waitUnregistered reports whether the status command stops finding the service within wait.
func waitUnregistered(ctx context.Context, runner ServiceRunner, status platform.ServiceCommand, wait time.Duration) bool {
	deadline := time.Now().Add(wait)
	for {
		if runner.Run(ctx, status) != nil {
			return true
		}
		if !time.Now().Before(deadline) {
			return false
		}
		timer := time.NewTimer(unregisterPoll)
		select {
		case <-ctx.Done():
			timer.Stop()
			return false
		case <-timer.C:
		}
	}
}

// WaitAvailable checks the live heartbeat, not merely a registered OS job.
func WaitAvailable(ctx context.Context, installation string) error {
	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	for {
		if Available(installation, time.Now()) {
			return nil
		}
		select {
		case <-ctx.Done():
			return fmt.Errorf("manager heartbeat unavailable: %w", ctx.Err())
		case <-ticker.C:
		}
	}
}
