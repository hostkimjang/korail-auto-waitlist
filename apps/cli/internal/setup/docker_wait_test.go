package setup

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"
)

func waitingDesktopFixture() (*dockerFixture, dockerOperations) {
	f := readyFixture()
	f.current, f.endpoint, f.offline = "desktop-linux", "unix:///Users/user/.docker/run/docker.sock", true
	ops := f.operations()
	ops.start = func(context.Context, string) error { f.started++; return nil }
	return f, ops
}

func TestDesktopWaitContinuesPastFifteenPollsWithoutAnotherPrompt(t *testing.T) {
	f, ops := waitingDesktopFixture()
	prompts, polls := 0, 0
	ops.wait = func(ctx context.Context) error {
		deadline, ok := ctx.Deadline()
		if !ok || time.Until(deadline) > 5*time.Minute || time.Until(deadline) < 4*time.Minute {
			t.Fatal("Desktop readiness wait must have a five-minute deadline")
		}
		polls++
		if polls == 20 {
			f.offline = false
		}
		return nil
	}
	ready, err := ensureDocker(context.Background(), "", func(Need) (Action, error) {
		prompts++
		if prompts > 1 {
			t.Fatal("license/setup completion must not require another prompt")
		}
		return Start, nil
	}, ops)
	if err != nil || ready.Context != "desktop-linux" || polls != 20 || prompts != 1 || f.started != 1 {
		t.Fatal(ready, err, polls, prompts, f.started)
	}
}

func TestDesktopWaitTimeoutReturnsDeadlineWithoutReprompting(t *testing.T) {
	f, ops := waitingDesktopFixture()
	prompts := 0
	ops.wait = func(context.Context) error { return context.DeadlineExceeded }
	_, err := ensureDocker(context.Background(), "", func(Need) (Action, error) {
		prompts++
		if prompts > 1 {
			t.Fatal("timeout must end setup instead of reopening the menu")
		}
		return Start, nil
	}, ops)
	if !errors.Is(err, context.DeadlineExceeded) || !strings.Contains(err.Error(), "5분") || prompts != 1 || f.started != 1 {
		t.Fatal(err, prompts, f.started)
	}
}

func TestDesktopWaitHonorsParentCancellationAtEveryBoundary(t *testing.T) {
	for _, phase := range []string{"prompt", "start", "wait"} {
		t.Run(phase, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			f, ops := waitingDesktopFixture()
			prompts, polls := 0, 0
			ops.start = func(context.Context, string) error {
				f.started++
				if phase == "start" {
					cancel()
					return context.Canceled
				}
				return nil
			}
			ops.wait = func(waitCtx context.Context) error {
				polls++
				cancel()
				return waitCtx.Err()
			}
			_, err := ensureDocker(ctx, "", func(Need) (Action, error) {
				prompts++
				if phase == "prompt" {
					cancel()
				}
				return Start, nil
			}, ops)
			wantStarts, wantPolls := 1, 0
			if phase == "prompt" {
				wantStarts = 0
			}
			if phase == "wait" {
				wantPolls = 1
			}
			if !errors.Is(err, context.Canceled) || prompts != 1 || f.started != wantStarts || polls != wantPolls {
				t.Fatal(err, prompts, f.started, polls)
			}
		})
	}
}

func TestDesktopWaitKeepsSelectedContextWhenGlobalSelectionChanges(t *testing.T) {
	for _, requested := range []string{"", "desktop-linux"} {
		t.Run("requested="+requested, func(t *testing.T) {
			f, ops := waitingDesktopFixture()
			ops.wait = func(context.Context) error {
				f.current, f.offline = "lima-homeserver", false
				return nil
			}
			ready, err := ensureDocker(context.Background(), requested, func(Need) (Action, error) {
				return Start, nil
			}, ops)
			if err != nil || ready.Context != "desktop-linux" || f.current != "lima-homeserver" {
				t.Fatal("wait followed or changed the global Docker selection", ready, err, f.current)
			}
			shows := 0
			for _, args := range f.calls {
				if reflect.DeepEqual(args, []string{"context", "show"}) {
					shows++
				}
				if len(args) >= 2 && args[0] == "context" && args[1] == "use" {
					t.Fatal("wait changed the global context")
				}
			}
			wantShows := 0
			if requested == "" {
				wantShows = 1
			}
			if shows != wantShows {
				t.Fatal("wait reread the global context", shows)
			}
		})
	}
}

func TestFreshDesktopMayTransitionFromAutomaticDefaultOnly(t *testing.T) {
	for _, requested := range []string{"", "default"} {
		t.Run("requested="+requested, func(t *testing.T) {
			f, ops := waitingDesktopFixture()
			f.current = "default"
			ops.run = func(_ context.Context, _ string, args ...string) ([]byte, error) {
				f.calls = append(f.calls, args)
				switch {
				case reflect.DeepEqual(args, []string{"context", "show"}):
					return []byte(f.current), nil
				case reflect.DeepEqual(args, []string{"context", "inspect", "default"}):
					return []byte(`[{"Endpoints":{"docker":{"Host":"unix:///var/run/docker.sock"}}}]`), nil
				case reflect.DeepEqual(args, []string{"context", "inspect", "desktop-linux"}):
					if f.started == 0 {
						return nil, errors.New("not created yet")
					}
					return []byte(`[{"Endpoints":{"docker":{"Host":"unix:///Users/user/.docker/run/docker.sock"}}}]`), nil
				case len(args) >= 3 && args[0] == "--host" && args[1] == f.endpoint:
					if args[2] == "info" {
						return []byte("linux"), nil
					}
					return []byte("2.24.0"), nil
				default:
					return nil, errors.New("default socket is unavailable")
				}
			}
			ops.start = func(context.Context, string) error {
				f.started++
				f.current = "desktop-linux"
				return nil
			}
			ops.wait = func(context.Context) error { return context.DeadlineExceeded }
			ready, err := ensureDocker(context.Background(), requested, func(need Need) (Action, error) {
				if !need.CanStart {
					t.Fatal("fresh Desktop start was not available", need)
				}
				return Start, nil
			}, ops)
			if requested == "" {
				if err != nil || ready.Context != "desktop-linux" || ready.Endpoint != f.endpoint {
					t.Fatal("fresh Desktop's supported context transition failed", ready, err)
				}
			} else {
				if !errors.Is(err, context.DeadlineExceeded) {
					t.Fatal("an explicit default selection was replaced", ready, err)
				}
				for _, args := range f.calls {
					if reflect.DeepEqual(args, []string{"context", "inspect", "desktop-linux"}) {
						t.Fatal("an explicit default selection inspected a fallback context")
					}
				}
			}
		})
	}
}

func TestDesktopWaitRejectsEndpointChangesBeforeUsingReadyResult(t *testing.T) {
	f, ops := waitingDesktopFixture()
	ops.wait = func(context.Context) error {
		f.endpoint, f.offline = "unix:///Users/other/.docker/run/docker.sock", false
		return nil
	}
	ready, err := ensureDocker(context.Background(), "", func(Need) (Action, error) { return Start, nil }, ops)
	if err == nil || ready.Endpoint != "" || !strings.Contains(err.Error(), "연결 주소가 변경") {
		t.Fatal("wait adopted a changed Docker endpoint", ready, err)
	}
}

func TestDesktopWaitRejectsUnrelatedContextAfterMissingCLIBecomesAvailable(t *testing.T) {
	f, ops := waitingDesktopFixture()
	f.missing = true
	ops.start = func(context.Context, string) error {
		f.started++
		f.missing, f.offline = false, false
		f.current, f.endpoint = "lima-homeserver", "unix:///lima/docker.sock"
		return nil
	}
	ready, err := ensureDocker(context.Background(), "", func(Need) (Action, error) { return Start, nil }, ops)
	if err == nil || ready.Context != "" || f.current != "lima-homeserver" {
		t.Fatal("Desktop wait adopted or changed an unrelated environment", ready, err)
	}
}
