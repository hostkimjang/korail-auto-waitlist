package install

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/access"
)

var accessKeys = []string{"AUTH_ALLOWED_ORIGINS", "CORS_ORIGINS", "AUTH_COOKIE_SECURE"}

// AccessEnvironment restricts browser authentication to the selected private
// HTTPS origin. Secure cookies intentionally require using that HTTPS URL.
func AccessEnvironment(data []byte, origin string) ([]byte, error) {
	if err := access.ValidateOrigin(origin); err != nil {
		return nil, err
	}
	if _, err := ParseEnv(string(data)); err != nil {
		return nil, err
	}
	values := map[string]string{"AUTH_ALLOWED_ORIGINS": origin, "CORS_ORIGINS": origin, "AUTH_COOKIE_SECURE": "true"}
	lines := strings.Split(string(data), "\n")
	for index, line := range lines {
		key, _, ok := strings.Cut(line, "=")
		if value, exists := values[strings.TrimSpace(key)]; ok && exists {
			lines[index] = strings.TrimSpace(key) + "='" + value + "'"
			delete(values, strings.TrimSpace(key))
		}
	}
	for _, key := range accessKeys {
		if value, exists := values[key]; exists {
			lines = append(lines, key+"='"+value+"'")
		}
	}
	return []byte(strings.Join(lines, "\n")), nil
}

func AccessConfigured(data []byte, origin string) (bool, error) {
	if err := access.ValidateOrigin(origin); err != nil {
		return false, err
	}
	values, err := ParseEnv(string(data))
	if err != nil {
		return false, err
	}
	return values["AUTH_ALLOWED_ORIGINS"] == origin && values["CORS_ORIGINS"] == origin && values["AUTH_COOKIE_SECURE"] == "true", nil
}

func (i *Installer) PlanConfigureAccess(ctx context.Context, path, origin string) (UpdatePlan, error) {
	if err := access.ValidateOrigin(origin); err != nil {
		return UpdatePlan{}, err
	}
	s, err := ReadState(path)
	if err != nil {
		return UpdatePlan{}, err
	}
	data, err := os.ReadFile(filepath.Join(s.Path, ".env"))
	if err != nil {
		return UpdatePlan{}, err
	}
	if configured, err := AccessConfigured(data, origin); err != nil {
		return UpdatePlan{}, err
	} else if configured {
		return UpdatePlan{}, errors.New("이미 해당 HTTPS 주소를 사용합니다")
	}
	candidate, err := AccessEnvironment(data, origin)
	if err != nil {
		return UpdatePlan{}, err
	}
	r := installedRelease(s)
	plan, err := i.makePlanMode(ctx, s, r, planConfigure)
	if err != nil {
		return plan, err
	}
	plan.Steps = []string{"checking", "preparing_config", "draining", "backing_up", "applying", "verifying", "resuming"}
	return savePlan(s, plan, r.SHA256, "configure_access", candidate)
}

func validateAccessCandidate(original, candidate []byte) error {
	before, err := ParseEnv(string(original))
	if err != nil {
		return err
	}
	after, err := ParseEnv(string(candidate))
	if err != nil {
		return err
	}
	if configured, err := AccessConfigured(candidate, after["AUTH_ALLOWED_ORIGINS"]); err != nil || !configured {
		return errors.New("HTTPS 접속 설정 후보가 잘못되었습니다")
	}
	for _, key := range accessKeys {
		delete(before, key)
		delete(after, key)
	}
	if !reflect.DeepEqual(before, after) {
		return fmt.Errorf("HTTPS 출처·보안 쿠키 외 설정이 변경된 계획은 적용하지 않습니다")
	}
	return nil
}
