package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/manager"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

// managerUpgrader replaces an installation's host manager with the running CLI and
// restarts it. The manager never replaces itself: a web update changes the app, not the
// manager, so a feature that needs a newer manager arrives when a newer CLI runs here.
type managerUpgrader struct {
	runner manager.ServiceRunner
	// version reports the release version an installed manager executable was built as.
	version func(ctx context.Context, executable string) (string, error)
	// wait bounds how long the old manager may take to stop and the new one to start.
	wait time.Duration
	// endProcess ends a manager process that its service manager left running; nil uses
	// platform.EndManagerProcess.
	endProcess func(pid int, executable string, startedAt time.Time, wait time.Duration) error
	// notice tells the user about a leftover manager process that was ended; nil stays quiet.
	notice func(string)
}

func newManagerUpgrader() managerUpgrader {
	return managerUpgrader{runner: manager.ExecServiceRunner{}, version: executableVersion, wait: 30 * time.Second, endProcess: platform.EndManagerProcess}
}

var versionLine = regexp.MustCompile(`^railwait (\S+) `)

// errServiceDefinitionReview stops an upgrade before the manager is touched when its service
// definition is not one Railwait wrote, for example after it was edited by hand. Restarting
// from such a definition might not run the replaced executable.
var errServiceDefinitionReview = errors.New("관리 서비스 정의가 Railwait가 만든 형식과 달라 관리 서비스를 바꾸지 않았습니다; 정의를 확인한 뒤 다시 실행하세요")

func executableVersion(ctx context.Context, executable string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	output := &bytes.Buffer{}
	cmd := exec.CommandContext(ctx, executable, "version")
	cmd.Stdout = output
	cmd.Stderr = io.Discard
	if err := cmd.Run(); err != nil {
		return "", err
	}
	match := versionLine.FindStringSubmatch(strings.TrimSpace(output.String()))
	if match == nil {
		return "", errors.New("관리 실행 파일의 버전 출력을 읽지 못했습니다")
	}
	return match[1], nil
}

// releaseVersion parses the X.Y.Z version an official CLI is built with. Development
// builds report something else and are never treated as newer automatically.
func releaseVersion(value string) ([3]int, bool) {
	var parsed [3]int
	parts := strings.Split(value, ".")
	if len(parts) != 3 {
		return parsed, false
	}
	for n, part := range parts {
		number, err := strconv.Atoi(part)
		if err != nil || number < 0 || strconv.Itoa(number) != part {
			return parsed, false
		}
		parsed[n] = number
	}
	return parsed, true
}

func newerRelease(candidate, installed string) bool {
	a, okA := releaseVersion(candidate)
	b, okB := releaseVersion(installed)
	if !okA || !okB {
		return false
	}
	for n := range a {
		if a[n] != b[n] {
			return a[n] > b[n]
		}
	}
	return false
}

// managerUpgradeResult says what upgrade did; From and To are empty when nothing changed.
type managerUpgradeResult struct{ From, To string }

// upgrade replaces the installed manager when this CLI is a newer release, or with force
// when the versions cannot be compared (development builds). It keeps the previous
// executable and starts it again when the new manager does not come up.
func (u managerUpgrader) upgrade(ctx context.Context, path, source, current string, force bool) (managerUpgradeResult, error) {
	dest := managerExecutablePath(path)
	info, err := os.Lstat(dest)
	if errors.Is(err, os.ErrNotExist) {
		return managerUpgradeResult{}, nil
	}
	if err != nil {
		return managerUpgradeResult{}, err
	}
	if !info.Mode().IsRegular() {
		return managerUpgradeResult{}, fmt.Errorf("기존 관리 실행 파일이 실제 파일이 아닙니다")
	}
	source, err = filepath.EvalSymlinks(source)
	if err != nil {
		return managerUpgradeResult{}, err
	}
	replacement, err := os.ReadFile(source)
	if err != nil {
		return managerUpgradeResult{}, err
	}
	previous, err := os.ReadFile(dest)
	if err != nil {
		return managerUpgradeResult{}, err
	}
	if bytes.Equal(previous, replacement) {
		return managerUpgradeResult{}, nil
	}
	installed, versionErr := u.version(ctx, dest)
	if !force && (versionErr != nil || !newerRelease(current, installed)) {
		return managerUpgradeResult{}, nil
	}
	if installed == "" {
		installed = "알 수 없음"
	}
	result := managerUpgradeResult{From: installed, To: current}
	// The installation lock is held by every update and settings job, so the manager is
	// never replaced in the middle of one.
	lock, err := platform.AcquireLock(filepath.Join(path, ".railwait", "install.lock"))
	if errors.Is(err, platform.ErrLocked) {
		return result, fmt.Errorf("진행 중인 업데이트나 설정 변경이 끝난 뒤 다시 실행하세요")
	}
	if err != nil {
		return result, err
	}
	defer lock.Close()
	plan, err := platform.CurrentServicePlan(dest, path)
	if err != nil {
		return result, err
	}
	// A definition written from another shell is restarted as written; anything else
	// unknown stops here, while the old manager still runs.
	state, err := manager.InspectServiceDefinition(plan)
	if err != nil {
		return managerUpgradeResult{}, err
	}
	if state == manager.DefinitionUnknown {
		return managerUpgradeResult{}, fmt.Errorf("%w: %s", errServiceDefinitionReview, plan.Definition)
	}
	before, _ := manager.ReadHeartbeat(path)
	stopErr := u.runner.Run(ctx, plan.Stop)
	if err = u.waitStopped(ctx, path); err != nil {
		// The old manager still runs, so nothing was replaced; make sure it stays started.
		return result, errors.Join(fmt.Errorf("이전 관리 서비스를 멈추지 못했습니다: %w", err), stopErr, u.start(ctx, path, dest))
	}
	if err = writeManagerExecutable(dest, replacement); err != nil {
		return result, errors.Join(err, u.start(ctx, path, dest))
	}
	if err = u.start(ctx, path, dest); err == nil {
		err = u.waitRestarted(ctx, path, before)
	}
	if err == nil {
		return result, nil
	}
	restoreErr := writeManagerExecutable(dest, previous)
	if restoreErr == nil {
		_ = u.runner.Run(ctx, plan.Stop)
		if restoreErr = u.waitStopped(ctx, path); restoreErr == nil {
			restoreErr = u.start(ctx, path, dest)
		}
	}
	if restoreErr != nil {
		return result, errors.Join(fmt.Errorf("새 관리 서비스가 시작되지 않았고 이전 관리 서비스로도 되돌리지 못했습니다: %w", err), restoreErr)
	}
	return result, fmt.Errorf("새 관리 서비스가 시작되지 않아 이전 관리 서비스로 되돌렸습니다: %w", err)
}

func (u managerUpgrader) start(ctx context.Context, path, executable string) error {
	_, err := manager.InstallService(ctx, path, executable, u.runner)
	return err
}

// leftoverGrace is how long a stopped manager may take to release its serve lock before the
// process its heartbeat names is checked and ended. Task Scheduler's /End ends only the
// conhost that hosts the Windows manager, so the manager itself keeps running and holding
// the lock until the user logs off; launchd and systemd stop the manager itself.
var leftoverGrace = 3 * time.Second

// waitStopped waits until no manager process holds the manager's serve lock. A manager
// process the service manager left running is ended once it is verified as this
// installation's manager.
func (u managerUpgrader) waitStopped(ctx context.Context, path string) error {
	started := time.Now()
	deadline := started.Add(u.wait)
	var leftover *managerStillRunning
	checked := false
	for {
		lock, err := platform.AcquireLock(filepath.Join(manager.InstallationPaths(path).Root, "serve.lock"))
		if err == nil {
			return lock.Close()
		}
		if !errors.Is(err, platform.ErrLocked) {
			return err
		}
		if !checked && time.Since(started) >= leftoverGrace {
			checked = true
			leftover = u.endLeftover(path)
		}
		if time.Now().After(deadline) {
			if leftover == nil {
				leftover = &managerStillRunning{}
			}
			leftover.wait = u.wait
			return leftover
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(200 * time.Millisecond):
		}
	}
}

// managerStillRunning says a manager process kept the serve lock after its service was
// stopped. pid and executable name the process its heartbeat named, once the CLI checked it.
type managerStillRunning struct {
	wait       time.Duration
	pid        int
	executable string
	// ended is set when the process was ended but the lock stayed.
	ended bool
	// cause says why the process was not ended.
	cause error
}

func (e *managerStillRunning) Error() string {
	message := fmt.Sprintf("관리 서비스가 %s 안에 멈추지 않았습니다", e.wait)
	switch {
	case e.pid <= 0:
		return message
	case e.ended:
		return fmt.Sprintf("%s; heartbeat의 관리 프로세스(PID %d)를 끝냈지만 잠금이 풀리지 않았습니다", message, e.pid)
	}
	return fmt.Sprintf("%s; heartbeat의 PID %d는 끝내지 않았습니다(관리 실행 파일 %s): %v", message, e.pid, e.executable, e.cause)
}

func (e *managerStillRunning) Unwrap() error { return e.cause }

// endLeftover ends the manager process the heartbeat names when it is verified as this
// installation's manager. It describes the process it checked, or returns nil when the
// heartbeat names none or the service manager stops managers itself.
func (u managerUpgrader) endLeftover(path string) *managerStillRunning {
	beat, err := manager.ReadHeartbeat(path)
	if err != nil || beat.PID <= 0 {
		return nil
	}
	leftover := &managerStillRunning{pid: beat.PID, executable: managerExecutablePath(path)}
	started, err := time.Parse(time.RFC3339Nano, beat.StartedAt)
	if err != nil {
		leftover.cause = errors.New("heartbeat의 시작 시각을 읽지 못했습니다")
		return leftover
	}
	end := u.endProcess
	if end == nil {
		end = platform.EndManagerProcess
	}
	err = end(beat.PID, leftover.executable, started, 10*time.Second)
	if errors.Is(err, errors.ErrUnsupported) {
		return nil
	}
	if err != nil {
		leftover.cause = err
		return leftover
	}
	leftover.ended = true
	if u.notice != nil {
		u.notice(fmt.Sprintf("작업 스케줄러가 멈추지 못한 관리 프로세스(PID %d)를 끝냈습니다.", beat.PID))
	}
	return leftover
}

// waitRestarted waits for a live heartbeat from a manager process other than the old one.
func (u managerUpgrader) waitRestarted(ctx context.Context, path string, before manager.Heartbeat) error {
	deadline := time.Now().Add(u.wait)
	for {
		if beat, err := manager.ReadHeartbeat(path); err == nil && beat.StartedAt != before.StartedAt && manager.Available(path, time.Now()) {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("새 관리 서비스 heartbeat를 %s 안에 확인하지 못했습니다", u.wait)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(200 * time.Millisecond):
		}
	}
}

// writeManagerExecutable replaces the manager executable in place. Windows can refuse
// the rename for a moment after the old process exits, so it is retried briefly.
func writeManagerExecutable(dest string, data []byte) error {
	f, err := os.CreateTemp(filepath.Dir(dest), ".railwait-bin-*")
	if err != nil {
		return err
	}
	temp := f.Name()
	defer os.Remove(temp)
	if err = platform.SecureFile(temp); err == nil {
		_, err = f.Write(data)
	}
	if err == nil {
		err = f.Sync()
	}
	if err = errors.Join(err, f.Close()); err != nil {
		return err
	}
	if err = os.Chmod(temp, 0o700); err != nil {
		return err
	}
	deadline := time.Now().Add(10 * time.Second)
	for {
		err = os.Rename(temp, dest)
		if err == nil || runtime.GOOS != "windows" || time.Now().After(deadline) {
			return err
		}
		time.Sleep(250 * time.Millisecond)
	}
}
