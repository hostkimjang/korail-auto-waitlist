package install

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/release"
	"gopkg.in/yaml.v3"
)

func importLocalPackage(ctx context.Context, installation, source, key string) (*release.LocalSource, error) {
	verified, err := release.OpenLocalMetadata(ctx, source, key)
	if err != nil {
		return nil, err
	}
	dest := filepath.Join(installation, ".railwait", "local-package")
	if _, err = os.Lstat(dest); err == nil {
		existing, err := release.OpenLocal(ctx, dest, filepath.Join(dest, "trust-key.pub"))
		if err != nil {
			return nil, err
		}
		if !reflect.DeepEqual(existing.Manifest, verified.Manifest) {
			return nil, fmt.Errorf("다른 로컬 패키지가 이미 보존되어 있습니다")
		}
		return existing, nil
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	_, staging, err := stageLocalPackage(ctx, installation, source, key, ".local-package-*")
	if err != nil {
		return nil, err
	}
	if err = os.Rename(staging, dest); err != nil {
		return nil, errors.Join(err, os.RemoveAll(staging))
	}
	return release.OpenLocal(ctx, dest, filepath.Join(dest, "trust-key.pub"))
}

// stageLocalPackage copies a verified package and the explicitly selected public key
// into a new private directory under .railwait and verifies the copy again. The caller
// renames the directory into place or removes it.
func stageLocalPackage(ctx context.Context, installation, source, key, pattern string) (*release.LocalSource, string, error) {
	verified, err := release.OpenLocalMetadata(ctx, source, key)
	if err != nil {
		return nil, "", err
	}
	if err = checkLocalPackageSpace(installation, verified.Manifest); err != nil {
		return nil, "", err
	}
	if err = verified.VerifyAssets(ctx); err != nil {
		return nil, "", err
	}
	staging, err := os.MkdirTemp(filepath.Join(installation, ".railwait"), pattern)
	if err != nil {
		return nil, "", err
	}
	staged, err := copyLocalPackage(ctx, verified, source, key, staging)
	if err != nil {
		return nil, "", errors.Join(err, os.RemoveAll(staging))
	}
	return staged, staging, nil
}

func copyLocalPackage(ctx context.Context, verified *release.LocalSource, source, key, staging string) (*release.LocalSource, error) {
	for _, name := range []string{"local-manifest.json", "local-manifest.sig", "railwait-bundle.tar.gz", "railwait-images.tar", "trust-key.pub"} {
		path := filepath.Join(source, name)
		if name == "trust-key.pub" {
			path = key
		}
		input, err := os.Open(path)
		if err != nil {
			return nil, err
		}
		output, err := os.OpenFile(filepath.Join(staging, name), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if err != nil {
			input.Close()
			return nil, err
		}
		limit := int64(128 << 10)
		switch name {
		case "railwait-bundle.tar.gz":
			limit = verified.Manifest.Bundle.SizeBytes
		case "railwait-images.tar":
			limit = verified.Manifest.ImageArchive.SizeBytes
		case "trust-key.pub":
			limit = 1024
		case "local-manifest.sig":
			limit = 256
		}
		written, copyErr := io.Copy(output, &localContextReader{ctx, io.LimitReader(input, limit+1)})
		if written > limit {
			copyErr = errors.Join(copyErr, fmt.Errorf("패키지 파일 크기가 계약을 넘었습니다"))
		}
		err = errors.Join(copyErr, output.Sync(), output.Close(), input.Close())
		if err != nil {
			return nil, err
		}
	}
	staged, err := release.OpenLocal(ctx, staging, filepath.Join(staging, "trust-key.pub"))
	if err != nil {
		return nil, err
	}
	if !reflect.DeepEqual(staged.Manifest, verified.Manifest) {
		return nil, fmt.Errorf("복사한 로컬 패키지가 검증한 패키지와 다릅니다")
	}
	return staged, nil
}

type localContextReader struct {
	ctx    context.Context
	reader io.Reader
}

func (r *localContextReader) Read(p []byte) (int, error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	return r.reader.Read(p)
}

func validateLocalCompose(dir string, images map[string]string) error {
	data, err := os.ReadFile(filepath.Join(dir, "compose.release.yml"))
	if err != nil {
		return err
	}
	var document struct {
		Services map[string]struct {
			Image         string `yaml:"image"`
			PullPolicy    string `yaml:"pull_policy"`
			Build         any    `yaml:"build"`
			ContainerName string `yaml:"container_name"`
		} `yaml:"services"`
		Volumes  map[string]map[string]any `yaml:"volumes"`
		Networks map[string]map[string]any `yaml:"networks"`
	}
	if yaml.Unmarshal(data, &document) != nil || len(document.Services) != len(images) {
		return fmt.Errorf("로컬 Compose 서비스 계약이 다릅니다")
	}
	for name, service := range document.Services {
		if service.Image != images[name] || service.PullPolicy != "never" || service.Build != nil || service.ContainerName != "" {
			return fmt.Errorf("로컬 Compose는 image ID·pull_policy never와 독립 서비스만 허용합니다")
		}
	}
	for _, resources := range []map[string]map[string]any{document.Volumes, document.Networks} {
		for _, options := range resources {
			if _, ok := options["name"]; ok {
				return fmt.Errorf("로컬 패키지의 고정 자원 이름은 허용하지 않습니다")
			}
			if external, ok := options["external"]; ok && external != false {
				return fmt.Errorf("외부 Docker 자원을 공유하지 않습니다")
			}
		}
	}
	return nil
}

func (i *Installer) loadLocalImages(ctx context.Context, s State, source *release.LocalSource) error {
	if err := source.VerifyAssets(ctx); err != nil {
		return err
	}
	f, err := os.Open(filepath.Join(source.Dir, source.Manifest.ImageArchive.File))
	if err != nil {
		return err
	}
	_, loadErr := i.docker(ctx, s, f, "image", "load", "--quiet")
	err = errors.Join(loadErr, f.Close())
	if err != nil {
		return fmt.Errorf("검증한 로컬 이미지를 불러오지 못했습니다: %w", err)
	}
	ids := []string{}
	seen := map[string]bool{}
	for _, id := range source.Manifest.Images {
		if !seen[id] {
			ids = append(ids, id)
			seen[id] = true
		}
	}
	sort.Strings(ids)
	out, err := i.docker(ctx, s, nil, append([]string{"image", "inspect", "--format", `{{json .Id}}`}, ids...)...)
	if err != nil {
		return err
	}
	lines := strings.Fields(string(out))
	if len(lines) != len(ids) {
		return fmt.Errorf("로드된 이미지 수가 다릅니다")
	}
	for n, line := range lines {
		var id string
		if json.Unmarshal([]byte(line), &id) != nil || id != ids[n] {
			return fmt.Errorf("로드된 이미지 ID가 서명한 패키지와 다릅니다")
		}
	}
	return nil
}

func verifyLocalState(ctx context.Context, s State) (err error) {
	if s.ReleaseSource != "local_package" {
		return nil
	}
	defer func() {
		if err != nil {
			err = withInterruptedLocalUpdateHint(s.Path, err)
		}
	}()
	source, err := release.OpenLocalMetadata(ctx, filepath.Join(s.Path, ".railwait", "local-package"), filepath.Join(s.Path, ".railwait", "local-package", "trust-key.pub"))
	if err != nil {
		return err
	}
	if source.Manifest.Version != s.Release {
		return fmt.Errorf("보존한 로컬 패키지 버전이 다릅니다")
	}
	for name, id := range s.ExpectedImages {
		if source.Manifest.Images[name] != id {
			return fmt.Errorf("설치 이미지가 서명한 패키지와 다릅니다")
		}
	}
	return nil
}
