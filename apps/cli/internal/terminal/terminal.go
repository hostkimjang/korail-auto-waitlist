// Package terminal presents public installer messages. Credentials are read by
// the caller and must never be passed to this renderer.
package terminal

import (
	"fmt"
	"io"
	"strings"
	"sync"
	"time"
)

type Options struct {
	Interactive bool
	Color       bool
	Animate     bool
	Width       int
}

// UI keeps all writes serialized, including the progress animation. It never
// marks an earlier step as successful merely because another step has started.
type UI struct {
	mu      sync.Mutex
	writer  io.Writer
	options Options
	stop    chan struct{}
	wg      sync.WaitGroup
	close   sync.Once
	closed  bool
	title   string
	started time.Time
	active  bool
	paused  bool
	visible bool
	frame   int
}

func New(writer io.Writer, options Options) *UI {
	if writer == nil {
		writer = io.Discard
	}
	if options.Width < 20 {
		options.Width = 80
	}
	if !options.Interactive {
		options.Color, options.Animate = false, false
	}
	u := &UI{writer: writer, options: options, stop: make(chan struct{})}
	if options.Animate {
		u.wg.Add(1)
		go u.animate()
	}
	return u
}

func (u *UI) animate() {
	defer u.wg.Done()
	ticker := time.NewTicker(120 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-u.stop:
			return
		case <-ticker.C:
			u.mu.Lock()
			if u.active && !u.paused && !u.closed {
				u.frame++
				u.drawLocked()
			}
			u.mu.Unlock()
		}
	}
}

// Close joins the animation before returning. An unfinished step remains a
// progress message rather than being relabelled as a success or a failure.
func (u *UI) Close() {
	u.close.Do(func() {
		close(u.stop)
		u.wg.Wait()
		u.mu.Lock()
		defer u.mu.Unlock()
		u.pauseLocked()
		u.closed = true
	})
}

func (u *UI) Header(version string) {
	u.write(func() {
		u.lineLocked("", "")
		u.lineLocked("", u.style("1;36", "RAILWAIT")+"  "+u.style("1", "레일웨잇 설치"))
		if version != "" {
			u.lineLocked("  ", "CLI "+clean(version))
		}
		u.lineLocked("  ", "준비부터 접속까지, 한 단계씩 안내합니다.")
	})
}

func (u *UI) Section(title string) {
	u.write(func() {
		u.lineLocked("", "")
		u.lineLocked("", u.style("1", clean(title)))
	})
}

func (u *UI) KeyValue(label, value string) {
	u.write(func() { u.lineLocked("  ", clean(label)+"  "+clean(value)) })
}

func (u *UI) Info(message string) {
	u.write(func() { u.lineLocked("  ", clean(message)) })
}

func (u *UI) Hint(message string) {
	u.write(func() { u.lineLocked("  ", u.style("2", clean(message))) })
}

// Command lets the terminal wrap visually without inserting newlines inside a
// quoted path, so copying an actionable command preserves its arguments.
func (u *UI) Command(command string) {
	u.write(func() { fmt.Fprintln(u.writer, "  "+clean(command)) })
}

func (u *UI) Start(title string) {
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.closed {
		return
	}
	title = clean(title)
	if u.active && !u.paused && title == u.title {
		return
	}
	u.pauseLocked()
	u.title, u.started, u.active, u.paused = title, time.Now(), true, false
	u.frame = 0
	if u.options.Animate {
		u.drawLocked()
	} else {
		u.lineLocked("  ", "[진행] "+title)
	}
}

// Progress translates durable installer states into current activities. These
// states are not percentages and do not establish that autostart is ready.
func (u *UI) Progress(stage string) {
	titles := map[string]string{
		"created":         "설치 환경 확인",
		"fetching":        "설치 패키지 준비",
		"bundle_verified": "검증한 설치 파일 배치",
		"release_ready":   "앱 설정 준비",
		"configured":      "앱 이미지 준비",
		"downloaded":      "데이터 저장소 시작",
		"migrating":       "데이터베이스 초기화",
		"admin_required":  "관리자 계정 확인",
		"starting":        "앱 시작 및 정상 상태 확인",
		"complete":        "앱 설치 확인됨 · 자동 시작 준비",
	}
	title, ok := titles[stage]
	if !ok {
		title = stage
	}
	u.Start(title)
}

// Pause leaves a stable line before the caller starts reading input.
func (u *UI) Pause() {
	u.mu.Lock()
	defer u.mu.Unlock()
	if !u.closed {
		u.pauseLocked()
	}
}

// Prompt only renders the question. The caller owns reading and validating the
// answer and must write a newline after a hidden input without echo.
func (u *UI) Prompt(label, hint string) {
	u.write(func() {
		u.lineLocked("", "")
		u.lineLocked("  ", u.style("1", clean(label)))
		if hint != "" {
			u.lineLocked("  ", u.style("2", clean(hint)))
		}
		fmt.Fprint(u.writer, u.style("36", "  > "))
	})
}

// Done is explicit: only the caller, after verification, may announce success.
func (u *UI) Done(message string) {
	u.finish("[완료]", "32", message)
}

func (u *UI) Success(title string) {
	u.finish("[완료]", "1;32", title)
}

func (u *UI) Failure(title, detail, next string) {
	u.finish("[중단]", "1;33", title)
	if detail != "" {
		u.Info(detail)
	}
	if next != "" {
		u.Hint("다음 단계: " + next)
	}
}

func (u *UI) finish(marker, color, message string) {
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.closed {
		return
	}
	u.clearLocked()
	u.active, u.paused = false, false
	u.lineLocked("  ", u.style(color, marker)+" "+clean(message))
}

func (u *UI) write(action func()) {
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.closed {
		return
	}
	u.pauseLocked()
	action()
}

func (u *UI) pauseLocked() {
	if u.visible {
		u.clearLocked()
		elapsed := elapsedText(time.Since(u.started))
		u.lineLocked("  ", "[진행] "+u.title+" · "+elapsed)
	}
	u.paused = true
}

func (u *UI) clearLocked() {
	if u.visible {
		fmt.Fprint(u.writer, "\r\x1b[2K")
		u.visible = false
	}
}

func (u *UI) drawLocked() {
	frames := []string{"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}
	duration := time.Since(u.started)
	marker := "·"
	if duration >= 300*time.Millisecond {
		marker = frames[u.frame%len(frames)]
	}
	suffix := " · " + elapsedText(duration)
	// Reserve one terminal column so a redraw never wraps into the next line.
	available := u.options.Width - displayWidth(suffix) - 5
	line := "  " + u.style("36", marker) + " " + truncate(u.title, available) + suffix
	fmt.Fprint(u.writer, "\r\x1b[2K", line)
	u.visible = true
}

func (u *UI) style(code, value string) string {
	if !u.options.Color {
		return value
	}
	return "\x1b[" + code + "m" + value + "\x1b[0m"
}

func (u *UI) lineLocked(prefix, text string) {
	// Styling is applied by this package, after public strings are sanitized.
	// Wrapping uses visible width and keeps ANSI sequences intact.
	for _, line := range wrapStyled(text, u.options.Width-displayWidth(prefix)-1) {
		fmt.Fprintln(u.writer, prefix+line)
	}
}

func elapsedText(duration time.Duration) string {
	if duration < 0 {
		duration = 0
	}
	seconds := int(duration / time.Second)
	if seconds < 60 {
		return fmt.Sprintf("%d초", seconds)
	}
	return fmt.Sprintf("%d분 %02d초", seconds/60, seconds%60)
}

// Plain returns settings for stable append-only text, useful for log capture or
// screen readers even when the output is attached to an interactive terminal.
func Plain(width int) Options { return Options{Width: width} }

// Newline terminates a hidden prompt. It does not accept or echo its value.
func (u *UI) Newline() {
	u.write(func() { fmt.Fprintln(u.writer) })
}

// Divider is intentionally short: wide boxes misalign in CJK and small shells.
func (u *UI) Divider() {
	u.write(func() {
		width := u.options.Width - 3
		if width > 44 {
			width = 44
		}
		u.lineLocked("  ", u.style("2", strings.Repeat("─", width)))
	})
}
