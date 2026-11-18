package terminal

import (
	"os"
	"strings"

	"golang.org/x/term"
)

// Detect is conservative: unsupported consoles get stable text, including
// NO_COLOR users. This also avoids cursor rewrites in screen-reader log views.
func Detect(file *os.File) Options {
	if file == nil || !term.IsTerminal(int(file.Fd())) {
		return Options{Width: 80}
	}
	width := 80
	if width, _, err := term.GetSize(int(file.Fd())); err == nil && width >= 20 {
		return terminalOptions(width, supportsANSI(file), os.LookupEnv)
	}
	return terminalOptions(width, supportsANSI(file), os.LookupEnv)
}

func terminalOptions(width int, ansi bool, lookup func(string) (string, bool)) Options {
	options := Options{Interactive: true, Width: width}
	_, noColor := lookup("NO_COLOR")
	plain, _ := lookup("RAILWAIT_PLAIN")
	termName, _ := lookup("TERM")
	if !noColor && plain == "" && !strings.EqualFold(termName, "dumb") && ansi {
		options.Color, options.Animate = true, true
	}
	return options
}
