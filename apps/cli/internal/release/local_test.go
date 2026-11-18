package release

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func localArchive(t *testing.T, files map[string][]byte, compressed bool) []byte {
	t.Helper()
	var out bytes.Buffer
	var writer *tar.Writer
	var gz *gzip.Writer
	if compressed {
		gz = gzip.NewWriter(&out)
		writer = tar.NewWriter(gz)
	} else {
		writer = tar.NewWriter(&out)
	}
	for name, data := range files {
		if err := writer.WriteHeader(&tar.Header{Name: name, Mode: 0644, Size: int64(len(data))}); err != nil {
			t.Fatal(err)
		}
		writer.Write(data)
	}
	writer.Close()
	if gz != nil {
		gz.Close()
	}
	return out.Bytes()
}
func localFixture(t *testing.T) (string, string, LocalManifest, ed25519.PrivateKey) {
	t.Helper()
	dir := t.TempDir()
	public, private, _ := ed25519.GenerateKey(rand.Reader)
	config := []byte(`{"architecture":"` + runtime.GOARCH + `","os":"linux","rootfs":{"type":"layers","diff_ids":[]}}`)
	configHash := hashLocal(config)
	entry, _ := json.Marshal([]map[string]any{{"Config": configHash + ".json", "RepoTags": nil, "Layers": []string{}}})
	images := localArchive(t, map[string][]byte{"manifest.json": entry, configHash + ".json": config}, false)
	bundle := localArchive(t, map[string][]byte{"compose.release.yml": []byte("services: {}"), ".env.example": []byte("TZ=Asia/Seoul\n")}, true)
	m := LocalManifest{SchemaVersion: 1, Kind: "local_package", Version: "1.4.0", SourceRevision: strings.Repeat("a", 40), SourceTreeSHA256: strings.Repeat("b", 64), SourceDirty: true, Platforms: []string{"linux/" + runtime.GOARCH}, MinimumCLIVersion: "0.1.0", DatabaseRevision: "rev1", SettingsSchema: 1, Bundle: LocalArtifact{"railwait-bundle.tar.gz", hashLocal(bundle), int64(len(bundle))}, ImageArchive: LocalArtifact{"railwait-images.tar", hashLocal(images), int64(len(images))}, Images: map[string]string{"api": "sha256:" + configHash}, ServiceProfiles: map[string][]string{"api": {}}}
	os.WriteFile(filepath.Join(dir, m.Bundle.File), bundle, 0600)
	os.WriteFile(filepath.Join(dir, m.ImageArchive.File), images, 0600)
	writeLocalManifest(t, dir, m, private)
	key := filepath.Join(t.TempDir(), "trust.pub")
	os.WriteFile(key, []byte(base64.StdEncoding.EncodeToString(public)), 0600)
	return dir, key, m, private
}
func hashLocal(data []byte) string { sum := sha256.Sum256(data); return hex.EncodeToString(sum[:]) }
func writeLocalManifest(t *testing.T, dir string, m LocalManifest, key ed25519.PrivateKey) {
	t.Helper()
	if m.ImageConfigIDs == nil {
		m.ImageConfigIDs = m.Images
	}
	metadata, _ := json.Marshal(m)
	os.WriteFile(filepath.Join(dir, "local-manifest.json"), metadata, 0600)
	os.WriteFile(filepath.Join(dir, "local-manifest.sig"), []byte(base64.StdEncoding.EncodeToString(ed25519.Sign(key, metadata))), 0600)
}

func TestLocalSignedPackageAndOfficialModeAreSeparate(t *testing.T) {
	dir, key, _, _ := localFixture(t)
	source, err := OpenLocal(context.Background(), dir, key)
	if err != nil {
		t.Fatal(err)
	}
	r, err := source.Resolve(context.Background(), "stable")
	if err != nil {
		t.Fatal(err)
	}
	if err = source.DownloadAndExtract(context.Background(), r, t.TempDir()); err != nil {
		t.Fatal(err)
	}
	metadata, _ := os.ReadFile(filepath.Join(dir, "local-manifest.json"))
	signature, _ := os.ReadFile(filepath.Join(dir, "local-manifest.sig"))
	keyData, _ := os.ReadFile(key)
	pub, _ := base64.StdEncoding.DecodeString(string(keyData))
	if _, err = (&Client{PublicKey: pub, CLIVersion: "0.1.0"}).verify(metadata, signature); err == nil {
		t.Fatal("official resolver accepted local package")
	}
}
func TestLocalTamperingAndWrongTrustKeyAreRejected(t *testing.T) {
	for _, name := range []string{"local-manifest.json", "local-manifest.sig", "railwait-bundle.tar.gz", "railwait-images.tar", "trust"} {
		t.Run(name, func(t *testing.T) {
			dir, key, _, _ := localFixture(t)
			target := filepath.Join(dir, name)
			if name == "trust" {
				target = key
			}
			f, _ := os.OpenFile(target, os.O_APPEND|os.O_WRONLY, 0600)
			f.WriteString("tamper")
			f.Close()
			if _, err := OpenLocal(context.Background(), dir, key); err == nil {
				t.Fatal("accepted tampered local asset")
			}
		})
	}
}
func TestLocalTaggedImageCannotOverwriteProductionTag(t *testing.T) {
	dir, key, m, private := localFixture(t)
	config := []byte(`{}`)
	configHash := hashLocal(config)
	entry, _ := json.Marshal([]map[string]any{{"Config": configHash + ".json", "RepoTags": []string{"rail-waitlist-api:local"}, "Layers": []string{}}})
	images := localArchive(t, map[string][]byte{"manifest.json": entry, configHash + ".json": config}, false)
	m.ImageArchive.SHA256 = hashLocal(images)
	m.ImageArchive.SizeBytes = int64(len(images))
	m.Images["api"] = "sha256:" + configHash
	os.WriteFile(filepath.Join(dir, m.ImageArchive.File), images, 0600)
	writeLocalManifest(t, dir, m, private)
	if _, err := OpenLocal(context.Background(), dir, key); err == nil {
		t.Fatal("accepted production tag overwrite")
	}
}
func TestLocalSignedWrongImageOrPlatformIsRejected(t *testing.T) {
	for _, name := range []string{"image", "platform"} {
		t.Run(name, func(t *testing.T) {
			dir, key, m, private := localFixture(t)
			if name == "image" {
				m.Images["api"] = "sha256:" + strings.Repeat("c", 64)
			} else {
				m.Platforms = []string{"linux/unsupported"}
			}
			writeLocalManifest(t, dir, m, private)
			if _, err := OpenLocal(context.Background(), dir, key); err == nil {
				t.Fatal("accepted invalid signed contract")
			}
		})
	}
}

func TestLocalOCIIndexIsBoundToPlatformConfigAndCannotNameProductionTags(t *testing.T) {
	for _, test := range []string{"valid", "wrongconfig", "tag"} {
		t.Run(test, func(t *testing.T) {
			dir, key, m, private := localFixture(t)
			config := []byte(`{"os":"linux","architecture":"` + runtime.GOARCH + `"}`)
			configID := "sha256:" + hashLocal(config)
			manifest, _ := json.Marshal(map[string]any{"schemaVersion": 2, "config": map[string]any{"digest": configID}, "layers": []any{}})
			manifestID := "sha256:" + hashLocal(manifest)
			index, _ := json.Marshal(map[string]any{"schemaVersion": 2, "manifests": []any{map[string]any{"digest": manifestID, "platform": map[string]any{"os": "linux", "architecture": runtime.GOARCH}}}})
			rootID := "sha256:" + hashLocal(index)
			annotations := map[string]string{"org.opencontainers.image.ref.name": rootID}
			if test == "tag" {
				annotations["org.opencontainers.image.ref.name"] = "rail-waitlist-api:local"
			}
			archiveIndex, _ := json.Marshal(map[string]any{"schemaVersion": 2, "manifests": []any{map[string]any{"digest": rootID, "annotations": annotations}}})
			dockerManifest, _ := json.Marshal([]map[string]any{{"Config": "blobs/sha256/" + strings.TrimPrefix(configID, "sha256:"), "RepoTags": nil, "Layers": []string{}}})
			data := localArchive(t, map[string][]byte{"index.json": archiveIndex, "manifest.json": dockerManifest, "blobs/sha256/" + strings.TrimPrefix(configID, "sha256:"): config, "blobs/sha256/" + strings.TrimPrefix(manifestID, "sha256:"): manifest, "blobs/sha256/" + strings.TrimPrefix(rootID, "sha256:"): index}, false)
			m.Images["api"] = rootID
			m.ImageConfigIDs = map[string]string{"api": configID}
			if test == "wrongconfig" {
				m.ImageConfigIDs["api"] = "sha256:" + strings.Repeat("d", 64)
			}
			m.ImageArchive.SHA256 = hashLocal(data)
			m.ImageArchive.SizeBytes = int64(len(data))
			os.WriteFile(filepath.Join(dir, m.ImageArchive.File), data, 0600)
			writeLocalManifest(t, dir, m, private)
			_, err := OpenLocal(context.Background(), dir, key)
			if test == "valid" && err != nil {
				t.Fatal(err)
			}
			if test != "valid" && err == nil {
				t.Fatal("invalid OCI identity accepted")
			}
		})
	}
}
