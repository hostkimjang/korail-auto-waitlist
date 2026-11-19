package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/install"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

// installFixture writes a complete installation record, .env and release Compose file for
// commands that read them. Tests pair it with fake runners, so Docker is never called.
func installFixture(t *testing.T) (string, install.State) {
	t.Helper()
	root := filepath.Join(t.TempDir(), "railwait-install")
	if err := platform.SecureDir(filepath.Join(root, ".railwait")); err != nil {
		t.Fatal(err)
	}
	id := strings.Repeat("a1", 12)
	compose := filepath.Join(root, "releases", "1.4.10", "compose.release.yml")
	if err := os.MkdirAll(filepath.Dir(compose), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(compose, []byte("services:\n  api:\n    image: ghcr.io/example/api@sha256:"+strings.Repeat("a", 64)+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	digest := func(name, char string) string {
		return "ghcr.io/example/" + name + "@sha256:" + strings.Repeat(char, 64)
	}
	state := install.State{
		Schema: 1, ID: id, Path: root, Project: "railwait-" + id, DockerContext: "default", DockerEndpoint: "unix:///var/run/docker.sock",
		Release: "1.4.10", ComposeFile: compose, Profiles: []string{"experimental-rail"}, Port: 8080, Stage: "complete",
		ExpectedImages:   map[string]string{"api": digest("api", "a"), "migration": digest("api", "a"), "web": digest("web", "b"), "worker": digest("api", "a"), "postgres": digest("postgres", "d"), "redis": digest("redis", "e"), "log-init": digest("alpine", "f")},
		DatabaseRevision: "rev1", SettingsSchema: 1,
	}
	writeInstallState(t, state)
	if err := os.WriteFile(filepath.Join(root, ".env"), []byte("TZ=Asia/Seoul\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return root, state
}

func writeInstallState(t *testing.T, state install.State) {
	t.Helper()
	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(state.Path, ".railwait", "install.json"), data, 0o600); err != nil {
		t.Fatal(err)
	}
}

// refusingDocker records Docker commands and fails each one.
type refusingDocker struct{ commands []string }

func (r *refusingDocker) Run(_ context.Context, c install.Command) ([]byte, error) {
	r.commands = append(r.commands, strings.Join(c.Args, " "))
	return nil, errors.New("docker is not available in this test")
}

func testConfirmation(input string, interactive bool, out *bytes.Buffer) confirmation {
	return confirmation{in: bufio.NewReader(strings.NewReader(input)), out: out, interactive: interactive}
}

func TestRestartWithoutTerminalNeedsYes(t *testing.T) {
	root, _ := installFixture(t)
	docker := &refusingDocker{}
	var out bytes.Buffer
	err := restartRun{installer: install.Installer{Runner: docker}, confirm: testConfirmation("y\n", false, &out), out: &out}.run(context.Background(), root, false)
	if err == nil || !strings.Contains(err.Error(), "--yes") || len(docker.commands) != 0 {
		t.Fatalf("err=%v commands=%v", err, docker.commands)
	}
}

func TestRestartShowsWhatRestartsAndStopsWhenDeclined(t *testing.T) {
	root, _ := installFixture(t)
	docker := &refusingDocker{}
	var out bytes.Buffer
	err := restartRun{installer: install.Installer{Runner: docker}, confirm: testConfirmation("n\n", true, &out), out: &out}.run(context.Background(), root, false)
	if err == nil || !strings.Contains(err.Error(), "취소") || len(docker.commands) != 0 {
		t.Fatalf("err=%v commands=%v", err, docker.commands)
	}
	summary := out.String()
	for _, part := range []string{"다시 시작: api, web, worker (3개)", "PostgreSQL·Redis", "1~3분", "KORAIL 로그인 세션", "30분 이상", "[y/N]"} {
		if !strings.Contains(summary, part) {
			t.Fatalf("summary lacks %q:\n%s", part, summary)
		}
	}
}

func TestConfirmedRestartChecksTheEnvironmentBeforeAnythingStops(t *testing.T) {
	for _, test := range []struct {
		name, input string
		yes         bool
	}{{"answered", "예\n", false}, {"--yes", "", true}} {
		t.Run(test.name, func(t *testing.T) {
			root, _ := installFixture(t)
			docker := &refusingDocker{}
			var out bytes.Buffer
			err := restartRun{installer: install.Installer{Runner: docker}, confirm: testConfirmation(test.input, !test.yes, &out), out: &out}.run(context.Background(), root, test.yes)
			if err == nil || !strings.Contains(err.Error(), ".env") || !strings.Contains(err.Error(), "다시 시작하지 않았습니다") {
				t.Fatal(err)
			}
			if len(docker.commands) != 1 || !strings.Contains(docker.commands[0], "config --quiet") {
				t.Fatalf("commands: %v", docker.commands)
			}
			if test.yes && strings.Contains(out.String(), "[y/N]") {
				t.Fatal("--yes still asked for confirmation")
			}
		})
	}
}

func TestRestartRefusesAnIncompleteInstallation(t *testing.T) {
	root, state := installFixture(t)
	state.Stage = "starting"
	writeInstallState(t, state)
	docker := &refusingDocker{}
	var out bytes.Buffer
	err := restartRun{installer: install.Installer{Runner: docker}, confirm: testConfirmation("", false, &out), out: &out}.run(context.Background(), root, true)
	if err == nil || !strings.Contains(err.Error(), "설치가 완료되지 않아") || len(docker.commands) != 0 {
		t.Fatalf("err=%v commands=%v", err, docker.commands)
	}
}
