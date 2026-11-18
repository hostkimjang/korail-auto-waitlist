package main

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/access"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/terminal"
)

type httpsApprovalOperations struct {
	apply func(context.Context, access.Plan) (access.Result, error)
	open  func(context.Context, string) error
	wait  func(context.Context) error
}

func applyRemoteWithApproval(ctx context.Context, ui *terminal.UI, plan access.Plan, interactive bool, ops httpsApprovalOperations) (access.Result, error) {
	result, err := ops.apply(ctx, plan)
	var approval *access.HTTPSApprovalRequired
	if err == nil || result.Created || result.RequiresReview || !errors.As(err, &approval) || approval.URL() == "" {
		return result, err
	}
	if ctx.Err() != nil {
		return result, ctx.Err()
	}
	if !interactive {
		return result, errors.New("Tailscale HTTPS 사용 승인이 필요합니다. 공식 안내에서 설정한 뒤 같은 명령으로 재개하세요: https://tailscale.com/docs/features/tailscale-serve")
	}
	approvalCtx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()
	ui.Pause()
	ui.Info("Tailscale HTTPS 승인 페이지를 엽니다. 브라우저에서 HTTPS 사용을 승인하고 공개 Funnel은 켜지 마세요.")
	ui.Info("승인 후 자동으로 이어갑니다. 승인 주소는 로그나 설치 기록에 저장하지 않습니다.")
	if openErr := ops.open(approvalCtx, approval.URL()); openErr != nil {
		if ctx.Err() != nil {
			return result, ctx.Err()
		}
		// OS/browser errors may repeat their argument; never wrap the token URL.
		return result, errors.New("HTTPS 승인 페이지를 열지 못했습니다. Tailscale 공식 HTTPS 설정을 확인한 뒤 같은 명령으로 재개하세요")
	}
	ui.Start("Tailscale HTTPS 승인 대기 · 최대 5분")
	for {
		if waitErr := ops.wait(approvalCtx); waitErr != nil {
			if ctx.Err() != nil {
				return result, ctx.Err()
			}
			return result, fmt.Errorf("HTTPS 승인 대기를 마치지 못했습니다. 승인·권한 상태를 확인한 뒤 같은 명령으로 재개하세요: %w", context.DeadlineExceeded)
		}
		// Every retry uses the same plan. Client.Apply rechecks identity, the
		// complete original route map and Funnel before making any change.
		result, err = ops.apply(approvalCtx, plan)
		if err == nil || result.Created || result.RequiresReview {
			return result, err
		}
		if ctx.Err() != nil {
			return result, ctx.Err()
		}
		if approvalCtx.Err() != nil {
			return result, fmt.Errorf("HTTPS 승인 대기 시간이 끝났습니다. 같은 명령으로 재개하세요: %w", context.DeadlineExceeded)
		}
		if !errors.As(err, &approval) || approval.URL() == "" {
			return result, err
		}
	}
}

func waitHTTPSApproval(ctx context.Context) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-time.After(2 * time.Second):
		return nil
	}
}
