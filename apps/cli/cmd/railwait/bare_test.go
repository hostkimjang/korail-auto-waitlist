package main

import (
	"bytes"
	"context"
	"strings"
	"testing"
)

func TestBareRailwaitListsCommandsUnlessDoubleClickedInExplorer(t *testing.T) {
	previousOutput, previousDoubleClicked, previousWizard := helpOutput, doubleClicked, installWizard
	defer func() {
		helpOutput, doubleClicked, installWizard = previousOutput, previousDoubleClicked, previousWizard
	}()
	var out bytes.Buffer
	helpOutput = &out
	wizardRuns := 0
	installWizard = func(_ context.Context, args []string) error {
		if len(args) != 0 {
			t.Fatalf("wizard arguments: %v", args)
		}
		wizardRuns++
		return nil
	}

	doubleClicked = func() bool { return false }
	if err := run(context.Background(), nil); err != nil || wizardRuns != 0 || !strings.Contains(out.String(), "railwait install") {
		t.Fatalf("terminal: err=%v wizard=%d output=%q", err, wizardRuns, out.String())
	}

	out.Reset()
	doubleClicked = func() bool { return true }
	if err := run(context.Background(), nil); err != nil || wizardRuns != 1 || out.Len() != 0 {
		t.Fatalf("double click: err=%v wizard=%d output=%q", err, wizardRuns, out.String())
	}

	// A double-clicked railwait.exe given arguments, for example by a shortcut, runs them.
	out.Reset()
	if err := run(context.Background(), []string{"help"}); err != nil || wizardRuns != 1 || out.Len() == 0 {
		t.Fatalf("arguments: err=%v wizard=%d", err, wizardRuns)
	}
}
