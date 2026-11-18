package install

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/manager"
)

const settingsEnvFixture = "# 공통 설정\n" +
	"COMPOSE_PROJECT_NAME='railwait-test'\n" +
	"POSTGRES_PASSWORD='postgres-secret-marker'\n" +
	"# OLD_API_TOKEN='commented-secret-marker'\n" +
	"TAGO_SERVICE_KEY='tago-secret-marker'\n" +
	"KORAIL_NOTICE_OBSERVATION_REDIS_URL='redis://:redis-secret-marker@redis:6379/1'\n" +
	"KORAIL_RESERVATION_ONCE_ENABLED='false'\n" +
	"AUTH_ALLOWED_ORIGINS='http://127.0.0.1:8080'\n" +
	"VITE_KORAIL_BOOKING_DEEPLINK_ENABLED=false\n" +
	"WEBPUSH_VAPID_PRIVATE_KEY=''\n" +
	"APP_LOG_LEVEL=INFO\n"

func rejectedCode(err error) string {
	var rejected manager.RejectedError
	if errors.As(err, &rejected) {
		return rejected.Code
	}
	return ""
}

func TestSettingsSnapshotHidesEverySecretAndReportsTheFormFields(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, ".env"), []byte(settingsEnvFixture), 0o600); err != nil {
		t.Fatal(err)
	}
	snapshot, err := SettingsSnapshot(dir)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Revision != hashBytes([]byte(settingsEnvFixture)) {
		t.Fatal("revision is not the .env digest")
	}
	for _, marker := range []string{"postgres-secret-marker", "commented-secret-marker", "tago-secret-marker", "redis-secret-marker"} {
		if strings.Contains(snapshot.Raw, marker) {
			t.Fatalf("raw view exposed %s", marker)
		}
	}
	for _, line := range []string{"POSTGRES_PASSWORD='" + MaskedSettingValue + "'", "# OLD_API_TOKEN='" + MaskedSettingValue + "'", "WEBPUSH_VAPID_PRIVATE_KEY=''", "APP_LOG_LEVEL=INFO", "# 공통 설정"} {
		if !strings.Contains(snapshot.Raw, line+"\n") {
			t.Fatalf("raw view lacks %q:\n%s", line, snapshot.Raw)
		}
	}
	if strings.Join(snapshot.SecretKeys, ",") != "POSTGRES_PASSWORD,TAGO_SERVICE_KEY,KORAIL_NOTICE_OBSERVATION_REDIS_URL,WEBPUSH_VAPID_PRIVATE_KEY" {
		t.Fatal(snapshot.SecretKeys)
	}
	if strings.Join(snapshot.LockedKeys, ",") != "COMPOSE_PROJECT_NAME,AUTH_ALLOWED_ORIGINS" {
		t.Fatal(snapshot.LockedKeys)
	}
	if tago := snapshot.Fields["TAGO_SERVICE_KEY"]; tago.Value != nil || !tago.Configured {
		t.Fatalf("TAGO field: %+v", tago)
	}
	if link := snapshot.Fields["VITE_KORAIL_BOOKING_DEEPLINK_ENABLED"]; link.Value == nil || *link.Value != "false" || !link.Configured {
		t.Fatalf("app link field: %+v", link)
	}
	if version := snapshot.Fields["VITE_SRT_TICKET_VALIDATED_VERSION"]; version.Value == nil || *version.Value != "" || version.Configured {
		t.Fatalf("missing field: %+v", version)
	}
	if len(snapshot.Fields) != len(settingsFields) {
		t.Fatal("snapshot does not report every form field")
	}
}

func TestRawEditRestoresSecretsAndRefusesSecretOrLockedChanges(t *testing.T) {
	original := []byte(settingsEnvFixture)
	masked, _, _ := maskEnvironment(original)
	unchanged, err := unmaskEnvironment(original, masked)
	if err != nil || string(unchanged) != settingsEnvFixture {
		t.Fatalf("round trip changed .env: %v\n%s", err, unchanged)
	}
	edited := strings.Replace(masked, "APP_LOG_LEVEL=INFO", "APP_LOG_LEVEL=DEBUG", 1) + "SRT_SEAT_STATUS_TIMEOUT_SECONDS=20\n"
	candidate, err := unmaskEnvironment(original, edited)
	if err != nil {
		t.Fatal(err)
	}
	values, err := ParseEnv(string(candidate))
	if err != nil {
		t.Fatal(err)
	}
	if values["APP_LOG_LEVEL"] != "DEBUG" || values["SRT_SEAT_STATUS_TIMEOUT_SECONDS"] != "20" || values["POSTGRES_PASSWORD"] != "postgres-secret-marker" || !strings.Contains(string(candidate), "# OLD_API_TOKEN='commented-secret-marker'") {
		t.Fatalf("candidate:\n%s", candidate)
	}
	for name, change := range map[string]string{
		"secret edited":       strings.Replace(masked, "TAGO_SERVICE_KEY='"+MaskedSettingValue+"'", "TAGO_SERVICE_KEY='typed-in-raw'", 1),
		"secret removed":      strings.Replace(masked, "POSTGRES_PASSWORD='"+MaskedSettingValue+"'\n", "", 1),
		"secret added":        masked + "NEW_SERVICE_TOKEN=abc\n",
		"locked removed":      strings.Replace(masked, "COMPOSE_PROJECT_NAME='railwait-test'\n", "", 1),
		"empty secret filled": strings.Replace(masked, "WEBPUSH_VAPID_PRIVATE_KEY=''", "WEBPUSH_VAPID_PRIVATE_KEY='x'", 1),
	} {
		if _, err := unmaskEnvironment(original, change); rejectedCode(err) != "E_SETTINGS_FORBIDDEN" {
			t.Fatalf("%s: %v", name, err)
		}
	}
	for name, change := range map[string]string{
		"duplicate key": masked + "APP_LOG_LEVEL=DEBUG\n",
		"bad line":      masked + "not an entry\n",
		"quote":         masked + "APP_NAME=\"quoted\"\n",
	} {
		if _, err := unmaskEnvironment(original, change); rejectedCode(err) != "E_SETTINGS_INVALID" {
			t.Fatalf("%s: %v", name, err)
		}
	}
}

func TestSettingsCandidateScopeAndRefusals(t *testing.T) {
	original := []byte(settingsEnvFixture)
	web := strings.Replace(settingsEnvFixture, "VITE_KORAIL_BOOKING_DEEPLINK_ENABLED=false", "VITE_KORAIL_BOOKING_DEEPLINK_ENABLED=true", 1) + "VITE_KORAIL_BOOKING_VALIDATED_VERSION='7.0.0+70000006'\n"
	if scope, err := validateSettingsCandidate(original, []byte(web)); err != nil || scope != "web" {
		t.Fatalf("app-link change: %s %v", scope, err)
	}
	app := strings.Replace(web, "KORAIL_RESERVATION_ONCE_ENABLED='false'", "KORAIL_RESERVATION_ONCE_ENABLED='true'", 1)
	if scope, err := validateSettingsCandidate(original, []byte(app)); err != nil || scope != "app" {
		t.Fatalf("reservation change: %s %v", scope, err)
	}
	for name, test := range map[string]struct{ candidate, code string }{
		"unchanged":     {settingsEnvFixture, "E_SETTINGS_UNCHANGED"},
		"locked":        {strings.Replace(settingsEnvFixture, "http://127.0.0.1:8080", "http://192.168.0.2:8080", 1), "E_SETTINGS_FORBIDDEN"},
		"secret":        {strings.Replace(settingsEnvFixture, "postgres-secret-marker", "other", 1), "E_SETTINGS_FORBIDDEN"},
		"boolean value": {strings.Replace(settingsEnvFixture, "VITE_KORAIL_BOOKING_DEEPLINK_ENABLED=false", "VITE_KORAIL_BOOKING_DEEPLINK_ENABLED=yes", 1), "E_SETTINGS_INVALID"},
	} {
		if _, err := validateSettingsCandidate(original, []byte(test.candidate)); rejectedCode(err) != test.code {
			t.Fatalf("%s: %v", name, err)
		}
	}
	if scope, err := validateSettingsCandidate(original, []byte(strings.Replace(settingsEnvFixture, "tago-secret-marker", "new-key", 1))); err != nil || scope != "app" {
		t.Fatalf("timetable key change: %s %v", scope, err)
	}
}

func TestFormFieldsReplaceOrAppendOnlyTheirLines(t *testing.T) {
	candidate, err := applySettingFields([]byte(settingsEnvFixture), map[string]string{"KORAIL_RESERVATION_ONCE_ENABLED": "true", "VITE_SRT_TICKET_VALIDATED_VERSION": "2.0.41+150", "TAGO_SERVICE_KEY": ""})
	if err != nil {
		t.Fatal(err)
	}
	want := strings.Replace(strings.Replace(settingsEnvFixture, "KORAIL_RESERVATION_ONCE_ENABLED='false'", "KORAIL_RESERVATION_ONCE_ENABLED='true'", 1), "TAGO_SERVICE_KEY='tago-secret-marker'", "TAGO_SERVICE_KEY=''", 1) + "VITE_SRT_TICKET_VALIDATED_VERSION='2.0.41+150'\n"
	if string(candidate) != want {
		t.Fatalf("candidate:\n%s", candidate)
	}
	if _, err := applySettingFields([]byte(settingsEnvFixture), map[string]string{"APP_LOG_LEVEL": "DEBUG"}); rejectedCode(err) != "E_SETTINGS_FORBIDDEN" {
		t.Fatal(err)
	}
	for key, value := range map[string]string{"VITE_SRT_MAIN_DEEPLINK_ENABLED": "1", "VITE_SRT_MAIN_VALIDATED_VERSION": "2.0 beta", "TAGO_SERVICE_KEY": "has space", "WEBPUSH_VAPID_SUBJECT": "https://example.com/path"} {
		if _, err := applySettingFields([]byte(settingsEnvFixture), map[string]string{key: value}); rejectedCode(err) != "E_SETTINGS_INVALID" {
			t.Fatalf("%s=%q: %v", key, value, err)
		}
	}
}

func settingsRevision(t *testing.T, state State) string {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(state.Path, ".env"))
	if err != nil {
		t.Fatal(err)
	}
	return hashBytes(data)
}

func commandsContaining(runner *updateRunner, part string) int {
	count := 0
	for _, c := range runner.commands {
		if strings.Contains(strings.Join(c.Args, " "), part) {
			count++
		}
	}
	return count
}

func TestAppLinkSettingsRecreateOnlyTheWebService(t *testing.T) {
	i, runner, _, state := updateFixture(t)
	plan, err := i.PlanSettingsChange(context.Background(), state.Path, SettingsChange{BaseRevision: settingsRevision(t, state), Fields: map[string]string{"VITE_KORAIL_BOOKING_DEEPLINK_ENABLED": "true", "VITE_KORAIL_BOOKING_VALIDATED_VERSION": "7.0.0+70000006"}})
	if err != nil {
		t.Fatal(err)
	}
	if !plan.Allowed || strings.Join(plan.Steps, ",") != "checking,preparing_config,applying,verifying" {
		t.Fatalf("plan: %+v", plan)
	}
	if commandsContaining(runner, "config --quiet") == 0 {
		t.Fatal("candidate not checked with Compose")
	}
	stages := []string{}
	result, err := i.Update(context.Background(), UpdateOptions{Path: state.Path, TargetReleaseID: state.Release, PlanID: plan.PlanID, Progress: func(stage string) error { stages = append(stages, stage); return nil }})
	if err != nil || result.State != "succeeded" {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	if strings.Join(stages, ",") != "checking,preparing_config,applying,verifying" {
		t.Fatal(stages)
	}
	if commandsContaining(runner, "up -d --force-recreate --no-deps --wait --wait-timeout 180 web") != 1 || commandsContaining(runner, "update-backup.sh") != 0 || commandsContaining(runner, "maintenance --status") != 0 || commandsContaining(runner, " stop ") != 0 {
		t.Fatal("app-link change drained, backed up or stopped services")
	}
	values, _ := ParseEnv(string(mustRead(t, filepath.Join(state.Path, ".env"))))
	if values["VITE_KORAIL_BOOKING_DEEPLINK_ENABLED"] != "true" || values["VITE_KORAIL_BOOKING_VALIDATED_VERSION"] != "7.0.0+70000006" {
		t.Fatal("app-link settings not written")
	}
	if _, err = os.Stat(filepath.Join(state.Path, ".railwait", "plans", plan.PlanID+".candidate.env")); !os.IsNotExist(err) {
		t.Fatal("candidate retained")
	}
	entries, _ := os.ReadDir(filepath.Join(state.Path, ".railwait", "plans"))
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), ".settings-check-") {
			t.Fatal("Compose check file retained")
		}
	}
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func TestAppSettingsDrainBackUpAndRestartLikeAnUpdate(t *testing.T) {
	i, runner, _, state := updateFixture(t)
	plan, err := i.PlanSettingsChange(context.Background(), state.Path, SettingsChange{BaseRevision: settingsRevision(t, state), Fields: map[string]string{"KORAIL_RESERVATION_ONCE_ENABLED": "true", "SRT_RESERVATION_ONCE_ENABLED": "true"}})
	if err != nil || !plan.Allowed {
		t.Fatalf("plan=%+v err=%v", plan, err)
	}
	before := mustRead(t, filepath.Join(state.Path, ".env"))
	stages := []string{}
	result, err := i.Update(context.Background(), UpdateOptions{Path: state.Path, TargetReleaseID: state.Release, PlanID: plan.PlanID, Progress: func(stage string) error { stages = append(stages, stage); return nil }})
	if err != nil || result.State != "succeeded" {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	if strings.Join(stages, ",") != "checking,preparing_config,draining,backing_up,applying,verifying,resuming" {
		t.Fatal(stages)
	}
	if len(runner.inputs) != 1 || runner.inputs[0] != string(before) {
		t.Fatal("previous settings were not backed up first")
	}
	values, _ := ParseEnv(string(mustRead(t, filepath.Join(state.Path, ".env"))))
	if values["KORAIL_RESERVATION_ONCE_ENABLED"] != "true" || values["SRT_RESERVATION_ONCE_ENABLED"] != "true" {
		t.Fatal("reservation switches not written")
	}
}

func TestFailedSettingsChangeRestoresThePreviousEnvironment(t *testing.T) {
	for _, test := range []struct {
		name, failure string
		fields        map[string]string
	}{
		{"app", "--exit-code-from migration", map[string]string{"KORAIL_RESERVATION_ONCE_ENABLED": "true"}},
		{"web", "--wait-timeout 180 web", map[string]string{"VITE_SRT_MAIN_DEEPLINK_ENABLED": "true"}},
	} {
		t.Run(test.name, func(t *testing.T) {
			i, runner, _, state := updateFixture(t)
			before := mustRead(t, filepath.Join(state.Path, ".env"))
			plan, err := i.PlanSettingsChange(context.Background(), state.Path, SettingsChange{BaseRevision: hashBytes(before), Fields: test.fields})
			if err != nil || !plan.Allowed {
				t.Fatalf("plan=%+v err=%v", plan, err)
			}
			runner.failure, runner.failureLimit = test.failure, 1
			result, err := i.Update(context.Background(), UpdateOptions{Path: state.Path, TargetReleaseID: state.Release, PlanID: plan.PlanID})
			if err == nil || result.State != "failed" || result.Stage != "rolled_back" {
				t.Fatalf("result=%+v err=%v", result, err)
			}
			if string(mustRead(t, filepath.Join(state.Path, ".env"))) != string(before) {
				t.Fatal("previous .env not restored")
			}
			gate := mustRead(t, filepath.Join(state.Path, ".railwait", "manager", "control", "maintenance.json"))
			if strings.Contains(string(gate), `"closed":true`) {
				t.Fatal("gate left closed after a rollback")
			}
		})
	}
}

func TestStaleOrEmptySettingsChangesAreRefusedBeforePlanning(t *testing.T) {
	i, runner, _, state := updateFixture(t)
	if _, err := i.PlanSettingsChange(context.Background(), state.Path, SettingsChange{BaseRevision: strings.Repeat("0", 64), Fields: map[string]string{"KORAIL_RESERVATION_ONCE_ENABLED": "true"}}); rejectedCode(err) != "E_SETTINGS_CHANGED" {
		t.Fatal(err)
	}
	values, _ := ParseEnv(string(mustRead(t, filepath.Join(state.Path, ".env"))))
	if _, err := i.PlanSettingsChange(context.Background(), state.Path, SettingsChange{BaseRevision: settingsRevision(t, state), Fields: map[string]string{"KORAIL_RESERVATION_ONCE_ENABLED": values["KORAIL_RESERVATION_ONCE_ENABLED"]}}); rejectedCode(err) != "E_SETTINGS_UNCHANGED" {
		t.Fatal(err)
	}
	if commandsContaining(runner, "--database-revision") != 0 {
		t.Fatal("refused change ran the update preflight")
	}
}
