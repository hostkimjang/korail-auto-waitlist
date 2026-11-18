package install

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func probeTestImages() map[string]string {
	return map[string]string{
		"api":      "ghcr.io/example/api@sha256:" + strings.Repeat("a", 64),
		"log-init": "alpine:3.20@sha256:" + strings.Repeat("d", 64),
	}
}

func commandIndex(commands []Command, fragment string) int {
	for n, c := range commands {
		if strings.Contains(strings.Join(c.Args, " "), fragment) {
			return n
		}
	}
	return -1
}

func TestInstallProvesFileSharingBeforePullingImages(t *testing.T) {
	root := filepath.Join(t.TempDir(), "installation")
	// Stop at the migration so the ordering check does not depend on the fake
	// status model of the one-shot log-init service.
	runner := &fakeRunner{failure: "-T migration"}
	installer := testInstaller(runner, &fakeReleases{images: probeTestImages()})
	if _, err := installer.Install(context.Background(), Options{Path: root, Credentials: func() (Credentials, error) {
		return Credentials{Username: "admin", Password: "never-print-this-value"}, nil
	}}); err == nil || !strings.Contains(err.Error(), "injected failure") {
		t.Fatalf("expected the injected migration stop: %v", err)
	}
	probe, pull := commandIndex(runner.commands, "/probe/host"), commandIndex(runner.commands, " pull")
	if probe < 0 || pull < 0 || probe > pull {
		t.Fatalf("file sharing probe must run before the image pull: probe=%d pull=%d", probe, pull)
	}
	args := strings.Join(runner.commands[probe].Args, " ")
	if !strings.Contains(args, "--network none") || !strings.Contains(args, "alpine:3.20@sha256:") {
		t.Fatalf("probe must use the pinned small image without network: %s", args)
	}
	leftovers, _ := filepath.Glob(filepath.Join(root, ".railwait", "share-probe-*"))
	if len(leftovers) != 0 {
		t.Fatal("probe folder left behind")
	}
}

func TestInstallStopsBeforeDownloadsWhenDockerCannotSeeTheFolder(t *testing.T) {
	root := filepath.Join(t.TempDir(), "installation")
	runner := &fakeRunner{shareBroken: true}
	installer := testInstaller(runner, &fakeReleases{images: probeTestImages()})
	_, err := installer.Install(context.Background(), Options{Path: root, Credentials: func() (Credentials, error) {
		return Credentials{Username: "admin", Password: "never-print-this-value"}, nil
	}})
	if err == nil || !strings.Contains(err.Error(), "--path") || !strings.Contains(err.Error(), root) {
		t.Fatalf("unshared folder must stop with guidance: %v", err)
	}
	if commandIndex(runner.commands, " pull") >= 0 {
		t.Fatal("images were pulled into an unshared folder")
	}
	leftovers, _ := filepath.Glob(filepath.Join(root, ".railwait", "share-probe-*"))
	if len(leftovers) != 0 {
		t.Fatal("probe folder left behind after failure")
	}
	if s, readErr := ReadState(root); readErr != nil || s.Stage != "configured" {
		t.Fatalf("a rerun must resume from the configured stage: %+v %v", s, readErr)
	}
	if _, statErr := os.Stat(filepath.Join(root, ".env")); statErr != nil {
		t.Fatal("generated settings must be preserved for the rerun")
	}
}

func TestFirstAvailablePortSkipsBusyPortsWithinRange(t *testing.T) {
	busy := map[int]bool{8080: true, 8081: true}
	port, err := FirstAvailablePort(8080, 3, func(p int) error {
		if busy[p] {
			return os.ErrExist
		}
		return nil
	})
	if err != nil || port != 8082 {
		t.Fatalf("got %d %v", port, err)
	}
	if _, err = FirstAvailablePort(8080, 2, func(int) error { return os.ErrExist }); err == nil || !strings.Contains(err.Error(), "8080~8081") {
		t.Fatalf("exhausted range must explain the scanned ports: %v", err)
	}
}
