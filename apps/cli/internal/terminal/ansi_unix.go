//go:build !windows

package terminal

import "os"

func supportsANSI(_ *os.File) bool { return true }
