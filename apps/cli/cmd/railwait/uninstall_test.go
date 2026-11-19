package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/install"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

// uninstallEvents records service and Docker commands in the order they ran.
type uninstallEvents struct{ lines []string }

func (e *uninstallEvents) index(part string) int {
	for n, line := range e.lines {
		if strings.Contains(line, part) {
			return n
		}
	}
	return -1
}

// uninstallServices stands in for launchctl, systemctl and schtasks for the host OS plan.
type uninstallServices struct {
	plan       platform.ServicePlan
	registered bool
	events     *uninstallEvents
}

func (r *uninstallServices) Run(_ context.Context, command platform.ServiceCommand) error {
	r.events.lines = append(r.events.lines, "service: "+command.Program+" "+strings.Join(command.Args, " "))
	if reflect.DeepEqual(command, r.plan.Status) {
		if r.registered {
			return nil
		}
		return errors.New("not registered")
	}
	if reflect.DeepEqual(command, r.plan.Unregister[len(r.plan.Unregister)-1]) {
		r.registered = false
	}
	return nil
}

// uninstallDocker answers the read-only queries uninstall makes and records the rest.
type uninstallDocker struct {
	project string
	events  *uninstallEvents
}

func (d *uninstallDocker) Run(_ context.Context, c install.Command) ([]byte, error) {
	args := strings.Join(c.Args, " ")
	d.events.lines = append(d.events.lines, "docker: "+args)
	switch {
	case strings.Contains(args, "volume ls"):
		return []byte(d.project + "_postgres_data\n" + d.project + "_backups\n"), nil
	case strings.Contains(args, "config --images"):
		return []byte("ghcr.io/example/api@sha256:" + strings.Repeat("a", 64) + "\nghcr.io/example/backup@sha256:" + strings.Repeat("c", 64) + "\n"), nil
	}
	return nil, nil
}

func (e *uninstallEvents) destructive() []string {
	found := []string{}
	for _, line := range e.lines {
		if strings.Contains(line, " down ") || strings.Contains(line, " rm -rf ") || strings.Contains(line, "bootout") || strings.Contains(line, "disable --now") || strings.Contains(line, "/Delete") || strings.Contains(line, "/End") {
			found = append(found, line)
		}
	}
	return found
}

type uninstallTest struct {
	root     string
	state    install.State
	plan     platform.ServicePlan
	services *uninstallServices
	events   *uninstallEvents
	out      *bytes.Buffer
	u        uninstaller
}

func newUninstallTest(t *testing.T, input string, interactive bool) *uninstallTest {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	root, state := installFixture(t)
	plan, err := platform.CurrentServicePlan(managerExecutablePath(root), root)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Dir(plan.Definition), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(plan.Definition, plan.Content, 0o600); err != nil {
		t.Fatal(err)
	}
	record, _ := json.Marshal(map[string]any{"url": "https://my-device.example.ts.net:8443", "local_port": 8080, "https_port": 8443, "verified_at": "2026-11-19T00:00:00Z"})
	if err := os.WriteFile(filepath.Join(root, ".railwait", "access.json"), record, 0o600); err != nil {
		t.Fatal(err)
	}
	events := &uninstallEvents{}
	services := &uninstallServices{plan: plan, registered: true, events: events}
	out := &bytes.Buffer{}
	u := uninstaller{
		installer: install.Installer{Runner: &uninstallDocker{project: state.Project, events: events}}, services: services,
		confirm: testConfirmation(input, interactive, out), out: out, goos: runtime.GOOS, home: home,
		executable: "/opt/railwait/bin/railwait", stopWait: time.Second,
	}
	return &uninstallTest{root: root, state: state, plan: plan, services: services, events: events, out: out, u: u}
}

func TestUninstallKeepsDataByDefault(t *testing.T) {
	test := newUninstallTest(t, "railwait-install\n", true)
	if err := test.u.run(context.Background(), uninstallOptions{Path: test.root}); err != nil {
		t.Fatalf("%v\n%s", err, test.out)
	}
	if test.services.registered {
		t.Fatal("manager service still registered")
	}
	if _, err := os.Lstat(test.plan.Definition); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("manager service definition kept")
	}
	down := test.events.index(" down ")
	if down < 0 || !strings.Contains(test.events.lines[down], "--project-name "+test.state.Project) || !strings.Contains(test.events.lines[down], "down --remove-orphans --timeout 120") || strings.Contains(test.events.lines[down], "--volumes") {
		t.Fatalf("compose down: %v", test.events.lines)
	}
	if unregister := test.events.index(commandLine(test.plan.Unregister[0])); unregister < 0 || unregister > down {
		t.Fatal("containers removed before the manager service was unregistered")
	}
	if test.events.index("rm -rf") >= 0 {
		t.Fatal("default uninstall removed files through a container")
	}
	for _, path := range []string{test.root, filepath.Join(test.root, ".env"), filepath.Join(test.root, ".railwait", "install.json")} {
		if _, err := os.Lstat(path); err != nil {
			t.Fatalf("%s removed: %v", path, err)
		}
	}
	output := test.out.String()
	// The CLI removal hint is written for the shell of the OS running the test.
	removeCLI := "rm -- '/opt/railwait/bin/railwait'"
	if runtime.GOOS == "windows" {
		removeCLI = "Remove-Item -LiteralPath '/opt/railwait/bin/railwait'"
	}
	for _, part := range []string{"지울 항목", "남길 항목", test.state.Project + "_postgres_data", "--delete-data", "backup.env", "image", "rm", "ghcr.io/example/backup@sha256:", "serve", "--https=8443", "off", "127.0.0.1:8080", removeCLI} {
		if !strings.Contains(output, part) {
			t.Fatalf("output lacks %q:\n%s", part, output)
		}
	}
}

func commandLine(command platform.ServiceCommand) string {
	return command.Program + " " + strings.Join(command.Args, " ")
}

func TestUninstallDeleteDataRemovesVolumesAndTheFolder(t *testing.T) {
	test := newUninstallTest(t, "", false)
	if err := test.u.run(context.Background(), uninstallOptions{Path: test.root, DeleteData: true, Yes: true}); err != nil {
		t.Fatalf("%v\n%s", err, test.out)
	}
	down := test.events.index(" down ")
	if down < 0 || !strings.Contains(test.events.lines[down], "down --remove-orphans --timeout 120 --volumes") {
		t.Fatalf("compose down: %v", test.events.lines)
	}
	logs := test.events.index("rm -rf")
	if logs < down || !strings.Contains(test.events.lines[logs], "-v "+filepath.Join(test.root, "releases")+":/releases") || !strings.Contains(test.events.lines[logs], test.state.ExpectedImages["log-init"]) || !strings.Contains(test.events.lines[logs], "--network none") {
		t.Fatalf("container log cleanup: %v", test.events.lines)
	}
	if _, err := os.Lstat(test.root); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("installation folder kept")
	}
	if output := test.out.String(); !strings.Contains(output, "image") || strings.Contains(output, "--delete-data'") {
		t.Fatalf("output:\n%s", output)
	}
}

func TestUninstallConfirmation(t *testing.T) {
	cases := []struct {
		name, input   string
		interactive   bool
		options       uninstallOptions
		want          string
		folderRemains bool
	}{
		{"non-interactive needs --yes", "", false, uninstallOptions{}, "--yes", true},
		{"non-interactive delete needs --yes", "railwait-install\nDELETE\n", false, uninstallOptions{DeleteData: true}, "--yes", true},
		{"wrong folder name", "Railwait\n", true, uninstallOptions{}, "취소", true},
		{"delete not confirmed", "railwait-install\ndelete\n", true, uninstallOptions{DeleteData: true}, "취소", true},
		{"delete confirmed", "railwait-install\nDELETE\n", true, uninstallOptions{DeleteData: true}, "", false},
	}
	for _, tt := range cases {
		t.Run(tt.name, func(t *testing.T) {
			test := newUninstallTest(t, tt.input, tt.interactive)
			tt.options.Path = test.root
			err := test.u.run(context.Background(), tt.options)
			if tt.want == "" && err != nil || tt.want != "" && (err == nil || !strings.Contains(err.Error(), tt.want)) {
				t.Fatalf("err=%v", err)
			}
			if _, statErr := os.Lstat(test.root); (statErr == nil) != tt.folderRemains {
				t.Fatalf("folder remains=%v", statErr == nil)
			}
			if tt.want != "" {
				if destructive := test.events.destructive(); len(destructive) != 0 || !test.services.registered {
					t.Fatalf("refused uninstall changed something: %v", destructive)
				}
				if !strings.Contains(test.out.String(), "지울 항목") {
					t.Fatal("plan not shown before asking")
				}
			}
		})
	}
}

func TestUninstallRefusesWhileAnotherOperationHoldsTheLock(t *testing.T) {
	test := newUninstallTest(t, "", false)
	lock, err := platform.AcquireLock(filepath.Join(test.root, ".railwait", "install.lock"))
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	err = test.u.run(context.Background(), uninstallOptions{Path: test.root, DeleteData: true, Yes: true})
	if !errors.Is(err, errOperationRunning) || len(test.events.lines) != 0 {
		t.Fatalf("err=%v commands=%v", err, test.events.lines)
	}
	if _, err := os.Lstat(test.root); err != nil {
		t.Fatal("folder removed while another operation ran")
	}
}

func TestDeleteDataRefusesFoldersOtherThanTheInstallationRoot(t *testing.T) {
	root, state := installFixture(t)
	home := t.TempDir()
	if err := deletableInstallation(root, home, runtime.GOOS, ""); err != nil {
		t.Fatalf("installation root refused: %v", err)
	}
	refused := map[string]func() (string, string){
		"home folder":         func() (string, string) { return root, root },
		"parent of home":      func() (string, string) { return root, filepath.Join(root, "users", "me") },
		"filesystem root":     func() (string, string) { return string(filepath.Separator), home },
		"unknown home":        func() (string, string) { return root, "" },
		"no install record":   func() (string, string) { return t.TempDir(), home },
		"relative path":       func() (string, string) { return "railwait-install", home },
		"other record's path": func() (string, string) { other, _ := installFixture(t); return filepath.Dir(other), home },
	}
	for name, paths := range refused {
		path, homeDir := paths()
		if err := deletableInstallation(path, homeDir, runtime.GOOS, ""); err == nil {
			t.Fatalf("%s accepted", name)
		}
	}
	moved := state
	moved.Path = filepath.Join(filepath.Dir(root), "elsewhere")
	data, _ := json.Marshal(moved)
	if err := os.WriteFile(filepath.Join(root, ".railwait", "install.json"), data, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := deletableInstallation(root, home, runtime.GOOS, ""); err == nil {
		t.Fatal("folder whose record names another path accepted")
	}
	if runtime.GOOS != "windows" {
		link := filepath.Join(t.TempDir(), "link")
		if err := os.Symlink(root, link); err != nil {
			t.Fatal(err)
		}
		if err := deletableInstallation(link, home, runtime.GOOS, ""); err == nil {
			t.Fatal("symbolic link accepted")
		}
	}
	if !containsPath(filepath.Join(string(filepath.Separator), "a"), filepath.Join(string(filepath.Separator), "a", "b"), "linux") || containsPath(filepath.Join(string(filepath.Separator), "a"), filepath.Join(string(filepath.Separator), "ab"), "linux") {
		t.Fatal("containsPath")
	}
}
