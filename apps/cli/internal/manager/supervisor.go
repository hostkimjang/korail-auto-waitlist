package manager

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

type ServiceRunner interface {
	Run(context.Context, platform.ServiceCommand) error
}
type ExecServiceRunner struct{}

func (ExecServiceRunner) Run(ctx context.Context, command platform.ServiceCommand) error {
	cmd := exec.CommandContext(ctx, command.Program, command.Args...)
	cmd.Stdout = io.Discard
	cmd.Stderr = io.Discard
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("user service command failed: %w", err)
	}
	return nil
}

// InstallService writes only this installation's deterministic service definition.
// Existing differing definitions require explicit review instead of silent replacement.
// Success means registration/start commands succeeded; use Available to verify the daemon.
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
	if err := WriteServiceDefinition(plan); err != nil {
		return plan, err
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

// WriteServiceDefinition is separate so installers can display/review a rendered
// plan and tests can validate it without touching real host service managers.
func WriteServiceDefinition(plan platform.ServicePlan) error {
	if len(plan.Content) == 0 || plan.Definition == "" {
		return errors.New("service definition is required")
	}
	if data, err := readBounded(plan.Definition, 64<<10); err == nil {
		if !bytes.Equal(data, plan.Content) {
			return errors.New("existing service definition differs; review it before replacing")
		}
		return nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(plan.Definition), 0o755); err != nil {
		return err
	}
	return writeFile(plan.Definition, plan.Content, 0o600)
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
