package platform

import (
	"bytes"
	"encoding/binary"
	"encoding/xml"
	"io"
	"strings"
	"testing"
	"unicode/utf16"
)

func TestServiceDefinitionsPreservePathsAndPrivilege(t *testing.T) {
	cases := []struct{ os, home, user, exe, root string }{
		{"linux", "/home/a", "1000", "/home/a/My Apps/railwait", "/home/a/App Data/50% $rail"},
		{"darwin", "/Users/a", "501", "/Users/a/My Apps/railwait", "/Users/a/App Data/a & b"},
		{"windows", `C:\Users\a`, "S-1-5-21-123", `C:\My Apps\railwait.exe`, `C:\App Data\a & b\`},
	}
	for _, tt := range cases {
		t.Run(tt.os, func(t *testing.T) {
			plan, err := RenderService(tt.os, tt.home, tt.user, tt.exe, tt.root)
			if err != nil {
				t.Fatal(err)
			}
			if len(plan.Register) == 0 || len(plan.Content) == 0 || plan.Availability == "" {
				t.Fatal("incomplete plan")
			}
			data := string(plan.Content)
			if tt.os == "windows" {
				data = decodeUTF16Document(t, plan.Content)
			}
			if strings.Contains(data, "sudo") || strings.Contains(data, "HighestAvailable") {
				t.Fatal("unexpected privilege escalation")
			}
			if tt.os == "linux" {
				if !strings.Contains(data, `"/home/a/App Data/50%% $$rail"`) {
					t.Fatal("systemd interpolation not escaped")
				}
			} else {
				var document anyXML
				if err := unmarshalDocument(data, &document); err != nil {
					t.Fatal(err)
				}
				if !strings.Contains(data, "&amp;") {
					t.Fatal("XML path not escaped")
				}
			}
		})
	}
}

type anyXML struct {
	XMLName xml.Name
	Content string `xml:",innerxml"`
}

type windowsTask struct {
	Exec struct {
		Command          string
		Arguments        string
		WorkingDirectory string
	} `xml:"Actions>Exec"`
	LogonUser string `xml:"Triggers>LogonTrigger>UserId"`
	RunLevel  string `xml:"Principals>Principal>RunLevel"`
}

// decodeUTF16Document requires the byte order mark schtasks needs and returns the text.
func decodeUTF16Document(t *testing.T, content []byte) string {
	t.Helper()
	if len(content) < 2 || len(content)%2 != 0 || content[0] != 0xFF || content[1] != 0xFE {
		t.Fatalf("Windows task definition is not UTF-16LE with a byte order mark: % x", content[:min(len(content), 8)])
	}
	units := make([]uint16, (len(content)-2)/2)
	for n := range units {
		units[n] = binary.LittleEndian.Uint16(content[2+2*n:])
	}
	return string(utf16.Decode(units))
}

// unmarshalDocument parses already decoded text whose declaration may name UTF-16.
func unmarshalDocument(text string, v any) error {
	decoder := xml.NewDecoder(strings.NewReader(text))
	decoder.CharsetReader = func(_ string, input io.Reader) (io.Reader, error) { return input, nil }
	return decoder.Decode(v)
}

func TestWindowsTaskIsUTF16AndRunsManagerWithoutWindow(t *testing.T) {
	exe := `C:\Users\a\AppData\Local\Railwait\.railwait\bin\railwait.exe`
	root := `C:\Users\a\AppData\Local\Railwait`
	plan, err := RenderService("windows", `C:\Users\a`, "S-1-5-21-123", exe, root)
	if err != nil {
		t.Fatal(err)
	}
	text := decodeUTF16Document(t, plan.Content)
	if !strings.HasPrefix(text, `<?xml version="1.0" encoding="UTF-16"?>`) {
		t.Fatal("declaration must match the UTF-16 bytes", text[:60])
	}
	var task windowsTask
	if err := unmarshalDocument(text, &task); err != nil {
		t.Fatal(err)
	}
	if task.Exec.Command != `%SystemRoot%\System32\conhost.exe` {
		t.Fatal("manager must run under headless conhost", task.Exec.Command)
	}
	if want := `--headless "` + exe + `" manager serve --path "` + root + `"`; task.Exec.Arguments != want {
		t.Fatalf("hosted command changed: got %q want %q", task.Exec.Arguments, want)
	}
	if task.Exec.WorkingDirectory != root || task.LogonUser != "S-1-5-21-123" || task.RunLevel != "LeastPrivilege" {
		t.Fatalf("task identity or directory changed: %+v", task)
	}
}

// 1.4.5 wrote exactly these bytes. A new CLI may replace only this rendering.
func TestWindowsLegacyDefinitionMatchesReleasedRendering(t *testing.T) {
	exe := `C:\Users\a\AppData\Local\Railwait\.railwait\bin\railwait.exe`
	root := `C:\Users\a\AppData\Local\Railwait`
	plan, err := RenderService("windows", `C:\Users\a`, "S-1-5-21-123", exe, root)
	if err != nil {
		t.Fatal(err)
	}
	released := "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<Task version=\"1.2\" xmlns=\"http://schemas.microsoft.com/windows/2004/02/mit/task\"><Triggers><LogonTrigger><Enabled>true</Enabled><UserId>S-1-5-21-123</UserId></LogonTrigger></Triggers><Principals><Principal id=\"Author\"><UserId>S-1-5-21-123</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals><Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure></Settings><Actions Context=\"Author\"><Exec><Command>C:\\Users\\a\\AppData\\Local\\Railwait\\.railwait\\bin\\railwait.exe</Command><Arguments>manager serve --path &#34;C:\\Users\\a\\AppData\\Local\\Railwait&#34;</Arguments><WorkingDirectory>C:\\Users\\a\\AppData\\Local\\Railwait</WorkingDirectory></Exec></Actions></Task>\n"
	if len(plan.Legacy) != 1 || !bytes.Equal(plan.Legacy[0], []byte(released)) {
		t.Fatalf("legacy rendering no longer matches 1.4.5:\n%s", plan.Legacy)
	}
	if bytes.Equal(plan.Content, plan.Legacy[0]) {
		t.Fatal("new definition must differ from the rejected one")
	}
	for _, goos := range []string{"linux", "darwin"} {
		other, err := RenderService(goos, "/home/a", "501", "/opt/railwait", "/srv/railwait")
		if err != nil {
			t.Fatal(err)
		}
		if len(other.Legacy) != 0 {
			t.Fatal("only the Windows definition has a replaceable legacy form", goos)
		}
	}
}

func TestServiceRejectsRelativeAndInjectedPaths(t *testing.T) {
	for _, root := range []string{"relative", "/data\nExecStart=/bad", "/data\x00bad"} {
		if _, err := RenderService("linux", "/home/a", "1", "/usr/bin/railwait", root); err == nil {
			t.Fatal("invalid path accepted")
		}
	}
	if _, err := RenderService("windows", `C:\Users\a`, "S-1-5-1", `C:\railwait.exe`, `C:relative`); err == nil {
		t.Fatal("drive relative path accepted")
	}
}

func TestWindowsArgumentsHandleTrailingBackslash(t *testing.T) {
	if got := windowsQuote(`C:\App Data\`); got != `"C:\App Data\\"` {
		t.Fatalf("invalid quoting: %s", got)
	}
}
