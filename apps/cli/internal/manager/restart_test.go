package manager

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"testing"
	"time"
)

func restartRequest(t *testing.T, p Paths, id, key string, mutate func(*RestartRequest)) RestartRequest {
	t.Helper()
	token, err := readToken(p)
	if err != nil {
		t.Fatal(err)
	}
	request := RestartRequest{Schema: 1, ID: strings.Repeat(id, 32), IdempotencyKey: strings.Repeat(key, 32), AuthToken: token, Scope: "app"}
	if mutate != nil {
		mutate(&request)
	}
	if err := WriteStatusFile(filepath.Join(p.Requests, request.ID+".restart.json"), request); err != nil {
		t.Fatal(err)
	}
	return request
}

func plannedRestart(r RestartRequest) Request {
	return Request{Schema: 1, ID: r.ID, TargetReleaseID: "1.4.10", PlanID: strings.Repeat("b", 64), IdempotencyKey: r.IdempotencyKey}
}

func TestRestartRequestIsPlannedThenRunAsAnOrdinaryJob(t *testing.T) {
	root, p := fixture(t)
	token, _ := readToken(p)
	request := restartRequest(t, p, "1", "a", nil)
	var planned RestartRequest
	var handled Request
	planner := func(_ context.Context, r RestartRequest) (Request, error) {
		planned = r
		if _, err := os.Lstat(filepath.Join(p.Requests, r.ID+".restart.json")); !errors.Is(err, os.ErrNotExist) {
			t.Error("authenticated request stayed in the shared folder while planning")
		}
		return plannedRestart(r), nil
	}
	handler := func(_ context.Context, r Request, progress Progress) (Result, error) {
		handled = r
		for _, stage := range []string{"draining", "applying", "verifying", "resuming"} {
			if err := progress(stage); err != nil {
				return Result{}, err
			}
		}
		return Result{State: "succeeded", Stage: "succeeded", CurrentVersion: "1.4.10", TargetVersion: "1.4.10"}, nil
	}
	if err := processPending(context.Background(), p, token, handler, planners{restart: planner}); err != nil {
		t.Fatal(err)
	}
	if planned.AuthToken != "" || planned.Scope != "app" || planned.ID != request.ID || planned.IdempotencyKey != request.IdempotencyKey {
		t.Fatalf("planner input: %+v", planned)
	}
	if handled.ID != request.ID || handled.PlanID != strings.Repeat("b", 64) || handled.TargetReleaseID != "1.4.10" || handled.AuthToken != "" {
		t.Fatalf("handler input: %+v", handled)
	}
	job, err := ReadJob(root, request.ID)
	if err != nil {
		t.Fatal(err)
	}
	if job.State != "succeeded" || job.Stage != "succeeded" || job.PlanID != strings.Repeat("b", 64) || job.TargetReleaseID != "1.4.10" || job.ErrorCode != "" {
		t.Fatalf("job: %+v", job)
	}
	data, _ := os.ReadFile(filepath.Join(p.Jobs, request.ID+".json"))
	if strings.Contains(string(data), token) {
		t.Fatal("request credential entered the journal")
	}
	if entries, _ := os.ReadDir(p.Requests); len(entries) != 0 {
		t.Fatal("restart request retained")
	}
	// The same idempotency key reports the first job instead of restarting again.
	duplicate := restartRequest(t, p, "2", "a", nil)
	calls := 0
	if err := processPending(context.Background(), p, token, func(context.Context, Request, Progress) (Result, error) { calls++; return Result{}, nil }, planners{restart: planner}); err != nil {
		t.Fatal(err)
	}
	job, err = ReadJob(root, duplicate.ID)
	if err != nil || calls != 0 || job.DuplicateOf != request.ID || job.State != "succeeded" || job.PlanID != strings.Repeat("b", 64) {
		t.Fatalf("duplicate job: %+v calls=%d err=%v", job, calls, err)
	}
}

func TestRefusedRestartRecordsTheReasonWithoutRunning(t *testing.T) {
	for _, code := range []string{"E_RESTART_ENV_INVALID", "E_RESTART_UNAVAILABLE"} {
		t.Run(code, func(t *testing.T) {
			root, p := fixture(t)
			token, _ := readToken(p)
			request := restartRequest(t, p, "1", "a", nil)
			calls := 0
			handler := func(context.Context, Request, Progress) (Result, error) { calls++; return Result{}, nil }
			planner := func(context.Context, RestartRequest) (Request, error) {
				return Request{TargetReleaseID: "1.4.10"}, RejectedError{Code: code}
			}
			if err := processPending(context.Background(), p, token, handler, planners{restart: planner}); err != nil {
				t.Fatal(err)
			}
			job, err := ReadJob(root, request.ID)
			if err != nil {
				t.Fatal(err)
			}
			if calls != 0 || job.State != "rejected" || job.Stage != "rejected" || job.ErrorCode != code || job.PlanID != noPlan || job.TargetReleaseID != "1.4.10" {
				t.Fatalf("job: %+v calls=%d", job, calls)
			}
		})
	}
	root, p := fixture(t)
	token, _ := readToken(p)
	request := restartRequest(t, p, "3", "c", nil)
	if err := processPending(context.Background(), p, token, func(context.Context, Request, Progress) (Result, error) { return Result{}, nil }, planners{restart: func(context.Context, RestartRequest) (Request, error) {
		return Request{}, errors.New("docker unavailable fixture marker")
	}}); err != nil {
		t.Fatal(err)
	}
	job, err := ReadJob(root, request.ID)
	data, _ := os.ReadFile(filepath.Join(p.Jobs, request.ID+".json"))
	if err != nil || job.State != "failed" || job.ErrorCode != "E_HANDLER_FAILED" || strings.Contains(string(data), "fixture marker") {
		t.Fatalf("job: %+v err=%v", job, err)
	}
}

func TestUntrustedOrMalformedRestartRequestsNeverReachThePlanner(t *testing.T) {
	root, p := fixture(t)
	token, _ := readToken(p)
	calls := 0
	planner := func(context.Context, RestartRequest) (Request, error) { calls++; return Request{}, nil }
	handler := func(context.Context, Request, Progress) (Result, error) { calls++; return Result{}, nil }
	untrusted := restartRequest(t, p, "1", "a", func(r *RestartRequest) { r.AuthToken = strings.Repeat("x", 43) })
	restartRequest(t, p, "2", "b", func(r *RestartRequest) { r.Scope = "all" })
	restartRequest(t, p, "3", "c", func(r *RestartRequest) { r.Schema = 2 })
	restartRequest(t, p, "4", "d", func(r *RestartRequest) { r.IdempotencyKey = "short" })
	extra, _ := json.Marshal(map[string]any{"schema": 1, "id": strings.Repeat("5", 32), "idempotency_key": strings.Repeat("e", 32), "auth_token": token, "scope": "app", "command": "docker compose down"})
	files := map[string][]byte{
		strings.Repeat("5", 32) + ".restart.json": extra,
		strings.Repeat("6", 32) + ".restart.json": []byte(strings.Repeat("x", 9<<10)),
		strings.Repeat("7", 32) + ".restart.json": []byte(`{"schema":1,"id":"` + strings.Repeat("8", 32) + `","idempotency_key":"` + strings.Repeat("f", 32) + `","auth_token":"` + token + `","scope":"app"}`),
	}
	for name, data := range files {
		if err := os.WriteFile(filepath.Join(p.Requests, name), data, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	if err := processPending(context.Background(), p, token, handler, planners{restart: planner}); err != nil {
		t.Fatal(err)
	}
	if calls != 0 {
		t.Fatal("untrusted or malformed restart request planned")
	}
	job, err := ReadJob(root, untrusted.ID)
	if err != nil || job.State != "rejected" || job.ErrorCode != "E_UNAUTHORIZED" {
		t.Fatalf("untrusted job: %+v %v", job, err)
	}
	if entries, _ := os.ReadDir(p.Requests); len(entries) != 0 {
		t.Fatal("malformed restart request retained")
	}
	if entries, _ := os.ReadDir(p.Jobs); len(entries) != 1 {
		t.Fatal("malformed restart request recorded")
	}
}

func TestManagerWithoutRestartSupportLeavesRestartRequests(t *testing.T) {
	_, p := fixture(t)
	token, _ := readToken(p)
	restartRequest(t, p, "1", "a", nil)
	calls := 0
	settings := func(context.Context, SettingsRequest) (Request, error) { calls++; return Request{}, nil }
	if err := processPending(context.Background(), p, token, func(context.Context, Request, Progress) (Result, error) { calls++; return Result{}, nil }, planners{settings: settings}); err != nil {
		t.Fatal(err)
	}
	if entries, _ := os.ReadDir(p.Requests); len(entries) != 1 || calls != 0 {
		t.Fatal("a manager without restart support consumed the request")
	}
}

func TestRunningManagerPublishesCapabilitiesWithItsStartTime(t *testing.T) {
	root, p := fixture(t)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		done <- RunWithOptions(ctx, root, func(context.Context, Request, Progress) (Result, error) { return Result{}, nil }, Options{
			PollInterval: 5 * time.Millisecond,
			PlanSettings: func(context.Context, SettingsRequest) (Request, error) {
				return Request{}, RejectedError{Code: "E_SETTINGS_UNCHANGED"}
			},
			SettingsSnapshot: func() (SettingsSnapshot, error) {
				return SettingsSnapshot{Revision: strings.Repeat("d", 64), Fields: map[string]SettingsField{}}, nil
			},
			PlanRestart: func(context.Context, RestartRequest) (Request, error) {
				return Request{}, RejectedError{Code: "E_RESTART_UNAVAILABLE"}
			},
			Version: "1.4.11",
		})
	}()
	defer func() {
		cancel()
		<-done
	}()
	path := filepath.Join(p.Status, "capabilities.json")
	read := func() (map[string]any, Heartbeat) {
		deadline := time.Now().Add(3 * time.Second)
		for {
			beat, beatErr := ReadHeartbeat(root)
			data, err := os.ReadFile(path)
			if beatErr == nil && err == nil {
				value := map[string]any{}
				if err := json.Unmarshal(data, &value); err != nil {
					t.Fatal(err)
				}
				return value, beat
			}
			if time.Now().After(deadline) {
				t.Fatal("capabilities not published")
			}
			time.Sleep(5 * time.Millisecond)
		}
	}
	value, beat := read()
	keys := []string{}
	for key := range value {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	if strings.Join(keys, ",") != "capabilities,host_os,install_path,manager_started_at,schema,version" {
		t.Fatalf("capabilities fields: %v", keys)
	}
	abs, _ := filepath.Abs(root)
	if value["schema"] != float64(1) || value["manager_started_at"] != beat.StartedAt || value["version"] != "1.4.11" || value["install_path"] != abs || value["host_os"] != runtime.GOOS {
		t.Fatalf("capabilities: %v heartbeat started_at %q", value, beat.StartedAt)
	}
	if list, _ := json.Marshal(value["capabilities"]); string(list) != `["settings","restart"]` {
		t.Fatalf("capabilities list: %s", list)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	again, _ := read()
	if again["manager_started_at"] != beat.StartedAt {
		t.Fatal("removed capabilities were not published again by the same manager")
	}
}
