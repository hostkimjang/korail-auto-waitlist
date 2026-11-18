package install

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/release"
)

func signedInstallPackage(t *testing.T) (string, string, string) {
	t.Helper()
	dir := t.TempDir()
	pub, private, _ := ed25519.GenerateKey(rand.Reader)
	config := []byte(`{"architecture":"` + runtime.GOARCH + `","os":"linux"}`)
	id := "sha256:" + hashBytes(config)
	entry, _ := json.Marshal([]map[string]any{{"Config": "config.json", "RepoTags": nil, "Layers": []string{}}})
	images := installTar(t, map[string][]byte{"manifest.json": entry, "config.json": config}, false)
	bundle := installTar(t, map[string][]byte{"compose.release.yml": []byte("services:\n  api:\n    image: " + id + "\n    pull_policy: never\n"), ".env.example": []byte("COMPOSE_PROFILES=\n")}, true)
	m := release.LocalManifest{SchemaVersion: 1, Kind: "local_package", Version: "1.4.0", SourceRevision: strings.Repeat("a", 40), SourceTreeSHA256: strings.Repeat("b", 64), Platforms: []string{"linux/" + runtime.GOARCH}, MinimumCLIVersion: "0.1.0", DatabaseRevision: "rev1", SettingsSchema: 1, Bundle: release.LocalArtifact{File: "railwait-bundle.tar.gz", SHA256: hashBytes(bundle), SizeBytes: int64(len(bundle))}, ImageArchive: release.LocalArtifact{File: "railwait-images.tar", SHA256: hashBytes(images), SizeBytes: int64(len(images))}, Images: map[string]string{"api": id}, ServiceProfiles: map[string][]string{"api": {}}}
	metadata, _ := json.Marshal(m)
	m.ImageConfigIDs = m.Images
	metadata, _ = json.Marshal(m)
	for name, data := range map[string][]byte{"local-manifest.json": metadata, "local-manifest.sig": []byte(base64.StdEncoding.EncodeToString(ed25519.Sign(private, metadata))), m.Bundle.File: bundle, m.ImageArchive.File: images} {
		if err := os.WriteFile(filepath.Join(dir, name), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	key := filepath.Join(t.TempDir(), "key.pub")
	os.WriteFile(key, []byte(base64.StdEncoding.EncodeToString(pub)), 0600)
	return dir, key, id
}
func installTar(t *testing.T, files map[string][]byte, compressed bool) []byte {
	t.Helper()
	var data bytes.Buffer
	var gz *gzip.Writer
	var tw *tar.Writer
	if compressed {
		gz = gzip.NewWriter(&data)
		tw = tar.NewWriter(gz)
	} else {
		tw = tar.NewWriter(&data)
	}
	for name, content := range files {
		tw.WriteHeader(&tar.Header{Name: name, Mode: 0644, Size: int64(len(content))})
		tw.Write(content)
	}
	tw.Close()
	if gz != nil {
		gz.Close()
	}
	return data.Bytes()
}

type localRunner struct {
	base     fakeRunner
	commands []Command
	id       string
	failLoad bool
}

func (r *localRunner) Run(ctx context.Context, c Command) ([]byte, error) {
	r.commands = append(r.commands, c)
	args := strings.Join(c.Args, " ")
	if strings.Contains(args, "image load") {
		if c.Input == nil {
			return nil, io.ErrUnexpectedEOF
		}
		data, err := io.ReadAll(c.Input)
		if err != nil {
			return nil, err
		}
		if len(data) == 0 {
			return nil, io.ErrUnexpectedEOF
		}
		if r.failLoad {
			return nil, io.ErrUnexpectedEOF
		}
		return []byte("Loaded image ID: " + r.id), nil
	}
	if strings.Contains(args, "image inspect") {
		return json.Marshal(r.id)
	}
	if strings.Contains(args, "inspect --format") {
		s, err := ReadState(c.Dir)
		if err != nil {
			return nil, err
		}
		return []byte(`{"com.docker.compose.service":"api","com.docker.compose.project":"` + s.Project + `"} "` + r.id + `"`), nil
	}
	return r.base.Run(ctx, c)
}

func TestLocalInstallLoadsVerifiedUntaggedImagesBeforeDatabaseAndResumesWithoutOriginal(t *testing.T) {
	dir, key, id := signedInstallPackage(t)
	root := filepath.Join(t.TempDir(), "installation")
	r := &localRunner{id: id, failLoad: true}
	i := Installer{Runner: r, Releases: &fakeReleases{fail: true}, CheckSpace: func(string) error { return nil }, CheckPort: func(int) error { return nil }, ProbeInterval: time.Millisecond}
	_, err := i.Install(context.Background(), Options{Path: root, PackagePath: dir, TrustKey: key})
	if err == nil {
		t.Fatal("expected injected load failure")
	}
	before, _ := os.ReadFile(filepath.Join(root, ".env"))
	for _, c := range r.commands {
		if strings.Contains(strings.Join(c.Args, " "), "up -d") {
			t.Fatal("database started before image verification")
		}
	}
	os.RemoveAll(dir)
	os.Remove(key)
	r.failLoad = false
	r.base.configured = true
	s, err := i.Install(context.Background(), Options{Path: root})
	if err != nil {
		t.Fatal(err)
	}
	if s.ReleaseSource != "local_package" || s.Stage != "complete" {
		t.Fatal(s)
	}
	after, _ := os.ReadFile(filepath.Join(root, ".env"))
	if !bytes.Equal(before, after) {
		t.Fatal("resume regenerated secrets")
	}
	load, up := -1, -1
	for n, c := range r.commands {
		args := strings.Join(c.Args, " ")
		if strings.Contains(args, " pull") {
			t.Fatal("local install attempted network pull")
		}
		if strings.Contains(args, "image load") {
			load = n
		}
		if up < 0 && strings.Contains(args, "up -d") {
			up = n
		}
	}
	if load < 0 || up <= load {
		t.Fatal("incorrect image load order")
	}
	p, err := i.PlanUpdate(context.Background(), root)
	if err != nil || p.Allowed || len(p.BlockedReasons) == 0 {
		t.Fatalf("local update not blocked: %+v %v", p, err)
	}
}

func TestLocalComposeRejectsMutableImagesBuildAndExternalResources(t *testing.T) {
	for _, body := range []string{"services:\n  api:\n    image: api:latest\n    pull_policy: never\n", "services:\n  api:\n    image: sha256:abc\n    pull_policy: always\n", "services:\n  api:\n    image: sha256:abc\n    pull_policy: never\n    build: .\n", "services:\n  api:\n    image: sha256:abc\n    pull_policy: never\nvolumes:\n  database:\n    external: true\n"} {
		dir := t.TempDir()
		os.WriteFile(filepath.Join(dir, "compose.release.yml"), []byte(body), 0600)
		if err := validateLocalCompose(dir, map[string]string{"api": "sha256:abc"}); err == nil {
			t.Fatal("accepted unsafe local Compose")
		}
	}
}
