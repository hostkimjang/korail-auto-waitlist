package setup

import (
	"context"
	"errors"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

type macInstallFixture struct {
	calls      [][]string
	fail       string
	present    bool
	authorized func(context.Context)
}

func (f *macInstallFixture) ops() macInstallOperations {
	return macInstallOperations{
		present: func(string) bool { return f.present },
		run: func(ctx context.Context, program string, args ...string) ([]byte, error) {
			f.calls = append(f.calls, append([]string{program}, args...))
			key := filepath.Base(program)
			if key == "hdiutil" {
				key += " " + args[0]
			}
			if f.fail == key {
				return nil, errors.New("test failure")
			}
			if filepath.Base(program) == "pkgutil" {
				return []byte("Status: signed by a certificate trusted by macOS\n  1. Developer ID Installer: Tailscale Inc. (W5364U7YZB)\n  2. Developer ID Certification Authority\n"), nil
			}
			if filepath.Base(program) == "osascript" && f.authorized != nil {
				f.authorized(ctx)
			}
			return nil, nil
		},
	}
}
func (f *macInstallFixture) count(program string) int {
	n := 0
	for _, call := range f.calls {
		if filepath.Base(call[0]) == program {
			n++
		}
	}
	return n
}

func TestMacDockerRejectsUntrustedImageAndPublisherBeforeAuthorization(t *testing.T) {
	for _, stage := range []string{"hdiutil verify", "codesign", "spctl"} {
		t.Run(stage, func(t *testing.T) {
			p, _ := DependencyPlan("docker", "darwin", "arm64")
			f := macInstallFixture{fail: stage}
			result, err := installMacWith(context.Background(), p, filepath.Join(t.TempDir(), "Docker.dmg"), nil, f.ops())
			if err == nil || result.Installed || f.count("osascript") != 0 {
				t.Fatalf("untrusted installer ran: %+v %v %v", result, err, f.calls)
			}
			if stage != "hdiutil verify" && !reflect.DeepEqual(f.calls[len(f.calls)-1][:2], []string{"/usr/bin/hdiutil", "detach"}) {
				t.Fatal("failed verification leaked mount", f.calls)
			}
		})
	}
}

func TestMacDockerVerifiedInstallerDoesNotAcceptLicense(t *testing.T) {
	p, _ := DependencyPlan("docker", "darwin", "arm64")
	f := macInstallFixture{}
	result, err := installMacWith(context.Background(), p, filepath.Join(t.TempDir(), "Docker.dmg"), nil, f.ops())
	if err != nil || !result.Installed || !result.NeedsUserAction || result.RebootRequired {
		t.Fatalf("unexpected result: %+v %v", result, err)
	}
	if f.count("osascript") != 1 || f.count("open") != 1 {
		t.Fatal("native install/application launch missing", f.calls)
	}
	for _, call := range f.calls {
		if strings.Contains(strings.Join(call, " "), "accept-license") {
			t.Fatal("license implicitly accepted")
		}
		if filepath.Base(call[0]) == "codesign" && !strings.Contains(strings.Join(call, " "), dockerMacTeam) {
			t.Fatal("publisher not constrained")
		}
		for index, arg := range call {
			if arg == "-R" && (index+1 >= len(call) || !strings.HasPrefix(call[index+1], "=anchor apple generic")) {
				t.Fatal("codesign requirement would be interpreted as a filename", call)
			}
		}
	}
	last := f.calls[len(f.calls)-1]
	if !reflect.DeepEqual(last[:2], []string{"/usr/bin/hdiutil", "detach"}) {
		t.Fatal("successful install leaked mount", last)
	}
}

func TestMacNativeCancellationWaitsAndThenDetaches(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	p, _ := DependencyPlan("docker", "darwin", "arm64")
	f := macInstallFixture{authorized: func(nativeCtx context.Context) {
		cancel()
		if nativeCtx.Err() != nil {
			t.Fatal("native process would be killed before it can finish")
		}
	}}
	_, err := installMacWith(ctx, p, filepath.Join(t.TempDir(), "Docker.dmg"), nil, f.ops())
	if !errors.Is(err, context.Canceled) {
		t.Fatal("cancellation lost", err)
	}
	if f.count("open") != 0 || !reflect.DeepEqual(f.calls[len(f.calls)-1][:2], []string{"/usr/bin/hdiutil", "detach"}) {
		t.Fatal("cancel continued setup or leaked mount", f.calls)
	}
}

func TestMacDetachFailureRetainsMountedResources(t *testing.T) {
	p, _ := DependencyPlan("docker", "darwin", "arm64")
	f := macInstallFixture{fail: "hdiutil detach"}
	_, err := installMacWith(context.Background(), p, filepath.Join(t.TempDir(), "Docker.dmg"), nil, f.ops())
	var retained *retainedInstallerError
	if !errors.As(err, &retained) {
		t.Fatal("mount cleanup failure not retained", err)
	}
}

func TestMacFailedAttachDoesNotDeleteUnconfirmedMount(t *testing.T) {
	p, _ := DependencyPlan("docker", "darwin", "arm64")
	detachAttempted := false
	ops := macInstallOperations{
		present: func(string) bool { return false },
		run: func(_ context.Context, program string, args ...string) ([]byte, error) {
			if filepath.Base(program) == "hdiutil" && args[0] == "attach" {
				return nil, context.DeadlineExceeded
			}
			if filepath.Base(program) == "hdiutil" && args[0] == "detach" {
				detachAttempted = true
				return nil, errors.New("mount state unknown")
			}
			return nil, nil
		},
	}
	_, err := installMacWith(context.Background(), p, filepath.Join(t.TempDir(), "Docker.dmg"), nil, ops)
	var retained *retainedInstallerError
	if !detachAttempted || !errors.As(err, &retained) {
		t.Fatal("unconfirmed mount was eligible for recursive cleanup", err)
	}
}

func TestMacTailscaleRequiresTrustedInstallerLeafAndGatekeeper(t *testing.T) {
	for _, input := range []string{
		"1. Developer ID Installer: Someone Else (W5364U7YZB)",
		"2. Developer ID Installer: Tailscale Inc. (W5364U7YZB)",
		"1. Developer ID Application: Tailscale Inc. (W5364U7YZB)",
		"1. Developer ID Installer: Tailscale Inc. (AAAAAAAAAA)",
	} {
		if tailscalePackageSigner([]byte(input)) {
			t.Fatal("untrusted leaf accepted", input)
		}
	}
	for _, stage := range []string{"pkgutil", "spctl", ""} {
		t.Run(stage, func(t *testing.T) {
			p, _ := DependencyPlan("tailscale", "darwin", "arm64")
			f := macInstallFixture{fail: stage}
			result, err := installMacWith(context.Background(), p, filepath.Join(t.TempDir(), "Tailscale.pkg"), nil, f.ops())
			if stage != "" {
				if err == nil || result.Installed || f.count("osascript") != 0 {
					t.Fatal("untrusted package installed", result, err)
				}
			} else if err != nil || !result.Installed || !result.NeedsUserAction || f.count("osascript") != 1 {
				t.Fatal("verified native install missing", result, err)
			}
		})
	}
}

func TestMacNativeUserDeclineIsCancellation(t *testing.T) {
	ops := macInstallOperations{run: func(context.Context, string, ...string) ([]byte, error) {
		return []byte("User canceled. (-128)"), errors.New("exit 1")
	}}
	if err := runMacAuthorized(context.Background(), ops, "script", "path"); !errors.Is(err, ErrCancelled) {
		t.Fatal("native decline not recognized", err)
	}
}
