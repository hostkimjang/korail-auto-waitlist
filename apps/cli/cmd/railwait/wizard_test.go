package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/install"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/setup"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/terminal"
)

func TestTailscaleAuthenticationPreservesExistingProfileAndSettings(t *testing.T) {
	command := tailscaleAuthenticationCommand(context.Background(), "/known/tailscale")
	if !reflect.DeepEqual(command.Args, []string{"/known/tailscale", "up"}) {
		t.Fatal("authentication must not switch profiles, reset settings or force reauthentication")
	}
}

func TestProtectedInstallInputAcceptsManualKeysAndRejectsUnsafeOverrides(t *testing.T) {
	data, err := install.InitialEnv("", nil, "railwait-key-test", 8080, "")
	if err != nil {
		t.Fatal(err)
	}
	values, err := install.ParseEnv(string(data))
	if err != nil {
		t.Fatal(err)
	}
	valid := installInput{Secrets: map[string]string{"AUTH_SESSION_SECRET": strings.Repeat("k", 48)}, Push: install.PushOptions{PublicKey: values["WEBPUSH_VAPID_PUBLIC_KEY"], PrivateKey: values["WEBPUSH_VAPID_PRIVATE_KEY"], Subject: "https://example.com"}}
	file := filepath.Join(t.TempDir(), "input.json")
	encoded, _ := json.Marshal(valid)
	if err := os.WriteFile(file, encoded, 0600); err != nil {
		t.Fatal(err)
	}
	parsed, err := readInstallInput(file)
	if err != nil || !reflect.DeepEqual(parsed.Secrets, valid.Secrets) || parsed.Push != valid.Push {
		t.Fatal("valid private input rejected")
	}
	for _, body := range []string{`{"app_secrets":{"AUTH_SESSION_SECRET":"secret-canary"}}`, `{"app_secrets":{"DOCKER_HOST":"secret-canary"}}`, `{"webpush":{"private_key":"secret-canary"}}`} {
		os.WriteFile(file, []byte(body), 0600)
		_, err = readInstallInput(file)
		if err == nil || strings.Contains(err.Error(), "secret-canary") {
			t.Fatal("invalid key accepted or disclosed")
		}
	}
}

func TestPrerequisiteInstallRechecksReadinessAndStopsForReboot(t *testing.T) {
	for _, tc := range []struct {
		name      string
		result    setup.InstallResult
		err       error
		wantError bool
	}{
		{"installed-needs-consent", setup.InstallResult{Installed: true, NeedsUserAction: true}, nil, false},
		{"reboot", setup.InstallResult{Installed: true, RebootRequired: true}, nil, true},
		{"not-installed", setup.InstallResult{}, nil, true},
		{"cancelled", setup.InstallResult{}, context.Canceled, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var output bytes.Buffer
			ui := terminal.New(&output, terminal.Plain(80))
			err := installPrerequisite(context.Background(), ui, "docker", func(_ context.Context, tool string, progress func(string)) (setup.InstallResult, error) {
				if tool != "docker" {
					t.Fatal("wrong dependency")
				}
				progress("공식 서명 확인")
				return tc.result, tc.err
			})
			ui.Pause()
			if (err != nil) != tc.wantError {
				t.Fatalf("unexpected error %v", err)
			}
			if tc.err != nil && !errors.Is(err, tc.err) {
				t.Fatal("lost cancellation")
			}
			if strings.Contains(output.String(), "Docker 준비됨") {
				t.Fatal("installer result claimed runtime readiness")
			}
		})
	}
}

func TestMissingDockerInstallsAndStartsWithoutAnotherCLIConsentPrompt(t *testing.T) {
	var output bytes.Buffer
	ui := terminal.New(&output, terminal.Plain(80))
	defer ui.Pause()
	calls := 0
	interact := prerequisitePromptWith(context.Background(), ui, func(_ context.Context, tool string, _ func(string)) (setup.InstallResult, error) {
		calls++
		return setup.InstallResult{Installed: true, NeedsUserAction: true}, nil
	})
	action, err := interact(setup.Need{Kind: "docker_missing", CanInstall: true})
	if err != nil || action != setup.Retry || calls != 1 {
		t.Fatalf("missing Docker: %v %v", action, err)
	}
	action, err = interact(setup.Need{Kind: "docker_stopped", CanStart: true})
	if err != nil || action != setup.Start || calls != 1 {
		t.Fatalf("start installed Docker: %v %v", action, err)
	}
	if strings.Contains(output.String(), "선택 [") {
		t.Fatal("asked for redundant CLI approval")
	}
}

func TestWizardRejectsIncompleteOrContradictoryPlanBeforeActions(t *testing.T) {
	for _, args := range [][]string{{"--package", "package"}, {"--trust-key", "key"}, {"--access", "internet"}, {"--https-port", "8443"}, {"--access", "local", "--https-port", "8443"}, {"--port", "-1"}, {"--https-port", "65536"}, {"extra"}} {
		if _, err := parseWizardOptions(args); err == nil {
			t.Fatalf("accepted invalid options: %v", args)
		}
	}
	o, err := parseWizardOptions([]string{"--access", "tailscale", "--https-port", "8443", "--plain", "--yes"})
	if err != nil || o.Access != "tailscale" || o.HTTPSPort != 8443 || !o.Plain || !o.Yes {
		t.Fatal("valid explicit plan rejected")
	}
}

func TestBrokenExistingRecordIsNeverTreatedAsNew(t *testing.T) {
	root := t.TempDir()
	if s, err := existingInstallation(root); s != nil || err != nil {
		t.Fatal("new path rejected")
	}
	os.Mkdir(filepath.Join(root, ".railwait"), 0700)
	os.WriteFile(filepath.Join(root, ".railwait", "install.json"), []byte(`{"broken":true}`), 0600)
	if _, err := existingInstallation(root); err == nil {
		t.Fatal("corrupt state would ask for new credentials")
	}
}

func TestWizardRemovesOnlyItsCompletedOrFailedPrivateCandidate(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, ".railwait", "plans")
	os.MkdirAll(dir, 0700)
	id := strings.Repeat("a", 64)
	other := filepath.Join(dir, strings.Repeat("b", 64)+".candidate.env")
	file := filepath.Join(dir, id+".candidate.env")
	os.WriteFile(file, []byte("private-fixture"), 0600)
	os.WriteFile(other, []byte("other-plan"), 0600)
	if err := removeWizardCandidate(root, id); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(file); !os.IsNotExist(err) {
		t.Fatal("owned candidate remains")
	}
	if _, err := os.Stat(other); err != nil {
		t.Fatal("another plan was removed")
	}
	if err := removeWizardCandidate(root, "../escape"); err == nil {
		t.Fatal("unsafe plan ID accepted")
	}
}

func TestAdministratorValidationMatchesAPIAndNeverEchoesPassword(t *testing.T) {
	for _, c := range [][2]string{{"a", "secret-canary"}, {"bad name", "secret-canary"}, {"admin", "short"}, {"admin", strings.Repeat("x", 129)}} {
		if err := validateAdministrator(c[0], c[1]); err == nil {
			t.Fatal("invalid credentials accepted")
		} else if strings.Contains(err.Error(), c[1]) {
			t.Fatal("password leaked")
		}
	}
	if err := validateAdministrator("user.name-1", "비밀번호공백 포함  열두자이상"); err != nil {
		t.Fatal(err)
	}
}

func TestWebVerificationRequiresRealConfiguredAPIAndDoesNotFollowRedirects(t *testing.T) {
	for _, test := range []struct {
		name, body string
		code       int
		want       bool
	}{{"ready", `{"configured":true}`, 200, true}, {"spa-fallback", "<html>SPA</html>", 200, false}, {"unconfigured", `{"configured":false}`, 200, false}, {"missing-field", `{}`, 200, false}, {"redirect", `{}`, 302, false}} {
		t.Run(test.name, func(t *testing.T) {
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/" {
					w.Write([]byte("<html>Railwait</html>"))
					return
				}
				if r.URL.Path != "/api/v1/auth/status" {
					t.Errorf("wrong verification path %s", r.URL.Path)
				}
				if test.code == 302 {
					w.Header().Set("Location", "https://example.invalid/")
				}
				w.WriteHeader(test.code)
				w.Write([]byte(test.body))
			}))
			defer s.Close()
			if err := checkWeb(context.Background(), s.URL); (err == nil) != test.want {
				t.Fatalf("unexpected verification result: %v", err)
			}
		})
	}
}
