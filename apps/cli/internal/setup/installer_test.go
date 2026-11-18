package setup

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestOfficialDependencyPlans(t *testing.T) {
	standalone, err := DependencyPlan("tailscale", "darwin", "arm64")
	if err != nil || standalone.URL != "https://pkgs.tailscale.com/stable/Tailscale-latest-macos.pkg" {
		t.Fatal("macOS must use the standalone package, not the legacy App Store artifact", standalone, err)
	}
	for _, goos := range []string{"darwin", "windows"} {
		for _, arch := range []string{"amd64", "arm64"} {
			for _, tool := range []string{"docker", "tailscale"} {
				p, err := DependencyPlan(tool, goos, arch)
				if err != nil || !dependencyURLAllowed(tool, p.URL) || p.MaxBytes <= 0 || filepath.Base(p.Filename) != p.Filename {
					t.Fatalf("invalid plan: %+v %v", p, err)
				}
			}
		}
	}
	for _, input := range [][3]string{{"docker", "linux", "amd64"}, {"docker", "windows", "386"}, {"unknown", "darwin", "arm64"}} {
		if _, err := DependencyPlan(input[0], input[1], input[2]); err == nil {
			t.Fatalf("unsupported plan accepted: %v", input)
		}
	}
}

func TestInstallerPreservesExistingAndNewlyAppearedApplications(t *testing.T) {
	p, _ := DependencyPlan("docker", "darwin", "arm64")
	for _, appearsAfterDownload := range []bool{false, true} {
		dir := filepath.Join(t.TempDir(), "installer")
		presentCalls, downloads, installs := 0, 0, 0
		ops := dependencyInstallOperations{
			present:  func(string) bool { presentCalls++; return !appearsAfterDownload || presentCalls > 1 },
			makeTemp: func() (string, error) { return dir, os.Mkdir(dir, 0o700) },
			download: func(context.Context, InstallPlan, string, func(string)) error { downloads++; return nil },
			install: func(context.Context, InstallPlan, string, func(string)) (InstallResult, error) {
				installs++
				return InstallResult{}, nil
			},
		}
		result, err := installDependency(context.Background(), p, nil, ops)
		if err != nil || !result.Installed || installs != 0 {
			t.Fatalf("existing install overwritten: %+v %v calls=%d", result, err, installs)
		}
		if appearsAfterDownload && downloads != 1 {
			t.Fatal("download race was not exercised")
		}
		if _, err := os.Stat(dir); !os.IsNotExist(err) {
			t.Fatal("temporary directory remains", err)
		}
	}
}

func TestInstallerCleansDownloadsOnFailureAndCancellation(t *testing.T) {
	p, _ := DependencyPlan("tailscale", "darwin", "arm64")
	for _, phase := range []string{"download", "verification", "canceled"} {
		t.Run(phase, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			dir := filepath.Join(t.TempDir(), "installer")
			installs := 0
			ops := dependencyInstallOperations{
				present: func(string) bool { return false }, makeTemp: func() (string, error) { return dir, os.Mkdir(dir, 0o700) },
				download: func(_ context.Context, _ InstallPlan, path string, _ func(string)) error {
					if err := os.WriteFile(path, []byte("untrusted"), 0o600); err != nil {
						return err
					}
					if phase == "download" {
						return errors.New("network failed")
					}
					if phase == "canceled" {
						cancel()
					}
					return nil
				},
				install: func(context.Context, InstallPlan, string, func(string)) (InstallResult, error) {
					installs++
					return InstallResult{}, errors.New("untrusted signer")
				},
			}
			if _, err := installDependency(ctx, p, nil, ops); err == nil {
				t.Fatal("failure was hidden")
			}
			if phase != "verification" && installs != 0 {
				t.Fatal("installer started after failed/canceled download")
			}
			if _, err := os.Stat(dir); !os.IsNotExist(err) {
				t.Fatal("temporary resources remain", err)
			}
		})
	}
}

func TestMountedInstallerIsRetainedIfDetachFails(t *testing.T) {
	p, _ := DependencyPlan("docker", "darwin", "arm64")
	dir := filepath.Join(t.TempDir(), "installer")
	ops := dependencyInstallOperations{
		present: func(string) bool { return false }, makeTemp: func() (string, error) { return dir, os.Mkdir(dir, 0o700) },
		download: func(context.Context, InstallPlan, string, func(string)) error { return nil },
		install: func(context.Context, InstallPlan, string, func(string)) (InstallResult, error) {
			return InstallResult{}, &retainedInstallerError{path: dir, cause: errors.New("mounted")}
		},
	}
	_, err := installDependency(context.Background(), p, nil, ops)
	if err == nil || !strings.Contains(err.Error(), dir) {
		t.Fatal("retained artifact location missing", err)
	}
	if _, err := os.Stat(dir); err != nil {
		t.Fatal("mounted resource was deleted", err)
	}
}

func TestNativeApplicationPresenceProtectsIncompleteInstall(t *testing.T) {
	for _, tool := range []string{"docker", "tailscale"} {
		if !nativeDependencyPresent("darwin", tool, func(string) string { return "/Users/person" }, func(path string) bool { return strings.HasPrefix(path, "/Users/person/Applications/") }) {
			t.Fatal("user app overlooked", tool)
		}
	}
}

func TestDockerMissingDoesNotInstallOverChosenVM(t *testing.T) {
	f := &dockerFixture{missing: true}
	ops := f.operations()
	ops.desktop = func() string { return "" }
	ops.canInstall = func(string) bool { return true }
	for _, chosen := range []string{"", "desktop-linux", "lima-homeserver", "colima"} {
		_, need := probeDocker(context.Background(), chosen, ops)
		want := chosen == "" || chosen == "desktop-linux"
		if need == nil || need.CanInstall != want {
			t.Fatalf("context %q install=%+v", chosen, need)
		}
	}
}
