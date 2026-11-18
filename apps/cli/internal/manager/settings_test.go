package manager

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func settingsRequest(t *testing.T, p Paths, id, key string, mutate func(*SettingsRequest)) SettingsRequest {
	t.Helper()
	token, err := readToken(p)
	if err != nil {
		t.Fatal(err)
	}
	request := SettingsRequest{Schema: 1, ID: strings.Repeat(id, 32), IdempotencyKey: strings.Repeat(key, 32), AuthToken: token, BaseRevision: strings.Repeat("c", 64), Fields: map[string]string{"TAGO_SERVICE_KEY": "new-credential-marker"}}
	if mutate != nil {
		mutate(&request)
	}
	if err := WriteStatusFile(filepath.Join(p.Requests, request.ID+".settings.json"), request); err != nil {
		t.Fatal(err)
	}
	return request
}

func TestSettingsRequestIsPlannedThenRunAsAnOrdinaryJob(t *testing.T) {
	root, p := fixture(t)
	token, _ := readToken(p)
	request := settingsRequest(t, p, "1", "a", nil)
	var planned SettingsRequest
	var handled Request
	planner := func(_ context.Context, r SettingsRequest) (Request, error) {
		planned = r
		if _, err := os.Lstat(filepath.Join(p.Requests, r.ID+".settings.json")); !errors.Is(err, os.ErrNotExist) {
			t.Error("request carrying a credential stayed in the shared folder while planning")
		}
		return Request{Schema: 1, ID: r.ID, TargetReleaseID: "1.4.8", PlanID: strings.Repeat("b", 64), IdempotencyKey: r.IdempotencyKey}, nil
	}
	handler := func(_ context.Context, r Request, progress Progress) (Result, error) {
		handled = r
		if err := progress("applying"); err != nil {
			return Result{}, err
		}
		return Result{State: "succeeded", Stage: "succeeded", CurrentVersion: "1.4.8", TargetVersion: "1.4.8"}, nil
	}
	if err := processPending(context.Background(), p, token, handler, planner); err != nil {
		t.Fatal(err)
	}
	if planned.AuthToken != "" || planned.Fields["TAGO_SERVICE_KEY"] != "new-credential-marker" || planned.BaseRevision != request.BaseRevision {
		t.Fatalf("planner input: %+v", planned)
	}
	if handled.ID != request.ID || handled.PlanID != strings.Repeat("b", 64) || handled.TargetReleaseID != "1.4.8" || handled.IdempotencyKey != request.IdempotencyKey || handled.AuthToken != "" {
		t.Fatalf("handler input: %+v", handled)
	}
	job, err := ReadJob(root, request.ID)
	if err != nil {
		t.Fatal(err)
	}
	if job.State != "succeeded" || job.PlanID != strings.Repeat("b", 64) || job.TargetReleaseID != "1.4.8" {
		t.Fatalf("job: %+v", job)
	}
	data, _ := os.ReadFile(filepath.Join(p.Jobs, request.ID+".json"))
	if strings.Contains(string(data), "new-credential-marker") || strings.Contains(string(data), token) {
		t.Fatal("credential entered the journal")
	}
	if entries, _ := os.ReadDir(p.Requests); len(entries) != 0 {
		t.Fatal("settings request retained")
	}
}

func TestRefusedSettingsRequestRecordsTheReasonWithoutRunning(t *testing.T) {
	root, p := fixture(t)
	token, _ := readToken(p)
	request := settingsRequest(t, p, "1", "a", nil)
	calls := 0
	handler := func(context.Context, Request, Progress) (Result, error) { calls++; return Result{}, nil }
	planner := func(context.Context, SettingsRequest) (Request, error) {
		return Request{TargetReleaseID: "1.4.8"}, RejectedError{Code: "E_SETTINGS_CHANGED"}
	}
	if err := processPending(context.Background(), p, token, handler, planner); err != nil {
		t.Fatal(err)
	}
	job, err := ReadJob(root, request.ID)
	if err != nil {
		t.Fatal(err)
	}
	if calls != 0 || job.State != "rejected" || job.Stage != "rejected" || job.ErrorCode != "E_SETTINGS_CHANGED" || job.PlanID != noPlan || job.TargetReleaseID != "1.4.8" {
		t.Fatalf("job: %+v calls=%d", job, calls)
	}
	failed := settingsRequest(t, p, "2", "b", nil)
	if err := processPending(context.Background(), p, token, handler, func(context.Context, SettingsRequest) (Request, error) {
		return Request{}, errors.New("compose unavailable fixture marker")
	}); err != nil {
		t.Fatal(err)
	}
	job, err = ReadJob(root, failed.ID)
	if err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(filepath.Join(p.Jobs, failed.ID+".json"))
	if calls != 0 || job.State != "failed" || job.ErrorCode != "E_HANDLER_FAILED" || job.TargetReleaseID != "0.0.0" || strings.Contains(string(data), "fixture marker") {
		t.Fatalf("job: %+v", job)
	}
}

func TestUntrustedOrMalformedSettingsRequestsNeverReachThePlanner(t *testing.T) {
	root, p := fixture(t)
	token, _ := readToken(p)
	calls := 0
	planner := func(context.Context, SettingsRequest) (Request, error) { calls++; return Request{}, nil }
	handler := func(context.Context, Request, Progress) (Result, error) { calls++; return Result{}, nil }
	untrusted := settingsRequest(t, p, "1", "a", func(r *SettingsRequest) { r.AuthToken = strings.Repeat("x", 43) })
	raw := "TZ=Asia/Seoul\n"
	settingsRequest(t, p, "2", "b", func(r *SettingsRequest) { r.Raw = &raw })
	settingsRequest(t, p, "3", "c", func(r *SettingsRequest) { r.Fields = nil })
	settingsRequest(t, p, "4", "d", func(r *SettingsRequest) { r.BaseRevision = "short" })
	if err := os.WriteFile(filepath.Join(p.Requests, strings.Repeat("5", 32)+".settings.json"), []byte(strings.Repeat("x", 170<<10)), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := processPending(context.Background(), p, token, handler, planner); err != nil {
		t.Fatal(err)
	}
	if calls != 0 {
		t.Fatal("untrusted or malformed request planned")
	}
	job, err := ReadJob(root, untrusted.ID)
	if err != nil || job.State != "rejected" || job.ErrorCode != "E_UNAUTHORIZED" {
		t.Fatalf("untrusted job: %+v %v", job, err)
	}
	if entries, _ := os.ReadDir(p.Requests); len(entries) != 0 {
		t.Fatal("malformed settings request retained")
	}
	if entries, _ := os.ReadDir(p.Jobs); len(entries) != 1 {
		t.Fatal("malformed settings request recorded")
	}
}

func TestManagerWithoutSettingsSupportLeavesSettingsRequests(t *testing.T) {
	_, p := fixture(t)
	token, _ := readToken(p)
	settingsRequest(t, p, "1", "a", nil)
	if err := processPending(context.Background(), p, token, func(context.Context, Request, Progress) (Result, error) { return Result{}, nil }, nil); err != nil {
		t.Fatal(err)
	}
	if entries, _ := os.ReadDir(p.Requests); len(entries) != 1 {
		t.Fatal("a manager without settings support consumed the request")
	}
}

func TestSettingsSnapshotFollowsTheEnvironmentAndTheRunningManager(t *testing.T) {
	root, p := fixture(t)
	revision := strings.Repeat("a", 64)
	failing := false
	snapshot := func() (SettingsSnapshot, error) {
		if failing {
			return SettingsSnapshot{}, errors.New("unreadable fixture")
		}
		value := "false"
		return SettingsSnapshot{Revision: revision, Fields: map[string]SettingsField{"SRT_RESERVATION_ONCE_ENABLED": {Value: &value, Configured: true}, "TAGO_SERVICE_KEY": {Configured: true}}, Raw: "TZ=Asia/Seoul\n", SecretKeys: []string{}, LockedKeys: []string{}}, nil
	}
	var publication settingsPublication
	started := time.Now().UTC().Format(time.RFC3339Nano)
	if err := publication.publish(p, started, snapshot); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(p.Status, "settings.json")
	read := func() map[string]any {
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		value := map[string]any{}
		if err := json.Unmarshal(data, &value); err != nil {
			t.Fatal(err)
		}
		return value
	}
	first := read()
	if first["schema"] != float64(1) || first["manager_started_at"] != started || first["revision"] != revision {
		t.Fatalf("snapshot: %v", first)
	}
	if tago := first["fields"].(map[string]any)["TAGO_SERVICE_KEY"].(map[string]any); tago["value"] != nil || tago["configured"] != true {
		t.Fatalf("secret field exposed a value: %v", tago)
	}
	if err := publication.publish(p, started, snapshot); err != nil {
		t.Fatal(err)
	}
	if read()["generated_at"] != first["generated_at"] {
		t.Fatal("unchanged settings rewritten on every poll")
	}
	revision = strings.Repeat("b", 64)
	if err := publication.publish(p, started, snapshot); err != nil {
		t.Fatal(err)
	}
	if read()["revision"] != revision {
		t.Fatal("changed .env not republished")
	}
	failing = true
	if err := publication.publish(p, started, snapshot); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("stale snapshot kept after .env became unreadable")
	}
	_ = root
}

func TestRunningManagerPublishesSettingsWithItsStartTime(t *testing.T) {
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
		})
	}()
	defer func() {
		cancel()
		<-done
	}()
	deadline := time.Now().Add(3 * time.Second)
	for {
		beat, beatErr := ReadHeartbeat(root)
		data, err := os.ReadFile(filepath.Join(p.Status, "settings.json"))
		if beatErr == nil && err == nil {
			var snapshot SettingsSnapshot
			if err := json.Unmarshal(data, &snapshot); err != nil {
				t.Fatal(err)
			}
			if snapshot.ManagerStartedAt != beat.StartedAt {
				t.Fatalf("snapshot %q does not match heartbeat %q", snapshot.ManagerStartedAt, beat.StartedAt)
			}
			return
		}
		if time.Now().After(deadline) {
			t.Fatal("settings snapshot not published")
		}
		time.Sleep(5 * time.Millisecond)
	}
}
