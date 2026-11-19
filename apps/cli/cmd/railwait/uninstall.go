package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/access"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/install"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/manager"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/setup"
)

type uninstallOptions struct {
	Path       string
	DeleteData bool
	Yes        bool
}

// uninstaller removes an installation's host manager service and containers. With
// DeleteData it also removes the volumes and the installation folder. Images, the CLI
// itself and any Tailscale route stay; it prints how to remove them by hand.
type uninstaller struct {
	installer  install.Installer
	services   manager.ServiceRunner
	confirm    confirmation
	out        io.Writer
	goos       string
	home       string
	executable string
	// stopWait bounds how long the manager process may take to exit after unregistering.
	stopWait time.Duration
}

func uninstallCommand(ctx context.Context, args []string) error {
	defaultPath, err := platform.DefaultInstallDir()
	if err != nil {
		return err
	}
	flags := newCommandFlags("uninstall")
	path := flags.String("path", defaultPath, "설치 경로")
	deleteData := flags.Bool("delete-data", false, "DB·Redis·백업 볼륨과 설치 폴더(.env·복구키 포함)까지 영구 삭제")
	yes := flags.Bool("yes", false, "확인 질문 없이 진행 (대화형 터미널이 아니면 필요)")
	if err = parseCommandFlags(flags, args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return fmt.Errorf("위치 인자를 사용할 수 없습니다")
	}
	home, _ := os.UserHomeDir()
	executable, err := os.Executable()
	if err == nil {
		if resolved, resolveErr := filepath.EvalSymlinks(executable); resolveErr == nil {
			executable = resolved
		}
	}
	return uninstaller{
		installer: newInstaller(), services: manager.ExecServiceRunner{}, confirm: terminalConfirmation(), out: os.Stderr,
		goos: runtime.GOOS, home: home, executable: executable, stopWait: 30 * time.Second,
	}.run(ctx, uninstallOptions{Path: *path, DeleteData: *deleteData, Yes: *yes})
}

func (u uninstaller) run(ctx context.Context, o uninstallOptions) error {
	abs, err := filepath.Abs(o.Path)
	if err != nil {
		return err
	}
	state, err := install.ReadState(abs)
	if err != nil {
		return err
	}
	rerun := []string{"uninstall", "--path", abs}
	if o.DeleteData {
		rerun = append(rerun, "--delete-data")
	}
	lock, err := platform.AcquireLock(filepath.Join(abs, ".railwait", "install.lock"))
	if errors.Is(err, platform.ErrLocked) {
		return errOperationRunning
	}
	if err != nil {
		return err
	}
	defer lock.Close()
	if o.DeleteData {
		if err = deletableInstallation(abs, u.home, u.goos, u.executable); err != nil {
			return err
		}
	}
	service, err := platform.CurrentServicePlan(managerExecutablePath(abs), abs)
	if err != nil {
		return err
	}
	var volumes, images []string
	if state.ComposeFile != "" {
		// Both lists are shown only; a Docker that is not running fails the removal below.
		volumes, _ = u.installer.ProjectVolumes(ctx, state)
		images = u.installer.ReleaseImages(ctx, state)
	}
	remote, hasRemote := readRemoteAccess(state)
	u.printPlan(abs, state, service, volumes, len(images), hasRemote, o.DeleteData)
	if !o.Yes {
		if !u.confirm.interactive {
			return fmt.Errorf("대화형 터미널이 아니면 --yes를 붙여 실행하세요: %s", pasteableCommand(append(rerun, "--yes")...))
		}
		if err = u.confirmRemoval(abs, o.DeleteData); err != nil {
			return err
		}
	}
	fmt.Fprintln(u.out, "관리 서비스를 멈추고 등록을 해제하는 중입니다.")
	if err = manager.UnregisterService(ctx, service, u.services); err != nil {
		return fmt.Errorf("관리 서비스를 해제하지 못해 아무것도 지우지 않았습니다: %w", err)
	}
	if err = u.waitManagerStopped(ctx, abs); err != nil {
		return fmt.Errorf("관리 서비스 등록은 해제했지만 관리 프로세스가 멈추지 않아 컨테이너는 지우지 않았습니다; 잠시 뒤 %s 를 다시 실행하세요: %w", pasteableCommand(rerun...), err)
	}
	if state.ComposeFile != "" {
		if o.DeleteData {
			fmt.Fprintln(u.out, "앱 컨테이너·네트워크와 데이터 볼륨을 지우는 중입니다.")
		} else {
			fmt.Fprintln(u.out, "앱 컨테이너와 네트워크를 지우는 중입니다. 데이터 볼륨은 남깁니다.")
		}
		if err = u.installer.RemoveContainers(ctx, state, o.DeleteData); err != nil {
			return fmt.Errorf("관리 서비스는 해제했지만 Docker 컨테이너를 지우지 못했습니다; Docker가 실행 중인지 확인한 뒤 %s 를 다시 실행하세요: %w", pasteableCommand(rerun...), err)
		}
	}
	if o.DeleteData {
		if err = u.deleteFolder(ctx, abs, state, lock); err != nil {
			return err
		}
	}
	u.printResult(abs, state, volumes, images, remote, hasRemote, o.DeleteData)
	return nil
}

func (u uninstaller) confirmRemoval(path string, deleteData bool) error {
	base := filepath.Base(path)
	answer, err := u.confirm.ask(fmt.Sprintf("계속하려면 설치 폴더 이름(%s)을 입력하세요: ", base))
	if err != nil {
		return err
	}
	if answer != base {
		return errors.New("입력한 이름이 설치 폴더 이름과 달라 제거를 취소했습니다; 아무것도 바꾸지 않았습니다")
	}
	if !deleteData {
		return nil
	}
	answer, err = u.confirm.ask("DB·Redis·백업 볼륨과 설치 폴더는 되돌릴 수 없게 삭제됩니다. 정말 삭제하려면 DELETE를 입력하세요: ")
	if err != nil {
		return err
	}
	if answer != "DELETE" {
		return errors.New("데이터 삭제를 확인하지 않아 제거를 취소했습니다; 아무것도 바꾸지 않았습니다")
	}
	return nil
}

// waitManagerStopped waits until no manager process holds the serve lock, so nothing
// runs from the installation folder while its containers and files are removed.
func (u uninstaller) waitManagerStopped(ctx context.Context, path string) error {
	if _, err := os.Lstat(filepath.Join(manager.InstallationPaths(path).Root, "serve.lock")); errors.Is(err, os.ErrNotExist) {
		return nil
	}
	return managerUpgrader{wait: u.stopWait}.waitStopped(ctx, path)
}

// deleteFolder removes the installation folder after checking again that it is this
// installation's root. Container log folders are removed first because the host user may
// not own them.
func (u uninstaller) deleteFolder(ctx context.Context, path string, state install.State, lock *platform.Lock) error {
	if err := deletableInstallation(path, u.home, u.goos, u.executable); err != nil {
		return err
	}
	logsErr := u.installer.RemoveContainerLogs(ctx, state)
	// Windows cannot delete the lock file while it is open.
	if err := lock.Close(); err != nil {
		return err
	}
	fmt.Fprintln(u.out, "설치 폴더를 지우는 중입니다.")
	if err := os.RemoveAll(path); err != nil {
		hint := "남은 폴더는 직접 지우세요."
		if u.goos == "linux" {
			hint = "컨테이너가 만든 파일은 권한이 달라 남을 수 있습니다. 남은 폴더는 직접 지우세요: sudo rm -rf -- " + shellQuote(u.goos, path)
		}
		if logsErr != nil {
			hint += " (컨테이너 로그 폴더 정리 실패: " + logsErr.Error() + ")"
		}
		return fmt.Errorf("볼륨은 지웠지만 설치 폴더 %s 를 모두 지우지 못했습니다: %w; %s", path, err, hint)
	}
	return nil
}

// deletableInstallation lets --delete-data remove path only when it is this installation's
// root and removing it cannot take unrelated data with it: never a filesystem or drive
// root, the user's home folder or a folder that contains it.
func deletableInstallation(path, home, goos, executable string) error {
	clean := filepath.Clean(path)
	if !filepath.IsAbs(clean) {
		return fmt.Errorf("설치 경로는 절대경로여야 합니다: %s", path)
	}
	if home == "" {
		return errors.New("사용자 홈 폴더를 확인하지 못해 설치 폴더를 지우지 않습니다")
	}
	info, err := os.Lstat(clean)
	if err != nil {
		return err
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.IsDir() {
		return fmt.Errorf("설치 경로가 실제 폴더가 아니어서 지우지 않습니다: %s", clean)
	}
	candidates := withResolved(clean)
	homes := withResolved(filepath.Clean(home))
	for _, candidate := range candidates {
		if filepath.Dir(candidate) == candidate || volumeRoot(candidate) {
			return fmt.Errorf("파일 시스템이나 드라이브의 최상위 폴더는 지우지 않습니다: %s", candidate)
		}
		for _, homeDir := range homes {
			if samePath(candidate, homeDir, goos) {
				return fmt.Errorf("사용자 홈 폴더는 지우지 않습니다: %s", candidate)
			}
			if containsPath(candidate, homeDir, goos) {
				return fmt.Errorf("사용자 홈 폴더를 포함하는 폴더는 지우지 않습니다: %s", candidate)
			}
		}
	}
	data, err := os.ReadFile(filepath.Join(clean, ".railwait", "install.json"))
	if err != nil {
		return fmt.Errorf("설치 기록이 없는 폴더는 지우지 않습니다: %s", clean)
	}
	var record struct {
		Path string `json:"path"`
	}
	if json.Unmarshal(data, &record) != nil || record.Path == "" || !samePath(filepath.Clean(record.Path), clean, goos) {
		return fmt.Errorf("설치 기록의 경로가 이 폴더와 달라 지우지 않습니다: %s", clean)
	}
	if goos == "windows" && executable != "" && containsPath(clean, executable, goos) {
		return errors.New("실행 중인 railwait가 설치 폴더 안에 있어 지울 수 없습니다; 설치 폴더 밖에 있는 railwait로 다시 실행하세요")
	}
	return nil
}

func withResolved(path string) []string {
	paths := []string{path}
	if resolved, err := filepath.EvalSymlinks(path); err == nil && resolved != path {
		paths = append(paths, resolved)
	}
	return paths
}

func volumeRoot(path string) bool {
	volume := filepath.VolumeName(path)
	return volume != "" && (path == volume || path == volume+string(filepath.Separator))
}

// samePath compares paths the way the OS does; macOS and Windows folders are usually
// case-insensitive, so the comparison errs on the side of refusing.
func samePath(a, b, goos string) bool {
	if goos == "windows" || goos == "darwin" {
		return strings.EqualFold(a, b)
	}
	return a == b
}

// containsPath reports whether child is inside parent.
func containsPath(parent, child, goos string) bool {
	if goos == "windows" || goos == "darwin" {
		parent, child = strings.ToLower(parent), strings.ToLower(child)
	}
	rel, err := filepath.Rel(parent, child)
	return err == nil && rel != "." && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel)
}

type remoteAccess struct {
	URL                  string
	HTTPSPort, LocalPort int
}

// readRemoteAccess finds the Tailscale HTTPS route the installer connected to this app,
// from .railwait/access.json or else the allowed origin in .env. Only a valid Tailscale
// origin is reported, so the printed commands never carry other text.
func readRemoteAccess(s install.State) (remoteAccess, bool) {
	var record struct {
		URL       string `json:"url"`
		LocalPort int    `json:"local_port"`
		HTTPSPort int    `json:"https_port"`
	}
	if data, err := os.ReadFile(filepath.Join(s.Path, ".railwait", "access.json")); err == nil && len(data) <= 4096 && json.Unmarshal(data, &record) == nil {
		if access.ValidateOrigin(record.URL) == nil && record.HTTPSPort > 0 && record.HTTPSPort <= 65535 && record.LocalPort > 0 && record.LocalPort <= 65535 {
			return remoteAccess{URL: record.URL, HTTPSPort: record.HTTPSPort, LocalPort: record.LocalPort}, true
		}
	}
	data, err := os.ReadFile(filepath.Join(s.Path, ".env"))
	if err != nil {
		return remoteAccess{}, false
	}
	values, err := install.ParseEnv(string(data))
	origin := values["AUTH_ALLOWED_ORIGINS"]
	if err != nil || access.ValidateOrigin(origin) != nil {
		return remoteAccess{}, false
	}
	parsed, err := url.Parse(origin)
	if err != nil {
		return remoteAccess{}, false
	}
	port := 443
	if text := parsed.Port(); text != "" {
		if port, err = strconv.Atoi(text); err != nil {
			return remoteAccess{}, false
		}
	}
	return remoteAccess{URL: origin, HTTPSPort: port, LocalPort: s.Port}, true
}

func (u uninstaller) printPlan(path string, s install.State, service platform.ServicePlan, volumes []string, images int, remote, deleteData bool) {
	w := u.out
	volumeText := "Compose 프로젝트 " + s.Project + "의 볼륨"
	if len(volumes) > 0 {
		volumeText = strings.Join(volumes, ", ")
	}
	fmt.Fprintln(w, "Railwait 제거 계획:", path)
	fmt.Fprintln(w, "\n지울 항목")
	fmt.Fprintf(w, "  - 관리 서비스 %s (정의 파일 %s)\n", service.Name, service.Definition)
	if s.ComposeFile != "" {
		fmt.Fprintf(w, "  - Docker 컨테이너·네트워크 (Compose 프로젝트 %s)\n", s.Project)
	}
	if deleteData {
		fmt.Fprintf(w, "  - DB·Redis·백업 볼륨 (되돌릴 수 없음): %s\n", volumeText)
		fmt.Fprintf(w, "  - 설치 폴더 %s (.env 설정, 복구키 .railwait/backup.env, 설치 기록 포함)\n", path)
	}
	fmt.Fprintln(w, "\n남길 항목")
	if !deleteData {
		fmt.Fprintf(w, "  - DB·Redis·백업 볼륨: %s\n", volumeText)
		fmt.Fprintf(w, "  - 설치 폴더 %s (.env 설정, 복구키 .railwait/backup.env 포함)\n", path)
	}
	if images > 0 {
		fmt.Fprintf(w, "  - Docker 이미지 %d개 (제거 뒤 지우는 명령을 보여 줍니다)\n", images)
	}
	if remote {
		fmt.Fprintln(w, "  - Tailscale HTTPS 경로 (자동으로 지우지 않습니다)")
	}
	fmt.Fprintln(w, "  - railwait CLI 실행 파일")
	fmt.Fprintln(w)
}

func (u uninstaller) printResult(path string, s install.State, volumes, images []string, remote remoteAccess, hasRemote, deleteData bool) {
	w := u.out
	if deleteData {
		fmt.Fprintln(w, "제거를 마쳤습니다. 관리 서비스, 컨테이너, 데이터 볼륨과 설치 폴더를 지웠습니다.")
	} else {
		fmt.Fprintln(w, "제거를 마쳤습니다. 관리 서비스와 컨테이너를 지우고 데이터는 남겼습니다.")
		fmt.Fprintln(w, "\n남은 데이터")
		fmt.Fprintln(w, "  설치 폴더:", path)
		fmt.Fprintln(w, "  복구키:", filepath.Join(path, ".railwait", "backup.env"), "(백업 볼륨의 암호화 백업을 풀 때 필요합니다)")
		if len(volumes) > 0 {
			fmt.Fprintln(w, "  Docker 볼륨:", strings.Join(volumes, ", "))
		}
		fmt.Fprintln(w, "  나중에 볼륨과 설치 폴더를 모두 지우려면:")
		fmt.Fprintln(w, "    "+pasteableCommand("uninstall", "--path", path, "--delete-data"))
	}
	if len(images) > 0 {
		fmt.Fprintln(w, "\nDocker 이미지는 남겼습니다. 다른 곳에서 쓰지 않으면 직접 지우세요:")
		for _, image := range images {
			fmt.Fprintln(w, "  "+shellCommand(u.goos, "docker", "--context", s.DockerContext, "image", "rm", image))
		}
	}
	if hasRemote {
		tailscale, err := setup.Executable("tailscale")
		if err != nil {
			tailscale = "tailscale"
		}
		fmt.Fprintf(w, "\nTailscale HTTPS 경로 %s 는 자동으로 지우지 않았습니다.\n", remote.URL)
		fmt.Fprintf(w, "  1. %s 로 %d 포트가 이 앱(http://127.0.0.1:%d)으로만 연결되는지 확인하세요.\n", shellCommand(u.goos, tailscale, "serve", "status"), remote.HTTPSPort, remote.LocalPort)
		fmt.Fprintf(w, "  2. 이 앱만 쓰는 경로라면 %s 로 지우세요. 같은 포트를 다른 서비스가 쓰면 지우지 마세요.\n", shellCommand(u.goos, tailscale, "serve", "--https="+strconv.Itoa(remote.HTTPSPort), "off"))
	}
	if u.executable != "" && !(deleteData && containsPath(path, u.executable, u.goos)) {
		fmt.Fprintln(w, "\nrailwait CLI 실행 파일은 남아 있습니다:", u.executable)
		if u.goos == "windows" {
			fmt.Fprintln(w, "  Windows에서는 실행 중인 파일을 스스로 지울 수 없습니다. 이 창을 닫은 뒤 새 PowerShell에서 지우세요:")
			fmt.Fprintln(w, "    Remove-Item -LiteralPath "+shellQuote(u.goos, u.executable))
		} else {
			fmt.Fprintln(w, "  필요 없으면 지우세요: rm -- "+shellQuote(u.goos, u.executable))
		}
	}
}

// shellQuote quotes one argument as literal text for the user's shell: PowerShell on
// Windows, a POSIX shell elsewhere.
func shellQuote(goos, value string) string {
	if goos == "windows" {
		return "'" + strings.ReplaceAll(value, "'", "''") + "'"
	}
	return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'"
}

// shellCommand renders a command whose words are all quoted literals.
func shellCommand(goos, program string, args ...string) string {
	command, err := wizardShellCommand(goos, program, args...)
	if err != nil {
		return program + " " + strings.Join(args, " ")
	}
	return command
}
