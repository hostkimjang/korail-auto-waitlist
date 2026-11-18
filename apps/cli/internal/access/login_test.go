package access

import (
	"strings"
	"testing"
)

func TestLoginURLAcceptsOnlyUnambiguousOfficialAuthenticationPath(t *testing.T) {
	valid := "https://login.tailscale.com/a/fixture-auth_token-123"
	for _, line := range []string{valid, "\t" + valid + "\t", "To authenticate, visit: " + valid} {
		if LoginURLFromLine(line) != valid {
			t.Fatal("official login URL was not recognized")
		}
	}
	for _, line := range []string{
		"http://login.tailscale.com/a/token", "https://other.example/a/token",
		"https://login.tailscale.com.evil.example/a/token", "https://user@login.tailscale.com/a/token",
		"https://login.tailscale.com:443/a/token", "https://login.tailscale.com:invalid/a/token",
		"https://login.tailscale.com/a/token#", "https://login.tailscale.com/a/token#fragment",
		"https://login.tailscale.com/a/token?key=secret", "https://login.tailscale.com/a/token?",
		"https://login.tailscale.com/a/", "https://login.tailscale.com/a/token/extra",
		"https://login.tailscale.com/a/%74oken", "https://login.tailscale.com/a/token%0A",
		"https://login.tailscale.com/a/token\x1b", "https://login.tailscale.com/a/tok\u200ben",
		"https://login.tailscale.com/f/serve?node=fixture", "https://login.tailscale.com/admin/machines",
		valid + " https://login.tailscale.com/a/another-token", valid + strings.Repeat("x", 8192),
	} {
		if LoginURLFromLine(line) != "" {
			t.Fatal("untrusted authentication URL was accepted")
		}
	}
}
