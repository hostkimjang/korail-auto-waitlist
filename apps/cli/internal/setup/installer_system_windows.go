//go:build windows

package setup

import "golang.org/x/sys/windows"

func installerWindowsSystemDirectory() (string, error) {
	return windows.GetSystemDirectory()
}
