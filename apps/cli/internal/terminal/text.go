package terminal

import (
	"strings"
	"unicode"
)

// clean drops terminal commands and direction-changing controls from values
// such as paths and error text. UI-owned SGR sequences are added only afterward.
func clean(value string) string {
	return strings.TrimSpace(visible(value))
}

func visible(value string) string {
	runes := []rune(value)
	var out strings.Builder
	for n := 0; n < len(runes); n++ {
		r := runes[n]
		if r == '\x1b' {
			if n+1 < len(runes) && runes[n+1] == '[' {
				n += 2
				for n < len(runes) && (runes[n] < 0x40 || runes[n] > 0x7e) {
					n++
				}
			} else if n+1 < len(runes) && runes[n+1] == ']' {
				n += 2
				for n < len(runes) && runes[n] != '\a' {
					if runes[n] == '\x1b' && n+1 < len(runes) && runes[n+1] == '\\' {
						n++
						break
					}
					n++
				}
			}
			continue
		}
		if unicode.IsControl(r) || unicode.Is(unicode.Cf, r) {
			if r == '\n' || r == '\r' || r == '\t' {
				out.WriteByte(' ')
			}
			continue
		}
		out.WriteRune(r)
	}
	return out.String()
}

func runeWidth(r rune) int {
	if unicode.Is(unicode.Mn, r) || unicode.Is(unicode.Me, r) {
		return 0
	}
	if r >= 0x1100 && (r <= 0x115f || r == 0x2329 || r == 0x232a ||
		r >= 0x2e80 && r <= 0xa4cf && r != 0x303f ||
		r >= 0xac00 && r <= 0xd7a3 || r >= 0xf900 && r <= 0xfaff ||
		r >= 0xfe10 && r <= 0xfe19 || r >= 0xfe30 && r <= 0xfe6f ||
		r >= 0xff00 && r <= 0xff60 || r >= 0xffe0 && r <= 0xffe6 ||
		r >= 0x1f300 && r <= 0x1faff || r >= 0x20000 && r <= 0x3fffd) {
		return 2
	}
	return 1
}

func displayWidth(value string) int {
	width := 0
	for _, r := range visible(value) {
		width += runeWidth(r)
	}
	return width
}

func truncate(value string, width int) string {
	if width <= 0 {
		return ""
	}
	if displayWidth(value) <= width {
		return value
	}
	var out strings.Builder
	used := 0
	for _, r := range value {
		if used+runeWidth(r) > width-1 {
			break
		}
		out.WriteRune(r)
		used += runeWidth(r)
	}
	return out.String() + "…"
}

func wrapStyled(value string, width int) []string {
	if width < 1 {
		width = 1
	}
	var lines []string
	var line strings.Builder
	used := 0
	runes := []rune(value)
	for n := 0; n < len(runes); n++ {
		r := runes[n]
		if r == '\x1b' && n+1 < len(runes) && runes[n+1] == '[' {
			line.WriteRune(r)
			n++
			for ; n < len(runes); n++ {
				line.WriteRune(runes[n])
				if runes[n] >= 0x40 && runes[n] <= 0x7e && runes[n] != '[' {
					break
				}
			}
			continue
		}
		if used+runeWidth(r) > width {
			lines = append(lines, line.String())
			line.Reset()
			used = 0
		}
		line.WriteRune(r)
		used += runeWidth(r)
	}
	return append(lines, line.String())
}
