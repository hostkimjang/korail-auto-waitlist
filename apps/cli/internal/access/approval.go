package access

import (
	"errors"
	"net/url"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

// HTTPSApprovalRequired keeps the one-time consent address only in memory.
// Error formatting and JSON encoding must never expose its query parameters.
type HTTPSApprovalRequired struct{ address string }

func (*HTTPSApprovalRequired) Error() string {
	return "Tailscale HTTPS 사용 승인이 필요합니다. 공식 HTTPS 설정을 승인한 뒤 다시 실행하세요"
}
func (e *HTTPSApprovalRequired) GoString() string { return e.Error() }
func (e *HTTPSApprovalRequired) URL() string {
	if e == nil {
		return ""
	}
	return e.address
}

func NewHTTPSApprovalRequired(address string) (*HTTPSApprovalRequired, error) {
	if !validHTTPSApprovalURL(address) {
		return nil, errors.New("공식 Tailscale HTTPS 승인 주소가 아닙니다")
	}
	return &HTTPSApprovalRequired{address: address}, nil
}

func validHTTPSApprovalURL(address string) bool {
	if len(address) == 0 || len(address) > 4096 || !utf8.ValidString(address) || strings.Contains(address, "#") || strings.IndexFunc(address, func(r rune) bool { return unicode.IsControl(r) || unicode.IsSpace(r) || unicode.Is(unicode.Cf, r) }) >= 0 {
		return false
	}
	u, err := url.Parse(address)
	if err != nil || u.Scheme != "https" || u.Host != "login.tailscale.com" || u.User != nil || u.Path != "/f/serve" || u.RawPath != "" || u.Fragment != "" || u.Opaque != "" {
		return false
	}
	query, err := url.ParseQuery(u.RawQuery)
	if err != nil {
		return false
	}
	for name, values := range query {
		for _, value := range append([]string{name}, values...) {
			if !utf8.ValidString(value) || strings.IndexFunc(value, func(r rune) bool { return unicode.IsControl(r) || unicode.Is(unicode.Cf, r) }) >= 0 {
				return false
			}
		}
	}
	return true
}

// Only our exact HTTPS loopback apply command can request this UI. Status,
// login, rollback, Funnel and arbitrary commands never trigger a browser.
func isHTTPSApplyCommand(args []string) bool {
	if len(args) != 4 || args[0] != "serve" || args[1] != "--bg" || !strings.HasPrefix(args[2], "--https=") {
		return false
	}
	port, err := strconv.Atoi(strings.TrimPrefix(args[2], "--https="))
	if err != nil || port < 1 || port > 65535 || args[2] != "--https="+strconv.Itoa(port) {
		return false
	}
	u, err := url.Parse(args[3])
	if err != nil || u.Scheme != "http" || u.Hostname() != "127.0.0.1" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" {
		return false
	}
	localPort, err := strconv.Atoi(u.Port())
	return err == nil && localPort >= 1 && localPort <= 65535 && args[3] == "http://127.0.0.1:"+strconv.Itoa(localPort)
}

func approvalFromOutput(stdout, stderr []byte) *HTTPSApprovalRequired {
	if len(stdout) > maxOutput || len(stderr) > maxOutput {
		return nil
	}
	var found *HTTPSApprovalRequired
	for _, output := range [][]byte{stdout, stderr} {
		for _, field := range strings.Fields(string(output)) {
			candidate, err := NewHTTPSApprovalRequired(field)
			if err != nil {
				continue
			}
			if found != nil && found.address != candidate.address {
				return nil
			}
			found = candidate
		}
	}
	return found
}
