#!/usr/bin/env bash
#
# 로컬 패키지로 설치한 레일웨잇(railwait install --package)에 이 저장소의 커밋을 한 번에 반영한다.
#
#   1. 지정한 커밋을 임시 worktree에 꺼내 버전 기록·DB revision을 확인한다.
#   2. Compose의 build 정의대로 앱 이미지를 버전 태그로 빌드한다. 기존 :local 태그는 건드리지 않는다.
#      빌드한 이미지는 실행 사용자로 한 번씩 실행해 보고, 실패하면 설치를 건드리기 전에 멈춘다.
#   3. 일회성 Ed25519 키로 서명한 로컬 패키지를 만들고 개인키는 바로 지운다.
#   4. 같은 커밋의 호스트 CLI를 Docker 안에서 빌드해 업데이트 계획을 확인하고 적용한다.
#   5. CLI 상태, HTTP 응답, 화면 파일의 버전을 확인한다. 선택하면 KORAIL 공개 좌석 조회도 확인한다.
#
# 운영 .env와 비밀값을 읽거나 출력하지 않는다. 볼륨과 이전 이미지를 지우지 않으므로 이전 버전 이미지는
# 되돌리기용으로 남는다. macOS 기본 bash 3.2에서도 돌도록 연관 배열·mapfile·빈 배열 확장을 쓰지 않고,
# 명령 치환 안에 heredoc을 두지 않는다.

set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"

usage() {
    cat <<'EOF'
사용법: bash scripts/update-local-install.sh --path <설치 경로> [--ref <커밋>] [--dry-run] [--korail-smoke]

  --path <경로>    railwait install --package로 만든 설치 디렉터리 (필수)
  --ref <커밋>     반영할 커밋. 기본값은 HEAD이며 커밋하지 않은 변경은 들어가지 않습니다.
  --dry-run        이미지 빌드·패키지 생성·업데이트 계획 확인까지만 하고 적용하지 않습니다.
  --korail-smoke   적용 뒤 설치된 KORAIL 브라우저 어댑터로 공개 좌석 조회를 한 번 실행합니다.

환경변수:
  UV                 uv 실행 파일 경로. 지정하지 않으면 PATH에서 찾습니다.
  RAILWAIT_GO_IMAGE  CLI를 빌드할 Go 이미지. 기본값은 apps/cli/go.mod의 Go 버전입니다.
EOF
}

log() {
    printf '==> %s\n' "$*" >&2
}

die() {
    printf '오류: %s\n' "$*" >&2
    exit 1
}

install_arg=""
ref="HEAD"
dry_run=0
korail_smoke=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --path)
            [[ $# -ge 2 ]] || die "--path에 설치 경로가 필요합니다"
            install_arg="$2"
            shift 2
            ;;
        --ref)
            [[ $# -ge 2 ]] || die "--ref에 커밋이 필요합니다"
            ref="$2"
            shift 2
            ;;
        --dry-run)
            dry_run=1
            shift
            ;;
        --korail-smoke)
            korail_smoke=1
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            die "알 수 없는 인자입니다: $1"
            ;;
    esac
done
if [[ -z "$install_arg" ]]; then
    usage >&2
    exit 2
fi
install_dir="$(cd -- "$install_arg" 2>/dev/null && pwd)" || die "설치 경로를 찾지 못했습니다: $install_arg"
state_file="$install_dir/.railwait/install.json"
[[ -f "$state_file" ]] || die "설치 기록을 찾지 못했습니다: $state_file"

uv_bin="${UV:-$(command -v uv || true)}"
[[ -n "$uv_bin" && -x "$uv_bin" ]] \
    || die "uv가 필요합니다. https://docs.astral.sh/uv/ 에서 설치하거나 UV 환경변수로 경로를 지정하세요"
for tool in git docker curl; do
    command -v "$tool" >/dev/null || die "$tool 명령이 필요합니다"
done

# JSON·YAML 확인도 uv가 준비한 Python 3.12로 실행해 호스트 python3 버전에 기대지 않는다. 현재 폴더의
# Python 프로젝트 설정이 섞이지 않도록 프로젝트가 없는 / 에서 실행하므로 인자는 모두 절대 경로로 넘긴다.
py() {
    (cd / && PYTHONDONTWRITEBYTECODE=1 "$uv_bin" run --quiet --python 3.12 python "$@")
}

# 릴리스 도구와 같은 고정 의존성(PyYAML·cryptography)이 필요한 단계에 쓴다.
py_tools() {
    (cd / && PYTHONDONTWRITEBYTECODE=1 "$uv_bin" run --quiet --python 3.12 \
        --with-requirements "$source_dir/scripts/releases/requirements.txt" python "$@")
}

read_install_state() {
    py - "$1" <<'PY'
import json
import sys

state = json.load(open(sys.argv[1], encoding="utf-8"))
names = ("path", "release_source", "docker_context", "compose_project", "port",
         "release", "database_revision")
print("\x1f".join(str(state.get(name) or "") for name in names))
PY
}

# 버전 기록·DB revision을 확인하고 빌드할 이미지와 이미 있어야 하는 이미지를 한 줄씩 출력한다.
inspect_source() {
    py_tools - "$source_dir" "$current_release" "$current_revision" <<'PY'
import re
import sys
from pathlib import Path

import yaml

root, installed_release, installed_revision = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
sys.path.insert(0, str(root / "scripts" / "releases"))
import build_bundle  # noqa: E402

REFERENCE = re.compile(r"[a-z0-9][a-z0-9._/:@-]*")


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def semantic(value):
    return tuple(int(part) for part in value.split("."))


release = (root / "apps/web/src/shared/lib/appRelease.ts").read_text(encoding="utf-8")
version = re.search(r'\bversion: "(\d+\.\d+\.\d+)"', release)
released_on = re.search(r'\breleasedOn: "(\d{4}-\d{2}-\d{2})"', release)
changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
heading = re.search(r"^## (\d+\.\d+\.\d+) ", changelog, re.M)
if not version or not released_on or not heading:
    fail("appRelease.ts 또는 CHANGELOG.md에서 릴리스 버전을 읽지 못했습니다")
if heading[1] != version[1]:
    fail(f"appRelease.ts 버전({version[1]})과 CHANGELOG.md 맨 위 버전({heading[1]})이 다릅니다")
if build_bundle.VERSION.fullmatch(installed_release) and semantic(version[1]) <= semantic(installed_release):
    fail(f"반영할 버전({version[1]})이 설치된 버전({installed_release})보다 높지 않습니다. "
         "CONTRIBUTING.md의 '릴리스 버전 올리기'대로 버전을 올려 커밋한 뒤 다시 실행하세요")
try:
    revision = build_bundle.database_head(root)
except ValueError:
    fail("DB migration head를 하나로 정하지 못했습니다")
if revision != installed_revision:
    fail(f"DB revision이 {installed_revision}에서 {revision}(으)로 바뀌는 버전은 "
         "로컬 패키지 업데이트로 반영할 수 없습니다")
print("release", version[1], released_on[1])

builds = {}
external = set()
for service in yaml.safe_load((root / "compose.yml").read_text(encoding="utf-8"))["services"].values():
    if "test" in (service.get("profiles") or []):
        continue
    image = service["image"]
    if not REFERENCE.fullmatch(image):
        fail(f"처리할 수 없는 이미지 이름입니다: {image}")
    build = service.get("build")
    if build is None:
        external.add(image)
        continue
    if isinstance(build, str):
        build = {"context": build}
    spec = (build.get("context", "."), build.get("dockerfile", "Dockerfile"), build.get("target") or "-")
    if any(not part or any(character.isspace() for character in part) for part in spec):
        fail(f"{image}의 빌드 경로를 처리할 수 없습니다")
    if builds.setdefault(image, spec) != spec:
        fail(f"{image} 이미지의 빌드 정의가 서비스마다 다릅니다")
for image, spec in sorted(builds.items()):
    print("build", image, *spec)
for image in sorted(external - builds.keys()):
    print("external", image)
PY
}

read_plan() {
    py - "$1" <<'PY'
import json
import sys

plan = json.load(open(sys.argv[1], encoding="utf-8"))
print("allowed" if plan.get("allowed") is True else "blocked", plan.get("plan_id") or "-",
      plan.get("current_release_id") or "-", plan.get("target_release_id") or "-")
for reason in plan.get("blocked_reasons") or []:
    print("reason", reason)
PY
}

read_status() {
    py - "$1" <<'PY'
import json
import sys

status = json.load(open(sys.argv[1], encoding="utf-8"))
print(status.get("installation", {}).get("release") or "-",
      "healthy" if status.get("healthy") is True else "unhealthy")
for service in status.get("services") or []:
    print("service", service.get("service"), service.get("state"), service.get("health") or "-")
PY
}

# 내일 KST 낮 시간대의 공개 좌석 조회 한 건만 보낸다. 로그인·예약을 하지 않으며 token 값은
# 컨테이너 안에서만 읽고 출력하지 않는다.
korail_smoke_check() {
    docker --context "$docker_context" exec -i "$1" python - <<'PY'
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

travel_date = (datetime.now(timezone(timedelta(hours=9))) + timedelta(days=1)).date().isoformat()
body = json.dumps({
    "origin": "대전", "destination": "서울", "travel_date": travel_date,
    "departure_from": "12:00", "departure_to": "14:00", "passenger_count": 1,
}).encode("utf-8")
try:
    request = urllib.request.Request(
        "http://127.0.0.1:8001/v1/seat-snapshot", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + os.environ["KORAIL_BROWSER_ADAPTER_TOKEN"]},
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        trains = json.load(response).get("trains") or []
except urllib.error.HTTPError as error:
    raise SystemExit(f"KORAIL 조회 확인 실패: HTTP {error.code}")
except Exception as error:
    raise SystemExit(f"KORAIL 조회 확인 실패: {type(error).__name__}")
print(f"KORAIL 조회 확인: {travel_date} 대전→서울 12~14시 열차 {len(trains)}편")
PY
}

# Docker 29의 containerd 이미지 저장소는 같은 플랫폼 매니페스트를 공유하는 다른 이미지의 provenance 증명을
# docker image save 결과에 referrer 최상위 항목으로 덧붙인다. 레이어가 모두 캐시인 재빌드는 이전 이미지와
# 매니페스트가 같아지므로, 실행마다 다른 build-id 라벨로 config를 고유하게 만든다. 레이어 캐시는 그대로 쓴다.
build_image() {
    local tag="$1" context="$2" dockerfile="$3" target="$4"
    set -- --platform "$platform" --file "$source_dir/$context/$dockerfile" --tag "$tag" \
        --label "org.opencontainers.image.version=$version" \
        --label "org.opencontainers.image.revision=$commit" \
        --label "io.github.hostkimjang.railwait.build-id=$build_id"
    if [[ "$target" != - ]]; then
        set -- "$@" --target "$target"
    fi
    docker --context "$docker_context" build --quiet "$@" "$source_dir/$context" </dev/null
}

# 적용 단계는 기존 서비스를 멈춘 뒤 새 migration부터 실행하고 자동으로 되돌리지 않는다. 그래서 파일 권한·
# 설정·의존성 오류는 여기서 막는다. 앱 이미지는 모두 비root 기본 사용자로 실행되므로 그대로 실행해 본다.
smoke_check() {
    local label="$1" output
    shift
    if ! output="$(docker --context "$docker_context" run --rm --network none --read-only --tmpfs /tmp "$@" </dev/null 2>&1)"; then
        printf '%s\n' "$output" | tail -n 5 >&2
        die "$label 실행 점검이 실패했습니다. 설치는 바꾸지 않았습니다"
    fi
    printf '%s\n' "$output"
}

smoke_image() {
    local original="$1" tag="$2" heads head_lines
    case "$original" in
        rail-waitlist-api:local)
            smoke_check "$tag" --entrypoint python "$tag" \
                -c 'import rail_waitlist.main; import rail_waitlist.admin_auth.bootstrap' >/dev/null
            # migration은 /app의 설정과 migration 파일을 읽는다. DB 없이 head만 계산해 읽기 권한과 revision을 함께 본다.
            heads="$(smoke_check "$tag" --entrypoint alembic "$tag" heads)"
            head_lines="$(grep ' (head)$' <<<"$heads" || true)"
            [[ "$head_lines" == "$current_revision (head)" ]] \
                || die "${tag}의 migration head가 설치 DB revision($current_revision)과 다릅니다: $head_lines"
            ;;
        rail-waitlist-web:local)
            smoke_check "$tag" --tmpfs /var/run:uid=101,gid=101,mode=0755 \
                --tmpfs /var/cache/nginx:uid=101,gid=101,mode=0755 --entrypoint sh "$tag" \
                -ec 'nginx -t; test -r /usr/share/nginx/html/index.html' >/dev/null
            ;;
        rail-waitlist-korail-browser-adapter:local)
            smoke_check "$tag" --entrypoint python "$tag" -c 'import subprocess; import pydoll; import rail_waitlist.korail_sidecar.http; subprocess.run(["/usr/bin/google-chrome", "--version"], check=True, timeout=15)' >/dev/null
            ;;
        rail-waitlist-backup:local)
            smoke_check "$tag" --entrypoint sh "$tag" -ec 'command -v age; command -v pg_dump; test -x /scripts/backup-postgres.sh; test -x /scripts/restore-postgres.sh; test -x /scripts/update-backup.sh' >/dev/null
            ;;
        *)
            log "  $original 이미지는 실행 점검 항목이 없어 빌드 결과만 확인했습니다"
            return 0
            ;;
    esac
    log "  실행 점검 통과"
}

container_for() {
    docker --context "$docker_context" ps --quiet \
        --filter "label=com.docker.compose.project=$compose_project" \
        --filter "label=com.docker.compose.service=$1" </dev/null
}

check_http() {
    local endpoint="$1" code="" attempt=0
    while [[ "$attempt" -lt 12 ]]; do
        code="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 \
            "http://127.0.0.1:${port}${endpoint}" || true)"
        if [[ "$code" == 200 ]]; then
            return 0
        fi
        attempt=$((attempt + 1))
        sleep 5
    done
    die "http://127.0.0.1:${port}${endpoint} 응답이 ${code:-없음}입니다"
}

state_fields="$(read_install_state "$state_file")"
IFS=$'\x1f' read -r install_path release_source docker_context compose_project port \
    current_release current_revision <<<"$state_fields"
[[ "$release_source" == local_package ]] \
    || die "로컬 패키지로 설치한 경로만 이 스크립트로 업데이트합니다 (release_source=${release_source:-없음}). 공식 릴리스 설치는 웹의 앱 업데이트나 railwait update를 사용하세요"
[[ -n "$install_path" && -n "$docker_context" && -n "$compose_project" && -n "$current_release" \
    && -n "$current_revision" && "$port" =~ ^[0-9]+$ ]] \
    || die "설치 기록에서 경로·Docker context·포트·버전·DB revision을 읽지 못했습니다"
[[ "$(cd -- "$install_dir" && pwd -P)" == "$(cd -- "$install_path" && pwd -P)" ]] \
    || die "설치 기록의 경로($install_path)가 --path와 다릅니다"

docker_platform="$(docker --context "$docker_context" info --format '{{.OSType}}/{{.Architecture}}' </dev/null)" \
    || die "Docker context ${docker_context}에 연결하지 못했습니다"
case "$docker_platform" in
    linux/aarch64 | linux/arm64) platform=linux/arm64 ;;
    linux/x86_64 | linux/amd64) platform=linux/amd64 ;;
    *) die "지원하지 않는 Docker 플랫폼입니다: $docker_platform" ;;
esac
case "$(uname -s)" in
    Darwin) host_os=darwin ;;
    Linux) host_os=linux ;;
    *) die "이 스크립트는 macOS와 Linux에서 실행합니다" ;;
esac
case "$(uname -m)" in
    arm64 | aarch64) host_arch=arm64 ;;
    x86_64 | amd64) host_arch=amd64 ;;
    *) die "지원하지 않는 CPU입니다: $(uname -m)" ;;
esac
# CLI는 로컬 패키지의 CPU를 자신의 CPU와 비교하므로 Docker와 CPU가 다르면 미리 멈춘다.
[[ "${platform#linux/}" == "$host_arch" ]] \
    || die "Docker($platform)와 이 컴퓨터의 CPU($host_arch)가 달라 로컬 패키지 업데이트를 할 수 없습니다"

commit="$(git -C "$repo_root" rev-parse --verify --quiet "${ref}^{commit}")" || die "커밋을 찾지 못했습니다: $ref"
if [[ -n "$(git -C "$repo_root" status --porcelain)" ]]; then
    log "주의: 커밋하지 않은 변경은 반영하지 않습니다. 반영할 내용은 먼저 커밋하세요"
fi

work_dir=""
source_dir=""
seed_file=""
keep_work=0

cleanup() {
    local status=$?
    if [[ -n "$seed_file" && -e "$seed_file" ]]; then
        rm -f -- "$seed_file"
    fi
    if [[ -n "$source_dir" && -e "$source_dir" ]]; then
        git -C "$repo_root" worktree remove --force "$source_dir" >/dev/null 2>&1 \
            || printf '주의: 임시 worktree를 지우지 못했습니다: %s\n' "$source_dir" >&2
    fi
    if [[ -n "$work_dir" && -d "$work_dir" ]]; then
        if [[ "$keep_work" -eq 1 ]]; then
            printf '작업 확인용으로 새 CLI와 패키지를 남겼습니다: %s\n' "$work_dir" >&2
        else
            rm -rf -- "$work_dir"
        fi
    fi
    return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

temporary_root="${TMPDIR:-/tmp}"
# mktemp -d가 만든 0700 작업 폴더가 내용 전체를 다른 사용자로부터 막는다.
work_dir="$(mktemp -d "${temporary_root%/}/railwait-local-update.XXXXXX")"
cd -- "$work_dir"
source_dir="$work_dir/source"
log "커밋 ${commit:0:12} 기준으로 임시 worktree를 만듭니다"
# Docker COPY는 빌드 컨텍스트의 파일 권한을 그대로 이미지에 넣는다. 사용자의 umask가 077이면 비root 실행
# 사용자가 /app 파일을 읽지 못하므로, 체크아웃은 저장소 기본 권한(0644·0755)으로 고정한다.
(umask 022 && git -C "$repo_root" worktree add --quiet --detach "$source_dir" "$commit")

release_info="$(inspect_source)"
version=""
released_on=""
build_specs=""
external_images=""
while read -r kind first second third fourth; do
    case "$kind" in
        release)
            version="$first"
            released_on="$second"
            ;;
        build) build_specs="${build_specs}${first} ${second} ${third} ${fourth}"$'\n' ;;
        external) external_images="${external_images} ${first}" ;;
    esac
done <<<"$release_info"
[[ -n "$version" && -n "$build_specs" ]] || die "반영할 버전과 빌드할 이미지를 정하지 못했습니다"
log "반영할 버전 v$version ($released_on), 설치된 버전 v$current_release"
# CLI는 이미 있는 releases/<버전> 폴더를 새 번들과 비교해 다르면 멈춘다. 실패한 이전 작업이 남긴 폴더는
# 설치 기록이 쓰지 않는 것을 운영자가 확인하고 지우도록 빌드 전에 알린다.
[[ ! -e "$install_path/releases/$version" ]] \
    || die "이전 업데이트 작업이 남긴 $install_path/releases/$version 폴더가 있습니다. 설치 기록의 현재 버전이 v${current_release}인지 확인한 뒤 이 폴더를 지우고 다시 실행하세요"

# 패키지는 모든 운영 프로필의 이미지를 담는다. 빌드하지 않는 이미지는 미리 있어야 하므로 먼저 확인한다.
missing_images=""
for image in $external_images; do
    docker --context "$docker_context" image inspect "$image" >/dev/null 2>&1 </dev/null \
        || missing_images="${missing_images} ${image}"
done
[[ -z "$missing_images" ]] \
    || die "Docker context ${docker_context}에 없는 이미지가 있습니다:${missing_images}. docker --context $docker_context pull <이미지>로 받은 뒤 다시 실행하세요"

images_json="{"
separator=""
built_tags=""
build_id="$(py -c 'import secrets; print(secrets.token_hex(8))')"
while read -r image context dockerfile target; do
    [[ -n "$image" ]] || continue
    name="${image%:*}"
    name="${name##*/}"
    name="${name#rail-waitlist-}"
    tag="railwait-local-install-${name}:${version}"
    log "이미지 빌드: $tag"
    image_id="$(build_image "$tag" "$context" "$dockerfile" "$target")"
    log "  ${image_id}"
    smoke_image "$image" "$tag"
    images_json="${images_json}${separator}\"${image}\":\"${tag}\""
    separator=","
    built_tags="${built_tags} ${tag}"
done <<<"$build_specs"
printf '%s}\n' "$images_json" >"$work_dir/images.json"

seed_file="$work_dir/signing-seed"
(umask 077 && py -c 'import base64, os; print(base64.b64encode(os.urandom(32)).decode("ascii"))' >"$seed_file")
package_dir="$work_dir/package"
trust_key="$work_dir/trust-key.pub"
log "로컬 패키지 생성: v$version ($platform). 이미지 묶음을 저장하는 데 몇 분 걸립니다"
py_tools "$source_dir/scripts/releases/build_local_package.py" \
    --docker-context "$docker_context" --platform "$platform" --version "$version" \
    --output "$package_dir" --signing-key-file "$seed_file" \
    --public-key-output "$trust_key" --images-json "$work_dir/images.json"
rm -f -- "$seed_file"
seed_file=""

go_version="$(sed -nE 's/^go ([0-9]+\.[0-9]+)(\.[0-9]+)?$/\1/p' "$source_dir/apps/cli/go.mod")"
[[ -n "$go_version" ]] || die "apps/cli/go.mod에서 Go 버전을 읽지 못했습니다"
go_image="${RAILWAIT_GO_IMAGE:-golang:${go_version}}"
cli="$work_dir/railwait"
log "호스트 CLI 빌드: $host_os/$host_arch ($go_image)"
git -C "$repo_root" archive --format=tar "${commit}:apps/cli" \
    | docker --context "$docker_context" run --rm -i \
        -e CGO_ENABLED=0 -e GOOS="$host_os" -e GOARCH="$host_arch" -e GOTOOLCHAIN=local \
        -e HOME=/tmp -e GOCACHE=/tmp/go-build -e GOMODCACHE=/tmp/go-mod \
        "$go_image" sh -c 'mkdir /tmp/src && tar -xf - -C /tmp/src && cd /tmp/src && go build -trimpath -ldflags "-X main.version=$1" -o /tmp/railwait ./cmd/railwait && cat /tmp/railwait' \
        railwait-build "local-${commit:0:12}" >"$cli"
chmod 700 "$cli"
"$cli" version >/dev/null || die "새로 빌드한 CLI를 실행하지 못했습니다"

log "업데이트 계획 확인"
"$cli" update --path "$install_path" --package "$package_dir" --trust-key "$trust_key" \
    --dry-run --json >"$work_dir/plan.json"
plan_info="$(read_plan "$work_dir/plan.json")"
read -r decision plan_id plan_current plan_target <<<"$plan_info"
if [[ "$decision" != allowed ]]; then
    printf '업데이트 계획이 차단되었습니다.\n' >&2
    sed -n 's/^reason /  - /p' <<<"$plan_info" >&2
    exit 1
fi
log "계획 $plan_id: v$plan_current → v$plan_target 적용 가능"
if [[ "$dry_run" -eq 1 ]]; then
    log "--dry-run이므로 적용하지 않습니다. 빌드한 이미지 태그는 남겨 둡니다:$built_tags"
    exit 0
fi

# 적용 중 실패하거나 중단되면 작업 기록을 확인할 새 CLI와 패키지를 남긴다.
keep_work=1
log "업데이트 적용: 새 작업 차단 → 기존 작업 종료 대기 → 암호화 백업 → 교체 → 확인 → 재개"
if ! "$cli" update --path "$install_path" --package "$package_dir" --trust-key "$trust_key" \
    --target "$plan_target" --plan "$plan_id"; then
    die "업데이트가 끝나지 않았습니다. 위에 표시된 작업 ID로 \"$cli\" status --path \"$install_path\" --job <작업 ID>를 실행해 상태를 확인하고, docs/OPERATIONS.md의 '로컬 패키지 업데이트가 멈췄을 때' 절차로 복구하세요"
fi
keep_work=0

log "설치 상태 확인"
"$cli" status --path "$install_path" --json >"$work_dir/status.json"
status_info="$(read_status "$work_dir/status.json")"
read -r status_release status_health <<<"$status_info"
if [[ "$status_release" != "$version" || "$status_health" != healthy ]]; then
    sed -n 's/^service /  - /p' <<<"$status_info" >&2
    die "업데이트 뒤 설치 상태가 v$status_release · ${status_health}입니다"
fi

for endpoint in /healthz /readyz /; do
    check_http "$endpoint"
done
log "HTTP 확인: /healthz /readyz / 모두 200"

web_container=""
read -r web_container <<<"$(container_for web)" || true
[[ -n "$web_container" ]] || die "web 컨테이너를 찾지 못했습니다"
docker --context "$docker_context" exec "$web_container" \
    grep -rqF "version:\"${version}\",releasedOn:\"${released_on}\"" /usr/share/nginx/html </dev/null \
    || die "web 컨테이너의 화면 파일에서 v$version 표시를 찾지 못했습니다"
log "화면 파일 확인: v$version ($released_on)"

if [[ "$korail_smoke" -eq 1 ]]; then
    adapter_container=""
    read -r adapter_container <<<"$(container_for korail-browser-adapter)" || true
    [[ -n "$adapter_container" ]] \
        || die "KORAIL 브라우저 어댑터 컨테이너를 찾지 못했습니다 (experimental-rail 프로필 필요)"
    log "KORAIL 공개 좌석 조회 1건 확인 (로그인·예약 없음)"
    korail_smoke_check "$adapter_container" >&2 \
        || die "업데이트는 끝났지만 KORAIL 조회 확인이 실패했습니다"
fi

log "완료: v$plan_current → v$version. 새 이미지 태그:$built_tags"
log "이전 버전 이미지는 되돌리기용으로 Docker에 남아 있습니다"
