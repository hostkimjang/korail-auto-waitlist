package install

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"strings"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

func (i *Installer) preflight(ctx context.Context, s *State) error {
	out, err := i.docker(ctx, *s, nil, "context", "inspect", s.DockerContext)
	if err != nil {
		return fmt.Errorf("Docker context를 확인하지 못했습니다: %w", err)
	}
	var contexts []struct {
		Endpoints map[string]struct {
			Host string `json:"Host"`
		} `json:"Endpoints"`
	}
	if json.Unmarshal(out, &contexts) != nil || len(contexts) != 1 {
		return fmt.Errorf("Docker context 응답이 잘못되었습니다")
	}
	host := contexts[0].Endpoints["docker"].Host
	if !strings.HasPrefix(host, "unix:///") && !strings.HasPrefix(host, "npipe:////./pipe/") {
		return fmt.Errorf("로컬 Docker daemon만 지원합니다; 원격 context는 사용할 수 없습니다")
	}
	if s.DockerEndpoint != "" && s.DockerEndpoint != host {
		return fmt.Errorf("기록된 Docker context 대상이 변경되었습니다")
	}
	s.DockerEndpoint = host
	out, err = i.docker(ctx, *s, nil, "info", "--format", "{{.OSType}}")
	if err != nil {
		return fmt.Errorf("Docker daemon에 연결하지 못했습니다: %w", err)
	}
	if strings.TrimSpace(string(out)) != "linux" {
		return fmt.Errorf("Docker Linux containers가 필요합니다")
	}
	out, err = i.docker(ctx, *s, nil, "compose", "version", "--short")
	if err != nil {
		return fmt.Errorf("Docker Compose v2가 필요합니다: %w", err)
	}
	var major, minor int
	if _, err = fmt.Sscanf(strings.TrimPrefix(strings.TrimSpace(string(out)), "v"), "%d.%d", &major, &minor); err != nil || major < 2 || (major == 2 && minor < 24) {
		return fmt.Errorf("Docker Compose 2.24 이상이 필요합니다")
	}
	return nil
}

func (i *Installer) rejectConflicts(ctx context.Context, s State) error {
	for _, kind := range []string{"container", "volume", "network"} {
		args := []string{kind, "ls", "--filter", "label=com.docker.compose.project=" + s.Project, "--format", "{{.ID}}"}
		if kind == "container" {
			args = append(args, "--all")
		}
		if kind == "volume" || kind == "network" {
			args[len(args)-1] = "{{.Name}}"
		}
		out, err := i.docker(ctx, s, nil, args...)
		if err != nil {
			return err
		}
		if strings.TrimSpace(string(out)) != "" {
			return fmt.Errorf("기존 Compose %s 자원이 있습니다; install 대신 adopt가 필요합니다", kind)
		}
	}
	return nil
}

func checkPort(port int) error {
	listener, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", port))
	if err != nil {
		return fmt.Errorf("loopback 포트 %d를 사용할 수 없습니다", port)
	}
	return listener.Close()
}

func checkSpace(path string) error {
	available, err := platform.AvailableBytes(path)
	if err != nil {
		return fmt.Errorf("설치 파일시스템 여유 공간을 확인하지 못했습니다: %w", err)
	}
	if available < 4<<30 {
		return fmt.Errorf("설치 파일시스템에 최소 4 GiB 여유 공간이 필요합니다; Docker 저장소의 여유 공간도 확인하세요")
	}
	return nil
}
