package install

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/manager"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/release"
)

type ReleaseSource interface {
	Resolve(context.Context, string) (release.Release, error)
	DownloadAndExtract(context.Context, release.Release, string) error
}
type OfficialReleases struct{}

func (OfficialReleases) Resolve(ctx context.Context, channel string) (release.Release, error) {
	return release.Resolve(ctx, channel)
}
func (OfficialReleases) DownloadAndExtract(ctx context.Context, r release.Release, dest string) error {
	return release.DownloadAndExtract(ctx, r, dest)
}

type Credentials struct {
	Username string `json:"username"`
	Password string `json:"password"`
}
type Options struct {
	Path, DockerContext, TAGOKey string
	PackagePath, TrustKey        string
	Port                         int
	Push                         PushOptions
	Secrets                      map[string]string
	Credentials                  func() (Credentials, error)
}
type Installer struct {
	Runner   Runner
	Releases ReleaseSource
	Progress func(string)
	// CheckPort defaults to a real loopback bind; tests can isolate host resources.
	CheckPort     func(int) error
	CheckSpace    func(string) error
	ProbeInterval time.Duration
}

func (i *Installer) stage(s *State, stage string) error {
	s.Stage = stage
	s.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	if err := writeState(*s); err != nil {
		return err
	}
	if i.Progress != nil {
		i.Progress(stage)
	}
	return nil
}

// Install only owns resources recorded in its new installation directory. It never
// removes volumes or changes an existing source installation.
func (i *Installer) Install(ctx context.Context, o Options) (State, error) {
	var s State
	if i.Runner == nil || i.Releases == nil {
		return s, fmt.Errorf("installer dependencies are required")
	}
	if (o.PackagePath == "") != (o.TrustKey == "") {
		return s, fmt.Errorf("--package와 --trust-key를 함께 지정하세요")
	}
	if err := ValidateInitialSecrets(o.Secrets); err != nil {
		return s, err
	}
	if err := ValidatePushOptions(o.Push); err != nil {
		return s, err
	}
	requestedPort := o.Port
	requestedContext := o.DockerContext
	if o.Port == 0 {
		o.Port = 8080
	}
	if o.Port < 1 || o.Port > 65535 {
		return s, fmt.Errorf("포트는 1~65535여야 합니다")
	}
	if o.DockerContext == "" {
		o.DockerContext = "default"
	}
	abs, err := filepath.Abs(o.Path)
	if err != nil {
		return s, err
	}
	o.Path = abs
	if info, err := os.Lstat(abs); err == nil {
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return s, fmt.Errorf("설치 경로는 실제 디렉터리여야 합니다")
		}
	} else if !os.IsNotExist(err) {
		return s, err
	}
	entries, err := os.ReadDir(abs)
	if err != nil && !os.IsNotExist(err) {
		return s, err
	}
	_, stateErr := os.Stat(statePath(abs))
	resume := stateErr == nil
	if !resume && len(entries) != 0 && !onlyUnfinishedLock(entries, abs) {
		return s, fmt.Errorf("기존 파일이 있습니다; install 대신 adopt를 사용하세요")
	}
	if err = platform.SecureDir(abs); err != nil {
		return s, err
	}
	if err = platform.SecureDir(filepath.Join(abs, ".railwait")); err != nil {
		return s, err
	}
	lock, err := platform.AcquireLock(filepath.Join(abs, ".railwait", "install.lock"))
	if err != nil {
		return s, err
	}
	defer lock.Close()
	// Another installer may have finished before we acquired the kernel lock.
	if _, statErr := os.Stat(statePath(abs)); statErr == nil {
		resume = true
	}
	if resume {
		s, err = ReadState(abs)
		if err != nil {
			return s, err
		}
		if (requestedContext != "" && s.DockerContext != o.DockerContext) || (requestedPort != 0 && s.Port != o.Port) {
			return s, fmt.Errorf("재개 시 기록된 Docker context와 포트를 유지하세요")
		}
	} else {
		raw := make([]byte, 12)
		if _, err = rand.Read(raw); err != nil {
			return s, err
		}
		s = State{Schema: 1, ID: hex.EncodeToString(raw), Path: abs, DockerContext: o.DockerContext, Port: o.Port, Profiles: []string{}}
		s.Project = "railwait-" + s.ID
	}
	if !resume {
		if err = i.stage(&s, "created"); err != nil {
			return s, err
		}
	}
	if err = i.preflight(ctx, &s); err != nil {
		return s, err
	}
	spaceCheck := i.CheckSpace
	if spaceCheck == nil {
		spaceCheck = checkSpace
	}
	if err = spaceCheck(abs); err != nil {
		return s, err
	}
	var local *release.LocalSource
	if s.ReleaseSource == "local_package" {
		local, err = release.OpenLocal(ctx, filepath.Join(abs, ".railwait", "local-package"), filepath.Join(abs, ".railwait", "local-package", "trust-key.pub"))
		if err != nil {
			return s, err
		}
		if o.PackagePath != "" {
			supplied, openErr := release.OpenLocal(ctx, o.PackagePath, o.TrustKey)
			if openErr != nil {
				return s, openErr
			}
			if supplied.Manifest.Bundle.SHA256 != local.Manifest.Bundle.SHA256 || supplied.Manifest.ImageArchive.SHA256 != local.Manifest.ImageArchive.SHA256 {
				return s, fmt.Errorf("기록된 로컬 설치 패키지와 다릅니다")
			}
		}
	} else if o.PackagePath != "" {
		if resume && s.Stage != "created" {
			return s, fmt.Errorf("기존 공식 설치를 로컬 패키지로 바꾸지 않습니다")
		}
		if i.Progress != nil {
			i.Progress("로컬 패키지 서명·파일 검증 및 설치 경로에 보존 중")
		}
		local, err = importLocalPackage(ctx, abs, o.PackagePath, o.TrustKey)
		if err != nil {
			return s, err
		}
		s.ReleaseSource = "local_package"
		if err = i.stage(&s, s.Stage); err != nil {
			return s, err
		}
	}
	source := i.Releases
	if local != nil {
		source = local
	}
	if s.Stage == "created" {
		portCheck := i.CheckPort
		if portCheck == nil {
			portCheck = checkPort
		}
		if err = portCheck(s.Port); err != nil {
			return s, err
		}
		if err = i.rejectConflicts(ctx, s); err != nil {
			return s, err
		}
		if err = i.stage(&s, "created"); err != nil {
			return s, err
		}
	}
	_, resumeEnvErr := os.Lstat(filepath.Join(abs, ".env"))
	if resume && resumeEnvErr == nil && (len(o.Secrets) != 0 || o.Push.PublicKey != "") {
		data, readErr := os.ReadFile(filepath.Join(abs, ".env"))
		if readErr != nil {
			return s, readErr
		}
		values, parseErr := ParseEnv(string(data))
		if parseErr != nil {
			return s, parseErr
		}
		for key, supplied := range o.Secrets {
			if values[key] != supplied {
				return s, fmt.Errorf("기존 %s를 교체하지 않습니다", key)
			}
		}
		if o.Push.PublicKey != "" && (values["WEBPUSH_VAPID_PUBLIC_KEY"] != o.Push.PublicKey || values["WEBPUSH_VAPID_PRIVATE_KEY"] != o.Push.PrivateKey) {
			return s, fmt.Errorf("기존 VAPID 키를 교체하지 않습니다")
		}
	}
	if s.Stage == "complete" {
		status, err := i.Status(ctx, abs)
		if err != nil {
			return s, err
		}
		if !status.Healthy {
			return s, fmt.Errorf("기존 설치의 healthy 상태를 확인하지 못했습니다; railwait status를 확인하세요")
		}
		return s, nil
	}
	if s.Stage == "migrating" {
		return s, fmt.Errorf("migration 결과가 불명확합니다; 상태와 DB를 확인한 후 복구가 필요합니다")
	}
	if s.Stage == "created" || s.Stage == "fetching" {
		r, err := source.Resolve(ctx, "stable")
		if err != nil {
			return s, err
		}
		if r.Version == "" || strings.ContainsAny(r.Version, "/\\") || r.Version == "." || r.Version == ".." {
			return s, fmt.Errorf("잘못된 release version")
		}
		if s.Release != "" && s.Release != r.Version {
			return s, fmt.Errorf("중단 뒤 안정 릴리스가 변경되었습니다; 고정한 대상 %s의 복구가 필요합니다", s.Release)
		}
		s.Release = r.Version
		s.DatabaseRevision = r.DatabaseRevision
		s.SettingsSchema = r.SettingsSchema
		releasesDir := filepath.Join(abs, "releases")
		if err = platform.SecureDir(releasesDir); err != nil {
			return s, err
		}
		dest := filepath.Join(releasesDir, r.Version)
		if _, err = os.Stat(dest); err == nil {
			return s, fmt.Errorf("검증되지 않은 기존 릴리스 디렉터리가 있습니다")
		}
		staging := filepath.Join(releasesDir, ".download-"+s.ID)
		if s.Stage == "fetching" {
			if err = os.RemoveAll(staging); err != nil {
				return s, err
			}
		}
		if err = i.stage(&s, "fetching"); err != nil {
			return s, err
		}
		if err = os.Mkdir(staging, 0700); err != nil {
			return s, err
		}
		err = source.DownloadAndExtract(ctx, r, staging)
		if err != nil {
			return s, errors.Join(err, os.RemoveAll(staging))
		}
		template, err := os.ReadFile(filepath.Join(staging, ".env.example"))
		if err != nil {
			return s, errors.Join(err, os.RemoveAll(staging))
		}
		defaults, err := ParseEnv(string(template))
		if err != nil {
			return s, errors.Join(err, os.RemoveAll(staging))
		}
		s.Profiles = []string{}
		if selected := defaults["COMPOSE_PROFILES"]; selected != "" {
			if selected != "experimental-rail" {
				return s, errors.Join(fmt.Errorf("릴리스의 기본 프로필을 지원하지 않습니다"), os.RemoveAll(staging))
			}
			s.Profiles = []string{selected}
		}
		s.ExpectedImages = activeImages(r.Images, r.ServiceProfiles, s.Profiles)
		if local != nil {
			if err = validateLocalCompose(staging, local.Manifest.Images); err != nil {
				return s, errors.Join(err, os.RemoveAll(staging))
			}
		}
		if len(s.ExpectedImages) == 0 {
			return s, errors.Join(fmt.Errorf("릴리스에 실행할 서비스 이미지가 없습니다"), os.RemoveAll(staging))
		}
		s.BundleHashes, err = bundleHashes(staging)
		if err != nil {
			return s, errors.Join(err, os.RemoveAll(staging))
		}
		s.ComposeFile = filepath.Join(dest, "compose.release.yml")
		if err = i.stage(&s, "bundle_verified"); err != nil {
			return s, err
		}
	}
	if s.Stage == "bundle_verified" {
		dest := filepath.Dir(s.ComposeFile)
		staging := filepath.Join(abs, "releases", ".download-"+s.ID)
		if _, err = os.Lstat(dest); os.IsNotExist(err) {
			if err = verifyBundle(staging, s.BundleHashes); err != nil {
				return s, err
			}
			if err = os.Rename(staging, dest); err != nil {
				return s, err
			}
		} else if err != nil {
			return s, err
		}
		if err = verifyBundle(dest, s.BundleHashes); err != nil {
			return s, err
		}
		if err = i.stage(&s, "release_ready"); err != nil {
			return s, err
		}
	}
	if err = verifyBundle(filepath.Dir(s.ComposeFile), s.BundleHashes); err != nil {
		return s, err
	}
	if s.Stage == "release_ready" {
		if _, err = os.Lstat(filepath.Join(abs, ".env")); err == nil {
			if err = platform.SecureFile(filepath.Join(abs, ".env")); err != nil {
				return s, err
			}
			data, readErr := os.ReadFile(filepath.Join(abs, ".env"))
			if readErr != nil {
				return s, readErr
			}
			values, parseErr := ParseEnv(string(data))
			if parseErr != nil {
				return s, parseErr
			}
			if values["COMPOSE_PROJECT_NAME"] != s.Project {
				return s, fmt.Errorf("기존 .env의 설치 프로젝트가 일치하지 않습니다")
			}
			for _, key := range secretKeys {
				if values[key] == "" {
					return s, fmt.Errorf("기존 .env의 %s가 없습니다; 키 복구가 필요합니다", key)
				}
			}
			if err = i.stage(&s, "configured"); err != nil {
				return s, err
			}
		} else if !os.IsNotExist(err) {
			return s, err
		}
	}
	if s.Stage == "release_ready" {
		template, err := os.ReadFile(filepath.Join(filepath.Dir(s.ComposeFile), ".env.example"))
		if err != nil {
			return s, err
		}
		env, err := InitialEnvWithSetup(string(template), nil, s.Project, s.Port, o.TAGOKey, o.Secrets, o.Push)
		if err != nil {
			return s, err
		}
		env = append(env, []byte("RAILWAIT_INSTALL_DIR='"+filepath.ToSlash(abs)+"'\n")...)
		if strings.ContainsAny(abs, "'\r\n") {
			return s, fmt.Errorf("설치 경로에 작은따옴표나 줄바꿈을 사용할 수 없습니다")
		}
		if err = atomicWrite(filepath.Join(abs, ".env"), env); err != nil {
			return s, err
		}
		if err = os.MkdirAll(filepath.Join(abs, "logs"), 0755); err != nil {
			return s, err
		}
		if err = i.stage(&s, "configured"); err != nil {
			return s, err
		}
	}
	if _, err = os.Lstat(filepath.Join(abs, ".env")); err != nil {
		return s, fmt.Errorf("설정 파일이 없습니다; 기존 암호화키 복구가 필요합니다")
	}
	if err = platform.SecureFile(filepath.Join(abs, ".env")); err != nil {
		return s, err
	}
	data, err := os.ReadFile(filepath.Join(abs, ".env"))
	if err != nil {
		return s, err
	}
	values, err := ParseEnv(string(data))
	if err != nil {
		return s, err
	}
	for key, supplied := range o.Secrets {
		if values[key] != supplied {
			return s, fmt.Errorf("기존 %s를 교체하지 않습니다", key)
		}
	}
	for _, key := range secretKeys {
		if values[key] == "" {
			return s, fmt.Errorf("필수 키 %s가 없습니다; 새로 생성하지 않고 복구를 중지합니다", key)
		}
	}
	logs := filepath.Join(abs, "logs")
	if err = os.MkdirAll(logs, 0755); err != nil {
		return s, err
	}
	if info, err := os.Lstat(logs); err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return s, fmt.Errorf("로그 경로는 실제 디렉터리여야 합니다")
	}
	if _, err = manager.Prepare(abs); err != nil {
		return s, err
	}
	if _, err = i.compose(ctx, s, nil, "config", "--quiet"); err != nil {
		return s, err
	}
	if s.Stage == "configured" {
		if local != nil {
			if i.Progress != nil {
				i.Progress("검증한 로컬 이미지를 Docker에 불러오는 중")
			}
			if err = i.loadLocalImages(ctx, s, local); err != nil {
				return s, err
			}
		} else {
			if _, err = i.compose(ctx, s, nil, "pull"); err != nil {
				return s, err
			}
		}
		if err = i.checkManagementMount(ctx, s); err != nil {
			return s, err
		}
		if err = i.stage(&s, "downloaded"); err != nil {
			return s, err
		}
	}
	if s.Stage == "downloaded" {
		if _, err = i.compose(ctx, s, nil, "up", "-d", "--wait", "--wait-timeout", "180", "postgres", "redis"); err != nil {
			return s, err
		}
		if err = i.stage(&s, "migrating"); err != nil {
			return s, err
		}
		if _, err = i.compose(ctx, s, nil, "run", "--rm", "--no-deps", "-T", "migration"); err != nil {
			return s, err
		}
		if err = i.stage(&s, "admin_required"); err != nil {
			return s, err
		}
	}
	if s.Stage == "admin_required" {
		// The read-only probe detects a committed singleton account without asking again.
		out, probeErr := i.compose(ctx, s, strings.NewReader("{\"check_only\":true}\n"), "run", "--rm", "--no-deps", "-T", "api", "python", "-m", "rail_waitlist.admin_auth.bootstrap")
		if probeErr != nil {
			return s, fmt.Errorf("관리자 존재 여부를 확인하지 못했습니다: %w", probeErr)
		}
		already := probeErr == nil && bootstrapStatus(out) == "already_configured"
		if !already && bootstrapStatus(out) != "not_configured" {
			return s, fmt.Errorf("관리자 조회 결과가 잘못되었습니다")
		}
		if !already {
			if o.Credentials == nil {
				return s, fmt.Errorf("관리자 입력이 필요합니다; install을 다시 실행하세요")
			}
			credentials, err := o.Credentials()
			if err != nil {
				return s, err
			}
			payload, err := json.Marshal(credentials)
			if err != nil {
				return s, err
			}
			if len(payload) > 4096 {
				return s, fmt.Errorf("관리자 입력이 너무 큽니다")
			}
			out, err = i.compose(ctx, s, bytes.NewReader(payload), "run", "--rm", "--no-deps", "-T", "api", "python", "-m", "rail_waitlist.admin_auth.bootstrap")
			for n := range payload {
				payload[n] = 0
			}
			credentials.Password = ""
			if err != nil {
				return s, fmt.Errorf("관리자 생성에 실패했습니다: %w", err)
			}
			if status := bootstrapStatus(out); status != "created" && status != "already_configured" {
				return s, fmt.Errorf("관리자 생성 결과를 확인하지 못했습니다")
			}
		}
		if err = i.stage(&s, "starting"); err != nil {
			return s, err
		}
	}
	if s.Stage == "starting" {
		if _, err = i.compose(ctx, s, nil, "up", "-d", "--wait", "--wait-timeout", "180"); err != nil {
			return s, err
		}
		status, err := i.Status(ctx, abs)
		if err != nil {
			return s, err
		}
		if !status.Healthy {
			return s, fmt.Errorf("모든 서비스의 healthy 상태를 확인하지 못했습니다")
		}
		if err = i.stage(&s, "complete"); err != nil {
			return s, err
		}
	}
	return s, nil
}

func (i *Installer) checkManagementMount(ctx context.Context, s State) error {
	data, err := os.ReadFile(filepath.Join(s.Path, ".railwait", "manager", "control", "maintenance.json"))
	if err != nil {
		return err
	}
	var host struct {
		Schema     int    `json:"schema"`
		Closed     bool   `json:"closed"`
		Generation string `json:"generation"`
	}
	if json.Unmarshal(data, &host) != nil || host.Schema != 1 || host.Closed {
		return fmt.Errorf("설치 관리 gate가 준비되지 않았습니다")
	}
	for attempt := 0; attempt < 3; attempt++ {
		probeCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
		out, err := i.compose(probeCtx, s, nil, "run", "--rm", "--no-deps", "-T", "api", "python", "-m", "rail_waitlist.maintenance", "--status")
		cancel()
		var mounted struct {
			Schema     int    `json:"schema"`
			Closed     bool   `json:"closed"`
			Generation string `json:"generation"`
			Drained    bool   `json:"drained"`
		}
		if err == nil && json.Unmarshal(out, &mounted) == nil && mounted.Schema == 1 && !mounted.Closed && mounted.Generation == host.Generation && mounted.Drained {
			return nil
		}
		if attempt < 2 {
			if err = i.pauseProbe(ctx); err != nil {
				return err
			}
		}
	}
	return fmt.Errorf("컨테이너에서 설치 관리 파일을 읽지 못했습니다; 설치 경로의 Docker 파일 공유와 파일 권한을 확인하세요")
}

func (i *Installer) pauseProbe(ctx context.Context) error {
	interval := i.ProbeInterval
	if interval <= 0 {
		interval = time.Second
	}
	timer := time.NewTimer(interval)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func bootstrapStatus(data []byte) string {
	var result struct {
		Status string `json:"status"`
	}
	if json.Unmarshal(bytes.TrimSpace(data), &result) != nil {
		return ""
	}
	return result.Status
}

func activeImages(images map[string]string, serviceProfiles map[string][]string, selected []string) map[string]string {
	result := map[string]string{}
	for service, image := range images {
		profiles := serviceProfiles[service]
		active := len(profiles) == 0
		for _, profile := range profiles {
			for _, choice := range selected {
				if profile == choice {
					active = true
				}
			}
		}
		if active {
			result[service] = image
		}
	}
	return result
}
