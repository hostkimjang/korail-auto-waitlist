//go:build !windows

package main

import (
	"context"
	"io"
	"os/exec"
)

type execRevealRunner struct{}

func (execRevealRunner) Run(ctx context.Context, command revealCommand) error {
	cmd := exec.CommandContext(ctx, command.Program, command.Args...)
	cmd.Stdout, cmd.Stderr = io.Discard, io.Discard
	return cmd.Run()
}
