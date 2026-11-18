package install

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/manager"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/release"
)

type localTestPackage struct {
	dir, key string
	images   map[string]string
	private  ed25519.PrivateKey
}

// localTestServices shares one API image between api and migration like the release
// Compose file and keeps the storage images fixed unless a test overrides them.
func localTestServices(api string) map[string]string {
	return map[string]string{"api": api, "migration": api, "log-init": "alpine", "postgres": "postgres", "redis": "redis"}
}

func newLocalTestPackage(t *testing.T, version, revision string, markers map[string]string) localTestPackage {
	t.Helper()
	_, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return signedLocalTestPackage(t, version, revision, markers, private)
}

// signedLocalTestPackage writes a classic untagged image archive in which services with
// the same marker share one image ID, and signs the manifest with the given key.
func signedLocalTestPackage(t *testing.T, version, revision string, markers map[string]string, private ed25519.PrivateKey) localTestPackage {
	t.Helper()
	archive := map[string][]byte{}
	entries := []map[string]any{}
	images := map[string]string{}
	profiles := map[string][]string{}
	var compose strings.Builder
	compose.WriteString("services:\n")
	for _, name := range sortedLocalKeys(markers) {
		config := []byte(`{"architecture":"` + runtime.GOARCH + `","os":"linux","marker":"` + markers[name] + `"}`)
		file := markers[name] + ".json"
		if _, ok := archive[file]; !ok {
			archive[file] = config
			entries = append(entries, map[string]any{"Config": file, "RepoTags": nil, "Layers": []string{}})
		}
		images[name] = "sha256:" + hashBytes(config)
		profiles[name] = []string{}
		fmt.Fprintf(&compose, "  %s:\n    image: %s\n    pull_policy: never\n", name, images[name])
	}
	archive["manifest.json"], _ = json.Marshal(entries)
	imageArchive := installTar(t, archive, false)
	bundle := installTar(t, map[string][]byte{"compose.release.yml": []byte(compose.String()), ".env.example": []byte("COMPOSE_PROFILES=\n")}, true)
	m := release.LocalManifest{SchemaVersion: 1, Kind: "local_package", Version: version, SourceRevision: strings.Repeat("a", 40), SourceTreeSHA256: strings.Repeat("b", 64), Platforms: []string{"linux/" + runtime.GOARCH}, MinimumCLIVersion: "0.1.0", DatabaseRevision: revision, SettingsSchema: 1, Bundle: release.LocalArtifact{File: "railwait-bundle.tar.gz", SHA256: hashBytes(bundle), SizeBytes: int64(len(bundle))}, ImageArchive: release.LocalArtifact{File: "railwait-images.tar", SHA256: hashBytes(imageArchive), SizeBytes: int64(len(imageArchive))}, Images: images, ImageConfigIDs: images, ServiceProfiles: profiles}
	metadata, err := json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	for name, data := range map[string][]byte{"local-manifest.json": metadata, m.Bundle.File: bundle, m.ImageArchive.File: imageArchive} {
		if err = os.WriteFile(filepath.Join(dir, name), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	return localTestPackage{dir: dir, key: signLocalTestPackage(t, dir, private), images: images, private: private}
}

// signLocalTestPackage signs the manifest in dir and returns a new trust key file.
func signLocalTestPackage(t *testing.T, dir string, private ed25519.PrivateKey) string {
	t.Helper()
	metadata, err := os.ReadFile(filepath.Join(dir, "local-manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	signature := base64.StdEncoding.EncodeToString(ed25519.Sign(private, metadata))
	if err = os.WriteFile(filepath.Join(dir, "local-manifest.sig"), []byte(signature), 0600); err != nil {
		t.Fatal(err)
	}
	key := filepath.Join(t.TempDir(), "trust-key.pub")
	public, ok := private.Public().(ed25519.PublicKey)
	if !ok {
		t.Fatal("unexpected public key type")
	}
	if err = os.WriteFile(key, []byte(base64.StdEncoding.EncodeToString(public)+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	return key
}

// resignLocalTestPackage keeps every signed byte of pkg and signs it with a new key.
func resignLocalTestPackage(t *testing.T, pkg localTestPackage) localTestPackage {
	t.Helper()
	_, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	for _, name := range []string{"local-manifest.json", "railwait-bundle.tar.gz", "railwait-images.tar"} {
		data, err := os.ReadFile(filepath.Join(pkg.dir, name))
		if err != nil {
			t.Fatal(err)
		}
		if err = os.WriteFile(filepath.Join(dir, name), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	return localTestPackage{dir: dir, key: signLocalTestPackage(t, dir, private), images: pkg.images, private: private}
}

func sortedLocalKeys(values map[string]string) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

// localUpdateRunner emulates Docker for a local-package installation: status reports the
// running images, and recreating the application services switches to the next set.
type localUpdateRunner struct {
	base             *fakeRunner
	project          string
	running          map[string]string
	next             map[string]string
	unhealthyAfterUp bool
	unhealthy        bool
	gateClosedAtLoad bool
	notDrained       bool
	commands         []string
}

func (r *localUpdateRunner) Run(ctx context.Context, c Command) ([]byte, error) {
	args := strings.Join(c.Args, " ")
	r.commands = append(r.commands, args)
	switch {
	case strings.Contains(args, "context inspect"):
		return r.base.Run(ctx, c)
	case strings.Contains(args, "--database-revision"):
		return []byte(`{"schema":1,"database_revision":"rev1"}`), nil
	case strings.Contains(args, "rail_waitlist.maintenance --status"):
		data, err := os.ReadFile(filepath.Join(c.Dir, ".railwait", "manager", "control", "maintenance.json"))
		if err != nil {
			return nil, err
		}
		var gate map[string]any
		if err = json.Unmarshal(data, &gate); err != nil {
			return nil, err
		}
		gate["drained"] = !r.notDrained
		return json.Marshal(gate)
	case strings.Contains(args, "update-backup.sh"):
		if c.Input == nil {
			return nil, fmt.Errorf("missing settings backup stdin")
		}
		if _, err := io.Copy(io.Discard, c.Input); err != nil {
			return nil, err
		}
		return []byte(`{"status":"verified"}`), nil
	case strings.Contains(args, "image load"):
		if c.Input == nil {
			return nil, io.ErrUnexpectedEOF
		}
		data, err := io.ReadAll(c.Input)
		if err != nil || len(data) == 0 {
			return nil, io.ErrUnexpectedEOF
		}
		gate, err := os.ReadFile(filepath.Join(c.Dir, ".railwait", "manager", "control", "maintenance.json"))
		if err != nil {
			return nil, err
		}
		r.gateClosedAtLoad = r.gateClosedAtLoad || strings.Contains(string(gate), `"closed":true`)
		return []byte("Loaded image"), nil
	case strings.Contains(args, "image inspect"):
		var out strings.Builder
		for _, arg := range c.Args {
			if strings.HasPrefix(arg, "sha256:") {
				line, _ := json.Marshal(arg)
				out.Write(append(line, '\n'))
			}
		}
		return []byte(out.String()), nil
	case strings.Contains(args, "up -d --force-recreate"):
		r.running = r.next
		r.unhealthy = r.unhealthyAfterUp
		return nil, nil
	case strings.Contains(args, "ps --all --format"):
		services := []map[string]any{}
		for _, name := range sortedLocalKeys(r.running) {
			state, health := "running", "healthy"
			if r.unhealthy {
				health = "unhealthy"
			}
			if name == "migration" || name == "log-init" {
				state, health = "exited", ""
			}
			services = append(services, map[string]any{"Service": name, "State": state, "Health": health, "ExitCode": 0})
		}
		return json.Marshal(services)
	case strings.Contains(args, "ps --all --quiet"):
		return []byte(strings.Repeat("b", 64)), nil
	case strings.Contains(args, "inspect --format"):
		var out strings.Builder
		for _, name := range sortedLocalKeys(r.running) {
			labels, _ := json.Marshal(map[string]string{"com.docker.compose.service": name, "com.docker.compose.project": r.project})
			image, _ := json.Marshal(r.running[name])
			fmt.Fprintf(&out, "%s %s\n", labels, image)
		}
		return []byte(out.String()), nil
	}
	return r.base.Run(ctx, c)
}

// localUpdateFixture converts a completed test installation into a healthy local-package
// installation of 1.4.0 with a running host manager and an open maintenance gate.
func localUpdateFixture(t *testing.T) (*Installer, *localUpdateRunner, State) {
	t.Helper()
	ctx := context.Background()
	root := filepath.Join(t.TempDir(), "installation")
	base := &fakeRunner{configured: true}
	initial := testInstaller(base, &fakeReleases{})
	state, err := initial.Install(ctx, Options{Path: root})
	if err != nil {
		t.Fatal(err)
	}
	current := newLocalTestPackage(t, "1.4.0", "rev1", localTestServices("api-1.4.0"))
	preserved, err := importLocalPackage(ctx, root, current.dir, current.key)
	if err != nil {
		t.Fatal(err)
	}
	r, err := preserved.Resolve(ctx, "stable")
	if err != nil {
		t.Fatal(err)
	}
	dir := filepath.Join(root, "releases", r.Version)
	if err = os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	if err = preserved.DownloadAndExtract(ctx, r, dir); err != nil {
		t.Fatal(err)
	}
	state.Release, state.ReleaseSource = r.Version, "local_package"
	state.ComposeFile = filepath.Join(dir, "compose.release.yml")
	state.ExpectedImages, state.DatabaseRevision, state.SettingsSchema = current.images, r.DatabaseRevision, r.SettingsSchema
	if state.BundleHashes, err = bundleHashes(dir); err != nil {
		t.Fatal(err)
	}
	if err = writeState(state); err != nil {
		t.Fatal(err)
	}
	p, err := manager.Prepare(root)
	if err != nil {
		t.Fatal(err)
	}
	heartbeat := manager.Heartbeat{Schema: 1, State: "ready", PID: os.Getpid(), StartedAt: time.Now().UTC().Format(time.RFC3339Nano), HeartbeatAt: time.Now().UTC().Format(time.RFC3339Nano)}
	if err = manager.WriteStatusFile(filepath.Join(p.Status, "manager.json"), heartbeat); err != nil {
		t.Fatal(err)
	}
	if err = atomicWrite(filepath.Join(root, ".railwait", "backup.env"), []byte("BACKUP_AGE_IDENTITY_BASE64='dGVzdA=='\nBACKUP_AGE_RECIPIENT='age1test'\n")); err != nil {
		t.Fatal(err)
	}
	runner := &localUpdateRunner{base: base, project: state.Project, running: current.images}
	// The official resolver fails when consulted; local updates must never use it.
	i := &Installer{Runner: runner, Releases: &fakeReleases{fail: true}, CheckSpace: func(string) error { return nil }, ProbeInterval: time.Millisecond}
	return i, runner, state
}

func assertNoLocalPackageLeftovers(t *testing.T, root string) {
	t.Helper()
	for dir, prefix := range map[string]string{".railwait": ".local-package-", "releases": ".update-"} {
		entries, err := os.ReadDir(filepath.Join(root, dir))
		if err != nil {
			t.Fatal(err)
		}
		for _, entry := range entries {
			if strings.HasPrefix(entry.Name(), prefix) {
				t.Fatal("temporary update files left behind:", filepath.Join(dir, entry.Name()))
			}
		}
	}
}

func assertPreservedLocalPackage(t *testing.T, root, version string) {
	t.Helper()
	preserved := filepath.Join(root, ".railwait", "local-package")
	source, err := release.OpenLocal(context.Background(), preserved, filepath.Join(preserved, "trust-key.pub"))
	if err != nil || source.Manifest.Version != version {
		t.Fatalf("preserved package is not %s: %v", version, err)
	}
}

func copyTestFile(t *testing.T, from, to string) {
	t.Helper()
	data, err := os.ReadFile(from)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(to, data, 0600); err != nil {
		t.Fatal(err)
	}
}

func assertGateClosed(t *testing.T, root string, closed bool) {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(root, ".railwait", "manager", "control", "maintenance.json"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(data), `"closed":true`) != closed {
		t.Fatalf("gate=%s", data)
	}
}

func assertLocalUpdateDidNotStart(t *testing.T, runner *localUpdateRunner, state State) {
	t.Helper()
	for _, args := range runner.commands {
		if strings.Contains(args, "image load") {
			t.Fatal("changed the installation for a rejected plan:", args)
		}
	}
	assertServicesUntouched(t, runner, state)
}

// assertServicesUntouched checks that an update stopped before the maintenance gate closed:
// no backup ran, no service was stopped or recreated, and the installation record, the
// preserved package and the open gate are unchanged.
func assertServicesUntouched(t *testing.T, runner *localUpdateRunner, state State) {
	t.Helper()
	for _, args := range runner.commands {
		if strings.Contains(args, " stop ") || strings.Contains(args, " up ") || strings.Contains(args, "update-backup.sh") {
			t.Fatal("touched the running services:", args)
		}
	}
	updated, err := ReadState(state.Path)
	if err != nil || updated.Release != state.Release {
		t.Fatalf("state changed: %+v %v", updated, err)
	}
	assertPreservedLocalPackage(t, state.Path, state.Release)
	assertGateClosed(t, state.Path, false)
	assertNoLocalPackageLeftovers(t, state.Path)
}

func TestLocalPackageUpdateLoadsImagesAndSwapsPreservedPackageBeforeClosingTheGate(t *testing.T) {
	ctx := context.Background()
	i, runner, state := localUpdateFixture(t)
	target := newLocalTestPackage(t, "1.4.1", "rev1", localTestServices("api-1.4.1"))
	runner.next = target.images
	plan, err := i.PlanLocalUpdate(ctx, state.Path, target.dir, target.key)
	if err != nil {
		t.Fatal(err)
	}
	if !plan.Allowed || plan.TargetReleaseID != "1.4.1" {
		t.Fatalf("plan=%+v", plan)
	}
	preserved := filepath.Join(state.Path, ".railwait", "local-package")
	stages := []string{}
	gateAtSwap, packageAtDrain := "", ""
	result, err := i.Update(ctx, UpdateOptions{Path: state.Path, TargetReleaseID: plan.TargetReleaseID, PlanID: plan.PlanID, PackagePath: target.dir, TrustKey: target.key, Progress: func(stage string) error {
		stages = append(stages, stage)
		switch stage {
		case "replacing_package":
			data, _ := os.ReadFile(filepath.Join(state.Path, ".railwait", "manager", "control", "maintenance.json"))
			gateAtSwap = string(data)
		case "draining":
			if source, openErr := release.OpenLocalMetadata(ctx, preserved, filepath.Join(preserved, "trust-key.pub")); openErr == nil {
				packageAtDrain = source.Manifest.Version
			}
		}
		return nil
	}})
	if err != nil || result.State != "succeeded" || len(result.Warnings) != 0 {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	if strings.Join(stages, ",") != strings.Join(localUpdateSteps, ",") {
		t.Fatal(stages)
	}
	// A refused swap must surface while the gate is open and before any service changes.
	if gateAtSwap == "" || strings.Contains(gateAtSwap, `"closed":true`) || packageAtDrain != "1.4.1" {
		t.Fatalf("the preserved package must be swapped before the gate closes: gate=%s package=%q", gateAtSwap, packageAtDrain)
	}
	if runner.gateClosedAtLoad {
		t.Fatal("loaded images after closing the maintenance gate")
	}
	job, err := ReadUpdateJob(state.Path, result.JobID)
	if err != nil || job.State != "succeeded" || job.PreviousPackage != "" {
		t.Fatalf("job=%+v err=%v", job, err)
	}
	updated, err := ReadState(state.Path)
	if err != nil {
		t.Fatal(err)
	}
	if updated.Release != "1.4.1" || updated.ComposeFile != filepath.Join(state.Path, "releases", "1.4.1", "compose.release.yml") || !reflect.DeepEqual(updated.ExpectedImages, target.images) {
		t.Fatalf("state=%+v", updated)
	}
	source, err := release.OpenLocal(ctx, preserved, filepath.Join(preserved, "trust-key.pub"))
	if err != nil || source.Manifest.Version != "1.4.1" {
		t.Fatalf("preserved package was not replaced: %v", err)
	}
	want, _ := localTrustKey(target.key)
	got, _ := localTrustKey(filepath.Join(preserved, "trust-key.pub"))
	if want == nil || !bytes.Equal(want, got) {
		t.Fatal("preserved package kept the previous trust key")
	}
	assertNoLocalPackageLeftovers(t, state.Path)
	if status, err := i.Status(ctx, state.Path); err != nil || !status.Healthy {
		t.Fatalf("status=%+v err=%v", status, err)
	}
	load, backup, stop := -1, -1, -1
	for n, args := range runner.commands {
		switch {
		case strings.Contains(args, "image load"):
			load = n
		case strings.Contains(args, "update-backup.sh"):
			backup = n
		case strings.Contains(args, " stop "):
			stop = n
		case strings.Contains(args, " pull"):
			t.Fatal("local update attempted a registry pull")
		}
	}
	if load < 0 || backup <= load || stop <= backup {
		t.Fatalf("load=%d backup=%d stop=%d", load, backup, stop)
	}
	assertGateClosed(t, state.Path, false)
}

func TestLocalPackageUpdateBlocksUnsafeTargets(t *testing.T) {
	ctx := context.Background()
	storage := localTestServices("api-1.4.1")
	storage["postgres"] = "postgres-17"
	extra := localTestServices("api-1.4.1")
	extra["web"] = "web"
	for name, target := range map[string]struct {
		version, revision string
		services          map[string]string
	}{
		"same version":      {"1.4.0", "rev1", localTestServices("api-1.4.1")},
		"older version":     {"1.3.9", "rev1", localTestServices("api-1.4.1")},
		"database revision": {"1.4.1", "rev2", localTestServices("api-1.4.1")},
		"storage image":     {"1.4.1", "rev1", storage},
		"service set":       {"1.4.1", "rev1", extra},
	} {
		t.Run(name, func(t *testing.T) {
			i, runner, state := localUpdateFixture(t)
			pkg := newLocalTestPackage(t, target.version, target.revision, target.services)
			plan, err := i.PlanLocalUpdate(ctx, state.Path, pkg.dir, pkg.key)
			if err != nil {
				t.Fatal(err)
			}
			if plan.Allowed || len(plan.BlockedReasons) == 0 {
				t.Fatalf("unsafe plan allowed: %+v", plan)
			}
			if _, err = i.Update(ctx, UpdateOptions{Path: state.Path, TargetReleaseID: plan.TargetReleaseID, PlanID: plan.PlanID, PackagePath: pkg.dir, TrustKey: pkg.key}); err == nil {
				t.Fatal("applied a blocked plan")
			}
			assertLocalUpdateDidNotStart(t, runner, state)
		})
	}
	t.Run("untrusted key", func(t *testing.T) {
		i, runner, state := localUpdateFixture(t)
		pkg := newLocalTestPackage(t, "1.4.1", "rev1", localTestServices("api-1.4.1"))
		other := newLocalTestPackage(t, "1.4.1", "rev1", localTestServices("api-1.4.1"))
		if _, err := i.PlanLocalUpdate(ctx, state.Path, pkg.dir, other.key); err == nil {
			t.Fatal("planned a package signed by another key")
		}
		assertLocalUpdateDidNotStart(t, runner, state)
	})
	t.Run("official installation", func(t *testing.T) {
		i, _, _, state := updateFixture(t)
		pkg := newLocalTestPackage(t, "1.4.1", "rev1", localTestServices("api-1.4.1"))
		if _, err := i.PlanLocalUpdate(ctx, state.Path, pkg.dir, pkg.key); err == nil {
			t.Fatal("planned a local package for an official installation")
		}
	})
}

func TestLocalPackageUpdateIsBoundToPlannedPackageAndKey(t *testing.T) {
	ctx := context.Background()
	i, runner, state := localUpdateFixture(t)
	target := newLocalTestPackage(t, "1.4.1", "rev1", localTestServices("api-1.4.1"))
	runner.next = target.images
	plan, err := i.PlanLocalUpdate(ctx, state.Path, target.dir, target.key)
	if err != nil || !plan.Allowed {
		t.Fatalf("plan=%+v err=%v", plan, err)
	}
	sameKey := signedLocalTestPackage(t, "1.4.1", "rev1", localTestServices("api-other"), target.private)
	resigned := resignLocalTestPackage(t, target)
	for name, o := range map[string]UpdateOptions{
		"manager request without package": {},
		"different package, same key":     {PackagePath: sameKey.dir, TrustKey: sameKey.key},
		"same package, different key":     {PackagePath: resigned.dir, TrustKey: resigned.key},
	} {
		o.Path, o.TargetReleaseID, o.PlanID = state.Path, plan.TargetReleaseID, plan.PlanID
		if _, err = i.Update(ctx, o); err == nil {
			t.Fatalf("%s: applied a plan for another package or key", name)
		}
	}
	assertLocalUpdateDidNotStart(t, runner, state)
}

func TestLocalPackageUpdateRestoresPreservedPackageWhenNewServicesFail(t *testing.T) {
	ctx := context.Background()
	i, runner, state := localUpdateFixture(t)
	target := newLocalTestPackage(t, "1.4.1", "rev1", localTestServices("api-1.4.1"))
	runner.next, runner.unhealthyAfterUp = target.images, true
	plan, err := i.PlanLocalUpdate(ctx, state.Path, target.dir, target.key)
	if err != nil || !plan.Allowed {
		t.Fatalf("plan=%+v err=%v", plan, err)
	}
	result, err := i.Update(ctx, UpdateOptions{Path: state.Path, TargetReleaseID: plan.TargetReleaseID, PlanID: plan.PlanID, PackagePath: target.dir, TrustKey: target.key})
	if err == nil || result.State != "recovery_required" {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	updated, err := ReadState(state.Path)
	if err != nil || updated.Release != "1.4.0" {
		t.Fatalf("committed a failed update: %+v %v", updated, err)
	}
	assertPreservedLocalPackage(t, state.Path, "1.4.0")
	assertNoLocalPackageLeftovers(t, state.Path)
	assertGateClosed(t, state.Path, true)
	if job, err := ReadUpdateJob(state.Path, result.JobID); err != nil || job.State != "recovery_required" || job.PreviousPackage != "" {
		t.Fatalf("job=%+v err=%v", job, err)
	}
}

func TestLocalPackageUpdateFailingAtThePackageSwapLeavesServicesUntouched(t *testing.T) {
	ctx := context.Background()
	i, runner, state := localUpdateFixture(t)
	target := newLocalTestPackage(t, "1.4.1", "rev1", localTestServices("api-1.4.1"))
	runner.next = target.images
	plan, err := i.PlanLocalUpdate(ctx, state.Path, target.dir, target.key)
	if err != nil || !plan.Allowed {
		t.Fatalf("plan=%+v err=%v", plan, err)
	}
	result, err := i.Update(ctx, UpdateOptions{Path: state.Path, TargetReleaseID: plan.TargetReleaseID, PlanID: plan.PlanID, PackagePath: target.dir, TrustKey: target.key, Progress: func(stage string) error {
		if stage == "replacing_package" {
			return errors.New("injected failure at the package swap")
		}
		return nil
	}})
	if err == nil || result.State != "failed" || result.PreviousPackage != "" {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	if job, err := ReadUpdateJob(state.Path, result.JobID); err != nil || job.State != "failed" || job.PreviousPackage != "" {
		t.Fatalf("job=%+v err=%v", job, err)
	}
	assertServicesUntouched(t, runner, state)
}

func TestLocalPackageUpdateRestoresPreservedPackageWhenTheDrainIsDeferred(t *testing.T) {
	i, runner, state := localUpdateFixture(t)
	target := newLocalTestPackage(t, "1.4.1", "rev1", localTestServices("api-1.4.1"))
	runner.next, runner.notDrained = target.images, true
	plan, err := i.PlanLocalUpdate(context.Background(), state.Path, target.dir, target.key)
	if err != nil || !plan.Allowed {
		t.Fatalf("plan=%+v err=%v", plan, err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result, err := i.Update(ctx, UpdateOptions{Path: state.Path, TargetReleaseID: plan.TargetReleaseID, PlanID: plan.PlanID, PackagePath: target.dir, TrustKey: target.key, Progress: func(stage string) error {
		if stage == "draining" {
			cancel()
		}
		return nil
	}})
	if err == nil || result.State != "deferred" || result.PreviousPackage != "" {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	if updated, err := ReadState(state.Path); err != nil || updated.Release != "1.4.0" {
		t.Fatalf("committed a deferred update: %+v %v", updated, err)
	}
	assertPreservedLocalPackage(t, state.Path, "1.4.0")
	assertNoLocalPackageLeftovers(t, state.Path)
	for _, args := range runner.commands {
		if strings.Contains(args, " stop ") || strings.Contains(args, "update-backup.sh") {
			t.Fatal("continued past a deferred drain:", args)
		}
	}
}

func TestLocalPackageUpdateRejectsPackageOrKeyChangedBeforeTheCopy(t *testing.T) {
	ctx := context.Background()
	for _, change := range []string{"package", "key"} {
		t.Run(change, func(t *testing.T) {
			i, runner, state := localUpdateFixture(t)
			target := newLocalTestPackage(t, "1.4.1", "rev1", localTestServices("api-1.4.1"))
			runner.next = target.images
			plan, err := i.PlanLocalUpdate(ctx, state.Path, target.dir, target.key)
			if err != nil || !plan.Allowed {
				t.Fatalf("plan=%+v err=%v", plan, err)
			}
			replacement := signedLocalTestPackage(t, "1.4.1", "rev1", localTestServices("api-other"), target.private)
			if change == "key" {
				replacement = resignLocalTestPackage(t, target)
			}
			// The early checks have passed by now; swap the files before the private copy.
			swap := func(stage string) error {
				if stage == "loading_package" {
					for _, name := range []string{"local-manifest.json", "local-manifest.sig", "railwait-bundle.tar.gz", "railwait-images.tar"} {
						copyTestFile(t, filepath.Join(replacement.dir, name), filepath.Join(target.dir, name))
					}
					copyTestFile(t, replacement.key, target.key)
				}
				return nil
			}
			result, err := i.Update(ctx, UpdateOptions{Path: state.Path, TargetReleaseID: plan.TargetReleaseID, PlanID: plan.PlanID, PackagePath: target.dir, TrustKey: target.key, Progress: swap})
			if err == nil || result.State != "failed" {
				t.Fatalf("result=%+v err=%v", result, err)
			}
			assertLocalUpdateDidNotStart(t, runner, state)
			assertPreservedLocalPackage(t, state.Path, "1.4.0")
		})
	}
}

func TestLocalPackageUpdateCancelledDuringVerificationRestoresPreservedPackage(t *testing.T) {
	i, runner, state := localUpdateFixture(t)
	target := newLocalTestPackage(t, "1.4.1", "rev1", localTestServices("api-1.4.1"))
	runner.next = target.images
	plan, err := i.PlanLocalUpdate(context.Background(), state.Path, target.dir, target.key)
	if err != nil || !plan.Allowed {
		t.Fatalf("plan=%+v err=%v", plan, err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result, err := i.Update(ctx, UpdateOptions{Path: state.Path, TargetReleaseID: plan.TargetReleaseID, PlanID: plan.PlanID, PackagePath: target.dir, TrustKey: target.key, Progress: func(stage string) error {
		if stage == "verifying" {
			cancel()
		}
		return nil
	}})
	if err == nil || result.State != "recovery_required" || result.PreviousPackage != "" {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	if updated, err := ReadState(state.Path); err != nil || updated.Release != "1.4.0" {
		t.Fatalf("committed a cancelled update: %+v %v", updated, err)
	}
	assertPreservedLocalPackage(t, state.Path, "1.4.0")
	assertNoLocalPackageLeftovers(t, state.Path)
	assertGateClosed(t, state.Path, true)
}

func TestLocalPackageUpdateFailureAfterNewRecordKeepsNewPackage(t *testing.T) {
	ctx := context.Background()
	i, runner, state := localUpdateFixture(t)
	target := newLocalTestPackage(t, "1.4.1", "rev1", localTestServices("api-1.4.1"))
	runner.next = target.images
	plan, err := i.PlanLocalUpdate(ctx, state.Path, target.dir, target.key)
	if err != nil || !plan.Allowed {
		t.Fatalf("plan=%+v err=%v", plan, err)
	}
	result, err := i.Update(ctx, UpdateOptions{Path: state.Path, TargetReleaseID: plan.TargetReleaseID, PlanID: plan.PlanID, PackagePath: target.dir, TrustKey: target.key, Progress: func(stage string) error {
		if stage == "resuming" {
			return errors.New("injected failure after the new record")
		}
		return nil
	}})
	if err == nil || result.State != "recovery_required" || result.PreviousPackage != "" || len(result.Warnings) != 0 {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	if updated, err := ReadState(state.Path); err != nil || updated.Release != "1.4.1" {
		t.Fatalf("state=%+v err=%v", updated, err)
	}
	assertPreservedLocalPackage(t, state.Path, "1.4.1")
	assertNoLocalPackageLeftovers(t, state.Path)
	assertGateClosed(t, state.Path, true)
}

func TestInterruptedLocalUpdateHintNamesThePreviousPackageJob(t *testing.T) {
	root := t.TempDir()
	job := strings.Repeat("c", 32)
	if err := os.MkdirAll(filepath.Join(root, ".railwait", previousLocalPackagePrefix+job), 0700); err != nil {
		t.Fatal(err)
	}
	err := withInterruptedLocalUpdateHint(root, errors.New("보존한 로컬 패키지 버전이 다릅니다"))
	if !strings.Contains(err.Error(), "railwait status --job "+job) {
		t.Fatal(err)
	}
	if plain := withInterruptedLocalUpdateHint(t.TempDir(), errors.New("plain")); plain.Error() != "plain" {
		t.Fatal(plain)
	}
}
