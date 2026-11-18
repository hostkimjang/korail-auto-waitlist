package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/access"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/install"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/setup"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/terminal"
)

func prepareRemote(ctx context.Context, ui *terminal.UI, o wizardOptions, interactive bool) (*access.Client, access.Plan, error) {
	return prepareRemoteWith(ctx, ui, o, interactive, remotePreparationOperations{
		find: setup.Executable, canInstall: setup.CanInstallDependency, install: setup.InstallDependency,
		client: func(path string) access.Client { return access.Client{Runner: access.ExecRunner{Executable: path}} },
		login:  loginTailscale, wait: waitRemoteInitialization, prompt: remotePreparationPrompt,
		approveDevice: func(ctx context.Context) error { return setup.OpenURL(ctx, access.DeviceApprovalURL) },
	})
}

// The orchestration stays real in tests; only OS discovery, installation,
// authentication and waiting cross the injected boundary.
type remotePreparationOperations struct {
	find          func(string) (string, error)
	canInstall    func(string) bool
	install       dependencyInstaller
	client        func(string) access.Client
	login         func(context.Context, *terminal.UI, string) error
	wait          func(context.Context) error
	prompt        func(context.Context, *terminal.UI, setup.Need, bool) (string, error)
	approveDevice func(context.Context) error
}

func prepareRemoteWith(ctx context.Context, ui *terminal.UI, o wizardOptions, interactive bool, ops remotePreparationOperations) (*access.Client, access.Plan, error) {
	installAttempted, loginAttempted := false, false
	deviceApprovalOpened := false
	var readyCtx context.Context
	waits := 0
	knownDNS, lastTitle := "", ""
	startWaiting := func(title string) {
		if title != lastTitle {
			ui.Start(title)
			lastTitle = title
		}
	}
	readinessError := func(cause error) error {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return fmt.Errorf("Tailscale 준비·기기 승인을 기다리는 시간이 끝났습니다. 앱의 VPN 허용과 계정·기기 승인 상태를 확인한 뒤 설치를 다시 실행해 주세요: %w", cause)
	}
	wait := func() error {
		if waits >= 150 {
			return readinessError(context.DeadlineExceeded)
		}
		waits++
		if err := ops.wait(readyCtx); err != nil {
			return readinessError(err)
		}
		return nil
	}
	startWaiting("Tailscale 연결 확인")
	for {
		if ctx.Err() != nil {
			return nil, access.Plan{}, ctx.Err()
		}
		if readyCtx != nil && readyCtx.Err() != nil {
			return nil, access.Plan{}, readinessError(readyCtx.Err())
		}
		needsLogin := false
		path, findErr := ops.find("tailscale")
		if findErr == nil {
			if readyCtx == nil {
				var cancel context.CancelFunc
				readyCtx, cancel = context.WithTimeout(ctx, 5*time.Minute)
				defer cancel()
			}
			client := ops.client(path)
			status, statusErr := client.Status(readyCtx)
			if readyCtx.Err() != nil {
				return nil, access.Plan{}, readinessError(readyCtx.Err())
			}
			needsLogin = statusErr == nil && status.BackendState == "NeedsLogin"
			if statusErr == nil && status.BackendState == "Running" && status.DNSName != "" {
				if knownDNS != "" && knownDNS != status.DNSName {
					return nil, access.Plan{}, errors.New("준비 중 Tailscale 연결 대상이 변경되었습니다. 기존 설정을 보존하고 중단합니다")
				}
				knownDNS = status.DNSName
			}
			if statusErr == nil && status.BackendState == "Running" && status.Online && status.DNSName != "" {
				plan, err := client.Plan(readyCtx, o.Port, o.HTTPSPort)
				if err != nil {
					return nil, access.Plan{}, err
				}
				if plan.DNSName != knownDNS {
					return nil, access.Plan{}, errors.New("접속 계획을 확인하는 중 Tailscale 연결 대상이 변경되었습니다")
				}
				ui.Done("Tailscale 연결 준비됨")
				ui.KeyValue("원격 접속", plan.URL)
				return &client, plan, nil
			}
			if interactive && needsLogin {
				if loginAttempted {
					return nil, access.Plan{}, errors.New("Tailscale 로그인이 완료되지 않았습니다. 계정 로그인을 확인한 뒤 설치를 다시 실행해 주세요")
				}
				loginAttempted = true
				if err := ops.login(readyCtx, ui, path); err != nil {
					if errors.Is(err, context.DeadlineExceeded) {
						return nil, access.Plan{}, readinessError(err)
					}
					return nil, access.Plan{}, err
				}
				continue
			}
			if interactive && (statusErr != nil || status.BackendState == "NoState" || status.BackendState == "Starting" || status.BackendState == "NeedsMachineAuth" || status.BackendState == "Running") {
				if statusErr == nil && status.BackendState == "NeedsMachineAuth" {
					if !deviceApprovalOpened {
						deviceApprovalOpened = true
						ui.Info("Tailscale 기기 승인 대기 · 열린 관리 화면에서 승인하거나 조직 관리자에게 승인을 요청해 주세요.")
						if ops.approveDevice != nil {
							if err := ops.approveDevice(readyCtx); err != nil {
								if readyCtx.Err() != nil {
									return nil, access.Plan{}, readinessError(readyCtx.Err())
								}
								return nil, access.Plan{}, errors.New("Tailscale 기기 승인 페이지를 열지 못했습니다. 앱에서 승인 상태를 확인해 주세요")
							}
						}
					}
					startWaiting("Tailscale 기기 승인 대기 · 최대 5분")
				} else {
					startWaiting("Tailscale 실행·연결 준비 대기 · 최대 5분")
				}
				if err := wait(); err != nil {
					return nil, access.Plan{}, err
				}
				continue
			}
		}
		ui.Pause()
		guide := setup.Guide("tailscale")
		if !interactive {
			return nil, access.Plan{}, fmt.Errorf("Tailscale 설치·로그인 후 같은 명령으로 다시 실행하세요. %s", guide.URL)
		}
		if findErr != nil && !installAttempted && ops.canInstall("tailscale") {
			installAttempted = true
			if err := installPrerequisite(ctx, ui, "tailscale", ops.install); err != nil {
				return nil, access.Plan{}, err
			}
			continue
		}
		if findErr != nil && installAttempted {
			if readyCtx == nil {
				var cancel context.CancelFunc
				readyCtx, cancel = context.WithTimeout(ctx, 5*time.Minute)
				defer cancel()
			}
			startWaiting("설치한 Tailscale 실행 경로 준비 대기 · 최대 5분")
			if err := wait(); err != nil {
				return nil, access.Plan{}, err
			}
			continue
		}
		answer, err := ops.prompt(ctx, ui, guide, needsLogin)
		if err != nil {
			return nil, access.Plan{}, err
		}
		switch answer {
		case "0":
			return nil, access.Plan{}, context.Canceled
		case "3":
			if err := ops.login(ctx, ui, path); err != nil {
				return nil, access.Plan{}, err
			}
		}
	}
}

func waitRemoteInitialization(ctx context.Context) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-time.After(2 * time.Second):
		return nil
	}
}

func remotePreparationPrompt(ctx context.Context, ui *terminal.UI, guide setup.Need, needsLogin bool) (string, error) {
	ui.Info(guide.Detail)
	hint := "1 공식 설치 안내 열기  ·  2 준비 후 다시 확인  ·  0 중단"
	allowed := []string{"0", "1", "2"}
	if needsLogin {
		hint += "  ·  3 로그인 시작"
		allowed = append(allowed, "3")
	}
	answer, err := choose(ctx, ui, "선택 [2]: ", hint, "2", allowed...)
	if err == nil && answer == "1" {
		err = setup.OpenURL(ctx, guide.URL)
	}
	return answer, err
}

func connectRemote(ctx context.Context, ui *terminal.UI, i *install.Installer, s install.State, client access.Client, plan access.Plan, interactive bool) (err error) {
	data, err := os.ReadFile(filepath.Join(s.Path, ".env"))
	if err != nil {
		return err
	}
	configured, err := install.AccessConfigured(data, plan.URL)
	if err != nil {
		return err
	}
	var configPlan install.UpdatePlan
	if !configured {
		ui.Start("HTTPS 접속 설정 사전 확인")
		configPlan, err = i.PlanConfigureAccess(ctx, s.Path, plan.URL)
		if err != nil {
			return err
		}
		defer func() { err = errors.Join(err, removeWizardCandidate(s.Path, configPlan.PlanID)) }()
		if !configPlan.Allowed {
			return fmt.Errorf("접속 설정을 적용할 수 없습니다: %s", strings.Join(configPlan.BlockedReasons, "; "))
		}
	}
	ui.Start("Tailscale HTTPS 연결")
	result, err := applyRemoteWithApproval(ctx, ui, plan, interactive, httpsApprovalOperations{
		apply: client.Apply, open: setup.OpenURL, wait: waitHTTPSApproval,
	})
	if err != nil {
		if result.Created {
			cleanupCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
			cleanupErr := client.Rollback(cleanupCtx, result)
			cancel()
			if cleanupErr != nil {
				return errors.Join(err, errors.New("추가한 HTTPS 연결 상태를 확인해야 합니다. tailscale serve status로 확인하세요"))
			}
		}
		return err
	}
	ui.Done("내 Tailscale 기기용 HTTPS 연결 확인")
	defer func() {
		if err == nil || !result.Created {
			return
		}
		current, readErr := os.ReadFile(filepath.Join(s.Path, ".env"))
		applied, checkErr := install.AccessConfigured(current, plan.URL)
		gateData, gateErr := os.ReadFile(filepath.Join(s.Path, ".railwait", "manager", "control", "maintenance.json"))
		var gate struct {
			Closed bool `json:"closed"`
		}
		if readErr != nil || checkErr != nil || gateErr != nil || json.Unmarshal(gateData, &gate) != nil || applied || gate.Closed {
			err = errors.Join(err, errors.New("진행 상태 확인을 위해 새 HTTPS 연결을 보존했습니다. 설정 작업과 서비스 상태를 확인한 뒤 같은 명령으로 재개하세요"))
			return
		}
		cleanupCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		cleanupErr := client.Rollback(cleanupCtx, result)
		cancel()
		if cleanupErr != nil {
			err = errors.Join(err, errors.New("추가한 HTTPS 연결 정리를 확인하지 못했습니다. tailscale serve status로 확인하세요"))
		}
	}()
	if !configured {
		ui.Start("설정 백업과 HTTPS 로그인 적용")
		raw := make([]byte, 16)
		if _, err = rand.Read(raw); err != nil {
			return err
		}
		id := hex.EncodeToString(raw)
		_, err = i.Update(ctx, install.UpdateOptions{Path: s.Path, PlanID: configPlan.PlanID, TargetReleaseID: configPlan.TargetReleaseID, JobID: id, Progress: func(stage string) error {
			labels := map[string]string{"checking": "접속 설정 재확인", "preparing_config": "HTTPS 로그인 설정 준비", "draining": "진행 중인 작업 종료 대기", "backing_up": "DB와 설정 암호화 백업", "applying": "HTTPS 로그인 설정 적용", "verifying": "전체 서비스 정상 상태 확인", "resuming": "작업 재개"}
			title := labels[stage]
			if title == "" {
				title = "접속 설정 · " + stage
			}
			ui.Start(title)
			return nil
		}})
		if err != nil {
			return fmt.Errorf("HTTPS 설정 작업 %s 확인이 필요합니다: %w", id, err)
		}
		ui.Done("HTTPS 로그인과 보안 쿠키 적용 완료")
	} else {
		ui.Info("재사용 · HTTPS 로그인 설정")
	}
	ui.Start("HTTPS 웹 앱 응답 확인")
	if err = checkWeb(ctx, plan.URL); err != nil {
		return fmt.Errorf("HTTPS 주소 응답을 확인하지 못했습니다. 같은 Tailscale 네트워크에서 %s 를 확인해 주세요: %w", plan.URL, err)
	}
	ui.Done("HTTPS 접속과 서비스 응답 확인")
	// This record is advisory; every resume reads the live Tailscale configuration
	// and protected app settings again. It never authorizes overwriting a route.
	record := struct {
		URL        string `json:"url"`
		LocalPort  int    `json:"local_port"`
		HTTPSPort  int    `json:"https_port"`
		VerifiedAt string `json:"verified_at"`
	}{plan.URL, plan.LocalPort, plan.HTTPSPort, time.Now().UTC().Format(time.RFC3339)}
	encoded, _ := json.MarshalIndent(record, "", "  ")
	return writeAdvisoryRecord(s.Path, "access.json", encoded)
}

func removeWizardCandidate(path, planID string) error {
	decoded, err := hex.DecodeString(planID)
	if err != nil || len(decoded) != 32 || hex.EncodeToString(decoded) != planID {
		return errors.New("정리할 접속 설정 계획 ID가 잘못되었습니다")
	}
	file := filepath.Join(path, ".railwait", "plans", planID+".candidate.env")
	info, err := os.Lstat(file)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return errors.New("접속 설정 후보 경로가 일반 파일이 아닙니다")
	}
	return os.Remove(file)
}

func checkWeb(ctx context.Context, address string) error {
	client := &http.Client{Timeout: 10 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	for _, path := range []string{"/", "/api/v1/auth/status"} {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, address+path, nil)
		if err != nil {
			return errors.New("접속 주소가 잘못되었습니다")
		}
		resp, err := client.Do(req)
		if err != nil {
			return errors.New("웹 앱에 연결하지 못했습니다. 주소·DNS·Tailscale 연결을 확인해 주세요")
		}
		body, readErr := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
		closeErr := resp.Body.Close()
		if readErr != nil || closeErr != nil || resp.StatusCode != http.StatusOK {
			return fmt.Errorf("웹 앱 상태 확인에 실패했습니다 (HTTP %d)", resp.StatusCode)
		}
		if path == "/api/v1/auth/status" {
			var status struct {
				Configured *bool `json:"configured"`
			}
			if json.Unmarshal(body, &status) != nil || status.Configured == nil || !*status.Configured {
				return errors.New("관리자 계정이 준비된 API 응답을 확인하지 못했습니다")
			}
		}
	}
	return nil
}
