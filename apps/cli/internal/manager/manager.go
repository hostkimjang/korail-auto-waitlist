package manager

import (
	"context"
	"crypto/subtle"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

type Options struct {
	PollInterval    time.Duration
	CatalogInterval time.Duration
	RefreshCatalog  func(context.Context) (Catalog, error)
	// PlanSettings and SettingsSnapshot are set together by a manager that supports the
	// web settings window. Without them settings requests stay where the API wrote them.
	PlanSettings     SettingsPlanner
	SettingsSnapshot func() (SettingsSnapshot, error)
}

func Run(ctx context.Context, installation string, handler Handler) error {
	return RunWithOptions(ctx, installation, handler, Options{})
}

// RunWithOptions executes one request at a time. Actual installation locking and
// release verification belong to the Handler shared with the local CLI.
func RunWithOptions(ctx context.Context, installation string, handler Handler, options Options) error {
	if handler == nil {
		return errors.New("manager handler is required")
	}
	p, err := Prepare(installation)
	if err != nil {
		return err
	}
	lock, err := platform.AcquireLock(filepath.Join(p.Root, "serve.lock"))
	if err != nil {
		return err
	}
	defer lock.Close()
	token, err := readToken(p)
	if err != nil {
		return err
	}
	if err := recoverInterrupted(p); err != nil {
		return err
	}
	if options.PollInterval <= 0 {
		options.PollInterval = time.Second
	}
	if options.CatalogInterval <= 0 {
		options.CatalogInterval = 5 * time.Minute
	}
	started := time.Now().UTC().Format(time.RFC3339Nano)
	beat := func(state string) error {
		return WriteStatusFile(filepath.Join(p.Status, "manager.json"), Heartbeat{1, state, os.Getpid(), started, time.Now().UTC().Format(time.RFC3339Nano)})
	}
	if err := beat("ready"); err != nil {
		return err
	}
	beatCtx, stopBeat := context.WithCancel(context.Background())
	var beatWG sync.WaitGroup
	beatWG.Add(1)
	go func() {
		defer beatWG.Done()
		ticker := time.NewTicker(2 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-beatCtx.Done():
				return
			case <-ticker.C:
				_ = beat("ready")
			}
		}
	}()
	defer func() { stopBeat(); beatWG.Wait(); _ = beat("stopped") }()
	ticker := time.NewTicker(options.PollInterval)
	defer ticker.Stop()
	lastCatalog := time.Time{}
	var catalogFingerprint string
	var settings settingsPublication
	for {
		fingerprint, err := journalFingerprint(p)
		if err != nil {
			return err
		}
		if options.RefreshCatalog != nil && (time.Since(lastCatalog) >= options.CatalogInterval || fingerprint != catalogFingerprint) {
			catalog, err := options.RefreshCatalog(ctx)
			if err != nil {
				catalog = Catalog{Schema: 1, CheckedAt: time.Now().UTC().Format(time.RFC3339Nano), Reason: "update_blocked"}
			}
			if err := WriteStatusFile(filepath.Join(p.Status, "catalog.json"), catalog); err != nil {
				return err
			}
			lastCatalog = time.Now()
			catalogFingerprint = fingerprint
		}
		if options.SettingsSnapshot != nil {
			if err := settings.publish(p, started, options.SettingsSnapshot); err != nil {
				return err
			}
		}
		if err := processPending(ctx, p, token, handler, options.PlanSettings); err != nil {
			return err
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}

// settingsPublication rewrites status/settings.json when .env changes, and every few
// minutes otherwise, so the API never shows values older than the file it would change.
type settingsPublication struct {
	revision string
	at       time.Time
}

func (s *settingsPublication) publish(p Paths, started string, snapshot func() (SettingsSnapshot, error)) error {
	path := filepath.Join(p.Status, "settings.json")
	value, err := snapshot()
	if err != nil || !planPattern.MatchString(value.Revision) {
		// Without a current snapshot the API must not offer a change based on an old one.
		s.revision = ""
		if removeErr := os.Remove(path); removeErr != nil && !errors.Is(removeErr, os.ErrNotExist) {
			return removeErr
		}
		return nil
	}
	if value.Revision == s.revision && time.Since(s.at) < 5*time.Minute {
		return nil
	}
	value.Schema = 1
	value.ManagerStartedAt = started
	value.GeneratedAt = time.Now().UTC().Format(time.RFC3339Nano)
	if err := WriteStatusFile(path, value); err != nil {
		return err
	}
	s.revision, s.at = value.Revision, time.Now()
	return nil
}

// Any accepted/completed job invalidates the catalog before the next poll, so a
// successful update is never advertised as available for another five minutes.
func journalFingerprint(p Paths) (string, error) {
	entries, err := os.ReadDir(p.Jobs)
	if err != nil {
		return "", err
	}
	var result strings.Builder
	for _, entry := range entries {
		if !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			return "", err
		}
		result.WriteString(entry.Name())
		result.WriteString(info.ModTime().UTC().Format(time.RFC3339Nano))
		result.WriteRune(';')
	}
	return result.String(), nil
}

func recoverInterrupted(p Paths) error {
	entries, err := os.ReadDir(p.Jobs)
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		job, err := readJob(filepath.Join(p.Jobs, entry.Name()))
		if err != nil {
			return err
		}
		if job.State == "running" {
			job.State = "recovery_required"
			job.Stage = "interrupted"
			job.ErrorCode = "E_MANAGER_RESTARTED"
			job.UpdatedAt = time.Now().UTC().Format(time.RFC3339Nano)
			if err := writeJob(p, job); err != nil {
				return err
			}
		}
	}
	return nil
}

func processPending(ctx context.Context, p Paths, token string, handler Handler, planSettings SettingsPlanner) error {
	entries, err := os.ReadDir(p.Requests)
	if err != nil {
		return err
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Name() < entries[j].Name() })
	processed := 0
	for _, entry := range entries {
		if processed >= 64 {
			return nil
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		name := entry.Name()
		if id, ok := strings.CutSuffix(name, ".settings.json"); ok {
			if planSettings == nil || !idPattern.MatchString(id) {
				continue
			}
			processed++
			if err := processSettings(ctx, p, token, handler, planSettings, id); err != nil {
				return err
			}
			continue
		}
		if !strings.HasSuffix(name, ".json") {
			continue
		}
		id := strings.TrimSuffix(name, ".json")
		if !idPattern.MatchString(id) {
			continue
		}
		processed++
		path := filepath.Join(p.Requests, name)
		if _, err := os.Lstat(filepath.Join(p.Jobs, name)); err == nil {
			if err := os.Remove(path); err != nil {
				return err
			}
			continue
		} else if !errors.Is(err, os.ErrNotExist) {
			return err
		}
		data, readErr := readBounded(path, 8192)
		var request Request
		if readErr != nil || decode(data, &request) != nil || !validRequest(request) || request.ID != id {
			// Do not copy invalid or unauthorized fields into public status.
			if err := os.Remove(path); err != nil {
				return err
			}
			continue
		}
		job := Job{Schema: 1, ID: id, TargetReleaseID: request.TargetReleaseID, PlanID: request.PlanID, IdempotencyKey: request.IdempotencyKey, State: "running", Stage: "accepted", CreatedAt: time.Now().UTC().Format(time.RFC3339Nano)}
		job.UpdatedAt = job.CreatedAt
		if subtle.ConstantTimeCompare([]byte(request.AuthToken), []byte(token)) != 1 {
			job.State = "rejected"
			job.Stage = "rejected"
			job.ErrorCode = "E_UNAUTHORIZED"
			if err := writeJob(p, job); err != nil {
				return err
			}
			if err := os.Remove(path); err != nil {
				return err
			}
			continue
		}
		previous, found, err := findIdempotent(p, request.IdempotencyKey)
		if err != nil {
			return err
		}
		if found {
			job.State = previous.State
			job.Stage = previous.Stage
			job.CurrentVersion = previous.CurrentVersion
			job.TargetVersion = previous.TargetVersion
			job.DuplicateOf = previous.ID
			job.ErrorCode = previous.ErrorCode
			if previous.TargetReleaseID != request.TargetReleaseID || previous.PlanID != request.PlanID {
				job.State = "rejected"
				job.Stage = "rejected"
				job.ErrorCode = "E_IDEMPOTENCY_CONFLICT"
			}
			if err := writeJob(p, job); err != nil {
				return err
			}
			if err := os.Remove(path); err != nil {
				return err
			}
			continue
		}
		if err := writeJob(p, job); err != nil {
			return err
		}
		if err := os.Remove(path); err != nil {
			return err
		}
		request.AuthToken = ""
		if err := runJob(ctx, p, job, request, handler); err != nil {
			return err
		}
	}
	return nil
}

// runJob runs an accepted request and records the handler's terminal state.
func runJob(ctx context.Context, p Paths, job Job, request Request, handler Handler) error {
	result, handlerErr := handler(ctx, request, func(stage string) error {
		if !stagePattern.MatchString(stage) {
			return errors.New("invalid manager stage")
		}
		job.Stage = stage
		job.UpdatedAt = time.Now().UTC().Format(time.RFC3339Nano)
		return writeJob(p, job)
	})
	if !terminal(result.State) {
		result.State = "recovery_required"
	}
	job.State = result.State
	if stagePattern.MatchString(result.Stage) {
		job.Stage = result.Stage
	}
	if versionPattern.MatchString(result.CurrentVersion) {
		job.CurrentVersion = result.CurrentVersion
	}
	if versionPattern.MatchString(result.TargetVersion) {
		job.TargetVersion = result.TargetVersion
	}
	if handlerErr != nil {
		job.ErrorCode = "E_HANDLER_FAILED"
		if job.State == "succeeded" {
			job.State = "failed"
		}
	}
	job.UpdatedAt = time.Now().UTC().Format(time.RFC3339Nano)
	return writeJob(p, job)
}

// noPlan fills the plan ID of a settings job refused before a plan existed.
var noPlan = strings.Repeat("0", 64)

var settingsErrorCodes = map[string]bool{"E_SETTINGS_CHANGED": true, "E_SETTINGS_INVALID": true, "E_SETTINGS_FORBIDDEN": true, "E_SETTINGS_UNCHANGED": true}

// processSettings plans a settings request and then runs the plan as an ordinary job,
// so a settings change has the same journal, progress and recovery as an update.
func processSettings(ctx context.Context, p Paths, token string, handler Handler, planSettings SettingsPlanner, id string) error {
	path := filepath.Join(p.Requests, id+".settings.json")
	if _, err := os.Lstat(filepath.Join(p.Jobs, id+".json")); err == nil {
		return os.Remove(path)
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	data, readErr := readBounded(path, 160<<10)
	var request SettingsRequest
	if readErr != nil || decode(data, &request) != nil || !validSettingsRequest(request) || request.ID != id {
		return os.Remove(path)
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	job := Job{Schema: 1, ID: id, TargetReleaseID: "0.0.0", PlanID: noPlan, IdempotencyKey: request.IdempotencyKey, State: "running", Stage: "accepted", CreatedAt: now, UpdatedAt: now}
	reject := func(code string) error {
		job.State, job.Stage, job.ErrorCode = "rejected", "rejected", code
		if err := writeJob(p, job); err != nil {
			return err
		}
		return os.Remove(path)
	}
	if subtle.ConstantTimeCompare([]byte(request.AuthToken), []byte(token)) != 1 {
		return reject("E_UNAUTHORIZED")
	}
	previous, found, err := findIdempotent(p, request.IdempotencyKey)
	if err != nil {
		return err
	}
	if found {
		job.State, job.Stage, job.ErrorCode = previous.State, previous.Stage, previous.ErrorCode
		job.TargetReleaseID, job.PlanID, job.DuplicateOf = previous.TargetReleaseID, previous.PlanID, previous.ID
		job.CurrentVersion, job.TargetVersion = previous.CurrentVersion, previous.TargetVersion
		if err := writeJob(p, job); err != nil {
			return err
		}
		return os.Remove(path)
	}
	if err := writeJob(p, job); err != nil {
		return err
	}
	// The request may carry a new credential, so it leaves the shared folder before planning.
	if err := os.Remove(path); err != nil {
		return err
	}
	request.AuthToken = ""
	planned, planErr := planSettings(ctx, request)
	request.Fields, request.Raw = nil, nil
	if versionPattern.MatchString(planned.TargetReleaseID) {
		job.TargetReleaseID = planned.TargetReleaseID
	}
	var rejected RejectedError
	if errors.As(planErr, &rejected) && settingsErrorCodes[rejected.Code] {
		job.State, job.Stage, job.ErrorCode = "rejected", "rejected", rejected.Code
		job.UpdatedAt = time.Now().UTC().Format(time.RFC3339Nano)
		return writeJob(p, job)
	}
	if planErr != nil || !planPattern.MatchString(planned.PlanID) || !versionPattern.MatchString(planned.TargetReleaseID) {
		job.State, job.Stage, job.ErrorCode = "failed", "checking", "E_HANDLER_FAILED"
		job.UpdatedAt = time.Now().UTC().Format(time.RFC3339Nano)
		return writeJob(p, job)
	}
	job.PlanID = planned.PlanID
	job.Stage = "checking"
	job.UpdatedAt = time.Now().UTC().Format(time.RFC3339Nano)
	if err := writeJob(p, job); err != nil {
		return err
	}
	return runJob(ctx, p, job, Request{Schema: 1, ID: id, TargetReleaseID: planned.TargetReleaseID, PlanID: planned.PlanID, IdempotencyKey: request.IdempotencyKey}, handler)
}

var envKeyPattern = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]{0,127}$`)

func validSettingsRequest(r SettingsRequest) bool {
	if r.Schema != 1 || !idPattern.MatchString(r.ID) || !idPattern.MatchString(r.IdempotencyKey) || !planPattern.MatchString(r.BaseRevision) || len(r.AuthToken) != 43 {
		return false
	}
	if (len(r.Fields) == 0) == (r.Raw == nil) || len(r.Fields) > 32 {
		return false
	}
	if r.Raw != nil && (len(*r.Raw) > 64<<10 || strings.ContainsRune(*r.Raw, 0)) {
		return false
	}
	for key, value := range r.Fields {
		if !envKeyPattern.MatchString(key) || len(value) > 1024 {
			return false
		}
	}
	return true
}

func validRequest(r Request) bool {
	return r.Schema == 1 && idPattern.MatchString(r.ID) && versionPattern.MatchString(r.TargetReleaseID) && planPattern.MatchString(r.PlanID) && idPattern.MatchString(r.IdempotencyKey) && len(r.AuthToken) == 43
}
func terminal(state string) bool {
	return state == "succeeded" || state == "failed" || state == "deferred" || state == "recovery_required"
}
func writeJob(p Paths, job Job) error {
	return WriteStatusFile(filepath.Join(p.Jobs, job.ID+".json"), job)
}
func findIdempotent(p Paths, key string) (Job, bool, error) {
	entries, err := os.ReadDir(p.Jobs)
	if err != nil {
		return Job{}, false, err
	}
	for _, entry := range entries {
		if !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		job, err := readJob(filepath.Join(p.Jobs, entry.Name()))
		if err != nil {
			return Job{}, false, err
		}
		if job.IdempotencyKey == key && job.DuplicateOf == "" {
			return job, true, nil
		}
	}
	return Job{}, false, nil
}

// Submit authenticates a local CLI request using the host-only token file. The
// container API writes the same wire contract after its admin/CSRF checks.
func Submit(installation string, request Request) error {
	p := InstallationPaths(installation)
	token, err := readToken(p)
	if err != nil {
		return err
	}
	request.AuthToken = token
	if !validRequest(request) {
		return errors.New("invalid manager request")
	}
	path := filepath.Join(p.Requests, request.ID+".json")
	if _, err := os.Lstat(path); err == nil {
		return errors.New("request ID already exists")
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return WriteStatusFile(path, request)
}
