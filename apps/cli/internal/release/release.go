// Package release verifies official release metadata before allowing Docker input.
package release

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"time"
)

const repository = "hostkimjang/korail-auto-waitlist"
const maxBundle = 32 << 20

// PublicKeyBase64 is embedded by the release pipeline. Development binaries must
// fail closed instead of trusting a key delivered alongside an untrusted bundle.
var PublicKeyBase64 string
var CLIVersion = "0.1.0"

var versionPattern = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`)
var digestPattern = regexp.MustCompile(`^[a-f0-9]{64}$`)
var revisionPattern = regexp.MustCompile(`^[a-f0-9]{40}$`)
var imagePattern = regexp.MustCompile(`^[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}$`)

type Bundle struct {
	URL       string `json:"url"`
	SHA256    string `json:"sha256"`
	SizeBytes int64  `json:"size_bytes"`
}
type Manifest struct {
	SchemaVersion     int                 `json:"schema_version"`
	Version           string              `json:"version"`
	Channel           string              `json:"channel"`
	SourceRevision    string              `json:"source_revision"`
	Platforms         []string            `json:"platforms"`
	MinimumCLIVersion string              `json:"minimum_cli_version"`
	DatabaseRevision  string              `json:"database_revision"`
	SettingsSchema    int                 `json:"settings_schema"`
	CompatibleFrom    []string            `json:"compatible_from"`
	Bundle            Bundle              `json:"bundle"`
	Images            map[string]string   `json:"images"`
	ServiceProfiles   map[string][]string `json:"service_profiles"`
}
type Release struct {
	Version          string              `json:"version"`
	BundleURL        string              `json:"bundle_url"`
	SHA256           string              `json:"sha256"`
	ComposeProject   string              `json:"compose_project,omitempty"`
	ManifestURL      string              `json:"manifest_url"`
	DatabaseRevision string              `json:"database_revision"`
	SettingsSchema   int                 `json:"settings_schema"`
	CompatibleFrom   []string            `json:"compatible_from"`
	Images           map[string]string   `json:"images"`
	ServiceProfiles  map[string][]string `json:"service_profiles"`
	manifest         []byte
	signature        []byte
}

// Client's transport can be replaced in tests; origins and embedded trust remain
// constrained for ordinary command-line users.
type Client struct {
	HTTP       *http.Client
	PublicKey  ed25519.PublicKey
	CLIVersion string
}

func officialClient() (*Client, error) {
	key, err := base64.StdEncoding.DecodeString(PublicKeyBase64)
	if err != nil || len(key) != ed25519.PublicKeySize {
		return nil, errors.New("공식 릴리스 검증키가 없는 개발용 CLI입니다; 서명된 정식 CLI가 필요합니다")
	}
	return &Client{HTTP: &http.Client{Timeout: 5 * time.Minute, CheckRedirect: secureRedirect}, PublicKey: key, CLIVersion: CLIVersion}, nil
}
func secureRedirect(req *http.Request, via []*http.Request) error {
	if len(via) >= 5 {
		return errors.New("too many redirects")
	}
	if req.URL.Scheme != "https" || req.URL.User != nil {
		return errors.New("insecure release redirect")
	}
	host := req.URL.Hostname()
	if host != "github.com" && host != "api.github.com" && host != "release-assets.githubusercontent.com" && host != "objects.githubusercontent.com" {
		return errors.New("unexpected release redirect host")
	}
	return nil
}
func Resolve(ctx context.Context, channel string) (Release, error) {
	c, err := officialClient()
	if err != nil {
		return Release{}, err
	}
	return c.Resolve(ctx, channel)
}
func DownloadAndExtract(ctx context.Context, r Release, dest string) error {
	c, err := officialClient()
	if err != nil {
		return err
	}
	return c.DownloadAndExtract(ctx, r, dest)
}
func (c *Client) fetch(ctx context.Context, address string, limit int64) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, address, nil)
	if err != nil {
		return nil, errors.New("invalid release address")
	}
	req.Header.Set("Accept", "application/octet-stream")
	req.Header.Set("User-Agent", "railwait-cli")
	response, err := c.HTTP.Do(req)
	if err != nil {
		return nil, errors.New("릴리스 서버에 연결하지 못했습니다")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("릴리스 조회 실패 (HTTP %d)", response.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, limit+1))
	if err != nil {
		return nil, errors.New("릴리스 다운로드 실패")
	}
	if int64(len(data)) > limit {
		return nil, errors.New("release response exceeds size limit")
	}
	return data, nil
}
func strictJSON(data []byte, value any) error {
	d := json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	if err := d.Decode(value); err != nil {
		return errors.New("invalid release metadata")
	}
	if d.Decode(new(any)) != io.EOF {
		return errors.New("trailing release metadata")
	}
	return nil
}
func (c *Client) Resolve(ctx context.Context, channel string) (Release, error) {
	if channel != "stable" {
		return Release{}, errors.New("안정 릴리스만 지원합니다")
	}
	data, err := c.fetch(ctx, "https://api.github.com/repos/"+repository+"/releases/latest", 1<<20)
	if err != nil {
		return Release{}, err
	}
	var listing struct {
		Tag        string `json:"tag_name"`
		Draft      bool   `json:"draft"`
		Prerelease bool   `json:"prerelease"`
		Assets     []struct {
			Name string `json:"name"`
			URL  string `json:"browser_download_url"`
		} `json:"assets"`
	}
	if err = json.Unmarshal(data, &listing); err != nil || listing.Draft || listing.Prerelease || !strings.HasPrefix(listing.Tag, "v") || !versionPattern.MatchString(strings.TrimPrefix(listing.Tag, "v")) {
		return Release{}, errors.New("invalid stable release")
	}
	base := "https://github.com/" + repository + "/releases/download/" + listing.Tag + "/"
	assets := map[string]bool{}
	for _, a := range listing.Assets {
		if a.URL == base+a.Name {
			if assets[a.Name] {
				return Release{}, errors.New("duplicate release asset")
			}
			assets[a.Name] = true
		}
	}
	for _, name := range []string{"release-manifest.json", "release-manifest.sig", "railwait-bundle.tar.gz"} {
		if !assets[name] {
			return Release{}, errors.New("설치 가능한 서명된 릴리스 자산이 아직 준비되지 않았습니다")
		}
	}
	metadata, err := c.fetch(ctx, base+"release-manifest.json", 128<<10)
	if err != nil {
		return Release{}, err
	}
	signature, err := c.fetch(ctx, base+"release-manifest.sig", 256)
	if err != nil {
		return Release{}, err
	}
	m, err := c.verify(metadata, signature)
	if err != nil {
		return Release{}, err
	}
	if "v"+m.Version != listing.Tag || m.Bundle.URL != base+"railwait-bundle.tar.gz" {
		return Release{}, errors.New("release identity mismatch")
	}
	return Release{Version: m.Version, BundleURL: m.Bundle.URL, SHA256: m.Bundle.SHA256, ManifestURL: base + "release-manifest.json", DatabaseRevision: m.DatabaseRevision, SettingsSchema: m.SettingsSchema, CompatibleFrom: m.CompatibleFrom, Images: m.Images, ServiceProfiles: m.ServiceProfiles, manifest: metadata, signature: signature}, nil
}
func (c *Client) verify(metadata, signature []byte) (Manifest, error) {
	var m Manifest
	sig, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(signature)))
	if err != nil || len(c.PublicKey) != ed25519.PublicKeySize || !ed25519.Verify(c.PublicKey, metadata, sig) {
		return m, errors.New("릴리스 서명 검증에 실패했습니다")
	}
	if err = strictJSON(metadata, &m); err != nil {
		return m, err
	}
	if m.SchemaVersion != 1 || m.Channel != "stable" || !versionPattern.MatchString(m.Version) || !versionPattern.MatchString(m.MinimumCLIVersion) || !revisionPattern.MatchString(m.SourceRevision) || !digestPattern.MatchString(m.Bundle.SHA256) || m.Bundle.SizeBytes <= 0 || m.Bundle.SizeBytes > maxBundle || len(m.Images) == 0 {
		return m, errors.New("invalid release contract")
	}
	if versionLess(c.CLIVersion, m.MinimumCLIVersion) {
		return m, errors.New("먼저 최신 공식 CLI로 업데이트하세요")
	}
	if !regexp.MustCompile(`^[a-zA-Z0-9_]{1,64}$`).MatchString(m.DatabaseRevision) || m.SettingsSchema != 1 {
		return m, errors.New("unsupported database/settings release contract")
	}
	for _, source := range m.CompatibleFrom {
		if !versionPattern.MatchString(source) || !versionLess(source, m.Version) {
			return m, errors.New("invalid compatible source version")
		}
	}
	if len(m.ServiceProfiles) != len(m.Images) {
		return m, errors.New("incomplete service profile contract")
	}
	for name, profiles := range m.ServiceProfiles {
		if _, ok := m.Images[name]; !ok {
			return m, errors.New("unknown profile service")
		}
		for _, profile := range profiles {
			if !regexp.MustCompile(`^[a-z][a-z0-9-]*$`).MatchString(profile) {
				return m, errors.New("invalid service profile")
			}
		}
	}
	u, err := url.Parse(m.Bundle.URL)
	if err != nil || u.Scheme != "https" || u.Host != "github.com" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "/"+repository+"/releases/download/v"+m.Version+"/railwait-bundle.tar.gz" {
		return m, errors.New("invalid bundle origin")
	}
	compatible := false
	for _, p := range m.Platforms {
		if p == "linux/"+runtime.GOARCH {
			compatible = true
		}
	}
	if !compatible {
		return m, errors.New("이 CPU를 지원하는 릴리스 이미지가 없습니다")
	}
	for _, reference := range m.Images {
		if !imagePattern.MatchString(reference) {
			return m, errors.New("release image is not pinned to a digest")
		}
	}
	return m, nil
}
func versionLess(a, b string) bool {
	if !versionPattern.MatchString(a) {
		return true
	}
	aa, bb := strings.Split(a, "."), strings.Split(b, ".")
	for n := 0; n < 3; n++ {
		if len(aa[n]) != len(bb[n]) {
			return len(aa[n]) < len(bb[n])
		}
		if aa[n] != bb[n] {
			return aa[n] < bb[n]
		}
	}
	return false
}
func (c *Client) DownloadAndExtract(ctx context.Context, r Release, dest string) error {
	m, err := c.verify(r.manifest, r.signature)
	if err != nil {
		return err
	}
	if r.Version != m.Version || r.BundleURL != m.Bundle.URL || r.SHA256 != m.Bundle.SHA256 {
		return errors.New("release metadata changed")
	}
	data, err := c.fetch(ctx, m.Bundle.URL, m.Bundle.SizeBytes)
	if err != nil {
		return err
	}
	sum := sha256.Sum256(data)
	if int64(len(data)) != m.Bundle.SizeBytes || hex.EncodeToString(sum[:]) != m.Bundle.SHA256 {
		return errors.New("릴리스 파일 무결성 검사 실패")
	}
	return extract(data, dest)
}
func extract(data []byte, dest string) error {
	info, err := os.Lstat(dest)
	if err != nil {
		return err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return errors.New("bundle destination is not a directory")
	}
	entries, err := os.ReadDir(dest)
	if err != nil {
		return err
	}
	if len(entries) != 0 {
		return errors.New("bundle destination must be empty")
	}
	gz, err := gzip.NewReader(bytes.NewReader(data))
	if err != nil {
		return errors.New("invalid release archive")
	}
	defer gz.Close()
	tr := tar.NewReader(io.LimitReader(gz, maxBundle+1))
	seen := map[string]bool{}
	var total int64
	for count := 0; ; count++ {
		h, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return errors.New("invalid release archive")
		}
		if count >= 256 {
			return errors.New("too many bundle files")
		}
		name := strings.TrimSuffix(h.Name, "/")
		if name == "" || path.Clean(name) != name || strings.HasPrefix(name, "/") || strings.ContainsAny(name, "\\:\x00") || name == ".." || strings.HasPrefix(name, "../") || seen[name] {
			return errors.New("unsafe bundle path")
		}
		if name != "compose.release.yml" && name != ".env.example" && name != "infra" && !strings.HasPrefix(name, "infra/") {
			return errors.New("unexpected bundle file")
		}
		seen[name] = true
		if h.Typeflag != tar.TypeReg && h.Typeflag != tar.TypeDir {
			return errors.New("archive links and special files are forbidden")
		}
		if h.Size < 0 || h.Size > 8<<20 {
			return errors.New("bundle file too large")
		}
		total += h.Size
		if total > maxBundle {
			return errors.New("bundle too large")
		}
		output := filepath.Join(dest, filepath.FromSlash(name))
		if h.Typeflag == tar.TypeDir {
			if err = os.MkdirAll(output, 0755); err != nil {
				return err
			}
			continue
		}
		if err = os.MkdirAll(filepath.Dir(output), 0755); err != nil {
			return err
		}
		f, err := os.OpenFile(output, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
		if err != nil {
			return err
		}
		_, copyErr := io.CopyN(f, tr, h.Size)
		closeErr := f.Close()
		if copyErr != nil {
			return copyErr
		}
		if closeErr != nil {
			return closeErr
		}
	}
	// Read through gzip EOF to verify its checksum even when tar ended earlier.
	if _, err = io.Copy(io.Discard, io.LimitReader(gz, maxBundle-total+1)); err != nil {
		return errors.New("invalid archive checksum")
	}
	if !seen["compose.release.yml"] || !seen[".env.example"] {
		return errors.New("required bundle files missing")
	}
	return nil
}
