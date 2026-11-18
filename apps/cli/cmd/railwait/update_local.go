package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/install"
)

type localUpdateOptions struct {
	Path, Package, TrustKey, Target, Plan string
	DryRun, JSON                          bool
}

// localUpdateCommand runs in this CLI process under the installation lock. The host
// manager applies only official release and settings plans, so package paths and trust
// keys never cross its request channel.
func localUpdateCommand(ctx context.Context, i *install.Installer, o localUpdateOptions) error {
	for _, value := range []*string{&o.Path, &o.Package, &o.TrustKey} {
		abs, err := filepath.Abs(*value)
		if err != nil {
			return err
		}
		*value = abs
	}
	if o.DryRun || o.Target == "" || o.Plan == "" {
		fmt.Fprintln(os.Stderr, "로컬 패키지 서명·번들·이미지 파일과 설치 상태를 검증하는 중입니다.")
		plan, err := i.PlanLocalUpdate(ctx, o.Path, o.Package, o.TrustKey)
		if err != nil {
			return err
		}
		if o.JSON {
			return json.NewEncoder(os.Stdout).Encode(plan)
		}
		fmt.Printf("현재: %s → 대상: %s\n", plan.CurrentReleaseID, plan.TargetReleaseID)
		if key, keyErr := install.LocalTrustKey(o.TrustKey); keyErr == nil {
			relation := "현재 보존 패키지의 공개키와 다름"
			if current, currentErr := install.LocalTrustKey(filepath.Join(o.Path, ".railwait", "local-package", "trust-key.pub")); currentErr == nil && current == key {
				relation = "현재 보존 패키지의 공개키와 같음"
			}
			fmt.Printf("신뢰할 공개키: %s (%s)\n", key, relation)
		}
		if !plan.Allowed {
			fmt.Println("차단:", strings.Join(plan.BlockedReasons, "; "))
			return nil
		}
		fmt.Printf("검증 → 패키지 보존·이미지 불러오기 → 작업 종료 대기 → 암호화 백업 → 교체 → 확인 → 재개\n적용: railwait update --path %q --package %q --trust-key %q --target %s --plan %s\n", o.Path, o.Package, o.TrustKey, plan.TargetReleaseID, plan.PlanID)
		return nil
	}
	raw := make([]byte, 16)
	if _, err := rand.Read(raw); err != nil {
		return err
	}
	id := hex.EncodeToString(raw)
	// A dropped terminal or closed output pipe must not abandon the update half way: keep
	// running and leave the result in the job record. A termination request still cancels
	// through the failure path so the preserved package and the job record stay consistent.
	signal.Ignore(syscall.SIGHUP, syscall.SIGPIPE)
	ctx, stop := signal.NotifyContext(ctx, syscall.SIGTERM)
	defer stop()
	fmt.Fprintln(os.Stderr, "로컬 패키지 업데이트 작업:", id)
	result, err := i.Update(ctx, install.UpdateOptions{Path: o.Path, TargetReleaseID: o.Target, PlanID: o.Plan, JobID: id, PackagePath: o.Package, TrustKey: o.TrustKey, Progress: func(stage string) error {
		fmt.Fprintln(os.Stderr, "업데이트 단계:", stage)
		return nil
	}})
	for _, warning := range result.Warnings {
		fmt.Fprintln(os.Stderr, "주의:", warning)
	}
	if err != nil {
		return fmt.Errorf("업데이트가 %s 상태입니다; railwait status --path %q --job %s 로 확인하세요: %w", result.State, o.Path, id, err)
	}
	if o.JSON {
		return json.NewEncoder(os.Stdout).Encode(result)
	}
	fmt.Printf("작업 %s: %s (%s → %s)\n", id, result.State, result.CurrentVersion, result.TargetVersion)
	return nil
}
