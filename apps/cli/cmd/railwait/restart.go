package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/install"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/manager"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

// restartStages names the stages of a restart job for the terminal.
var restartStages = map[string]string{
	"checking":  "현재 설치와 .env 확인",
	"draining":  "새 작업을 막고 진행 중인 작업 종료 대기",
	"applying":  "앱 서비스 다시 시작",
	"verifying": "서비스 상태 확인",
	"resuming":  "작업 재개",
}

// restartRun restarts the app services in this CLI process under the installation lock,
// with the same restart_services plan the host manager runs for the web. A manager from
// an earlier release does not know that plan, so the CLI does not hand it over.
type restartRun struct {
	installer install.Installer
	confirm   confirmation
	out       io.Writer
}

func restartCommand(ctx context.Context, args []string) error {
	defaultPath, err := platform.DefaultInstallDir()
	if err != nil {
		return err
	}
	flags := newCommandFlags("restart")
	path := flags.String("path", defaultPath, "설치 경로")
	yes := flags.Bool("yes", false, "확인 질문 없이 진행 (대화형 터미널이 아니면 필요)")
	if err = parseCommandFlags(flags, args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return fmt.Errorf("위치 인자를 사용할 수 없습니다")
	}
	return restartRun{installer: newInstaller(), confirm: terminalConfirmation(), out: os.Stderr}.run(ctx, *path, *yes)
}

func (r restartRun) run(ctx context.Context, path string, yes bool) error {
	abs, err := filepath.Abs(path)
	if err != nil {
		return err
	}
	state, err := install.ReadState(abs)
	if err != nil {
		return err
	}
	if !yes && !r.confirm.interactive {
		return fmt.Errorf("대화형 터미널이 아니면 --yes를 붙여 실행하세요: %s", pasteableCommand("restart", "--path", abs, "--yes"))
	}
	printRestartSummary(r.out, abs, install.RestartServices(state))
	if !yes {
		confirmed, err := r.confirm.yes("앱 서비스를 다시 시작할까요? [y/N]: ")
		if err != nil {
			return err
		}
		if !confirmed {
			return errors.New("다시 시작을 취소했습니다; 아무것도 바꾸지 않았습니다")
		}
	}
	fmt.Fprintln(r.out, "현재 .env와 서비스 상태를 확인하는 중입니다.")
	plan, err := r.installer.PlanRestart(ctx, abs)
	var rejected manager.RejectedError
	if errors.As(err, &rejected) {
		switch rejected.Code {
		case "E_RESTART_ENV_INVALID":
			return fmt.Errorf("Docker Compose가 현재 .env를 받아들이지 않아 다시 시작하지 않았습니다; %s 의 내용을 확인하세요", filepath.Join(abs, ".env"))
		case "E_RESTART_UNAVAILABLE":
			return fmt.Errorf("설치가 완료되지 않아 다시 시작할 수 없습니다; %s 로 상태를 확인하세요", pasteableCommand("status", "--path", abs))
		}
	}
	if err != nil {
		return err
	}
	if !plan.Allowed {
		return fmt.Errorf("지금은 다시 시작할 수 없습니다: %s", strings.Join(plan.BlockedReasons, "; "))
	}
	return applyPlanInProcess(ctx, &r.installer, abs, state.Release, plan.PlanID, false, inProcessJob{noun: "다시 시작", stages: restartStages})
}

func printRestartSummary(w io.Writer, path string, services []string) {
	fmt.Fprintln(w, "앱 서비스 다시 시작:", path)
	fmt.Fprintf(w, "  다시 시작: %s (%d개)\n", strings.Join(services, ", "), len(services))
	fmt.Fprintln(w, "  유지: PostgreSQL·Redis는 멈추지 않으며 데이터는 그대로입니다. .env는 바꾸지 않고 백업도 만들지 않습니다.")
	fmt.Fprintln(w, "  순서: 새 작업 차단 → 진행 중인 작업 종료 대기 → 서비스 재생성 → 상태 확인 → 작업 재개")
	fmt.Fprintln(w, "  주의: 1~3분 동안 웹 접속과 좌석 감시가 끊깁니다.")
	fmt.Fprintln(w, "  주의: KORAIL 로그인 세션이 끊겨 앱이 다시 로그인합니다. KORAIL이 자동 로그인을 막으면 KORAIL 좌석 관측이 30분 이상 멈출 수 있습니다.")
}
