package manager

import (
	"bytes"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

var idPattern = regexp.MustCompile(`^[a-f0-9]{32}$`)
var planPattern = regexp.MustCompile(`^[a-f0-9]{64}$`)
var versionPattern = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`)
var stagePattern = regexp.MustCompile(`^[a-z][a-z0-9_]{0,63}$`)

func InstallationPaths(installation string) Paths {
	root := filepath.Join(installation, ".railwait", "manager")
	return Paths{root, filepath.Join(root, "auth"), filepath.Join(root, "control"), filepath.Join(root, "gate"), filepath.Join(root, "requests"), filepath.Join(root, "status"), filepath.Join(root, "status", "jobs")}
}

// Prepare exposes only the subdirectories explicitly bind-mounted into containers.
// The host-only ancestor remains private; API requests must use 0644 files so the
// host user can read files created by the container's different Unix UID.
func Prepare(installation string) (Paths, error) {
	abs, err := filepath.Abs(installation)
	if err != nil {
		return Paths{}, err
	}
	info, err := os.Lstat(filepath.Join(abs, ".railwait"))
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return Paths{}, errors.New("installation state directory is required")
	}
	p := InstallationPaths(abs)
	if err := platform.SecureDir(p.Root); err != nil {
		return p, err
	}
	for _, dir := range []string{p.Auth, p.Control, p.Gate, p.Status, p.Jobs} {
		if err := bridgeDir(dir, 0o755); err != nil {
			return p, err
		}
	}
	if err := bridgeDir(p.Requests, 0o733); err != nil {
		return p, err
	}
	tokenPath := filepath.Join(p.Auth, "request-token")
	if _, err := os.Lstat(tokenPath); errors.Is(err, os.ErrNotExist) {
		raw := make([]byte, 32)
		if _, err := rand.Read(raw); err != nil {
			return p, err
		}
		if err := writeFile(tokenPath, []byte(base64.RawURLEncoding.EncodeToString(raw)), 0o644); err != nil {
			return p, err
		}
	} else if err != nil {
		return p, err
	}
	if _, err := readToken(p); err != nil {
		return p, err
	}
	lockPath := filepath.Join(p.Gate, "operation.lock")
	f, err := os.OpenFile(lockPath, os.O_CREATE|os.O_RDWR, 0o644)
	if err != nil {
		return p, err
	}
	if err := f.Close(); err != nil {
		return p, err
	}
	if err := ensureRegular(lockPath); err != nil {
		return p, err
	}
	if err := os.Chmod(lockPath, 0o644); err != nil {
		return p, err
	}
	gate := filepath.Join(p.Control, "maintenance.json")
	if _, err := os.Lstat(gate); errors.Is(err, os.ErrNotExist) {
		raw := make([]byte, 16)
		if _, err := rand.Read(raw); err != nil {
			return p, err
		}
		initial := map[string]any{"schema": 1, "closed": false, "generation": hex.EncodeToString(raw)}
		if err := WriteStatusFile(gate, initial); err != nil {
			return p, err
		}
	} else if err != nil {
		return p, err
	}
	if err := validateGate(gate); err != nil {
		return p, err
	}
	return p, nil
}

func validateGate(path string) error {
	data, err := readBounded(path, 4096)
	if err != nil {
		return err
	}
	var gate struct {
		Schema     int    `json:"schema"`
		Closed     *bool  `json:"closed"`
		Generation string `json:"generation"`
	}
	if err := decode(data, &gate); err != nil {
		return err
	}
	if gate.Schema != 1 || gate.Closed == nil || !regexp.MustCompile(`^[A-Za-z0-9._-]{1,128}$`).MatchString(gate.Generation) {
		return errors.New("invalid maintenance gate")
	}
	return nil
}

func bridgeDir(path string, mode os.FileMode) error {
	if info, err := os.Lstat(path); err == nil {
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return errors.New("invalid manager directory")
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := platform.SecureDir(path); err != nil {
		return err
	}
	return os.Chmod(path, mode)
}

func readToken(p Paths) (string, error) {
	data, err := readBounded(filepath.Join(p.Auth, "request-token"), 128)
	if err != nil {
		return "", err
	}
	token := strings.TrimSpace(string(data))
	raw, err := base64.RawURLEncoding.DecodeString(token)
	if err != nil || len(raw) != 32 {
		return "", errors.New("invalid manager request credential")
	}
	return token, nil
}

func ensureRegular(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return errors.New("manager file must be regular")
	}
	return nil
}

func readBounded(path string, limit int64) ([]byte, error) {
	if err := ensureRegular(path); err != nil {
		return nil, err
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > limit {
		return nil, errors.New("manager file exceeds size limit")
	}
	return data, nil
}

func decode(data []byte, out any) error {
	d := json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	if err := d.Decode(out); err != nil {
		return errors.New("invalid manager JSON")
	}
	if d.Decode(new(any)) != io.EOF {
		return errors.New("trailing manager JSON")
	}
	return nil
}

// WriteStatusFile atomically publishes secret-free manager control/status JSON.
// The caller chooses a fixed path; API-controlled filenames never reach this API.
func WriteStatusFile(path string, value any) error {
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	return writeFile(path, append(data, '\n'), 0o644)
}

func writeFile(path string, data []byte, mode os.FileMode) error {
	if _, err := os.Lstat(path); err == nil {
		if err := ensureRegular(path); err != nil {
			return err
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".railwait-manager-write-*")
	if err != nil {
		return err
	}
	name := f.Name()
	defer os.Remove(name)
	if err = platform.SecureFile(name); err == nil {
		_, err = f.Write(data)
	}
	if err == nil {
		err = f.Chmod(mode)
	}
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	return replaceFile(name, path)
}

func readJob(path string) (Job, error) {
	var job Job
	data, err := readBounded(path, 32<<10)
	if err != nil {
		return job, err
	}
	if err = decode(data, &job); err != nil {
		return job, err
	}
	if job.Schema != 1 || !idPattern.MatchString(job.ID) || !planPattern.MatchString(job.PlanID) || !idPattern.MatchString(job.IdempotencyKey) || !versionPattern.MatchString(job.TargetReleaseID) || !stagePattern.MatchString(job.Stage) {
		return job, errors.New("invalid manager job")
	}
	return job, nil
}

func ReadJob(installation, id string) (Job, error) {
	if !idPattern.MatchString(id) {
		return Job{}, errors.New("invalid job ID")
	}
	return readJob(filepath.Join(InstallationPaths(installation).Jobs, id+".json"))
}

func ReadHeartbeat(installation string) (Heartbeat, error) {
	var status Heartbeat
	data, err := readBounded(filepath.Join(InstallationPaths(installation).Status, "manager.json"), 4096)
	if err != nil {
		return status, err
	}
	if err := decode(data, &status); err != nil {
		return status, err
	}
	if status.Schema != 1 {
		return status, fmt.Errorf("invalid manager heartbeat")
	}
	return status, nil
}

func Available(installation string, now time.Time) bool {
	status, err := ReadHeartbeat(installation)
	if err != nil || status.State != "ready" {
		return false
	}
	at, err := time.Parse(time.RFC3339Nano, status.HeartbeatAt)
	age := now.Sub(at)
	return err == nil && age >= -time.Second && age <= 10*time.Second
}
