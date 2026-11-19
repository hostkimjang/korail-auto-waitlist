package main

import (
	"bytes"
	"context"
	"errors"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

type recordingReveal struct {
	commands []revealCommand
	err      error
}

func (r *recordingReveal) Run(_ context.Context, command revealCommand) error {
	r.commands = append(r.commands, command)
	return r.err
}

func environment(values map[string]string) func(string) string {
	return func(key string) string { return values[key] }
}

func TestOpenPrintsTheSettingsFileAndRevealsIt(t *testing.T) {
	root, _ := installFixture(t)
	env := filepath.Join(root, ".env")
	for goos, want := range map[string]revealCommand{
		"darwin":  {"open", []string{"-R", env}},
		"windows": {"explorer.exe", []string{"/select," + env}},
		"linux":   {"xdg-open", []string{root}},
	} {
		t.Run(goos, func(t *testing.T) {
			runner := &recordingReveal{}
			var out, log bytes.Buffer
			o := opener{runner: runner, goos: goos, getenv: environment(map[string]string{"DISPLAY": ":0"}), out: &out, log: &log}
			if err := o.run(context.Background(), root); err != nil {
				t.Fatal(err)
			}
			if out.String() != env+"\n" {
				t.Fatalf("stdout: %q", out.String())
			}
			if len(runner.commands) != 1 || !reflect.DeepEqual(runner.commands[0], want) {
				t.Fatalf("commands: %+v", runner.commands)
			}
			if !strings.Contains(log.String(), "railwait restart") {
				t.Fatalf("log: %s", log.String())
			}
		})
	}
}

func TestOpenShowsACommandWhenItCannotReveal(t *testing.T) {
	root, _ := installFixture(t)
	env := filepath.Join(root, ".env")
	cases := []struct {
		name, goos, manual string
		env                map[string]string
		err                error
		runs               int
	}{
		{"linux without display", "linux", "'xdg-open' '" + root + "'", map[string]string{}, nil, 0},
		{"ssh session", "darwin", "'open' '-R' '" + env + "'", map[string]string{"SSH_CONNECTION": "10.0.0.1 22 10.0.0.2 22"}, nil, 0},
		{"file manager failed", "darwin", "'open' '-R' '" + env + "'", map[string]string{}, errors.New("no finder"), 1},
		{"explorer failed", "windows", "& 'explorer.exe' '" + root + "'", map[string]string{}, errors.New("no explorer"), 1},
	}
	for _, tt := range cases {
		t.Run(tt.name, func(t *testing.T) {
			runner := &recordingReveal{err: tt.err}
			var out, log bytes.Buffer
			o := opener{runner: runner, goos: tt.goos, getenv: environment(tt.env), out: &out, log: &log}
			if err := o.run(context.Background(), root); err != nil {
				t.Fatal(err)
			}
			if out.String() != env+"\n" || len(runner.commands) != tt.runs || !strings.Contains(log.String(), tt.manual) {
				t.Fatalf("stdout=%q runs=%d log:\n%s", out.String(), len(runner.commands), log.String())
			}
		})
	}
}

func TestOpenNeedsAnInstallation(t *testing.T) {
	runner := &recordingReveal{}
	var out, log bytes.Buffer
	o := opener{runner: runner, goos: "linux", getenv: environment(map[string]string{"DISPLAY": ":0"}), out: &out, log: &log}
	if err := o.run(context.Background(), t.TempDir()); err == nil || len(runner.commands) != 0 || out.Len() != 0 {
		t.Fatalf("err=%v", err)
	}
}

func TestExplorerSelectsTheFileWithItsOwnQuoting(t *testing.T) {
	command := revealFor("windows", `C:\Users\a b\Railwait`, `C:\Users\a b\Railwait\.env`)
	if got := windowsRevealCommandLine(command); got != `explorer.exe /select,"C:\Users\a b\Railwait\.env"` {
		t.Fatal(got)
	}
	if windowsRevealCommandLine(revealFor("linux", "/srv/railwait", "/srv/railwait/.env")) != "" {
		t.Fatal("only explorer needs a raw command line")
	}
}
