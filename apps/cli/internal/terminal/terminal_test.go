package terminal

import (
	"bytes"
	"os"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestRedirectedOutputIsStableAndDoesNotInventCompletedSteps(t *testing.T) {
	var output bytes.Buffer
	u := New(&output, Options{Color: true, Animate: true, Width: 80})
	u.Header("0.1.0-test")
	u.Progress("starting") // Resuming here does not prove earlier steps ran now.
	u.Progress("starting")
	u.Progress("complete")
	u.Prompt("관리자 비밀번호", "입력한 문자는 표시되지 않습니다.")
	u.Newline()
	u.Failure("자동 시작 등록을 마치지 못했습니다", "앱은 실행 중입니다.", "관리 서비스를 다시 확인하세요.")
	u.Close()
	text := output.String()
	if strings.ContainsAny(text, "\x1b\r") {
		t.Fatal("redirected output contains cursor commands")
	}
	if strings.Count(text, "[진행] 앱 시작 및 정상 상태 확인") != 1 {
		t.Fatal("repeated state emitted duplicate log entries")
	}
	if strings.Contains(text, "[완료]") || strings.Contains(text, "설치 환경 확인") {
		t.Fatal("resume or durable state was represented as a verified success")
	}
	if !strings.Contains(text, "[중단]") || !strings.Contains(text, "다음 단계:") {
		t.Fatal("failure recovery guidance is missing")
	}
}

func TestLongRecoveryCommandRemainsCopyableAtNarrowWidths(t *testing.T) {
	var output bytes.Buffer
	u := New(&output, Plain(40))
	command := `railwait status --path "/Users/example/Library/Application Support/Railwait"`
	u.Command(command)
	u.Close()
	if !strings.Contains(output.String(), command+"\n") {
		t.Fatal("terminal formatting split a quoted command argument")
	}
}

func TestControlSequencesAndBidiCannotEscapePublicValues(t *testing.T) {
	var output bytes.Buffer
	u := New(&output, Plain(80))
	u.KeyValue("경로", "safe\x1b[2J/path\x1b]52;c;clipboard-secret\a\u202e.end\nnew-line")
	u.Info("hidden\x1b]0;window-title\x1b\\ shown\x00")
	u.Close()
	text := output.String()
	if strings.ContainsAny(text, "\x1b\u202e\x00\a\r") || strings.Contains(text, "clipboard-secret") || strings.Contains(text, "window-title") {
		t.Fatal("untrusted terminal commands or direction controls reached output")
	}
	if !strings.Contains(text, "safe/path.end new-line") || !strings.Contains(text, "hidden shown") {
		t.Fatal("safe visible values were lost")
	}
}

func TestKoreanNarrowLayoutAndAnimatedLineStayWithinWidth(t *testing.T) {
	for _, color := range []bool{false, true} {
		var output bytes.Buffer
		u := New(&output, Options{Interactive: true, Color: color, Animate: true, Width: 40})
		u.Header("0.1.0-local")
		u.KeyValue("설치 경로", "/사용자/긴 폴더 이름/레일웨잇 설치/하위 경로")
		u.Start("데이터베이스 마이그레이션과 서비스 준비 상태를 확인하는 중")
		u.Pause()
		u.Done("서비스 정상 상태를 확인했습니다")
		u.Close()
		for _, line := range strings.FieldsFunc(output.String(), func(r rune) bool { return r == '\n' || r == '\r' }) {
			if width := displayWidth(line); width >= 40 {
				t.Fatalf("line wraps into the next terminal row: width=%d line=%q", width, line)
			}
		}
	}
}

func TestPromptFreezesAnimationAndCloseJoinsItsGoroutine(t *testing.T) {
	var output bytes.Buffer
	u := New(&output, Options{Interactive: true, Color: true, Animate: true, Width: 80})
	u.Start("Docker 준비")
	u.Prompt("계정 입력", "입력값은 기록하지 않습니다.")
	frozen := output.String()
	time.Sleep(150 * time.Millisecond)
	if output.String() != frozen {
		t.Fatal("animation wrote over an active input prompt")
	}
	u.Close()
	afterClose := output.String()
	u.Start("closed")
	u.Info("closed")
	u.Close()
	if output.String() != afterClose {
		t.Fatal("closed renderer wrote additional output")
	}
}

func TestConcurrentProgressAndCloseDoNotRace(t *testing.T) {
	var output bytes.Buffer
	u := New(&output, Options{Interactive: true, Animate: true, Width: 80})
	var workers sync.WaitGroup
	for n := 0; n < 5; n++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for j := 0; j < 30; j++ {
				u.Start("상태 확인")
				u.Info("안내")
				u.Pause()
			}
		}()
	}
	workers.Add(1)
	go func() { defer workers.Done(); u.Close() }()
	workers.Wait()
	u.Close()
}

func TestDetectedPipeUsesPlainModeEvenWithColorEnvironment(t *testing.T) {
	read, write, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer read.Close()
	defer write.Close()
	t.Setenv("TERM", "xterm-256color")
	got := Detect(write)
	if got.Interactive || got.Color || got.Animate {
		t.Fatal("a redirected stream was treated as a terminal")
	}
	if got = Detect(nil); got.Interactive || got.Animate || got.Color {
		t.Fatal("missing output was treated as a terminal")
	}
}

func TestPlainPreferenceAndUnsupportedConsoleDisableColorAndAnimation(t *testing.T) {
	for _, values := range []map[string]string{
		{"NO_COLOR": "1"}, {"NO_COLOR": ""}, {"TERM": "dumb"}, {"RAILWAIT_PLAIN": "1"},
	} {
		options := terminalOptions(40, true, func(key string) (string, bool) { value, ok := values[key]; return value, ok })
		if options.Color || options.Animate || options.Width != 40 || !options.Interactive {
			t.Fatal("plain preference changed input capability or enabled cursor output")
		}
	}
	lookup := func(string) (string, bool) { return "", false }
	if options := terminalOptions(80, false, lookup); options.Color || options.Animate {
		t.Fatal("unsupported console was given ANSI output")
	}
	if options := terminalOptions(80, true, lookup); !options.Color || !options.Animate {
		t.Fatal("capable interactive console did not enable presentation")
	}
}

func TestElapsedTimeAndMixedCharacterWidth(t *testing.T) {
	if elapsedText(65*time.Second) != "1분 05초" || elapsedText(-time.Second) != "0초" {
		t.Fatal("elapsed time representation differs")
	}
	if displayWidth("  한글 e\u0301") != 8 {
		t.Fatal("CJK, combining characters, or indentation counted incorrectly")
	}
	if got := truncate("한글 경로", 6); got != "한글 …" {
		t.Fatal(got)
	}
}
