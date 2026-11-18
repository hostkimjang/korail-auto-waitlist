package access

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"
)

const fixtureApprovalURL = "https://login.tailscale.com/f/serve?node=fixture-node&token=fixture-consent-token"

func TestHTTPSApprovalURLAcceptsOnlyOfficialServeConsent(t *testing.T) {
	approval, err := NewHTTPSApprovalRequired(fixtureApprovalURL)
	if err != nil || approval.URL() != fixtureApprovalURL {
		t.Fatal("official consent URL was not retained in memory")
	}
	for _, address := range []string{
		"http://login.tailscale.com/f/serve",
		"https://other.example/f/serve",
		"https://login.tailscale.com.evil.example/f/serve",
		"https://user@login.tailscale.com/f/serve",
		"https://login.tailscale.com:443/f/serve",
		"https://login.tailscale.com:8443/f/serve",
		"https://login.tailscale.com:invalid/f/serve",
		"https://login.tailscale.com/f/funnel",
		"https://login.tailscale.com/f/serve/extra",
		"https://login.tailscale.com/f/%73erve",
		fixtureApprovalURL + "#fragment",
		fixtureApprovalURL + "#",
		fixtureApprovalURL + "\r\n",
		fixtureApprovalURL + "&x=%0A",
		fixtureApprovalURL + "&x=%E2%80%8B",
		fixtureApprovalURL + "&x=%FF",
		fixtureApprovalURL + "&x=%",
		fixtureApprovalURL + strings.Repeat("x", 4096),
	} {
		if _, err := NewHTTPSApprovalRequired(address); err == nil {
			t.Fatal("unsafe consent URL was accepted")
		} else if strings.Contains(err.Error(), "fixture-consent-token") {
			t.Fatal("invalid URL leaked through error")
		}
	}
}

func TestHTTPSApprovalCannotLeakThroughErrorFormattingOrJSON(t *testing.T) {
	approval, _ := NewHTTPSApprovalRequired(fixtureApprovalURL)
	encoded, err := json.Marshal(approval)
	if err != nil {
		t.Fatal(err)
	}
	for _, text := range []string{approval.Error(), fmt.Sprintf("%v", approval), fmt.Sprintf("%+v", approval), fmt.Sprintf("%#v", approval), string(encoded)} {
		if strings.Contains(text, "login.tailscale.com") || strings.Contains(text, "fixture-consent-token") {
			t.Fatal("one-time consent URL escaped its explicit accessor")
		}
	}
}

func TestHTTPSApprovalOutputRequiresOneUnambiguousOfficialURL(t *testing.T) {
	for _, output := range []struct{ stdout, stderr string }{
		{"Enable HTTPS:\n" + fixtureApprovalURL + "\nWaiting...", ""},
		{"", "Enable HTTPS:\n" + fixtureApprovalURL + "\nWaiting..."},
		{fixtureApprovalURL, fixtureApprovalURL},
	} {
		approval := approvalFromOutput([]byte(output.stdout), []byte(output.stderr))
		if approval == nil || approval.URL() != fixtureApprovalURL {
			t.Fatal("consent URL was not extracted")
		}
	}
	for _, output := range []string{
		"permission denied",
		"https://login.tailscale.com/f/funnel?token=fixture-consent-token",
		fixtureApprovalURL + "\nhttps://login.tailscale.com/f/serve?node=another",
		fixtureApprovalURL + strings.Repeat(" ", maxOutput),
	} {
		if approvalFromOutput([]byte(output), nil) != nil {
			t.Fatal("ambiguous or excessive output triggered approval")
		}
	}
}

func TestHTTPSApprovalOnlyClassifiesExactServeApplyCommand(t *testing.T) {
	if !isHTTPSApplyCommand([]string{"serve", "--bg", "--https=443", "http://127.0.0.1:18080"}) {
		t.Fatal("expected apply command rejected")
	}
	for _, args := range [][]string{
		{"serve", "--bg", "--https=443", "off"},
		{"serve", "status", "--json"},
		{"funnel", "--bg", "--https=443", "http://127.0.0.1:18080"},
		{"serve", "--bg", "--https=0443", "http://127.0.0.1:18080"},
		{"serve", "--bg", "--https=65536", "http://127.0.0.1:18080"},
		{"serve", "--bg", "--https=443", "http://localhost:18080"},
		{"serve", "--bg", "--https=443", "http://127.0.0.1:18080/"},
		{"serve", "--bg", "--https=443", "http://127.0.0.1:18080", "--yes"},
	} {
		if isHTTPSApplyCommand(args) {
			t.Fatal("non-apply command accepted", args)
		}
	}
}

// Only the test executable is launched, never a host Tailscale command.
func TestHTTPSApprovalExecHelper(t *testing.T) {
	mode := os.Args[len(os.Args)-1]
	if mode == "fixture-approval-stdout" {
		fmt.Fprintln(os.Stdout, fixtureApprovalURL)
		os.Exit(1)
	}
	if mode == "fixture-approval-stderr" {
		fmt.Fprintln(os.Stderr, fixtureApprovalURL)
		os.Exit(1)
	}
	if mode == "fixture-approval-wait" {
		fmt.Fprintln(os.Stderr, fixtureApprovalURL)
		time.Sleep(time.Minute)
		os.Exit(1)
	}
}

func TestExecRunnerCapturesConsentOnlyForApplyAndRedactsRawOutput(t *testing.T) {
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"fixture-approval-stdout", "fixture-approval-stderr"} {
		runner := ExecRunner{Executable: "fixture-tailscale", command: func(ctx context.Context, _ string, _ ...string) *exec.Cmd {
			return exec.CommandContext(ctx, executable, "-test.run=^TestHTTPSApprovalExecHelper$", "--", mode)
		}}
		for _, apply := range []bool{true, false} {
			args := []string{"status", "--json"}
			if apply {
				args = []string{"serve", "--bg", "--https=443", "http://127.0.0.1:18080"}
			}
			output, runErr := runner.Run(context.Background(), args...)
			var approval *HTTPSApprovalRequired
			if runErr == nil || len(output) != 0 || errors.As(runErr, &approval) != apply {
				t.Fatal("wrong command error classification", runErr)
			}
			if strings.Contains(runErr.Error(), "fixture-consent-token") {
				t.Fatal("raw process output leaked")
			}
		}
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		_, runErr := runner.Run(ctx, "serve", "--bg", "--https=443", "http://127.0.0.1:18080")
		if !errors.Is(runErr, context.Canceled) {
			t.Fatal("canceled command requested approval", runErr)
		}
	}
}

func TestExecRunnerRetainsConsentWhenCommandWaitTimesOut(t *testing.T) {
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	var stopCommand context.CancelFunc
	t.Cleanup(func() {
		if stopCommand != nil {
			stopCommand()
		}
	})
	runner := ExecRunner{Executable: "fixture-tailscale", command: func(ctx context.Context, _ string, _ ...string) *exec.Cmd {
		// Shorten only the child command's timeout. The user context remains
		// active, just as it does after the production 30-second command wait.
		commandCtx, cancel := context.WithTimeout(ctx, 500*time.Millisecond)
		stopCommand = cancel
		return exec.CommandContext(commandCtx, executable, "-test.run=^TestHTTPSApprovalExecHelper$", "--", "fixture-approval-wait")
	}}
	output, err := runner.Run(context.Background(), "serve", "--bg", "--https=443", "http://127.0.0.1:18080")
	var approval *HTTPSApprovalRequired
	if len(output) != 0 || !errors.As(err, &approval) || approval.URL() != fixtureApprovalURL {
		t.Fatal("command timeout lost the pending consent request", err)
	}
}

func TestApplyApprovalErrorPreservesObservedMutationFlags(t *testing.T) {
	approval, _ := NewHTTPSApprovalRequired(fixtureApprovalURL)
	for _, committed := range []bool{false, true} {
		runner := newRunner()
		if committed {
			runner.serveErrAfterApply = approval
		} else {
			runner.serveErr = approval
		}
		client := Client{Runner: runner}
		plan, err := client.Plan(context.Background(), 18080, 0)
		if err != nil {
			t.Fatal(err)
		}
		result, err := client.Apply(context.Background(), plan)
		var got *HTTPSApprovalRequired
		if !errors.As(err, &got) || result.Created != committed || result.RequiresReview {
			t.Fatal("approval result lost post-command observation", result, err)
		}
	}
}

func TestApprovalFailureRollbackPreservesAnotherRouteSharingItsPort(t *testing.T) {
	approval, _ := NewHTTPSApprovalRequired(fixtureApprovalURL)
	for _, scope := range []string{"another-host", "foreground"} {
		t.Run(scope, func(t *testing.T) {
			runner := newRunner()
			runner.serveErrAfterApply = approval
			runner.changeAfterApply = func(config map[string]any) {
				if scope == "another-host" {
					object(config, "Web")["other.tail12345.ts.net:443"] = map[string]any{"Handlers": map[string]any{"/": map[string]any{"Proxy": "http://127.0.0.1:8080"}}}
				} else {
					config["Foreground"] = map[string]any{"external-session": map[string]any{"TCP": map[string]any{"443": map[string]any{"HTTPS": true}}}}
				}
			}
			client := Client{Runner: runner}
			plan, err := client.Plan(context.Background(), 18080, 0)
			if err != nil {
				t.Fatal(err)
			}
			result, err := client.Apply(context.Background(), plan)
			if err == nil || !result.Created {
				t.Fatal("expected a committed route despite the command failure", result, err)
			}
			before, _ := json.Marshal(runner.config)
			if err := client.Rollback(context.Background(), result); err == nil {
				t.Fatal("rollback removed a shared HTTPS port")
			}
			after, _ := json.Marshal(runner.config)
			if string(before) != string(after) || mutations(runner) != 1 {
				t.Fatal("rollback changed another route sharing the port")
			}
		})
	}
}
