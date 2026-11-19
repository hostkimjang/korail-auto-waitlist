package main

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/install"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/manager"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/terminal"
)

// existingChoice is what someone who double-clicked railwait.exe chose to do with the
// installation that is already there.
type existingChoice int

const (
	existingOpen existingChoice = iota + 1
	existingProceed
	existingClose
)

// existingInstallSummary is what the wizard shows before it touches a finished installation:
// where it is, what runs there and what going ahead would change.
type existingInstallSummary struct {
	Path, Release, Manager, Address string
	Changes                         []string
}

// describeExistingInstall reads a finished installation without changing it. managerVersion
// reports the release an installed manager executable was built as.
func describeExistingInstall(ctx context.Context, path string, s install.State, managerVersion func(context.Context, string) (string, error)) existingInstallSummary {
	summary := existingInstallSummary{Path: path, Release: s.Release, Address: fmt.Sprintf("http://127.0.0.1:%d", s.Port)}
	origin, _ := installedOrigin(path, &s)
	if origin != "" {
		summary.Address = origin
	}
	summary.Changes = []string{"Docker와 앱 상태를 다시 확인합니다. 앱 버전, 관리자 계정과 저장된 설정은 그대로 둡니다."}
	running := manager.Available(path, time.Now())
	state := "응답 없음"
	if running {
		state = "실행 중"
	}
	executable := managerExecutablePath(path)
	if _, err := os.Lstat(executable); err != nil {
		summary.Manager = "설치되지 않음"
		summary.Changes = append(summary.Changes, "로그인할 때 시작하는 관리 서비스를 등록하고 시작합니다.")
	} else {
		installed, versionErr := managerVersion(ctx, executable)
		if versionErr != nil || installed == "" {
			installed = "버전 확인 못 함"
		}
		summary.Manager = installed + " · " + state
		switch {
		case newerRelease(version, installed):
			summary.Changes = append(summary.Changes, fmt.Sprintf("관리 서비스를 %s에서 %s로 바꿉니다. 바꾸는 동안 잠시 멈췄다 다시 시작합니다.", installed, version))
		case !running:
			summary.Changes = append(summary.Changes, "멈춰 있는 관리 서비스를 다시 시작합니다.")
		}
	}
	if origin != "" || recordedAccess(path) == "tailscale" {
		summary.Changes = append(summary.Changes, "Tailscale HTTPS 접속을 확인하고, 연결이 빠졌으면 다시 연결합니다.")
	}
	return summary
}

// existingInstallChoice shows the installation and asks what to do. The default opens the
// app and changes nothing.
func existingInstallChoice(ctx context.Context, ui *terminal.UI, summary existingInstallSummary, read func(context.Context) (string, error)) (existingChoice, error) {
	ui.Section("이미 설치되어 있습니다")
	ui.KeyValue("설치 폴더", summary.Path)
	ui.KeyValue("앱 버전", summary.Release)
	ui.KeyValue("관리 서비스", summary.Manager)
	ui.KeyValue("접속 주소", summary.Address)
	ui.Info("2 설치 다시 확인을 고르면 다음을 진행합니다.")
	for _, change := range summary.Changes {
		ui.Hint("· " + change)
	}
	answer, err := chooseWith(ctx, ui, read, "선택 [1]: ", "1 웹 앱 열기  ·  2 설치 다시 확인  ·  0 닫기", "1", "1", "2", "0")
	if err != nil {
		return 0, err
	}
	switch answer {
	case "2":
		return existingProceed, nil
	case "0":
		return existingClose, nil
	}
	return existingOpen, nil
}

// waitBeforeClosing keeps a double-clicked wizard's console open until Enter: Explorer closes
// it as soon as railwait exits, before the result could be read.
func waitBeforeClosing(ctx context.Context, ui *terminal.UI, read func(context.Context) (string, error)) {
	ui.Pause()
	ui.Prompt("Enter를 누르면 이 창을 닫습니다.", "")
	_, _ = read(ctx)
}
