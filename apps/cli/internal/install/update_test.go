package install

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/manager"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/release"
)

type updateSource struct{ target release.Release }

func (s *updateSource) Resolve(context.Context, string) (release.Release, error) {
	return s.target, nil
}
func (s *updateSource) DownloadAndExtract(ctx context.Context, r release.Release, path string) error {
	return (&fakeReleases{}).DownloadAndExtract(ctx, r, path)
}

type updateRunner struct {
	base          *fakeRunner
	state         State
	failure       string
	failureLimit  int
	failed        int
	drainRevision string
	commands      []Command
	inputs        []string
	drainFailures int
	// unhealthy names a service that ps reports unhealthy until a command containing healedBy
	// succeeds.
	unhealthy string
	healedBy  string
}

func (r *updateRunner) Run(ctx context.Context, c Command) ([]byte, error) {
	r.commands = append(r.commands, c)
	args := strings.Join(c.Args, " ")
	if strings.Contains(args, "--database-revision") {
		return []byte(`{"schema":1,"database_revision":"` + r.drainRevision + `"}`), nil
	}
	if strings.Contains(args, "rail_waitlist.maintenance --status") {
		if r.drainFailures > 0 {
			r.drainFailures--
			return nil, fmt.Errorf("transient shared filesystem visibility")
		}
		data, err := os.ReadFile(filepath.Join(r.state.Path, ".railwait", "manager", "control", "maintenance.json"))
		if err != nil {
			return nil, err
		}
		var gate map[string]any
		json.Unmarshal(data, &gate)
		gate["drained"] = true
		return json.Marshal(gate)
	}
	if r.failure != "" && strings.Contains(args, r.failure) && (r.failureLimit == 0 || r.failed < r.failureLimit) {
		r.failed++
		return nil, fmt.Errorf("injected update failure")
	}
	if strings.Contains(args, "ps --all --format") {
		services := []map[string]any{}
		for name := range r.state.ExpectedImages {
			state, health := "running", "healthy"
			if name == "migration" || name == "log-init" {
				state = "exited"
				health = ""
			} else if name == r.unhealthy {
				health = "unhealthy"
			}
			services = append(services, map[string]any{"Service": name, "State": state, "Health": health, "ExitCode": 0})
		}
		return json.Marshal(services)
	}
	if strings.Contains(args, "ps --all --quiet") {
		return []byte(strings.Repeat("b", 64)), nil
	}
	if strings.Contains(args, "inspect --format") {
		var out strings.Builder
		names := []string{}
		for name := range r.state.ExpectedImages {
			names = append(names, name)
		}
		sort.Strings(names)
		for _, name := range names {
			labels, _ := json.Marshal(map[string]string{"com.docker.compose.service": name, "com.docker.compose.project": r.state.Project})
			image, _ := json.Marshal(r.state.ExpectedImages[name])
			fmt.Fprintf(&out, "%s %s\n", labels, image)
		}
		return []byte(out.String()), nil
	}
	if strings.Contains(args, "update-backup.sh") {
		if c.Input == nil {
			return nil, fmt.Errorf("missing settings backup stdin")
		}
		data, err := io.ReadAll(c.Input)
		if err != nil {
			return nil, err
		}
		r.inputs = append(r.inputs, string(data))
		return []byte(`{"status":"verified"}`), nil
	}
	if r.healedBy != "" && strings.Contains(args, r.healedBy) {
		r.unhealthy = ""
	}
	return r.base.Run(ctx, c)
}

func updateFixture(t *testing.T) (*Installer, *updateRunner, *updateSource, State) {
	t.Helper()
	root := filepath.Join(t.TempDir(), "installation")
	base := &fakeRunner{configured: true}
	initial := testInstaller(base, &fakeReleases{})
	state, err := initial.Install(context.Background(), Options{Path: root})
	if err != nil {
		t.Fatal(err)
	}
	state.DatabaseRevision = "rev1"
	state.SettingsSchema = 1
	state.ExpectedImages = map[string]string{"api": "ghcr.io/example/api@sha256:" + strings.Repeat("a", 64), "postgres": "postgres@sha256:" + strings.Repeat("d", 64), "redis": "redis@sha256:" + strings.Repeat("e", 64), "migration": "ghcr.io/example/api@sha256:" + strings.Repeat("a", 64), "log-init": "alpine@sha256:" + strings.Repeat("f", 64)}
	if err = writeState(state); err != nil {
		t.Fatal(err)
	}
	p, err := manager.Prepare(root)
	if err != nil {
		t.Fatal(err)
	}
	if err = manager.WriteStatusFile(filepath.Join(p.Status, "manager.json"), manager.Heartbeat{Schema: 1, State: "ready", PID: os.Getpid(), StartedAt: time.Now().UTC().Format(time.RFC3339Nano), HeartbeatAt: time.Now().UTC().Format(time.RFC3339Nano)}); err != nil {
		t.Fatal(err)
	}
	if err = atomicWrite(filepath.Join(root, ".railwait", "backup.env"), []byte("BACKUP_AGE_IDENTITY_BASE64='dGVzdA=='\nBACKUP_AGE_RECIPIENT='age1test'\n")); err != nil {
		t.Fatal(err)
	}
	runner := &updateRunner{base: base, state: state, drainRevision: "rev1"}
	source := &updateSource{target: release.Release{Version: "1.2.4", SHA256: strings.Repeat("c", 64), Images: state.ExpectedImages, DatabaseRevision: "rev1", SettingsSchema: 1, CompatibleFrom: []string{state.Release}}}
	i := &Installer{Runner: runner, Releases: source, CheckSpace: func(string) error { return nil }, ProbeInterval: time.Millisecond}
	return i, runner, source, state
}

func TestUpdateSameSchemaDrainsBacksUpAndVerifiesBeforeResuming(t *testing.T) {
	i, runner, _, state := updateFixture(t)
	plan, err := i.PlanUpdate(context.Background(), state.Path)
	if err != nil {
		t.Fatal(err)
	}
	if !plan.Allowed {
		t.Fatal(plan.BlockedReasons)
	}
	stages := []string{}
	result, err := i.Update(context.Background(), UpdateOptions{Path: state.Path, TargetReleaseID: plan.TargetReleaseID, PlanID: plan.PlanID, Progress: func(stage string) error { stages = append(stages, stage); return nil }})
	if err != nil {
		t.Fatal(err)
	}
	if result.State != "succeeded" {
		t.Fatal(result)
	}
	updated, err := ReadState(state.Path)
	if err != nil {
		t.Fatal(err)
	}
	if updated.Release != "1.2.4" || updated.DatabaseRevision != "rev1" {
		t.Fatal(updated.Release)
	}
	gateData, _ := os.ReadFile(filepath.Join(state.Path, ".railwait", "manager", "control", "maintenance.json"))
	if strings.Contains(string(gateData), `"closed":true`) {
		t.Fatal("gate stayed closed after success")
	}
	if strings.Join(stages, ",") != "checking,downloading,draining,backing_up,applying,verifying,resuming" {
		t.Fatal(stages)
	}
	backup, stop := -1, -1
	for n, c := range runner.commands {
		args := strings.Join(c.Args, " ")
		if strings.Contains(args, "update-backup.sh") {
			backup = n
		}
		if strings.Contains(args, " stop ") {
			stop = n
		}
		if strings.Contains(args, "up -d --force-recreate") {
			if !strings.Contains(args, "--no-deps") || strings.HasSuffix(args, " postgres") || strings.HasSuffix(args, " redis") {
				t.Fatal("storage was recreated")
			}
		}
	}
	if backup < 0 || stop <= backup {
		t.Fatal("services stopped before verified backup")
	}
	if len(runner.inputs) != 1 || !strings.Contains(runner.inputs[0], "SECRET_ENCRYPTION_KEY") {
		t.Fatal("settings did not reach encrypted backup")
	}
}

func TestUpdateRejectsSchemaStorageAndConfigurationDrift(t *testing.T) {
	for _, kind := range []string{"database", "storage", "environment"} {
		t.Run(kind, func(t *testing.T) {
			i, runner, source, state := updateFixture(t)
			if kind == "database" {
				source.target.DatabaseRevision = "rev2"
			}
			if kind == "storage" {
				copyImages := map[string]string{}
				for name, image := range source.target.Images {
					copyImages[name] = image
				}
				copyImages["redis"] = "redis@sha256:" + strings.Repeat("1", 64)
				source.target.Images = copyImages
			}
			plan, err := i.PlanUpdate(context.Background(), state.Path)
			if err != nil {
				t.Fatal(err)
			}
			if kind != "environment" {
				if plan.Allowed {
					t.Fatal("unsafe plan allowed")
				}
				return
			}
			if !plan.Allowed {
				t.Fatal(plan.BlockedReasons)
			}
			f, err := os.OpenFile(filepath.Join(state.Path, ".env"), os.O_APPEND|os.O_WRONLY, 0600)
			if err != nil {
				t.Fatal(err)
			}
			f.WriteString("TAGO_NEW='changed'\n")
			f.Close()
			count := len(runner.commands)
			if _, err = i.Update(context.Background(), UpdateOptions{Path: state.Path, TargetReleaseID: plan.TargetReleaseID, PlanID: plan.PlanID}); err == nil {
				t.Fatal("configuration drift applied")
			}
			for _, c := range runner.commands[count:] {
				if strings.Contains(strings.Join(c.Args, " "), "update-backup.sh") {
					t.Fatal("ran backup after invalid plan")
				}
			}
		})
	}
}

func TestBackupFailurePreservesClosedGateAndOldRelease(t *testing.T) {
	i, runner, _, state := updateFixture(t)
	plan, err := i.PlanUpdate(context.Background(), state.Path)
	if err != nil {
		t.Fatal(err)
	}
	runner.failure = "update-backup.sh"
	result, err := i.Update(context.Background(), UpdateOptions{Path: state.Path, TargetReleaseID: plan.TargetReleaseID, PlanID: plan.PlanID})
	if err == nil || result.State != "recovery_required" {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	updated, _ := ReadState(state.Path)
	if updated.Release != state.Release {
		t.Fatal("committed failed release")
	}
	gate, _ := os.ReadFile(filepath.Join(state.Path, ".railwait", "manager", "control", "maintenance.json"))
	if !strings.Contains(string(gate), `"closed":true`) {
		t.Fatal("reopened unsafe gate")
	}
	for _, c := range runner.commands {
		if strings.Contains(strings.Join(c.Args, " "), " stop ") {
			t.Fatal("stopped services after backup failure")
		}
	}
}

func TestTAGOConfigurationUsesManagementBackupAndPreservesAllOtherSecrets(t *testing.T) {
	i, runner, source, state := updateFixture(t)
	before, err := os.ReadFile(filepath.Join(state.Path, ".env"))
	if err != nil {
		t.Fatal(err)
	}
	source.target.Version = "not-used-for-configure"
	plan, err := i.PlanConfigureTAGO(context.Background(), state.Path, "new-test-tago-secret")
	if err != nil {
		t.Fatal(err)
	}
	if !plan.Allowed {
		t.Fatal(plan.BlockedReasons)
	}
	serialized, _ := json.Marshal(plan)
	if strings.Contains(string(serialized), "new-test-tago-secret") {
		t.Fatal("secret leaked into public plan")
	}
	result, err := i.Update(context.Background(), UpdateOptions{Path: state.Path, TargetReleaseID: state.Release, PlanID: plan.PlanID})
	if err != nil {
		t.Fatal(err)
	}
	if result.State != "succeeded" {
		t.Fatal(result)
	}
	after, _ := os.ReadFile(filepath.Join(state.Path, ".env"))
	oldValues, _ := ParseEnv(string(before))
	newValues, _ := ParseEnv(string(after))
	if newValues["TAGO_SERVICE_KEY"] != "new-test-tago-secret" {
		t.Fatal("TAGO setting not applied")
	}
	for name, value := range oldValues {
		if name != "TAGO_SERVICE_KEY" && newValues[name] != value {
			t.Fatalf("changed existing key %s", name)
		}
	}
	if len(runner.inputs) != 1 || runner.inputs[0] != string(before) {
		t.Fatal("old settings were not backed up before replacement")
	}
	if _, err = os.Stat(filepath.Join(state.Path, ".railwait", "plans", plan.PlanID+".candidate.env")); !os.IsNotExist(err) {
		t.Fatal("temporary secret candidate was retained")
	}
	updated, _ := ReadState(state.Path)
	if updated.Release != state.Release {
		t.Fatal("configuration changed release")
	}
}

func TestTransientSharedFilesystemVisibilityIsRetriedBeforeDrain(t *testing.T) {
	i, runner, _, state := updateFixture(t)
	plan, err := i.PlanUpdate(context.Background(), state.Path)
	if err != nil {
		t.Fatal(err)
	}
	runner.drainFailures = 2
	result, err := i.Update(context.Background(), UpdateOptions{Path: state.Path, TargetReleaseID: plan.TargetReleaseID, PlanID: plan.PlanID})
	if err != nil || result.State != "succeeded" {
		t.Fatalf("result=%+v err=%v", result, err)
	}
}
