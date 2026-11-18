package install

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/release"
)

type fakeReleases struct {
	fail  bool
	calls int
}

func (f *fakeReleases) Resolve(context.Context, string) (release.Release, error) {
	f.calls++
	if f.fail {
		return release.Release{}, errors.New("signature rejected")
	}
	return release.Release{Version: "v1.2.3", Images: map[string]string{"api": "ghcr.io/example/api@sha256:" + strings.Repeat("a", 64)}}, nil
}
func (f *fakeReleases) DownloadAndExtract(_ context.Context, _ release.Release, path string) error {
	if err := os.WriteFile(filepath.Join(path, "compose.release.yml"), []byte("services: {}\n"), 0600); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(path, ".env.example"), []byte("POSTGRES_PASSWORD=\nSECRET_ENCRYPTION_KEY=\nAUTH_SESSION_SECRET=\n"), 0600)
}

type fakeRunner struct {
	commands      []Command
	inputs        []string
	conflict      bool
	endpoint      string
	failure       string
	unhealthy     bool
	configured    bool
	wrongImage    bool
	mountFailures int
}

func testInstaller(runner *fakeRunner, source *fakeReleases) Installer {
	return Installer{Runner: runner, Releases: source, CheckPort: func(int) error { return nil }, CheckSpace: func(string) error { return nil }, ProbeInterval: time.Millisecond}
}

func (r *fakeRunner) Run(_ context.Context, c Command) ([]byte, error) {
	r.commands = append(r.commands, c)
	input := ""
	if c.Input != nil {
		data, _ := io.ReadAll(c.Input)
		input = string(data)
	}
	r.inputs = append(r.inputs, input)
	args := strings.Join(c.Args, " ")
	if r.failure != "" && strings.Contains(args, r.failure) {
		return nil, errors.New("injected failure")
	}
	switch {
	case strings.Contains(args, "context inspect"):
		endpoint := r.endpoint
		if endpoint == "" {
			endpoint = "unix:///var/run/docker.sock"
		}
		return []byte(`[{"Endpoints":{"docker":{"Host":"` + endpoint + `"}}}]`), nil
	case strings.Contains(args, "info --format"):
		return []byte("linux\n"), nil
	case strings.Contains(args, "compose version"):
		return []byte("2.39.0\n"), nil
	case strings.Contains(args, "ps --all --quiet"):
		return []byte(strings.Repeat("b", 64) + "\n"), nil
	case strings.Contains(args, "inspect --format"):
		var state State
		data, _ := os.ReadFile(statePath(c.Dir))
		json.Unmarshal(data, &state)
		digest := strings.Repeat("a", 64)
		if r.wrongImage {
			digest = strings.Repeat("c", 64)
		}
		return []byte(`{"com.docker.compose.service":"api","com.docker.compose.project":"` + state.Project + `"} "ghcr.io/example/api@sha256:` + digest + `"`), nil
	case strings.Contains(args, " ls --filter"):
		if r.conflict {
			return []byte("existing\n"), nil
		}
		return nil, nil
	case strings.Contains(args, "rail_waitlist.admin_auth.bootstrap"):
		if strings.Contains(input, "check_only") {
			if r.configured {
				return []byte(`{"status":"already_configured"}`), nil
			}
			return []byte(`{"status":"not_configured"}`), nil
		}
		r.configured = true
		return []byte(`{"status":"created"}`), nil
	case strings.Contains(args, "rail_waitlist.maintenance --status"):
		if r.mountFailures > 0 {
			r.mountFailures--
			return nil, errors.New("transient shared filesystem visibility")
		}
		data, err := os.ReadFile(filepath.Join(c.Dir, ".railwait", "manager", "control", "maintenance.json"))
		if err != nil {
			return nil, err
		}
		var gate map[string]any
		if err = json.Unmarshal(data, &gate); err != nil {
			return nil, err
		}
		gate["drained"] = true
		return json.Marshal(gate)
	case strings.Contains(args, "ps --all"):
		if r.unhealthy {
			return []byte(`{"Service":"api","State":"running","Health":"unhealthy"}`), nil
		}
		return []byte(`{"Service":"api","State":"running","Health":"healthy"}`), nil
	default:
		return nil, nil
	}
}

func TestInstallCreatesPrivateSecretsAndBootstrapsBeforePublicServices(t *testing.T) {
	root := filepath.Join(t.TempDir(), "installation")
	runner := &fakeRunner{}
	source := &fakeReleases{}
	installer := testInstaller(runner, source)
	state, err := installer.Install(context.Background(), Options{Path: root, Credentials: func() (Credentials, error) {
		return Credentials{Username: "admin", Password: "never-print-this-value"}, nil
	}})
	if err != nil {
		t.Fatal(err)
	}
	if state.Stage != "complete" {
		t.Fatalf("stage=%s", state.Stage)
	}
	data, err := os.ReadFile(filepath.Join(root, ".env"))
	if err != nil {
		t.Fatal(err)
	}
	values, err := ParseEnv(string(data))
	if err != nil {
		t.Fatal(err)
	}
	seen := map[string]bool{}
	for _, key := range secretKeys {
		value := values[key]
		if len(value) < 43 || seen[value] {
			t.Fatalf("missing or reused key %s", key)
		}
		seen[value] = true
	}
	if values["AUTH_INITIAL_REGISTRATION_ENABLED"] != "false" || values["CADDY_HTTP_BIND"] != "127.0.0.1:8080" || values["KORAIL_RESERVATION_ONCE_ENABLED"] != "false" {
		t.Fatal("unsafe defaults")
	}
	bootstrap, up := -1, -1
	for n, c := range runner.commands {
		args := strings.Join(c.Args, " ")
		if strings.Contains(args, "never-print") {
			t.Fatal("credential leaked to argv")
		}
		if strings.Contains(args, "bootstrap") && !strings.Contains(runner.inputs[n], "check_only") {
			bootstrap = n
		}
		if strings.HasSuffix(args, "up -d --wait --wait-timeout 180") {
			up = n
		}
	}
	if bootstrap < 0 || up <= bootstrap {
		t.Fatal("public services started before admin bootstrap")
	}
	journal, _ := os.ReadFile(statePath(root))
	if strings.Contains(string(journal), "never-print") {
		t.Fatal("credential persisted in state")
	}
	before := string(data)
	state, err = installer.Install(context.Background(), Options{Path: root})
	if err != nil {
		t.Fatal(err)
	}
	after, _ := os.ReadFile(filepath.Join(root, ".env"))
	if string(after) != before || source.calls != 1 {
		t.Fatal("repeat install regenerated release or secrets")
	}
}

func TestInstallRefusesExistingFilesAndDockerResources(t *testing.T) {
	t.Run("existing directory", func(t *testing.T) {
		path := t.TempDir()
		os.WriteFile(filepath.Join(path, "user-data"), []byte("keep"), 0600)
		runner := &fakeRunner{}
		i := testInstaller(runner, &fakeReleases{})
		if _, err := i.Install(context.Background(), Options{Path: path}); err == nil {
			t.Fatal("accepted existing installation")
		}
		if len(runner.commands) != 0 {
			t.Fatal("touched docker")
		}
		data, _ := os.ReadFile(filepath.Join(path, "user-data"))
		if string(data) != "keep" {
			t.Fatal("lost user data")
		}
	})
	t.Run("project conflict", func(t *testing.T) {
		runner := &fakeRunner{conflict: true}
		source := &fakeReleases{}
		i := testInstaller(runner, source)
		if _, err := i.Install(context.Background(), Options{Path: filepath.Join(t.TempDir(), "new")}); err == nil {
			t.Fatal("accepted existing docker resources")
		}
		if source.calls != 0 {
			t.Fatal("downloaded release despite conflict")
		}
	})
}

func TestInstallRejectsRemoteContextAndSignatureFailureWithoutMutations(t *testing.T) {
	for _, test := range []struct {
		name, endpoint string
		signature      bool
	}{{"remote", "tcp://remote:2376", false}, {"signature", "", true}} {
		t.Run(test.name, func(t *testing.T) {
			runner := &fakeRunner{endpoint: test.endpoint}
			source := &fakeReleases{fail: test.signature}
			i := testInstaller(runner, source)
			root := filepath.Join(t.TempDir(), "install")
			if _, err := i.Install(context.Background(), Options{Path: root}); err == nil {
				t.Fatal("expected failure")
			}
			for _, c := range runner.commands {
				args := strings.Join(c.Args, " ")
				if strings.Contains(args, " up ") || strings.Contains(args, " pull") || strings.Contains(args, " run ") {
					t.Fatalf("mutated Docker: %s", args)
				}
			}
			if _, err := os.Stat(filepath.Join(root, ".env")); !os.IsNotExist(err) {
				t.Fatal("secrets generated before trust checks")
			}
		})
	}
}

func TestBootstrapResumePreservesSecretsAndCommittedAdmin(t *testing.T) {
	root := filepath.Join(t.TempDir(), "install")
	runner := &fakeRunner{failure: "api python"}
	i := testInstaller(runner, &fakeReleases{})
	if _, err := i.Install(context.Background(), Options{Path: root}); err == nil {
		t.Fatal("expected probe failure")
	}
	before, _ := os.ReadFile(filepath.Join(root, ".env"))
	runner.failure = ""
	runner.configured = true
	state, err := i.Install(context.Background(), Options{Path: root, Credentials: func() (Credentials, error) {
		t.Fatal("asked for credentials after committed admin")
		return Credentials{}, nil
	}})
	if err != nil {
		t.Fatal(err)
	}
	if state.Stage != "complete" {
		t.Fatal(state.Stage)
	}
	after, _ := os.ReadFile(filepath.Join(root, ".env"))
	if string(before) != string(after) {
		t.Fatal("regenerated secrets")
	}
}

func TestMigrationUnknownAndUnhealthyCannotComplete(t *testing.T) {
	for _, test := range []struct {
		name, failure string
		unhealthy     bool
	}{{"migration", "-T migration", false}, {"health", "", true}} {
		t.Run(test.name, func(t *testing.T) {
			root := filepath.Join(t.TempDir(), "install")
			runner := &fakeRunner{failure: test.failure, unhealthy: test.unhealthy}
			i := testInstaller(runner, &fakeReleases{})
			_, err := i.Install(context.Background(), Options{Path: root, Credentials: func() (Credentials, error) { return Credentials{"admin", "a-valid-password"}, nil }})
			if err == nil {
				t.Fatal("failure reported success")
			}
			state, err := ReadState(root)
			if err != nil {
				t.Fatal(err)
			}
			if state.Stage == "complete" {
				t.Fatal("incorrect completion journal")
			}
			if test.failure != "" {
				runner.failure = ""
				count := len(runner.commands)
				if _, err = i.Install(context.Background(), Options{Path: root}); err == nil {
					t.Fatal("unknown migration auto retried")
				}
				for _, c := range runner.commands[count:] {
					if strings.Contains(strings.Join(c.Args, " "), "-T migration") {
						t.Fatal("migration repeated")
					}
				}
			}
		})
	}
}

func TestDockerEnvironmentIgnoresAmbientTargetsAndInterpolation(t *testing.T) {
	result := strings.Join(DockerEnvironment([]string{"PATH=/bin", "HOME=/home/user", "DOCKER_HOST=tcp://bad", "DOCKER_CONTEXT=evil", "COMPOSE_FILE=evil", "POSTGRES_PASSWORD=evil", "CADDY_HTTP_BIND=0.0.0.0:80", "DOCKER_CONFIG=evil"}), "\n")
	if strings.Contains(result, "evil") || strings.Contains(result, "tcp://bad") || strings.Contains(result, "0.0.0.0") {
		t.Fatal("ambient Docker configuration leaked")
	}
	if !strings.Contains(result, "PATH=/bin") {
		t.Fatal("missing executable path")
	}
}

func TestEnvParserAndSecretPreservation(t *testing.T) {
	for _, bad := range []string{"A=1\nA=2", "BAD KEY=value", "A='unclosed", "A=\"quoted\""} {
		if _, err := ParseEnv(bad); err == nil {
			t.Fatalf("accepted invalid dotenv %q", bad)
		}
	}
	data, err := InitialEnv("POSTGRES_PASSWORD=\n", map[string]string{"POSTGRES_PASSWORD": "keep-existing-value", "SECRET_ENCRYPTION_KEY": "keep-existing-key"}, "railwait-test", 8080, "")
	if err != nil {
		t.Fatal(err)
	}
	values, _ := ParseEnv(string(data))
	if values["POSTGRES_PASSWORD"] != "keep-existing-value" || values["SECRET_ENCRYPTION_KEY"] != "keep-existing-key" {
		t.Fatal("existing keys changed")
	}
}

func TestInitialEnvPreservesMonitoringDefaultsAndDisablesAutomaticReservations(t *testing.T) {
	data, err := InitialEnv("COMPOSE_PROFILES=experimental-rail\nEXPERIMENTAL_RAIL_ENABLED=true\nKORAIL_BROWSER_ADAPTER_ENABLED=true\nSRT_SEAT_STATUS_ENABLED=true\nKORAIL_RESERVATION_ONCE_ENABLED=true\n", nil, "railwait-test", 8080, "")
	if err != nil {
		t.Fatal(err)
	}
	values, err := ParseEnv(string(data))
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"EXPERIMENTAL_RAIL_ENABLED", "KORAIL_BROWSER_ADAPTER_ENABLED", "SRT_SEAT_STATUS_ENABLED"} {
		if values[key] != "true" {
			t.Fatalf("changed default %s", key)
		}
	}
	if values["COMPOSE_PROFILES"] != "experimental-rail" || values["KORAIL_RESERVATION_ONCE_ENABLED"] != "false" || values["SRT_RESERVATION_ONCE_ENABLED"] != "false" {
		t.Fatal("profile or reservation defaults changed")
	}
	images := activeImages(map[string]string{"api": "one", "adapter": "two", "backup": "three"}, map[string][]string{"adapter": {"experimental-rail"}, "backup": {"backup"}}, []string{"experimental-rail"})
	if len(images) != 2 || images["adapter"] != "two" || images["backup"] != "" {
		t.Fatal("wrong active service set")
	}
}

func TestResumeAfterBundleRenameAndInitialLockCreation(t *testing.T) {
	for _, stage := range []string{"lock_only", "before_rename", "after_rename"} {
		t.Run(stage, func(t *testing.T) {
			root := filepath.Join(t.TempDir(), "installation")
			runner := &fakeRunner{configured: true}
			i := testInstaller(runner, &fakeReleases{})
			if stage == "lock_only" {
				if err := os.MkdirAll(filepath.Join(root, ".railwait"), 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(filepath.Join(root, ".railwait", "install.lock"), nil, 0600); err != nil {
					t.Fatal(err)
				}
			} else {
				s, err := i.Install(context.Background(), Options{Path: root})
				if err != nil {
					t.Fatal(err)
				}
				s.Stage = "bundle_verified"
				if err = writeState(s); err != nil {
					t.Fatal(err)
				}
				if stage == "before_rename" {
					if err = os.Rename(filepath.Dir(s.ComposeFile), filepath.Join(root, "releases", ".download-"+s.ID)); err != nil {
						t.Fatal(err)
					}
				}
			}
			s, err := i.Install(context.Background(), Options{Path: root})
			if err != nil {
				t.Fatal(err)
			}
			if s.Stage != "complete" {
				t.Fatal(s.Stage)
			}
		})
	}
}

func TestReleaseDriftAndWrongRunningImageCannotComplete(t *testing.T) {
	t.Run("bundle drift", func(t *testing.T) {
		root := filepath.Join(t.TempDir(), "installation")
		runner := &fakeRunner{failure: "api python"}
		i := testInstaller(runner, &fakeReleases{})
		_, err := i.Install(context.Background(), Options{Path: root})
		if err == nil {
			t.Fatal("expected probe failure")
		}
		s, err := ReadState(root)
		if err != nil {
			t.Fatal(err)
		}
		if err = os.WriteFile(s.ComposeFile, []byte("tampered"), 0600); err != nil {
			t.Fatal(err)
		}
		runner.failure = ""
		before := len(runner.commands)
		if _, err = i.Install(context.Background(), Options{Path: root}); err == nil {
			t.Fatal("accepted release drift")
		}
		for _, c := range runner.commands[before:] {
			args := strings.Join(c.Args, " ")
			if strings.Contains(args, " run ") || strings.Contains(args, " up ") {
				t.Fatal("mutated after drift")
			}
		}
	})
	t.Run("wrong image", func(t *testing.T) {
		root := filepath.Join(t.TempDir(), "installation")
		runner := &fakeRunner{configured: true, wrongImage: true}
		i := testInstaller(runner, &fakeReleases{})
		if _, err := i.Install(context.Background(), Options{Path: root}); err == nil {
			t.Fatal("healthy wrong image completed installation")
		}
	})
}

func TestPreflightCapacityAndPortFailuresDoNotChangeDocker(t *testing.T) {
	for _, failure := range []string{"space", "port"} {
		t.Run(failure, func(t *testing.T) {
			runner := &fakeRunner{}
			i := testInstaller(runner, &fakeReleases{})
			if failure == "space" {
				i.CheckSpace = func(string) error { return errors.New("insufficient space") }
			} else {
				i.CheckPort = func(int) error { return errors.New("port occupied") }
			}
			if _, err := i.Install(context.Background(), Options{Path: filepath.Join(t.TempDir(), "installation")}); err == nil {
				t.Fatal("expected preflight failure")
			}
			for _, c := range runner.commands {
				args := strings.Join(c.Args, " ")
				if strings.Contains(args, " run ") || strings.Contains(args, " up ") || strings.Contains(args, " pull") {
					t.Fatal("mutated despite preflight failure")
				}
			}
		})
	}
}

func TestMissingDockerFileSharingStopsBeforeDatabaseOrBootstrap(t *testing.T) {
	runner := &fakeRunner{failure: "rail_waitlist.maintenance --status"}
	i := testInstaller(runner, &fakeReleases{})
	root := filepath.Join(t.TempDir(), "installation")
	_, err := i.Install(context.Background(), Options{Path: root})
	if err == nil || !strings.Contains(err.Error(), "파일 공유") {
		t.Fatalf("wrong mount failure: %v", err)
	}
	for _, c := range runner.commands {
		args := strings.Join(c.Args, " ")
		if strings.Contains(args, "up -d") || strings.Contains(args, "admin_auth.bootstrap") || strings.Contains(args, "-T migration") {
			t.Fatal("initialized database before mount validation")
		}
	}
	state, err := ReadState(root)
	if err != nil {
		t.Fatal(err)
	}
	if state.Stage != "configured" {
		t.Fatal(state.Stage)
	}
}
