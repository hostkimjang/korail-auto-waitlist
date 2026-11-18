package platform

import (
	"encoding/xml"
	"strings"
	"testing"
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
			if strings.Contains(data, "sudo") || strings.Contains(data, "HighestAvailable") {
				t.Fatal("unexpected privilege escalation")
			}
			if tt.os == "linux" {
				if !strings.Contains(data, `"/home/a/App Data/50%% $$rail"`) {
					t.Fatal("systemd interpolation not escaped")
				}
			} else {
				var document anyXML
				if err := xml.Unmarshal(plan.Content, &document); err != nil {
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
