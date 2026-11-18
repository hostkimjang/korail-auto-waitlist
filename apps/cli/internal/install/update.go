package install

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/manager"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/release"
)

type UpdatePlan struct {
	Schema           int      `json:"schema"`
	PlanID           string   `json:"plan_id"`
	InstallationID   string   `json:"installation_id"`
	CurrentReleaseID string   `json:"current_release_id"`
	TargetReleaseID  string   `json:"target_release_id"`
	Allowed          bool     `json:"allowed"`
	Reason           string   `json:"reason"`
	BlockedReasons   []string `json:"blocked_reasons"`
	Steps            []string `json:"steps"`
	ExpiresAt        string   `json:"expires_at"`
}
type UpdateOptions struct {
	Path, TargetReleaseID, PlanID, JobID string
	// PackagePath and TrustKey are set only by a host CLI applying a local package plan.
	PackagePath, TrustKey string
	Progress              func(string) error
}
type UpdateResult struct {
	JobID          string   `json:"job_id"`
	State          string   `json:"state"`
	CurrentVersion string   `json:"current_version"`
	TargetVersion  string   `json:"target_version"`
	Stage          string   `json:"stage"`
	Warnings       []string `json:"warnings,omitempty"`
	// PreviousPackage is set while a local package update keeps the previous package aside,
	// so an interrupted job can be recovered from its record.
	PreviousPackage string `json:"previous_package,omitempty"`
}
type savedUpdatePlan struct {
	Plan              UpdatePlan `json:"plan"`
	EnvironmentSHA256 string     `json:"environment_sha256"`
	TargetSHA256      string     `json:"target_sha256"`
	StateSHA256       string     `json:"state_sha256"`
	Kind              string     `json:"kind"`
	CandidateSHA256   string     `json:"candidate_sha256,omitempty"`
}

func hashBytes(data []byte) string { sum := sha256.Sum256(data); return hex.EncodeToString(sum[:]) }

// ReadUpdateJob reads a job record that Update wrote in this installation. Jobs run by the
// host CLI itself, such as local package updates, are not in the manager's job folder.
func ReadUpdateJob(path, id string) (UpdateResult, error) {
	var result UpdateResult
	if !regexp.MustCompile(`^[a-f0-9]{32}$`).MatchString(id) {
		return result, fmt.Errorf("작업 ID가 잘못되었습니다")
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return result, err
	}
	file := filepath.Join(abs, ".railwait", "updates", id+".json")
	info, err := os.Lstat(file)
	if err != nil {
		return result, err
	}
	if !info.Mode().IsRegular() || info.Size() > 64<<10 {
		return result, fmt.Errorf("작업 기록이 잘못되었습니다")
	}
	data, err := os.ReadFile(file)
	if err != nil {
		return result, err
	}
	if json.Unmarshal(data, &result) != nil || result.JobID != id {
		return result, fmt.Errorf("작업 기록이 잘못되었습니다")
	}
	return result, nil
}

func (i *Installer) PlanUpdate(ctx context.Context, path string) (UpdatePlan, error) {
	s, err := ReadState(path)
	if err != nil {
		return UpdatePlan{}, err
	}
	if s.ReleaseSource == "local_package" {
		return UpdatePlan{Schema: 1, InstallationID: s.ID, CurrentReleaseID: s.Release, Allowed: false, Reason: "update_blocked", BlockedReasons: []string{"로컬 패키지 설치는 공식 릴리스 대신 호스트에서 railwait update --package와 --trust-key로 업데이트합니다"}, Steps: []string{}}, nil
	}
	r, err := i.Releases.Resolve(ctx, "stable")
	if err != nil {
		return UpdatePlan{}, err
	}
	plan, err := i.makePlan(ctx, s, r)
	if err != nil {
		return UpdatePlan{}, err
	}
	return savePlan(s, plan, r.SHA256, "release", nil)
}

func savePlan(s State, plan UpdatePlan, targetSHA, kind string, candidate []byte) (UpdatePlan, error) {
	env, err := os.ReadFile(filepath.Join(s.Path, ".env"))
	if err != nil {
		return plan, err
	}
	stateJSON, _ := json.Marshal(s)
	planData, _ := json.Marshal(struct{ Installation, Current, Target, Bundle, Env, State, Expires, Kind, Candidate string }{s.ID, s.Release, plan.TargetReleaseID, targetSHA, hashBytes(env), hashBytes(stateJSON), plan.ExpiresAt, kind, hashBytes(candidate)})
	plan.PlanID = hashBytes(planData)
	saved := savedUpdatePlan{Plan: plan, EnvironmentSHA256: hashBytes(env), TargetSHA256: targetSHA, StateSHA256: hashBytes(stateJSON), Kind: kind, CandidateSHA256: hashBytes(candidate)}
	dir := filepath.Join(s.Path, ".railwait", "plans")
	if err = platform.SecureDir(dir); err != nil {
		return plan, err
	}
	if err = cleanupExpiredPlans(dir); err != nil {
		return plan, err
	}
	if configureKind(kind) {
		if err = atomicWrite(filepath.Join(dir, plan.PlanID+".candidate.env"), candidate); err != nil {
			return plan, err
		}
	}
	data, _ := json.Marshal(saved)
	if err = atomicWrite(filepath.Join(dir, plan.PlanID+".json"), data); err != nil {
		return plan, err
	}
	return plan, nil
}

// configureKind lists the plans that change one group of .env settings and keep the
// installed release. Update validates each candidate with that group's own check.
func configureKind(kind string) bool {
	switch kind {
	case "configure_tago", "configure_access", "configure_push", "configure_reservation", "configure_settings":
		return true
	}
	return false
}

func cleanupExpiredPlans(dir string) error {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return err
	}
	pattern := regexp.MustCompile(`^[a-f0-9]{64}\.json$`)
	for _, entry := range entries {
		if !pattern.MatchString(entry.Name()) || !entry.Type().IsRegular() {
			continue
		}
		path := filepath.Join(dir, entry.Name())
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		var saved savedUpdatePlan
		if json.Unmarshal(data, &saved) != nil {
			continue
		}
		expiry, err := time.Parse(time.RFC3339, saved.Plan.ExpiresAt)
		if err != nil || time.Now().Before(expiry) {
			continue
		}
		if saved.Plan.PlanID+".json" != entry.Name() {
			continue
		}
		if configureKind(saved.Kind) {
			candidate := filepath.Join(dir, saved.Plan.PlanID+".candidate.env")
			if info, err := os.Lstat(candidate); err == nil {
				if !info.Mode().IsRegular() {
					return fmt.Errorf("설정 후보 파일이 실제 파일이 아닙니다")
				}
				if err = os.Remove(candidate); err != nil {
					return err
				}
			} else if !os.IsNotExist(err) {
				return err
			}
		}
		if err = os.Remove(path); err != nil {
			return err
		}
	}
	return nil
}

func (i *Installer) makePlan(ctx context.Context, s State, r release.Release) (UpdatePlan, error) {
	return i.makePlanMode(ctx, s, r, false)
}

func (i *Installer) makePlanMode(ctx context.Context, s State, r release.Release, configure bool) (UpdatePlan, error) {
	p := UpdatePlan{Schema: 1, InstallationID: s.ID, CurrentReleaseID: s.Release, TargetReleaseID: r.Version, BlockedReasons: []string{}, Steps: []string{"checking", "downloading", "draining", "backing_up", "applying", "verifying", "resuming"}, ExpiresAt: time.Now().UTC().Add(15 * time.Minute).Format(time.RFC3339)}
	p.Reason = "update_blocked"
	block := func(reason string) { p.BlockedReasons = append(p.BlockedReasons, reason) }
	if s.Stage != "complete" {
		block("설치가 완료 상태가 아닙니다")
	}
	if !configure && s.Release == r.Version {
		block("이미 해당 안정 릴리스를 사용합니다")
	}
	if !configure && !newerVersion(r.Version, s.Release) {
		block("일반 업데이트는 더 높은 버전으로만 적용할 수 있습니다")
	}
	if s.DatabaseRevision == "" || r.DatabaseRevision == "" || s.DatabaseRevision != r.DatabaseRevision {
		block("DB schema 변경 또는 출발 revision 불명으로 자동 업데이트할 수 없습니다")
	}
	if s.SettingsSchema != 1 || r.SettingsSchema != 1 {
		block("지원하지 않는 설정 schema입니다")
	}
	for _, service := range []string{"postgres", "redis"} {
		if s.ExpectedImages[service] == "" || r.Images[service] != s.ExpectedImages[service] {
			block("저장소 이미지 변경은 일반 앱 업데이트로 적용할 수 없습니다")
		}
	}
	compatible := false
	for _, source := range r.CompatibleFrom {
		if source == s.Release {
			compatible = true
		}
	}
	if !compatible {
		block("대상 릴리스가 현재 버전에서의 업데이트를 허용하지 않습니다")
	}
	if _, err := os.Stat(filepath.Join(s.Path, ".railwait", "manager", "auth", "request-token")); err != nil {
		block("호스트 관리 서비스 연결이 준비되지 않았습니다")
	}
	if !manager.Available(s.Path, time.Now()) {
		block("호스트 관리 서비스가 실행 중이 아닙니다")
		p.Reason = "manager_unavailable"
	}
	gateData, gateErr := os.ReadFile(filepath.Join(s.Path, ".railwait", "manager", "control", "maintenance.json"))
	var gate struct {
		Schema int  `json:"schema"`
		Closed bool `json:"closed"`
	}
	if gateErr != nil || json.Unmarshal(gateData, &gate) != nil || gate.Schema != 1 || gate.Closed {
		block("유지보수 상태가 닫혀 있거나 확인되지 않습니다")
		p.Reason = "recovery_required"
	}
	nextImages := activeImages(r.Images, r.ServiceProfiles, s.Profiles)
	if len(nextImages) != len(s.ExpectedImages) {
		block("서비스 구성 변경은 별도 이전이 필요합니다")
	} else {
		for name := range s.ExpectedImages {
			if _, ok := nextImages[name]; !ok {
				block("서비스 구성 변경은 별도 이전이 필요합니다")
				break
			}
		}
	}
	if err := verifyBundle(filepath.Dir(s.ComposeFile), s.BundleHashes); err != nil {
		block("현재 릴리스 파일의 검증 지문이 일치하지 않습니다")
	}
	if out, err := i.compose(ctx, s, nil, "run", "--rm", "--no-deps", "-T", "api", "python", "-m", "rail_waitlist.maintenance", "--database-revision"); err != nil {
		block("현재 DB revision을 확인하지 못했습니다")
	} else {
		var revision struct {
			Schema           int    `json:"schema"`
			DatabaseRevision string `json:"database_revision"`
		}
		if json.Unmarshal(out, &revision) != nil || revision.Schema != 1 || revision.DatabaseRevision != s.DatabaseRevision {
			block("실제 DB revision이 릴리스 계약과 일치하지 않습니다")
		}
	}
	status, err := i.Status(ctx, s.Path)
	if err != nil {
		block("현재 서비스 상태를 확인하지 못했습니다")
	} else if !status.Healthy {
		block("현재 서비스와 이미지가 정상 상태가 아닙니다")
	}
	spaceCheck := i.CheckSpace
	if spaceCheck == nil {
		spaceCheck = checkSpace
	}
	if err := spaceCheck(s.Path); err != nil {
		block("설치 파일시스템의 여유 공간을 확인하세요")
	}
	p.Allowed = len(p.BlockedReasons) == 0
	if p.Allowed {
		p.Reason = "update_available"
	} else if p.Reason == "update_blocked" && s.Release == r.Version {
		p.Reason = "up_to_date"
	}
	return p, nil
}

func (i *Installer) Update(ctx context.Context, o UpdateOptions) (result UpdateResult, err error) {
	result = UpdateResult{JobID: o.JobID, State: "running", TargetVersion: o.TargetReleaseID, Stage: "checking"}
	if !regexp.MustCompile(`^[a-f0-9]{64}$`).MatchString(o.PlanID) {
		return result, fmt.Errorf("유효한 업데이트 계획이 필요합니다")
	}
	if o.JobID == "" {
		raw := make([]byte, 16)
		if _, err = rand.Read(raw); err != nil {
			return result, err
		}
		o.JobID = hex.EncodeToString(raw)
		result.JobID = o.JobID
	}
	if !regexp.MustCompile(`^[a-f0-9]{32}$`).MatchString(o.JobID) {
		return result, fmt.Errorf("작업 ID가 잘못되었습니다")
	}
	abs, err := filepath.Abs(o.Path)
	if err != nil {
		return result, err
	}
	lock, err := platform.AcquireLock(filepath.Join(abs, ".railwait", "install.lock"))
	if err != nil {
		return result, err
	}
	defer lock.Close()
	s, err := ReadState(abs)
	if err != nil {
		return result, err
	}
	result.CurrentVersion = s.Release
	jobDir := filepath.Join(abs, ".railwait", "updates")
	if err = platform.SecureDir(jobDir); err != nil {
		return result, err
	}
	closed := false
	progress := func(stage string) error {
		result.Stage = stage
		data, _ := json.Marshal(result)
		if err := atomicWrite(filepath.Join(jobDir, o.JobID+".json"), data); err != nil {
			return err
		}
		if o.Progress != nil {
			return o.Progress(stage)
		}
		return nil
	}
	defer func() {
		// A failed package restore has already marked the job for recovery by hand.
		if err != nil && result.State != "deferred" && result.State != "recovery_required" {
			if closed {
				result.State = "recovery_required"
			} else {
				result.State = "failed"
			}
		}
		data, _ := json.Marshal(result)
		err = errors.Join(err, atomicWrite(filepath.Join(jobDir, o.JobID+".json"), data))
	}()
	if err = progress("checking"); err != nil {
		return result, err
	}
	savedData, err := os.ReadFile(filepath.Join(abs, ".railwait", "plans", o.PlanID+".json"))
	if err != nil {
		return result, fmt.Errorf("업데이트 계획을 읽지 못했습니다")
	}
	var saved savedUpdatePlan
	if json.Unmarshal(savedData, &saved) != nil {
		return result, fmt.Errorf("업데이트 계획이 잘못되었습니다")
	}
	configure := configureKind(saved.Kind)
	local := saved.Kind == "local_package"
	if local != (o.PackagePath != "" || o.TrustKey != "") {
		// The host manager never forwards package paths, so only a host CLI caller that names
		// the planned package and key again can apply a local package plan.
		return result, fmt.Errorf("로컬 패키지 업데이트는 계획을 만든 --package와 --trust-key로만 적용합니다")
	}
	if local && s.ReleaseSource != "local_package" {
		return result, fmt.Errorf("로컬 패키지로 설치한 경로만 로컬 패키지로 업데이트합니다")
	}
	if s.ReleaseSource == "local_package" && !configure && !local {
		return result, fmt.Errorf("로컬 패키지 설치의 공식 릴리스 업데이트는 지원하지 않습니다; --package와 --trust-key를 사용하세요")
	}
	if saved.Kind != "release" && !configure && !local {
		return result, fmt.Errorf("지원하지 않는 관리 작업 종류입니다")
	}
	expiry, err := time.Parse(time.RFC3339, saved.Plan.ExpiresAt)
	if err != nil || !time.Now().Before(expiry) || saved.Plan.PlanID != o.PlanID || saved.Plan.TargetReleaseID != o.TargetReleaseID || saved.Plan.InstallationID != s.ID || !saved.Plan.Allowed {
		return result, fmt.Errorf("업데이트 계획이 만료되었거나 요청과 일치하지 않습니다")
	}
	env, err := os.ReadFile(filepath.Join(abs, ".env"))
	if err != nil {
		return result, err
	}
	stateJSON, _ := json.Marshal(s)
	if hashBytes(env) != saved.EnvironmentSHA256 || hashBytes(stateJSON) != saved.StateSHA256 {
		return result, fmt.Errorf("설정이나 설치 상태가 변경되었습니다; 새 계획이 필요합니다")
	}
	r := installedRelease(s)
	target := r.SHA256
	if local {
		selected, key, openErr := openSelectedLocalPackage(ctx, o.PackagePath, o.TrustKey, false)
		if openErr != nil {
			return result, openErr
		}
		if hashBytes(key) != saved.CandidateSHA256 {
			return result, fmt.Errorf("신뢰 공개키가 계획과 다릅니다; 새 계획이 필요합니다")
		}
		if r, err = localUpdateRelease(ctx, selected, s); err != nil {
			return result, err
		}
		target = localPackageIdentity(selected.Manifest)
	} else if !configure {
		r, err = i.Releases.Resolve(ctx, "stable")
		if err != nil {
			return result, err
		}
		target = r.SHA256
	}
	if r.Version != o.TargetReleaseID || target != saved.TargetSHA256 {
		return result, fmt.Errorf("확인한 릴리스가 변경되었습니다; 새 계획이 필요합니다")
	}
	currentPlan, err := i.makePlanMode(ctx, s, r, configure)
	if err != nil {
		return result, err
	}
	if !currentPlan.Allowed {
		return result, fmt.Errorf("업데이트 사전검사에서 차단되었습니다: %s", strings.Join(currentPlan.BlockedReasons, "; "))
	}
	oldState, _ := json.Marshal(s)
	if err = atomicWrite(filepath.Join(jobDir, o.JobID+".installation.json"), oldState); err != nil {
		return result, err
	}
	next := s
	var candidate []byte
	var staged *release.LocalSource
	stagedPackage := ""
	settingsScope := ""
	if configure {
		candidatePath := filepath.Join(abs, ".railwait", "plans", o.PlanID+".candidate.env")
		candidate, err = os.ReadFile(candidatePath)
		if err != nil {
			return result, err
		}
		if hashBytes(candidate) != saved.CandidateSHA256 {
			return result, fmt.Errorf("설정 후보가 계획과 일치하지 않습니다")
		}
		switch saved.Kind {
		case "configure_access":
			err = validateAccessCandidate(env, candidate)
		case "configure_push":
			err = validatePushCandidate(env, candidate)
		case "configure_reservation":
			err = validateReservationCandidate(env, candidate)
		case "configure_settings":
			settingsScope, err = validateSettingsCandidate(env, candidate)
		default:
			err = validateTAGOCandidate(env, candidate)
		}
		if err != nil {
			return result, err
		}
		defer func() { err = errors.Join(err, os.Remove(candidatePath)) }()
		if err = progress("preparing_config"); err != nil {
			return result, err
		}
	} else {
		stage := "downloading"
		if local {
			stage = "loading_package"
		}
		if err = progress(stage); err != nil {
			return result, err
		}
		next.Release = r.Version
		next.ComposeFile = filepath.Join(abs, "releases", r.Version, "compose.release.yml")
		next.ExpectedImages = activeImages(r.Images, r.ServiceProfiles, s.Profiles)
		next.DatabaseRevision = r.DatabaseRevision
		next.SettingsSchema = r.SettingsSchema
		staging := filepath.Join(abs, "releases", ".update-"+o.JobID)
		if _, err = os.Lstat(staging); err == nil {
			return result, fmt.Errorf("이 작업의 기존 다운로드가 있습니다; 복구 확인이 필요합니다")
		}
		if err = os.Mkdir(staging, 0700); err != nil {
			return result, err
		}
		defer func() { err = errors.Join(err, os.RemoveAll(staging)) }()
		if local {
			// Later steps read only this private copy, verified again and bound to the plan.
			if staged, stagedPackage, err = stageLocalPackage(ctx, abs, o.PackagePath, o.TrustKey, ".local-package-update-*"); err != nil {
				return result, err
			}
			defer func() { err = errors.Join(err, os.RemoveAll(stagedPackage)) }()
			stagedKey, keyErr := localTrustKey(filepath.Join(stagedPackage, "trust-key.pub"))
			if keyErr != nil {
				return result, keyErr
			}
			if localPackageIdentity(staged.Manifest) != saved.TargetSHA256 || hashBytes(stagedKey) != saved.CandidateSHA256 {
				return result, fmt.Errorf("보존할 로컬 패키지가 계획과 다릅니다; 새 계획이 필요합니다")
			}
			if err = staged.DownloadAndExtract(ctx, r, staging); err != nil {
				return result, err
			}
			if err = validateLocalCompose(staging, staged.Manifest.Images); err != nil {
				return result, err
			}
		} else if err = i.Releases.DownloadAndExtract(ctx, r, staging); err != nil {
			return result, err
		}
		next.BundleHashes, err = bundleHashes(staging)
		if err != nil {
			return result, err
		}
		if _, err = os.Lstat(filepath.Dir(next.ComposeFile)); err == nil {
			if err = verifyBundle(filepath.Dir(next.ComposeFile), next.BundleHashes); err != nil {
				return result, err
			}
		} else if os.IsNotExist(err) {
			if err = os.Rename(staging, filepath.Dir(next.ComposeFile)); err != nil {
				return result, err
			}
		} else {
			return result, err
		}
		if _, err = i.compose(ctx, next, nil, "config", "--quiet"); err != nil {
			return result, err
		}
		if local {
			if err = i.loadLocalImages(ctx, s, staged); err != nil {
				return result, err
			}
		} else if _, err = i.compose(ctx, next, nil, "pull"); err != nil {
			return result, err
		}
	}
	if saved.Kind == "configure_settings" && settingsScope == "web" {
		if err = i.applyWebSettings(ctx, s, env, candidate, &result, progress); err != nil {
			return result, err
		}
		result.State = "succeeded"
		result.Stage = "succeeded"
		return result, nil
	}
	if s.ReleaseSource != "local_package" {
		if _, err = i.compose(ctx, s, nil, "--profile", "backup", "pull", "backup"); err != nil {
			return result, err
		}
	}
	if err = i.ensureBackupKey(ctx, s); err != nil {
		return result, err
	}
	controlDir := filepath.Join(abs, ".railwait", "manager", "control")
	if info, statErr := os.Lstat(controlDir); statErr != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		err = fmt.Errorf("관리 서비스 control 디렉터리가 준비되지 않았습니다")
		return result, err
	}
	generation := o.JobID
	latestEnv, readErr := os.ReadFile(filepath.Join(abs, ".env"))
	if readErr != nil {
		return result, readErr
	}
	latestState, readErr := ReadState(abs)
	if readErr != nil {
		return result, readErr
	}
	latestStateJSON, _ := json.Marshal(latestState)
	if hashBytes(latestEnv) != saved.EnvironmentSHA256 || hashBytes(latestStateJSON) != saved.StateSHA256 {
		return result, fmt.Errorf("다운로드 중 설정이나 설치 상태가 변경되었습니다; 새 계획이 필요합니다")
	}
	if err = verifyBundle(filepath.Dir(s.ComposeFile), s.BundleHashes); err != nil {
		return result, err
	}
	if err = verifyBundle(filepath.Dir(next.ComposeFile), next.BundleHashes); err != nil {
		return result, err
	}
	committed := false
	if local {
		// Swap the preserved package before the gate closes, so a swap that the OS refuses, for
		// example while antivirus holds a file open on Windows, fails the job before any service
		// or maintenance state changes. Status verification reads the preserved package, so it
		// holds the new package by the verifying stage. Until the new installation record is
		// written every later failure restores the previous package; afterwards the previous
		// package is only cleanup. Record where it goes before it moves, so an interrupted job
		// can be recovered by hand from its job record.
		result.PreviousPackage = filepath.Join(abs, ".railwait", previousLocalPackagePrefix+o.JobID)
		if err = progress("replacing_package"); err != nil {
			result.PreviousPackage = ""
			return result, err
		}
		if err = replaceLocalPackage(abs, stagedPackage, result.PreviousPackage); err != nil {
			// A refused swap leaves the preserved package in place unless the rollback also
			// failed, so the job names a previous package only while that directory exists.
			if _, statErr := os.Lstat(result.PreviousPackage); os.IsNotExist(statErr) {
				result.PreviousPackage = ""
			} else if _, statErr := os.Lstat(filepath.Join(abs, ".railwait", "local-package")); statErr != nil {
				// The rollback was refused too and no package is preserved until the previous one
				// is moved back by hand, so the job needs recovery although the gate never closed.
				result.State = "recovery_required"
			}
			return result, err
		}
		defer func() {
			if !committed {
				if restoreErr := restoreLocalPackage(abs, result.PreviousPackage); restoreErr != nil {
					err = errors.Join(err, restoreErr)
					// The preserved package is left half restored, so the job needs recovery by
					// hand even when the drain was deferred or the gate never closed.
					result.State = "recovery_required"
				} else {
					result.PreviousPackage = ""
				}
			} else if removeErr := retryWhileFileHeld(func() error { return os.RemoveAll(result.PreviousPackage) }); removeErr != nil {
				result.Warnings = append(result.Warnings, "설치 기록은 새 버전으로 바뀌었지만 이전 로컬 패키지를 지우지 못했습니다: "+result.PreviousPackage)
			} else {
				result.PreviousPackage = ""
			}
		}()
	}
	if err = writeGate(controlDir, true, generation); err != nil {
		return result, err
	}
	closed = true
	if err = progress("draining"); err != nil {
		return result, err
	}
	if err = i.waitDrain(ctx, s, generation); err != nil {
		result.State = "deferred"
		return result, err
	}
	if out, revisionErr := i.compose(ctx, s, nil, "run", "--rm", "--no-deps", "-T", "api", "python", "-m", "rail_waitlist.maintenance", "--database-revision"); revisionErr != nil {
		return result, revisionErr
	} else {
		var revision struct {
			Schema           int    `json:"schema"`
			DatabaseRevision string `json:"database_revision"`
		}
		if json.Unmarshal(out, &revision) != nil || revision.Schema != 1 || revision.DatabaseRevision != s.DatabaseRevision {
			return result, fmt.Errorf("종료 대기 중 DB revision이 변경되었습니다")
		}
	}
	if err = progress("backing_up"); err != nil {
		return result, err
	}
	backupEnv := filepath.Join(abs, ".railwait", "backup.env")
	if _, err = i.composeExtra(ctx, s, backupEnv, bytes.NewReader(env), "--profile", "backup", "run", "--rm", "--no-deps", "-T", "-e", "RAILWAIT_UPDATE_JOB_ID="+o.JobID, "--entrypoint", "/scripts/update-backup.sh", "backup"); err != nil {
		return result, fmt.Errorf("암호화 백업 검증에 실패했습니다: %w", err)
	}
	if err = progress("applying"); err != nil {
		return result, err
	}
	services := []string{}
	for name := range s.ExpectedImages {
		if name != "postgres" && name != "redis" && name != "migration" && name != "log-init" {
			services = append(services, name)
		}
	}
	sort.Strings(services)
	if _, err = i.compose(ctx, s, nil, append([]string{"stop", "--timeout", "300"}, services...)...); err != nil {
		return result, err
	}
	if configure {
		if err = atomicWrite(filepath.Join(abs, ".env"), candidate); err != nil {
			return result, err
		}
		// From here a failure puts the previous settings back and restarts the services
		// with them, so a value the app rejects does not leave it down behind a closed gate.
		defer func() {
			if err == nil {
				return
			}
			if restoreErr := i.restoreSettings(ctx, s, env, services); restoreErr != nil {
				err = errors.Join(err, restoreErr)
				return
			}
			if gateErr := writeGate(controlDir, false, generation); gateErr != nil {
				err = errors.Join(err, gateErr)
				return
			}
			closed = false
			result.Stage = "rolled_back"
		}()
	}
	for _, oneShot := range []string{"migration", "log-init"} {
		if _, err = i.compose(ctx, next, nil, "up", "--no-deps", "--force-recreate", "--abort-on-container-exit", "--exit-code-from", oneShot, oneShot); err != nil {
			return result, err
		}
	}
	nextServices := []string{}
	for name := range next.ExpectedImages {
		if name != "postgres" && name != "redis" && name != "migration" && name != "log-init" {
			nextServices = append(nextServices, name)
		}
	}
	sort.Strings(nextServices)
	if _, err = i.compose(ctx, next, nil, append([]string{"up", "-d", "--force-recreate", "--no-deps", "--wait", "--wait-timeout", "180"}, nextServices...)...); err != nil {
		return result, err
	}
	if err = progress("verifying"); err != nil {
		return result, err
	}
	status, err := i.statusState(ctx, next)
	if err != nil {
		return result, err
	}
	if !status.Healthy {
		return result, fmt.Errorf("새 서비스의 health와 이미지가 일치하지 않습니다")
	}
	next.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	if err = writeState(next); err != nil {
		// A failed directory sync can follow a completed rename; keep the preserved package
		// that matches the record actually on disk.
		if written, readErr := ReadState(abs); readErr == nil && written.Release == next.Release && written.UpdatedAt == next.UpdatedAt {
			committed = true
		}
		return result, err
	}
	committed = true
	if err = progress("resuming"); err != nil {
		return result, err
	}
	if err = writeGate(controlDir, false, generation); err != nil {
		return result, err
	}
	closed = false
	result.State = "succeeded"
	result.Stage = "succeeded"
	return result, nil
}

func writeGate(dir string, closed bool, generation string) error {
	data, _ := json.Marshal(map[string]any{"schema": 1, "closed": closed, "generation": generation})
	return manager.WriteStatusFile(filepath.Join(dir, "maintenance.json"), json.RawMessage(data))
}

func (i *Installer) waitDrain(ctx context.Context, s State, generation string) error {
	drainCtx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	for {
		out, err := i.compose(drainCtx, s, nil, "run", "--rm", "--no-deps", "-T", "api", "python", "-m", "rail_waitlist.maintenance", "--status")
		var value struct {
			Schema     int    `json:"schema"`
			Closed     bool   `json:"closed"`
			Generation string `json:"generation"`
			Drained    bool   `json:"drained"`
		}
		if err == nil && json.Unmarshal(out, &value) == nil && value.Schema == 1 && value.Closed && value.Generation == generation && value.Drained {
			return nil
		}
		if err = i.pauseProbe(drainCtx); err != nil {
			return fmt.Errorf("작업 종료를 안전하게 확인하지 못했습니다; 유지보수 상태를 보존합니다: %w", err)
		}
	}
}

func (i *Installer) ensureBackupKey(ctx context.Context, s State) error {
	path := filepath.Join(s.Path, ".railwait", "backup.env")
	if _, err := os.Lstat(path); err == nil {
		if err = platform.SecureFile(path); err != nil {
			return err
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		values, err := ParseEnv(string(data))
		if err != nil {
			return err
		}
		if values["BACKUP_AGE_IDENTITY_BASE64"] == "" || !strings.HasPrefix(values["BACKUP_AGE_RECIPIENT"], "age1") {
			return fmt.Errorf("백업 복구키가 잘못되었습니다")
		}
		return nil
	} else if !os.IsNotExist(err) {
		return err
	}
	identity, err := i.compose(ctx, s, nil, "--profile", "backup", "run", "--rm", "--no-deps", "-T", "--entrypoint", "age-keygen", "backup")
	if err != nil {
		return err
	}
	if len(identity) > 8192 || !strings.Contains(string(identity), "AGE-SECRET-KEY-1") {
		return fmt.Errorf("백업 복구키 생성 결과가 잘못되었습니다")
	}
	defer func() {
		for n := range identity {
			identity[n] = 0
		}
	}()
	pub, err := i.compose(ctx, s, strings.NewReader(string(identity)), "--profile", "backup", "run", "--rm", "--no-deps", "-T", "--entrypoint", "age-keygen", "backup", "-y")
	if err != nil {
		return err
	}
	recipient := strings.TrimSpace(string(pub))
	if !regexp.MustCompile(`^age1[a-z0-9]+$`).MatchString(recipient) {
		return fmt.Errorf("백업 공개키 생성 결과가 잘못되었습니다")
	}
	env := []byte("BACKUP_AGE_IDENTITY_BASE64='" + base64.StdEncoding.EncodeToString(identity) + "'\nBACKUP_AGE_RECIPIENT='" + recipient + "'\n")
	return atomicWrite(path, env)
}

func (i *Installer) composeExtra(ctx context.Context, s State, envFile string, input io.Reader, args ...string) ([]byte, error) {
	base := []string{"compose", "--project-name", s.Project, "--project-directory", filepath.Dir(s.ComposeFile), "--env-file", filepath.Join(s.Path, ".env"), "--env-file", envFile, "-f", s.ComposeFile}
	for _, profile := range s.Profiles {
		base = append(base, "--profile", profile)
	}
	return i.docker(ctx, s, input, append(base, args...)...)
}

func newerVersion(target, current string) bool {
	var a, b [3]int
	if n, err := fmt.Sscanf(strings.TrimPrefix(target, "v"), "%d.%d.%d", &a[0], &a[1], &a[2]); err != nil || n != 3 {
		return false
	}
	if n, err := fmt.Sscanf(strings.TrimPrefix(current, "v"), "%d.%d.%d", &b[0], &b[1], &b[2]); err != nil || n != 3 {
		return false
	}
	for n := 0; n < 3; n++ {
		if a[n] != b[n] {
			return a[n] > b[n]
		}
	}
	return false
}
