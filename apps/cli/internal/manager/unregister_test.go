package manager

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

// registrationRunner stands in for launchctl, systemctl and schtasks. The status command
// succeeds while the service is registered; removes is the command that unregisters it.
type registrationRunner struct {
	plan       platform.ServicePlan
	registered bool
	removes    platform.ServiceCommand
	ignore     bool
	commands   []string
	// definitionAtAfterRemoval records whether the definition still existed when the
	// after-removal command ran.
	definitionAtAfterRemoval []bool
}

func (r *registrationRunner) Run(_ context.Context, command platform.ServiceCommand) error {
	line := command.Program + " " + strings.Join(command.Args, " ")
	r.commands = append(r.commands, line)
	if reflect.DeepEqual(command, r.plan.Status) {
		if r.registered {
			return nil
		}
		return errors.New("not registered")
	}
	for _, after := range r.plan.AfterRemoval {
		if reflect.DeepEqual(command, after) {
			_, err := os.Lstat(r.plan.Definition)
			r.definitionAtAfterRemoval = append(r.definitionAtAfterRemoval, err == nil)
		}
	}
	if reflect.DeepEqual(command, r.removes) && !r.ignore {
		r.registered = false
	}
	return nil
}

func TestUnregisterServiceRemovesTheRegistrationOnEachOS(t *testing.T) {
	cases := []struct{ os, home, user, exe, root string }{
		{"linux", "/home/a", "1000", "/home/a/.local/bin/railwait", "/home/a/.local/share/railwait"},
		{"darwin", "/Users/a", "501", "/Users/a/.local/bin/railwait", "/Users/a/Library/Application Support/Railwait"},
		{"windows", `C:\Users\a`, "S-1-5-21-123", `C:\Users\a\Railwait\.railwait\bin\railwait.exe`, `C:\Users\a\Railwait`},
	}
	for _, tt := range cases {
		t.Run(tt.os, func(t *testing.T) {
			plan, err := platform.RenderService(tt.os, tt.home, tt.user, tt.exe, tt.root)
			if err != nil {
				t.Fatal(err)
			}
			// The rendered definition path belongs to the target OS; keep a real file here.
			plan.Definition = filepath.Join(t.TempDir(), filepath.Base(strings.ReplaceAll(plan.Definition, `\`, "/")))
			if err := os.WriteFile(plan.Definition, plan.Content, 0o600); err != nil {
				t.Fatal(err)
			}
			runner := &registrationRunner{plan: plan, registered: true, removes: plan.Unregister[len(plan.Unregister)-1]}
			if err := UnregisterService(context.Background(), plan, runner); err != nil {
				t.Fatal(err)
			}
			want := []string{}
			for _, command := range plan.Unregister {
				want = append(want, command.Program+" "+strings.Join(command.Args, " "))
			}
			if got := strings.Join(runner.commands, "\n"); !strings.Contains(got, strings.Join(want, "\n")) {
				t.Fatalf("commands:\n%s", got)
			}
			if _, err := os.Lstat(plan.Definition); !errors.Is(err, os.ErrNotExist) {
				t.Fatal("service definition kept")
			}
			if len(runner.definitionAtAfterRemoval) != len(plan.AfterRemoval) || (len(plan.AfterRemoval) > 0 && runner.definitionAtAfterRemoval[0]) {
				t.Fatalf("after-removal commands ran before the definition was removed: %v", runner.definitionAtAfterRemoval)
			}
			// A second run after the service is gone changes nothing.
			runner.commands = nil
			if err := UnregisterService(context.Background(), plan, runner); err != nil {
				t.Fatal(err)
			}
			if len(runner.commands) != 1 || runner.commands[0] != plan.Status.Program+" "+strings.Join(plan.Status.Args, " ") {
				t.Fatalf("repeated uninstall ran %v", runner.commands)
			}
		})
	}
}

func TestUnregisterServiceKeepsTheDefinitionWhileTheServiceStaysRegistered(t *testing.T) {
	plan, err := platform.RenderService("darwin", "/Users/a", "501", "/Users/a/.local/bin/railwait", "/Users/a/Railwait")
	if err != nil {
		t.Fatal(err)
	}
	plan.Definition = filepath.Join(t.TempDir(), "agent.plist")
	if err := os.WriteFile(plan.Definition, plan.Content, 0o600); err != nil {
		t.Fatal(err)
	}
	runner := &registrationRunner{plan: plan, registered: true, removes: plan.Unregister[0], ignore: true}
	if err := UnregisterService(context.Background(), plan, runner); err == nil {
		t.Fatal("reported success while the service stayed registered")
	}
	if _, err := os.Lstat(plan.Definition); err != nil {
		t.Fatal("definition removed while the service stayed registered")
	}
}
