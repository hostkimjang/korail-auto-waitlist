package main

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/terminal"
)

func TestWizardResumePreservesEveryInstallationOption(t *testing.T) {
	o := wizardOptions{
		Path: "/data/railwait", Package: "/downloads/local package", TrustKey: "/downloads/key.pub",
		Access: "tailscale", DockerContext: "lima-homeserver", Input: "/private/admin.json",
		Port: 18080, HTTPSPort: 8443, Plain: true, Yes: true, Open: true,
	}
	got, err := parseWizardOptions(wizardResumeArgs(o)[1:])
	if err != nil {
		t.Fatal(err)
	}
	// filepath.Abs follows the native OS; the command still preserves all values.
	for _, path := range []*string{&o.Path, &o.Package, &o.TrustKey, &o.Input} {
		*path, err = filepath.Abs(*path)
		if err != nil {
			t.Fatal(err)
		}
	}
	if !reflect.DeepEqual(got, o) {
		t.Fatalf("resume changed options: got %+v want %+v", got, o)
	}
}

func TestWizardNormalizesResumeFilePathsWithoutReadingSecrets(t *testing.T) {
	args := []string{"--path", "relative-install", "--package", "missing-package", "--trust-key", "missing-key.pub", "--input", "missing-admin.json"}
	o, err := parseWizardOptions(args)
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{o.Path, o.Package, o.TrustKey, o.Input} {
		if !filepath.IsAbs(path) {
			t.Fatal("relative path would break after reboot", path)
		}
	}
	for _, flag := range []string{"--package", "--trust-key", "--input"} {
		if !strings.Contains(strings.Join(wizardResumeArgs(o), " "), flag) {
			t.Fatal("lost file option", flag)
		}
	}
	o, err = parseWizardOptions([]string{"--input", "-"})
	if err != nil || o.Input != "-" {
		t.Fatal("stdin marker changed into a filename", o, err)
	}
}

func TestWizardPOSIXCommandPreservesLiteralShellCharacters(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX shell execution is checked on Unix")
	}
	values := []string{"/Users/O'Brien/a b", "$(printf INJECTED)", "`printf INJECTED`", "$HOME", "semi; printf INJECTED", `quote"and\slash`, "", "한글 경로"}
	args := append([]string{"%s\\000"}, values...)
	command, err := wizardShellCommand("darwin", "printf", args...)
	if err != nil {
		t.Fatal(err)
	}
	output, err := exec.Command("/bin/sh", "-c", command).Output()
	if err != nil {
		t.Fatal(err)
	}
	want := strings.Join(values, "\x00") + "\x00"
	if string(output) != want {
		t.Fatalf("shell interpreted literal arguments: got %q want %q", output, want)
	}
}

func TestWizardPowerShellCommandUsesInvocationAndLiteralQuotes(t *testing.T) {
	executable := `C:\Program Files\O'Brien\$railwait.exe`
	path := "C:\\Users\\O'Brien\\$(echo injected); & `literal`"
	command, err := wizardShellCommand("windows", executable, "install", "--path", path)
	if err != nil {
		t.Fatal(err)
	}
	want := "& 'C:\\Program Files\\O''Brien\\$railwait.exe' 'install' '--path' 'C:\\Users\\O''Brien\\$(echo injected); & `literal`'"
	if command != want {
		t.Fatalf("PowerShell command is not literal/copyable: got %q want %q", command, want)
	}
}

func TestWizardCommandRefusesTerminalControlCharacters(t *testing.T) {
	for _, goos := range []string{"darwin", "windows"} {
		for _, path := range []string{"line\nbreak", "carriage\rreturn", "escape\x1b[31m", "null\x00byte", "direction\u202econtrol", "invalid\xffbyte"} {
			if _, err := wizardShellCommand(goos, "railwait", "--path", path); err == nil {
				t.Fatal("terminal sanitizer would silently change the command", goos, path)
			}
		}
	}
}

func TestWizardRecoveryOmitsStatusBeforeAppStateAndExplainsStdin(t *testing.T) {
	dir := t.TempDir()
	var output bytes.Buffer
	ui := terminal.New(&output, terminal.Plain(80))
	showWizardResumeCommands(ui, wizardOptions{Path: dir, Input: "-", Access: "tailscale"})
	if strings.Contains(output.String(), "'status'") {
		t.Fatal("new WSL install has no app status yet")
	}
	if !strings.Contains(output.String(), "'--input' '-'") || !strings.Contains(output.String(), "표준 입력으로 다시") {
		t.Fatal("stdin replay instruction missing", output.String())
	}
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	expected, err := wizardShellCommand(runtime.GOOS, executable, wizardResumeArgs(wizardOptions{Path: dir, Input: "-", Access: "tailscale"})...)
	if err != nil || !strings.Contains(output.String(), expected) {
		t.Fatal("actual executable path missing", err)
	}
	if err := os.Mkdir(filepath.Join(dir, ".railwait"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, ".railwait", "install.json"), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	output.Reset()
	showWizardResumeCommands(ui, wizardOptions{Path: dir})
	if !strings.Contains(output.String(), "'status' '--path'") {
		t.Fatal("existing app status command missing")
	}
	ui.Close()
}
