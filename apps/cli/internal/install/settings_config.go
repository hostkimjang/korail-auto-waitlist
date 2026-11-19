package install

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/manager"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

// The web settings window edits the settings below through the host manager. The API
// (apps/api/src/rail_waitlist/maintenance) and the web screen keep the same list, but the
// manager is the one that validates and applies a change.
type settingKind int

const (
	settingBoolean settingKind = iota
	settingVersion
	settingSecret
	settingContact
)

type settingField struct {
	key  string
	kind settingKind
}

var settingsFields = []settingField{
	{"KORAIL_RESERVATION_ONCE_ENABLED", settingBoolean},
	{"SRT_RESERVATION_ONCE_ENABLED", settingBoolean},
	{"TAGO_SERVICE_KEY", settingSecret},
	{"WEBPUSH_VAPID_SUBJECT", settingContact},
	{"VITE_KORAIL_BOOKING_DEEPLINK_ENABLED", settingBoolean},
	{"VITE_KORAIL_BOOKING_VALIDATED_VERSION", settingVersion},
	{"VITE_KORAIL_TICKET_DEEPLINK_ENABLED", settingBoolean},
	{"VITE_KORAIL_TICKET_VALIDATED_VERSION", settingVersion},
	{"VITE_SRT_MAIN_DEEPLINK_ENABLED", settingBoolean},
	{"VITE_SRT_MAIN_VALIDATED_VERSION", settingVersion},
	{"VITE_SRT_TICKET_DEEPLINK_ENABLED", settingBoolean},
	{"VITE_SRT_TICKET_VALIDATED_VERSION", settingVersion},
}

// MaskedSettingValue stands for every secret value in the raw view. It cannot be a
// credential the CLI wrote, so an untouched secret line is told apart from an edited one.
const MaskedSettingValue = "••••••"

const maxSettingsEnvBytes = 64 << 10

var (
	settingVersionPattern = regexp.MustCompile(`^[0-9A-Za-z.+_-]{1,64}$`)
	// Printable ASCII without space, " and ', which fits a single-quoted .env value.
	settingSecretPattern = regexp.MustCompile(`^[!#-&(-~]{1,512}$`)
	commentEntryPattern  = regexp.MustCompile(`^(\s*#\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$`)
	errSettingsChanged   = manager.RejectedError{Code: "E_SETTINGS_CHANGED"}
	errSettingsInvalid   = manager.RejectedError{Code: "E_SETTINGS_INVALID"}
	errSettingsForbidden = manager.RejectedError{Code: "E_SETTINGS_FORBIDDEN"}
	errSettingsUnchanged = manager.RejectedError{Code: "E_SETTINGS_UNCHANGED"}
)

// lockedSettings decide the install's identity, access address, storage or browser
// remote control. A wrong value could lock the admin out or detach the data, so only the
// CLI changes them; the raw view shows them read-only.
var lockedSettings = map[string]bool{
	"SITE_ADDRESS": true, "AUTH_ALLOWED_ORIGINS": true, "CORS_ORIGINS": true, "AUTH_COOKIE_SECURE": true,
	"API_UPSTREAM": true, "WEB_UPSTREAM": true, "AUTH_INITIAL_REGISTRATION_ENABLED": true,
	"EXPERIMENTAL_RAIL_ENABLED": true, "KORAIL_BROWSER_GUI_ENABLED": true, "KORAIL_BROWSER_BRIDGE_ENABLED": true,
}

var lockedSettingPrefixes = []string{"COMPOSE_", "RAILWAIT_", "CADDY_", "POSTGRES_", "BACKUP_AGE_", "KORAIL_NOVNC_"}

// secretSetting errs on the side of hiding: any name that may hold a credential or a URL
// with one is masked, and the web window cannot change it.
func secretSetting(key string) bool {
	upper := strings.ToUpper(key)
	for _, part := range []string{"PASSWORD", "PASSPHRASE", "SECRET", "TOKEN", "PRIVATE", "IDENTITY", "CREDENTIAL", "_KEY"} {
		if strings.Contains(upper, part) {
			return true
		}
	}
	return strings.HasSuffix(upper, "_URL") || strings.HasSuffix(upper, "_DSN")
}

func lockedSetting(key string) bool {
	upper := strings.ToUpper(key)
	if lockedSettings[upper] {
		return true
	}
	for _, prefix := range lockedSettingPrefixes {
		if strings.HasPrefix(upper, prefix) {
			return true
		}
	}
	return false
}

func validSettingValue(kind settingKind, value string) bool {
	switch kind {
	case settingBoolean:
		return value == "true" || value == "false"
	case settingVersion:
		return value == "" || settingVersionPattern.MatchString(value)
	case settingSecret:
		return value == "" || settingSecretPattern.MatchString(value)
	case settingContact:
		return value == "" || len(value) <= 254 && validatePushOptions(PushOptions{Subject: value}, false) == nil
	}
	return false
}

func settingFieldFor(key string) (settingField, bool) {
	for _, field := range settingsFields {
		if field.key == key {
			return field, true
		}
	}
	return settingField{}, false
}

func readSettingsEnv(path string) ([]byte, error) {
	file := filepath.Join(path, ".env")
	info, err := os.Lstat(file)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Size() > maxSettingsEnvBytes {
		return nil, fmt.Errorf(".env가 실제 파일이 아니거나 너무 큽니다")
	}
	f, err := os.Open(file)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, maxSettingsEnvBytes+1))
	if err != nil {
		return nil, err
	}
	if len(data) > maxSettingsEnvBytes {
		return nil, fmt.Errorf(".env가 너무 큽니다")
	}
	return data, nil
}

// SettingsSnapshot returns the secret-free view of the install's .env that the manager
// publishes for the web settings window.
func SettingsSnapshot(path string) (manager.SettingsSnapshot, error) {
	abs, err := filepath.Abs(path)
	if err != nil {
		return manager.SettingsSnapshot{}, err
	}
	data, err := readSettingsEnv(abs)
	if err != nil {
		return manager.SettingsSnapshot{}, err
	}
	values, err := ParseEnv(string(data))
	if err != nil {
		return manager.SettingsSnapshot{}, err
	}
	snapshot := manager.SettingsSnapshot{Schema: 1, Revision: hashBytes(data), Fields: map[string]manager.SettingsField{}}
	for _, field := range settingsFields {
		value, present := values[field.key]
		if field.kind == settingSecret {
			snapshot.Fields[field.key] = manager.SettingsField{Configured: value != ""}
			continue
		}
		snapshot.Fields[field.key] = manager.SettingsField{Value: &value, Configured: present}
	}
	snapshot.Raw, snapshot.SecretKeys, snapshot.LockedKeys = maskEnvironment(data)
	return snapshot, nil
}

func envLines(data string) []string {
	return strings.Split(strings.ReplaceAll(data, "\r\n", "\n"), "\n")
}

func unquotedSetting(raw string) string {
	value := strings.TrimSpace(raw)
	if len(value) >= 2 && (value[0] == '\'' && value[len(value)-1] == '\'' || value[0] == '"' && value[len(value)-1] == '"') {
		return value[1 : len(value)-1]
	}
	return value
}

// maskLine hides a secret entry, or a commented-out secret entry, and reports the key.
func maskLine(line string) (masked, key string, entry bool) {
	trimmed := strings.TrimSpace(line)
	if trimmed == "" {
		return line, "", false
	}
	if strings.HasPrefix(trimmed, "#") {
		match := commentEntryPattern.FindStringSubmatch(line)
		if match != nil && secretSetting(match[2]) && unquotedSetting(match[3]) != "" {
			return match[1] + match[2] + "='" + MaskedSettingValue + "'", "", false
		}
		return line, "", false
	}
	name, value, ok := strings.Cut(line, "=")
	if !ok {
		return line, "", false
	}
	key = strings.TrimSpace(name)
	if secretSetting(key) && unquotedSetting(value) != "" {
		return key + "='" + MaskedSettingValue + "'", key, true
	}
	return line, key, true
}

func maskEnvironment(data []byte) (string, []string, []string) {
	lines := envLines(string(data))
	secrets, locked := []string{}, []string{}
	for n, line := range lines {
		masked, key, entry := maskLine(line)
		lines[n] = masked
		if !entry {
			continue
		}
		if secretSetting(key) {
			secrets = append(secrets, key)
		} else if lockedSetting(key) {
			locked = append(locked, key)
		}
	}
	return strings.Join(lines, "\n"), secrets, locked
}

// unmaskEnvironment rebuilds a candidate .env from the edited raw view. Each secret line
// must come back masked or empty as it was shown, so the original line is restored; a
// secret or locked key cannot be added or removed here.
func unmaskEnvironment(original []byte, edited string) ([]byte, error) {
	if len(edited) > maxSettingsEnvBytes || strings.ContainsRune(edited, 0) {
		return nil, errSettingsInvalid
	}
	before, err := ParseEnv(string(original))
	if err != nil {
		return nil, err
	}
	originalLines := map[string]string{}
	maskedComments := map[string][]string{}
	for _, line := range envLines(string(original)) {
		masked, key, entry := maskLine(line)
		if entry {
			originalLines[key] = line
		} else if masked != line {
			maskedComments[masked] = append(maskedComments[masked], line)
		}
	}
	lines := envLines(edited)
	seen := map[string]bool{}
	for n, line := range lines {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" {
			continue
		}
		if strings.HasPrefix(trimmed, "#") {
			if queue := maskedComments[line]; len(queue) > 0 {
				lines[n], maskedComments[line] = queue[0], queue[1:]
			}
			continue
		}
		values, parseErr := ParseEnv(line)
		if parseErr != nil || len(values) != 1 {
			return nil, errSettingsInvalid
		}
		for key, value := range values {
			if seen[key] {
				return nil, errSettingsInvalid
			}
			seen[key] = true
			if !secretSetting(key) {
				continue
			}
			previous, existed := before[key]
			switch {
			case !existed:
				return nil, errSettingsForbidden
			case previous != "" && value == MaskedSettingValue, previous == "" && value == "":
				lines[n] = originalLines[key]
			default:
				return nil, errSettingsForbidden
			}
		}
	}
	for key := range before {
		if (secretSetting(key) || lockedSetting(key)) && !seen[key] {
			return nil, errSettingsForbidden
		}
	}
	candidate := strings.Join(lines, "\n")
	if !strings.HasSuffix(candidate, "\n") {
		candidate += "\n"
	}
	return []byte(candidate), nil
}

// applySettingFields sets the form fields in .env and keeps every other line.
func applySettingFields(data []byte, fields map[string]string) ([]byte, error) {
	for key, value := range fields {
		field, ok := settingFieldFor(key)
		if !ok {
			return nil, errSettingsForbidden
		}
		if !validSettingValue(field.kind, value) {
			return nil, errSettingsInvalid
		}
	}
	lines := envLines(string(data))
	found := map[string]bool{}
	for n, line := range lines {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		name, _, ok := strings.Cut(line, "=")
		key := strings.TrimSpace(name)
		if value, wanted := fields[key]; ok && wanted {
			lines[n] = key + "='" + value + "'"
			found[key] = true
		}
	}
	var missing []string
	for _, field := range settingsFields {
		if value, wanted := fields[field.key]; wanted && !found[field.key] {
			missing = append(missing, field.key+"='"+value+"'")
		}
	}
	if last := len(lines) - 1; last >= 0 && lines[last] == "" {
		lines = append(lines[:last], append(missing, "")...)
	} else {
		lines = append(append(lines, missing...), "")
	}
	return []byte(strings.Join(lines, "\n")), nil
}

// validateSettingsCandidate allows only what the settings window may change and reports
// which services must restart: "web" when only app-link values changed, otherwise "app".
func validateSettingsCandidate(original, candidate []byte) (string, error) {
	if len(candidate) > maxSettingsEnvBytes {
		return "", errSettingsInvalid
	}
	before, err := ParseEnv(string(original))
	if err != nil {
		return "", err
	}
	after, err := ParseEnv(string(candidate))
	if err != nil {
		return "", errSettingsInvalid
	}
	changed := []string{}
	for key, value := range after {
		if previous, ok := before[key]; !ok || previous != value {
			changed = append(changed, key)
		}
	}
	for key := range before {
		if _, ok := after[key]; !ok {
			changed = append(changed, key)
		}
	}
	if len(changed) == 0 {
		return "", errSettingsUnchanged
	}
	scope := "web"
	for _, key := range changed {
		if lockedSetting(key) || secretSetting(key) && key != "TAGO_SERVICE_KEY" {
			return "", errSettingsForbidden
		}
		value, kept := after[key]
		if field, ok := settingFieldFor(key); ok && kept && !validSettingValue(field.kind, value) {
			return "", errSettingsInvalid
		}
		if !strings.HasPrefix(key, "VITE_") {
			scope = "app"
		}
	}
	return scope, nil
}

// SettingsChange is one submission from the web settings window: either form fields or the
// whole edited raw view, checked against the .env revision the window showed.
type SettingsChange struct {
	BaseRevision string
	Fields       map[string]string
	Raw          *string
}

// PlanSettingsChange validates a settings change and saves a configure_settings plan. A
// refused change returns manager.RejectedError with the reason the web screen explains.
func (i *Installer) PlanSettingsChange(ctx context.Context, path string, change SettingsChange) (UpdatePlan, error) {
	s, err := ReadState(path)
	if err != nil {
		return UpdatePlan{}, err
	}
	data, err := readSettingsEnv(s.Path)
	if err != nil {
		return UpdatePlan{CurrentReleaseID: s.Release, TargetReleaseID: s.Release}, err
	}
	blank := UpdatePlan{CurrentReleaseID: s.Release, TargetReleaseID: s.Release}
	if hashBytes(data) != change.BaseRevision {
		return blank, errSettingsChanged
	}
	var candidate []byte
	if change.Raw != nil {
		candidate, err = unmaskEnvironment(data, *change.Raw)
	} else {
		candidate, err = applySettingFields(data, change.Fields)
	}
	if err != nil {
		return blank, err
	}
	scope, err := validateSettingsCandidate(data, candidate)
	if err != nil {
		return blank, err
	}
	if err = i.checkSettingsCompose(ctx, s, candidate); err != nil {
		return blank, errSettingsInvalid
	}
	r := installedRelease(s)
	plan, err := i.makePlanMode(ctx, s, r, planConfigure)
	if err != nil {
		return plan, err
	}
	plan.Steps = []string{"checking", "preparing_config", "draining", "backing_up", "applying", "verifying", "resuming"}
	if scope == "web" {
		plan.Steps = []string{"checking", "preparing_config", "applying", "verifying"}
	}
	return savePlan(s, plan, r.SHA256, "configure_settings", candidate)
}

// PlanSettingsRequest is the manager's settings planner: it turns an authenticated request
// into the ordinary request that applies the saved plan.
func (i *Installer) PlanSettingsRequest(ctx context.Context, path string, request manager.SettingsRequest) (manager.Request, error) {
	plan, err := i.PlanSettingsChange(ctx, path, SettingsChange{BaseRevision: request.BaseRevision, Fields: request.Fields, Raw: request.Raw})
	planned := manager.Request{Schema: 1, ID: request.ID, TargetReleaseID: plan.TargetReleaseID, IdempotencyKey: request.IdempotencyKey}
	if err != nil {
		return planned, err
	}
	if !plan.Allowed {
		return planned, errors.New("settings change blocked: " + strings.Join(plan.BlockedReasons, "; "))
	}
	planned.PlanID = plan.PlanID
	return planned, nil
}

// checkSettingsCompose runs Compose's own interpolation check against the candidate, so a
// value that breaks a required variable is refused before anything restarts.
func (i *Installer) checkSettingsCompose(ctx context.Context, s State, candidate []byte) error {
	dir := filepath.Join(s.Path, ".railwait", "plans")
	if err := platform.SecureDir(dir); err != nil {
		return err
	}
	f, err := os.CreateTemp(dir, ".settings-check-*.env")
	if err != nil {
		return err
	}
	name := f.Name()
	defer os.Remove(name)
	if err = platform.SecureFile(name); err == nil {
		_, err = f.Write(candidate)
	}
	if err = errors.Join(err, f.Close()); err != nil {
		return err
	}
	args := []string{"compose", "--project-name", s.Project, "--project-directory", filepath.Dir(s.ComposeFile), "--env-file", name, "-f", s.ComposeFile}
	for _, profile := range s.Profiles {
		args = append(args, "--profile", profile)
	}
	_, err = i.docker(ctx, s, nil, append(args, "config", "--quiet")...)
	return err
}

// restoreSettings puts the previous .env back and recreates the given services with it.
func (i *Installer) restoreSettings(ctx context.Context, s State, env []byte, services []string) error {
	restoreCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Minute)
	defer cancel()
	if err := atomicWrite(filepath.Join(s.Path, ".env"), env); err != nil {
		return fmt.Errorf("이전 설정을 되돌리지 못했습니다: %w", err)
	}
	if _, err := i.compose(restoreCtx, s, nil, append([]string{"up", "-d", "--force-recreate", "--no-deps", "--wait", "--wait-timeout", "180"}, services...)...); err != nil {
		return fmt.Errorf("이전 설정으로 서비스를 다시 시작하지 못했습니다: %w", err)
	}
	status, err := i.statusState(restoreCtx, s)
	if err != nil {
		return err
	}
	if !status.Healthy {
		return fmt.Errorf("이전 설정으로 되돌린 서비스가 정상 상태가 아닙니다")
	}
	return nil
}

// applyWebSettings applies a change of app-link values only. The web container reads them at
// start, so recreating that one service is enough: no drain, gate or backup is needed.
func (i *Installer) applyWebSettings(ctx context.Context, s State, env, candidate []byte, result *UpdateResult, progress func(string) error) (err error) {
	if err = progress("applying"); err != nil {
		return err
	}
	if err = atomicWrite(filepath.Join(s.Path, ".env"), candidate); err != nil {
		return err
	}
	defer func() {
		if err == nil {
			return
		}
		if restoreErr := i.restoreSettings(ctx, s, env, []string{"web"}); restoreErr != nil {
			err = errors.Join(err, restoreErr)
			result.State = "recovery_required"
			return
		}
		result.Stage = "rolled_back"
	}()
	if _, err = i.compose(ctx, s, nil, "up", "-d", "--force-recreate", "--no-deps", "--wait", "--wait-timeout", "180", "web"); err != nil {
		return err
	}
	if err = progress("verifying"); err != nil {
		return err
	}
	status, err := i.statusState(ctx, s)
	if err != nil {
		return err
	}
	if !status.Healthy {
		return fmt.Errorf("웹 서비스가 새 설정으로 정상 상태가 되지 않았습니다")
	}
	return nil
}
