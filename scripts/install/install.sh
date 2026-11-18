#!/bin/sh
# Railwait 설치 CLI를 공식 GitHub Release에서 받아 검증·설치하고 설치 마법사를 시작합니다.
#
# 사용법(macOS·Linux):
#   curl -fsSL https://github.com/hostkimjang/korail-auto-waitlist/releases/latest/download/install.sh | sh
#   curl -fsSL https://github.com/hostkimjang/korail-auto-waitlist/releases/latest/download/install.sh | sh -s -- --custom --port 9090
#
# 스크립트 전체를 main 함수로 감싸 마지막 줄에서 한 번만 호출합니다. 내려받기가 중간에 끊겨
# 일부만 전달되면 아무 작업도 실행하지 않습니다. sudo를 쓰지 않으며 셸 설정 파일도 바꾸지 않습니다.
# Docker 준비, 릴리스 서명 검증, 관리자 입력은 설치 CLI(railwait install)가 맡습니다.

RAILWAIT_REPOSITORY_URL='https://github.com/hostkimjang/korail-auto-waitlist'

railwait_usage() {
    cat <<EOF
Railwait 설치 스크립트

사용법:
  curl -fsSL ${RAILWAIT_REPOSITORY_URL}/releases/latest/download/install.sh | sh
  curl -fsSL ${RAILWAIT_REPOSITORY_URL}/releases/latest/download/install.sh | sh -s -- <railwait install 인자>

하는 일:
  1. OS(macOS·Linux)와 CPU(arm64·x86_64)에 맞는 설치 CLI와 .sha256 파일을 받습니다.
  2. SHA-256이 맞을 때만 \$RAILWAIT_BIN_DIR/railwait(기본 ~/.local/bin/railwait)에 설치합니다.
  3. 이어서 'railwait install'을 실행합니다. 넘긴 인자는 그대로 전달합니다.
     대화형 터미널이 없으면 실행하지 않고 다음에 실행할 명령을 안내합니다.

환경변수:
  RAILWAIT_VERSION   설치 CLI 실행 파일의 버전만 고정합니다(예: 1.4.4).
                     'railwait install'은 이 값과 관계없이 항상 최신 stable 앱 릴리스를 설치합니다.
  RAILWAIT_BIN_DIR   설치 CLI를 둘 폴더(절대경로, 기본 \$HOME/.local/bin).
                     앱 설치 폴더 안은 사용할 수 없습니다.
  RAILWAIT_DOWNLOAD_BASE
                     자산을 받을 폴더 URL(테스트용). https 또는 127.0.0.1·localhost의 http만 허용하며,
                     지정하면 RAILWAIT_VERSION보다 우선합니다.

설치 마법사의 옵션은 설치 후 'railwait install --help'로 확인하세요.
EOF
}

railwait_say() {
    printf '%s\n' "$*"
}

railwait_fail() {
    printf 'railwait 설치 오류: %s\n' "$*" >&2
    exit 1
}

# 사용자가 그대로 복사해 실행할 수 있도록 각 인자를 작은따옴표 리터럴로 표시합니다.
railwait_quote() {
    railwait_quoted=''
    for railwait_word in "$@"; do
        railwait_escaped=$(printf '%s' "$railwait_word" | sed "s/'/'\\\\''/g")
        railwait_quoted="$railwait_quoted '$railwait_escaped'"
    done
    printf '%s' "${railwait_quoted# }"
}

railwait_detect_os() {
    railwait_kernel=$(uname -s 2>/dev/null) || railwait_fail '운영체제를 확인하지 못했습니다.'
    case $railwait_kernel in
        Darwin) RAILWAIT_OS=darwin ;;
        Linux) RAILWAIT_OS=linux ;;
        MINGW* | MSYS* | CYGWIN* | Windows_NT)
            railwait_fail "Windows에서는 PowerShell에서 다음 명령으로 설치하세요: irm ${RAILWAIT_REPOSITORY_URL}/releases/latest/download/install.ps1 | iex" ;;
        *) railwait_fail "지원하지 않는 운영체제입니다(${railwait_kernel:-알 수 없음}). macOS와 Linux만 지원합니다." ;;
    esac
}

railwait_detect_arch() {
    railwait_machine=$(uname -m 2>/dev/null) || railwait_fail 'CPU 종류를 확인하지 못했습니다.'
    case $railwait_machine in
        arm64 | aarch64) RAILWAIT_ARCH=arm64 ;;
        x86_64 | amd64) RAILWAIT_ARCH=amd64 ;;
        *) railwait_fail "지원하지 않는 CPU입니다(${railwait_machine:-알 수 없음}). arm64(aarch64)와 x86_64(amd64)만 지원합니다." ;;
    esac
    # Rosetta 2로 실행 중인 x86_64 셸이어도 Apple Silicon에는 arm64 실행 파일을 설치합니다.
    if [ "$RAILWAIT_OS" = darwin ] && [ "$RAILWAIT_ARCH" = amd64 ]; then
        if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null)" = 1 ]; then
            RAILWAIT_ARCH=arm64
        fi
    fi
}

railwait_select_base() {
    if [ -n "${RAILWAIT_DOWNLOAD_BASE:-}" ]; then
        case $RAILWAIT_DOWNLOAD_BASE in
            https://?* | http://127.0.0.1:* | http://127.0.0.1/* | http://localhost:* | http://localhost/*) ;;
            *) railwait_fail 'RAILWAIT_DOWNLOAD_BASE는 https 주소이거나 127.0.0.1·localhost의 http 주소여야 합니다.' ;;
        esac
        RAILWAIT_BASE=${RAILWAIT_DOWNLOAD_BASE%/}
        return
    fi
    if [ -n "${RAILWAIT_VERSION:-}" ]; then
        railwait_version=${RAILWAIT_VERSION#v}
        case $railwait_version in
            '' | *[!0-9.]* | .* | *. | *..*) railwait_fail 'RAILWAIT_VERSION 형식이 올바르지 않습니다(예: 1.4.4).' ;;
        esac
        railwait_dots=$(printf '%s' "$railwait_version" | tr -cd '.')
        [ "$railwait_dots" = '..' ] || railwait_fail 'RAILWAIT_VERSION 형식이 올바르지 않습니다(예: 1.4.4).'
        RAILWAIT_BASE="${RAILWAIT_REPOSITORY_URL}/releases/download/v${railwait_version}"
        return
    fi
    RAILWAIT_BASE="${RAILWAIT_REPOSITORY_URL}/releases/latest/download"
}

railwait_download() {
    railwait_url=$1
    railwait_output=$2
    case $railwait_url in
        https://*) railwait_proto='=https' ;;
        *) railwait_proto='=http' ;;
    esac
    if command -v curl >/dev/null 2>&1; then
        curl --proto "$railwait_proto" --proto-redir "$railwait_proto" -fsSL --retry 3 \
            --connect-timeout 20 -o "$railwait_output" "$railwait_url"
    elif command -v wget >/dev/null 2>&1; then
        if [ "$railwait_proto" = '=https' ] && wget --version >/dev/null 2>&1; then
            # GNU wget만 --https-only를 지원합니다. BusyBox wget은 아래 기본 호출을 씁니다.
            wget -q --https-only -O "$railwait_output" "$railwait_url"
        else
            wget -q -O "$railwait_output" "$railwait_url"
        fi
    else
        railwait_fail 'curl 또는 wget이 필요합니다.'
    fi
}

railwait_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        railwait_digest=$(sha256sum "$1") || return 1
    elif command -v shasum >/dev/null 2>&1; then
        railwait_digest=$(shasum -a 256 "$1") || return 1
    else
        railwait_fail 'SHA-256 확인 도구(sha256sum 또는 shasum)가 필요합니다.'
    fi
    printf '%s' "${railwait_digest%% *}"
}

# .sha256 파일에서 자산 이름이 정확히 일치하는 줄이 하나일 때만 그 해시를 사용합니다.
railwait_expected_sha256() {
    railwait_sum_file=$1
    railwait_asset=$2
    railwait_matches=0
    railwait_expected=''
    railwait_cr=$(printf '\r')
    while IFS= read -r railwait_line || [ -n "$railwait_line" ]; do
        railwait_line=${railwait_line%"$railwait_cr"}
        railwait_hash=${railwait_line%%[ ]*}
        case ${railwait_line#"$railwait_hash"} in
            "  $railwait_asset" | " *$railwait_asset")
                railwait_matches=$((railwait_matches + 1))
                railwait_expected=$railwait_hash
                ;;
        esac
    done <"$railwait_sum_file"
    if [ "$railwait_matches" -ne 1 ]; then
        railwait_fail "${railwait_asset}.sha256 파일에서 ${railwait_asset} 항목을 정확히 하나 찾지 못했습니다."
    fi
    case $railwait_expected in
        *[!0-9a-f]*) railwait_fail "${railwait_asset}.sha256 파일의 해시 형식이 올바르지 않습니다." ;;
    esac
    [ ${#railwait_expected} -eq 64 ] || railwait_fail "${railwait_asset}.sha256 파일의 해시 형식이 올바르지 않습니다."
    printf '%s' "$railwait_expected"
}

# 설치 CLI의 기본 앱 설치 폴더(platform.installDir)와 같은 규칙입니다.
railwait_app_dir() {
    if [ "$RAILWAIT_OS" = darwin ]; then
        printf '%s' "$HOME/Library/Application Support/Railwait"
    elif [ -n "${XDG_DATA_HOME:-}" ] && [ "${XDG_DATA_HOME#/}" != "$XDG_DATA_HOME" ]; then
        printf '%s' "${XDG_DATA_HOME%/}/railwait"
    else
        printf '%s' "$HOME/.local/share/railwait"
    fi
}

railwait_physical_dir() {
    if [ -d "$1" ]; then
        (cd "$1" 2>/dev/null && pwd -P) || printf '%s' "$1"
    else
        printf '%s' "$1"
    fi
}

# 설치 CLI를 앱 설치 폴더 밖에 두도록 경로 문자열과 실제 경로를 모두 확인합니다.
railwait_reject_app_dir() {
    railwait_app=$(railwait_app_dir)
    railwait_app_physical=$(railwait_physical_dir "$railwait_app")
    for railwait_candidate in "$RAILWAIT_TARGET_DIR" "$(railwait_physical_dir "$RAILWAIT_TARGET_DIR")"; do
        for railwait_forbidden in "$railwait_app" "$railwait_app_physical"; do
            case "$railwait_candidate/" in
                "$railwait_forbidden/"*)
                    railwait_fail "설치 CLI를 앱 설치 폴더($railwait_app) 안에 둘 수 없습니다. RAILWAIT_BIN_DIR를 바꿔 주세요." ;;
            esac
        done
    done
}

railwait_prepare_bin_dir() {
    RAILWAIT_TARGET_DIR=${RAILWAIT_BIN_DIR:-$HOME/.local/bin}
    case $RAILWAIT_TARGET_DIR in
        /*) ;;
        *) railwait_fail 'RAILWAIT_BIN_DIR는 절대경로여야 합니다.' ;;
    esac
    RAILWAIT_TARGET_DIR=${RAILWAIT_TARGET_DIR%/}
    [ -n "$RAILWAIT_TARGET_DIR" ] || railwait_fail '루트 폴더(/)에는 설치 CLI를 둘 수 없습니다.'
    railwait_reject_app_dir
    mkdir -p "$RAILWAIT_TARGET_DIR" || railwait_fail "설치 폴더를 만들지 못했습니다: $RAILWAIT_TARGET_DIR"
    railwait_reject_app_dir
    RAILWAIT_TARGET="$RAILWAIT_TARGET_DIR/railwait"
    if [ -d "$RAILWAIT_TARGET" ]; then
        railwait_fail "$RAILWAIT_TARGET 경로가 폴더라서 설치 CLI로 교체할 수 없습니다."
    fi
}

railwait_cleanup() {
    if [ -n "${RAILWAIT_STAGED:-}" ]; then
        rm -f "$RAILWAIT_STAGED"
    fi
    if [ -n "${RAILWAIT_WORK:-}" ]; then
        rm -rf "$RAILWAIT_WORK"
    fi
    RAILWAIT_STAGED=''
    RAILWAIT_WORK=''
}

railwait_path_hint() {
    railwait_physical=$(railwait_physical_dir "$RAILWAIT_TARGET_DIR")
    case ":${PATH:-}:" in
        *":$RAILWAIT_TARGET_DIR:"* | *":$RAILWAIT_TARGET_DIR/:"* | *":$railwait_physical:"*) return ;;
    esac
    railwait_say ''
    railwait_say "참고: $RAILWAIT_TARGET_DIR 폴더가 PATH에 없습니다. 이 스크립트는 셸 설정 파일을 바꾸지 않습니다."
    railwait_say "      'railwait' 이름만으로 실행하려면 셸 설정 파일에 다음 줄을 직접 추가하세요."
    case ${SHELL:-} in
        */fish) railwait_say "        fish_add_path $(railwait_quote "$RAILWAIT_TARGET_DIR")" ;;
        *) railwait_say "        export PATH=$(railwait_quote "$RAILWAIT_TARGET_DIR"):\"\$PATH\"" ;;
    esac
    railwait_say "      그 전까지는 전체 경로로 실행하세요: $(railwait_quote "$RAILWAIT_TARGET")"
}

main() {
    case ${1:-} in
        -h | --help)
            railwait_usage
            return 0
            ;;
    esac
    set -u
    umask 022
    if [ -z "${HOME:-}" ]; then
        railwait_fail 'HOME 환경변수가 필요합니다.'
    fi
    if [ "$(id -u 2>/dev/null)" = 0 ] && [ -n "${SUDO_USER:-}" ]; then
        railwait_fail 'sudo 없이 일반 사용자 권한으로 실행하세요.'
    fi

    railwait_detect_os
    railwait_detect_arch
    railwait_select_base
    railwait_prepare_bin_dir
    railwait_asset="railwait-${RAILWAIT_OS}-${RAILWAIT_ARCH}"

    RAILWAIT_WORK=''
    RAILWAIT_STAGED=''
    trap railwait_cleanup EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    RAILWAIT_WORK=$(mktemp -d "${TMPDIR:-/tmp}/railwait-install.XXXXXX") ||
        railwait_fail '임시 폴더를 만들지 못했습니다.'

    if [ -n "${RAILWAIT_DOWNLOAD_BASE:-}" ]; then
        railwait_say "다운로드 위치를 RAILWAIT_DOWNLOAD_BASE로 지정했습니다: $RAILWAIT_BASE"
    elif [ -n "${RAILWAIT_VERSION:-}" ]; then
        railwait_say "설치 CLI 버전을 ${RAILWAIT_VERSION#v}(으)로 고정했습니다. 앱은 항상 최신 stable 릴리스로 설치합니다."
    fi
    railwait_say "${railwait_asset} 설치 CLI를 내려받는 중입니다."
    railwait_download "$RAILWAIT_BASE/$railwait_asset" "$RAILWAIT_WORK/$railwait_asset" ||
        railwait_fail "$railwait_asset 파일을 내려받지 못했습니다: $RAILWAIT_BASE/$railwait_asset"
    railwait_download "$RAILWAIT_BASE/$railwait_asset.sha256" "$RAILWAIT_WORK/$railwait_asset.sha256" ||
        railwait_fail "$railwait_asset.sha256 파일을 내려받지 못했습니다: $RAILWAIT_BASE/$railwait_asset.sha256"

    railwait_want=$(railwait_expected_sha256 "$RAILWAIT_WORK/$railwait_asset.sha256" "$railwait_asset") || exit 1
    railwait_got=$(railwait_sha256 "$RAILWAIT_WORK/$railwait_asset") || railwait_fail 'SHA-256을 계산하지 못했습니다.'
    if [ "$railwait_got" != "$railwait_want" ]; then
        railwait_fail 'SHA-256이 일치하지 않아 설치를 중단했습니다. 기존 설치 CLI는 바꾸지 않았습니다.'
    fi
    railwait_say 'SHA-256 확인 완료.'

    # 같은 폴더에 임시 파일을 만든 뒤 rename으로 교체해, 중간에 끊겨도 기존 실행 파일이 깨지지 않게 합니다.
    RAILWAIT_STAGED=$(mktemp "$RAILWAIT_TARGET_DIR/.railwait-install.XXXXXX") ||
        railwait_fail "설치 폴더에 쓸 수 없습니다: $RAILWAIT_TARGET_DIR"
    if ! {
        cp "$RAILWAIT_WORK/$railwait_asset" "$RAILWAIT_STAGED" &&
            chmod 755 "$RAILWAIT_STAGED" &&
            mv -f "$RAILWAIT_STAGED" "$RAILWAIT_TARGET"
    }; then
        railwait_fail "설치 CLI를 $RAILWAIT_TARGET 위치에 설치하지 못했습니다."
    fi
    RAILWAIT_STAGED=''
    railwait_cleanup
    trap - EXIT HUP INT TERM

    railwait_say "설치 CLI를 설치했습니다: $RAILWAIT_TARGET"
    railwait_path_hint

    railwait_next=$(railwait_quote "$RAILWAIT_TARGET" install "$@")
    if ! (: </dev/tty) 2>/dev/null; then
        railwait_say ''
        railwait_say '대화형 터미널이 없어 설치 마법사를 시작하지 않았습니다. 터미널에서 다음 명령을 실행하세요.'
        railwait_say "  $railwait_next"
        return 0
    fi
    railwait_say ''
    railwait_say "설치 마법사를 시작합니다: $railwait_next"
    exec "$RAILWAIT_TARGET" install "$@" </dev/tty
}

main "$@"
