package access

import (
	"net/url"
	"strings"
)

// DeviceApprovalURL has no device token and never approves a device itself.
const DeviceApprovalURL = "https://login.tailscale.com/admin/machines"

// LoginURLFromLine extracts only the documented official authentication path.
// A login token remains in memory for the browser handoff; callers must not log
// the source line, the returned address or errors from an external browser.
func LoginURLFromLine(line string) string {
	if len(line) > 8192 {
		return ""
	}
	var found string
	for _, field := range strings.Fields(line) {
		u, err := url.Parse(field)
		if err != nil || u.Scheme != "https" || u.Host != "login.tailscale.com" || u.User != nil || u.RawPath != "" || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || u.Opaque != "" || strings.Contains(field, "#") || !strings.HasPrefix(u.Path, "/a/") {
			continue
		}
		token := strings.TrimPrefix(u.Path, "/a/")
		if len(token) == 0 || len(token) > 256 || strings.IndexFunc(token, func(r rune) bool {
			return !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '-' || r == '_')
		}) >= 0 {
			continue
		}
		if found != "" && found != field {
			return ""
		}
		found = field
	}
	return found
}
