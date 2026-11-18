package install

import (
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/release"
)

// localUpdateSteps replaces the download stage: the package is copied into the
// installation, verified again and its untagged images are loaded before the gate closes.
// replacing_package then swaps the preserved package while the gate is still open, so a
// refused swap leaves services untouched, and records the previous package path first.
var localUpdateSteps = []string{"checking", "loading_package", "replacing_package", "draining", "backing_up", "applying", "verifying", "resuming"}

const previousLocalPackagePrefix = ".local-package-previous-"

// packageHandleWait bounds how long a package folder swap waits for another process to close
// a file inside the folder. Windows refuses to rename a folder while any file inside it is
// open, even when that process allows deletion, and antivirus scans or the search indexer
// hold such files briefly. A refused swap fails the update before it touches services; a
// refused restore or cleanup leaves the previous package for recovery by hand.
var packageHandleWait = 30 * time.Second

// renamePackageDir moves a package folder. Tests replace it to refuse a chosen move, the way
// Windows refuses one while another process holds a file in the folder.
var renamePackageDir = os.Rename

// PlanLocalUpdate plans an update of a local-package installation from a package and a
// public key that the operator names explicitly on this host. The host manager and the
// web never receive package paths, so they cannot create or apply this plan kind.
func (i *Installer) PlanLocalUpdate(ctx context.Context, path, packagePath, trustKey string) (UpdatePlan, error) {
	s, err := ReadState(path)
	if err != nil {
		return UpdatePlan{}, err
	}
	if s.ReleaseSource != "local_package" {
		return UpdatePlan{}, fmt.Errorf("로컬 패키지로 설치한 경로만 로컬 패키지로 업데이트합니다")
	}
	source, key, err := openSelectedLocalPackage(ctx, packagePath, trustKey, true)
	if err != nil {
		return UpdatePlan{}, err
	}
	r, err := localUpdateRelease(ctx, source, s)
	if err != nil {
		return UpdatePlan{}, err
	}
	plan, err := i.makePlan(ctx, s, r)
	if err != nil {
		return plan, err
	}
	// Applying keeps a verified copy beside the current package until the new record is
	// written, so an allowed plan must also leave room for that copy.
	if spaceErr := checkLocalPackageSpace(s.Path, source.Manifest); spaceErr != nil {
		if plan.Allowed {
			plan.Reason = "update_blocked"
		}
		plan.Allowed = false
		plan.BlockedReasons = append(plan.BlockedReasons, spaceErr.Error())
	}
	plan.Steps = append([]string(nil), localUpdateSteps...)
	return savePlan(s, plan, localPackageIdentity(source.Manifest), "local_package", key)
}

// openSelectedLocalPackage verifies a package with the explicitly selected key and returns
// the raw public key, which the plan records as its candidate digest.
func openSelectedLocalPackage(ctx context.Context, dir, keyFile string, assets bool) (*release.LocalSource, []byte, error) {
	if dir == "" || keyFile == "" {
		return nil, nil, fmt.Errorf("--package와 --trust-key를 함께 지정하세요")
	}
	key, err := localTrustKey(keyFile)
	if err != nil {
		return nil, nil, err
	}
	open := release.OpenLocalMetadata
	if assets {
		open = release.OpenLocal
	}
	source, err := open(ctx, dir, keyFile)
	if err != nil {
		return nil, nil, err
	}
	return source, key, nil
}

func localTrustKey(path string) ([]byte, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Size() > 1024 {
		return nil, fmt.Errorf("로컬 패키지 신뢰 공개키 파일이 잘못되었습니다")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	key, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(data)))
	if err != nil || len(key) != ed25519.PublicKeySize {
		return nil, fmt.Errorf("로컬 패키지 신뢰 공개키가 잘못되었습니다")
	}
	return key, nil
}

// LocalTrustKey returns the canonical base64 form of a local package public key file so an
// operator can compare the key that a plan will trust with the preserved one.
func LocalTrustKey(path string) (string, error) {
	key, err := localTrustKey(path)
	if err != nil {
		return "", err
	}
	return base64.StdEncoding.EncodeToString(key), nil
}

// localUpdateRelease maps a signed local manifest to the release contract used by update
// planning. Local manifests carry no compatible_from: the operator selects the package and
// key explicitly, and planning still requires a newer version with the same database
// revision, settings schema, storage images and service set as the running installation.
func localUpdateRelease(ctx context.Context, source *release.LocalSource, s State) (release.Release, error) {
	r, err := source.Resolve(ctx, "stable")
	if err != nil {
		return release.Release{}, err
	}
	r.CompatibleFrom = []string{s.Release}
	return r, nil
}

// localPackageIdentity covers every signed manifest field: version, bundle and image
// archive digests, image and config IDs, profiles and source provenance.
func localPackageIdentity(m release.LocalManifest) string {
	data, _ := json.Marshal(m)
	return hashBytes(data)
}

func checkLocalPackageSpace(installation string, m release.LocalManifest) error {
	available, err := platform.AvailableBytes(installation)
	if err != nil {
		return err
	}
	required := uint64(m.Bundle.SizeBytes+m.ImageArchive.SizeBytes) + (4 << 30)
	if available < required {
		return fmt.Errorf("로컬 패키지 보존과 설치에 필요한 여유 공간이 부족합니다 (최소 %d GiB)", (required+(1<<30)-1)>>30)
	}
	return nil
}

// replaceLocalPackage moves the preserved package to previous and the staged copy into its
// place. The caller restores previous on failure and removes it only after the new
// installation record is written. Each move waits for files that another process briefly
// holds open on Windows.
func replaceLocalPackage(installation, staged, previous string) error {
	current := filepath.Join(installation, ".railwait", "local-package")
	if _, err := os.Lstat(previous); err == nil {
		return fmt.Errorf("이전 로컬 패키지 보관 경로가 이미 있습니다; 복구 확인이 필요합니다")
	} else if !os.IsNotExist(err) {
		return err
	}
	if err := retryWhileFileHeld(func() error { return renamePackageDir(current, previous) }); err != nil {
		return err
	}
	if err := retryWhileFileHeld(func() error { return renamePackageDir(staged, current) }); err != nil {
		return errors.Join(err, retryWhileFileHeld(func() error { return renamePackageDir(previous, current) }))
	}
	return nil
}

func restoreLocalPackage(installation, previous string) error {
	current := filepath.Join(installation, ".railwait", "local-package")
	if err := retryWhileFileHeld(func() error { return os.RemoveAll(current) }); err != nil {
		return fmt.Errorf("새 로컬 패키지를 되돌리지 못했습니다; 이전 패키지는 %s에 있습니다: %w", previous, err)
	}
	if err := retryWhileFileHeld(func() error { return renamePackageDir(previous, current) }); err != nil {
		return fmt.Errorf("이전 로컬 패키지를 복원하지 못했습니다; %s를 확인하세요: %w", previous, err)
	}
	return nil
}

// retryWhileFileHeld repeats a package folder rename or removal that Windows refused because
// another process had a file open, until it succeeds, fails for another reason or the wait
// runs out. Other platforms never report a held file, so the operation runs once.
func retryWhileFileHeld(op func() error) error {
	deadline := time.Now().Add(packageHandleWait)
	delay := 50 * time.Millisecond
	for {
		err := op()
		if err == nil || !fileHeldByAnotherProcess(err) || time.Now().Add(delay).After(deadline) {
			return err
		}
		time.Sleep(delay)
		delay = min(2*delay, time.Second)
	}
}

// withInterruptedLocalUpdateHint points to the previous package that an interrupted local
// update keeps for manual recovery. The directory suffix is the job ID of that update.
func withInterruptedLocalUpdateHint(installation string, err error) error {
	entries, readErr := os.ReadDir(filepath.Join(installation, ".railwait"))
	if readErr != nil {
		return err
	}
	for _, entry := range entries {
		if job, ok := strings.CutPrefix(entry.Name(), previousLocalPackagePrefix); ok && entry.IsDir() {
			return fmt.Errorf("%w; 중단된 로컬 패키지 업데이트의 이전 패키지가 %s에 있습니다. railwait status --job %s로 작업 기록을 확인한 뒤 복구하세요", err, filepath.Join(installation, ".railwait", entry.Name()), job)
		}
	}
	return err
}
