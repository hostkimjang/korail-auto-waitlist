package install

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/release"
)

func installedRelease(s State) release.Release {
	hashes, _ := json.Marshal(s.BundleHashes)
	return release.Release{Version: s.Release, SHA256: hashBytes(hashes), Images: s.ExpectedImages, DatabaseRevision: s.DatabaseRevision, SettingsSchema: s.SettingsSchema, CompatibleFrom: []string{s.Release}}
}

// PlanConfigureTAGO changes one setting through the same backup, drain and
// restart operation as a release update. Credentials never enter the IPC request.
func (i *Installer) PlanConfigureTAGO(ctx context.Context, path, key string) (UpdatePlan, error) {
	if strings.ContainsAny(key, "'\r\n\x00") {
		return UpdatePlan{}, fmt.Errorf("TAGO 키 형식이 잘못되었습니다")
	}
	s, err := ReadState(path)
	if err != nil {
		return UpdatePlan{}, err
	}
	data, err := os.ReadFile(filepath.Join(s.Path, ".env"))
	if err != nil {
		return UpdatePlan{}, err
	}
	values, err := ParseEnv(string(data))
	if err != nil {
		return UpdatePlan{}, err
	}
	if values["TAGO_SERVICE_KEY"] == key {
		return UpdatePlan{}, fmt.Errorf("TAGO 키가 현재 값과 같습니다")
	}
	lines := strings.Split(string(data), "\n")
	found := false
	for n, line := range lines {
		name, _, ok := strings.Cut(line, "=")
		if ok && strings.TrimSpace(name) == "TAGO_SERVICE_KEY" {
			lines[n] = "TAGO_SERVICE_KEY='" + key + "'"
			found = true
		}
	}
	if !found {
		lines = append(lines, "TAGO_SERVICE_KEY='"+key+"'")
	}
	candidate := []byte(strings.Join(lines, "\n"))
	r := installedRelease(s)
	plan, err := i.makePlanMode(ctx, s, r, true)
	if err != nil {
		return plan, err
	}
	plan.Steps = []string{"checking", "preparing_config", "draining", "backing_up", "applying", "verifying", "resuming"}
	return savePlan(s, plan, r.SHA256, "configure_tago", candidate)
}

func validateTAGOCandidate(original, candidate []byte) error {
	before, err := ParseEnv(string(original))
	if err != nil {
		return err
	}
	after, err := ParseEnv(string(candidate))
	if err != nil {
		return err
	}
	delete(before, "TAGO_SERVICE_KEY")
	delete(after, "TAGO_SERVICE_KEY")
	if !reflect.DeepEqual(before, after) {
		return fmt.Errorf("TAGO 외 설정이 변경된 계획은 적용하지 않습니다")
	}
	return nil
}
