package main

import (
	"bytes"
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/setup"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/terminal"
)

func scriptedLines(lines ...string) func(context.Context) (string, error) {
	return func(context.Context) (string, error) {
		if len(lines) == 0 {
			return "", errors.New("no more scripted input")
		}
		line := lines[0]
		lines = lines[1:]
		return line, nil
	}
}

func TestInstallFolderMovesIntoWritableVMShareOnlyWhenNeeded(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Lima와 Colima 공유 폴더는 Unix 경로입니다")
	}
	platformDefault := "/Users/example/Library/Application Support/Railwait"
	services := "/Users/example/Library/Application Support/Server/services"
	for _, tc := range []struct {
		name     string
		vm       setup.DockerVM
		isVM     bool
		want     string
		moved    bool
		unshared bool
	}{
		{name: "docker desktop keeps default", want: platformDefault},
		{name: "writable home keeps default", isVM: true, vm: setup.DockerVM{Folders: []setup.SharedFolder{{Path: "/Users/example", Writable: true}}}, want: platformDefault},
		{name: "read-only home moves to writable share", isVM: true, vm: setup.DockerVM{Folders: []setup.SharedFolder{{Path: "/Users/example", Writable: false}, {Path: "/tmp/lima", Writable: true}, {Path: services, Writable: true}}}, want: filepath.Join(services, "railwait"), moved: true},
		{name: "only temporary share stays and warns", isVM: true, vm: setup.DockerVM{Folders: []setup.SharedFolder{{Path: "/Users/example", Writable: false}, {Path: "/tmp/lima", Writable: true}}}, want: platformDefault, unshared: true},
		{name: "similar prefix is not a parent", isVM: true, vm: setup.DockerVM{Folders: []setup.SharedFolder{{Path: "/Users/example/Library/Application Sup", Writable: true}}}, want: "/Users/example/Library/Application Sup/railwait", moved: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := chooseInstallFolder(platformDefault, tc.vm, tc.isVM)
			if got.Path != tc.want || got.Moved != tc.moved || got.Unshared != tc.unshared {
				t.Fatalf("got %+v", got)
			}
		})
	}
}

func TestCustomFolderPromptAcceptsDefaultAndRejectsUnsharedFolders(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Lima와 Colima 공유 폴더는 Unix 경로입니다")
	}
	var output bytes.Buffer
	ui := terminal.New(&output, terminal.Plain(80))
	defer ui.Pause()
	shared := t.TempDir()
	choice := installFolderChoice{Path: filepath.Join(shared, "railwait"), IsVM: true, VM: setup.DockerVM{Kind: "lima", Name: "default", Folders: []setup.SharedFolder{{Path: filepath.ToSlash(shared), Writable: true}}}}
	got, err := folderInputWith(context.Background(), ui, choice, scriptedLines(""))
	if err != nil || got != choice.Path {
		t.Fatalf("default folder not accepted: %q %v", got, err)
	}
	outside := filepath.Join(t.TempDir(), "elsewhere")
	got, err = folderInputWith(context.Background(), ui, choice, scriptedLines(outside, filepath.Join(shared, "custom")))
	if err != nil || got != filepath.Join(shared, "custom") {
		t.Fatalf("unshared folder accepted or shared folder rejected: %q %v", got, err)
	}
	if !strings.Contains(output.String(), "공유한 폴더") {
		t.Fatal("rejection reason was not shown")
	}
	choice.Unshared = true
	if got, err = folderInputWith(context.Background(), ui, choice, scriptedLines(outside)); err != nil || got != outside {
		t.Fatal("a VM without writable shares must not trap the prompt")
	}
}

func TestPortPromptUsesSuggestionAndRejectsBusyOrInvalidPorts(t *testing.T) {
	var output bytes.Buffer
	ui := terminal.New(&output, terminal.Plain(80))
	defer ui.Pause()
	busy := func(port int) error {
		if port == 9000 {
			return errors.New("in use")
		}
		return nil
	}
	if got, err := portInputWith(context.Background(), ui, 8081, busy, scriptedLines("")); err != nil || got != 8081 {
		t.Fatalf("suggested port not accepted: %d %v", got, err)
	}
	if got, err := portInputWith(context.Background(), ui, 8081, busy, scriptedLines("abc", "70000", "9000", "9001")); err != nil || got != 9001 {
		t.Fatalf("invalid or busy port accepted: %d %v", got, err)
	}
	if !strings.Contains(output.String(), "9000 포트는 이미 사용 중") {
		t.Fatal("busy port reason was not shown")
	}
}

func TestLocalPortSelectionKeepsExplicitPortAndSkipsBusyDefaults(t *testing.T) {
	busy := map[int]bool{8080: true, 8081: true}
	available := func(port int) error {
		if busy[port] {
			return errors.New("in use")
		}
		return nil
	}
	if port, automatic, err := selectLocalPort(9090, available); err != nil || port != 9090 || automatic {
		t.Fatal("explicit port changed")
	}
	if port, automatic, err := selectLocalPort(0, available); err != nil || port != 8082 || !automatic {
		t.Fatalf("busy 8080 not skipped: %d %t %v", port, automatic, err)
	}
	if _, _, err := selectLocalPort(0, func(int) error { return errors.New("in use") }); err == nil || !strings.Contains(err.Error(), "--port") {
		t.Fatal("exhausted range must ask for --port")
	}
}

func TestWizardRemembersAccessOnlyAfterInstallerOwnsFolder(t *testing.T) {
	root := t.TempDir()
	if err := rememberAccess(root, "tailscale"); err != nil {
		t.Fatal(err)
	}
	if entries, _ := os.ReadDir(root); len(entries) != 0 {
		t.Fatal("record written into a folder the installer does not own yet")
	}
	if err := os.MkdirAll(filepath.Join(root, ".railwait"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, ".railwait", "install.json"), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	if recordedAccess(root) != "" {
		t.Fatal("missing record must not choose an access mode")
	}
	for _, mode := range []string{"tailscale", "local"} {
		if err := rememberAccess(root, mode); err != nil {
			t.Fatal(err)
		}
		if got := recordedAccess(root); got != mode {
			t.Fatalf("recorded %q, want %q", got, mode)
		}
	}
	if err := os.WriteFile(filepath.Join(root, ".railwait", wizardRecordName), []byte(`{"schema":1,"access":"internet"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if recordedAccess(root) != "" {
		t.Fatal("unknown access mode accepted")
	}
	temps, _ := filepath.Glob(filepath.Join(root, ".railwait", ".wizard.json-*"))
	if len(temps) != 0 {
		t.Fatal("temporary record left behind")
	}
}

func TestExplicitPathIsRecordedSoResumeNeverMovesTheFolder(t *testing.T) {
	_, pathSet, err := parseWizardCommand(nil)
	if err != nil || pathSet {
		t.Fatal("default path must stay replaceable")
	}
	folder := filepath.Join(t.TempDir(), "railwait")
	o, pathSet, err := parseWizardCommand([]string{"--path", folder})
	if err != nil || !pathSet || o.Path != folder {
		t.Fatal("explicit path lost")
	}
	resumed, resumedPathSet, err := parseWizardCommand(wizardResumeArgs(o)[1:])
	if err != nil || !resumedPathSet || resumed.Path != folder {
		t.Fatal("resume command must pin the chosen folder")
	}
}
