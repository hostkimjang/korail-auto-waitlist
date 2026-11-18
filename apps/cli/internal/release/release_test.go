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
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

type transport func(*http.Request) (*http.Response, error)

func (f transport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func archive(t *testing.T, headers []*tar.Header) []byte {
	t.Helper()
	var b bytes.Buffer
	gz := gzip.NewWriter(&b)
	tw := tar.NewWriter(gz)
	for _, h := range headers {
		if err := tw.WriteHeader(h); err != nil {
			t.Fatal(err)
		}
		if h.Size > 0 {
			if _, err := tw.Write(bytes.Repeat([]byte("x"), int(h.Size))); err != nil {
				t.Fatal(err)
			}
		}
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	if err := gz.Close(); err != nil {
		t.Fatal(err)
	}
	return b.Bytes()
}
func fixture(t *testing.T) (*Client, map[string][]byte, Manifest, ed25519.PrivateKey) {
	t.Helper()
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	bundle := archive(t, []*tar.Header{{Name: "compose.release.yml", Mode: 0644, Size: 2}, {Name: ".env.example", Mode: 0644, Size: 3}, {Name: "infra/caddy/Caddyfile", Mode: 0644, Size: 1}})
	sum := sha256.Sum256(bundle)
	base := "https://github.com/" + repository + "/releases/download/v1.4.0/"
	m := Manifest{DatabaseRevision: "0041_station_cache_v4", SettingsSchema: 1, CompatibleFrom: []string{"1.3.3"}, ServiceProfiles: map[string][]string{"api": {}}, SchemaVersion: 1, Version: "1.4.0", Channel: "stable", SourceRevision: strings.Repeat("a", 40), MinimumCLIVersion: "0.1.0", Platforms: []string{"linux/" + runtime.GOARCH}, Bundle: Bundle{URL: base + "railwait-bundle.tar.gz", SHA256: hex.EncodeToString(sum[:]), SizeBytes: int64(len(bundle))}, Images: map[string]string{"api": "ghcr.io/example/api@sha256:" + strings.Repeat("a", 64)}}
	metadata, _ := json.Marshal(m)
	sig := []byte(base64.StdEncoding.EncodeToString(ed25519.Sign(priv, metadata)))
	assets := []map[string]string{}
	for _, name := range []string{"release-manifest.json", "release-manifest.sig", "railwait-bundle.tar.gz"} {
		assets = append(assets, map[string]string{"name": name, "browser_download_url": base + name})
	}
	listing, _ := json.Marshal(map[string]any{"tag_name": "v1.4.0", "draft": false, "prerelease": false, "assets": assets})
	responses := map[string][]byte{"https://api.github.com/repos/" + repository + "/releases/latest": listing, base + "release-manifest.json": metadata, base + "release-manifest.sig": sig, base + "railwait-bundle.tar.gz": bundle}
	c := &Client{PublicKey: pub, CLIVersion: "0.1.0", HTTP: &http.Client{Transport: transport(func(r *http.Request) (*http.Response, error) {
		// Like GitHub, the REST API refuses a request that does not accept JSON.
		if r.URL.Host == "api.github.com" && !strings.Contains(r.Header.Get("Accept"), "json") {
			return &http.Response{StatusCode: http.StatusUnsupportedMediaType, Body: io.NopCloser(bytes.NewReader(nil)), Header: make(http.Header)}, nil
		}
		data, ok := responses[r.URL.String()]
		code := 200
		if !ok {
			code = 404
		}
		return &http.Response{StatusCode: code, Body: io.NopCloser(bytes.NewReader(data)), Header: make(http.Header)}, nil
	})}}
	return c, responses, m, priv
}
func TestVerifiedReleaseDownloadsAndExtracts(t *testing.T) {
	c, _, _, _ := fixture(t)
	r, err := c.Resolve(context.Background(), "stable")
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	if err = c.DownloadAndExtract(context.Background(), r, dir); err != nil {
		t.Fatal(err)
	}
	if _, err = os.Stat(filepath.Join(dir, "infra", "caddy", "Caddyfile")); err != nil {
		t.Fatal(err)
	}
}
func TestReleaseRequestsAcceptJSONFromTheAPIAndBinaryAssets(t *testing.T) {
	c, _, _, _ := fixture(t)
	accepted := map[string]string{}
	github := c.HTTP.Transport
	c.HTTP.Transport = transport(func(r *http.Request) (*http.Response, error) {
		accepted[r.URL.Host+r.URL.Path] = r.Header.Get("Accept")
		return github.RoundTrip(r)
	})
	r, err := c.Resolve(context.Background(), "stable")
	if err != nil {
		t.Fatal(err)
	}
	if err = c.DownloadAndExtract(context.Background(), r, t.TempDir()); err != nil {
		t.Fatal(err)
	}
	if len(accepted) != 4 {
		t.Fatalf("requests: %v", accepted)
	}
	for address, accept := range accepted {
		want := "application/octet-stream"
		if strings.HasPrefix(address, "api.github.com/") {
			want = "application/vnd.github+json"
		}
		if accept != want {
			t.Fatalf("%s asked for %q, want %q", address, accept, want)
		}
	}
}
func TestRejectTamperedMetadataAndBundle(t *testing.T) {
	for _, asset := range []string{"release-manifest.json", "release-manifest.sig", "railwait-bundle.tar.gz"} {
		t.Run(asset, func(t *testing.T) {
			c, responses, m, _ := fixture(t)
			key := strings.TrimSuffix(m.Bundle.URL, "railwait-bundle.tar.gz") + asset
			responses[key] = append(responses[key], byte('x'))
			r, err := c.Resolve(context.Background(), "stable")
			if err == nil {
				err = c.DownloadAndExtract(context.Background(), r, t.TempDir())
			}
			if err == nil {
				t.Fatal("tampered asset accepted")
			}
		})
	}
}
func TestRejectSignedInvalidContracts(t *testing.T) {
	cases := map[string]func(*Manifest){"origin": func(m *Manifest) { m.Bundle.URL = "https://attacker.example/bundle" }, "cli": func(m *Manifest) { m.MinimumCLIVersion = "99.0.0" }, "tag": func(m *Manifest) { m.Version = "1.5.0" }, "architecture": func(m *Manifest) { m.Platforms = []string{"linux/unsupported"} }, "mutable image": func(m *Manifest) { m.Images["api"] = "api:latest" }, "oversize": func(m *Manifest) { m.Bundle.SizeBytes = maxBundle + 1 }, "schema": func(m *Manifest) { m.SchemaVersion = 2 }, "database": func(m *Manifest) { m.DatabaseRevision = "../invalid" }, "settings": func(m *Manifest) { m.SettingsSchema = 2 }, "compatibility": func(m *Manifest) { m.CompatibleFrom = []string{"1.4.0"} }, "profiles": func(m *Manifest) { m.ServiceProfiles = nil }}
	for name, change := range cases {
		t.Run(name, func(t *testing.T) {
			c, responses, m, private := fixture(t)
			base := strings.TrimSuffix(m.Bundle.URL, "railwait-bundle.tar.gz")
			change(&m)
			metadata, _ := json.Marshal(m)
			responses[base+"release-manifest.json"] = metadata
			responses[base+"release-manifest.sig"] = []byte(base64.StdEncoding.EncodeToString(ed25519.Sign(private, metadata)))
			if _, err := c.Resolve(context.Background(), "stable"); err == nil {
				t.Fatal("invalid signed release accepted")
			}
		})
	}
}
func TestArchiveRejectsTraversalLinksAndUnexpectedFiles(t *testing.T) {
	for _, h := range []*tar.Header{{Name: "../outside", Mode: 0644, Size: 1}, {Name: "infra/../../outside", Mode: 0644, Size: 1}, {Name: "infra/link", Typeflag: tar.TypeSymlink, Linkname: "/etc/passwd"}, {Name: "infra/link", Typeflag: tar.TypeLink, Linkname: ".env.example"}, {Name: ".env", Size: 1}, {Name: "infra/C:evil", Size: 1}, {Name: "infra\\escape", Size: 1}} {
		t.Run(h.Name, func(t *testing.T) {
			if err := extract(archive(t, []*tar.Header{h}), t.TempDir()); err == nil {
				t.Fatal("unsafe archive accepted")
			}
		})
	}
}
func TestArchiveRejectsDuplicateOrMissingRequired(t *testing.T) {
	for _, headers := range [][]*tar.Header{{{Name: "compose.release.yml", Size: 1}}, {{Name: "compose.release.yml", Size: 1}, {Name: "compose.release.yml", Size: 1}, {Name: ".env.example", Size: 1}}} {
		if err := extract(archive(t, headers), t.TempDir()); err == nil {
			t.Fatal("invalid archive accepted")
		}
	}
}
func TestRedirectRequiresOfficialHTTPSHost(t *testing.T) {
	for _, address := range []string{"http://github.com/file", "https://evil.example/file", "https://user:pass@github.com/file"} {
		req, _ := http.NewRequest("GET", address, nil)
		if secureRedirect(req, nil) == nil {
			t.Fatal("unsafe redirect accepted")
		}
	}
}
