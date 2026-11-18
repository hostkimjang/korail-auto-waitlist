package install

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

type State struct {
	Schema           int               `json:"schema"`
	ID               string            `json:"installation_id"`
	Path             string            `json:"path"`
	Project          string            `json:"compose_project"`
	DockerContext    string            `json:"docker_context"`
	DockerEndpoint   string            `json:"docker_endpoint"`
	Release          string            `json:"release"`
	ComposeFile      string            `json:"compose_file"`
	Profiles         []string          `json:"profiles"`
	Port             int               `json:"port"`
	Stage            string            `json:"stage"`
	UpdatedAt        string            `json:"updated_at"`
	ExpectedImages   map[string]string `json:"expected_images"`
	BundleHashes     map[string]string `json:"bundle_hashes"`
	DatabaseRevision string            `json:"database_revision"`
	SettingsSchema   int               `json:"settings_schema"`
	ReleaseSource    string            `json:"release_source,omitempty"`
}

func statePath(path string) string { return filepath.Join(path, ".railwait", "install.json") }

func ReadState(path string) (State, error) {
	var s State
	data, err := os.ReadFile(statePath(path))
	if err != nil {
		return s, fmt.Errorf("설치 기록을 읽지 못했습니다: %w", err)
	}
	if err = json.Unmarshal(data, &s); err != nil {
		return s, fmt.Errorf("설치 기록이 잘못되었습니다")
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return s, err
	}
	if s.Schema != 1 || s.Path != abs || !regexp.MustCompile(`^[a-f0-9]{24}$`).MatchString(s.ID) || s.Project != "railwait-"+s.ID || s.Port < 1 || s.Port > 65535 || s.DockerContext == "" {
		return s, fmt.Errorf("설치 기록 계약이 일치하지 않습니다")
	}
	validStage := map[string]bool{"created": true, "fetching": true, "bundle_verified": true, "release_ready": true, "configured": true, "downloaded": true, "migrating": true, "admin_required": true, "starting": true, "complete": true}
	if !validStage[s.Stage] || (s.Release != "" && (strings.ContainsAny(s.Release, "/\\") || s.Release == "." || s.Release == "..")) {
		return s, fmt.Errorf("설치 기록의 단계나 릴리스가 잘못되었습니다")
	}
	if s.ComposeFile != "" {
		expected := filepath.Join(abs, "releases", s.Release, "compose.release.yml")
		if s.ComposeFile != expected {
			return s, fmt.Errorf("설치 Compose 경로가 일치하지 않습니다")
		}
	}
	if s.Stage != "created" && s.Stage != "fetching" && s.ComposeFile == "" {
		return s, fmt.Errorf("설치 Compose 경로가 없습니다")
	}
	if s.ReleaseSource != "" && s.ReleaseSource != "local_package" {
		return s, fmt.Errorf("지원하지 않는 설치 출처입니다")
	}
	if len(s.Profiles) > 1 || (len(s.Profiles) == 1 && s.Profiles[0] != "experimental-rail") {
		return s, fmt.Errorf("지원하지 않는 설치 프로필입니다")
	}
	return s, nil
}

func atomicWrite(path string, data []byte) error {
	if info, err := os.Lstat(path); err == nil && info.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("symbolic link is not supported: %s", filepath.Base(path))
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".railwait-write-*")
	if err != nil {
		return err
	}
	name := f.Name()
	defer os.Remove(name)
	if err = platform.SecureFile(name); err == nil {
		_, err = f.Write(data)
	}
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	// Windows does not replace an existing destination with os.Rename. A same-directory
	// replacement is provided by the platform package when available; Unix rename is atomic.
	return replaceFile(name, path)
}

func writeState(s State) error {
	data, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	return atomicWrite(statePath(s.Path), append(data, '\n'))
}
