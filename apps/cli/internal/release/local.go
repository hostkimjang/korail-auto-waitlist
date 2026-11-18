package release

import (
	"archive/tar"
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
)

type LocalArtifact struct {
	File      string `json:"file"`
	SHA256    string `json:"sha256"`
	SizeBytes int64  `json:"size_bytes"`
}
type LocalManifest struct {
	SchemaVersion     int                 `json:"schema_version"`
	Kind              string              `json:"kind"`
	Version           string              `json:"version"`
	SourceRevision    string              `json:"source_revision"`
	SourceTreeSHA256  string              `json:"source_tree_sha256"`
	SourceDirty       bool                `json:"source_dirty"`
	Platforms         []string            `json:"platforms"`
	MinimumCLIVersion string              `json:"minimum_cli_version"`
	DatabaseRevision  string              `json:"database_revision"`
	SettingsSchema    int                 `json:"settings_schema"`
	Bundle            LocalArtifact       `json:"bundle"`
	ImageArchive      LocalArtifact       `json:"image_archive"`
	Images            map[string]string   `json:"images"`
	ImageConfigIDs    map[string]string   `json:"image_config_ids"`
	ServiceProfiles   map[string][]string `json:"service_profiles"`
}

// LocalSource is deliberately separate from the official network resolver.
type LocalSource struct {
	Dir      string
	Manifest LocalManifest
}

func regularFile(path string) (*os.File, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, errors.New("package assets must be regular files")
	}
	return os.Open(path)
}
func boundedLocal(path string, limit int64) ([]byte, error) {
	f, err := regularFile(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > limit {
		return nil, errors.New("package metadata exceeds limit")
	}
	return data, nil
}

func OpenLocal(ctx context.Context, dir, keyFile string) (*LocalSource, error) {
	return openLocal(ctx, dir, keyFile, true)
}

// OpenLocalMetadata verifies the durable signed identity for read-only status.
// Large archive integrity is checked again before any load or extraction.
func OpenLocalMetadata(ctx context.Context, dir, keyFile string) (*LocalSource, error) {
	return openLocal(ctx, dir, keyFile, false)
}
func openLocal(ctx context.Context, dir, keyFile string, assets bool) (*LocalSource, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	info, err := os.Lstat(dir)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("package must be a real directory")
	}
	keyData, err := boundedLocal(keyFile, 1024)
	if err != nil {
		return nil, err
	}
	key, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(keyData)))
	if err != nil || len(key) != ed25519.PublicKeySize {
		return nil, errors.New("로컬 패키지 신뢰 공개키가 잘못되었습니다")
	}
	metadata, err := boundedLocal(filepath.Join(dir, "local-manifest.json"), 128<<10)
	if err != nil {
		return nil, err
	}
	signature, err := boundedLocal(filepath.Join(dir, "local-manifest.sig"), 256)
	if err != nil {
		return nil, err
	}
	sig, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(signature)))
	if err != nil || !ed25519.Verify(key, metadata, sig) {
		return nil, errors.New("로컬 패키지 서명 검증에 실패했습니다")
	}
	var m LocalManifest
	if err = strictJSON(metadata, &m); err != nil {
		return nil, err
	}
	if m.SchemaVersion != 1 || m.Kind != "local_package" || !versionPattern.MatchString(m.Version) || !versionPattern.MatchString(m.MinimumCLIVersion) || versionLess(CLIVersion, m.MinimumCLIVersion) || !revisionPattern.MatchString(m.SourceRevision) || !digestPattern.MatchString(m.SourceTreeSHA256) || m.SettingsSchema != 1 || !regexp.MustCompile(`^[a-zA-Z0-9_]{1,64}$`).MatchString(m.DatabaseRevision) || len(m.Images) == 0 || len(m.Images) != len(m.ServiceProfiles) || len(m.Images) != len(m.ImageConfigIDs) {
		return nil, errors.New("invalid local package contract")
	}
	compatible := false
	for _, target := range m.Platforms {
		if target == "linux/"+runtime.GOARCH {
			compatible = true
		}
	}
	if !compatible {
		return nil, errors.New("로컬 패키지의 CPU가 현재 Docker 대상과 다릅니다")
	}
	for name, image := range m.Images {
		if !regexp.MustCompile(`^[a-z][a-z0-9-]*$`).MatchString(name) || !regexp.MustCompile(`^sha256:[a-f0-9]{64}$`).MatchString(image) {
			return nil, errors.New("local image must use its immutable config ID")
		}
		if !regexp.MustCompile(`^sha256:[a-f0-9]{64}$`).MatchString(m.ImageConfigIDs[name]) {
			return nil, errors.New("missing local image config identity")
		}
		if _, ok := m.ServiceProfiles[name]; !ok {
			return nil, errors.New("missing local service profile")
		}
		for _, profile := range m.ServiceProfiles[name] {
			if !regexp.MustCompile(`^[a-z][a-z0-9-]*$`).MatchString(profile) {
				return nil, errors.New("invalid local service profile")
			}
		}
	}
	if m.Bundle.File != "railwait-bundle.tar.gz" || m.ImageArchive.File != "railwait-images.tar" {
		return nil, errors.New("invalid local artifact filename")
	}
	for _, artifact := range []LocalArtifact{m.Bundle, m.ImageArchive} {
		if artifact.SizeBytes <= 0 || artifact.SizeBytes > 64<<30 || !digestPattern.MatchString(artifact.SHA256) {
			return nil, errors.New("invalid signed artifact contract")
		}
	}
	s := &LocalSource{Dir: dir, Manifest: m}
	if assets {
		if err = s.VerifyAssets(ctx); err != nil {
			return nil, err
		}
	}
	return s, nil
}

func (s *LocalSource) VerifyAssets(ctx context.Context) error {
	for _, artifact := range []LocalArtifact{s.Manifest.Bundle, s.Manifest.ImageArchive} {
		if artifact.SizeBytes <= 0 || !digestPattern.MatchString(artifact.SHA256) || (artifact.File == "railwait-bundle.tar.gz" && artifact.SizeBytes > maxBundle) || artifact.SizeBytes > 64<<30 {
			return errors.New("invalid package artifact size")
		}
		f, err := regularFile(filepath.Join(s.Dir, artifact.File))
		if err != nil {
			return err
		}
		sum := sha256.New()
		written, copyErr := io.Copy(sum, &contextReader{ctx: ctx, reader: io.LimitReader(f, artifact.SizeBytes+1)})
		closeErr := f.Close()
		if err = errors.Join(copyErr, closeErr); err != nil {
			return err
		}
		if written != artifact.SizeBytes || hex.EncodeToString(sum.Sum(nil)) != artifact.SHA256 {
			return fmt.Errorf("로컬 파일 무결성 검사 실패: %s", artifact.File)
		}
	}
	return verifyImageArchive(ctx, filepath.Join(s.Dir, s.Manifest.ImageArchive.File), s.Manifest.Images, s.Manifest.ImageConfigIDs)
}

type contextReader struct {
	ctx    context.Context
	reader io.Reader
}

func (r *contextReader) Read(p []byte) (int, error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	return r.reader.Read(p)
}

func (s *LocalSource) Resolve(ctx context.Context, channel string) (Release, error) {
	if err := ctx.Err(); err != nil {
		return Release{}, err
	}
	m := s.Manifest
	return Release{Version: m.Version, SHA256: m.Bundle.SHA256, Images: m.Images, ServiceProfiles: m.ServiceProfiles, DatabaseRevision: m.DatabaseRevision, SettingsSchema: m.SettingsSchema}, nil
}
func (s *LocalSource) DownloadAndExtract(ctx context.Context, r Release, dest string) error {
	if err := s.VerifyAssets(ctx); err != nil {
		return err
	}
	if r.Version != s.Manifest.Version || r.SHA256 != s.Manifest.Bundle.SHA256 {
		return errors.New("local release changed")
	}
	data, err := boundedLocal(filepath.Join(s.Dir, s.Manifest.Bundle.File), maxBundle)
	if err != nil {
		return err
	}
	return extract(data, dest)
}

// Docker save archives must be untagged: loading may add immutable image IDs,
// but must never overwrite an existing production image tag.
func verifyImageArchive(ctx context.Context, filename string, expected, configs map[string]string) error {
	f, err := regularFile(filename)
	if err != nil {
		return err
	}
	defer f.Close()
	tr := tar.NewReader(&contextReader{ctx: ctx, reader: f})
	hashes := map[string]string{}
	seen := map[string]bool{}
	var metadata []byte
	var indexData []byte
	blobs := map[string][]byte{}
	var metadataBytes int64
	for count := 0; ; count++ {
		h, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		name := strings.TrimSuffix(h.Name, "/")
		if count > 65536 || name == "" || path.Clean(name) != name || strings.HasPrefix(name, "/") || strings.HasPrefix(name, "../") || name == ".." || strings.ContainsAny(name, "\\:\x00") || seen[name] {
			return errors.New("unsafe Docker archive path")
		}
		seen[name] = true
		if h.Typeflag == tar.TypeDir {
			continue
		}
		if h.Typeflag != tar.TypeReg || h.Size < 0 || h.Size > 64<<30 {
			return errors.New("Docker archive links or special files are forbidden")
		}
		if name == "manifest.json" || name == "index.json" {
			data, readErr := io.ReadAll(io.LimitReader(tr, 16<<20))
			if name == "manifest.json" {
				metadata = data
			} else {
				indexData = data
			}
			if readErr != nil || int64(len(data)) != h.Size {
				return errors.New("invalid Docker archive manifest")
			}
		} else {
			sum := sha256.New()
			var small []byte
			if h.Size <= 16<<20 {
				small, err = io.ReadAll(io.TeeReader(tr, sum))
			} else {
				_, err = io.Copy(sum, tr)
			}
			if err != nil {
				return err
			}
			digest := hex.EncodeToString(sum.Sum(nil))
			hashes[name] = digest
			if strings.HasPrefix(name, "blobs/sha256/") {
				if strings.TrimPrefix(name, "blobs/sha256/") != digest {
					return errors.New("OCI image blob checksum mismatch")
				}
			}
			if small != nil && json.Valid(small) {
				metadataBytes += int64(len(small))
				if metadataBytes > 128<<20 {
					return errors.New("OCI JSON metadata exceeds memory bound")
				}
				blobs["sha256:"+digest] = small
			}
		}
	}
	var entries []struct {
		Config   string
		RepoTags []string
		Layers   []string
	}
	if json.Unmarshal(metadata, &entries) != nil || len(entries) == 0 {
		return errors.New("Docker save manifest.json is required")
	}
	want := map[string]bool{}
	for _, id := range configs {
		want[id] = true
	}
	found := map[string]bool{}
	for _, entry := range entries {
		if len(entry.RepoTags) != 0 {
			return errors.New("로컬 이미지 묶음은 기존 태그를 바꿀 수 없습니다; 태그 없는 image ID로 내보내세요")
		}
		id := "sha256:" + hashes[entry.Config]
		if !want[id] || found[id] {
			return errors.New("Docker archive config IDs do not match the signed manifest")
		}
		found[id] = true
		for _, layer := range entry.Layers {
			if !seen[layer] {
				return errors.New("Docker archive layer is missing")
			}
		}
	}
	if len(found) != len(want) {
		return errors.New("signed image is missing from the Docker archive")
	}
	if err = verifyOCIRoots(indexData, blobs, expected, configs); err != nil {
		return err
	}
	return nil
}

type localDescriptor struct {
	MediaType string `json:"mediaType"`
	Digest    string `json:"digest"`
	Platform  struct {
		OS           string `json:"os"`
		Architecture string `json:"architecture"`
	} `json:"platform"`
	Annotations map[string]string `json:"annotations"`
}

func verifyOCIRoots(indexData []byte, blobs map[string][]byte, images, configs map[string]string) error {
	roots := map[string]bool{}
	if len(indexData) > 0 {
		var index struct {
			Manifests []localDescriptor `json:"manifests"`
		}
		if json.Unmarshal(indexData, &index) != nil {
			return errors.New("invalid OCI archive index")
		}
		wanted := map[string]bool{}
		for _, id := range images {
			wanted[id] = true
		}
		for _, descriptor := range index.Manifests {
			if !wanted[descriptor.Digest] {
				return errors.New("unsigned OCI archive image root")
			}
			for key, value := range descriptor.Annotations {
				if (key == "io.containerd.image.name" || key == "org.opencontainers.image.ref.name") && value != "" && value != descriptor.Digest && value != "moby-dangling@"+descriptor.Digest {
					return errors.New("OCI archive must not introduce image tags")
				}
			}
			roots[descriptor.Digest] = true
		}
	}
	for service, id := range images {
		config := configs[service]
		if id == config && len(indexData) == 0 {
			data := blobs[config]
			if data == nil {
				return errors.New("classic image config metadata is missing")
			}
			var cfg struct {
				OS           string `json:"os"`
				Architecture string `json:"architecture"`
			}
			if json.Unmarshal(data, &cfg) != nil || cfg.OS != "linux" || cfg.Architecture != runtime.GOARCH {
				return errors.New("classic image config platform mismatch")
			}
			continue
		}
		if !roots[id] {
			return errors.New("signed OCI image root is missing")
		}
		if !ociConfigMatches(id, config, blobs, 0) {
			return errors.New("OCI image root/config/platform identities do not match")
		}
	}
	return nil
}
func ociConfigMatches(id, config string, blobs map[string][]byte, depth int) bool {
	if depth > 4 {
		return false
	}
	data, ok := blobs[id]
	if !ok {
		return false
	}
	var document struct {
		Manifests []localDescriptor `json:"manifests"`
		Config    localDescriptor   `json:"config"`
	}
	if json.Unmarshal(data, &document) != nil {
		return false
	}
	if document.Config.Digest != "" {
		if document.Config.Digest != config {
			return false
		}
		var cfg struct {
			OS           string `json:"os"`
			Architecture string `json:"architecture"`
		}
		return json.Unmarshal(blobs[config], &cfg) == nil && cfg.OS == "linux" && cfg.Architecture == runtime.GOARCH
	}
	matched := 0
	valid := false
	for _, descriptor := range document.Manifests {
		if descriptor.Platform.OS == "linux" && descriptor.Platform.Architecture == runtime.GOARCH {
			matched++
			valid = ociConfigMatches(descriptor.Digest, config, blobs, depth+1)
		}
	}
	return matched == 1 && valid
}
