package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/access"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/setup"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/terminal"
)

const remoteFixtureDNS = "new-device.tailfixture.ts.net"

// This fake stops at the OS command boundary. Status decoding, prerequisite
// result handling and the actual access.Client Serve planning run unchanged.
type remotePreparationFixture struct {
	present                                                   bool
	states                                                    []access.Status
	afterLogin                                                []access.Status
	installResult                                             setup.InstallResult
	installErr, loginErr, waitErr                             error
	installs, logins, waits, prompts, statusCalls, serveCalls int
	promptOffersLogin                                         bool
	events                                                    []string
	authenticationArgs                                        []string
	commands                                                  [][]string
	statusFailures, approvals                                 int
	unboundedWait                                             bool
}

func (f *remotePreparationFixture) Run(_ context.Context, args ...string) ([]byte, error) {
	f.commands = append(f.commands, append([]string(nil), args...))
	if reflect.DeepEqual(args, []string{"status", "--json"}) {
		f.statusCalls++
		if f.statusFailures > 0 {
			f.statusFailures--
			f.events = append(f.events, "status:daemon-starting")
			return nil, errors.New("fixture daemon is starting")
		}
		if len(f.states) == 0 {
			return nil, errors.New("fixture has no status")
		}
		state := f.states[0]
		if len(f.states) > 1 {
			f.states = f.states[1:]
		}
		f.events = append(f.events, "status:"+state.BackendState)
		value := map[string]any{"BackendState": state.BackendState}
		if state.DNSName != "" {
			value["Self"] = map[string]any{"DNSName": state.DNSName + ".", "Online": state.Online}
		}
		return json.Marshal(value)
	}
	if reflect.DeepEqual(args, []string{"serve", "status", "--json"}) {
		f.serveCalls++
		f.events = append(f.events, "serve:status")
		return []byte(`{}`), nil
	}
	return nil, errors.New("unexpected Tailscale mutation")
}

func (f *remotePreparationFixture) operations() remotePreparationOperations {
	return remotePreparationOperations{
		find: func(string) (string, error) {
			if !f.present {
				f.events = append(f.events, "discover:missing")
				return "", errors.New("not installed")
			}
			return "/fixture/tailscale", nil
		},
		canInstall: func(string) bool { return !f.present },
		install: func(_ context.Context, tool string, progress func(string)) (setup.InstallResult, error) {
			f.installs++
			f.events = append(f.events, "install:"+tool)
			if f.installErr == nil && f.installResult.Installed {
				f.present = true
			}
			progress("공식 설치 프로그램 결과 확인")
			return f.installResult, f.installErr
		},
		client: func(string) access.Client { return access.Client{Runner: f} },
		login: func(ctx context.Context, _ *terminal.UI, path string) error {
			f.logins++
			// Inspect the real command builder; never start the OS process.
			command := tailscaleAuthenticationCommand(ctx, path)
			f.authenticationArgs = append([]string(nil), command.Args...)
			f.events = append(f.events, "authenticate:"+strings.Join(command.Args[1:], " "))
			if f.loginErr == nil && len(f.afterLogin) != 0 {
				f.states = f.afterLogin
			}
			return f.loginErr
		},
		wait: func(ctx context.Context) error {
			deadline, ok := ctx.Deadline()
			f.unboundedWait = f.unboundedWait || !ok || time.Until(deadline) > 5*time.Minute
			f.waits++
			f.events = append(f.events, "wait")
			return f.waitErr
		},
		approveDevice: func(context.Context) error { f.approvals++; return nil },
		prompt: func(_ context.Context, _ *terminal.UI, _ setup.Need, needsLogin bool) (string, error) {
			f.prompts++
			f.promptOffersLogin = needsLogin
			return "0", nil
		},
	}
}

func remoteTestUI(t *testing.T) (*terminal.UI, *bytes.Buffer) {
	t.Helper()
	output := new(bytes.Buffer)
	ui := terminal.New(output, terminal.Plain(80))
	t.Cleanup(ui.Close)
	return ui, output
}

func remoteReadyState() access.Status {
	return access.Status{BackendState: "Running", DNSName: remoteFixtureDNS, Online: true}
}

func TestPrepareRemoteFirstInstallWaitsAuthenticatesAndPlansHTTPS(t *testing.T) {
	f := remotePreparationFixture{
		states:        []access.Status{{BackendState: "NoState"}, {BackendState: "Starting"}, {BackendState: "NeedsLogin"}},
		afterLogin:    []access.Status{remoteReadyState()},
		installResult: setup.InstallResult{Installed: true, NeedsUserAction: true},
	}
	ui, _ := remoteTestUI(t)
	client, plan, err := prepareRemoteWith(context.Background(), ui, wizardOptions{Port: 18080}, true, f.operations())
	if err != nil || client == nil || plan.URL != "https://"+remoteFixtureDNS || plan.LocalPort != 18080 || plan.HTTPSPort != 443 || plan.Existing {
		t.Fatalf("fresh installation did not reach a verified plan: client=%v plan=%+v err=%v", client, plan, err)
	}
	want := []string{"discover:missing", "install:tailscale", "status:NoState", "wait", "status:Starting", "wait", "status:NeedsLogin", "authenticate:up", "status:Running", "status:Running", "serve:status"}
	if !reflect.DeepEqual(f.events, want) {
		t.Fatalf("wrong first-login lifecycle: got %v want %v", f.events, want)
	}
	if f.installs != 1 || f.logins != 1 || f.waits != 2 || f.prompts != 0 || f.serveCalls != 1 {
		t.Fatalf("repeated or omitted preparation: %+v", f)
	}
	if !reflect.DeepEqual(f.authenticationArgs, []string{"/fixture/tailscale", "up"}) {
		t.Fatal("authentication changed profile or settings", f.authenticationArgs)
	}
	for _, command := range f.commands {
		if !reflect.DeepEqual(command, []string{"status", "--json"}) && !reflect.DeepEqual(command, []string{"serve", "status", "--json"}) {
			t.Fatal("planning mutated Tailscale", command)
		}
	}
}

func TestPrepareRemoteDeclinedLoginNeverReportsReadyOrRetriesAutomatically(t *testing.T) {
	f := remotePreparationFixture{
		states:        []access.Status{{BackendState: "NeedsLogin"}},
		installResult: setup.InstallResult{Installed: true, NeedsUserAction: true},
		// A command returning without completing authentication must stop with
		// an actionable error, not launch another login or claim readiness.
	}
	ui, output := remoteTestUI(t)
	client, plan, err := prepareRemoteWith(context.Background(), ui, wizardOptions{Port: 18080}, true, f.operations())
	if err == nil || !strings.Contains(err.Error(), "로그인이 완료되지") || client != nil || plan.URL != "" {
		t.Fatal("declined login was treated as ready", plan, err)
	}
	if f.installs != 1 || f.logins != 1 || f.prompts != 0 || f.serveCalls != 0 {
		t.Fatalf("login rejection repeated actions or planned a route: %+v", f)
	}
	if strings.Contains(output.String(), "Tailscale 로그인 재사용") {
		t.Fatal("success was displayed after rejection")
	}
}

func TestPrepareRemoteStopsAtInstallerRebootBeforeLoginOrPlan(t *testing.T) {
	for _, installed := range []bool{false, true} {
		f := remotePreparationFixture{installResult: setup.InstallResult{Installed: installed, RebootRequired: true, NeedsUserAction: true}}
		ui, _ := remoteTestUI(t)
		client, plan, err := prepareRemoteWith(context.Background(), ui, wizardOptions{Port: 18080}, true, f.operations())
		if err == nil || !strings.Contains(err.Error(), "재부팅") || client != nil || plan.URL != "" {
			t.Fatal("reboot requirement was ignored", plan, err)
		}
		if f.installs != 1 || f.logins != 0 || f.statusCalls != 0 || f.serveCalls != 0 || f.prompts != 0 {
			t.Fatalf("continued setup across reboot boundary: %+v", f)
		}
	}
}

func TestPrepareRemoteExistingOfflineNodeNeverAuthenticatesOrReinstalls(t *testing.T) {
	f := remotePreparationFixture{present: true, states: []access.Status{{BackendState: "Running", DNSName: remoteFixtureDNS, Online: false}}}
	ui, _ := remoteTestUI(t)
	client, plan, err := prepareRemoteWith(context.Background(), ui, wizardOptions{Port: 18080}, true, f.operations())
	if !errors.Is(err, context.DeadlineExceeded) || client != nil || plan.URL != "" {
		t.Fatal("offline node was treated as ready", plan, err)
	}
	if f.installs != 0 || f.logins != 0 || f.prompts != 0 || f.promptOffersLogin || f.serveCalls != 0 || f.waits != 150 || f.unboundedWait {
		t.Fatalf("offline existing profile would be changed: %+v", f)
	}
}

func TestPrepareRemoteInitializationWaitIsBoundedAndCancelable(t *testing.T) {
	for _, waitErr := range []error{nil, context.Canceled} {
		f := remotePreparationFixture{present: true, states: []access.Status{{BackendState: "NoState"}}, waitErr: waitErr}
		ui, _ := remoteTestUI(t)
		client, _, err := prepareRemoteWith(context.Background(), ui, wizardOptions{Port: 18080}, true, f.operations())
		wantErr := context.DeadlineExceeded
		if waitErr != nil {
			wantErr = waitErr
		}
		if !errors.Is(err, wantErr) || client != nil || f.logins != 0 || f.serveCalls != 0 || f.unboundedWait {
			t.Fatal("uninitialized node became ready", err)
		}
		if waitErr == nil && (f.waits != 150 || f.statusCalls != 151 || f.prompts != 0) {
			t.Fatalf("initialization did not stop after bounded retries: %+v", f)
		}
		if waitErr != nil && (f.waits != 1 || f.statusCalls != 1 || f.prompts != 0) {
			t.Fatalf("cancellation did not stop initialization immediately: %+v", f)
		}
	}
}

func TestPrepareRemoteAutomaticallyWaitsForDaemonMachineApprovalAndNetwork(t *testing.T) {
	f := remotePreparationFixture{
		present: true, statusFailures: 2,
		states: []access.Status{
			{BackendState: "NoState"}, {BackendState: "Starting"},
			{BackendState: "NeedsMachineAuth"}, {BackendState: "NeedsMachineAuth"},
			{BackendState: "Running", DNSName: remoteFixtureDNS, Online: false}, remoteReadyState(),
		},
	}
	ui, output := remoteTestUI(t)
	client, plan, err := prepareRemoteWith(context.Background(), ui, wizardOptions{Port: 18080}, true, f.operations())
	if err != nil || client == nil || plan.DNSName != remoteFixtureDNS || f.waits != 7 || f.approvals != 1 || f.prompts != 0 || f.logins != 0 || f.installs != 0 || f.unboundedWait {
		t.Fatal("preparation required a technical choice or modified authentication", err, f.waits, f.approvals)
	}
	if strings.Contains(output.String(), "선택 [") {
		t.Fatal("automatic waiting prompted for Enter")
	}
}

func TestPrepareRemoteRejectsIdentityChangeWhileOfflineWithoutAuthenticating(t *testing.T) {
	f := remotePreparationFixture{present: true, states: []access.Status{
		{BackendState: "Running", DNSName: remoteFixtureDNS, Online: false},
		{BackendState: "Running", DNSName: "other.tailfixture.ts.net", Online: true},
	}}
	ui, _ := remoteTestUI(t)
	client, _, err := prepareRemoteWith(context.Background(), ui, wizardOptions{Port: 18080}, true, f.operations())
	if err == nil || client != nil || f.logins != 0 || f.serveCalls != 0 || f.waits != 1 {
		t.Fatal("changed profile was used", err)
	}
}

func TestPrepareRemoteNoninteractiveNeverOpensInstallsAuthenticatesOrWaits(t *testing.T) {
	for _, state := range []string{"NoState", "NeedsLogin", "NeedsMachineAuth", "Running"} {
		f := remotePreparationFixture{present: true, states: []access.Status{{BackendState: state}}}
		ui, _ := remoteTestUI(t)
		client, _, err := prepareRemoteWith(context.Background(), ui, wizardOptions{Port: 18080}, false, f.operations())
		if err == nil || client != nil || f.logins+f.approvals+f.installs+f.waits+f.prompts != 0 {
			t.Fatal("noninteractive preparation mutated the host", state, err)
		}
	}
}
