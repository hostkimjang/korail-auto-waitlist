package main

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"

	"golang.org/x/term"
)

// confirmation asks the user before a disruptive command. Without an interactive
// terminal the command must be given --yes instead.
type confirmation struct {
	in          *bufio.Reader
	out         io.Writer
	interactive bool
}

func terminalConfirmation() confirmation {
	return confirmation{in: bufio.NewReader(os.Stdin), out: os.Stderr, interactive: term.IsTerminal(int(os.Stdin.Fd()))}
}

// ask prints the prompt and returns the answer without surrounding spaces.
func (c confirmation) ask(prompt string) (string, error) {
	fmt.Fprint(c.out, prompt)
	line, err := c.in.ReadString('\n')
	if err != nil && (!errors.Is(err, io.EOF) || line == "") {
		return "", errors.New("확인 입력을 읽지 못했습니다")
	}
	return strings.TrimSpace(line), nil
}

// yes asks a yes/no question whose default is no.
func (c confirmation) yes(prompt string) (bool, error) {
	answer, err := c.ask(prompt)
	if err != nil {
		return false, err
	}
	switch strings.ToLower(answer) {
	case "y", "yes", "예", "네":
		return true, nil
	}
	return false, nil
}
