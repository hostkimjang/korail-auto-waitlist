package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
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
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/terminal"
	"golang.org/x/term"
)

type wizardOptions struct {
	Path, DockerContext, Package, TrustKey, Input, Access string
	Port, HTTPSPort                                       int
	Plain, Yes, Open, Custom                              bool
}

func parseWizardOptions(args []string) (wizardOptions, error) {
	path, err := platform.DefaultInstallDir()
	if err != nil {
		return wizardOptions{}, err
	}
	var o wizardOptions
	f := flag.NewFlagSet("install", flag.ContinueOnError)
	f.StringVar(&o.Path, "path", path, "설치 폴더. 같은 폴더로 다시 실행하면 이어서 설정")
	f.StringVar(&o.DockerContext, "docker-context", "", "사용할 로컬 Docker 환경. 대화형 기본: 현재 환경")
	f.IntVar(&o.Port, "port", 0, "이 컴퓨터의 접속 포트. 새 설치 기본 8080")
	f.StringVar(&o.Package, "package", "", "서명된 로컬 설치 패키지 폴더")
	f.StringVar(&o.TrustKey, "trust-key", "", "직접 신뢰할 로컬 패키지 공개키 파일")
	f.StringVar(&o.Input, "input", "", "보호된 관리자 입력 JSON 파일 또는 stdin(-)")
	f.StringVar(&o.Access, "access", "", "접속 방식: local 또는 tailscale. 기존 HTTPS 설정은 유지")
	f.IntVar(&o.HTTPSPort, "https-port", 0, "Tailscale HTTPS 포트. 생략하면 기존 연결을 피해 선택")
	f.BoolVar(&o.Plain, "plain", false, "색·애니메이션 없이 출력")
	f.BoolVar(&o.Yes, "yes", false, "설치 계획 확인 생략. OS 승인·계정 로그인은 생략하지 않음")
	f.BoolVar(&o.Open, "open", false, "완료 후 접속 주소를 브라우저로 열기")
	f.BoolVar(&o.Custom, "custom", false, "접속 방식·보안키·TAGO 키를 직접 선택하는 고급 설치")
	if err = f.Parse(args); err != nil {
		return o, err
	}
	if f.NArg() != 0 {
		return o, errors.New("위치 인자를 사용할 수 없습니다")
	}
	if o.Access != "" && o.Access != "local" && o.Access != "tailscale" {
		return o, errors.New("--access는 local 또는 tailscale입니다")
	}
	if o.Port < 0 || o.Port > 65535 || o.HTTPSPort < 0 || o.HTTPSPort > 65535 {
		return o, errors.New("포트는 1~65535 범위입니다")
	}
	if (o.Package == "") != (o.TrustKey == "") {
		return o, errors.New("--package와 --trust-key를 함께 지정하세요")
	}
	if o.HTTPSPort != 0 && o.Access != "tailscale" {
		return o, errors.New("--https-port에는 --access tailscale이 필요합니다")
	}
	// Preserve file arguments across a reboot or a different shell working
	// directory. Never read administrator input to construct a resume command.
	for _, path := range []*string{&o.Path, &o.Package, &o.TrustKey, &o.Input} {
		if *path == "" || (path == &o.Input && *path == "-") {
			continue
		}
		*path, err = filepath.Abs(*path)
		if err != nil {
			return o, err
		}
	}
	return o, nil
}

// An unreadable record is not a fresh installation. Never ask for replacement
// credentials or generate new settings when the existing state needs recovery.
func existingInstallation(path string) (*install.State, error) {
	_, err := os.Lstat(filepath.Join(path, ".railwait", "install.json"))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	s, err := install.ReadState(path)
	if err != nil {
		return nil, err
	}
	return &s, nil
}

func installCommand(ctx context.Context, args []string) (err error) {
	o, err := parseWizardOptions(args)
	if err != nil {
		return err
	}
	caps := terminal.Detect(os.Stderr)
	if o.Plain {
		caps = terminal.Plain(caps.Width)
	}
	ui := terminal.New(os.Stderr, caps)
	defer ui.Close()
	defer func() {
		if err == nil {
			return
		}
		if ctx.Err() != nil {
			err = ctx.Err()
		}
		var reboot *prerequisiteRebootRequired
		if errors.As(err, &reboot) && runtime.GOOS == "windows" {
			ui.Pause()
			ui.Info("Windows 준비를 마치려면 재부팅이 필요합니다.")
			if resumeErr := scheduleWizardResume(ctx, o); resumeErr != nil {
				if ctx.Err() != nil {
					ui.Info("설정을 중단했습니다.")
					ui.Hint(resumeErr.Error())
					err = &presentedError{errors.Join(ctx.Err(), resumeErr)}
					return
				}
				ui.Hint("자동 재개를 등록하지 못했습니다: " + resumeErr.Error())
				showWizardResumeCommands(ui, o)
			} else {
				ui.Info("다음 Windows 로그인 때 설치 창이 한 번 자동으로 열립니다.")
				ui.Hint("작업을 저장한 뒤 Windows를 재부팅해 주세요. 설치 실행 파일과 로컬 패키지는 그대로 두세요.")
			}
			err = &presentedError{err}
			return
		}
		title := "설정을 마치지 못했습니다"
		if isUserStop(err) {
			title = "설정을 중단했습니다"
		}
		ui.Failure(title, err.Error(), "아래 명령으로 같은 설치 설정을 이어갈 수 있습니다.")
		showWizardResumeCommands(ui, o)
		err = &presentedError{err}
	}()
	interactive := term.IsTerminal(int(os.Stdin.Fd())) && o.Input == ""
	ui.Header(version)
	ui.KeyValue("환경", runtime.GOOS+" / "+runtime.GOARCH)
	ui.KeyValue("설치 폴더", o.Path)
	if !o.Custom {
		ui.Info("간편 설치 · 프로그램 준비와 보안키 생성은 자동으로 진행합니다.")
		ui.Hint("열린 창의 권한·약관 승인과 로그인을 진행해 주세요. 직접 설정하려면 --custom을 사용하세요.")
	}
	existing, err := existingInstallation(o.Path)
	if err != nil {
		return err
	}
	if existing != nil {
		if o.DockerContext != "" && o.DockerContext != existing.DockerContext {
			return errors.New("기록된 Docker 환경을 유지해 주세요")
		}
		if o.Port != 0 && o.Port != existing.Port {
			return errors.New("기록된 설치 포트를 유지해 주세요")
		}
		o.DockerContext, o.Port = existing.DockerContext, existing.Port
		ui.Info("재사용 · 기존 설치와 저장된 설정을 이어서 확인합니다.")
	}
	if (existing == nil || existing.Stage != "complete") && !interactive && o.Input == "" {
		return errors.New("새 설치에는 대화형 터미널 또는 --input이 필요합니다")
	}
	if o.Port == 0 {
		o.Port = 8080
	}
	if !interactive && o.DockerContext == "" {
		o.DockerContext = "default"
	}
	ui.Section("1 / 5  실행 환경")
	ui.Start("Docker 준비 상태 확인")
	var interact setup.Interact
	if interactive {
		interact = prerequisitePrompt(ctx, ui)
	}
	docker, err := setup.EnsureDocker(ctx, o.DockerContext, interact)
	if err != nil {
		return err
	}
	o.DockerContext = docker.Context
	ui.Done("Docker 준비됨 · " + docker.Context)
	currentOrigin, err := installedOrigin(o.Path, existing)
	if err != nil {
		return err
	}
	if o.Access == "" {
		if currentOrigin != "" {
			o.Access = "tailscale"
		} else if useAllInOneDefaults(o, interactive, existing != nil) {
			o.Access = "tailscale"
		} else if interactive && o.Custom && !o.Yes {
			answer, askErr := choose(ctx, ui, "접속 방식 [1]: ", "1 이 컴퓨터에서만  ·  2 내 Tailscale 기기에서도 (HTTPS)", "1", "1", "2")
			if askErr != nil {
				return askErr
			}
			if answer == "2" {
				o.Access = "tailscale"
			} else {
				o.Access = "local"
			}
		} else {
			o.Access = "local"
		}
	}
	if o.Access == "local" && currentOrigin != "" {
		return errors.New("기존 HTTPS 접속은 그대로 유지됩니다. --access tailscale로 다시 실행하세요. 로컬 전용으로 되돌리는 기능은 아직 제공하지 않습니다")
	}
	if currentOrigin != "" && o.HTTPSPort == 0 {
		u, _ := url.Parse(currentOrigin)
		o.HTTPSPort = 443
		if u.Port() != "" {
			o.HTTPSPort, _ = strconv.Atoi(u.Port())
		}
	}
	var remote *access.Client
	var remotePlan access.Plan
	if o.Access == "tailscale" {
		remote, remotePlan, err = prepareRemote(ctx, ui, o, interactive)
		if err != nil {
			return err
		}
	}
	ui.Section("2 / 5  설치 계획")
	if existing != nil && existing.Stage == "complete" {
		ui.Info("앱·관리자·API 키 재사용 → 자동 시작 확인 → 접속 설정")
	} else {
		ui.Info("설치 파일 검증 → 계정·설정 → 서비스 시작 → 접속 설정")
	}
	ui.KeyValue("이 컴퓨터", fmt.Sprintf("http://127.0.0.1:%d", o.Port))
	if remote != nil {
		ui.KeyValue("로그인 주소", remotePlan.URL)
		ui.Hint("HTTPS 보안 쿠키를 사용하므로 로그인은 위 HTTPS 주소에서 진행합니다.")
	} else {
		ui.Info("건너뜀 · Tailscale 원격 접속")
	}
	if o.Package != "" || (existing != nil && existing.ReleaseSource == "local_package") {
		ui.Hint("로컬 서명 패키지 · 정식 릴리스 자동 업데이트는 아직 제공되지 않습니다.")
	}
	if interactive && o.Custom && !o.Yes {
		answer, askErr := choose(ctx, ui, "진행 [1]: ", "1 이 계획으로 진행  ·  0 중단", "1", "1", "0")
		if askErr != nil {
			return askErr
		}
		if answer == "0" {
			return context.Canceled
		}
	}
	i := newInstaller()
	i.Progress = ui.Progress
	var s install.State
	ui.Section("3 / 5  앱과 계정")
	var provided *installInput
	if o.Input != "" {
		input, inputErr := readInstallInput(o.Input)
		if inputErr != nil {
			return inputErr
		}
		provided = &input
	}
	subjectOrigin := ""
	if remote != nil {
		subjectOrigin = remotePlan.URL
	}
	pushSubject, err := install.AutoPushSubject(subjectOrigin)
	if err != nil {
		return err
	}
	if existing != nil && existing.Stage == "complete" {
		ui.Start("기존 설치 상태 확인")
		if o.Package != "" || provided != nil {
			checkOptions := install.Options{Path: o.Path, Port: o.Port, DockerContext: o.DockerContext, PackagePath: o.Package, TrustKey: o.TrustKey}
			if provided != nil {
				checkOptions.Secrets = provided.Secrets
			}
			if _, checkErr := i.Install(ctx, checkOptions); checkErr != nil {
				return checkErr
			}
		}
		status, statusErr := i.Status(ctx, o.Path)
		if statusErr != nil {
			return statusErr
		}
		if !status.Healthy {
			return errors.New("기존 서비스가 정상 상태가 아닙니다. railwait status로 확인해 주세요")
		}
		s = status.Installation
		ui.Done("재사용 · 앱 정상, 관리자 계정과 API 키 유지")
	} else {
		opts := install.Options{Path: o.Path, Port: o.Port, DockerContext: o.DockerContext, PackagePath: o.Package, TrustKey: o.TrustKey}
		if provided != nil {
			input := *provided
			opts.TAGOKey = input.TAGOKey
			opts.Secrets, opts.Push = input.Secrets, input.Push
			if opts.Push.Subject == "" {
				opts.Push.Subject = pushSubject
			}
			opts.Credentials = func() (install.Credentials, error) {
				if e := validateAdministrator(strings.ToLower(strings.TrimSpace(input.Username)), input.Password); e != nil {
					return install.Credentials{}, e
				}
				return install.Credentials{Username: input.Username, Password: input.Password}, nil
			}
		} else {
			if existing == nil {
				opts.Secrets, opts.Push, err = initialKeysInput(ctx, ui, pushSubject, interactive && o.Custom && !o.Yes)
				if err != nil {
					return err
				}
				if o.Custom {
					key, keyErr := secretInput(ctx, ui, "TAGO API 키: ", "열차 시간표 검색에 사용합니다. 나중에 설정하려면 Enter를 누르세요.")
					if keyErr != nil {
						return keyErr
					}
					opts.TAGOKey = strings.TrimSpace(key)
				}
				if opts.TAGOKey == "" {
					ui.Info("건너뜀 · 시간표 검색 키는 나중에 설정")
				} else {
					ui.Done("시간표 검색 키 입력됨")
				}
				credentials, credErr := administratorInput(ctx, ui)
				if credErr != nil {
					return credErr
				}
				opts.Credentials = func() (install.Credentials, error) { return credentials, nil }
			} else {
				opts.Credentials = func() (install.Credentials, error) { return administratorInput(ctx, ui) }
				ui.Info("재사용 · 저장된 API 키와 설정을 유지합니다.")
			}
		}
		ui.Start("검증한 설치 준비")
		s, err = i.Install(ctx, opts)
		if err != nil {
			return err
		}
		ui.Done("앱 설치와 서비스 상태 확인 완료")
	}
	pushData, err := os.ReadFile(filepath.Join(s.Path, ".env"))
	if err != nil {
		return err
	}
	pushConfigured, err := install.PushConfigured(pushData)
	if err != nil {
		return err
	}
	push := install.PushOptions{Subject: pushSubject}
	pushChange := false
	if provided != nil {
		push = provided.Push
		if push.Subject == "" && !pushConfigured {
			push.Subject = pushSubject
		}
		if pushConfigured && push != (install.PushOptions{}) {
			candidate, candidateErr := install.PushEnvironment(pushData, push)
			if candidateErr != nil {
				return candidateErr
			}
			pushChange = pushConfigured && string(candidate) != string(pushData)
		}
	}
	if !pushConfigured || pushChange {
		if provided == nil {
			push, err = pushKeysInput(ctx, ui, pushSubject, interactive && o.Custom && !o.Yes)
			if err != nil {
				return err
			}
		}
		if err = configureMissingPush(ctx, ui, &i, s, push); err != nil {
			return err
		}
	} else {
		ui.Done("재사용 · 브라우저 알림키 준비됨")
	}
	ui.Section("4 / 5  자동 시작")
	ui.Start("사용자 관리 서비스 확인")
	if err = installManager(ctx, s.Path); err != nil {
		return fmt.Errorf("앱은 준비됐지만 자동 시작 등록이 필요합니다: %w", err)
	}
	ui.Done("로그인 시 관리 서비스 시작 · 현재 실행 확인")
	ui.Hint("앱 실행에는 Docker도 실행 중이어야 합니다. OS 재부팅은 이 과정에서 수행하지 않습니다.")
	ui.Section("5 / 5  접속 확인")
	address := fmt.Sprintf("http://127.0.0.1:%d", s.Port)
	if remote != nil {
		if err = connectRemote(ctx, ui, &i, s, *remote, remotePlan, interactive); err != nil {
			return fmt.Errorf("앱 설치는 완료됐지만 원격 접속 설정을 마치지 못했습니다: %w", err)
		}
		address = remotePlan.URL
	} else {
		ui.Start("웹 앱 응답 확인")
		if err = checkWeb(ctx, address); err != nil {
			return err
		}
		ui.Done("이 컴퓨터에서 접속 가능")
	}
	if !manager.Available(s.Path, time.Now()) {
		return errors.New("마지막 관리 서비스 heartbeat를 확인하지 못했습니다")
	}
	ui.Success("레일웨잇 준비가 끝났습니다")
	ui.KeyValue("접속 주소", address)
	ui.KeyValue("버전", s.Release)
	ui.Hint("입력한 관리자 계정으로 로그인하세요.")
	ui.Hint("상태 확인")
	if runtime.GOOS == "windows" {
		ui.Hint("PowerShell에서 실행하세요.")
	}
	showWizardStatusCommand(ui, s.Path)
	if o.Open || useAllInOneDefaults(o, interactive, existing != nil) {
		if openErr := setup.OpenURL(ctx, address); openErr != nil {
			ui.Hint("브라우저를 열지 못했습니다. 위 접속 주소를 직접 열어 주세요.")
		}
	}
	return nil
}

func installedOrigin(path string, s *install.State) (string, error) {
	if s == nil {
		return "", nil
	}
	data, err := os.ReadFile(filepath.Join(path, ".env"))
	if errors.Is(err, os.ErrNotExist) && s.Stage != "complete" {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	values, err := install.ParseEnv(string(data))
	if err != nil {
		return "", err
	}
	if values["AUTH_COOKIE_SECURE"] != "true" {
		return "", nil
	}
	origin := values["AUTH_ALLOWED_ORIGINS"]
	if err := access.ValidateOrigin(origin); err != nil {
		return "", errors.New("기존 HTTPS 접속 설정은 설치 마법사에서 자동 변경할 수 없습니다")
	}
	return origin, nil
}

func previewCommand(ctx context.Context, args []string) error {
	f := flag.NewFlagSet("preview", flag.ContinueOnError)
	plain := f.Bool("plain", false, "색·애니메이션 없이 표시")
	if err := f.Parse(args); err != nil {
		return err
	}
	if f.NArg() != 0 {
		return errors.New("preview는 --plain만 지원합니다")
	}
	caps := terminal.Detect(os.Stderr)
	if *plain {
		caps = terminal.Plain(caps.Width)
	}
	ui := terminal.New(os.Stderr, caps)
	defer ui.Close()
	ui.Header(version)
	ui.Info("화면 미리보기 · 설치나 설정을 변경하지 않는 예시입니다.")
	ui.Section("1 / 5  실행 환경")
	ui.Info("재사용 · 실행 중인 Docker")
	ui.Section("2 / 5  설치 계획")
	ui.KeyValue("접속 방식", "내 Tailscale 기기 · HTTPS")
	ui.Section("3 / 5  앱과 계정")
	ui.KeyValue("보안·알림 키", "자동 생성 · 직접 입력은 --custom")
	ui.Start("설치 파일 서명 확인 · 예시")
	if !*plain {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(1500 * time.Millisecond):
		}
	}
	ui.Done("서명 확인 완료 · 예시")
	ui.Info("재사용 · 관리자 계정과 API 키")
	ui.Section("4 / 5  자동 시작")
	ui.Info("사용자 로그인 시 시작 · 예시")
	ui.Section("5 / 5  접속 확인")
	ui.KeyValue("주소 예시", "https://my-device.example.ts.net:"+strconv.Itoa(8443))
	ui.Success("미리보기가 끝났습니다")
	ui.Hint("실제 설치: railwait install")
	return nil
}
