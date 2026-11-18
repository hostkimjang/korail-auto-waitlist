package platform

import (
	"bytes"
	"os/exec"
	"testing"
)

func TestLaunchAgentPassesNativePropertyListValidation(t *testing.T) {
	plan, err := RenderService("darwin", "/Users/fixture", "501", "/Users/fixture/My Apps/railwait", "/Users/fixture/Application Data/a & b")
	if err != nil {
		t.Fatal(err)
	}
	command := exec.Command("/usr/bin/plutil", "-lint", "-")
	command.Stdin = bytes.NewReader(plan.Content)
	if data, err := command.CombinedOutput(); err != nil {
		t.Fatalf("invalid launch agent plist: %v %s", err, data)
	}
}
