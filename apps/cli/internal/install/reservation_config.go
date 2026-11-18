package install

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
)

// automaticReservationKeys are the installation-wide switches that let a watch which
// chose automatic reservation reserve once before payment. Railwait moves them
// together; each watch still opts in on its own.
var automaticReservationKeys = []string{"KORAIL_RESERVATION_ONCE_ENABLED", "SRT_RESERVATION_ONCE_ENABLED"}

func reservationSwitchValue(enabled bool) string {
	if enabled {
		return "true"
	}
	return "false"
}

// AutomaticReservationEnabled reports whether every reservation switch is on.
func AutomaticReservationEnabled(values map[string]string) bool {
	for _, key := range automaticReservationKeys {
		if values[key] != "true" {
			return false
		}
	}
	return true
}

// ReservationEnvironment sets both reservation switches and keeps every other line.
func ReservationEnvironment(data []byte, enabled bool) ([]byte, error) {
	if _, err := ParseEnv(string(data)); err != nil {
		return nil, err
	}
	value := reservationSwitchValue(enabled)
	lines := strings.Split(string(data), "\n")
	found := map[string]bool{}
	for n, line := range lines {
		name, _, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		for _, key := range automaticReservationKeys {
			if strings.TrimSpace(name) == key {
				lines[n] = key + "='" + value + "'"
				found[key] = true
			}
		}
	}
	var missing []string
	for _, key := range automaticReservationKeys {
		if !found[key] {
			missing = append(missing, key+"='"+value+"'")
		}
	}
	if len(missing) > 0 {
		if last := len(lines) - 1; last >= 0 && lines[last] == "" {
			lines = append(lines[:last], append(missing, "")...)
		} else {
			lines = append(lines, missing...)
		}
	}
	return []byte(strings.Join(lines, "\n")), nil
}

// PlanConfigureReservation turns automatic reservation on or off through the same
// drain, encrypted backup and restart operation as the other settings.
func (i *Installer) PlanConfigureReservation(ctx context.Context, path string, enabled bool) (UpdatePlan, error) {
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
	unchanged := true
	for _, key := range automaticReservationKeys {
		if values[key] != reservationSwitchValue(enabled) {
			unchanged = false
		}
	}
	if unchanged && enabled {
		return UpdatePlan{}, fmt.Errorf("자동 예매가 이미 켜져 있습니다")
	}
	if unchanged {
		return UpdatePlan{}, fmt.Errorf("자동 예매가 이미 꺼져 있습니다")
	}
	candidate, err := ReservationEnvironment(data, enabled)
	if err != nil {
		return UpdatePlan{}, err
	}
	r := installedRelease(s)
	plan, err := i.makePlanMode(ctx, s, r, true)
	if err != nil {
		return plan, err
	}
	plan.Steps = []string{"checking", "preparing_config", "draining", "backing_up", "applying", "verifying", "resuming"}
	return savePlan(s, plan, r.SHA256, "configure_reservation", candidate)
}

func validateReservationCandidate(original, candidate []byte) error {
	before, err := ParseEnv(string(original))
	if err != nil {
		return err
	}
	after, err := ParseEnv(string(candidate))
	if err != nil {
		return err
	}
	value := after[automaticReservationKeys[0]]
	if value != "true" && value != "false" {
		return fmt.Errorf("자동 예매 설정값이 잘못되었습니다")
	}
	for _, key := range automaticReservationKeys {
		if after[key] != value {
			return fmt.Errorf("KORAIL과 SRT 자동 예매 설정은 함께 바꿉니다")
		}
		delete(before, key)
		delete(after, key)
	}
	if !reflect.DeepEqual(before, after) {
		return fmt.Errorf("자동 예매 외 설정이 변경된 계획은 적용하지 않습니다")
	}
	return nil
}

// ReservationPlanEnables reports which way a saved reservation plan sets the
// switches, so the CLI can refuse to apply a plan made for the opposite request.
func ReservationPlanEnables(path, planID string) (bool, error) {
	if !regexp.MustCompile(`^[a-f0-9]{64}$`).MatchString(planID) {
		return false, fmt.Errorf("유효한 설정 변경 계획이 필요합니다")
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return false, err
	}
	dir := filepath.Join(abs, ".railwait", "plans")
	data, err := os.ReadFile(filepath.Join(dir, planID+".json"))
	if err != nil {
		return false, fmt.Errorf("설정 변경 계획을 읽지 못했습니다")
	}
	var saved savedUpdatePlan
	if json.Unmarshal(data, &saved) != nil || saved.Kind != "configure_reservation" {
		return false, fmt.Errorf("자동 예매 설정 계획이 아닙니다")
	}
	candidate, err := os.ReadFile(filepath.Join(dir, planID+".candidate.env"))
	if err != nil || hashBytes(candidate) != saved.CandidateSHA256 {
		return false, fmt.Errorf("설정 후보가 계획과 일치하지 않습니다")
	}
	values, err := ParseEnv(string(candidate))
	if err != nil {
		return false, err
	}
	return AutomaticReservationEnabled(values), nil
}
