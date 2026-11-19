package main

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/install"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

// revealCommand is the file manager command that shows the settings file.
type revealCommand struct {
	Program string
	Args    []string
}

type revealRunner interface {
	Run(context.Context, revealCommand) error
}

// opener prints an installation's .env path and shows it in the OS file manager.
type opener struct {
	runner revealRunner
	goos   string
	getenv func(string) string
	out    io.Writer
	log    io.Writer
}

func openCommand(ctx context.Context, args []string) error {
	defaultPath, err := platform.DefaultInstallDir()
	if err != nil {
		return err
	}
	flags := newCommandFlags("open")
	path := flags.String("path", defaultPath, "설치 경로")
	if err = parseCommandFlags(flags, args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return fmt.Errorf("위치 인자를 사용할 수 없습니다")
	}
	return opener{runner: execRevealRunner{}, goos: runtime.GOOS, getenv: os.Getenv, out: os.Stdout, log: os.Stderr}.run(ctx, *path)
}

func (o opener) run(ctx context.Context, path string) error {
	abs, err := filepath.Abs(path)
	if err != nil {
		return err
	}
	if _, err = install.ReadState(abs); err != nil {
		return err
	}
	env := filepath.Join(abs, ".env")
	if info, err := os.Lstat(env); err != nil || !info.Mode().IsRegular() {
		return fmt.Errorf("설치 폴더에 설정 파일이 없습니다: %s", env)
	}
	fmt.Fprintln(o.out, env)
	command := revealFor(o.goos, abs, env)
	manual := manualRevealCommand(o.goos, abs, env)
	if !guiAvailable(o.goos, o.getenv) {
		fmt.Fprintln(o.log, "이 세션에는 화면이 없어 파일 관리자를 열지 않았습니다. 화면이 있는 세션에서는 다음 명령으로 열 수 있습니다:")
		fmt.Fprintln(o.log, "  "+manual)
		return nil
	}
	revealCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	if err := o.runner.Run(revealCtx, command); err != nil {
		fmt.Fprintln(o.log, "파일 관리자를 열지 못했습니다. 다음 명령으로 직접 열 수 있습니다:")
		fmt.Fprintln(o.log, "  "+manual)
		return nil
	}
	fmt.Fprintln(o.log, "파일 관리자에서 설정 파일 위치를 열었습니다.")
	fmt.Fprintln(o.log, "설정을 직접 고쳤다면 railwait restart로 앱 서비스에 반영합니다.")
	return nil
}

// revealFor selects the file in Finder or Explorer. Linux file managers have no common
// way to select a file, so the installation folder is opened instead.
func revealFor(goos, installation, env string) revealCommand {
	switch goos {
	case "darwin":
		return revealCommand{"open", []string{"-R", env}}
	case "windows":
		return revealCommand{"explorer.exe", []string{"/select," + env}}
	}
	return revealCommand{"xdg-open", []string{installation}}
}

// manualRevealCommand is a command the user can paste into their own shell. PowerShell
// re-quotes explorer's /select argument, so on Windows it opens the folder instead.
func manualRevealCommand(goos, installation, env string) string {
	if goos == "windows" {
		return shellCommand(goos, "explorer.exe", installation)
	}
	command := revealFor(goos, installation, env)
	return shellCommand(goos, command.Program, command.Args...)
}

// windowsRevealCommandLine writes explorer's command line as it expects it: the path
// after /select, is quoted on its own, which Go's argument quoting cannot express.
func windowsRevealCommandLine(command revealCommand) string {
	if command.Program == "explorer.exe" && len(command.Args) == 1 {
		if path, ok := strings.CutPrefix(command.Args[0], "/select,"); ok {
			return `explorer.exe /select,"` + path + `"`
		}
	}
	return ""
}

// guiAvailable reports whether a file manager window can appear for this user. A remote
// shell would open it on another screen, if at all.
func guiAvailable(goos string, getenv func(string) string) bool {
	if getenv("SSH_CONNECTION") != "" || getenv("SSH_TTY") != "" {
		return false
	}
	if goos == "linux" {
		return getenv("DISPLAY") != "" || getenv("WAYLAND_DISPLAY") != ""
	}
	return true
}
