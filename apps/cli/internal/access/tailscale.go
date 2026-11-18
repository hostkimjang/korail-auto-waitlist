// Package access connects a loopback installation to an existing private tailnet.
// It never resets a device's Serve configuration or enables Funnel.
package access

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"os/exec"
	"reflect"
	"strconv"
	"strings"
	"time"
)

const maxOutput = 1 << 20

type Runner interface {
	Run(context.Context, ...string) ([]byte, error)
}

// ExecRunner requires a discovered executable, including the native macOS app CLI.
type ExecRunner struct {
	Executable string
	command    func(context.Context, string, ...string) *exec.Cmd
}

type boundedBuffer struct {
	bytes.Buffer
	exceeded bool
}

func (b *boundedBuffer) Write(p []byte) (int, error) {
	if b.Len()+len(p) > maxOutput {
		b.exceeded = true
		return 0, errors.New("Tailscale output exceeds limit")
	}
	return b.Buffer.Write(p)
}

func (r ExecRunner) Run(ctx context.Context, args ...string) ([]byte, error) {
	if r.Executable == "" {
		return nil, errors.New("Tailscale 실행 파일이 필요합니다")
	}
	parent := ctx
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	command := r.command
	if command == nil {
		command = exec.CommandContext
	}
	cmd := command(ctx, r.Executable, args...)
	cmd.Env = make([]string, 0, len(os.Environ())+1)
	for _, item := range os.Environ() {
		if !strings.HasPrefix(item, "TAILSCALE_BE_CLI=") {
			cmd.Env = append(cmd.Env, item)
		}
	}
	cmd.Env = append(cmd.Env, "TAILSCALE_BE_CLI=1")
	var output, diagnostic boundedBuffer
	cmd.Stdout, cmd.Stderr, cmd.Stdin = &output, io.Discard, nil
	approvalCommand := isHTTPSApplyCommand(args)
	if approvalCommand {
		cmd.Stderr = &diagnostic
	}
	if err := cmd.Run(); err != nil {
		if parent.Err() != nil {
			return nil, parent.Err()
		}
		if approvalCommand && !output.exceeded && !diagnostic.exceeded {
			if approval := approvalFromOutput(output.Bytes(), diagnostic.Bytes()); approval != nil {
				return nil, approval
			}
		}
		// Tailscale may print a one-time login URL. Keep raw output out of errors.
		return nil, fmt.Errorf("Tailscale 명령을 완료하지 못했습니다. 앱 로그인·HTTPS 사용 설정·실행 권한을 확인하세요: %w", err)
	}
	return output.Bytes(), nil
}

type Client struct{ Runner Runner }

type Status struct {
	BackendState string
	DNSName      string
	Online       bool
}

type Plan struct {
	URL       string
	DNSName   string
	LocalPort int
	HTTPSPort int
	Existing  bool
	baseline  map[string]any
}

type Result struct {
	URL            string `json:"url"`
	LocalPort      int    `json:"local_port"`
	HTTPSPort      int    `json:"https_port"`
	Created        bool   `json:"created"`
	RequiresReview bool   `json:"requires_review,omitempty"`
	plan           Plan
}

func (c Client) Status(ctx context.Context) (Status, error) {
	if c.Runner == nil {
		return Status{}, errors.New("Tailscale 실행 경로가 준비되지 않았습니다")
	}
	data, err := c.Runner.Run(ctx, "status", "--json")
	if err != nil {
		return Status{}, err
	}
	var state struct {
		BackendState string
		Self         *struct {
			DNSName string
			Online  bool
		}
	}
	if len(data) > maxOutput || json.Unmarshal(data, &state) != nil {
		return Status{}, errors.New("Tailscale 상태 응답을 확인하지 못했습니다")
	}
	result := Status{BackendState: state.BackendState}
	if state.Self != nil {
		result.DNSName = strings.TrimSuffix(state.Self.DNSName, ".")
		result.Online = state.Self.Online
	}
	return result, nil
}

// ValidateOrigin accepts only a complete HTTPS origin on Tailscale's DNS suffix.
func ValidateOrigin(origin string) error {
	u, err := url.Parse(origin)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || u.Opaque != "" || u.RawPath != "" || u.Host == "" || origin != "https://"+u.Host {
		return errors.New("Tailscale HTTPS 주소만 사용할 수 있습니다")
	}
	host := u.Hostname()
	if host != strings.ToLower(host) || !strings.HasSuffix(host, ".ts.net") || len(host) > 253 || !validDNS(host) || strings.ContainsAny(origin, "'\r\n\x00") {
		return errors.New("Tailscale DNS 주소가 잘못되었습니다")
	}
	if port := u.Port(); port != "" {
		n, err := strconv.Atoi(port)
		if err != nil || n < 1 || n > 65535 || strconv.Itoa(n) != port {
			return errors.New("HTTPS 포트가 잘못되었습니다")
		}
	} else if strings.Contains(u.Host, ":") {
		return errors.New("HTTPS 포트가 비어 있습니다")
	}
	return nil
}

func validDNS(host string) bool {
	labels := strings.Split(host, ".")
	if len(labels) < 3 {
		return false
	}
	for _, label := range labels {
		if len(label) == 0 || len(label) > 63 || label[0] == '-' || label[len(label)-1] == '-' {
			return false
		}
		for _, char := range label {
			if char != '-' && (char < 'a' || char > 'z') && (char < '0' || char > '9') {
				return false
			}
		}
	}
	return true
}

func (c Client) serveConfig(ctx context.Context) (map[string]any, error) {
	data, err := c.Runner.Run(ctx, "serve", "status", "--json")
	if err != nil {
		return nil, err
	}
	if len(data) > maxOutput {
		return nil, errors.New("Tailscale Serve 설정이 너무 큽니다")
	}
	var config map[string]any
	if json.Unmarshal(data, &config) != nil {
		return nil, errors.New("Tailscale Serve 설정을 확인하지 못했습니다")
	}
	if config == nil {
		config = map[string]any{}
	}
	if err := validateConfig(config); err != nil {
		return nil, err
	}
	return config, nil
}

// Unknown fields remain intact, but a malformed known routing map must not be
// mistaken for a free port and overwritten by a later CLI command.
func validateConfig(config map[string]any) error {
	for key, value := range config {
		child, objectValue := value.(map[string]any)
		if key == "TCP" || key == "Web" || key == "AllowFunnel" {
			if value != nil && !objectValue {
				return errors.New("Tailscale Serve 경로 형식을 확인하지 못했습니다")
			}
			for address, route := range child {
				if key == "AllowFunnel" {
					if _, ok := route.(bool); !ok {
						return errors.New("Tailscale 공개 범위 설정을 확인하지 못했습니다")
					}
				} else if _, ok := route.(map[string]any); !ok || address == "" {
					return errors.New("Tailscale Serve 경로를 확인하지 못했습니다")
				}
			}
		}
		if objectValue {
			if err := validateConfig(child); err != nil {
				return err
			}
		}
	}
	return nil
}

func (c Client) Plan(ctx context.Context, localPort, requestedHTTPSPort int) (Plan, error) {
	if localPort < 1 || localPort > 65535 || requestedHTTPSPort < 0 || requestedHTTPSPort > 65535 {
		return Plan{}, errors.New("접속 포트가 잘못되었습니다")
	}
	status, err := c.Status(ctx)
	if err != nil {
		return Plan{}, err
	}
	if status.BackendState != "Running" || !status.Online {
		return Plan{}, errors.New("Tailscale 앱에 로그인하고 연결한 뒤 다시 확인해 주세요")
	}
	if err := ValidateOrigin("https://" + status.DNSName); err != nil {
		return Plan{}, errors.New("Tailscale DNS 이름이 준비되지 않았습니다. MagicDNS와 HTTPS 설정을 확인해 주세요")
	}
	config, err := c.serveConfig(ctx)
	if err != nil {
		return Plan{}, err
	}
	ports := []int{443, 8443, 9443, 10443}
	if requestedHTTPSPort != 0 {
		ports = []int{requestedHTTPSPort}
	}
	for _, port := range ports {
		plan := Plan{DNSName: status.DNSName, LocalPort: localPort, HTTPSPort: port, baseline: config}
		plan.URL = "https://" + status.DNSName
		if port != 443 {
			plan.URL += ":" + strconv.Itoa(port)
		}
		if funnelEnabled(config, port) {
			continue
		}
		if exactRoute(config, plan) {
			plan.Existing = true
			return plan, nil
		}
		if !portOccupied(config, port) {
			return plan, nil
		}
	}
	return Plan{}, errors.New("선택한 HTTPS 포트를 기존 Tailscale 설정이 사용 중이거나 공개 경로로 설정했습니다. 다른 포트를 선택하세요")
}

func routeKey(p Plan) string { return p.DNSName + ":" + strconv.Itoa(p.HTTPSPort) }
func target(p Plan) string   { return "http://127.0.0.1:" + strconv.Itoa(p.LocalPort) }

func object(parent map[string]any, key string) map[string]any {
	value, _ := parent[key].(map[string]any)
	return value
}

func exactRoute(config map[string]any, p Plan) bool {
	tcp := object(object(config, "TCP"), strconv.Itoa(p.HTTPSPort))
	web := object(object(config, "Web"), routeKey(p))
	return reflect.DeepEqual(tcp, map[string]any{"HTTPS": true}) && reflect.DeepEqual(web, map[string]any{"Handlers": map[string]any{"/": map[string]any{"Proxy": target(p)}}})
}

func funnelEnabled(config map[string]any, port int) bool {
	for key, value := range config {
		child, ok := value.(map[string]any)
		if !ok {
			continue
		}
		if key == "AllowFunnel" {
			for address, allowed := range child {
				if allowed != false {
					_, text, found := strings.Cut(address, ":")
					if !found || text == strconv.Itoa(port) {
						return true
					}
				}
			}
		}
		if funnelEnabled(child, port) {
			return true
		}
	}
	return false
}

func portOccupied(config map[string]any, port int) bool {
	text := strconv.Itoa(port)
	for key, value := range config {
		child, ok := value.(map[string]any)
		if !ok {
			continue
		}
		if key == "TCP" {
			if _, exists := child[text]; exists {
				return true
			}
		}
		if key == "Web" {
			for address := range child {
				if strings.HasSuffix(address, ":"+text) {
					return true
				}
			}
		}
		if portOccupied(child, port) {
			return true
		}
	}
	return false
}

// Apply checks the plan again immediately before adding a previously unused port.
// Tailscale has no CLI compare-and-swap; a concurrent outside edit is detected
// after the command and reported instead of restoring an obsolete global config.
func (c Client) Apply(ctx context.Context, p Plan) (Result, error) {
	result := Result{URL: p.URL, LocalPort: p.LocalPort, HTTPSPort: p.HTTPSPort, plan: p}
	if p.baseline == nil || ValidateOrigin(p.URL) != nil {
		return result, errors.New("원격 접속 계획을 다시 확인해 주세요")
	}
	status, err := c.Status(ctx)
	if err != nil || status.BackendState != "Running" || !status.Online || status.DNSName != p.DNSName {
		return result, errors.New("Tailscale 연결 대상이 달라졌습니다. 원격 접속 계획을 다시 확인해 주세요")
	}
	before, err := c.serveConfig(ctx)
	if err != nil {
		return result, err
	}
	if !reflect.DeepEqual(before, p.baseline) || funnelEnabled(before, p.HTTPSPort) {
		return result, errors.New("Tailscale 설정이 변경되었습니다. 기존 설정을 보존하고 계획을 다시 확인해 주세요")
	}
	if p.Existing {
		if !exactRoute(before, p) {
			return result, errors.New("기존 원격 접속 설정이 다릅니다")
		}
		return result, nil
	}
	if portOccupied(before, p.HTTPSPort) {
		return result, errors.New("HTTPS 포트에 다른 서비스가 연결되어 있습니다")
	}
	_, err = c.Runner.Run(ctx, "serve", "--bg", "--https="+strconv.Itoa(p.HTTPSPort), target(p))
	if err != nil {
		// The daemon can commit before its client is interrupted. A bounded
		// observation distinguishes a verified owned route from an unknown result.
		checkCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
		defer cancel()
		after, checkErr := c.serveConfig(checkCtx)
		if checkErr == nil && exactRoute(after, p) && !funnelEnabled(after, p.HTTPSPort) {
			result.Created = true
		} else if checkErr != nil || !reflect.DeepEqual(normalized(after), normalized(p.baseline)) {
			result.RequiresReview = true
		}
		return result, err
	}
	result.Created = true
	after, err := c.serveConfig(ctx)
	if err != nil {
		result.RequiresReview = true
		return result, errors.New("원격 접속 명령 후 상태를 확인하지 못했습니다. Tailscale Serve 상태를 확인해 주세요")
	}
	if !exactRoute(after, p) || funnelEnabled(after, p.HTTPSPort) || !reflect.DeepEqual(withoutRoute(after, p), normalized(p.baseline)) {
		result.RequiresReview = true
		return result, errors.New("원격 접속 설정이 예상과 다릅니다. 전체 설정을 자동 복원하지 않으며 Tailscale Serve 확인이 필요합니다")
	}
	return result, nil
}

// Rollback removes only the exact dedicated port this client just created.
// An already-existing route or a changed/shared route is never removed.
func (c Client) Rollback(ctx context.Context, r Result) error {
	if !r.Created {
		return nil
	}
	p := r.plan
	if p.baseline == nil {
		return errors.New("생성한 접속 경로의 소유 정보를 확인하지 못했습니다")
	}
	before, err := c.serveConfig(ctx)
	if err != nil {
		return err
	}
	if !exactRoute(before, p) || funnelEnabled(before, p.HTTPSPort) {
		return errors.New("접속 경로가 변경되어 자동 제거하지 않습니다")
	}
	// `serve ... off` removes the entire port, including any different host or
	// foreground route another client added while this operation was running.
	if portOccupied(withoutRoute(before, p), p.HTTPSPort) {
		return errors.New("같은 HTTPS 포트에 다른 경로가 추가되어 자동 제거하지 않습니다")
	}
	_, err = c.Runner.Run(ctx, "serve", "--bg", "--https="+strconv.Itoa(p.HTTPSPort), "off")
	if err != nil {
		return err
	}
	after, err := c.serveConfig(ctx)
	if err != nil {
		return err
	}
	if portOccupied(after, p.HTTPSPort) || !reflect.DeepEqual(normalized(after), withoutRoute(before, p)) {
		return errors.New("접속 경로 제거 후 설정 확인이 필요합니다")
	}
	return nil
}

func normalized(config map[string]any) map[string]any {
	result := map[string]any{}
	for key, value := range config {
		if child, ok := value.(map[string]any); ok {
			value = normalized(child)
			if len(value.(map[string]any)) == 0 {
				continue
			}
		}
		if value != nil {
			result[key] = value
		}
	}
	return result
}

func withoutRoute(config map[string]any, p Plan) map[string]any {
	copy := normalized(config)
	delete(object(copy, "TCP"), strconv.Itoa(p.HTTPSPort))
	delete(object(copy, "Web"), routeKey(p))
	delete(object(copy, "AllowFunnel"), routeKey(p))
	return normalized(copy)
}
