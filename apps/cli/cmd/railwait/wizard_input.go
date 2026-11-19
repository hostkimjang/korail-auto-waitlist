package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/install"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/setup"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/terminal"
	"golang.org/x/term"
)

type presentedError struct{ cause error }

func (e *presentedError) Error() string { return e.cause.Error() }
func (e *presentedError) Unwrap() error { return e.cause }

// Reading a byte at a time avoids buffering a pasted password behind a previous
// public prompt. Secret input remains owned by the terminal's echo-off reader.
func readLine(ctx context.Context) (string, error) {
	type result struct {
		value string
		err   error
	}
	done := make(chan result, 1)
	go func() {
		var data []byte
		one := make([]byte, 1)
		for len(data) <= 4096 {
			n, err := os.Stdin.Read(one)
			if n == 1 {
				if one[0] == '\n' {
					done <- result{strings.TrimSpace(string(data)), nil}
					return
				}
				data = append(data, one[0])
			}
			if err != nil {
				done <- result{"", err}
				return
			}
		}
		done <- result{"", errors.New("입력이 너무 깁니다")}
	}()
	select {
	case <-ctx.Done():
		return "", ctx.Err()
	case r := <-done:
		return r.value, r.err
	}
}

func secretInput(ctx context.Context, ui *terminal.UI, label, hint string) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	fd := int(os.Stdin.Fd())
	if !term.IsTerminal(fd) {
		return "", errors.New("숨김 입력에는 터미널이 필요합니다. --input으로 보호된 JSON을 지정하세요")
	}
	ui.Prompt(label, hint+" 입력 내용은 표시되지 않습니다.")
	// Change terminal mode synchronously before starting the reader. Cancellation
	// must never restore echo before a late ReadPassword goroutine disables it.
	state, err := term.MakeRaw(fd)
	if err != nil {
		return "", err
	}
	defer term.Restore(fd, state)
	type result struct {
		data string
		err  error
	}
	done := make(chan result)
	go func() {
		reader := term.NewTerminal(terminalIO{os.Stdin, os.Stderr}, "")
		data, err := reader.ReadPassword("")
		if errors.Is(err, term.ErrPasteIndicator) {
			err = nil
		}
		select {
		case done <- result{data, err}:
		case <-ctx.Done():
		}
	}()
	select {
	case <-ctx.Done():
		term.Restore(fd, state)
		ui.Newline()
		return "", ctx.Err()
	case r := <-done:
		if errors.Is(r.err, io.EOF) {
			term.Restore(fd, state)
			ui.Newline()
			return "", context.Canceled
		}
		return r.data, r.err
	}
}

type terminalIO struct {
	io.Reader
	io.Writer
}

func choose(ctx context.Context, ui *terminal.UI, label, hint, fallback string, allowed ...string) (string, error) {
	return chooseWith(ctx, ui, readLine, label, hint, fallback, allowed...)
}

// chooseWith asks until the answer is one of allowed; an empty answer picks fallback.
func chooseWith(ctx context.Context, ui *terminal.UI, read func(context.Context) (string, error), label, hint, fallback string, allowed ...string) (string, error) {
	for {
		ui.Prompt(label, hint)
		value, err := read(ctx)
		if err != nil {
			return "", err
		}
		if value == "" {
			value = fallback
		}
		for _, option := range allowed {
			if value == option {
				return value, nil
			}
		}
		ui.Info("표시된 번호를 입력해 주세요.")
	}
}

func prerequisitePrompt(ctx context.Context, ui *terminal.UI) setup.Interact {
	return prerequisitePromptWith(ctx, ui, setup.InstallDependency)
}

func prerequisitePromptWith(ctx context.Context, ui *terminal.UI, run dependencyInstaller) setup.Interact {
	installAttempted, startAttempted := false, false
	return func(need setup.Need) (setup.Action, error) {
		if need.CanInstall && !installAttempted {
			installAttempted = true
			if err := installPrerequisite(ctx, ui, "docker", run); err != nil {
				return setup.Cancel, err
			}
			return setup.Retry, nil
		}
		if need.CanStart && !startAttempted {
			startAttempted = true
			ui.Start("Docker Desktop 시작 · 약관·권한 승인 후 최대 5분 자동 확인")
			return setup.Start, nil
		}
		ui.Pause()
		ui.Section(need.Title)
		ui.Info(need.Detail)
		ui.KeyValue("공식 안내", need.URL)
		hint := "1 공식 설치 안내 열기  ·  2 준비 후 다시 확인  ·  0 중단"
		allowed := []string{"0", "1", "2"}
		if need.CanStart {
			hint += "  ·  3 설치된 Docker Desktop 시작"
			allowed = append(allowed, "3")
		}
		answer, err := choose(ctx, ui, "선택 [2]: ", hint, "2", allowed...)
		if err != nil {
			return setup.Cancel, err
		}
		switch answer {
		case "1":
			return setup.OpenGuide, nil
		case "2":
			ui.Start("준비 상태 다시 확인")
			return setup.Retry, nil
		case "3":
			ui.Start("Docker Desktop 시작 · 최대 5분 자동 확인")
			return setup.Start, nil
		default:
			return setup.Cancel, nil
		}
	}
}

type dependencyInstaller func(context.Context, string, func(string)) (setup.InstallResult, error)

func installPrerequisite(ctx context.Context, ui *terminal.UI, tool string, run dependencyInstaller) error {
	ui.Pause()
	ui.Info("필수 프로그램을 설치합니다. 시스템 승인·약관 동의는 열린 설치 화면에서 진행해 주세요.")
	result, err := run(ctx, tool, func(message string) {
		ui.Pause()
		ui.Info(message)
	})
	if err != nil {
		return err
	}
	if result.Message != "" {
		ui.Info(result.Message)
	}
	if result.RebootRequired {
		return &prerequisiteRebootRequired{}
	}
	if !result.Installed {
		return errors.New("필수 프로그램 설치가 끝나지 않았습니다. 설치 화면을 확인한 뒤 같은 명령으로 다시 실행하세요")
	}
	ui.Start("설치한 프로그램의 실행 상태 확인")
	return nil
}

func validateAdministrator(username, password string) error {
	if !regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{2,63}$`).MatchString(username) {
		return errors.New("아이디는 영문 소문자·숫자로 시작하고 영문 소문자·숫자·점·밑줄·하이픈 3~64자로 입력하세요")
	}
	if n := utf8.RuneCountInString(password); n < 12 || n > 128 {
		return errors.New("비밀번호는 12~128자로 입력하세요")
	}
	return nil
}

func administratorInput(ctx context.Context, ui *terminal.UI) (install.Credentials, error) {
	ui.Section("관리자 계정")
	for {
		ui.Prompt("아이디: ", "영문 소문자·숫자·점·밑줄·하이픈, 3~64자")
		name, err := readLine(ctx)
		if err != nil {
			return install.Credentials{}, err
		}
		name = strings.ToLower(strings.TrimSpace(name))
		if err = validateAdministrator(name, strings.Repeat("x", 12)); err != nil {
			ui.Info(err.Error())
			continue
		}
		for attempt := 0; attempt < 3; attempt++ {
			password, err := secretInput(ctx, ui, "비밀번호: ", "12~128자로 입력하세요.")
			if err != nil {
				return install.Credentials{}, err
			}
			if err = validateAdministrator(name, password); err != nil {
				ui.Info(err.Error())
				continue
			}
			confirmation, err := secretInput(ctx, ui, "비밀번호 확인: ", "같은 비밀번호를 한 번 더 입력하세요.")
			if err != nil {
				return install.Credentials{}, err
			}
			if password != confirmation {
				ui.Info("두 비밀번호가 다릅니다. 다시 입력해 주세요.")
				continue
			}
			ui.Done("관리자 입력 준비됨")
			return install.Credentials{Username: name, Password: password}, nil
		}
		return install.Credentials{}, fmt.Errorf("비밀번호 확인을 마치지 못했습니다. 같은 설치 명령으로 다시 시작하세요")
	}
}

func isUserStop(err error) bool {
	return errors.Is(err, context.Canceled) || errors.Is(err, setup.ErrCancelled) || errors.Is(err, io.EOF)
}
