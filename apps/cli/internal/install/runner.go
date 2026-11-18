package install

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/setup"
)

// Command never includes credentials in its arguments. Input is only passed on stdin.
type Command struct {
	Args  []string
	Dir   string
	Env   []string
	Input io.Reader
}

type Runner interface {
	Run(context.Context, Command) ([]byte, error)
}
type ExecRunner struct{}

func (ExecRunner) Run(ctx context.Context, c Command) ([]byte, error) {
	executable, err := setup.Executable("docker")
	if err != nil {
		return nil, fmt.Errorf("Docker 실행 파일을 찾지 못했습니다: %w", err)
	}
	cmd := exec.CommandContext(ctx, executable, c.Args...)
	cmd.Dir, cmd.Env, cmd.Stdin = c.Dir, c.Env, c.Input
	var out bytes.Buffer
	cmd.Stdout = &out
	// Docker errors can contain expanded configuration; never forward raw stderr.
	cmd.Stderr = io.Discard
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("Docker command failed: %w", err)
	}
	return out.Bytes(), nil
}

// DockerEnvironment excludes interpolation and target overrides inherited from the shell.
func DockerEnvironment(environ []string) []string {
	allow := map[string]bool{"PATH": true, "HOME": true, "USERPROFILE": true, "SYSTEMROOT": true, "WINDIR": true, "TEMP": true, "TMP": true, "LOCALAPPDATA": true, "APPDATA": true, "PROGRAMDATA": true, "XDG_RUNTIME_DIR": true}
	result := []string{"COMPOSE_DISABLE_ENV_FILE=1", "DOCKER_CLI_HINTS=false"}
	for _, item := range environ {
		key, _, ok := strings.Cut(item, "=")
		if ok && allow[strings.ToUpper(key)] {
			result = append(result, item)
		}
	}
	return result
}

func (i *Installer) docker(ctx context.Context, s State, input io.Reader, args ...string) ([]byte, error) {
	target := []string{"--context", s.DockerContext}
	if s.DockerEndpoint != "" {
		target = []string{"--host", s.DockerEndpoint}
	}
	dir := s.Path
	if s.ComposeFile == "" {
		dir = ""
	}
	return i.Runner.Run(ctx, Command{Args: append(target, args...), Dir: dir, Env: DockerEnvironment(os.Environ()), Input: input})
}

func (i *Installer) compose(ctx context.Context, s State, input io.Reader, args ...string) ([]byte, error) {
	base := []string{"compose", "--project-name", s.Project, "--project-directory", filepath.Dir(s.ComposeFile), "--env-file", s.Path + string(os.PathSeparator) + ".env", "-f", s.ComposeFile}
	for _, profile := range s.Profiles {
		base = append(base, "--profile", profile)
	}
	return i.docker(ctx, s, input, append(base, args...)...)
}
