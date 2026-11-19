//go:build !windows

package main

// consoleOwnedByThisProcess is false outside Windows. There a double-clicked program runs in a
// terminal window that stays open after it exits, so the command list stays readable.
func consoleOwnedByThisProcess() bool { return false }
