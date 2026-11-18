//go:build windows

package terminal

import (
	"os"

	"golang.org/x/sys/windows"
)

func supportsANSI(file *os.File) bool {
	var mode uint32
	if windows.GetConsoleMode(windows.Handle(file.Fd()), &mode) != nil {
		return false
	}
	return mode&windows.ENABLE_VIRTUAL_TERMINAL_PROCESSING != 0
}
