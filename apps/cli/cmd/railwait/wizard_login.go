package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/access"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/setup"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/terminal"
)

type tailscaleLoginOperations struct {
	run  func(context.Context, string, io.Writer) error
	open func(context.Context, string) error
}

func loginTailscale(ctx context.Context, ui *terminal.UI, path string) error {
	return loginTailscaleWith(ctx, ui, path, tailscaleLoginOperations{run: runTailscaleAuthentication, open: setup.OpenURL})
}

func loginTailscaleWith(ctx context.Context, ui *terminal.UI, path string, ops tailscaleLoginOperations) error {
	ui.Pause()
	ui.Info("Tailscale 로그인 페이지를 자동으로 엽니다. 계정 로그인과 필요한 기기 승인을 진행해 주세요.")
	ui.Info("완료되면 자동으로 이어갑니다. 인증 주소는 화면·로그·설치 기록에 남기지 않습니다.")
	loginCtx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()
	output := &tailscaleLoginOutput{ctx: loginCtx, cancel: cancel, open: ops.open, ui: ui}
	runErr := ops.run(loginCtx, path, output)
	output.finish()
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if err := output.failure(); err != nil {
		return err
	}
	if loginCtx.Err() != nil {
		return fmt.Errorf("Tailscale 로그인·기기 승인을 5분 안에 마치지 못했습니다. 같은 설치로 다시 이어갈 수 있습니다: %w", loginCtx.Err())
	}
	if runErr != nil {
		// Command and browser output can contain authentication URLs. Only the
		// fixed actionable error crosses the UI/log boundary.
		return errors.New("Tailscale 로그인을 마치지 못했습니다. 로그인·기기 승인 상태를 확인한 뒤 설치를 다시 실행해 주세요")
	}
	return nil
}

func tailscaleAuthenticationCommand(ctx context.Context, path string) *exec.Cmd {
	// Bare up preserves the current profile and settings. login creates a new
	// profile, while reset/force-reauth would discard or interrupt existing state.
	return exec.CommandContext(ctx, path, "up")
}

func runTailscaleAuthentication(ctx context.Context, path string, output io.Writer) error {
	cmd := tailscaleAuthenticationCommand(ctx, path)
	for _, item := range os.Environ() {
		if !strings.HasPrefix(item, "TAILSCALE_BE_CLI=") {
			cmd.Env = append(cmd.Env, item)
		}
	}
	cmd.Env = append(cmd.Env, "TAILSCALE_BE_CLI=1")
	cmd.Stdout, cmd.Stderr = output, output
	cmd.WaitDelay = 2 * time.Second
	return cmd.Run()
}

// The same synchronized sink receives both streams. It retains at most one
// bounded line and never forwards a raw line to the terminal or an error.
type tailscaleLoginOutput struct {
	mu                        sync.Mutex
	ctx                       context.Context
	cancel                    context.CancelFunc
	open                      func(context.Context, string) error
	ui                        *terminal.UI
	line                      []byte
	total                     int
	loginOpened, deviceOpened bool
	err                       error
}

func (w *tailscaleLoginOutput) Write(data []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.err != nil {
		return 0, w.err
	}
	if w.total+len(data) > 1<<20 {
		return 0, w.fail("Tailscale 로그인 응답이 너무 커서 중단했습니다")
	}
	w.total += len(data)
	for _, char := range data {
		if char == '\n' || char == '\r' {
			if err := w.consumeLine(); err != nil {
				return 0, err
			}
			continue
		}
		if len(w.line) == 8192 {
			return 0, w.fail("Tailscale 로그인 응답을 확인하지 못해 중단했습니다")
		}
		w.line = append(w.line, char)
	}
	return len(data), nil
}

func (w *tailscaleLoginOutput) consumeLine() error {
	line := string(w.line)
	clear(w.line)
	w.line = w.line[:0]
	if err := w.ctx.Err(); err != nil {
		return err
	}
	address := ""
	if !w.loginOpened {
		address = access.LoginURLFromLine(line)
		if address != "" {
			w.loginOpened = true
		}
	}
	if address == "" && !w.deviceOpened {
		for _, field := range strings.Fields(line) {
			if field == access.DeviceApprovalURL || field == "https://login.tailscale.com/admin" {
				address, w.deviceOpened = access.DeviceApprovalURL, true
				w.ui.Info("Tailscale 기기 승인이 필요합니다. 열린 관리 화면에서 승인하거나 조직 관리자에게 승인을 요청해 주세요.")
				break
			}
		}
	}
	if address != "" {
		if err := w.open(w.ctx, address); err != nil {
			return w.fail("Tailscale 로그인·승인 페이지를 열지 못했습니다. Tailscale 앱에서 로그인한 뒤 설치를 다시 실행해 주세요")
		}
		w.ui.Start("Tailscale 로그인·기기 승인 대기 · 최대 5분")
	}
	return nil
}

func (w *tailscaleLoginOutput) fail(message string) error {
	w.err = errors.New(message)
	clear(w.line)
	w.line = nil
	w.cancel()
	return w.err
}

func (w *tailscaleLoginOutput) finish() {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.err == nil && len(bytes.TrimSpace(w.line)) != 0 {
		_ = w.consumeLine()
	}
	clear(w.line)
	w.line = nil
}

func (w *tailscaleLoginOutput) failure() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.err
}
