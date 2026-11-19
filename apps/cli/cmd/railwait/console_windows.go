package main

import (
	"syscall"
	"unsafe"
)

var procGetConsoleProcessList = syscall.NewLazyDLL("kernel32.dll").NewProc("GetConsoleProcessList")

// consoleOwnedByThisProcess reports whether this process is the only one attached to its
// console. Explorer starts a double-clicked console program that way, while cmd.exe,
// PowerShell and Windows Terminal share their console with the programs they start.
func consoleOwnedByThisProcess() bool {
	var ids [2]uint32
	count, _, _ := procGetConsoleProcessList.Call(uintptr(unsafe.Pointer(&ids[0])), uintptr(len(ids)))
	return count == 1
}
