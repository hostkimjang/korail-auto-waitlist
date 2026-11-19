package main

import (
	"context"
	"os"

	"golang.org/x/term"
)

// doubleClicked reports whether railwait.exe was double-clicked in Windows Explorer: the
// process has a console of its own and reads from it. It is a variable so tests can decide.
var doubleClicked = func() bool {
	return consoleOwnedByThisProcess() && term.IsTerminal(int(os.Stdin.Fd()))
}

// installWizard runs the install wizard; a variable so tests can stand in for it.
var installWizard = installCommand

// bareInvocation handles railwait without arguments. It shows the command list, except for a
// double-clicked railwait.exe: Explorer closes that console as soon as railwait exits, so the
// list would only flash, and such a user expects the installer as before.
func bareInvocation(ctx context.Context) error {
	if doubleClicked() {
		return installWizard(ctx, nil)
	}
	printTopHelp(helpOutput)
	return nil
}
