//go:build windows

package platform

import (
	"crypto/rand"
	"encoding/hex"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// Registering a real task changes the host, so this check runs only on request:
// RAILWAIT_TEST_TASK_SCHEDULER=1. It creates uniquely named tasks and deletes them.
func TestTaskSchedulerAcceptsDefinitionAndRejectsLegacyUTF8(t *testing.T) {
	if os.Getenv("RAILWAIT_TEST_TASK_SCHEDULER") != "1" {
		t.Skip("set RAILWAIT_TEST_TASK_SCHEDULER=1 to register a temporary Task Scheduler task")
	}
	identity, err := serviceIdentity()
	if err != nil {
		t.Fatal(err)
	}
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	root := filepath.Join(t.TempDir(), "Rail Wait 설치")
	plan, err := RenderService("windows", home, identity, filepath.Join(root, ".railwait", "bin", "railwait.exe"), root)
	if err != nil {
		t.Fatal(err)
	}
	register := func(content []byte) (string, string, error) {
		suffix := make([]byte, 6)
		if _, err := rand.Read(suffix); err != nil {
			t.Fatal(err)
		}
		name := "railwait-test-" + hex.EncodeToString(suffix)
		definition := filepath.Join(t.TempDir(), name+".xml")
		if err := os.WriteFile(definition, content, 0o600); err != nil {
			t.Fatal(err)
		}
		output, err := exec.Command("schtasks.exe", "/Create", "/TN", name, "/XML", definition, "/F").CombinedOutput()
		if err == nil {
			t.Cleanup(func() { _ = exec.Command("schtasks.exe", "/Delete", "/TN", name, "/F").Run() })
		}
		return name, string(output), err
	}
	name, output, err := register(plan.Content)
	if err != nil {
		t.Fatalf("Task Scheduler rejected the definition: %v %s", err, output)
	}
	stored, err := exec.Command("schtasks.exe", "/Query", "/TN", name, "/V", "/FO", "LIST").CombinedOutput()
	if err != nil {
		t.Fatal(err, string(stored))
	}
	if !strings.Contains(string(stored), "conhost.exe --headless") {
		t.Fatalf("stored task does not run the manager headless:\n%s", stored)
	}
	// The message is localized, so only the rejection itself is asserted.
	if _, output, err := register(plan.Legacy[0]); err == nil {
		t.Fatal("legacy UTF-8 definition was accepted; the migration reason no longer holds")
	} else {
		t.Logf("legacy UTF-8 definition rejected as in 1.4.5: %v %q", err, output)
	}
}
