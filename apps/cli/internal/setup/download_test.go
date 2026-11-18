package setup

import (
	"context"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type dependencyRoundTrip func(*http.Request) (*http.Response, error)

func (f dependencyRoundTrip) RoundTrip(req *http.Request) (*http.Response, error) { return f(req) }

func TestDownloadRejectsUntrustedOriginsAndRedirects(t *testing.T) {
	for _, address := range []string{"http://desktop.docker.com/file", "https://desktop.docker.com.evil.example/file", "https://desktop.docker.com:443/file", "https://person@desktop.docker.com/file", "https://pkgs.tailscale.com/file#fake", "https://example.com/file"} {
		if dependencyURLAllowed("docker", address) {
			t.Fatal("unsafe origin accepted", address)
		}
	}
	p, _ := DependencyPlan("docker", "darwin", "arm64")
	client := downloadClient(p)
	requests := 0
	client.Transport = dependencyRoundTrip(func(req *http.Request) (*http.Response, error) {
		requests++
		return &http.Response{StatusCode: 302, Header: http.Header{"Location": []string{"https://evil.example/payload"}}, Body: io.NopCloser(strings.NewReader("")), Request: req}, nil
	})
	if err := downloadWithClient(context.Background(), client, p, filepath.Join(t.TempDir(), "file"), func(string) {}); err == nil || requests != 1 {
		t.Fatalf("external redirect followed: %v requests=%d", err, requests)
	}
}

func TestDownloadLimitsAndCompleteness(t *testing.T) {
	for _, tc := range []struct {
		name, body  string
		length, max int64
		status      int
		success     bool
	}{
		{"complete", "abc", 3, 4, 200, true},
		{"oversized-header", "abc", 9, 4, 200, false},
		{"oversized-stream", "abcde", -1, 4, 200, false},
		{"truncated", "ab", 3, 4, 200, false},
		{"empty", "", -1, 4, 200, false},
		{"error-response", "abc", 3, 4, 404, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p, _ := DependencyPlan("tailscale", "darwin", "arm64")
			p.MaxBytes = tc.max
			client := &http.Client{Transport: dependencyRoundTrip(func(req *http.Request) (*http.Response, error) {
				return &http.Response{StatusCode: tc.status, ContentLength: tc.length, Body: io.NopCloser(strings.NewReader(tc.body)), Request: req}, nil
			})}
			path := filepath.Join(t.TempDir(), "artifact")
			err := downloadWithClient(context.Background(), client, p, path, func(string) {})
			if (err == nil) != tc.success {
				t.Fatalf("success=%v error=%v", tc.success, err)
			}
			if tc.success {
				data, err := os.ReadFile(path)
				if err != nil || string(data) != tc.body {
					t.Fatal("wrong artifact", err)
				}
			} else if _, err := os.Stat(path); !os.IsNotExist(err) {
				t.Fatal("partial download remains", err)
			}
		})
	}
}

func TestDownloadNeverOverwritesExistingArtifact(t *testing.T) {
	p, _ := DependencyPlan("docker", "darwin", "arm64")
	client := &http.Client{Transport: dependencyRoundTrip(func(req *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, ContentLength: 3, Body: io.NopCloser(strings.NewReader("new")), Request: req}, nil
	})}
	path := filepath.Join(t.TempDir(), "artifact")
	if err := os.WriteFile(path, []byte("existing"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := downloadWithClient(context.Background(), client, p, path, func(string) {}); err == nil {
		t.Fatal("existing file overwritten")
	}
	data, err := os.ReadFile(path)
	if err != nil || string(data) != "existing" {
		t.Fatal("original file changed", err)
	}
}
