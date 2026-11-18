package install

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/release"
	"golang.org/x/sys/windows"
)

// holdFile opens a file the way antivirus scanners and the search indexer do. Every sharing
// mode is allowed, yet Windows still refuses to rename the folder that contains the file.
func holdFile(t *testing.T, path string) (release func()) {
	t.Helper()
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		t.Fatal(err)
	}
	share := uint32(windows.FILE_SHARE_READ | windows.FILE_SHARE_WRITE | windows.FILE_SHARE_DELETE)
	handle, err := windows.CreateFile(name, windows.GENERIC_READ, share, nil, windows.OPEN_EXISTING, windows.FILE_ATTRIBUTE_NORMAL, 0)
	if err != nil {
		t.Fatal(err)
	}
	var once sync.Once
	release = func() { once.Do(func() { _ = windows.CloseHandle(handle) }) }
	t.Cleanup(release)
	return release
}

// holdFileWithoutDeleteSharing opens a file the way most programs do, without allowing
// deletion, so Windows refuses to remove the file until the handle closes.
func holdFileWithoutDeleteSharing(t *testing.T, path string) {
	t.Helper()
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		t.Fatal(err)
	}
	share := uint32(windows.FILE_SHARE_READ | windows.FILE_SHARE_WRITE)
	handle, err := windows.CreateFile(name, windows.GENERIC_READ, share, nil, windows.OPEN_EXISTING, windows.FILE_ATTRIBUTE_NORMAL, 0)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = windows.CloseHandle(handle) })
}

func shortPackageHandleWait(t *testing.T) {
	saved := packageHandleWait
	packageHandleWait = 200 * time.Millisecond
	t.Cleanup(func() { packageHandleWait = saved })
}

func packageSwapFixture(t *testing.T) (root, current, staged, previous string) {
	t.Helper()
	root = t.TempDir()
	current = filepath.Join(root, ".railwait", "local-package")
	staged = filepath.Join(root, ".railwait", ".local-package-update-test")
	previous = filepath.Join(root, ".railwait", previousLocalPackagePrefix+"test")
	for dir, version := range map[string]string{current: "old", staged: "new"} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "local-manifest.json"), []byte(version), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	return root, current, staged, previous
}

func packageVersion(dir string) string {
	data, _ := os.ReadFile(filepath.Join(dir, "local-manifest.json"))
	return string(data)
}

func TestLocalPackageSwapWaitsForAShortWindowsFileHandle(t *testing.T) {
	root, current, staged, previous := packageSwapFixture(t)
	release := holdFile(t, filepath.Join(current, "local-manifest.json"))
	time.AfterFunc(300*time.Millisecond, release)
	if err := replaceLocalPackage(root, staged, previous); err != nil {
		t.Fatal(err)
	}
	if packageVersion(current) != "new" || packageVersion(previous) != "old" {
		t.Fatal("package swap is incomplete")
	}
}

func TestLocalPackageSwapKeepsThePreservedPackageWhileAHandleStaysOpen(t *testing.T) {
	shortPackageHandleWait(t)
	for _, held := range []string{"preserved", "staged"} {
		t.Run(held, func(t *testing.T) {
			root, current, staged, previous := packageSwapFixture(t)
			dir := current
			if held == "staged" {
				dir = staged
			}
			holdFile(t, filepath.Join(dir, "local-manifest.json"))
			if err := replaceLocalPackage(root, staged, previous); !errors.Is(err, windows.ERROR_ACCESS_DENIED) {
				t.Fatalf("expected Windows to refuse the folder rename: %v", err)
			}
			if packageVersion(current) != "old" || packageVersion(staged) != "new" {
				t.Fatal("the preserved or staged package moved")
			}
			if _, err := os.Lstat(previous); !os.IsNotExist(err) {
				t.Fatal("previous package path left behind", err)
			}
		})
	}
}

func TestLocalPackageUpdateFailsBeforeTouchingServicesWhenTheSwapIsRefused(t *testing.T) {
	shortPackageHandleWait(t)
	ctx := context.Background()
	i, runner, state := localUpdateFixture(t)
	target := newLocalTestPackage(t, "1.4.1", "rev1", localTestServices("api-1.4.1"))
	runner.next = target.images
	plan, err := i.PlanLocalUpdate(ctx, state.Path, target.dir, target.key)
	if err != nil || !plan.Allowed {
		t.Fatalf("plan=%+v err=%v", plan, err)
	}
	preserved := filepath.Join(state.Path, ".railwait", "local-package")
	result, err := i.Update(ctx, UpdateOptions{Path: state.Path, TargetReleaseID: plan.TargetReleaseID, PlanID: plan.PlanID, PackagePath: target.dir, TrustKey: target.key, Progress: func(stage string) error {
		if stage == "replacing_package" {
			holdFile(t, filepath.Join(preserved, "local-manifest.json"))
		}
		return nil
	}})
	if err == nil || result.State != "failed" || result.PreviousPackage != "" {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	if job, err := ReadUpdateJob(state.Path, result.JobID); err != nil || job.State != "failed" || job.PreviousPackage != "" {
		t.Fatalf("job names a previous package that never moved: %+v %v", job, err)
	}
	// The swap now runs before the gate closes, so a refused swap changes no service.
	assertServicesUntouched(t, runner, state)
}

func TestLocalPackageUpdateNeedsRecoveryWhenADeferredDrainCannotRestoreThePackage(t *testing.T) {
	shortPackageHandleWait(t)
	i, runner, state := localUpdateFixture(t)
	target := newLocalTestPackage(t, "1.4.1", "rev1", localTestServices("api-1.4.1"))
	runner.next, runner.notDrained = target.images, true
	plan, err := i.PlanLocalUpdate(context.Background(), state.Path, target.dir, target.key)
	if err != nil || !plan.Allowed {
		t.Fatalf("plan=%+v err=%v", plan, err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	preserved := filepath.Join(state.Path, ".railwait", "local-package")
	result, err := i.Update(ctx, UpdateOptions{Path: state.Path, TargetReleaseID: plan.TargetReleaseID, PlanID: plan.PlanID, PackagePath: target.dir, TrustKey: target.key, Progress: func(stage string) error {
		if stage == "draining" {
			// The new package is in place by now; a handle without delete sharing blocks its removal.
			holdFileWithoutDeleteSharing(t, filepath.Join(preserved, "local-manifest.json"))
			cancel()
		}
		return nil
	}})
	if err == nil || result.State != "recovery_required" || result.PreviousPackage == "" {
		t.Fatalf("a half-restored package was reported as %q: %+v err=%v", result.State, result, err)
	}
	if job, err := ReadUpdateJob(state.Path, result.JobID); err != nil || job.State != "recovery_required" || job.PreviousPackage != result.PreviousPackage {
		t.Fatalf("job=%+v err=%v", job, err)
	}
	previous, err := release.OpenLocal(context.Background(), result.PreviousPackage, filepath.Join(result.PreviousPackage, "trust-key.pub"))
	if err != nil || previous.Manifest.Version != "1.4.0" {
		t.Fatalf("the previous package is not intact where the job record points: %v", err)
	}
}
