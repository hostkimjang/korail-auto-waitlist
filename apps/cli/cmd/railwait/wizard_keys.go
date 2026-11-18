package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"strings"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/install"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/terminal"
)

func initialKeysInput(ctx context.Context, ui *terminal.UI, subject string, ask bool) (map[string]string, install.PushOptions, error) {
	ui.Section("보안·알림 키")
	mode := "1"
	var err error
	if ask {
		mode, err = choose(ctx, ui, "키 준비 [1]: ", "1 자동 생성 (권장)  ·  2 직접 입력", "1", "1", "2")
		if err != nil {
			return nil, install.PushOptions{}, err
		}
	}
	if mode == "1" {
		ui.Info("앱 보안키 6종과 브라우저 알림키를 설치 중 자동 생성해 안전하게 저장합니다.")
		return nil, install.PushOptions{Subject: subject}, nil
	}
	ui.Info("가지고 있는 키를 입력하세요. 빈 항목은 자동 생성하며 기존 설치의 키는 변경하지 않습니다.")
	secrets := make(map[string]string)
	labels := map[string]string{
		"POSTGRES_PASSWORD": "데이터베이스 비밀번호", "SECRET_ENCRYPTION_KEY": "저장 정보 암호화키",
		"AUTH_SESSION_SECRET": "로그인 세션키", "KORAIL_BROWSER_ADAPTER_TOKEN": "KORAIL 내부 연결키",
		"SRT_PROVIDER_ADAPTER_TOKEN": "SRT 내부 연결키", "KORAIL_PROVIDER_COOLDOWN_REDIS_PASSWORD": "내부 상태 저장소 키",
	}
	for _, name := range install.InitialSecretNames() {
		value, err := secretInput(ctx, ui, labels[name]+": ", "영문·숫자·밑줄·하이픈 32자 이상. Enter: 자동 생성")
		if err != nil {
			return nil, install.PushOptions{}, err
		}
		if value != "" {
			secrets[name] = value
		}
	}
	if err = install.ValidateInitialSecrets(secrets); err != nil {
		return nil, install.PushOptions{}, err
	}
	push, err := manualPushInput(ctx, ui, subject)
	return secrets, push, err
}

func pushKeysInput(ctx context.Context, ui *terminal.UI, subject string, ask bool) (install.PushOptions, error) {
	if ask {
		mode, err := choose(ctx, ui, "브라우저 알림키 [1]: ", "1 자동 생성 (권장)  ·  2 직접 입력", "1", "1", "2")
		if err != nil {
			return install.PushOptions{}, err
		}
		if mode == "2" {
			return manualPushInput(ctx, ui, subject)
		}
	}
	ui.Info("누락된 브라우저 알림키를 생성합니다. 기존 앱 보안키와 계정은 유지합니다.")
	return install.PushOptions{Subject: subject}, nil
}

func manualPushInput(ctx context.Context, ui *terminal.UI, subject string) (install.PushOptions, error) {
	ui.Info("VAPID 공개키와 비밀키는 같은 쌍이어야 합니다. 두 항목 모두 비우면 자동 생성합니다.")
	public, err := secretInput(ctx, ui, "알림 공개키: ", "P-256 base64url 형식")
	if err != nil {
		return install.PushOptions{}, err
	}
	private, err := secretInput(ctx, ui, "알림 비밀키: ", "P-256 base64url 형식")
	if err != nil {
		return install.PushOptions{}, err
	}
	ui.Prompt("알림 발신자 주소: ", "https://호스트명(포트·경로 제외) 또는 mailto:이메일. Enter: 자동 선택")
	value, err := readLine(ctx)
	if err != nil {
		return install.PushOptions{}, err
	}
	if value != "" {
		subject = value
	}
	push := install.PushOptions{PublicKey: strings.TrimSpace(public), PrivateKey: strings.TrimSpace(private), Subject: subject}
	return push, install.ValidatePushOptions(push)
}

func configureMissingPush(ctx context.Context, ui *terminal.UI, i *install.Installer, s install.State, push install.PushOptions) (err error) {
	ui.Start("브라우저 알림키 설정 사전 확인")
	plan, err := i.PlanConfigurePush(ctx, s.Path, push)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, removeWizardCandidate(s.Path, plan.PlanID)) }()
	if !plan.Allowed {
		return errors.New("브라우저 알림키 설정을 적용할 수 없습니다: " + strings.Join(plan.BlockedReasons, "; "))
	}
	raw := make([]byte, 16)
	if _, err = rand.Read(raw); err != nil {
		return err
	}
	_, err = i.Update(ctx, install.UpdateOptions{Path: s.Path, PlanID: plan.PlanID, TargetReleaseID: plan.TargetReleaseID, JobID: hex.EncodeToString(raw), Progress: func(stage string) error {
		labels := map[string]string{"checking": "알림 설정 재확인", "preparing_config": "알림키 설정 준비", "draining": "진행 중인 작업 종료 대기", "backing_up": "DB와 설정 암호화 백업", "applying": "브라우저 알림 설정 적용", "verifying": "전체 서비스 정상 상태 확인", "resuming": "작업 재개"}
		if label := labels[stage]; label != "" {
			ui.Start(label)
		}
		return nil
	}})
	if err != nil {
		return err
	}
	data, err := os.ReadFile(filepath.Join(s.Path, ".env"))
	if err != nil {
		return err
	}
	configured, err := install.PushConfigured(data)
	if err != nil {
		return err
	}
	if !configured {
		return errors.New("브라우저 알림키 설정 완료를 확인하지 못했습니다")
	}
	ui.Done("브라우저 알림키 준비됨")
	return nil
}
