package manager

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

func TestInspectServiceDefinitionTellsAnotherShellsPathFromAnEdit(t *testing.T) {
	for _, tt := range []struct{ os, home, user, exe, root string }{
		{"darwin", "/Users/a", "501", "/Users/a/inst/.railwait/bin/railwait", "/Users/a/inst"},
		{"linux", "/home/a", "1000", "/home/a/inst/.railwait/bin/railwait", "/home/a/inst"},
	} {
		t.Run(tt.os, func(t *testing.T) {
			rendered, err := platform.RenderService(tt.os, tt.home, tt.user, tt.exe, tt.root)
			if err != nil {
				t.Fatal(err)
			}
			plan := rendered
			plan.Definition = filepath.Join(t.TempDir(), "definition")
			plan.PathFree = rendered.Content
			plan.Content = platform.WithServicePath(tt.os, rendered.Content, "/codex/tmp/bin:/usr/bin")
			plan.Legacy = [][]byte{[]byte("an earlier rendering the OS rejected")}
			if state, err := InspectServiceDefinition(plan); err != nil || state != DefinitionMissing {
				t.Fatalf("missing definition: %v %v", state, err)
			}
			for _, c := range []struct {
				name    string
				content []byte
				want    DefinitionState
			}{
				{"current", plan.Content, DefinitionCurrent},
				{"legacy", plan.Legacy[0], DefinitionLegacy},
				{"first terminal PATH", platform.WithServicePath(tt.os, rendered.Content, "/opt/homebrew/bin:/usr/bin:/bin"), DefinitionOtherPath},
				{"no PATH", rendered.Content, DefinitionOtherPath},
				{"edited by hand", bytes.Replace(plan.Content, []byte("manager"), []byte("other"), 1), DefinitionUnknown},
				{"another variable added by hand", withAnotherVariable(tt.os, plan.Content), DefinitionUnknown},
			} {
				if err := os.WriteFile(plan.Definition, c.content, 0o600); err != nil {
					t.Fatal(err)
				}
				if state, err := InspectServiceDefinition(plan); err != nil || state != c.want {
					t.Fatalf("%s: got %v %v", c.name, state, err)
				}
			}
		})
	}
}

// withAnotherVariable adds a variable next to PATH, as an edit by hand would.
func withAnotherVariable(goos string, content []byte) []byte {
	if goos == "linux" {
		return bytes.Replace(content, []byte("[Service]\n"), []byte("[Service]\nEnvironment=\"FOO=bar\"\n"), 1)
	}
	return bytes.Replace(content, []byte("</string></dict>"), []byte("</string><key>FOO</key><string>bar</string></dict>"), 1)
}

func TestInstallServiceStartsADefinitionFromAnotherShellAsWritten(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Task Scheduler definitions do not carry PATH")
	}
	root, _ := fixture(t)
	home := t.TempDir()
	t.Setenv("HOME", home)
	exe := filepath.Join(t.TempDir(), "railwait")
	if err := os.WriteFile(exe, []byte("fixture executable"), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", "/first/terminal/bin:/usr/bin")
	first, err := platform.CurrentServicePlan(exe, root)
	if err != nil {
		t.Fatal(err)
	}
	if err := WriteServiceDefinition(first); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", "/codex/tmp/bin:/usr/bin")
	runner := &fakeServiceRunner{}
	plan, err := InstallService(context.Background(), root, exe, runner)
	if err != nil {
		t.Fatal(err)
	}
	if data, _ := os.ReadFile(plan.Definition); !bytes.Equal(data, first.Content) {
		t.Fatal("definition from the first terminal was rewritten")
	}
	if len(runner.commands) == 0 || runner.commands[len(runner.commands)-1].Program != plan.Start.Program {
		t.Fatalf("service not started: %v", runner.commands)
	}
	if err := os.WriteFile(plan.Definition, []byte("edited by hand"), 0o600); err != nil {
		t.Fatal(err)
	}
	runner.commands = nil
	if _, err := InstallService(context.Background(), root, exe, runner); err == nil {
		t.Fatal("started a service from a definition edited by hand")
	}
	if len(runner.commands) != 0 {
		t.Fatalf("service commands ran for an unknown definition: %v", runner.commands)
	}
}
