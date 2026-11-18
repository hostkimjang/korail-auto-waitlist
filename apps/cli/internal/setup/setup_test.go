package setup

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
)

func TestDiscoveryIncludesUserInstallLocations(t *testing.T) {
	env := func(name string) string {
		return map[string]string{"LOCALAPPDATA": `C:\Users\User\AppData\Local`, "ProgramFiles": `C:\Program Files`}[name]
	}
	cases := []struct{ os, tool, wanted string }{
		{"darwin", "docker", "/Users/example/.docker/bin/docker"},
		{"darwin", "tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale"},
		{"windows", "docker", `C:\Users\User\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe`},
		{"windows", "tailscale", `C:\Program Files\Tailscale\tailscale.exe`},
		{"linux", "docker", "/usr/bin/docker"},
	}
	for _, tc := range cases {
		t.Run(tc.os+tc.tool, func(t *testing.T) {
			found := false
			for _, candidate := range executableCandidates(tc.os, tc.tool, "/Users/example", env) {
				found = found || candidate == tc.wanted
			}
			if !found {
				t.Fatalf("missing expected installation path %q", tc.wanted)
			}
		})
	}
	if got := executableCandidates("windows", "docker", "", func(string) string { return "relative" }); len(got) != 0 {
		t.Fatal("relative environment paths must not be used", got)
	}
}

func TestBrowserArgumentsDoNotUseShellExpansion(t *testing.T) {
	for _, goos := range []string{"darwin", "windows", "linux"} {
		_, args := browserCommand(goos, "https://example.com/?a=1&b=2")
		if args[len(args)-1] != "https://example.com/?a=1&b=2" {
			t.Fatal(goos, args)
		}
	}
	for _, address := range []string{"file:///etc/passwd", "http://external.example/", "javascript:alert(1)", "https://user:pass@example.com", "https://example.com/\nanything"} {
		if OpenURL(context.Background(), address) == nil {
			t.Fatal("accepted unsafe browser address", address)
		}
	}
}

type dockerFixture struct {
	current, endpoint, os, compose string
	missing, offline               bool
	started, opened, contacted     int
	calls                          [][]string
}

func (f *dockerFixture) operations() dockerOperations {
	return dockerOperations{
		find: func(string) (string, error) {
			if f.missing {
				return "", errors.New("not found")
			}
			return "/existing/docker", nil
		},
		run: func(_ context.Context, _ string, args ...string) ([]byte, error) {
			f.calls = append(f.calls, args)
			if reflect.DeepEqual(args, []string{"context", "show"}) {
				return []byte(f.current), nil
			}
			if len(args) == 3 && args[0] == "context" && args[1] == "inspect" {
				return []byte(`[{"Endpoints":{"docker":{"Host":"` + f.endpoint + `"}}}]`), nil
			}
			if len(args) >= 3 && args[0] == "--host" && args[1] == f.endpoint {
				f.contacted++
				if f.offline {
					return nil, errors.New("offline")
				}
				if args[2] == "info" {
					return []byte(f.os), nil
				}
				return []byte(f.compose), nil
			}
			return nil, errors.New("unexpected command")
		},
		open:    func(context.Context, string) error { f.opened++; return nil },
		desktop: func() string { return "/Applications/Docker.app" },
		start: func(context.Context, string) error {
			f.started++
			f.offline, f.missing = false, false
			return nil
		},
		guide: guide("darwin", "docker"),
	}
}

func readyFixture() *dockerFixture {
	return &dockerFixture{current: "lima-homeserver", endpoint: "unix:///example/docker.sock", os: "linux", compose: "v5.7.1"}
}

func TestReadyExistingLimaIsReusedWithoutPromptsOrMutations(t *testing.T) {
	f := readyFixture()
	result, err := ensureDocker(context.Background(), "", func(Need) (Action, error) {
		t.Fatal("ready environment must not prompt")
		return Cancel, nil
	}, f.operations())
	if err != nil || result.Context != "lima-homeserver" || result.Endpoint != f.endpoint || f.started != 0 || f.opened != 0 {
		t.Fatal(result, err, f)
	}
	for _, args := range f.calls {
		if strings.Join(args, " ") == "context use lima-homeserver" {
			t.Fatal("global Docker context changed")
		}
	}
}

func TestExplicitContextDoesNotReadOrChangeGlobalContext(t *testing.T) {
	f := readyFixture()
	result, err := ensureDocker(context.Background(), "selected", nil, f.operations())
	if err != nil || result.Context != "selected" {
		t.Fatal(result, err)
	}
	for _, args := range f.calls {
		if len(args) == 2 && args[0] == "context" && args[1] == "show" {
			t.Fatal("explicit context must not follow global selection")
		}
	}
}

func TestRemoteContextIsRejectedBeforeContactingDaemon(t *testing.T) {
	f := readyFixture()
	f.endpoint = "ssh://remote.example"
	_, err := ensureDocker(context.Background(), "", nil, f.operations())
	if err == nil || f.contacted != 0 || f.started != 0 {
		t.Fatal(err, f)
	}
}

func TestMissingDockerGuideAndRetryStayInSameFlow(t *testing.T) {
	f := readyFixture()
	f.missing = true
	prompts := 0
	_, err := ensureDocker(context.Background(), "", func(need Need) (Action, error) {
		prompts++
		if need.Kind != "docker_missing" {
			t.Fatal(need)
		}
		if prompts == 1 {
			return OpenGuide, nil
		}
		f.missing = false
		return Retry, nil
	}, f.operations())
	if err != nil || f.opened != 1 || f.started != 0 || prompts != 2 {
		t.Fatal(err, f, prompts)
	}
}

func TestOfflineLimaCannotStartDifferentDesktopEngine(t *testing.T) {
	for _, missing := range []bool{false, true} {
		f := readyFixture()
		f.offline, f.missing = true, missing
		_, err := ensureDocker(context.Background(), "lima-homeserver", func(need Need) (Action, error) {
			if need.CanStart {
				t.Fatal("must not offer a different engine for Lima")
			}
			return Start, nil
		}, f.operations())
		if err == nil || f.started != 0 {
			t.Fatal(err, f)
		}
	}
}

func TestDesktopStartsOnlyAfterExplicitActionThenReadinessPasses(t *testing.T) {
	f := readyFixture()
	f.current, f.offline = "desktop-linux", true
	f.endpoint = "unix:///Users/user/.docker/run/docker.sock"
	_, err := ensureDocker(context.Background(), "", func(need Need) (Action, error) {
		if !need.CanStart {
			t.Fatal(need)
		}
		return Start, nil
	}, f.operations())
	if err != nil || f.started != 1 {
		t.Fatal(err, f)
	}
}

func TestDefaultNamedCustomEngineCannotBeReplacedByDesktop(t *testing.T) {
	f := readyFixture()
	f.current, f.offline = "default", true
	f.endpoint = "unix:///Users/user/.colima/default/docker.sock"
	_, need := probeDocker(context.Background(), "", f.operations())
	if need == nil || need.CanStart {
		t.Fatal("context name alone must not identify the Docker engine", need)
	}
}

func TestNoninteractiveAndCancellationNeverOpenApps(t *testing.T) {
	f := readyFixture()
	f.missing = true
	if _, err := ensureDocker(context.Background(), "", nil, f.operations()); err == nil || !strings.Contains(err.Error(), "https://docs.docker.com") {
		t.Fatal(err)
	}
	if _, err := ensureDocker(context.Background(), "", func(Need) (Action, error) { return Cancel, nil }, f.operations()); !errors.Is(err, ErrCancelled) {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := ensureDocker(ctx, "", nil, f.operations()); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	if f.started != 0 || f.opened != 0 {
		t.Fatal(f)
	}
}

func TestDockerModeAndComposeCompatibility(t *testing.T) {
	for _, version := range []string{"2.24.0", "v2.24.1-desktop.2", "5.7.1"} {
		if !supportedCompose(version) {
			t.Fatal(version)
		}
	}
	for _, version := range []string{"", "2.23.9", "1.29.1", "not-a-version"} {
		if supportedCompose(version) {
			t.Fatal(version)
		}
	}
	f := readyFixture()
	f.os = "windows"
	_, need := probeDocker(context.Background(), "", f.operations())
	if need == nil || need.Kind != "docker_mode" {
		t.Fatal(need)
	}
}

func TestDockerProbeExcludesHostAndContextOverridesAndSecrets(t *testing.T) {
	env := dockerProbeEnvironment([]string{"PATH=/bin", "HOME=/home/user", "DOCKER_HOST=tcp://unwanted:2375", "DOCKER_CONTEXT=remote", "DOCKER_CONFIG=/other", "COMPOSE_PROJECT_NAME=production", "SECRET=do-not-pass", "SystemRoot=C:\\Windows"})
	joined := strings.Join(env, "\n")
	for _, forbidden := range []string{"unwanted", "remote", "/other", "production", "do-not-pass"} {
		if strings.Contains(joined, forbidden) {
			t.Fatal(joined)
		}
	}
	if !strings.Contains(joined, "SystemRoot=") || !strings.Contains(joined, "PATH=") {
		t.Fatal(env)
	}
}
