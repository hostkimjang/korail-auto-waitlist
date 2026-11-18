package main

import (
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/terminal"
)

func wizardExecutable() string {
	executable, err := os.Executable()
	if err != nil || executable == "" {
		return "railwait"
	}
	return executable
}

// windowsCommandHint is shown above copyable commands on Windows. A long command
// wraps in the terminal, and copying from the path instead of the leading & breaks
// it in PowerShell.
const windowsCommandHint = "아래 명령은 PowerShell에서 맨 앞의 & 부터 줄 끝까지 그대로 복사해 실행하세요."

// Shell syntax is selected explicitly: Windows examples target PowerShell.
// Go's %q uses double quotes, which still expand $, backticks and substitutions
// in a user's shell. Quote every argument as literal data instead.
func wizardShellCommand(goos, executable string, args ...string) (string, error) {
	parts := make([]string, 0, len(args)+1)
	for _, value := range append([]string{executable}, args...) {
		if !utf8.ValidString(value) || strings.IndexFunc(value, func(r rune) bool { return unicode.IsControl(r) || unicode.Is(unicode.Cf, r) }) >= 0 {
			return "", errors.New("제어 문자가 포함된 경로는 복사할 재개 명령으로 표시할 수 없습니다")
		}
		if goos == "windows" {
			parts = append(parts, "'"+strings.ReplaceAll(value, "'", "''")+"'")
		} else {
			parts = append(parts, "'"+strings.ReplaceAll(value, "'", "'\"'\"'")+"'")
		}
	}
	command := strings.Join(parts, " ")
	if goos == "windows" {
		command = "& " + command
	}
	return command, nil
}

func wizardResumeArgs(o wizardOptions) []string {
	args := []string{"install", "--path", o.Path}
	for _, option := range []struct{ flag, value string }{
		{"--package", o.Package}, {"--trust-key", o.TrustKey},
		{"--access", o.Access}, {"--docker-context", o.DockerContext},
		{"--input", o.Input},
	} {
		if option.value != "" {
			args = append(args, option.flag, option.value)
		}
	}
	if o.Port != 0 {
		args = append(args, "--port", strconv.Itoa(o.Port))
	}
	if o.HTTPSPort != 0 {
		args = append(args, "--https-port", strconv.Itoa(o.HTTPSPort))
	}
	for _, option := range []struct {
		flag    string
		enabled bool
	}{
		{"--plain", o.Plain}, {"--yes", o.Yes}, {"--open", o.Open}, {"--custom", o.Custom},
	} {
		if option.enabled {
			args = append(args, option.flag)
		}
	}
	return args
}

func showWizardCommand(ui *terminal.UI, args ...string) {
	command, err := wizardShellCommand(runtime.GOOS, wizardExecutable(), args...)
	if err != nil {
		ui.Hint(err.Error())
		return
	}
	ui.Command(command)
}

func showWizardStatusCommand(ui *terminal.UI, path string) {
	showWizardCommand(ui, "status", "--path", path)
}

// pasteableCommand renders a follow-up command for plain output. The Windows
// one-line install does not put railwait on PATH, so the running executable is
// named in full and every argument is quoted for the user's shell.
func pasteableCommand(args ...string) string {
	command, err := wizardShellCommand(runtime.GOOS, wizardExecutable(), args...)
	if err != nil {
		return "railwait " + strings.Join(args, " ")
	}
	return command
}

func showWizardResumeCommands(ui *terminal.UI, o wizardOptions) {
	if runtime.GOOS == "windows" {
		ui.Hint(windowsCommandHint)
	}
	// WSL/Docker preparation can stop before Railwait has an installation record.
	// A status command is useful only once that record exists.
	if _, err := os.Lstat(filepath.Join(o.Path, ".railwait", "install.json")); err == nil {
		showWizardStatusCommand(ui, o.Path)
	}
	command, err := wizardShellCommand(runtime.GOOS, wizardExecutable(), wizardResumeArgs(o)...)
	if err != nil {
		ui.Hint(err.Error())
		ui.Hint("처음 사용한 설치 명령을 다시 실행해 주세요.")
		return
	}
	ui.Command(command)
	if o.Input == "-" {
		ui.Hint("--input - 에 전달했던 보호된 JSON을 표준 입력으로 다시 전달해야 합니다. 입력 내용은 재개 명령에 저장하지 않습니다.")
	}
}
