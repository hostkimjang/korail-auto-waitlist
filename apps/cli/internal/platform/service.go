package platform

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/xml"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
)

type ServiceCommand struct {
	Program string   `json:"program"`
	Args    []string `json:"args"`
}

type ServicePlan struct {
	OS           string           `json:"os"`
	Name         string           `json:"name"`
	Definition   string           `json:"definition"`
	Content      []byte           `json:"-"`
	Register     []ServiceCommand `json:"register"`
	Start        ServiceCommand   `json:"start"`
	Status       ServiceCommand   `json:"status"`
	Stop         ServiceCommand   `json:"stop"`
	Availability string           `json:"availability"`
}

// CurrentServicePlan renders a user-level login service. It never registers a
// service or silently elevates privileges. Docker must be usable by this user.
func CurrentServicePlan(executable, installation string) (ServicePlan, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return ServicePlan{}, err
	}
	identity, err := serviceIdentity()
	if err != nil {
		return ServicePlan{}, err
	}
	plan, err := RenderService(runtime.GOOS, home, identity, executable, installation)
	if err != nil {
		return plan, err
	}
	path := os.Getenv("PATH")
	if strings.ContainsAny(path, "\x00\r\n") {
		return plan, fmt.Errorf("invalid service PATH")
	}
	if path != "" {
		if runtime.GOOS == "linux" {
			quoted := systemdQuote("PATH=" + path)
			// Environment= performs specifier expansion, but no $ expansion.
			quoted = strings.ReplaceAll(quoted, "$$", "$")
			plan.Content = []byte(strings.Replace(string(plan.Content), "[Service]\n", "[Service]\nEnvironment="+quoted+"\n", 1))
		}
		if runtime.GOOS == "darwin" {
			plan.Content = []byte(strings.Replace(string(plan.Content), "<key>RunAtLoad</key>", "<key>EnvironmentVariables</key><dict><key>PATH</key><string>"+xmlText(path)+"</string></dict><key>RunAtLoad</key>", 1))
		}
	}
	return plan, nil
}

func RenderService(goos, home, identity, executable, installation string) (ServicePlan, error) {
	for _, value := range []string{home, executable, installation} {
		if value == "" || strings.ContainsAny(value, "\x00\r\n") {
			return ServicePlan{}, fmt.Errorf("invalid service path")
		}
	}
	if !SupportedTarget(goos, "amd64") {
		return ServicePlan{}, fmt.Errorf("unsupported service platform")
	}
	if !absoluteForOS(goos, home) || !absoluteForOS(goos, executable) || !absoluteForOS(goos, installation) {
		return ServicePlan{}, fmt.Errorf("service paths must be absolute")
	}
	sum := sha256.Sum256([]byte(installation))
	name := "railwait-manager-" + hex.EncodeToString(sum[:6])
	plan := ServicePlan{OS: goos, Name: name}
	switch goos {
	case "linux":
		unit := name + ".service"
		plan.Definition = filepath.Join(home, ".config", "systemd", "user", unit)
		plan.Content = []byte("[Unit]\nDescription=Railwait host manager\n\n[Service]\nType=simple\nExecStart=" + systemdQuote(executable) + " manager serve --path " + systemdQuote(installation) + "\nRestart=on-failure\nRestartSec=5\nUMask=0077\n\n[Install]\nWantedBy=default.target\n")
		plan.Register = []ServiceCommand{{"systemctl", []string{"--user", "daemon-reload"}}, {"systemctl", []string{"--user", "enable", unit}}}
		plan.Start = ServiceCommand{"systemctl", []string{"--user", "start", unit}}
		plan.Status = ServiceCommand{"systemctl", []string{"--user", "is-active", "--quiet", unit}}
		plan.Stop = ServiceCommand{"systemctl", []string{"--user", "stop", unit}}
		plan.Availability = "사용자 systemd 세션에서 시작합니다. 로그아웃 뒤 유지·부팅 자동 시작은 관리자가 user lingering을 별도로 허용해야 합니다."
	case "darwin":
		if _, err := strconv.ParseUint(identity, 10, 32); err != nil {
			return ServicePlan{}, fmt.Errorf("invalid launchd user ID")
		}
		label := "com.railwait." + name
		plan.Name = label
		plan.Definition = filepath.Join(home, "Library", "LaunchAgents", label+".plist")
		plan.Content = []byte("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n<plist version=\"1.0\"><dict><key>Label</key><string>" + xmlText(label) + "</string><key>ProgramArguments</key><array><string>" + xmlText(executable) + "</string><string>manager</string><string>serve</string><string>--path</string><string>" + xmlText(installation) + "</string></array><key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>5</integer></dict></plist>\n")
		domain := "gui/" + identity
		target := domain + "/" + label
		plan.Register = []ServiceCommand{{"launchctl", []string{"bootstrap", domain, plan.Definition}}}
		plan.Start = ServiceCommand{"launchctl", []string{"kickstart", target}}
		plan.Status = ServiceCommand{"launchctl", []string{"print", target}}
		plan.Stop = ServiceCommand{"launchctl", []string{"bootout", target}}
		plan.Availability = "현재 사용자의 GUI 로그인 세션에서 LaunchAgent로 실행합니다. Docker Desktop도 로그인 후 사용 가능해야 합니다."
	case "windows":
		if !strings.HasPrefix(identity, "S-1-") || strings.ContainsAny(identity, "<&\"'\r\n") {
			return ServicePlan{}, fmt.Errorf("invalid Windows user SID")
		}
		plan.Definition = windowsJoin(installation, ".railwait", "manager", "service", name+".xml")
		args := strings.Join([]string{"manager", "serve", "--path", windowsQuote(installation)}, " ")
		plan.Content = []byte("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<Task version=\"1.2\" xmlns=\"http://schemas.microsoft.com/windows/2004/02/mit/task\"><Triggers><LogonTrigger><Enabled>true</Enabled><UserId>" + xmlText(identity) + "</UserId></LogonTrigger></Triggers><Principals><Principal id=\"Author\"><UserId>" + xmlText(identity) + "</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals><Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure></Settings><Actions Context=\"Author\"><Exec><Command>" + xmlText(executable) + "</Command><Arguments>" + xmlText(args) + "</Arguments><WorkingDirectory>" + xmlText(installation) + "</WorkingDirectory></Exec></Actions></Task>\n")
		plan.Register = []ServiceCommand{{"schtasks.exe", []string{"/Create", "/TN", name, "/XML", plan.Definition, "/F"}}}
		plan.Start = ServiceCommand{"schtasks.exe", []string{"/Run", "/TN", name}}
		plan.Status = ServiceCommand{"schtasks.exe", []string{"/Query", "/TN", name, "/XML"}}
		plan.Stop = ServiceCommand{"schtasks.exe", []string{"/End", "/TN", name}}
		plan.Availability = "현재 사용자 로그인 시 최소 권한의 Task Scheduler 작업으로 실행합니다. 로그아웃 상태에서는 실행하지 않습니다."
	}
	return plan, nil
}

func absoluteForOS(goos, path string) bool {
	if goos == "windows" {
		return len(path) > 2 && ((path[0] >= 'A' && path[0] <= 'Z') || (path[0] >= 'a' && path[0] <= 'z')) && path[1] == ':' && (path[2] == '\\' || path[2] == '/')
	}
	return strings.HasPrefix(path, "/")
}
func windowsJoin(parts ...string) string { return strings.Join(parts, "\\") }
func systemdQuote(value string) string {
	r := strings.NewReplacer("\\", "\\\\", "\"", "\\\"", "%", "%%", "$", "$$", "\t", "\\t")
	return "\"" + r.Replace(value) + "\""
}
func xmlText(value string) string {
	var b bytes.Buffer
	_ = xml.EscapeText(&b, []byte(value))
	return b.String()
}
func windowsQuote(value string) string {
	var out strings.Builder
	out.WriteByte('"')
	slashes := 0
	for _, char := range value {
		if char == '\\' {
			slashes++
			continue
		}
		if char == '"' {
			out.WriteString(strings.Repeat("\\", slashes*2+1))
		} else {
			out.WriteString(strings.Repeat("\\", slashes))
		}
		slashes = 0
		out.WriteRune(char)
	}
	out.WriteString(strings.Repeat("\\", slashes*2))
	out.WriteByte('"')
	return out.String()
}
