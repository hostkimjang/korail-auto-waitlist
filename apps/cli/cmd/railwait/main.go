package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/install"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/manager"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/release"
	"golang.org/x/term"
)

var version = "dev"

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()
	if err := run(ctx, os.Args[1:]); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return
		}
		var shown *presentedError
		if !errors.As(err, &shown) {
			fmt.Fprintln(os.Stderr, "E_RAILWAIT:", err)
		}
		if errors.Is(err, context.Canceled) {
			os.Exit(130)
		}
		os.Exit(1)
	}
}

func run(ctx context.Context, args []string) error {
	resumedArgs, resumed, err := consumeWizardResume(args)
	if err != nil {
		return err
	}
	if resumed {
		args = resumedArgs
	}
	if len(args) == 0 {
		printTopHelp(helpOutput)
		return nil
	}
	name, rest := args[0], args[1:]
	if isHelpArg(name) {
		name = "help"
	} else if name == "--version" {
		name = "version"
	}
	handler, ok := commandHandler(name)
	if !ok {
		return unknownCommand(name)
	}
	if !portableCommand(name) && !platform.SupportedTarget(runtime.GOOS, runtime.GOARCH) {
		return fmt.Errorf("지원하지 않는 OS/CPU입니다")
	}
	return handler(ctx, rest)
}

// commandHandler returns the function that runs a top-level command.
func commandHandler(name string) (func(context.Context, []string) error, bool) {
	switch name {
	case "install", "setup":
		return installCommand, true
	case "preview":
		return previewCommand, true
	case "help":
		return helpCommand, true
	case "version":
		return versionCommand, true
	case "update":
		return updateCommand, true
	case "configure":
		return configureCommand, true
	case "manager":
		return managerCommand, true
	case "status":
		return statusCommand, true
	case "doctor":
		return doctorCommand, true
	case "package":
		return verifyPackageCommand, true
	case "restart":
		return restartCommand, true
	case "uninstall":
		return uninstallCommand, true
	}
	return nil, false
}

// portableCommand runs before the OS/CPU check: the installer explains preparation itself.
func portableCommand(name string) bool {
	return name == "install" || name == "setup" || name == "preview" || name == "help" || name == "version"
}

func versionCommand(_ context.Context, args []string) error {
	flags := newCommandFlags("version")
	if err := parseCommandFlags(flags, args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return fmt.Errorf("version은 인자를 받지 않습니다")
	}
	// manager upgrade reads this line from an installed manager executable.
	fmt.Printf("railwait %s %s/%s\n", version, runtime.GOOS, runtime.GOARCH)
	return nil
}

func statusCommand(ctx context.Context, args []string) error {
	defaultPath, err := platform.DefaultInstallDir()
	if err != nil {
		return err
	}
	flags := newCommandFlags("status")
	path := flags.String("path", defaultPath, "설치 경로")
	jsonOutput := flags.Bool("json", false, "비밀값 없는 JSON으로 출력")
	jobID := flags.String("job", "", "결과를 볼 관리 작업 ID")
	if err = parseCommandFlags(flags, args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return fmt.Errorf("위치 인자를 사용할 수 없습니다")
	}
	abs, err := filepath.Abs(*path)
	if err != nil {
		return err
	}
	if *jobID != "" {
		job, err := manager.ReadJob(abs, *jobID)
		if err != nil {
			// Jobs that the host CLI ran itself, such as local package updates, are recorded
			// by the installer rather than the manager.
			local, localErr := install.ReadUpdateJob(abs, *jobID)
			if localErr != nil {
				return err
			}
			if *jsonOutput {
				return json.NewEncoder(os.Stdout).Encode(local)
			}
			fmt.Printf("작업 %s: %s · 단계 %s\n현재: %s → 대상: %s\n", local.JobID, local.State, local.Stage, local.CurrentVersion, local.TargetVersion)
			if local.PreviousPackage != "" {
				fmt.Println("이전 로컬 패키지:", local.PreviousPackage)
			}
			for _, warning := range local.Warnings {
				fmt.Println("주의:", warning)
			}
			return nil
		}
		if *jsonOutput {
			return json.NewEncoder(os.Stdout).Encode(job)
		}
		fmt.Printf("작업 %s: %s · 단계 %s\n현재: %s → 대상: %s\n", job.ID, job.State, job.Stage, job.CurrentVersion, job.TargetVersion)
		return nil
	}
	i := newInstaller()
	s, err := i.Status(ctx, abs)
	if err != nil {
		return err
	}
	if *jsonOutput {
		return json.NewEncoder(os.Stdout).Encode(s)
	}
	fmt.Printf("릴리스: %s · 설치 단계: %s · healthy: %t\n", s.Installation.Release, s.Installation.Stage, s.Healthy)
	for _, service := range s.Services {
		fmt.Printf("%s: %s / %s\n", service.Service, service.State, service.Health)
	}
	return nil
}

func doctorCommand(ctx context.Context, args []string) error {
	defaultPath, err := platform.DefaultInstallDir()
	if err != nil {
		return err
	}
	flags := newCommandFlags("doctor")
	path := flags.String("path", defaultPath, "설치 경로")
	dockerContext := flags.String("docker-context", "", "확인할 로컬 Docker context (설치 전 기본 default, 설치 후 기록한 값)")
	jsonOutput := flags.Bool("json", false, "JSON으로 출력")
	if err = parseCommandFlags(flags, args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return fmt.Errorf("위치 인자를 사용할 수 없습니다")
	}
	abs, err := filepath.Abs(*path)
	if err != nil {
		return err
	}
	i := newInstaller()
	if err = i.Doctor(ctx, abs, *dockerContext); err != nil {
		return fmt.Errorf("%w\n%s", err, platform.PrerequisiteHint())
	}
	if *jsonOutput {
		return json.NewEncoder(os.Stdout).Encode(map[string]any{"docker": "ready", "os": runtime.GOOS, "arch": runtime.GOARCH})
	}
	fmt.Println("로컬 Docker daemon과 Compose를 확인했습니다. 실제 철도사 인증·예약은 실행하지 않았습니다.")
	return nil
}

func verifyPackageCommand(ctx context.Context, args []string) error {
	if len(args) > 0 && isHelpArg(args[0]) {
		args = []string{"verify", args[0]}
	}
	if len(args) == 0 || args[0] != "verify" {
		return fmt.Errorf("package verify 명령이 필요합니다; railwait package --help 로 사용법을 확인하세요")
	}
	flags := newCommandFlags("package")
	path := flags.String("package", "", "검증할 로컬 패키지 폴더")
	key := flags.String("trust-key", "", "직접 신뢰할 공개키 파일")
	if err := parseCommandFlags(flags, args[1:]); err != nil {
		return err
	}
	if *path == "" || *key == "" || flags.NArg() != 0 {
		return fmt.Errorf("--package와 --trust-key가 필요합니다")
	}
	fmt.Fprintln(os.Stderr, "로컬 패키지 서명·번들·이미지 파일을 검증하는 중입니다.")
	source, err := release.OpenLocal(ctx, *path, *key)
	if err != nil {
		return err
	}
	fmt.Printf("로컬 패키지 검증 완료: v%s · 서비스 %d개 · 이미지 archive %d bytes\n", source.Manifest.Version, len(source.Manifest.Images), source.Manifest.ImageArchive.SizeBytes)
	return nil
}

func updateCommand(ctx context.Context, args []string) error {
	check := len(args) > 0 && args[0] == "check"
	if check {
		args = args[1:]
	}
	defaultPath, err := platform.DefaultInstallDir()
	if err != nil {
		return err
	}
	flags := newCommandFlags("update")
	path := flags.String("path", defaultPath, "설치 경로")
	jsonOutput := flags.Bool("json", false, "JSON으로 출력")
	dryRun := flags.Bool("dry-run", false, "적용하지 않고 계획만 표시")
	target := flags.String("target", "", "--dry-run에서 확인한 대상 버전")
	planID := flags.String("plan", "", "--dry-run에서 확인한 계획 ID")
	packagePath := flags.String("package", "", "로컬 패키지 설치에 적용할 새 로컬 패키지 디렉터리")
	trustKey := flags.String("trust-key", "", "직접 신뢰할 새 로컬 패키지 공개키 파일")
	if err = parseCommandFlags(flags, args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return fmt.Errorf("위치 인자를 사용할 수 없습니다")
	}
	i := newInstaller()
	if *packagePath != "" || *trustKey != "" {
		if *packagePath == "" || *trustKey == "" {
			return fmt.Errorf("--package와 --trust-key를 함께 지정하세요")
		}
		if check {
			return fmt.Errorf("update check는 로컬 패키지를 받지 않습니다")
		}
		return localUpdateCommand(ctx, &i, localUpdateOptions{Path: *path, Package: *packagePath, TrustKey: *trustKey, Target: *target, Plan: *planID, DryRun: *dryRun, JSON: *jsonOutput})
	}
	if check || *dryRun || *target == "" || *planID == "" {
		if check {
			if _, err := install.ReadState(*path); err != nil {
				r, resolveErr := release.Resolve(ctx, "stable")
				if resolveErr != nil {
					return resolveErr
				}
				if *jsonOutput {
					return json.NewEncoder(os.Stdout).Encode(map[string]string{"version": r.Version, "checked_at": time.Now().UTC().Format(time.RFC3339)})
				}
				fmt.Println("검증된 안정 릴리스:", r.Version)
				return nil
			}
		}
		plan, err := i.PlanUpdate(ctx, *path)
		if err != nil {
			return err
		}
		if *jsonOutput {
			return json.NewEncoder(os.Stdout).Encode(plan)
		}
		fmt.Printf("현재: %s → 대상: %s\n", plan.CurrentReleaseID, plan.TargetReleaseID)
		if !plan.Allowed {
			fmt.Println("차단:", strings.Join(plan.BlockedReasons, "; "))
			return nil
		}
		fmt.Printf("검증 → 다운로드 → 작업 종료 대기 → 암호화 백업 → 교체 → 확인 → 재개\n적용: %s\n", pasteableCommand("update", "--path", *path, "--target", plan.TargetReleaseID, "--plan", plan.PlanID))
		return nil
	}
	if !manager.Available(*path, time.Now()) {
		return fmt.Errorf("관리 서비스가 실행 중이 아닙니다; %s 를 확인하세요", pasteableCommand("manager", "install", "--path", *path))
	}
	raw := make([]byte, 16)
	if _, err = rand.Read(raw); err != nil {
		return err
	}
	id := hex.EncodeToString(raw)
	request := manager.Request{Schema: 1, ID: id, TargetReleaseID: *target, PlanID: *planID, IdempotencyKey: id}
	if err = manager.Submit(*path, request); err != nil {
		return err
	}
	fmt.Fprintln(os.Stderr, "관리 작업 제출:", id)
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	stage := ""
	for {
		job, readErr := manager.ReadJob(*path, id)
		if readErr == nil {
			if job.Stage != stage {
				stage = job.Stage
				fmt.Fprintln(os.Stderr, "업데이트 단계:", stage)
			}
			if job.State != "running" {
				if *jsonOutput {
					if err = json.NewEncoder(os.Stdout).Encode(job); err != nil {
						return err
					}
				} else {
					fmt.Printf("작업 %s: %s\n", id, job.State)
				}
				if job.State != "succeeded" {
					return fmt.Errorf("업데이트가 %s 상태입니다; %s 로 확인하세요", job.State, pasteableCommand("status", "--path", *path, "--job", id))
				}
				return nil
			}
		}
		select {
		case <-ctx.Done():
			return fmt.Errorf("진행 화면을 닫았습니다; 관리 작업 %s는 계속 실행됩니다", id)
		case <-ticker.C:
		}
	}
}

func newInstaller() install.Installer {
	return install.Installer{Runner: install.ExecRunner{}, Releases: install.OfficialReleases{}}
}

func configureCommand(ctx context.Context, args []string) error {
	if len(args) > 0 && args[0] == "reservation" {
		return configureReservationCommand(ctx, args[1:])
	}
	if len(args) > 0 && args[0] == "tago" {
		args = args[1:]
	}
	defaultPath, err := platform.DefaultInstallDir()
	if err != nil {
		return err
	}
	flags := newCommandFlags("configure")
	path := flags.String("path", defaultPath, "설치 경로")
	input := flags.String("input", "", "보호된 TAGO JSON 파일 또는 stdin: -")
	planID := flags.String("plan", "", "확인한 설정 변경 계획 ID")
	jsonOutput := flags.Bool("json", false, "JSON으로 출력")
	flags.Bool("dry-run", true, "변경 계획만 표시 (기본 동작)")
	if err = parseCommandFlags(flags, args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return fmt.Errorf("위치 인자를 사용할 수 없습니다")
	}
	if *planID != "" {
		state, err := install.ReadState(*path)
		if err != nil {
			return err
		}
		submitArgs := []string{"--path", *path, "--target", state.Release, "--plan", *planID}
		if *jsonOutput {
			submitArgs = append(submitArgs, "--json")
		}
		return updateCommand(ctx, submitArgs)
	}
	var key string
	if *input != "" {
		value, err := readInstallInput(*input)
		if err != nil {
			return err
		}
		if value.Username != "" || value.Password != "" {
			return fmt.Errorf("configure 입력에는 tago_service_key만 사용할 수 있습니다")
		}
		key = value.TAGOKey
	} else {
		if !term.IsTerminal(int(os.Stdin.Fd())) {
			return fmt.Errorf("대화형 터미널 또는 --input이 필요합니다")
		}
		fmt.Fprint(os.Stderr, "TAGO 인증키 (지우려면 Enter): ")
		raw, err := term.ReadPassword(int(os.Stdin.Fd()))
		fmt.Fprintln(os.Stderr)
		if err != nil {
			return err
		}
		key = string(raw)
		for n := range raw {
			raw[n] = 0
		}
	}
	i := newInstaller()
	plan, err := i.PlanConfigureTAGO(ctx, *path, key)
	key = ""
	if err != nil {
		return err
	}
	if *jsonOutput {
		return json.NewEncoder(os.Stdout).Encode(plan)
	}
	if !plan.Allowed {
		fmt.Println("차단:", strings.Join(plan.BlockedReasons, "; "))
		return nil
	}
	fmt.Printf("TAGO_SERVICE_KEY 변경: 작업 종료 대기 → 암호화 백업 → 앱 재시작 → 확인\n적용: %s\n", pasteableCommand("configure", "--path", *path, "--plan", plan.PlanID))
	return nil
}

// configureReservationCommand turns the installation-wide automatic reservation
// switches on or off. Each watch still chooses automatic reservation on its own.
func configureReservationCommand(ctx context.Context, args []string) error {
	defaultPath, err := platform.DefaultInstallDir()
	if err != nil {
		return err
	}
	flags := newCommandFlags("configure reservation")
	path := flags.String("path", defaultPath, "설치 경로")
	planID := flags.String("plan", "", "확인한 설정 변경 계획 ID")
	jsonOutput := flags.Bool("json", false, "JSON으로 출력")
	flags.Bool("dry-run", true, "변경 계획만 표시 (기본 동작)")
	if shown, err := subcommandHelp(flags, args); shown {
		return err
	}
	if len(args) == 0 || (args[0] != "on" && args[0] != "off") {
		return fmt.Errorf("configure reservation on 또는 configure reservation off로 지정하세요")
	}
	enabled := args[0] == "on"
	if err = parseCommandFlags(flags, args[1:]); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return fmt.Errorf("위치 인자를 사용할 수 없습니다")
	}
	abs, err := filepath.Abs(*path)
	if err != nil {
		return err
	}
	i := newInstaller()
	if *planID != "" {
		planned, err := install.ReservationPlanEnables(abs, *planID)
		if err != nil {
			return err
		}
		if planned != enabled {
			return fmt.Errorf("계획과 다른 동작입니다; configure reservation %s 로 계획을 다시 만드세요", args[0])
		}
		state, err := install.ReadState(abs)
		if err != nil {
			return err
		}
		return applyPlanInProcess(ctx, &i, abs, state.Release, *planID, *jsonOutput, inProcessJob{noun: "설정 변경"})
	}
	plan, err := i.PlanConfigureReservation(ctx, abs, enabled)
	if err != nil {
		return err
	}
	if *jsonOutput {
		return json.NewEncoder(os.Stdout).Encode(plan)
	}
	if !plan.Allowed {
		fmt.Println("차단:", strings.Join(plan.BlockedReasons, "; "))
		return nil
	}
	if enabled {
		fmt.Println("자동 예매 켜기: 작업 종료 대기 → 암호화 백업 → 앱 재시작 → 확인")
		fmt.Println("켠 뒤에는 '좌석 재발견마다 자동 예매'를 고른 대기가 좌석을 찾으면 저장한 철도 계정으로 결제 직전까지 예매합니다. 결제는 하지 않습니다.")
	} else {
		fmt.Println("자동 예매 끄기: 작업 종료 대기 → 암호화 백업 → 앱 재시작 → 확인")
		fmt.Println("끈 뒤에는 자동 예매를 고른 대기도 좌석을 찾으면 알림만 보냅니다.")
	}
	fmt.Println("적용:", pasteableCommand("configure", "reservation", args[0], "--path", abs, "--plan", plan.PlanID))
	return nil
}

// inProcessJob names a job the CLI runs itself in its terminal messages.
type inProcessJob struct {
	// noun is the job's name, such as "설정 변경"; it takes the particle 이.
	noun string
	// stages optionally explains stage names next to them.
	stages map[string]string
}

// errOperationRunning refuses a command while another update, settings change or restart
// holds the installation lock.
var errOperationRunning = errors.New("진행 중인 업데이트나 설정 변경이 끝난 뒤 다시 실행하세요")

// applyPlanInProcess applies a settings or restart plan in this CLI process under the
// installation lock, like a local package update. The host manager does not update
// itself, and one from an earlier release rejects plan kinds it does not know.
func applyPlanInProcess(ctx context.Context, i *install.Installer, path, target, planID string, jsonOutput bool, job inProcessJob) error {
	raw := make([]byte, 16)
	if _, err := rand.Read(raw); err != nil {
		return err
	}
	id := hex.EncodeToString(raw)
	// As with local package updates, a dropped terminal must not abandon the change
	// half way; a termination request still ends through the failure path.
	signal.Ignore(syscall.SIGHUP, syscall.SIGPIPE)
	ctx, stop := signal.NotifyContext(ctx, syscall.SIGTERM)
	defer stop()
	fmt.Fprintf(os.Stderr, "%s 작업: %s\n", job.noun, id)
	result, err := i.Update(ctx, install.UpdateOptions{Path: path, TargetReleaseID: target, PlanID: planID, JobID: id, Progress: func(stage string) error {
		if label := job.stages[stage]; label != "" {
			fmt.Fprintf(os.Stderr, "%s 단계: %s · %s\n", job.noun, stage, label)
		} else {
			fmt.Fprintf(os.Stderr, "%s 단계: %s\n", job.noun, stage)
		}
		return nil
	}})
	for _, warning := range result.Warnings {
		fmt.Fprintln(os.Stderr, "주의:", warning)
	}
	if errors.Is(err, platform.ErrLocked) {
		return errOperationRunning
	}
	if err != nil {
		return fmt.Errorf("%s이 %s 상태입니다; %s 로 확인하세요: %w", job.noun, result.State, pasteableCommand("status", "--path", path, "--job", id), err)
	}
	if jsonOutput {
		return json.NewEncoder(os.Stdout).Encode(result)
	}
	fmt.Printf("%s 작업 %s: %s\n", job.noun, id, result.State)
	return nil
}

func managerCommand(ctx context.Context, args []string) error {
	defaultPath, err := platform.DefaultInstallDir()
	if err != nil {
		return err
	}
	flags := newCommandFlags("manager")
	path := flags.String("path", defaultPath, "설치 경로")
	force := flags.Bool("force", false, "버전을 비교할 수 없는 개발용 CLI로도 관리 서비스를 교체 (upgrade)")
	if shown, err := subcommandHelp(flags, args); shown {
		return err
	}
	if len(args) == 0 {
		return fmt.Errorf("manager %s 명령이 필요합니다; railwait manager --help 로 사용법을 확인하세요", subcommandList("manager"))
	}
	if err = parseCommandFlags(flags, args[1:]); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return fmt.Errorf("위치 인자를 사용할 수 없습니다")
	}
	abs, err := filepath.Abs(*path)
	if err != nil {
		return err
	}
	switch args[0] {
	case "install":
		_, err = installOrUpgradeManager(ctx, abs)
		return err
	case "upgrade":
		if _, err = install.ReadState(abs); err != nil {
			return err
		}
		exe, err := os.Executable()
		if err != nil {
			return err
		}
		result, err := newManagerUpgrader().upgrade(ctx, abs, exe, version, *force)
		if err != nil {
			return err
		}
		if result.To == "" {
			if _, statErr := os.Lstat(managerExecutablePath(abs)); errors.Is(statErr, os.ErrNotExist) {
				return fmt.Errorf("관리 서비스가 아직 설치되지 않았습니다; %s 를 실행하세요", pasteableCommand("manager", "install", "--path", abs))
			}
			fmt.Println("관리 서비스가 이미 이 CLI 이상 버전입니다. 개발용 CLI로 바꾸려면 --force를 붙입니다.")
			return nil
		}
		fmt.Printf("관리 서비스를 교체했습니다: %s → %s\n", result.From, result.To)
		return nil
	case "status":
		if !manager.Available(abs, time.Now()) {
			return fmt.Errorf("관리 서비스 heartbeat를 확인하지 못했습니다")
		}
		fmt.Println("관리 서비스 실행 중")
		return nil
	case "serve":
		i := newInstaller()
		return manager.RunWithOptions(ctx, abs, func(ctx context.Context, r manager.Request, progress manager.Progress) (manager.Result, error) {
			result, err := i.Update(ctx, install.UpdateOptions{Path: abs, TargetReleaseID: r.TargetReleaseID, PlanID: r.PlanID, JobID: r.ID, Progress: progress})
			return manager.Result{State: result.State, Stage: result.Stage, CurrentVersion: result.CurrentVersion, TargetVersion: result.TargetVersion}, err
		}, manager.Options{
			RefreshCatalog: func(ctx context.Context) (manager.Catalog, error) {
				plan, err := i.PlanUpdate(ctx, abs)
				if err != nil {
					return manager.Catalog{}, err
				}
				return manager.Catalog{Schema: 1, CheckedAt: time.Now().UTC().Format(time.RFC3339), CurrentVersion: plan.CurrentReleaseID, LatestVersion: plan.TargetReleaseID, PlanID: plan.PlanID, Available: plan.Allowed, Reason: plan.Reason}, nil
			},
			PlanSettings: func(ctx context.Context, r manager.SettingsRequest) (manager.Request, error) {
				return i.PlanSettingsRequest(ctx, abs, r)
			},
			SettingsSnapshot: func() (manager.SettingsSnapshot, error) { return install.SettingsSnapshot(abs) },
			PlanRestart: func(ctx context.Context, r manager.RestartRequest) (manager.Request, error) {
				return i.PlanRestartRequest(ctx, abs, r)
			},
			Version: version,
		})
	default:
		return fmt.Errorf("지원하지 않는 manager 명령입니다: %q; railwait manager --help 로 사용법을 확인하세요", args[0])
	}
}

// installOrUpgradeManager starts this installation's manager, or replaces a running one
// built by an older release with this CLI. It reports the replaced versions.
func installOrUpgradeManager(ctx context.Context, path string) (managerUpgradeResult, error) {
	exe, err := os.Executable()
	if err != nil {
		return managerUpgradeResult{}, err
	}
	if _, err = install.ReadState(path); err != nil {
		return managerUpgradeResult{}, err
	}
	result, err := newManagerUpgrader().upgrade(ctx, path, exe, version, false)
	if errors.Is(err, errServiceDefinitionReview) && manager.Available(path, time.Now()) {
		// The running manager keeps working; only its replacement needs the review.
		fmt.Fprintln(os.Stderr, "주의:", err)
		return managerUpgradeResult{}, nil
	}
	if err != nil || result.To != "" || manager.Available(path, time.Now()) {
		return result, err
	}
	return result, installManager(ctx, path, exe)
}

func installManager(ctx context.Context, path, exe string) error {
	if _, err := install.ReadState(path); err != nil {
		return err
	}
	plan, err := platform.CurrentServicePlan(managerExecutablePath(path), path)
	if err != nil {
		return err
	}
	// 1.4.5 wrote a Windows task definition that Task Scheduler always rejected, so
	// the manager executable kept beside it was never started and may be replaced.
	neverStarted, err := manager.HasLegacyDefinition(plan)
	if err != nil {
		return err
	}
	exe, err = copyManagerExecutable(path, exe, neverStarted)
	if err != nil {
		return err
	}
	plan, err = manager.InstallService(ctx, path, exe, nil)
	if err != nil {
		return err
	}
	fmt.Fprintln(os.Stderr, plan.Availability)
	waitCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	return manager.WaitAvailable(waitCtx, path)
}

func managerExecutablePath(installation string) string {
	return filepath.Join(installation, ".railwait", "bin", platform.ExecutableName())
}

// copyManagerExecutable keeps the running CLI as this installation's manager. A
// different existing manager is replaced only when replaceUnstarted says it never
// ran as a service; otherwise updating the manager itself needs a separate review.
func copyManagerExecutable(installation, source string, replaceUnstarted bool) (string, error) {
	source, err := filepath.EvalSymlinks(source)
	if err != nil {
		return "", err
	}
	data, err := os.ReadFile(source)
	if err != nil {
		return "", err
	}
	dir := filepath.Join(installation, ".railwait", "bin")
	if err = platform.SecureDir(dir); err != nil {
		return "", err
	}
	dest := managerExecutablePath(installation)
	if info, err := os.Lstat(dest); err == nil {
		if !info.Mode().IsRegular() {
			return "", fmt.Errorf("기존 관리 실행 파일이 실제 파일이 아닙니다")
		}
		existing, err := os.ReadFile(dest)
		if err != nil {
			return "", err
		}
		if bytes.Equal(existing, data) {
			return dest, nil
		}
		if !replaceUnstarted {
			return "", fmt.Errorf("다른 관리 실행 파일이 있습니다; 관리 서비스 자체 업데이트는 별도 검토가 필요합니다")
		}
	} else if !os.IsNotExist(err) {
		return "", err
	}
	f, err := os.CreateTemp(dir, ".railwait-bin-*")
	if err != nil {
		return "", err
	}
	temp := f.Name()
	defer os.Remove(temp)
	if err = platform.SecureFile(temp); err == nil {
		_, err = f.Write(data)
	}
	if err == nil {
		err = f.Sync()
	}
	err = errors.Join(err, f.Close())
	if err != nil {
		return "", err
	}
	if err = os.Chmod(temp, 0700); err != nil {
		return "", err
	}
	if err = os.Rename(temp, dest); err != nil {
		return "", err
	}
	if runtime.GOOS != "windows" {
		directory, err := os.Open(dir)
		if err != nil {
			return "", err
		}
		if err = errors.Join(directory.Sync(), directory.Close()); err != nil {
			return "", err
		}
	}
	return dest, nil
}

type installInput struct {
	Username string              `json:"username"`
	Password string              `json:"password"`
	TAGOKey  string              `json:"tago_service_key"`
	Secrets  map[string]string   `json:"app_secrets,omitempty"`
	Push     install.PushOptions `json:"webpush,omitempty"`
}

func readInstallInput(path string) (installInput, error) {
	var value installInput
	var reader io.Reader = os.Stdin
	if path != "-" {
		info, err := os.Lstat(path)
		if err != nil {
			return value, err
		}
		if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 {
			return value, fmt.Errorf("설치 입력은 실제 파일이어야 합니다")
		}
		if runtime.GOOS != "windows" && info.Mode().Perm()&0077 != 0 {
			return value, fmt.Errorf("입력 파일 권한을 0600으로 제한하세요")
		}
		if err = platform.SecureFile(path); err != nil {
			return value, err
		}
		f, err := os.Open(path)
		if err != nil {
			return value, err
		}
		defer f.Close()
		reader = f
	}
	data, err := io.ReadAll(io.LimitReader(reader, 8193))
	if err != nil {
		return value, err
	}
	defer func() {
		for n := range data {
			data[n] = 0
		}
	}()
	if len(data) > 8192 {
		return value, fmt.Errorf("설치 입력이 너무 큽니다")
	}
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	decoder.DisallowUnknownFields()
	if err = decoder.Decode(&value); err != nil {
		return value, fmt.Errorf("설치 JSON 입력이 잘못되었습니다")
	}
	if err = decoder.Decode(new(any)); !errors.Is(err, io.EOF) {
		return value, fmt.Errorf("설치 JSON은 하나의 객체여야 합니다")
	}
	if err = install.ValidateInitialSecrets(value.Secrets); err != nil {
		return installInput{}, err
	}
	if err = install.ValidatePushOptions(value.Push); err != nil {
		return installInput{}, err
	}
	return value, nil
}
