package install

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/manager"
)

// restartKind is the plan kind that restarts the app services of the installed release
// with the current .env. It changes no settings and takes no backup.
const restartKind = "restart_services"

var (
	errRestartEnvInvalid  = manager.RejectedError{Code: "E_RESTART_ENV_INVALID"}
	errRestartUnavailable = manager.RejectedError{Code: "E_RESTART_UNAVAILABLE"}
)

// appServices lists the long-running app services that an update or an app settings change
// stops and recreates. PostgreSQL and Redis keep running; migration and log-init are
// one-shot services that run before the app services start again.
func appServices(images map[string]string) []string {
	services := []string{}
	for name := range images {
		if name != "postgres" && name != "redis" && name != "migration" && name != "log-init" {
			services = append(services, name)
		}
	}
	sort.Strings(services)
	return services
}

// RestartServices lists the services a restart recreates, for a summary before it runs.
func RestartServices(s State) []string { return appServices(s.ExpectedImages) }

// PlanRestart checks the current .env with the same Compose check a settings change gives
// its candidate, then saves a restart_services plan for the installed release. It returns
// manager.RejectedError E_RESTART_UNAVAILABLE for an installation that is not complete and
// E_RESTART_ENV_INVALID when Compose refuses .env. Unhealthy services and low free space do
// not block a restart; a plan the rest of the preflight blocks, for example while another
// job holds the gate closed or the service status cannot be read, has Allowed false.
func (i *Installer) PlanRestart(ctx context.Context, path string) (UpdatePlan, error) {
	s, err := ReadState(path)
	if err != nil {
		return UpdatePlan{}, err
	}
	blank := UpdatePlan{CurrentReleaseID: s.Release, TargetReleaseID: s.Release}
	if s.Stage != "complete" || s.ComposeFile == "" {
		return blank, errRestartUnavailable
	}
	env, err := readSettingsEnv(s.Path)
	if err != nil {
		return blank, errRestartEnvInvalid
	}
	if err = i.checkSettingsCompose(ctx, s, env); err != nil {
		return blank, errRestartEnvInvalid
	}
	r := installedRelease(s)
	plan, err := i.makePlanMode(ctx, s, r, planRestart)
	if err != nil {
		return plan, err
	}
	plan.Steps = []string{"checking", "draining", "applying", "verifying", "resuming"}
	return savePlan(s, plan, r.SHA256, restartKind, nil)
}

// PlanRestartRequest is the manager's restart planner: it turns an authenticated restart
// request into the ordinary request that runs the saved plan.
func (i *Installer) PlanRestartRequest(ctx context.Context, path string, request manager.RestartRequest) (manager.Request, error) {
	plan, err := i.PlanRestart(ctx, path)
	planned := manager.Request{Schema: 1, ID: request.ID, TargetReleaseID: plan.TargetReleaseID, IdempotencyKey: request.IdempotencyKey}
	if err != nil {
		return planned, err
	}
	if !plan.Allowed {
		return planned, errRestartUnavailable
	}
	planned.PlanID = plan.PlanID
	return planned, nil
}

// reopenAfterRestart ends a failed restart. A restart changes no release, setting or data, so
// the gate it closed protects nothing and always opens again: services it had begun to stop
// are started once more, and a start or health problem is returned for the job record rather
// than kept behind a closed gate. It reports false only when the gate itself could not be
// written, which leaves the job for recovery.
func (i *Installer) reopenAfterRestart(ctx context.Context, s State, controlDir, generation string, services []string, stopped bool) (bool, error) {
	recoverCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Minute)
	defer cancel()
	var problems []error
	if stopped {
		if _, err := i.compose(recoverCtx, s, nil, append([]string{"up", "-d", "--no-deps", "--wait", "--wait-timeout", "180"}, services...)...); err != nil {
			problems = append(problems, fmt.Errorf("멈춘 앱 서비스를 다시 시작하지 못했습니다: %w", err))
		}
	}
	if len(problems) == 0 {
		if status, err := i.statusState(recoverCtx, s); err != nil {
			problems = append(problems, fmt.Errorf("서비스 상태를 확인하지 못했습니다: %w", err))
		} else if !status.Healthy {
			problems = append(problems, errors.New("서비스가 아직 정상 상태가 아닙니다"))
		}
	}
	if err := writeGate(controlDir, false, generation); err != nil {
		return false, errors.Join(append(problems, fmt.Errorf("새 작업 차단을 풀지 못해 유지보수 상태가 남았습니다: %w", err))...)
	}
	return true, errors.Join(problems...)
}
