#!/bin/sh
# scripts/install/install.sh 기능 테스트입니다.
# 가짜 릴리스 자산을 127.0.0.1의 임시 HTTP 서버로 제공하고, 지정한 셸(기본: sh·dash·bash 중 있는 것)로
# OS·CPU 감지, SHA-256 검증 실패 시 중단, 인자 전달, TTY 유무 처리, 설치 위치 제한을 확인합니다.
# 실제 홈 폴더·네트워크를 쓰지 않으며 만든 임시 폴더와 서버는 종료할 때 정리합니다.
#
# 사용법: sh scripts/install/tests/test-install-sh.sh [셸 ...]
set -eu

TEST_DIR=$(cd "$(dirname "$0")" && pwd -P)
INSTALLER="$TEST_DIR/../install.sh"
PYTHON=$(command -v python3) || {
    echo 'python3가 필요합니다.' >&2
    exit 2
}
ORIGINAL_PATH=$PATH
unset RAILWAIT_VERSION RAILWAIT_BIN_DIR RAILWAIT_DOWNLOAD_BASE XDG_DATA_HOME SUDO_USER

WORK=$(mktemp -d "${TMPDIR:-/tmp}/railwait-install-test.XXXXXX")
SERVER_PID=''
cleanup() {
    if [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        set -- "$(sha256sum "$1")"
    else
        set -- "$(shasum -a 256 "$1")"
    fi
    printf '%s' "${1%% *}"
}

# --- 가짜 릴리스 자산 -------------------------------------------------------------------
WWW="$WORK/www"
mkdir -p "$WWW/good" "$WWW/crlf" "$WWW/badhash" "$WWW/noline" "$WWW/twolines" "$WWW/missing"
for os in darwin linux; do
    for arch in amd64 arm64; do
        asset="railwait-$os-$arch"
        cat >"$WWW/good/$asset" <<EOF
#!/bin/sh
# fake $asset
{
    if [ -t 0 ]; then echo stdin=tty; else echo stdin=other; fi
    for a in "\$@"; do printf 'arg=%s\n' "\$a"; done
} >"\$RAILWAIT_TEST_LOG"
EOF
        printf '%s  %s\n' "$(sha256_of "$WWW/good/$asset")" "$asset" >"$WWW/good/$asset.sha256"
    done
done
asset=railwait-linux-amd64
digest=$(sha256_of "$WWW/good/$asset")
for dir in crlf badhash noline twolines; do
    cp "$WWW/good/$asset" "$WWW/$dir/$asset"
done
printf 'tampered\n' >"$WORK/tampered"
printf '%s *%s\r\n' "$digest" "$asset" >"$WWW/crlf/$asset.sha256"
printf '%s  %s\n' "$(sha256_of "$WORK/tampered")" "$asset" >"$WWW/badhash/$asset.sha256"
printf '%s  %s\n' "$digest" railwait-linux-arm64 >"$WWW/noline/$asset.sha256"
printf '%s  %s\n%s  %s\n' "$digest" "$asset" "$digest" "$asset" >"$WWW/twolines/$asset.sha256"

# --- 로컬 HTTP 서버 -----------------------------------------------------------------------
"$PYTHON" - "$WWW" "$WORK/port" >"$WORK/server.log" 2>&1 <<'PY' &
import http.server
import os
import sys

root, port_file = sys.argv[1], sys.argv[2]


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=root, **kwargs)

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
with open(port_file + ".tmp", "w") as handle:
    handle.write(str(server.server_address[1]))
os.replace(port_file + ".tmp", port_file)
server.serve_forever()
PY
SERVER_PID=$!
tries=0
while [ ! -s "$WORK/port" ]; do
    tries=$((tries + 1))
    if [ "$tries" -gt 100 ]; then
        cat "$WORK/server.log" >&2
        echo '테스트 HTTP 서버가 시작되지 않았습니다.' >&2
        exit 1
    fi
    sleep 0.1
done
URL="http://127.0.0.1:$(cat "$WORK/port")"

# --- 실행 도우미와 스텁 -------------------------------------------------------------------
# notty: 새 세션으로 실행해 /dev/tty가 없게 합니다. tty: 가상 터미널을 제어 터미널로 붙입니다.
# 두 경우 모두 스크립트를 표준입력으로 넘겨 'curl ... | sh -s -- 인자'와 같은 방식으로 실행합니다.
RUNNER="$WORK/runner.py"
cat >"$RUNNER" <<'PY'
import os
import pty
import sys

mode, script, shell, *args = sys.argv[1:]


def child():
    fd = os.open(script, os.O_RDONLY)
    os.dup2(fd, 0)
    os.close(fd)
    os.execv(shell, [shell, "-s", "--", *args])


def code(status):
    return os.WEXITSTATUS(status) if os.WIFEXITED(status) else 128 + os.WTERMSIG(status)


if mode == "notty":
    pid = os.fork()
    if pid == 0:
        os.setsid()
        child()
    sys.exit(code(os.waitpid(pid, 0)[1]))

pid, master = pty.fork()
if pid == 0:
    child()
while True:
    try:
        data = os.read(master, 4096)
    except OSError:
        break
    if not data:
        break
    sys.stdout.buffer.write(data)
sys.stdout.buffer.flush()
sys.exit(code(os.waitpid(pid, 0)[1]))
PY

STUB="$WORK/stub"
mkdir -p "$STUB" "$WORK/stub-root" "$WORK/stub-urls" "$WORK/restricted"
cat >"$STUB/uname" <<'EOF'
#!/bin/sh
case $1 in
    -s) printf '%s\n' "$FAKE_UNAME_S" ;;
    -m) printf '%s\n' "$FAKE_UNAME_M" ;;
    *) exit 1 ;;
esac
EOF
cat >"$STUB/sysctl" <<'EOF'
#!/bin/sh
[ -n "${FAKE_TRANSLATED:-}" ] || exit 1
printf '%s\n' "$FAKE_TRANSLATED"
EOF
cat >"$WORK/stub-root/id" <<'EOF'
#!/bin/sh
echo 0
EOF
cat >"$WORK/stub-urls/curl" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >>"$RAILWAIT_TEST_URLS"
exit 22
EOF
cat >"$WORK/wget-stub" <<EOF
#!$PYTHON
import sys
import urllib.request

args = sys.argv[1:]
if args == ["--version"]:
    print("GNU Wget test stub")
    sys.exit(0)
output, url = args[args.index("-O") + 1], args[-1]
try:
    with urllib.request.urlopen(url) as response, open(output, "wb") as handle:
        handle.write(response.read())
except Exception:
    sys.exit(8)
EOF
chmod 755 "$STUB/uname" "$STUB/sysctl" "$WORK/stub-root/id" "$WORK/stub-urls/curl" "$WORK/wget-stub"

# curl 없이 wget만 있는 PATH입니다. install.sh가 쓰는 외부 명령만 연결합니다.
for tool in id mktemp mkdir cp chmod mv rm sed tr cat sha256sum shasum; do
    if found=$(command -v "$tool" 2>/dev/null); then
        ln -s "$found" "$WORK/restricted/$tool"
    fi
done
ln -s "$STUB/uname" "$WORK/restricted/uname"
ln -s "$STUB/sysctl" "$WORK/restricted/sysctl"
if found=$(command -v wget 2>/dev/null); then
    ln -s "$found" "$WORK/restricted/wget"
    WGET_KIND='실제 wget'
else
    ln -s "$WORK/wget-stub" "$WORK/restricted/wget"
    WGET_KIND='wget 스텁'
fi

# --- 단언 도우미 --------------------------------------------------------------------------
PASSED=0
fail() {
    printf 'FAIL [%s/%s] %s\n' "$SH_NAME" "$CASE" "$*" >&2
    if [ -f "$CASE_DIR/out" ]; then
        echo '--- 출력 ---' >&2
        cat "$CASE_DIR/out" >&2
    fi
    exit 1
}

new_case() {
    CASE=$1
    CASE_DIR="$WORK/runs/$SH_NAME/$CASE"
    mkdir -p "$CASE_DIR/home" "$CASE_DIR/tmp"
    K=Linux
    M=x86_64
    BASE="$URL/good"
    BIN="$CASE_DIR/bin"
    VERSION=''
    TRANSLATED=''
    SUDO=''
    RUN_PATH="$STUB:$ORIGINAL_PATH"
}

invoke() {
    mode=$1
    shift
    if env HOME="$CASE_DIR/home" TMPDIR="$CASE_DIR/tmp" PATH="$RUN_PATH" \
        FAKE_UNAME_S="$K" FAKE_UNAME_M="$M" FAKE_TRANSLATED="$TRANSLATED" SUDO_USER="$SUDO" \
        RAILWAIT_DOWNLOAD_BASE="$BASE" RAILWAIT_BIN_DIR="$BIN" RAILWAIT_VERSION="$VERSION" \
        RAILWAIT_TEST_LOG="$CASE_DIR/log" RAILWAIT_TEST_URLS="$CASE_DIR/urls" \
        "$PYTHON" "$RUNNER" "$mode" "$INSTALLER" "$SH_PATH" "$@" >"$CASE_DIR/out" 2>&1; then
        STATUS=0
    else
        STATUS=$?
    fi
}

passed() {
    PASSED=$((PASSED + 1))
}

expect_success() {
    [ "$STATUS" -eq 0 ] || fail "종료 코드 $STATUS (기대 0)"
}

expect_failure() {
    [ "$STATUS" -ne 0 ] || fail '실패해야 하는데 성공했습니다.'
}

expect_output() {
    grep -F -- "$1" "$CASE_DIR/out" >/dev/null || fail "출력에 '$1'이 없습니다."
}

expect_no_output() {
    if grep -F -- "$1" "$CASE_DIR/out" >/dev/null; then
        fail "출력에 '$1'이 있으면 안 됩니다."
    fi
}

expect_file() {
    [ -f "$1" ] || fail "$1 이 없습니다."
}

expect_tmp_clean() {
    [ -z "$(ls -A "$CASE_DIR/tmp")" ] || fail '임시 폴더가 정리되지 않았습니다.'
    if [ -d "$1" ]; then
        for staged in "$1"/.railwait-install.*; do
            [ ! -e "$staged" ] || fail "교체용 임시 파일이 남았습니다: $staged"
        done
    fi
}

expect_installed() {
    expect_file "$1/railwait"
    [ -x "$1/railwait" ] || fail "$1/railwait 에 실행 권한이 없습니다."
    cmp -s "$1/railwait" "$WWW/good/$2" || fail "$1/railwait 이 $2 와 다릅니다."
}

# --- 테스트 -------------------------------------------------------------------------------
run_suite() {
    new_case help
    invoke notty --help
    expect_success
    expect_output RAILWAIT_VERSION
    expect_output '항상 최신 stable 앱 릴리스'
    [ ! -e "$BIN" ] || fail '--help가 설치 폴더를 만들었습니다.'
    passed

    for detection in Darwin:arm64::darwin-arm64 Darwin:x86_64::darwin-amd64 Darwin:x86_64:1:darwin-arm64 \
        Linux:aarch64::linux-arm64 Linux:arm64::linux-arm64 Linux:x86_64::linux-amd64 Linux:amd64::linux-amd64; do
        kernel=${detection%%:*}
        rest=${detection#*:}
        machine=${rest%%:*}
        rest=${rest#*:}
        translated=${rest%%:*}
        expected=railwait-${rest#*:}
        new_case "detect-$kernel-$machine-$translated"
        K=$kernel
        M=$machine
        TRANSLATED=$translated
        invoke notty
        expect_success
        expect_installed "$BIN" "$expected"
        expect_output '대화형 터미널이 없어 설치 마법사를 시작하지 않았습니다.'
        expect_tmp_clean "$BIN"
        [ ! -e "$CASE_DIR/log" ] || fail 'TTY가 없는데 railwait install을 실행했습니다.'
        passed
    done

    new_case default-bin-dir
    BIN=''
    invoke notty
    expect_success
    expect_installed "$CASE_DIR/home/.local/bin" railwait-linux-amd64
    expect_output 'PATH에 없습니다'
    expect_output "export PATH='$CASE_DIR/home/.local/bin':\"\$PATH\""
    passed

    new_case bin-dir-on-path
    RUN_PATH="$BIN:$RUN_PATH"
    invoke notty
    expect_success
    expect_no_output 'PATH에 없습니다'
    passed

    new_case replaces-existing
    mkdir -p "$BIN"
    printf 'old\n' >"$BIN/railwait"
    invoke notty
    expect_success
    expect_installed "$BIN" railwait-linux-amd64
    expect_tmp_clean "$BIN"
    passed

    for unsupported in FreeBSD:amd64:'지원하지 않는 운영체제' MINGW64_NT-10.0:x86_64:install.ps1 \
        Linux:i686:'지원하지 않는 CPU' Linux:armv7l:'지원하지 않는 CPU' Darwin:ppc:'지원하지 않는 CPU'; do
        new_case "unsupported-${unsupported%%:*}"
        K=${unsupported%%:*}
        rest=${unsupported#*:}
        M=${rest%%:*}
        invoke notty
        expect_failure
        expect_output "${rest#*:}"
        [ ! -e "$BIN" ] || fail '지원하지 않는 환경에서 설치 폴더를 만들었습니다.'
        passed
    done

    new_case checksum-mismatch
    BASE="$URL/badhash"
    mkdir -p "$BIN"
    printf 'old\n' >"$BIN/railwait"
    invoke notty
    expect_failure
    expect_output 'SHA-256이 일치하지 않아 설치를 중단했습니다.'
    [ "$(cat "$BIN/railwait")" = old ] || fail '검증 실패 후 기존 설치 CLI가 바뀌었습니다.'
    expect_tmp_clean "$BIN"
    [ ! -e "$CASE_DIR/log" ] || fail '검증 실패 후 railwait를 실행했습니다.'
    passed

    for broken in noline twolines; do
        new_case "checksum-$broken"
        BASE="$URL/$broken"
        invoke notty
        expect_failure
        expect_output '항목을 정확히 하나 찾지 못했습니다.'
        [ ! -e "$BIN/railwait" ] || fail '체크섬 항목이 잘못됐는데 설치했습니다.'
        expect_tmp_clean "$BIN"
        passed
    done

    new_case checksum-crlf-binary-marker
    BASE="$URL/crlf"
    invoke notty
    expect_success
    expect_installed "$BIN" railwait-linux-amd64
    passed

    new_case missing-asset
    BASE="$URL/missing"
    invoke notty
    expect_failure
    expect_output '내려받지 못했습니다'
    [ ! -e "$BIN/railwait" ] || fail '자산이 없는데 설치했습니다.'
    expect_tmp_clean "$BIN"
    passed

    new_case tty-forwards-arguments
    invoke tty --custom --port 9090 --path "/tmp/with space/it's"
    expect_success
    expect_output '설치 마법사를 시작합니다'
    expect_file "$CASE_DIR/log"
    printf '%s\n' stdin=tty arg=install arg=--custom arg=--port arg=9090 arg=--path "arg=/tmp/with space/it's" \
        >"$CASE_DIR/expected-log"
    cmp -s "$CASE_DIR/log" "$CASE_DIR/expected-log" || fail "railwait install 인자가 다릅니다: $(cat "$CASE_DIR/log")"
    passed

    new_case notty-shows-next-command
    invoke notty --custom --port 9090 --path "/tmp/with space/it's"
    expect_success
    expect_output "'$BIN/railwait' 'install' '--custom' '--port' '9090' '--path' '/tmp/with space/it'\''s'"
    [ ! -e "$CASE_DIR/log" ] || fail 'TTY가 없는데 railwait install을 실행했습니다.'
    passed

    for pinned in 1.4.4:v1.4.4 v1.4.4:v1.4.4 :latest; do
        new_case "download-url-${pinned#*:}-${pinned%%:*}"
        BASE=''
        VERSION=${pinned%%:*}
        RUN_PATH="$WORK/stub-urls:$STUB:$ORIGINAL_PATH"
        invoke notty
        expect_failure
        if [ "${pinned#*:}" = latest ]; then
            want='https://github.com/hostkimjang/korail-auto-waitlist/releases/latest/download/railwait-linux-amd64'
        else
            want='https://github.com/hostkimjang/korail-auto-waitlist/releases/download/v1.4.4/railwait-linux-amd64'
        fi
        grep -F -- "--proto =https --proto-redir =https" "$CASE_DIR/urls" >/dev/null ||
            fail 'curl에 https 전용 옵션이 없습니다.'
        grep -F -- "$want" "$CASE_DIR/urls" >/dev/null || fail "다운로드 주소가 다릅니다: $(cat "$CASE_DIR/urls")"
        if [ -n "$VERSION" ]; then
            expect_output '앱은 항상 최신 stable 릴리스로 설치합니다.'
        fi
        passed
    done

    for invalid in 1.4 1.4.4.1 1.x.4 .1.4; do
        new_case "invalid-version-$invalid"
        BASE=''
        VERSION=$invalid
        invoke notty
        expect_failure
        expect_output 'RAILWAIT_VERSION 형식이 올바르지 않습니다'
        passed
    done

    new_case insecure-download-base
    BASE='http://example.com/assets'
    invoke notty
    expect_failure
    expect_output 'RAILWAIT_DOWNLOAD_BASE는 https 주소이거나'
    passed

    new_case relative-bin-dir
    BIN='relative/bin'
    invoke notty
    expect_failure
    expect_output 'RAILWAIT_BIN_DIR는 절대경로여야 합니다.'
    passed

    new_case bin-inside-linux-app-dir
    BIN="$CASE_DIR/home/.local/share/railwait/bin"
    invoke notty
    expect_failure
    expect_output '앱 설치 폴더'
    [ ! -e "$BIN" ] || fail '앱 설치 폴더 안에 설치 폴더를 만들었습니다.'
    passed

    new_case bin-inside-macos-app-dir
    K=Darwin
    M=arm64
    BIN="$CASE_DIR/home/Library/Application Support/Railwait/.railwait/bin"
    invoke notty
    expect_failure
    expect_output '앱 설치 폴더'
    passed

    new_case target-is-directory
    mkdir -p "$BIN/railwait"
    invoke notty
    expect_failure
    expect_output '폴더라서 설치 CLI로 교체할 수 없습니다.'
    passed

    new_case refuses-sudo
    SUDO=tester
    RUN_PATH="$WORK/stub-root:$STUB:$ORIGINAL_PATH"
    invoke notty
    expect_failure
    expect_output 'sudo 없이'
    [ ! -e "$BIN" ] || fail 'sudo 실행에서 설치 폴더를 만들었습니다.'
    passed

    new_case wget-fallback
    RUN_PATH="$WORK/restricted"
    invoke notty
    expect_success
    expect_installed "$BIN" railwait-linux-amd64
    expect_tmp_clean "$BIN"
    passed
}

if [ "$#" -eq 0 ]; then
    set -- sh dash bash
fi
RAN=0
for SH_NAME in "$@"; do
    if ! SH_PATH=$(command -v "$SH_NAME" 2>/dev/null); then
        echo "건너뜀: $SH_NAME 셸이 없습니다."
        continue
    fi
    PASSED=0
    run_suite
    echo "통과: ${SH_NAME} (${SH_PATH}) ${PASSED}개 사례 (wget 경로: ${WGET_KIND})"
    RAN=$((RAN + 1))
done
[ "$RAN" -gt 0 ] || {
    echo '실행할 셸이 없습니다.' >&2
    exit 1
}
