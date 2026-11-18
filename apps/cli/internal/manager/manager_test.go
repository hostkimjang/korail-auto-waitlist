package manager

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

func TestCompletedJobImmediatelyRefreshesCatalog(t *testing.T) {
	root, _ := fixture(t)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	var checks atomic.Int32
	go func() {
		done <- RunWithOptions(ctx, root, func(context.Context, Request, Progress) (Result, error) {
			return Result{State: "succeeded", Stage: "finished"}, nil
		}, Options{PollInterval: 5 * time.Millisecond, CatalogInterval: time.Hour, RefreshCatalog: func(context.Context) (Catalog, error) {
			checks.Add(1)
			return Catalog{Schema: 1, CheckedAt: time.Now().UTC().Format(time.RFC3339Nano), Reason: "up_to_date", CurrentVersion: "1.4.0", LatestVersion: "1.4.0"}, nil
		}})
	}()
	defer func() {
		cancel()
		select {
		case <-done:
		case <-time.After(3 * time.Second):
			t.Error("manager not stopped")
		}
	}()
	wait, stop := context.WithTimeout(context.Background(), 3*time.Second)
	defer stop()
	if err := WaitAvailable(wait, root); err != nil {
		t.Fatal(err)
	}
	if err := Submit(root, request("1", "a")); err != nil {
		t.Fatal(err)
	}
	deadline := time.NewTimer(3 * time.Second)
	defer deadline.Stop()
	poll := time.NewTicker(5 * time.Millisecond)
	defer poll.Stop()
	for checks.Load() < 2 {
		select {
		case <-deadline.C:
			t.Fatal("completed update left catalog stale until hourly refresh")
		case <-poll.C:
		}
	}
}

func fixture(t *testing.T) (string, Paths) {
	t.Helper()
	root := t.TempDir()
	if err := platform.SecureDir(filepath.Join(root, ".railwait")); err != nil {
		t.Fatal(err)
	}
	p, err := Prepare(root)
	if err != nil {
		t.Fatal(err)
	}
	return root, p
}
func request(id, key string) Request {
	return Request{Schema: 1, ID: strings.Repeat(id, 32), TargetReleaseID: "1.4.0", PlanID: strings.Repeat("a", 64), IdempotencyKey: strings.Repeat(key, 32)}
}

func TestPreparePreservesCredentialAndClosedGate(t *testing.T) {
	root, p := fixture(t)
	token, err := readToken(p)
	if err != nil {
		t.Fatal(err)
	}
	if err := WriteStatusFile(filepath.Join(p.Control, "maintenance.json"), map[string]any{"schema": 1, "closed": true, "generation": "active-job"}); err != nil {
		t.Fatal(err)
	}
	if _, err := Prepare(root); err != nil {
		t.Fatal(err)
	}
	again, _ := readToken(p)
	if again != token {
		t.Fatal("token changed on resume")
	}
	data, err := os.ReadFile(filepath.Join(p.Control, "maintenance.json"))
	if err != nil || !strings.Contains(string(data), `"closed":true`) {
		t.Fatal("closed gate reopened")
	}
	if err := os.WriteFile(filepath.Join(p.Control, "maintenance.json"), []byte(`{"schema":1,"closed":false}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Prepare(root); err == nil {
		t.Fatal("malformed existing gate repaired silently")
	}
}

func TestRequestIdempotencyAndSecretFreeJournal(t *testing.T) {
	root, p := fixture(t)
	token, _ := readToken(p)
	calls := 0
	handler := func(_ context.Context, r Request, progress Progress) (Result, error) {
		calls++
		if r.AuthToken != "" {
			t.Fatal("token forwarded to handler")
		}
		if err := progress("downloading"); err != nil {
			return Result{}, err
		}
		return Result{State: "succeeded", Stage: "finished", CurrentVersion: "1.3.0", TargetVersion: "1.4.0"}, nil
	}
	first := request("1", "a")
	if err := Submit(root, first); err != nil {
		t.Fatal(err)
	}
	if err := processPending(context.Background(), p, token, handler, nil); err != nil {
		t.Fatal(err)
	}
	second := request("2", "a")
	if err := Submit(root, second); err != nil {
		t.Fatal(err)
	}
	if err := processPending(context.Background(), p, token, handler, nil); err != nil {
		t.Fatal(err)
	}
	job, err := ReadJob(root, second.ID)
	if err != nil {
		t.Fatal(err)
	}
	if calls != 1 || job.DuplicateOf != first.ID || job.State != "succeeded" {
		t.Fatalf("duplicate ran twice: %+v calls%d", job, calls)
	}
	third := request("3", "a")
	third.TargetReleaseID = "1.5.0"
	if err := Submit(root, third); err != nil {
		t.Fatal(err)
	}
	if err := processPending(context.Background(), p, token, handler, nil); err != nil {
		t.Fatal(err)
	}
	job, err = ReadJob(root, third.ID)
	if err != nil {
		t.Fatal(err)
	}
	if calls != 1 || job.State != "rejected" || job.ErrorCode != "E_IDEMPOTENCY_CONFLICT" {
		t.Fatal("conflicting duplicate accepted")
	}
	entries, _ := os.ReadDir(p.Jobs)
	for _, entry := range entries {
		data, err := os.ReadFile(filepath.Join(p.Jobs, entry.Name()))
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(data), token) || strings.Contains(string(data), "auth_token") {
			t.Fatal("credential entered journal")
		}
	}
}

func TestRejectUntrustedRequestsBeforeHandler(t *testing.T) {
	_, p := fixture(t)
	token, _ := readToken(p)
	calls := 0
	handler := func(context.Context, Request, Progress) (Result, error) { calls++; return Result{}, nil }
	bad := request("1", "a")
	bad.AuthToken = strings.Repeat("x", 43)
	if err := WriteStatusFile(filepath.Join(p.Requests, bad.ID+".json"), bad); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(p.Requests, strings.Repeat("2", 32)+".json"), []byte(strings.Repeat("x", 9000)), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := processPending(context.Background(), p, token, handler, nil); err != nil {
		t.Fatal(err)
	}
	if calls != 0 {
		t.Fatal("invalid request dispatched")
	}
	entries, _ := os.ReadDir(p.Requests)
	if len(entries) != 0 {
		t.Fatal("invalid request retained")
	}
}

func TestInterruptedJobNeverReplayed(t *testing.T) {
	root, p := fixture(t)
	r := request("1", "a")
	job := Job{Schema: 1, ID: r.ID, TargetReleaseID: r.TargetReleaseID, PlanID: r.PlanID, IdempotencyKey: r.IdempotencyKey, State: "running", Stage: "applying"}
	if err := writeJob(p, job); err != nil {
		t.Fatal(err)
	}
	if err := recoverInterrupted(p); err != nil {
		t.Fatal(err)
	}
	actual, err := ReadJob(root, r.ID)
	if err != nil {
		t.Fatal(err)
	}
	if actual.State != "recovery_required" || actual.ErrorCode != "E_MANAGER_RESTARTED" {
		t.Fatalf("interrupted update replayable: %+v", actual)
	}
}

func TestHandlerErrorNeverPublishesRawError(t *testing.T) {
	root, p := fixture(t)
	r := request("1", "a")
	if err := Submit(root, r); err != nil {
		t.Fatal(err)
	}
	token, _ := readToken(p)
	if err := processPending(context.Background(), p, token, func(context.Context, Request, Progress) (Result, error) {
		return Result{State: "recovery_required", Stage: "applying"}, errors.New("secret fixture marker")
	}, nil); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(filepath.Join(p.Jobs, r.ID+".json"))
	if strings.Contains(string(data), "secret fixture") || !strings.Contains(string(data), "E_HANDLER_FAILED") {
		t.Fatal("handler error disclosure")
	}
}

func TestHeartbeatStopsAndDoesNotClaimRegisteredServiceIsLive(t *testing.T) {
	root, _ := fixture(t)
	if Available(root, time.Now()) {
		t.Fatal("unstarted daemon available")
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		done <- RunWithOptions(ctx, root, func(context.Context, Request, Progress) (Result, error) { return Result{}, nil }, Options{PollInterval: 5 * time.Millisecond})
	}()
	wait, stop := context.WithTimeout(context.Background(), 3*time.Second)
	defer stop()
	if err := WaitAvailable(wait, root); err != nil {
		t.Fatal(err)
	}
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatal(err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("daemon did not stop")
	}
	if Available(root, time.Now()) {
		t.Fatal("stopped daemon available")
	}
}
