package main

import (
	"bytes"
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/terminal"
)

func TestExistingInstallChoiceShowsTheInstallationAndDefaultsToOpeningIt(t *testing.T) {
	var output bytes.Buffer
	ui := terminal.New(&output, terminal.Plain(120))
	defer ui.Pause()
	summary := existingInstallSummary{
		Path: "/data/railwait", Release: "1.4.10", Manager: "1.4.5 · 실행 중", Address: "https://my-device.example.ts.net",
		Changes: []string{"관리 서비스를 1.4.5에서 1.5.2로 바꿉니다."},
	}
	for _, test := range []struct {
		lines []string
		want  existingChoice
	}{
		{[]string{""}, existingOpen},
		{[]string{"1"}, existingOpen},
		{[]string{"2"}, existingProceed},
		{[]string{"0"}, existingClose},
		{[]string{"y", "0"}, existingClose},
	} {
		got, err := existingInstallChoice(context.Background(), ui, summary, scriptedLines(test.lines...))
		if err != nil || got != test.want {
			t.Fatalf("%q: got %v %v", test.lines, got, err)
		}
	}
	for _, part := range []string{"/data/railwait", "1.4.10", "1.4.5 · 실행 중", "https://my-device.example.ts.net", "1.5.2로 바꿉니다", "0 닫기", "표시된 번호"} {
		if !strings.Contains(output.String(), part) {
			t.Fatalf("output lacks %q:\n%s", part, output.String())
		}
	}
	if _, err := existingInstallChoice(context.Background(), ui, summary, scriptedLines()); err == nil {
		t.Fatal("a closed input must not count as a choice")
	}
}

func TestDescribeExistingInstallSaysWhatGoingAheadChanges(t *testing.T) {
	root, state := installFixture(t)
	previous := version
	version = "1.5.2"
	defer func() { version = previous }()
	versionOf := func(release string) func(context.Context, string) (string, error) {
		return func(context.Context, string) (string, error) { return release, nil }
	}

	summary := describeExistingInstall(context.Background(), root, state, versionOf("1.4.5"))
	if summary.Manager != "설치되지 않음" || !strings.Contains(strings.Join(summary.Changes, "\n"), "등록하고 시작합니다") {
		t.Fatalf("without a manager: %+v", summary)
	}

	if err := platform.SecureDir(filepath.Dir(managerExecutablePath(root))); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(managerExecutablePath(root), []byte("manager"), 0o700); err != nil {
		t.Fatal(err)
	}
	writeManagerHeartbeat(t, root, 3333)
	summary = describeExistingInstall(context.Background(), root, state, versionOf("1.4.5"))
	if summary.Path != root || summary.Release != "1.4.10" || summary.Address != "http://127.0.0.1:8080" || summary.Manager != "1.4.5 · 실행 중" {
		t.Fatalf("summary: %+v", summary)
	}
	if !strings.Contains(strings.Join(summary.Changes, "\n"), "1.4.5에서 1.5.2로 바꿉니다") {
		t.Fatalf("an older manager must be named as replaced: %q", summary.Changes)
	}

	summary = describeExistingInstall(context.Background(), root, state, versionOf("1.5.2"))
	if strings.Contains(strings.Join(summary.Changes, "\n"), "관리 서비스") {
		t.Fatalf("a current running manager is left alone: %q", summary.Changes)
	}
	summary = describeExistingInstall(context.Background(), root, state, func(context.Context, string) (string, error) {
		return "", errors.New("no version output")
	})
	if summary.Manager != "버전 확인 못 함 · 실행 중" {
		t.Fatalf("unreadable version: %+v", summary)
	}
}
