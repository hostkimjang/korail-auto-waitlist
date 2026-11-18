package setup

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

type windowsInstallFixture struct {
	commands   []windowsInstallerCommand
	signature  func(windowsInstallerCommand) []byte
	probes     []windowsWSLProbe
	changeExit int
	install    []byte
	onInstall  func(context.Context)
}

func (f *windowsInstallFixture) run(ctx context.Context, command windowsInstallerCommand) ([]byte, error) {
	f.commands = append(f.commands, command)
	switch command.Purpose {
	case "signature":
		if f.signature != nil {
			return f.signature(command), nil
		}
		return json.Marshal(map[string]string{"status": "Valid", "publisher": command.Variables["RAILWAIT_INSTALLER_PUBLISHER"], "sha256": command.Variables["RAILWAIT_INSTALLER_SHA256"]})
	case "wsl_probe":
		value := windowsWSLProbe{Available: true, Version: "2.6.1.0"}
		if len(f.probes) > 0 {
			value, f.probes = f.probes[0], f.probes[1:]
		}
		return json.Marshal(value)
	case "wsl_install", "wsl_update":
		return json.Marshal(windowsProcessResult{Verified: true, ExitCode: f.changeExit})
	case "install":
		if f.onInstall != nil {
			f.onInstall(ctx)
		}
		if f.install != nil {
			return f.install, nil
		}
		return json.Marshal(windowsProcessResult{Verified: true})
	default:
		return nil, errors.New("unexpected Windows command")
	}
}

func windowsFixtureFile(t *testing.T, tool string) (InstallPlan, string) {
	t.Helper()
	plan, err := DependencyPlan(tool, "windows", "arm64")
	if err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(t.TempDir(), plan.Filename)
	if err := os.WriteFile(file, []byte("public installer verification fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	return plan, file
}

func TestWindowsPublisherSignatureAndHashMustPassBeforeAnyInstall(t *testing.T) {
	for _, change := range []string{"unsigned", "wrong_publisher", "wrong_hash", "trailing_output", "unknown_field"} {
		t.Run(change, func(t *testing.T) {
			plan, file := windowsFixtureFile(t, "docker")
			fixture := &windowsInstallFixture{signature: func(command windowsInstallerCommand) []byte {
				value := map[string]string{"status": "Valid", "publisher": plan.Publisher, "sha256": command.Variables["RAILWAIT_INSTALLER_SHA256"]}
				switch change {
				case "unsigned":
					value["status"] = "NotSigned"
				case "wrong_publisher":
					value["publisher"] = "Docker Inc impostor"
				case "wrong_hash":
					value["sha256"] = strings.Repeat("0", 64)
				case "unknown_field":
					value["untrusted"] = "ok"
				}
				data, _ := json.Marshal(value)
				if change == "trailing_output" {
					data = append(data, []byte(" {}")...)
				}
				return data
			}}
			result, err := installWindowsWith(context.Background(), plan, file, `C:\Windows\System32`, nil, fixture.run)
			if err == nil || result.Installed || len(fixture.commands) != 1 {
				t.Fatal("unverified artifact reached WSL or installer execution")
			}
		})
	}
}

func TestWindowsDockerReusesReadyWSLAndWaitsForNativeInstaller(t *testing.T) {
	plan, file := windowsFixtureFile(t, "docker")
	fixture := &windowsInstallFixture{}
	result, err := installWindowsWith(context.Background(), plan, file, `C:\Windows\System32`, nil, fixture.run)
	if err != nil || !result.Installed || result.RebootRequired {
		t.Fatal(result, err)
	}
	if got := windowsPurposes(fixture.commands); !reflect.DeepEqual(got, []string{"signature", "wsl_probe", "install"}) {
		t.Fatal(got)
	}
	for _, command := range fixture.commands {
		if command.Program != `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe` || strings.Contains(command.Script, file) {
			t.Fatal("system executable or literal path isolation differs")
		}
	}
}

func TestWindowsDockerPreparesMissingOrOldWSLBeforeInstaller(t *testing.T) {
	for _, test := range []struct {
		name   string
		before windowsWSLProbe
		action string
	}{
		{"inbox", windowsWSLProbe{Available: true, VersionExit: 1, StatusExit: 1}, "install"},
		{"outdated", windowsWSLProbe{Available: true, Version: "2.0.9.0"}, "update"},
		{"features_unready", windowsWSLProbe{Available: true, Version: "2.6.1", StatusExit: 1}, "install"},
	} {
		t.Run(test.name, func(t *testing.T) {
			plan, file := windowsFixtureFile(t, "docker")
			fixture := &windowsInstallFixture{probes: []windowsWSLProbe{test.before, {Available: true, Version: "2.6.1"}}}
			result, err := installWindowsWith(context.Background(), plan, file, `C:\Windows\System32`, nil, fixture.run)
			if err != nil || !result.Installed {
				t.Fatal(result, err)
			}
			if got := windowsPurposes(fixture.commands); !reflect.DeepEqual(got, []string{"signature", "wsl_probe", "wsl_" + test.action, "wsl_probe", "install"}) {
				t.Fatal(got)
			}
			if fixture.commands[2].Variables["RAILWAIT_WSL_ACTION"] != test.action {
				t.Fatal("WSL preparation action differs")
			}
		})
	}
}

func TestWindowsWSLRestartOrUnreadyStateStopsBeforeDockerInstall(t *testing.T) {
	for _, test := range []struct {
		name   string
		exit   int
		after  windowsWSLProbe
		reboot bool
		error  bool
	}{
		{"restart_requested", 3010, windowsWSLProbe{}, true, false},
		{"restart_reported", 1641, windowsWSLProbe{}, true, false},
		{"restart_pending", 0, windowsWSLProbe{Available: true, Version: "2.6.1", RebootPending: true}, true, false},
		{"still_unready", 0, windowsWSLProbe{Available: true, Version: "2.6.1", StatusExit: 1}, false, true},
		{"cancelled", 1223, windowsWSLProbe{}, false, true},
	} {
		t.Run(test.name, func(t *testing.T) {
			plan, file := windowsFixtureFile(t, "docker")
			fixture := &windowsInstallFixture{changeExit: test.exit, probes: []windowsWSLProbe{{Available: true, VersionExit: 1, StatusExit: 1}, test.after}}
			result, err := installWindowsWith(context.Background(), plan, file, `C:\Windows\System32`, nil, fixture.run)
			if (err != nil) != test.error || result.RebootRequired != test.reboot || result.Installed || !result.NeedsUserAction {
				t.Fatal(result, err)
			}
			for _, command := range fixture.commands {
				if command.Purpose == "install" {
					t.Fatal("Docker installer ran before WSL was ready")
				}
			}
		})
	}
}

func TestWindowsNativeExitDoesNotImplyDaemonReadyAndRejectsMissingStatus(t *testing.T) {
	for _, test := range []struct {
		name      string
		output    string
		installed bool
		reboot    bool
		failed    bool
	}{
		{"success", `{"exit_code":0,"verified":true}`, true, false, false},
		{"restart", `{"exit_code":3010,"verified":true}`, true, true, false},
		{"cancelled", `{"exit_code":1602,"verified":true}`, false, false, true},
		{"failure", `{"exit_code":1603,"verified":true}`, false, false, true},
		{"missing_code", `{"verified":true}`, false, false, true},
		{"not_verified", `{"exit_code":0,"verified":false}`, false, false, true},
	} {
		t.Run(test.name, func(t *testing.T) {
			plan, file := windowsFixtureFile(t, "tailscale")
			fixture := &windowsInstallFixture{install: []byte(test.output)}
			result, err := installWindowsWith(context.Background(), plan, file, `C:\Windows\System32`, nil, fixture.run)
			if (err != nil) != test.failed || result.Installed != test.installed || result.RebootRequired != test.reboot {
				t.Fatal(result, err)
			}
			if got := windowsPurposes(fixture.commands); !reflect.DeepEqual(got, []string{"signature", "install"}) {
				t.Fatal("Tailscale installation touched WSL", got)
			}
		})
	}
}

func TestWindowsOSInstallerWaitIsNotKilledByCLIInterrupt(t *testing.T) {
	plan, file := windowsFixtureFile(t, "tailscale")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	fixture := &windowsInstallFixture{onInstall: func(waitCtx context.Context) {
		cancel()
		if waitCtx.Err() != nil || waitCtx.Done() != nil {
			t.Fatal("CLI interrupt can kill only the waiter and orphan an elevated installer")
		}
	}}
	result, err := installWindowsWith(ctx, plan, file, `C:\Windows\System32`, nil, fixture.run)
	if !errors.Is(err, context.Canceled) || !result.Installed || !result.NeedsUserAction {
		t.Fatal("completed native installer and cancelled CLI were conflated", result, err)
	}
}

func TestCancelledWindowsProbeNeverStartsSystemCommand(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := runWindowsInstallerCommand(ctx, windowsInstallerCommand{Program: "must-not-run"})
	if !errors.Is(err, context.Canceled) {
		t.Fatal("cancelled probe attempted a process or lost the cancellation", err)
	}
}

func TestWindowsScriptsKeepConsentAndUseOnlySupportedInstallCommands(t *testing.T) {
	for _, script := range []string{windowsSignatureScript, windowsInstallScript, windowsWSLProbeScript, windowsWSLChangeScript} {
		for _, forbidden := range []string{"ExecutionPolicy", "Bypass", "--accept-license", "/quiet", "Restart-Computer", "shutdown.exe", "--reboot"} {
			if strings.Contains(script, forbidden) {
				t.Fatal("unexpected automatic consent or reboot:", forbidden)
			}
		}
	}
	for _, required := range []string{"Get-AuthenticodeSignature", "Get-FileHash", "-cne 'Valid'", "-Wait -PassThru", "'/norestart'", "-Verb RunAs", "'--user'"} {
		if !strings.Contains(windowsInstallScript, required) {
			t.Fatal("missing installer boundary:", required)
		}
	}
	if !strings.Contains(windowsWSLChangeScript, "'--no-distribution'") || !strings.Contains(windowsWSLChangeScript, "'--update'") {
		t.Fatal("WSL preparation installs an unwanted Linux distribution or omits update")
	}
	for _, version := range []string{"2.1.5", "2.6.1.0", "3.0.0"} {
		if !wslVersionReady(version) {
			t.Fatal(version)
		}
	}
	for _, version := range []string{"", "2.1.4", "1.9.99", "2.1", "3.x.0", "-1.4.5"} {
		if wslVersionReady(version) {
			t.Fatal(version)
		}
	}
}

func windowsPurposes(commands []windowsInstallerCommand) []string {
	var result []string
	for _, command := range commands {
		result = append(result, command.Purpose)
	}
	return result
}
