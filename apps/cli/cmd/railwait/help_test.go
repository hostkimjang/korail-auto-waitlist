package main

import (
	"bytes"
	"context"
	"errors"
	"flag"
	"strings"
	"testing"
)

// topLevelCommands is every command run accepts. A new command must be added here, to
// commandHandler and to commandDocs.
var topLevelCommands = []string{"install", "setup", "update", "configure", "manager", "restart", "status", "doctor", "package", "uninstall", "preview", "version", "help"}

func captureHelp(t *testing.T, args ...string) (string, error) {
	t.Helper()
	var buffer bytes.Buffer
	previous := helpOutput
	helpOutput = &buffer
	defer func() { helpOutput = previous }()
	err := run(context.Background(), args)
	if errors.Is(err, flag.ErrHelp) {
		err = nil
	}
	return buffer.String(), err
}

func TestTopLevelHelpListsEveryCommand(t *testing.T) {
	var first string
	for _, args := range [][]string{{}, {"help"}, {"--help"}, {"-h"}} {
		out, err := captureHelp(t, args...)
		if err != nil {
			t.Fatalf("%v: %v", args, err)
		}
		if first == "" {
			first = out
		} else if out != first {
			t.Fatalf("%v printed a different command list", args)
		}
	}
	for _, name := range topLevelCommands {
		if _, ok := commandHandler(name); !ok {
			t.Fatalf("%s has no handler", name)
		}
		doc, ok := findCommandDoc(name)
		if !ok || doc.Group == "" || doc.Summary == "" {
			t.Fatalf("%s has no top-level description", name)
		}
		if !strings.Contains(first, "  "+name+" ") || !strings.Contains(first, doc.Summary) {
			t.Fatalf("%s missing from the command list:\n%s", name, first)
		}
		if !strings.Contains(first, "\n"+doc.Group+"\n") {
			t.Fatalf("group %s missing:\n%s", doc.Group, first)
		}
	}
	listed := 0
	for _, doc := range commandDocs {
		if doc.Group != "" {
			listed++
		}
	}
	if listed != len(topLevelCommands) {
		t.Fatal("the command list has a command that run does not accept")
	}
	for _, part := range []string{"railwait <명령> --help", docsURL, "railwait install", "railwait status"} {
		if !strings.Contains(first, part) {
			t.Fatalf("command list lacks %q", part)
		}
	}
}

func TestHelpCommandMatchesTheHelpFlagOfEveryCommand(t *testing.T) {
	cases := [][]string{}
	for _, name := range topLevelCommands {
		cases = append(cases, []string{name})
	}
	cases = append(cases, []string{"configure", "reservation"}, []string{"manager", "install"}, []string{"package", "verify"}, []string{"update", "check"})
	for _, command := range cases {
		viaHelp, err := captureHelp(t, append([]string{"help"}, command...)...)
		if err != nil {
			t.Fatalf("help %v: %v", command, err)
		}
		viaFlag, err := captureHelp(t, append(append([]string{}, command...), "--help")...)
		if err != nil {
			t.Fatalf("%v --help: %v", command, err)
		}
		if viaHelp != viaFlag || viaHelp == "" {
			t.Fatalf("help %v differs from --help:\n%s\n---\n%s", command, viaHelp, viaFlag)
		}
		if command[0] == "help" {
			continue
		}
		name := command[0]
		if name == "setup" {
			name = "install"
		}
		if command[0] == "configure" && len(command) > 1 {
			name = "configure reservation"
		}
		if !strings.Contains(viaHelp, "사용법:") || !strings.Contains(viaHelp, "railwait "+name) {
			t.Fatalf("%v help lacks its usage:\n%s", command, viaHelp)
		}
	}
	for _, group := range []string{"configure", "manager", "package", "update"} {
		out, _ := captureHelp(t, group, "--help")
		doc, _ := findCommandDoc(group)
		if !strings.Contains(out, "하위 명령:") || len(doc.Subcommands) == 0 {
			t.Fatalf("%s help does not list its subcommands:\n%s", group, out)
		}
		for _, line := range doc.Subcommands {
			if !strings.Contains(out, strings.Fields(line)[0]) {
				t.Fatalf("%s help lacks %s", group, line)
			}
		}
	}
	out, _ := captureHelp(t, "status", "--help")
	for _, option := range []string{"옵션:", "-path", "-json", "-job"} {
		if !strings.Contains(out, option) {
			t.Fatalf("status help lacks %s:\n%s", option, out)
		}
	}
}

func TestUnknownCommandIsNamedAndPointsToHelp(t *testing.T) {
	for _, args := range [][]string{{"frobnicate"}, {"help", "frobnicate"}} {
		out, err := captureHelp(t, args...)
		if err == nil || !strings.Contains(err.Error(), "frobnicate") || !strings.Contains(err.Error(), "railwait help") || out != "" {
			t.Fatalf("%v: %v %q", args, err, out)
		}
	}
	_, err := captureHelp(t, "manager", "frobnicate")
	if err == nil || !strings.Contains(err.Error(), "frobnicate") || !strings.Contains(err.Error(), "railwait manager --help") {
		t.Fatal(err)
	}
}

func TestWrongOptionPointsToTheCommandHelp(t *testing.T) {
	out, err := captureHelp(t, "status", "--path", t.TempDir(), "--unknown-option")
	if err == nil || !strings.Contains(err.Error(), "railwait status --help") || out != "" {
		t.Fatalf("%v %q", err, out)
	}
}
