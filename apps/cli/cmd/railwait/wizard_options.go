package main

import (
	"context"
	"strings"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/install"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/terminal"
)

type optionalSettings struct {
	TAGOKey              string
	AutomaticReservation bool
}

type secretReader func(ctx context.Context, ui *terminal.UI, label, hint string) (string, error)

// optionalSettingsInputWith asks every new interactive install for the optional TAGO
// key. Automatic reservation is on by default; only a --custom install is asked
// whether to turn it off. Each watch still chooses automatic reservation on its own.
func optionalSettingsInputWith(ctx context.Context, ui *terminal.UI, readSecret secretReader, read func(context.Context) (string, error), askReservation bool) (optionalSettings, error) {
	settings := optionalSettings{AutomaticReservation: true}
	ui.Section("시간표 검색 키 (선택)")
	ui.Info("공공데이터포털에서 발급받은 TAGO 일반 인증키(Decoding)를 넣으면 최신 역 목록을 받고, 공식 시간표 조회가 안 될 때 TAGO 시간표로 대신합니다. 키가 없어도 앱은 동작합니다.")
	key, err := readSecret(ctx, ui, "TAGO 인증키 [Enter: 건너뜀]: ", "Enter를 누르면 나중에 넣습니다.")
	if err != nil {
		return settings, err
	}
	settings.TAGOKey = strings.TrimSpace(key)
	ui.Section("자동 예매")
	ui.Info("새 대기에서 '좌석 재발견마다 자동 예매'를 고른 대기만, 좌석을 찾으면 저장한 철도 계정으로 결제 직전까지 예매합니다. 결제는 하지 않습니다.")
	if !askReservation {
		return settings, nil
	}
	for {
		ui.Prompt("자동 예매 [1]: ", "1 켜기  ·  2 끄기 · 좌석을 찾으면 알림만")
		value, err := read(ctx)
		if err != nil {
			return settings, err
		}
		switch strings.TrimSpace(value) {
		case "", "1":
			return settings, nil
		case "2":
			settings.AutomaticReservation = false
			return settings, nil
		}
		ui.Info("표시된 번호를 입력해 주세요.")
	}
}

// showOptionalSettingCommands names the commands that change the optional settings.
// configure needs the host manager, which runs only once the install finishes.
func showOptionalSettingCommands(ui *terminal.UI, path string, env []byte) {
	values, err := install.ParseEnv(string(env))
	if err != nil {
		return
	}
	if values["TAGO_SERVICE_KEY"] == "" {
		ui.Hint("TAGO 키 넣기 (선택)")
		showWizardCommand(ui, "configure", "--path", path)
	}
	if install.AutomaticReservationEnabled(values) {
		ui.Hint("자동 예매 끄기 (선택)")
		showWizardCommand(ui, "configure", "reservation", "off", "--path", path)
	} else {
		ui.Hint("자동 예매 켜기 (선택)")
		showWizardCommand(ui, "configure", "reservation", "on", "--path", path)
	}
}
