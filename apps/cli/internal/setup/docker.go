package setup

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

var ErrCancelled = errors.New("설치 준비를 중단했습니다. 같은 명령으로 다시 시작할 수 있습니다")

const dockerReadyTimeout = 5 * time.Minute

type DockerReady struct {
	Executable string
	Context    string
	Endpoint   string
}

type Interact func(Need) (Action, error)

type dockerOperations struct {
	find       func(string) (string, error)
	run        func(context.Context, string, ...string) ([]byte, error)
	open       func(context.Context, string) error
	desktop    func() string
	start      func(context.Context, string) error
	wait       func(context.Context) error
	guide      Need
	canInstall func(string) bool
}

// EnsureDocker reuses an existing local Docker context. It does not change the
// global current context, start a different engine, or install any host package.
func EnsureDocker(ctx context.Context, requestedContext string, interact Interact) (DockerReady, error) {
	return ensureDocker(ctx, requestedContext, interact, dockerOperations{
		find: Executable, run: runDockerProbe, open: OpenURL, desktop: installedDesktop, start: startDesktop,
		wait: waitDockerPoll, guide: Guide("docker"), canInstall: CanInstallDependency,
	})
}

func ensureDocker(ctx context.Context, requested string, interact Interact, ops dockerOperations) (DockerReady, error) {
	for {
		if err := ctx.Err(); err != nil {
			return DockerReady{}, err
		}
		ready, need := probeDocker(ctx, requested, ops)
		if err := ctx.Err(); err != nil {
			return DockerReady{}, err
		}
		if need == nil {
			return ready, nil
		}
		if interact == nil {
			return DockerReady{}, errors.New(need.Detail + " " + need.URL)
		}
		action, err := interact(*need)
		if err != nil {
			return DockerReady{}, err
		}
		switch action {
		case Cancel:
			return DockerReady{}, ErrCancelled
		case OpenGuide:
			if err := ops.open(ctx, need.URL); err != nil {
				return DockerReady{}, err
			}
		case Start:
			if !need.CanStart {
				return DockerReady{}, errors.New("선택한 Docker 환경을 다른 엔진으로 바꾸지 않습니다. 기존 환경을 직접 시작해 주세요")
			}
			return startDockerAndWait(ctx, requested, ready, ops)
		case Retry:
		default:
			return DockerReady{}, errors.New("알 수 없는 설치 준비 동작입니다")
		}
	}
}

func startDockerAndWait(ctx context.Context, requested string, initial DockerReady, ops dockerOperations) (DockerReady, error) {
	waitCtx, cancel := context.WithTimeout(ctx, dockerReadyTimeout)
	defer cancel()
	if err := waitCtx.Err(); err != nil {
		return DockerReady{}, dockerWaitError(ctx, err)
	}
	if err := ops.start(waitCtx, ops.desktop()); err != nil {
		if waitCtx.Err() != nil {
			return DockerReady{}, dockerWaitError(ctx, waitCtx.Err())
		}
		return DockerReady{}, errors.New("Docker Desktop을 시작하지 못했습니다. 앱을 직접 열어 주세요")
	}
	selected, endpoint := requested, initial.Endpoint
	if selected == "" {
		selected = initial.Context
	}
	// A fresh Desktop installation may create desktop-linux after starting from
	// default. Only this automatic selection may transition; an explicit context
	// or an unrelated global selection is never replaced while waiting.
	allowDesktopTransition := requested == "" && (selected == "" || selected == "default")
	probe := func(name string) (DockerReady, *Need, error) {
		ready, need := probeDocker(waitCtx, name, ops)
		if err := waitCtx.Err(); err != nil {
			return DockerReady{}, nil, dockerWaitError(ctx, err)
		}
		if ready.Context != "" && ready.Context != "default" && ready.Context != "desktop-linux" ||
			ready.Endpoint != "" && !desktopContext(ready.Context, ready.Endpoint) {
			return DockerReady{}, nil, errors.New("Docker Desktop 준비 중 다른 Docker 환경이 선택되었습니다. 기존 환경을 보존하고 중단합니다")
		}
		return ready, need, nil
	}
	wait := ops.wait
	if wait == nil {
		wait = waitDockerPoll
	}
	for {
		if err := waitCtx.Err(); err != nil {
			return DockerReady{}, dockerWaitError(ctx, err)
		}
		ready, need, err := probe(selected)
		if err != nil {
			return DockerReady{}, err
		}
		if endpoint != "" && ready.Endpoint != "" && endpoint != ready.Endpoint {
			return DockerReady{}, errors.New("Docker Desktop 준비 중 연결 주소가 변경되었습니다. 기존 환경을 보존하고 같은 명령으로 다시 확인해 주세요")
		}
		if ready.Context != "" {
			selected = ready.Context
		}
		if ready.Endpoint != "" {
			endpoint = ready.Endpoint
		}
		if need == nil {
			return ready, nil
		}
		if allowDesktopTransition && selected == "default" {
			alternate, waiting, err := probe("desktop-linux")
			if err != nil {
				return DockerReady{}, err
			}
			if alternate.Endpoint != "" {
				selected, endpoint, allowDesktopTransition = alternate.Context, alternate.Endpoint, false
				if waiting == nil {
					return alternate, nil
				}
			}
		}
		if err := wait(waitCtx); err != nil {
			return DockerReady{}, dockerWaitError(ctx, err)
		}
	}
}

func waitDockerPoll(ctx context.Context) error {
	select {
	case <-time.After(2 * time.Second):
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func dockerWaitError(parent context.Context, err error) error {
	if parent.Err() != nil {
		return parent.Err()
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return fmt.Errorf("Docker Desktop이 5분 안에 준비되지 않았습니다. Docker 창의 약관·초기 설정을 마친 뒤 같은 명령으로 다시 실행하세요: %w", context.DeadlineExceeded)
	}
	return err
}

func probeDocker(ctx context.Context, requested string, ops dockerOperations) (DockerReady, *Need) {
	need := ops.guide
	executable, err := ops.find("docker")
	if err != nil {
		need.Kind = "docker_missing"
		need.CanStart = (requested == "" || requested == "default" || requested == "desktop-linux") && ops.desktop() != ""
		need.CanInstall = !need.CanStart && (requested == "" || requested == "default" || requested == "desktop-linux") && ops.desktop() == "" && ops.canInstall != nil && ops.canInstall("docker")
		return DockerReady{}, &need
	}
	selected := requested
	if selected == "" {
		out, err := ops.run(ctx, executable, "context", "show")
		if err != nil {
			need.Kind, need.Detail = "docker_context", "현재 Docker 환경을 확인하지 못했습니다. Docker 앱 또는 기존 VM을 준비한 뒤 다시 확인해 주세요."
			return DockerReady{}, &need
		}
		selected = strings.TrimSpace(string(out))
	}
	if selected == "" || strings.ContainsAny(selected, "\x00\r\n") || strings.HasPrefix(selected, "-") {
		need.Kind, need.Detail = "docker_context", "올바른 로컬 Docker context를 선택해 주세요."
		return DockerReady{}, &need
	}
	ready := DockerReady{Executable: executable, Context: selected}
	out, err := ops.run(ctx, executable, "context", "inspect", selected)
	var contexts []struct {
		Endpoints map[string]struct{ Host string } `json:"Endpoints"`
	}
	if err != nil || json.Unmarshal(out, &contexts) != nil || len(contexts) != 1 {
		need.Kind, need.Detail = "docker_context", "선택한 Docker context가 준비되지 않았습니다. 기존 환경을 시작하거나 --docker-context로 사용할 환경을 지정해 주세요."
		return ready, &need
	}
	ready.Endpoint = contexts[0].Endpoints["docker"].Host
	if !strings.HasPrefix(ready.Endpoint, "unix:///") && !strings.HasPrefix(ready.Endpoint, "npipe:////./pipe/") {
		need.Kind, need.Detail = "docker_remote", "이 설치는 로컬 Docker만 지원합니다. 원격 context를 바꾸지 않고 중단했습니다. --docker-context로 로컬 환경을 지정해 주세요."
		return ready, &need
	}
	out, err = ops.run(ctx, executable, "--host", ready.Endpoint, "info", "--format", "{{.OSType}}")
	if err != nil {
		need.Kind, need.Detail = "docker_stopped", "선택한 Docker 환경에 연결하지 못했습니다. 해당 앱 또는 VM을 시작하고 접근 권한을 확인한 뒤 다시 확인해 주세요."
		need.CanStart = desktopContext(selected, ready.Endpoint) && ops.desktop() != ""
		return ready, &need
	}
	if strings.TrimSpace(string(out)) != "linux" {
		need.Kind, need.Detail = "docker_mode", "Docker를 Linux containers 모드로 전환한 뒤 다시 확인해 주세요."
		return ready, &need
	}
	out, err = ops.run(ctx, executable, "--host", ready.Endpoint, "compose", "version", "--short")
	if err != nil || !supportedCompose(strings.TrimSpace(string(out))) {
		need.Kind, need.Detail = "compose_version", "Docker Compose 2.24 이상이 필요합니다. Docker Desktop 또는 Compose plugin을 업데이트한 뒤 다시 확인해 주세요."
		return ready, &need
	}
	return ready, nil
}

func desktopContext(name, endpoint string) bool {
	if name != "default" && name != "desktop-linux" {
		return false
	}
	return endpoint == "unix:///var/run/docker.sock" ||
		strings.HasSuffix(endpoint, "/.docker/run/docker.sock") ||
		endpoint == "npipe:////./pipe/docker_engine" ||
		endpoint == "npipe:////./pipe/dockerDesktopLinuxEngine"
}

func supportedCompose(version string) bool {
	parts := strings.Split(strings.TrimPrefix(version, "v"), ".")
	if len(parts) < 2 {
		return false
	}
	major, majorErr := strconv.Atoi(parts[0])
	minor, minorErr := strconv.Atoi(parts[1])
	return majorErr == nil && minorErr == nil && (major > 2 || (major == 2 && minor >= 24))
}

func runDockerProbe(ctx context.Context, program string, args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, program, args...)
	cmd.Env = dockerProbeEnvironment(os.Environ())
	var output limitedOutput
	cmd.Stdout, cmd.Stderr = &output, io.Discard
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("Docker 환경 확인에 실패했습니다")
	}
	return output.Bytes(), nil
}

func dockerProbeEnvironment(values []string) []string {
	allowed := map[string]bool{"PATH": true, "HOME": true, "USERPROFILE": true, "SYSTEMROOT": true, "WINDIR": true, "TEMP": true, "TMP": true, "LOCALAPPDATA": true, "APPDATA": true, "PROGRAMDATA": true, "XDG_RUNTIME_DIR": true}
	result := []string{"COMPOSE_DISABLE_ENV_FILE=1", "DOCKER_CLI_HINTS=false"}
	for _, value := range values {
		name, _, ok := strings.Cut(value, "=")
		if ok && allowed[strings.ToUpper(name)] {
			result = append(result, value)
		}
	}
	return result
}

type limitedOutput struct{ bytes.Buffer }

func (b *limitedOutput) Write(data []byte) (int, error) {
	if b.Len()+len(data) > 128<<10 {
		return 0, errors.New("Docker 확인 응답이 너무 큽니다")
	}
	return b.Buffer.Write(data)
}
