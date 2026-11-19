package main

import (
	"context"
	"errors"
	"io"
	"os/exec"
	"syscall"
)

type execRevealRunner struct{}

func (execRevealRunner) Run(ctx context.Context, command revealCommand) error {
	cmd := exec.CommandContext(ctx, command.Program, command.Args...)
	cmd.Stdout, cmd.Stderr = io.Discard, io.Discard
	if line := windowsRevealCommandLine(command); line != "" {
		cmd.SysProcAttr = &syscall.SysProcAttr{CmdLine: line}
	}
	err := cmd.Run()
	// explorer.exe exits with status 1 even after it opened the window.
	var exit *exec.ExitError
	if command.Program == "explorer.exe" && errors.As(err, &exit) {
		return nil
	}
	return err
}
