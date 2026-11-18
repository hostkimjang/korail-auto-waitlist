package main

import (
	"bytes"
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/terminal"
)

func scriptedSecret(value string, err error) secretReader {
	return func(context.Context, *terminal.UI, string, string) (string, error) { return value, err }
}

func TestOptionalSettingsDefaultToNoTAGOKeyAndAutomaticReservationWithoutAsking(t *testing.T) {
	var output bytes.Buffer
	ui := terminal.New(&output, terminal.Plain(80))
	settings, err := optionalSettingsInputWith(context.Background(), ui, scriptedSecret("", nil), scriptedLines(), false)
	if err != nil {
		t.Fatal(err)
	}
	if settings != (optionalSettings{AutomaticReservation: true}) {
		t.Fatalf("defaults changed: %+v", settings)
	}
	for _, want := range []string{"TAGO 일반 인증키(Decoding)", "결제 직전까지 예매", "결제는 하지 않습니다"} {
		if !strings.Contains(output.String(), want) {
			t.Fatalf("missing %q in\n%s", want, output.String())
		}
	}
	if strings.Contains(output.String(), "자동 예매 [1]") {
		t.Fatal("simple install asked about automatic reservation")
	}
}

func TestCustomInstallKeepsAutomaticReservationOnEnterAndTurnsItOffOnRequest(t *testing.T) {
	for _, tc := range []struct {
		lines []string
		want  bool
	}{{[]string{""}, true}, {[]string{"1"}, true}, {[]string{"2"}, false}} {
		ui := terminal.New(&bytes.Buffer{}, terminal.Plain(80))
		settings, err := optionalSettingsInputWith(context.Background(), ui, scriptedSecret("", nil), scriptedLines(tc.lines...), true)
		if err != nil || settings.AutomaticReservation != tc.want {
			t.Fatalf("answer %q: settings=%+v err=%v", tc.lines, settings, err)
		}
	}
}

func TestOptionalSettingsKeepTheTAGOKeyHiddenAndAskAgainOnInvalidChoice(t *testing.T) {
	var output bytes.Buffer
	ui := terminal.New(&output, terminal.Plain(80))
	settings, err := optionalSettingsInputWith(context.Background(), ui, scriptedSecret("  tago-fixture-key \t", nil), scriptedLines("3", "2"), true)
	if err != nil {
		t.Fatal(err)
	}
	if settings.TAGOKey != "tago-fixture-key" || settings.AutomaticReservation {
		t.Fatalf("settings=%+v", settings)
	}
	if strings.Count(output.String(), "자동 예매 [1]") != 2 {
		t.Fatal("invalid choice was not asked again")
	}
	if strings.Contains(output.String(), "tago-fixture-key") {
		t.Fatal("TAGO key echoed")
	}
}

func TestOptionalSettingsStopOnCancelledInput(t *testing.T) {
	ui := terminal.New(&bytes.Buffer{}, terminal.Plain(80))
	if _, err := optionalSettingsInputWith(context.Background(), ui, scriptedSecret("", context.Canceled), scriptedLines("1"), true); !errors.Is(err, context.Canceled) {
		t.Fatalf("TAGO cancel: %v", err)
	}
	if _, err := optionalSettingsInputWith(context.Background(), ui, scriptedSecret("", nil), scriptedLines(), true); err == nil {
		t.Fatal("ended choice input accepted as a default")
	}
}

func TestOptionalSettingCommandsListOnlySettingsStillAtDefaults(t *testing.T) {
	for _, tc := range []struct {
		env        string
		tago       bool
		reserveCmd string
	}{
		{"TAGO_SERVICE_KEY=''\nKORAIL_RESERVATION_ONCE_ENABLED='false'\nSRT_RESERVATION_ONCE_ENABLED='false'\n", true, "'reservation' 'on'"},
		{"TAGO_SERVICE_KEY='set'\nKORAIL_RESERVATION_ONCE_ENABLED='true'\nSRT_RESERVATION_ONCE_ENABLED='false'\n", false, "'reservation' 'on'"},
		{"TAGO_SERVICE_KEY='set'\nKORAIL_RESERVATION_ONCE_ENABLED='true'\nSRT_RESERVATION_ONCE_ENABLED='true'\n", false, "'reservation' 'off'"},
	} {
		var output bytes.Buffer
		ui := terminal.New(&output, terminal.Plain(200))
		showOptionalSettingCommands(ui, "/srv/railwait", []byte(tc.env))
		text := output.String()
		if strings.Contains(text, "TAGO 키 넣기") != tc.tago || !strings.Contains(text, tc.reserveCmd) || strings.Count(text, "'reservation'") != 1 {
			t.Fatalf("env %q gave\n%s", tc.env, text)
		}
		if strings.Contains(text, "'set'") {
			t.Fatal("printed a setting value")
		}
	}
}
