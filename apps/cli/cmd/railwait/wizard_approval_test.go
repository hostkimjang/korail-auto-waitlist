package main

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/access"
)

const wizardApprovalURL = "https://login.tailscale.com/f/serve?node=fixture-node&token=fixture-consent-token"

type httpsApprovalFixture struct {
	config     map[string]any
	approved   bool
	applyError error
	applyCalls int
	commands   [][]string
}

func approvalRoute(config map[string]any, httpsPort, localPort int) {
	if config["TCP"] == nil {
		config["TCP"] = map[string]any{}
	}
	if config["Web"] == nil {
		config["Web"] = map[string]any{}
	}
	port := strconv.Itoa(httpsPort)
	config["TCP"].(map[string]any)[port] = map[string]any{"HTTPS": true}
	config["Web"].(map[string]any)[remoteFixtureDNS+":"+port] = map[string]any{"Handlers": map[string]any{"/": map[string]any{"Proxy": "http://127.0.0.1:" + strconv.Itoa(localPort)}}}
}

func (f *httpsApprovalFixture) Run(_ context.Context, args ...string) ([]byte, error) {
	f.commands = append(f.commands, append([]string(nil), args...))
	if reflect.DeepEqual(args, []string{"status", "--json"}) {
		return json.Marshal(map[string]any{"BackendState": "Running", "Self": map[string]any{"DNSName": remoteFixtureDNS + ".", "Online": true}})
	}
	if reflect.DeepEqual(args, []string{"serve", "status", "--json"}) {
		return json.Marshal(f.config)
	}
	if len(args) == 4 && args[0] == "serve" && args[1] == "--bg" {
		f.applyCalls++
		if !f.approved {
			approval, _ := access.NewHTTPSApprovalRequired(wizardApprovalURL)
			return nil, approval
		}
		if f.applyError != nil {
			return nil, f.applyError
		}
		httpsPort, err := strconv.Atoi(strings.TrimPrefix(args[2], "--https="))
		if err != nil || args[3] != "http://127.0.0.1:18080" {
			return nil, errors.New("unexpected mutation")
		}
		approvalRoute(f.config, httpsPort, 18080)
		return nil, nil
	}
	return nil, errors.New("unexpected Tailscale command")
}

func approvalClientPlan(t *testing.T, fixture *httpsApprovalFixture) (access.Client, access.Plan) {
	t.Helper()
	client := access.Client{Runner: fixture}
	plan, err := client.Plan(context.Background(), 18080, 0)
	if err != nil {
		t.Fatal(err)
	}
	return client, plan
}

func TestRemoteHTTPSApprovalOpensOnceThenVerifiesAndPreservesExistingRoutes(t *testing.T) {
	f := &httpsApprovalFixture{config: map[string]any{}}
	approvalRoute(f.config, 443, 8080)
	client, plan := approvalClientPlan(t, f)
	if plan.HTTPSPort != 8443 {
		t.Fatal("existing route was not respected")
	}
	ui, output := remoteTestUI(t)
	opens, waits := 0, 0
	result, err := applyRemoteWithApproval(context.Background(), ui, plan, true, httpsApprovalOperations{
		apply: client.Apply,
		open: func(_ context.Context, address string) error {
			opens++
			if address != wizardApprovalURL {
				t.Fatal("wrong consent page")
			}
			return nil
		},
		wait: func(ctx context.Context) error {
			waits++
			deadline, ok := ctx.Deadline()
			if !ok || time.Until(deadline) > 5*time.Minute {
				t.Fatal("approval wait is not bounded")
			}
			// One pending retry proves we neither reopen the browser nor report
			// completion merely because the page was opened.
			if waits == 2 {
				f.approved = true
			}
			return nil
		},
	})
	if err != nil || !result.Created || result.RequiresReview || opens != 1 || waits != 2 || f.applyCalls != 3 {
		t.Fatal("consent did not complete safely", result, err, opens, waits, f.applyCalls)
	}
	want := map[string]any{}
	approvalRoute(want, 443, 8080)
	approvalRoute(want, 8443, 18080)
	if !reflect.DeepEqual(f.config, want) {
		t.Fatal("existing routes changed")
	}
	encoded, _ := json.Marshal(result)
	if strings.Contains(output.String()+string(encoded), "fixture-consent-token") || strings.Contains(output.String()+string(encoded), "login.tailscale.com/f/serve") {
		t.Fatal("consent URL leaked to UI or result")
	}
	for _, args := range f.commands {
		if strings.Contains(strings.Join(args, " "), "funnel") || strings.Contains(strings.Join(args, " "), "reset") {
			t.Fatal("public exposure or reset attempted")
		}
	}
}

func TestRemoteHTTPSExistingRouteDoesNotRequestConsent(t *testing.T) {
	f := &httpsApprovalFixture{config: map[string]any{}}
	approvalRoute(f.config, 443, 18080)
	client, plan := approvalClientPlan(t, f)
	ui, _ := remoteTestUI(t)
	result, err := applyRemoteWithApproval(context.Background(), ui, plan, true, httpsApprovalOperations{
		apply: client.Apply,
		open:  func(context.Context, string) error { t.Fatal("reused route opened consent page"); return nil },
		wait:  func(context.Context) error { t.Fatal("reused route waited for consent"); return nil },
	})
	if err != nil || result.Created || result.RequiresReview || f.applyCalls != 0 || !plan.Existing {
		t.Fatal("existing route was not reused", result, err)
	}
}

func TestRemoteHTTPSOrdinaryPermissionDenialNeverOpensBrowser(t *testing.T) {
	f := &httpsApprovalFixture{config: map[string]any{}, approved: true, applyError: errors.New("permission denied")}
	client, plan := approvalClientPlan(t, f)
	ui, _ := remoteTestUI(t)
	result, err := applyRemoteWithApproval(context.Background(), ui, plan, true, httpsApprovalOperations{
		apply: client.Apply,
		open:  func(context.Context, string) error { t.Fatal("ordinary error opened consent page"); return nil },
	})
	if err == nil || result.Created || result.RequiresReview || f.applyCalls != 1 {
		t.Fatal("permission denial was ignored", result, err)
	}
}

func TestRemoteHTTPSApprovalStopsOnDenialTimeoutAndBrowserFailure(t *testing.T) {
	for _, scenario := range []string{"denied", "timeout", "browser", "cancel", "noninteractive"} {
		t.Run(scenario, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			f := &httpsApprovalFixture{config: map[string]any{}}
			client, plan := approvalClientPlan(t, f)
			ui, output := remoteTestUI(t)
			opens := 0
			result, err := applyRemoteWithApproval(ctx, ui, plan, scenario != "noninteractive", httpsApprovalOperations{
				apply: client.Apply,
				open: func(context.Context, string) error {
					opens++
					if scenario == "browser" {
						return errors.New("browser refused " + wizardApprovalURL)
					}
					if scenario == "denied" {
						f.approved = true
						f.applyError = errors.New("permission denied")
					}
					if scenario == "cancel" {
						cancel()
					}
					return nil
				},
				wait: func(ctx context.Context) error {
					if scenario == "timeout" {
						return context.DeadlineExceeded
					}
					return ctx.Err()
				},
			})
			wantOpens := 1
			if scenario == "noninteractive" {
				wantOpens = 0
			}
			if err == nil || result.Created || result.RequiresReview || opens != wantOpens || len(f.config) != 0 {
				t.Fatal("failed consent was treated as success", result, err, opens)
			}
			if scenario == "timeout" && !errors.Is(err, context.DeadlineExceeded) {
				t.Fatal("timeout was not preserved", err)
			}
			if scenario == "cancel" && !errors.Is(err, context.Canceled) {
				t.Fatal("cancellation was not preserved", err)
			}
			wantApplies := 1
			if scenario == "denied" {
				wantApplies = 2
			}
			if f.applyCalls != wantApplies {
				t.Fatal("unexpected mutation retries", f.applyCalls)
			}
			if strings.Contains(output.String()+err.Error(), "fixture-consent-token") {
				t.Fatal("consent token leaked on failure")
			}
		})
	}
}

func TestRemoteHTTPSConsentNeverRetriesAnObservedOrUnknownMutation(t *testing.T) {
	approval, _ := access.NewHTTPSApprovalRequired(wizardApprovalURL)
	for _, result := range []access.Result{{Created: true}, {RequiresReview: true}} {
		ui, _ := remoteTestUI(t)
		calls := 0
		got, err := applyRemoteWithApproval(context.Background(), ui, access.Plan{}, true, httpsApprovalOperations{
			apply: func(context.Context, access.Plan) (access.Result, error) { calls++; return result, approval },
			open:  func(context.Context, string) error { t.Fatal("uncertain mutation opened consent page"); return nil },
		})
		if err == nil || got.Created != result.Created || got.RequiresReview != result.RequiresReview || calls != 1 {
			t.Fatal("mutation observation was ignored", got, err)
		}
	}
}

func TestRemoteHTTPSConsentRechecksPlanAfterExternalRouteChange(t *testing.T) {
	f := &httpsApprovalFixture{config: map[string]any{}}
	client, plan := approvalClientPlan(t, f)
	ui, _ := remoteTestUI(t)
	result, err := applyRemoteWithApproval(context.Background(), ui, plan, true, httpsApprovalOperations{
		apply: client.Apply,
		open: func(context.Context, string) error {
			f.approved = true
			approvalRoute(f.config, 443, 8080)
			return nil
		},
		wait: func(context.Context) error { return nil },
	})
	want := map[string]any{}
	approvalRoute(want, 443, 8080)
	if err == nil || result.Created || result.RequiresReview || f.applyCalls != 1 || !reflect.DeepEqual(f.config, want) {
		t.Fatal("stale approval plan overwrote an external route", result, err)
	}
}
