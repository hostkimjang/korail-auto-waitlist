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
	if len(args) == 0 && term.IsTerminal(int(os.Stdin.Fd())) {
		return installCommand(ctx, nil)
	}
	if len(args) > 0 && (args[0] == "install" || args[0] == "setup") {
		return installCommand(ctx, args[1:])
	}
	if len(args) > 0 && args[0] == "preview" {
		return previewCommand(ctx, args[1:])
	}
	if len(args) == 0 || args[0] == "help" || args[0] == "--help" {
		fmt.Println("railwait install|setup|status|doctor|update|configure|manager|package|preview|version\n간편 설치: railwait install [--path <경로>] [--plain]\n직접 설정: railwait install --custom / 로컬 전용: --access local\n로컬 패키지: railwait install --path <새 경로> --package <패키지 디렉터리> --trust-key <공개키 파일>\n업데이트: railwait update --path <경로> --dry-run / --target <버전> --plan <계획ID>\nTAGO 설정: railwait configure --path <경로>\n비대화형: railwait install --input - < protected-input.json")
		return nil
	}
	if args[0] == "version" || args[0] == "--version" {
		fmt.Printf("railwait %s %s/%s\n", version, runtime.GOOS, runtime.GOARCH)
		return nil
	}
	if !platform.SupportedTarget(runtime.GOOS, runtime.GOARCH) {
		return fmt.Errorf("지원하지 않는 OS/CPU입니다")
	}
	if args[0] == "update" {
		return updateCommand(ctx, args[1:])
	}
	if args[0] == "manager" {
		return managerCommand(ctx, args[1:])
	}
	if args[0] == "configure" {
		return configureCommand(ctx, args[1:])
	}
	if args[0] == "package" {
		return verifyPackageCommand(ctx, args[1:])
	}
	if args[0] != "install" && args[0] != "status" && args[0] != "doctor" {
		return fmt.Errorf("알 수 없는 명령입니다; railwait help를 확인하세요")
	}
	defaultPath, err := platform.DefaultInstallDir()
	if err != nil {
		return err
	}
	flags := flag.NewFlagSet(args[0], flag.ContinueOnError)
	path := flags.String("path", defaultPath, "설치 디렉터리")
	dockerContext := flags.String("docker-context", "", "고정할 로컬 Docker context (신규 설치 기본 default)")
	jsonOutput := flags.Bool("json", false, "비밀값 없는 JSON 상태 출력")
	jobID := flags.String("job", "", "조회할 관리 작업 ID (status)")
	if err = flags.Parse(args[1:]); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return fmt.Errorf("위치 인자를 사용할 수 없습니다")
	}
	abs, err := filepath.Abs(*path)
	if err != nil {
		return err
	}
	i := install.Installer{Runner: install.ExecRunner{}, Releases: install.OfficialReleases{}}
	switch args[0] {
	case "status":
		if *jobID != "" {
			job, err := manager.ReadJob(abs, *jobID)
			if err != nil {
				return err
			}
			if *jsonOutput {
				return json.NewEncoder(os.Stdout).Encode(job)
			}
			fmt.Printf("작업 %s: %s · 단계 %s\n현재: %s → 대상: %s\n", job.ID, job.State, job.Stage, job.CurrentVersion, job.TargetVersion)
			return nil
		}
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
	case "doctor":
		if err = i.Doctor(ctx, abs, *dockerContext); err != nil {
			return fmt.Errorf("%w\n%s", err, platform.PrerequisiteHint())
		}
		if *jsonOutput {
			return json.NewEncoder(os.Stdout).Encode(map[string]any{"docker": "ready", "os": runtime.GOOS, "arch": runtime.GOARCH})
		}
		fmt.Println("로컬 Docker daemon과 Compose를 확인했습니다. 실제 철도사 인증·예약은 실행하지 않았습니다.")
	}
	return nil
}

func verifyPackageCommand(ctx context.Context, args []string) error {
	if len(args) == 0 || args[0] != "verify" {
		return fmt.Errorf("package verify 명령이 필요합니다")
	}
	flags := flag.NewFlagSet("package verify", flag.ContinueOnError)
	path := flags.String("package", "", "검증할 로컬 패키지")
	key := flags.String("trust-key", "", "직접 신뢰할 공개키")
	if err := flags.Parse(args[1:]); err != nil {
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
	flags := flag.NewFlagSet("update", flag.ContinueOnError)
	path := flags.String("path", defaultPath, "설치 경로")
	jsonOutput := flags.Bool("json", false, "JSON 출력")
	dryRun := flags.Bool("dry-run", false, "적용 계획만 표시")
	target := flags.String("target", "", "확인한 대상 버전")
	planID := flags.String("plan", "", "확인한 계획 ID")
	if err = flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return fmt.Errorf("위치 인자를 사용할 수 없습니다")
	}
	i := newInstaller()
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
		fmt.Printf("검증 → 다운로드 → 작업 종료 대기 → 암호화 백업 → 교체 → 확인 → 재개\n적용: railwait update --path %q --target %s --plan %s\n", *path, plan.TargetReleaseID, plan.PlanID)
		return nil
	}
	if !manager.Available(*path, time.Now()) {
		return fmt.Errorf("관리 서비스가 실행 중이 아닙니다; railwait manager install --path %q 를 확인하세요", *path)
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
					return fmt.Errorf("업데이트가 %s 상태입니다; railwait status --path %q --job %s 로 확인하세요", job.State, *path, id)
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
	if len(args) > 0 && args[0] == "tago" {
		args = args[1:]
	}
	defaultPath, err := platform.DefaultInstallDir()
	if err != nil {
		return err
	}
	flags := flag.NewFlagSet("configure", flag.ContinueOnError)
	path := flags.String("path", defaultPath, "설치 경로")
	input := flags.String("input", "", "보호된 TAGO JSON 파일 또는 stdin: -")
	planID := flags.String("plan", "", "확인한 설정 변경 계획 ID")
	jsonOutput := flags.Bool("json", false, "JSON 출력")
	flags.Bool("dry-run", true, "변경 계획만 표시 (기본 동작)")
	if err = flags.Parse(args); err != nil {
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
	fmt.Printf("TAGO_SERVICE_KEY 변경: 작업 종료 대기 → 암호화 백업 → 앱 재시작 → 확인\n적용: railwait configure --path %q --plan %s\n", *path, plan.PlanID)
	return nil
}

func managerCommand(ctx context.Context, args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("manager install|serve|status 명령이 필요합니다")
	}
	defaultPath, err := platform.DefaultInstallDir()
	if err != nil {
		return err
	}
	flags := flag.NewFlagSet("manager", flag.ContinueOnError)
	path := flags.String("path", defaultPath, "설치 경로")
	if err = flags.Parse(args[1:]); err != nil {
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
		return installManager(ctx, abs)
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
		}, manager.Options{RefreshCatalog: func(ctx context.Context) (manager.Catalog, error) {
			plan, err := i.PlanUpdate(ctx, abs)
			if err != nil {
				return manager.Catalog{}, err
			}
			return manager.Catalog{Schema: 1, CheckedAt: time.Now().UTC().Format(time.RFC3339), CurrentVersion: plan.CurrentReleaseID, LatestVersion: plan.TargetReleaseID, PlanID: plan.PlanID, Available: plan.Allowed, Reason: plan.Reason}, nil
		}})
	default:
		return fmt.Errorf("지원하지 않는 manager 명령입니다")
	}
}

func installManager(ctx context.Context, path string) error {
	if manager.Available(path, time.Now()) {
		return nil
	}
	if _, err := install.ReadState(path); err != nil {
		return err
	}
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	exe, err = copyManagerExecutable(path, exe)
	if err != nil {
		return err
	}
	plan, err := manager.InstallService(ctx, path, exe, nil)
	if err != nil {
		return err
	}
	fmt.Fprintln(os.Stderr, plan.Availability)
	waitCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	return manager.WaitAvailable(waitCtx, path)
}

func copyManagerExecutable(installation, source string) (string, error) {
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
	dest := filepath.Join(dir, platform.ExecutableName())
	if info, err := os.Lstat(dest); err == nil {
		if !info.Mode().IsRegular() {
			return "", fmt.Errorf("기존 관리 실행 파일이 실제 파일이 아닙니다")
		}
		existing, err := os.ReadFile(dest)
		if err != nil {
			return "", err
		}
		if !bytes.Equal(existing, data) {
			return "", fmt.Errorf("다른 관리 실행 파일이 있습니다; 관리 서비스 자체 업데이트는 별도 검토가 필요합니다")
		}
		return dest, nil
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
