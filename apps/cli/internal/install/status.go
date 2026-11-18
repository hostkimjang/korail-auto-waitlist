package install

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

type ServiceStatus struct {
	Service  string `json:"service"`
	State    string `json:"state"`
	Health   string `json:"health"`
	ExitCode int    `json:"exit_code"`
}
type Status struct {
	Installation State           `json:"installation"`
	Services     []ServiceStatus `json:"services"`
	Healthy      bool            `json:"healthy"`
}

func (i *Installer) Status(ctx context.Context, path string) (Status, error) {
	s, err := ReadState(path)
	if err != nil {
		return Status{}, err
	}
	return i.statusState(ctx, s)
}

func (i *Installer) statusState(ctx context.Context, s State) (Status, error) {
	var err error
	result := Status{Installation: s, Services: []ServiceStatus{}}
	if err = verifyLocalState(ctx, s); err != nil {
		return result, err
	}
	if err = i.preflight(ctx, &s); err != nil {
		return result, err
	}
	if s.ComposeFile == "" {
		return result, nil
	}
	if err = verifyBundle(filepath.Dir(s.ComposeFile), s.BundleHashes); err != nil {
		return result, err
	}
	out, err := i.compose(ctx, s, nil, "ps", "--all", "--format", "json")
	if err != nil {
		return result, err
	}
	services, err := decodeServices(out)
	if err != nil {
		return result, err
	}
	result.Services = services
	result.Healthy = len(services) > 0
	seen := map[string]bool{}
	for _, service := range services {
		seen[service.Service] = true
	}
	if len(s.ExpectedImages) == 0 {
		result.Healthy = false
	}
	for name := range s.ExpectedImages {
		if !seen[name] {
			result.Healthy = false
		}
	}
	ids, err := i.compose(ctx, s, nil, "ps", "--all", "--quiet")
	if err != nil {
		return result, err
	}
	if names := strings.Fields(string(ids)); len(names) > 0 {
		for _, name := range names {
			for _, c := range name {
				if !(c >= 'a' && c <= 'f' || c >= '0' && c <= '9') {
					return result, fmt.Errorf("컨테이너 식별자가 잘못되었습니다")
				}
			}
		}
		out, err := i.docker(ctx, s, nil, append([]string{"inspect", "--format", `{{json .Config.Labels}} {{json .Config.Image}}`}, names...)...)
		if err != nil {
			return result, err
		}
		matched := map[string]bool{}
		for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
			decoder := json.NewDecoder(strings.NewReader(line))
			var labels map[string]string
			var image string
			if decoder.Decode(&labels) != nil || decoder.Decode(&image) != nil {
				return result, fmt.Errorf("컨테이너 이미지 응답이 잘못되었습니다")
			}
			service := labels["com.docker.compose.service"]
			expected, ok := s.ExpectedImages[service]
			if !ok || image != expected || labels["com.docker.compose.project"] != s.Project {
				result.Healthy = false
			} else {
				matched[service] = true
			}
		}
		for service := range s.ExpectedImages {
			if !matched[service] {
				result.Healthy = false
			}
		}
	} else {
		result.Healthy = false
	}
	for _, service := range services {
		oneShot := service.Service == "migration" || service.Service == "log-init"
		if oneShot {
			if service.State != "exited" || service.ExitCode != 0 {
				result.Healthy = false
			}
		} else if service.State != "running" || service.Health != "healthy" {
			result.Healthy = false
		}
	}
	return result, nil
}

func decodeServices(data []byte) ([]ServiceStatus, error) {
	type composeService struct {
		Service  string
		State    string
		Health   string
		ExitCode int
	}
	var raw []composeService
	trimmed := bytes.TrimSpace(data)
	if len(trimmed) == 0 {
		return []ServiceStatus{}, nil
	}
	if trimmed[0] == '[' {
		if json.Unmarshal(trimmed, &raw) != nil {
			return nil, fmt.Errorf("서비스 상태 응답이 잘못되었습니다")
		}
	} else {
		decoder := json.NewDecoder(bytes.NewReader(trimmed))
		for {
			var item composeService
			err := decoder.Decode(&item)
			if err == io.EOF {
				break
			}
			if err != nil {
				return nil, fmt.Errorf("서비스 상태 응답이 잘못되었습니다")
			}
			raw = append(raw, item)
		}
	}
	result := make([]ServiceStatus, 0, len(raw))
	for _, item := range raw {
		if item.Service == "" {
			return nil, fmt.Errorf("서비스 이름이 누락되었습니다")
		}
		result = append(result, ServiceStatus{item.Service, item.State, item.Health, item.ExitCode})
	}
	return result, nil
}

// Doctor performs read-only checks. It never logs in to a rail provider.
func (i *Installer) Doctor(ctx context.Context, path, dockerContext string) error {
	if dockerContext == "" {
		dockerContext = "default"
	}
	s := State{Path: path, DockerContext: dockerContext}
	if _, err := os.Stat(statePath(path)); err == nil {
		existing, readErr := ReadState(path)
		if readErr != nil {
			return readErr
		}
		s = existing
	} else if !os.IsNotExist(err) {
		return err
	}
	if err := i.preflight(ctx, &s); err != nil {
		return err
	}
	if s.ComposeFile != "" {
		if err := verifyBundle(filepath.Dir(s.ComposeFile), s.BundleHashes); err != nil {
			return err
		}
		if _, err := i.compose(ctx, s, nil, "config", "--quiet"); err != nil {
			return err
		}
	}
	return nil
}
