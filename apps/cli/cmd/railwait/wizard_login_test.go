package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/access"
)

const fixtureLoginURL = "https://login.tailscale.com/a/fixture-login-secret"

func TestFirstTailscaleLoginOpensValidatedStreamURLOnceWithoutPrintingIt(t *testing.T) {
	ui, output := remoteTestUI(t)
	opens := []string{}
	err := loginTailscaleWith(context.Background(), ui, "/fixture/tailscale", tailscaleLoginOperations{
		run: func(ctx context.Context, path string, writer io.Writer) error {
			command := tailscaleAuthenticationCommand(ctx, path)
			if !reflect.DeepEqual(command.Args, []string{path, "up"}) {
				t.Fatal("authentication changes profile or network settings", command.Args)
			}
			deadline, ok := ctx.Deadline()
			if !ok || time.Until(deadline) > 5*time.Minute {
				t.Fatal("login is not bounded")
			}
			for _, fragment := range []string{
				"To authenticate, visit:\n\thttps://login.", "tailscale.com/a/fixture-", "login-secret\n",
				fixtureLoginURL + "\n", "https://login.tailscale.com/a/second-token\nSuccess.\n",
			} {
				if _, err := io.WriteString(writer, fragment); err != nil {
					return err
				}
			}
			return nil
		},
		open: func(_ context.Context, address string) error { opens = append(opens, address); return nil },
	})
	if err != nil || !reflect.DeepEqual(opens, []string{fixtureLoginURL}) {
		t.Fatal("login did not open exactly one authentication page", err, len(opens))
	}
	if strings.Contains(output.String(), "fixture-login-secret") || strings.Contains(output.String(), "second-token") || strings.Contains(output.String(), "login.tailscale.com/a/") {
		t.Fatal("authentication output leaked to terminal")
	}
}

func TestTailscaleLoginRejectsUntrustedURLsAndNeverPrintsCommandErrors(t *testing.T) {
	ui, output := remoteTestUI(t)
	err := loginTailscaleWith(context.Background(), ui, "/fixture/tailscale", tailscaleLoginOperations{
		run: func(_ context.Context, _ string, writer io.Writer) error {
			for _, address := range []string{
				"https://untrusted.example/a/fixture-login-secret", "https://user@login.tailscale.com/a/fixture-login-secret",
				"https://login.tailscale.com:443/a/fixture-login-secret", "https://login.tailscale.com/a/fixture-login-secret#ignored",
				"https://login.tailscale.com/f/funnel?token=fixture-login-secret", "https://login.tailscale.com/admin?token=fixture-login-secret",
			} {
				fmt.Fprintln(writer, address)
			}
			return errors.New("native error repeats " + fixtureLoginURL)
		},
		open: func(context.Context, string) error { t.Fatal("untrusted URL opened a browser"); return nil },
	})
	if err == nil {
		t.Fatal("native failure was ignored")
	}
	if strings.Contains(output.String()+err.Error(), "fixture-login-secret") {
		t.Fatal("native error or output exposed authentication URL")
	}
}

func TestTailscaleLoginHandsOffDeviceApprovalOnceWithoutApprovingAnything(t *testing.T) {
	ui, output := remoteTestUI(t)
	opens := []string{}
	err := loginTailscaleWith(context.Background(), ui, "/fixture/tailscale", tailscaleLoginOperations{
		run: func(_ context.Context, _ string, writer io.Writer) error {
			fmt.Fprintln(writer, fixtureLoginURL)
			fmt.Fprintln(writer, "To approve your machine, visit (as admin):\nhttps://login.tailscale.com/admin")
			fmt.Fprintln(writer, access.DeviceApprovalURL)
			return nil
		},
		open: func(_ context.Context, address string) error { opens = append(opens, address); return nil },
	})
	if err != nil || !reflect.DeepEqual(opens, []string{fixtureLoginURL, access.DeviceApprovalURL}) {
		t.Fatal("device approval browser handoff is wrong", err, len(opens))
	}
	if strings.Contains(output.String(), fixtureLoginURL) {
		t.Fatal("login URL leaked during device approval")
	}
}

func TestTailscaleLoginStopsOnBrowserErrorCancellationAndDeadline(t *testing.T) {
	for _, scenario := range []string{"browser", "cancel", "deadline", "large-output"} {
		t.Run(scenario, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if scenario == "deadline" {
				var stop context.CancelFunc
				ctx, stop = context.WithTimeout(ctx, 20*time.Millisecond)
				defer stop()
			}
			ui, output := remoteTestUI(t)
			opens := 0
			err := loginTailscaleWith(ctx, ui, "/fixture/tailscale", tailscaleLoginOperations{
				run: func(runCtx context.Context, _ string, writer io.Writer) error {
					if scenario == "large-output" {
						_, _ = io.WriteString(writer, strings.Repeat("x", 8193))
					} else {
						_, _ = fmt.Fprintln(writer, fixtureLoginURL)
					}
					if scenario == "cancel" {
						cancel()
					}
					<-runCtx.Done()
					return runCtx.Err()
				},
				open: func(context.Context, string) error {
					opens++
					if scenario == "browser" {
						return errors.New("browser failed for " + fixtureLoginURL)
					}
					return nil
				},
			})
			if err == nil {
				t.Fatal("failed login became successful")
			}
			if scenario == "cancel" && !errors.Is(err, context.Canceled) {
				t.Fatal("cancellation was not preserved", err)
			}
			if scenario == "deadline" && !errors.Is(err, context.DeadlineExceeded) {
				t.Fatal("deadline was not preserved", err)
			}
			if opens > 1 || scenario == "large-output" && opens != 0 {
				t.Fatal("browser was reopened after failure", opens)
			}
			if strings.Contains(output.String()+err.Error(), "fixture-login-secret") {
				t.Fatal("failure leaked login token")
			}
		})
	}
}

func TestTailscaleLoginCancellationNeverOpensBufferedURL(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	ui, _ := remoteTestUI(t)
	err := loginTailscaleWith(ctx, ui, "/fixture/tailscale", tailscaleLoginOperations{
		run: func(_ context.Context, _ string, writer io.Writer) error {
			_, _ = io.WriteString(writer, fixtureLoginURL)
			cancel()
			return context.Canceled
		},
		open: func(context.Context, string) error { t.Fatal("canceled login opened a buffered URL"); return nil },
	})
	if !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
}

func TestTailscaleLoginConcurrentOutputDoesNotOpenTheBrowserTwice(t *testing.T) {
	ui, _ := remoteTestUI(t)
	opens := 0
	err := loginTailscaleWith(context.Background(), ui, "/fixture/tailscale", tailscaleLoginOperations{
		run: func(_ context.Context, _ string, writer io.Writer) error {
			var group sync.WaitGroup
			for range 2 {
				group.Go(func() { _, _ = fmt.Fprintln(writer, fixtureLoginURL) })
			}
			group.Wait()
			return nil
		},
		open: func(context.Context, string) error { opens++; return nil },
	})
	if err != nil || opens != 1 {
		t.Fatal("concurrent authentication streams reopened the browser", err, opens)
	}
}
