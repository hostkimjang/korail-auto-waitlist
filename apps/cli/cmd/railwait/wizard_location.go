package main

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/install"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/setup"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/terminal"
)

const (
	firstLocalPort   = 8080
	localPortChoices = 20
)

// installFolderChoice records why the default folder moved so the plan can say so.
type installFolderChoice struct {
	Path string
	// Moved is true when the platform default is not writable from the Docker VM
	// and a writable shared folder of that VM was chosen instead.
	Moved bool
	// Unshared is true when the VM has no persistent writable shared folder.
	Unshared bool
	VM       setup.DockerVM
	IsVM     bool
}

// chooseInstallFolder keeps the platform default unless the Docker engine runs in
// a Lima or Colima VM that cannot write there. Bind mounts of an unshared folder
// silently resolve inside the VM, so a writable shared folder is the safe default.
func chooseInstallFolder(platformDefault string, vm setup.DockerVM, isVM bool) installFolderChoice {
	choice := installFolderChoice{Path: platformDefault, VM: vm, IsVM: isVM}
	if !isVM || writableShared(vm, platformDefault) {
		return choice
	}
	for _, folder := range vm.Folders {
		if folder.Writable && persistentFolder(folder.Path) {
			choice.Path, choice.Moved = filepath.Join(filepath.FromSlash(folder.Path), "railwait"), true
			return choice
		}
	}
	choice.Unshared = true
	return choice
}

func writableShared(vm setup.DockerVM, target string) bool {
	target = filepath.ToSlash(filepath.Clean(target))
	for _, folder := range vm.Folders {
		if folder.Writable && (target == folder.Path || strings.HasPrefix(target, strings.TrimSuffix(folder.Path, "/")+"/")) {
			return true
		}
	}
	return false
}

// Temporary folders are shared writable by default in Lima but are cleared by
// the OS, so they must never hold the database configuration and logs.
func persistentFolder(folder string) bool {
	for _, prefix := range []string{"/tmp", "/private/tmp", "/var/folders", "/private/var/folders"} {
		if folder == prefix || strings.HasPrefix(folder, prefix+"/") {
			return false
		}
	}
	return true
}

func describeVM(vm setup.DockerVM) string {
	name := "Lima"
	if vm.Kind == "colima" {
		name = "Colima"
	}
	return name + " VM " + vm.Name
}

func folderInputWith(ctx context.Context, ui *terminal.UI, choice installFolderChoice, read func(context.Context) (string, error)) (string, error) {
	for {
		ui.Prompt("설치 폴더 [Enter: 기본값]: ", "기본값 "+choice.Path)
		value, err := read(ctx)
		if err != nil {
			return "", err
		}
		value = strings.TrimSpace(value)
		if value == "" {
			return choice.Path, nil
		}
		if rest, ok := strings.CutPrefix(value, "~"); ok && (rest == "" || rest[0] == '/' || rest[0] == '\\') {
			home, homeErr := os.UserHomeDir()
			if homeErr != nil {
				return "", homeErr
			}
			value = home + rest
		}
		folder, err := filepath.Abs(value)
		if err != nil || strings.ContainsAny(folder, "'\r\n") {
			ui.Info("작은따옴표나 줄바꿈이 없는 폴더 경로를 입력하세요.")
			continue
		}
		if choice.IsVM && !choice.Unshared && !writableShared(choice.VM, folder) {
			ui.Info(describeVM(choice.VM) + "가 쓸 수 있게 공유한 폴더 안의 경로를 입력하세요.")
			continue
		}
		return folder, nil
	}
}

func portInputWith(ctx context.Context, ui *terminal.UI, suggested int, available func(int) error, read func(context.Context) (string, error)) (int, error) {
	for {
		ui.Prompt(fmt.Sprintf("접속 포트 [%d]: ", suggested), "이 컴퓨터에서 여는 주소 http://127.0.0.1:포트 입니다. Enter를 누르면 위 포트를 씁니다.")
		value, err := read(ctx)
		if err != nil {
			return 0, err
		}
		value = strings.TrimSpace(value)
		if value == "" {
			return suggested, nil
		}
		port, convErr := strconv.Atoi(value)
		if convErr != nil || port < 1 || port > 65535 {
			ui.Info("1~65535 사이의 숫자로 입력하세요.")
			continue
		}
		if err = available(port); err != nil {
			ui.Info(fmt.Sprintf("%d 포트는 이미 사용 중입니다. 다른 포트를 입력하세요.", port))
			continue
		}
		return port, nil
	}
}

// selectLocalPort keeps an explicit or recorded port and otherwise uses the first
// free loopback port from 8080, so a busy 8080 no longer stops a new install.
func selectLocalPort(requested int, available func(int) error) (port int, automatic bool, err error) {
	if requested != 0 {
		return requested, false, nil
	}
	port, err = install.FirstAvailablePort(firstLocalPort, localPortChoices, available)
	if err != nil {
		return 0, false, err
	}
	return port, true, nil
}
