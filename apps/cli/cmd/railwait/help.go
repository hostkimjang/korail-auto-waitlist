package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"
)

// docsURL is the CLI guide shown at the end of railwait help.
const docsURL = "https://github.com/hostkimjang/korail-auto-waitlist/blob/main/apps/cli/README.md"

// helpOutput receives help text: the command list and each command's usage. Errors still
// go to stderr through main.
var helpOutput io.Writer = os.Stdout

// commandDoc describes a command for railwait help and railwait <command> --help. Top-level
// commands have a Group; subcommands such as "configure reservation" do not.
type commandDoc struct {
	Name    string
	Group   string
	Summary string
	// Usage lines omit the leading "railwait ".
	Usage []string
	// Subcommands are "name  description" lines for a command group.
	Subcommands []string
	Details     []string
}

var commandGroups = []string{"설치·업데이트", "설정", "관리 서비스·운영", "진단", "제거", "기타"}

var commandDocs = []commandDoc{
	{
		Name: "install", Group: "설치·업데이트", Summary: "앱을 설치하거나 멈춘 설치를 이어서 진행합니다",
		Usage:   []string{"install [옵션]", "install --custom", "install --path <새 설치 경로> --package <로컬 패키지> --trust-key <공개키 파일>"},
		Details: []string{"실행 환경 → 설치 계획 → 앱·계정 → 자동 시작 → 접속 확인 순서로 진행합니다.", "같은 --path로 다시 실행하면 이미 끝난 단계는 확인만 하고 이어갑니다."},
	},
	{Name: "setup", Group: "설치·업데이트", Summary: "install과 같습니다", Usage: []string{"setup [옵션]"}, Details: []string{"옵션은 railwait install --help 와 같습니다."}},
	{
		Name: "update", Group: "설치·업데이트", Summary: "새 안정 릴리스를 확인하고 계획한 버전으로 업데이트합니다",
		Usage: []string{
			"update check [--path <설치 경로>] [--json]",
			"update [--path <설치 경로>] --dry-run",
			"update [--path <설치 경로>] --target <확인한 버전> --plan <계획 ID>",
			"update [--path <설치 경로>] --package <새 로컬 패키지> --trust-key <공개키 파일> [--dry-run | --target <버전> --plan <계획 ID>]",
		},
		Subcommands: []string{"check   최신 안정 릴리스와 업데이트 가능 여부만 확인합니다"},
		Details:     []string{"--dry-run이 보여 준 --target·--plan을 붙여 다시 실행하면 적용합니다. 계획은 15분 동안 유효합니다."},
	},
	{
		Name: "configure", Group: "설정", Summary: "TAGO 키와 자동 예매 스위치를 바꿉니다",
		Usage: []string{
			"configure [tago] [--path <설치 경로>] [--input <보호된 JSON 파일 | ->]",
			"configure [--path <설치 경로>] --plan <계획 ID>",
			"configure reservation on|off [--path <설치 경로>] [--plan <계획 ID>]",
		},
		Subcommands: []string{"tago          TAGO 인증키를 바꾸는 계획을 만듭니다 (기본)", "reservation   자동 예매 스위치를 켜거나 끄는 계획을 만듭니다"},
		Details:     []string{"먼저 계획을 만들고, 출력된 --plan을 붙여 다시 실행하면 적용합니다."},
	},
	{
		Name: "configure reservation", Summary: "자동 예매 스위치(KORAIL·SRT)를 함께 켜거나 끕니다",
		Usage:   []string{"configure reservation on|off [--path <설치 경로>] [--json]", "configure reservation on|off [--path <설치 경로>] --plan <계획 ID>"},
		Details: []string{"계획을 만든 뒤 같은 on·off와 --plan으로 적용합니다. 적용은 이 CLI가 직접 실행하므로 끝날 때까지 창을 닫지 마세요."},
	},
	{
		Name: "manager", Group: "관리 서비스·운영", Summary: "호스트 관리 서비스를 설치·교체·확인합니다",
		Usage: []string{"manager install|upgrade|status [--path <설치 경로>]", "manager upgrade [--path <설치 경로>] --force"},
		Subcommands: []string{
			"install   관리 서비스를 등록하고 시작합니다. 더 오래된 관리 서비스는 이 CLI로 바꿉니다",
			"upgrade   관리 서비스 실행 파일을 이 CLI로 바꾸고 다시 시작합니다",
			"status    관리 서비스 heartbeat를 확인합니다",
			"serve     관리 서비스 본체입니다. OS 서비스가 실행하므로 직접 실행하지 않습니다",
		},
	},
	{
		Name: "restart", Group: "관리 서비스·운영", Summary: "앱 서비스를 다시 시작합니다 (PostgreSQL·Redis 유지)",
		Usage: []string{"restart [--path <설치 경로>] [--yes]"},
		Details: []string{
			"현재 .env를 Compose로 확인한 뒤 새 작업 차단 → 진행 중인 작업 종료 대기 → 앱 서비스 재생성 → 상태 확인 → 재개 순서로 진행합니다.",
			"1~3분 동안 접속이 끊기고 KORAIL에 다시 로그인합니다. 대화형 터미널이 아니면 --yes가 필요합니다.",
		},
	},
	{
		Name: "status", Group: "진단", Summary: "설치·서비스 상태와 관리 작업 결과를 봅니다",
		Usage:   []string{"status [--path <설치 경로>] [--json]", "status [--path <설치 경로>] --job <작업 ID> [--json]"},
		Details: []string{"--job은 웹·관리 서비스·CLI가 실행한 업데이트와 설정 작업의 단계와 결과를 보여 줍니다."},
	},
	{
		Name: "open", Group: "관리 서비스·운영", Summary: "설정 파일(.env) 위치를 보여 주고 파일 관리자에서 엽니다",
		Usage: []string{"open [--path <설치 경로>]"},
		Details: []string{
			".env의 절대경로를 출력하고 macOS Finder·Windows 탐색기에서 파일을, Linux에서는 설치 폴더를 엽니다.",
			"화면이 없는 세션이거나 열지 못하면 경로와 직접 열 명령만 보여 줍니다. 고친 설정은 railwait restart로 반영합니다.",
		},
	},
	{
		Name: "doctor", Group: "진단", Summary: "Docker와 Compose 준비 상태를 확인합니다",
		Usage:   []string{"doctor [--path <설치 경로>] [--docker-context <이름>] [--json]"},
		Details: []string{"읽기 전용 확인입니다. 철도사 로그인·예약은 실행하지 않습니다."},
	},
	{
		Name: "package", Group: "진단", Summary: "로컬 설치 패키지의 서명과 파일을 검증합니다",
		Usage:       []string{"package verify --package <패키지 폴더> --trust-key <공개키 파일>"},
		Subcommands: []string{"verify   서명·번들·이미지 archive를 검증합니다. Docker에 불러오지는 않습니다"},
	},
	{Name: "preview", Group: "기타", Summary: "설치 화면 예시를 봅니다 (아무것도 바꾸지 않음)", Usage: []string{"preview [--plain]"}},
	{
		Name: "uninstall", Group: "제거", Summary: "관리 서비스와 컨테이너를 지웁니다 (데이터는 기본 보존)",
		Usage: []string{"uninstall [--path <설치 경로>] [--yes]", "uninstall [--path <설치 경로>] --delete-data [--yes]"},
		Details: []string{
			"관리 서비스 등록과 이 설치의 Docker 컨테이너·네트워크를 지웁니다. 볼륨(DB·Redis·백업)과 설치 폴더(.env·복구키)는 남깁니다.",
			"--delete-data는 볼륨과 설치 폴더까지 영구 삭제합니다. Docker 이미지, Tailscale 경로와 CLI 실행 파일은 지우는 방법만 안내합니다.",
			"대화형 터미널에서는 설치 폴더 이름을 입력해 확인하고, --delete-data는 DELETE도 입력합니다. 비대화형 실행은 --yes가 필요합니다.",
		},
	},
	{Name: "version", Group: "기타", Summary: "CLI 버전과 OS/CPU를 표시합니다", Usage: []string{"version"}},
	{Name: "help", Group: "기타", Summary: "명령 목록이나 명령별 도움말을 봅니다", Usage: []string{"help", "help <명령>"}, Details: []string{"railwait help <명령>은 railwait <명령> --help 와 같습니다."}},
}

// commonExamples are the commands most people need, with the default installation folder.
var commonExamples = [][2]string{
	{"railwait install", "처음 설치하거나 멈춘 설치를 이어갑니다"},
	{"railwait status", "앱과 서비스 상태를 확인합니다"},
	{"railwait update --dry-run", "업데이트할 수 있는지 계획을 확인합니다"},
	{"railwait configure", "TAGO 키를 바꿀 계획을 만듭니다"},
	{"railwait restart", "앱 서비스를 다시 시작합니다"},
	{"railwait open", "설정 파일(.env) 위치를 엽니다"},
	{"railwait manager upgrade", "관리 서비스를 이 CLI로 바꿉니다"},
}

func findCommandDoc(name string) (commandDoc, bool) {
	for _, doc := range commandDocs {
		if doc.Name == name {
			return doc, true
		}
	}
	return commandDoc{}, false
}

func printTopHelp(w io.Writer) {
	fmt.Fprintf(w, "railwait %s · Railwait 설치·운영 CLI\n\n사용법: railwait <명령> [옵션]\n", version)
	for _, group := range commandGroups {
		listed := false
		for _, doc := range commandDocs {
			if doc.Group == group {
				if !listed {
					fmt.Fprintf(w, "\n%s\n", group)
					listed = true
				}
				fmt.Fprintf(w, "  %-11s %s\n", doc.Name, doc.Summary)
			}
		}
	}
	fmt.Fprintln(w, "\n자주 쓰는 예")
	for _, example := range commonExamples {
		fmt.Fprintf(w, "  %-28s %s\n", example[0], example[1])
	}
	fmt.Fprintln(w, "  설치 폴더가 기본 위치가 아니면 --path <설치 경로>를 붙입니다.")
	fmt.Fprintln(w, "\n명령별 옵션은 railwait <명령> --help 로 확인하세요. (railwait help <명령>도 같습니다)")
	fmt.Fprintln(w, "자세한 안내:", docsURL)
}

func printCommandHelp(w io.Writer, name string, flags *flag.FlagSet) {
	doc, ok := findCommandDoc(name)
	if !ok {
		doc = commandDoc{Name: name, Usage: []string{name + " [옵션]"}}
	}
	if doc.Summary != "" {
		fmt.Fprintf(w, "railwait %s · %s\n", doc.Name, doc.Summary)
	}
	fmt.Fprintln(w, "\n사용법:")
	for _, line := range doc.Usage {
		fmt.Fprintln(w, "  railwait", line)
	}
	if len(doc.Subcommands) > 0 {
		fmt.Fprintln(w, "\n하위 명령:")
		for _, line := range doc.Subcommands {
			fmt.Fprintln(w, "  "+line)
		}
	}
	if len(doc.Details) > 0 {
		fmt.Fprintln(w)
		for _, line := range doc.Details {
			fmt.Fprintln(w, line)
		}
	}
	options := false
	if flags != nil {
		flags.VisitAll(func(*flag.Flag) { options = true })
	}
	if options {
		fmt.Fprintln(w, "\n옵션:")
		output := flags.Output()
		flags.SetOutput(w)
		flags.PrintDefaults()
		flags.SetOutput(output)
	}
	fmt.Fprintln(w, "\n전체 명령 목록: railwait help")
}

// newCommandFlags creates a command's flag set. Its Usage prints the command's purpose,
// usage lines and options.
func newCommandFlags(name string) *flag.FlagSet {
	flags := flag.NewFlagSet(name, flag.ContinueOnError)
	flags.Usage = func() { printCommandHelp(flags.Output(), name, flags) }
	return flags
}

// parseCommandFlags prints the command's help on --help and returns flag.ErrHelp, which
// main treats as success. A wrong option is reported once, pointing to that help.
func parseCommandFlags(flags *flag.FlagSet, args []string) error {
	flags.SetOutput(io.Discard)
	err := flags.Parse(args)
	if errors.Is(err, flag.ErrHelp) {
		flags.SetOutput(helpOutput)
		flags.Usage()
		return flag.ErrHelp
	}
	if err != nil {
		return fmt.Errorf("옵션을 확인하세요: %v; railwait %s --help 로 사용법을 볼 수 있습니다", err, flags.Name())
	}
	return nil
}

func isHelpArg(value string) bool {
	return value == "-h" || value == "--help" || value == "-help" || value == "--h"
}

// helpCommand prints the command list, or a command's help: railwait help <command> runs
// railwait <command> --help.
func helpCommand(ctx context.Context, args []string) error {
	if len(args) == 0 || isHelpArg(args[0]) {
		printTopHelp(helpOutput)
		return nil
	}
	if _, ok := commandHandler(args[0]); !ok {
		return unknownCommand(args[0])
	}
	return run(ctx, append(append([]string{}, args...), "--help"))
}

func unknownCommand(name string) error {
	return fmt.Errorf("알 수 없는 명령입니다: %q; 명령 목록은 railwait help 로 확인하세요", name)
}

// subcommandHelp handles "railwait <group> --help" for a command whose first argument
// chooses a subcommand.
func subcommandHelp(flags *flag.FlagSet, args []string) (bool, error) {
	if len(args) > 0 && isHelpArg(args[0]) {
		return true, parseCommandFlags(flags, args[:1])
	}
	return false, nil
}

// subcommandList renders a group's subcommand names for an error message.
func subcommandList(name string) string {
	doc, _ := findCommandDoc(name)
	names := []string{}
	for _, line := range doc.Subcommands {
		names = append(names, strings.Fields(line)[0])
	}
	return strings.Join(names, "|")
}
