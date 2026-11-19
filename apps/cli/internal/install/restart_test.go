package install

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/manager"
)

func gateClosed(t *testing.T, state State) bool {
	t.Helper()
	gate := string(mustRead(t, filepath.Join(state.Path, ".railwait", "manager", "control", "maintenance.json")))
	return strings.Contains(gate, `"closed":true`)
}

// touchesStorage reports a stop or recreate command that names PostgreSQL or Redis.
func touchesStorage(runner *updateRunner) bool {
	for _, c := range runner.commands {
		args := strings.Join(c.Args, " ")
		if (strings.Contains(args, " stop ") || strings.Contains(args, " up ")) && (strings.HasSuffix(args, " postgres") || strings.HasSuffix(args, " redis") || strings.Contains(args, " postgres ") || strings.Contains(args, " redis ")) {
			return true
		}
	}
	return false
}

func TestRestartDrainsAndRecreatesAppServicesWithoutBackupOrSettingsChange(t *testing.T) {
	i, runner, _, state := updateFixture(t)
	before := mustRead(t, filepath.Join(state.Path, ".env"))
	plan, err := i.PlanRestart(context.Background(), state.Path)
	if err != nil || !plan.Allowed {
		t.Fatalf("plan=%+v err=%v", plan, err)
	}
	if plan.TargetReleaseID != state.Release || strings.Join(plan.Steps, ",") != "checking,draining,applying,verifying,resuming" {
		t.Fatalf("plan: %+v", plan)
	}
	if commandsContaining(runner, "config --quiet") != 1 {
		t.Fatal("current .env not checked with Compose before planning")
	}
	if strings.Join(RestartServices(state), ",") != "api" {
		t.Fatal(RestartServices(state))
	}
	stages := []string{}
	result, err := i.Update(context.Background(), UpdateOptions{Path: state.Path, TargetReleaseID: state.Release, PlanID: plan.PlanID, Progress: func(stage string) error { stages = append(stages, stage); return nil }})
	if err != nil || result.State != "succeeded" || result.Stage != "succeeded" || result.TargetVersion != state.Release || result.CurrentVersion != state.Release {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	if strings.Join(stages, ",") != "checking,draining,applying,verifying,resuming" {
		t.Fatal(stages)
	}
	if commandsContaining(runner, "update-backup.sh") != 0 || commandsContaining(runner, "age-keygen") != 0 || commandsContaining(runner, " pull") != 0 {
		t.Fatal("restart took a backup or pulled images")
	}
	if commandsContaining(runner, "maintenance --status") == 0 || commandsContaining(runner, "stop --timeout 300 api") != 1 || commandsContaining(runner, "up -d --force-recreate --no-deps --wait --wait-timeout 180 api") != 1 {
		t.Fatal("restart did not drain, stop and recreate the app services")
	}
	if commandsContaining(runner, "--exit-code-from migration") != 1 || commandsContaining(runner, "--exit-code-from log-init") != 1 {
		t.Fatal("one-shot services did not run before the app services")
	}
	if touchesStorage(runner) {
		t.Fatal("restart stopped or recreated PostgreSQL or Redis")
	}
	if string(mustRead(t, filepath.Join(state.Path, ".env"))) != string(before) {
		t.Fatal("restart changed .env")
	}
	if gateClosed(t, state) {
		t.Fatal("gate left closed after a restart")
	}
	if _, err := os.Stat(filepath.Join(state.Path, ".railwait", "plans", plan.PlanID+".candidate.env")); !os.IsNotExist(err) {
		t.Fatal("restart plan wrote a settings candidate")
	}
}

func TestRestartRefusesAnInvalidEnvironmentBeforeAnythingStops(t *testing.T) {
	i, runner, _, state := updateFixture(t)
	runner.failure = "config --quiet"
	if _, err := i.PlanRestart(context.Background(), state.Path); rejectedCode(err) != "E_RESTART_ENV_INVALID" {
		t.Fatal(err)
	}
	if commandsContaining(runner, "--database-revision") != 0 || commandsContaining(runner, " stop ") != 0 || gateClosed(t, state) {
		t.Fatal("invalid .env reached the preflight or stopped services")
	}
	entries, _ := os.ReadDir(filepath.Join(state.Path, ".railwait", "plans"))
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), ".settings-check-") {
			t.Fatal("Compose check file retained")
		}
	}
}

func TestRestartIsUnavailableForAnIncompleteOrBlockedInstallation(t *testing.T) {
	i, _, _, state := updateFixture(t)
	request := manager.RestartRequest{Schema: 1, ID: strings.Repeat("1", 32), IdempotencyKey: strings.Repeat("a", 32), Scope: "app"}
	planned, err := i.PlanRestartRequest(context.Background(), state.Path, request)
	if err != nil || len(planned.PlanID) != 64 || planned.TargetReleaseID != state.Release || planned.ID != request.ID || planned.IdempotencyKey != request.IdempotencyKey {
		t.Fatalf("planned=%+v err=%v", planned, err)
	}
	if err := writeGate(filepath.Join(state.Path, ".railwait", "manager", "control"), true, "other-job"); err != nil {
		t.Fatal(err)
	}
	if _, err := i.PlanRestartRequest(context.Background(), state.Path, request); rejectedCode(err) != "E_RESTART_UNAVAILABLE" {
		t.Fatalf("closed gate: %v", err)
	}
	state.Stage = "starting"
	if err := writeState(state); err != nil {
		t.Fatal(err)
	}
	if _, err := i.PlanRestart(context.Background(), state.Path); rejectedCode(err) != "E_RESTART_UNAVAILABLE" {
		t.Fatalf("incomplete installation: %v", err)
	}
}

func TestFailedRestartStartsTheServicesAgainAndReopensTheGate(t *testing.T) {
	i, runner, _, state := updateFixture(t)
	plan, err := i.PlanRestart(context.Background(), state.Path)
	if err != nil || !plan.Allowed {
		t.Fatalf("plan=%+v err=%v", plan, err)
	}
	runner.failure, runner.failureLimit = "--exit-code-from migration", 1
	result, err := i.Update(context.Background(), UpdateOptions{Path: state.Path, TargetReleaseID: state.Release, PlanID: plan.PlanID})
	if err == nil || result.State != "failed" || result.Stage != "applying" {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	if commandsContaining(runner, "up -d --no-deps --wait --wait-timeout 180 api") != 1 {
		t.Fatal("stopped services were not started again")
	}
	if gateClosed(t, state) {
		t.Fatal("gate left closed although the services are healthy")
	}
}

func TestFailedRestartKeepsTheGateClosedWhileServicesStayDown(t *testing.T) {
	i, runner, _, state := updateFixture(t)
	plan, err := i.PlanRestart(context.Background(), state.Path)
	if err != nil || !plan.Allowed {
		t.Fatalf("plan=%+v err=%v", plan, err)
	}
	runner.failure = "--wait-timeout 180 api"
	result, err := i.Update(context.Background(), UpdateOptions{Path: state.Path, TargetReleaseID: state.Release, PlanID: plan.PlanID})
	if err == nil || result.State != "recovery_required" || result.Stage != "applying" {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	if !gateClosed(t, state) {
		t.Fatal("gate opened while the services could not start")
	}
	record, readErr := ReadUpdateJob(state.Path, result.JobID)
	if readErr != nil || record.State != "recovery_required" {
		t.Fatalf("job record: %+v %v", record, readErr)
	}
}

func TestDeferredRestartReopensTheGateWithoutStoppingAnything(t *testing.T) {
	i, runner, _, state := updateFixture(t)
	plan, err := i.PlanRestart(context.Background(), state.Path)
	if err != nil || !plan.Allowed {
		t.Fatalf("plan=%+v err=%v", plan, err)
	}
	runner.drainFailures = 1 << 30
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	result, err := i.Update(ctx, UpdateOptions{Path: state.Path, TargetReleaseID: state.Release, PlanID: plan.PlanID})
	if err == nil || result.State != "deferred" || result.Stage != "draining" {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	if commandsContaining(runner, " stop ") != 0 || commandsContaining(runner, " up ") != 0 {
		t.Fatal("deferred restart stopped or started services")
	}
	if gateClosed(t, state) {
		t.Fatal("gate left closed although nothing restarted")
	}
}
