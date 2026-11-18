package manager

import (
	"context"
	"crypto/subtle"
	"errors"
	"os"
	"path/filepath"
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
		if err := processPending(ctx, p, token, handler); err != nil {
			return err
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
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

func processPending(ctx context.Context, p Paths, token string, handler Handler) error {
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
		if err := writeJob(p, job); err != nil {
			return err
		}
	}
	return nil
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
