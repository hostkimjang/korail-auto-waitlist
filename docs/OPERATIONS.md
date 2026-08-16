# 설치·운영 가이드

이 문서는 레일웨잇을 직접 운영할 때 필요한 명령과 확인 방법을 정리합니다. 처음 설치한다면 [시작하기](GETTING_STARTED.md)를 먼저 읽으세요.

## 운영 전 확인

- 실제 비밀값은 저장소 루트의 `.env`에만 둡니다.
- `.env`, `secrets/`, 백업, 로그, 브라우저 인증 상태는 Git에 올리지 않습니다.
- Compose 설정은 항상 `config --quiet`로 확인합니다.
- 기본 포트는 로컬 컴퓨터에만 열립니다.
- 인터넷에 공개할 때는 HTTPS와 보안 쿠키를 함께 설정합니다.
- Linux 명령은 Bash와 Docker Compose v2를 기준으로 하며, Docker는 현재 사용자로 실행할 수 있어야 합니다.
- 실험 Chromium 이미지는 `linux/amd64`와 `linux/arm64` 빌드 대상을 지원합니다. 별도 Chromium 자동 검증은 현재 `linux/amd64`에서 실행합니다. 기본 adapter는 내부 Xvfb의 Pydoll non-headless 모드이며 noVNC listener를 시작하지 않습니다. ARM64 네이티브 배포의 전체 서비스 health, sidecar readiness와 실제 읽기 조회는 OCI에서 확인했습니다.

## 기본 명령

저장소 루트에서 실행합니다.

| 작업 | Linux Bash | Windows PowerShell |
| --- | --- | --- |
| 설정 검사 | `bash ./scripts/ops.sh config` | `./scripts/ops.ps1 config` |
| 현재 `.env.example` 기반 전체 프로필 빌드·시작 | `bash ./scripts/ops.sh experimental` | `./scripts/ops.ps1 experimental` |
| 실험 프로필을 사용하지 않는 기본 구성 빌드·시작 | `bash ./scripts/ops.sh up` | `./scripts/ops.ps1 up` |
| 상태 확인 | `bash ./scripts/ops.sh status` | `./scripts/ops.ps1 status` |
| 로그 보기 | `bash ./scripts/ops.sh logs` | `./scripts/ops.ps1 logs` |

Linux에서는 같은 명령을 `make config`, `make experimental`, `make status`, `make logs`로도 실행할 수 있습니다. 운영 관련 Make target은 `ops.sh`로 위임하므로 같은 단계적 종료와 실패 복구 계약을 사용합니다. `COMPOSE_PROFILES=experimental-rail`을 제거하고 실험 서비스를 사용하지 않는 환경에서만 `make up`을 사용합니다.

직접 Compose를 사용할 수도 있습니다.

```console
docker compose -f compose.yml config --quiet
docker compose -f compose.yml up -d --build
docker compose -f compose.yml ps
```

서비스를 중지할 때는 다음 명령을 사용합니다.

```console
docker compose -f compose.yml down
```

Linux의 `bash ./scripts/ops.sh down`은 모든 Compose 프로필을 대상으로 하며, 백업이나 복원이 실행 중이면 중단합니다. 데이터를 보존하려면 `down -v`를 실행하거나 Docker 볼륨을 삭제하지 마세요.

## 환경 설정

전체 형식과 설명은 [.env.example](../.env.example)에 있습니다.

반드시 설정할 값:

- `POSTGRES_PASSWORD`
- `SECRET_ENCRYPTION_KEY`
- `AUTH_SESSION_SECRET`
- `KORAIL_BROWSER_ADAPTER_TOKEN`
- `SRT_PROVIDER_ADAPTER_TOKEN`

`.env.example`은 두 좌석 감시 sidecar를 기본 구성에 포함하므로 내부 인증 token도 비워 둘 수 없습니다. 새 설치의 다섯 값 생성과 기존 값 보존 기준은 [시작하기](GETTING_STARTED.md#2-필수-값-설정하기)를 따릅니다.

처음 관리자 계정을 만들 때만 사용하는 값:

- `AUTH_INITIAL_REGISTRATION_ENABLED`

실제 시간표 검색에 필요한 값:

- 공공 시간표: `TAGO_SERVICE_KEY`

선택 기능에 필요한 값:

- Web Push: `WEBPUSH_VAPID_PRIVATE_KEY`, `WEBPUSH_VAPID_PUBLIC_KEY`, `WEBPUSH_VAPID_SUBJECT`
- 모니터링: `GRAFANA_ADMIN_PASSWORD`
- 암호화 백업: `BACKUP_AGE_IDENTITY`, `BACKUP_AGE_RECIPIENT`

`KORAIL_RESERVATION_ONCE_ENABLED`는 KORAIL 단발 예매 전체 기능 스위치이며 지연승낙 동의를 대신하지
않습니다. 정확한 `이용안내/확인`과 예약 결과의 `안내메세지/확인`은 기본 허용 목록으로 처리합니다. 그 밖의
공식 구조 단일 `확인`을 닫고, 예매 버튼을 누른 뒤 나타난 정확한 `이용안내`·지연승낙에서 `네`를 자동으로
선택하려면 개인 운영자가 예약 안내와 지연배상 제한을 수락한 뒤
`KORAIL_RESERVATION_DIALOG_AUTO_ACTION_ENABLED=true`를 설정합니다. 기본값은 `false`이고 사용자·대기별 값이
아닌 이 인스턴스의 모든 KORAIL 예매 시도에 적용되는 sidecar 전역값입니다. 값을 바꾼 뒤 비밀값을 출력하지
않는 `docker compose -f compose.yml --profile experimental-rail config --quiet`를 통과시키고 현재 프로필의
전체 서비스를 운영 스크립트로 다시 빌드·생성합니다. 기존예약 선택, 다중 대화상자, 제목·본문·버튼 구조를
확인할 수 없는 대화상자에는 이 설정을 적용하지 않습니다.

공식 앱 인계는 일반 선택 기능과 달리 경로별 실기기 QA를 통과한 배포에서만 켭니다. 기본값은 모두 꺼져 있습니다.

- 코레일+ 예매: `VITE_KORAIL_BOOKING_DEEPLINK_ENABLED`, `VITE_KORAIL_BOOKING_VALIDATED_VERSION`
- 코레일+ 승차권: `VITE_KORAIL_TICKET_DEEPLINK_ENABLED`, `VITE_KORAIL_TICKET_VALIDATED_VERSION`
- SRT 예매 홈: `VITE_SRT_MAIN_DEEPLINK_ENABLED`, `VITE_SRT_MAIN_VALIDATED_VERSION`
- SRT 승차권 확인: `VITE_SRT_TICKET_DEEPLINK_ENABLED`, `VITE_SRT_TICKET_VALIDATED_VERSION`

각 enabled 값은 대응하는 검증 버전이 함께 있어야 동작합니다. 코레일+ 예매·승차권, SRT 예매 홈·승차권 확인을 독립적으로 켭니다. SRT ticket은 `srapp://main`에 고정 문자열 extra `btnNo=2`만 전달합니다. 이 경로는 웜 실행에서 `승차권 확인` 화면을 열어도 기존 WebView 목록을 다시 조회하지 않을 수 있으므로, 결제 카드에는 방금 예약이 비어 보일 때 하단 `승차권 확인`을 한 번 더 누르라는 안내를 표시합니다. 공개 refresh extra가 없는 상태에서 두 번째 intent나 내부 클릭을 자동화하지 않습니다. 검증된 intent는 사용자 클릭에서 외부 anchor로 열어 앱이 없을 때 공식 HTTPS fallback이 PWA를 덮지 않고 Custom Tab에서 열리게 합니다. SRT main은 웜 실행에서 현재 SRT 화면을 유지할 수 있으므로 목적 화면 행렬을 통과하기 전 켜지 않습니다. 이 값들은 Vite 빌드 입력이므로 변경 후 전체 Compose 이미지를 다시 빌드·생성해야 합니다. PWA가 설치 앱 버전을 확인하는 기능은 아니며 앱 업데이트가 확인되면 해당 플래그를 먼저 끄고 재검증합니다. 절차와 합격 기준은 [Android 공식 앱 인계 검증](ANDROID_APP_HANDOFF_QA.md)을 따릅니다.

딥링크 QA 값을 Bash의 임시 `export NAME=value`나 PowerShell 프로세스의 `$env:NAME="value"`에만 넣으면 그 프로세스가 끝난 뒤 다음 build에서 Compose 기본값 `false`가 적용됩니다. 같은 장비에서 재부팅·재빌드 뒤에도 유지할 QA 값은 커밋하지 않는 로컬 `.env`에 기록합니다. 빌드 직전 `docker compose --profile experimental-rail config --format json`의 `services.web.build.args`를 확인하고, 빌드 뒤 배포 JavaScript에 enabled 값과 대응 검증 버전이 들어갔는지 확인합니다. 기존 Chrome 탭과 실행 중 PWA는 이미 로드한 JavaScript를 자동 교체하지 않으므로 배포 뒤 완전히 새로고침하거나 닫고 다시 엽니다.

비밀값을 바꿀 때는 기존 데이터와의 호환성을 먼저 확인하세요. 특히 `SECRET_ENCRYPTION_KEY`를 잃어버리면 저장된 철도 계정과 알림 비밀값을 복구할 수 없습니다.

## 첫 관리자 계정

관리자 계정이 하나도 없을 때만 새 계정을 만들 수 있습니다.

1. `.env`에서 `AUTH_INITIAL_REGISTRATION_ENABLED=true`로 시작합니다.
2. 화면에서 관리자 계정을 만듭니다.
3. 값을 다시 `false`로 바꿉니다.
4. API 서비스를 다시 만듭니다.

```console
docker compose -f compose.yml up -d --force-recreate api
```

여러 사용자를 위한 가입이나 초대 기능은 없습니다.

## 다른 기기에서 접속하기

### 로컬 컴퓨터

기본 주소는 `http://127.0.0.1`입니다. 기본 설정에서는 같은 네트워크의 다른 기기에서도 바로 접속할 수 없습니다.

### Tailscale

개인용으로 운영한다면 Tailnet 내부에서만 접속할 수 있도록 구성하는 방식을 권장합니다.

```console
tailscale serve --bg http://127.0.0.1:80
```

앱 화면은 현재 요청이 Tailscale Serve, Funnel, 일반 HTTPS 또는 다른 reverse proxy를 통과했는지 신뢰성 있게 판정하지 않습니다. 따라서 접속 경로를 확인하지 않은 `Tailscale 보호됨` 배지를 표시하지 않습니다. 실제 보호 여부는 `tailscale serve status`, Tailnet ACL과 외부 노출 설정으로 확인하세요.

HTTPS 주소를 사용하면 `AUTH_COOKIE_SECURE=true`로 설정하고, 허용 출처도 실제 접속 주소와 일치시킵니다.

### 공개 도메인

인터넷에 공개하려면 다음 항목을 함께 준비해야 합니다.

- 실제 도메인과 DNS
- 80·443 포트 접근
- `SITE_ADDRESS`
- Caddy의 외부 바인드 주소
- `AUTH_ALLOWED_ORIGINS`, `CORS_ORIGINS`
- `AUTH_COOKIE_SECURE=true`

관리자 등록 기능을 닫지 않은 상태로 인터넷에 공개하지 마세요.

## 업데이트와 재배포

코드, Dockerfile, Compose 또는 실행 이미지를 바꿨다면 일부 서비스만 이전 버전으로 남겨 두지 말고 모두 같은 버전으로 다시 만듭니다. 실행 중인 예약 호출을 보호하려면 raw `docker compose up -d --force-recreate` 대신 운영 스크립트를 사용합니다.

| 작업 | Linux Bash | Windows PowerShell |
| --- | --- | --- |
| 실행 작업 요약 | `bash ./scripts/ops.sh drain-status` | `./scripts/ops.ps1 drain-status` |
| 현재 `.env.example` 기반 안전한 전체 재배포 | `bash ./scripts/ops.sh experimental` | `./scripts/ops.ps1 experimental` |
| 실험 프로필을 사용하지 않는 기본 구성 재배포 | `bash ./scripts/ops.sh up` | `./scripts/ops.ps1 up` |
| 재배포 뒤 상태 | `bash ./scripts/ops.sh status` | `./scripts/ops.ps1 status` |

`drain-status`는 worker별 진행 중 작업 수와 task 이름만 보여 주며 task 인자나 계정 정보를 출력하지 않습니다. 결과는 그 순간의 참고값이고, 확인 직후 새 작업이 시작될 수 있으므로 `0건`만을 안전 조건으로 사용하지 않습니다.

`up`은 설정 검증과 전체 build 뒤 다음 순서로 재배포합니다.

1. `proxy`를 멈춰 새 외부 요청을 차단합니다.
2. `scheduler`를 멈춰 새 주기 작업 발행을 차단합니다.
3. Celery worker에 정상 종료를 요청하고 실행 중 작업이 끝날 때까지 기다립니다. worker의 5분 종료 유예 동안 KORAIL·SRT sidecar는 계속 실행됩니다.
4. API의 진행 중 요청을 종료한 뒤 나머지 서비스를 같은 revision으로 강제 재생성합니다.

KORAIL·SRT 실험 프로필이 실행 중이면 기본 `up`은 중단하고 Linux에서는 `bash ./scripts/ops.sh experimental`, Windows에서는 `./scripts/ops.ps1 experimental`을 사용하도록 안내합니다. 이 명령도 같은 drain 순서를 적용한 뒤 sidecar를 포함한 프로필 전체를 다시 만듭니다. 백업·복원이 실행 중일 때는 재배포하지 않습니다.

현재 `experimental` 명령은 `compose.yml`의 기본 KORAIL 구성을 사용합니다. 이 구성은 내부 Xvfb에서
non-headless Chrome을 실행하지만 x11vnc·websockify와 host 6080 포트는 시작하지 않습니다. 따라서 실제
운영에 `compose.korail-gui.yml`을 추가할 필요가 없습니다.

화면을 직접 확인해야 하는 짧은 진단에서만 [KORAIL 브라우저 화면 진단](../apps/api/KORAIL_BROWSER_DIAGNOSTICS.md)에
따라 noVNC overlay로 adapter 한 개를 다시 만듭니다. `--no-deps`를 유지해 이미 drain·재생성한 DB, Redis,
API와 worker를 다시 건드리지 않습니다.

```bash
docker compose -f compose.yml -f compose.korail-gui.yml --profile experimental-rail config --quiet
docker compose -f compose.yml -f compose.korail-gui.yml --profile experimental-rail up -d --force-recreate --no-deps korail-browser-adapter
```

진단이 끝나면 `compose.yml`만 사용해 adapter를 다시 만들고 비밀번호 파일을 제거합니다. 마지막에는
adapter의 `DISPLAY=:99`, X 접근, `/readyz`와 전체 profile health를 다시 확인합니다.

단계적 종료 중 Docker 오류가 발생하면 새 이미지 재생성을 강행하지 않고, 그 전에 실행 중이던 proxy·scheduler·worker·API 컨테이너를 다시 시작한 뒤 오류를 반환합니다. 이 자동 복구도 실패했다는 경고가 나오면 플랫폼별 운영 스크립트의 `status`로 중간 종료 상태를 확인하고 같은 프로필의 기존 컨테이너를 먼저 복구합니다.

수동 `docker compose up -d --force-recreate`가 종료 유예 중 끊기면 Compose가 만든 `<기존 ID>_<서비스명>` 교체 컨테이너와 기존 stateful service가 동시에 남을 수 있습니다. 특히 같은 volume을 공유하는 Redis가 둘 이상 실행된 상태를 정상으로 간주하지 않습니다. project·service label로 중복을 확인하고 기존 container와 생성된 교체본을 구분해 단일 instance로 복구한 뒤 `rdb_last_bgsave_status`와 `aof_last_write_status`가 `ok`인지 확인하고, 그 사이 Redis 오류를 관측한 worker를 다시 시작합니다. volume을 삭제하거나 `down -v`로 복구하지 않습니다. 가능하면 이 상태를 만들지 않도록 위 운영 스크립트를 사용합니다.

worker, API와 두 sidecar에는 5분의 `stop_grace_period`가 적용됩니다. 이는 KORAIL 브라우저 클라이언트의 최대 70초 drain과 provider 요청 timeout 뒤 정리를 위한 상한입니다. 이 시간에 `Ctrl+C`를 반복하거나 `docker kill`로 강제 종료하면 Celery late ACK와 DB reconciliation이 있더라도 실행 중 브라우저 세션 자체는 보존되지 않습니다. 유예를 초과한 작업이 있으면 재배포를 강행하지 말고 worker·sidecar 로그와 예약 상태를 먼저 확인합니다.

확인할 항목:

- 데이터베이스 마이그레이션과 로그 초기화 서비스가 정상 종료했는지
- 장기 실행 서비스가 `healthy`인지
- `http://127.0.0.1/`이 열리는지
- API의 `/healthz`, `/readyz`가 정상인지
- 최근 로그에 반복되는 오류가 없는지

Linux의 `experimental` 명령은 `docker compose up --wait --wait-timeout 180`을 사용하므로 성공 종료 시 선택 프로필의 장기 서비스가 실행·healthy 상태입니다. 제한 시간 안에 준비되지 않으면 서비스 상태 표를 출력하고 원래 오류 코드를 반환합니다. 이 판정은 migration·log-init의 성공 종료나 실제 운영사 접근 성공을 대신하지 않으므로 `status`, 일회성 작업의 `exited 0`, 각 sidecar의 `/readyz`와 실제 읽기 조회 결과를 별도로 기록합니다.

문서나 공개 이미지 파일만 바꾼 경우에는 전체 서비스 재배포가 필요하지 않습니다.

## 선택 프로필

### 철도사 실험 기능

| Linux Bash | Windows PowerShell |
| --- | --- |
| `bash ./scripts/ops.sh experimental` | `./scripts/ops.ps1 experimental` |

이 명령은 KORAIL Chromium과 SRT 연동 서비스를 포함한 `experimental-rail` 프로필 전체를 다시 빌드하고, 진행 중 worker와 API를 먼저 drain한 뒤 생성합니다.

KORAIL adapter의 기본 non-headless 가상 화면은 외부에 공개되지 않습니다. noVNC overlay는 위 "업데이트와
재배포" 절처럼 화면이 꼭 필요한 짧은 진단에서만 적용합니다.

`.env.example`은 `experimental-rail` 프로필과 KORAIL·SRT 좌석 확인·백그라운드 감시 설정을 기본 활성화합니다. adapter token을 각각 32바이트 이상 무작위 값으로 채운 뒤 위 명령으로 전체 프로필을 실행해야 하며, 코드 설정이나 환경변수가 누락된 직접 실행은 fail-closed합니다. 예매 시도는 기본적으로 꺼진 별도 운영사 gate, 로그인 확인을 마친 철도 계정과 작업별 정책이 추가로 필요합니다.

실험 기능은 안정적인 성공을 보장하지 않습니다. 보호 안내나 호출 제한이 나타나면 반복 실행하지 말고 중단 시간을 지키세요.

#### ARM64 네이티브 Chromium 판정

2026년 8월 9일 OCI ARM64 네이티브 환경에서 KORAIL Chromium 이미지 빌드와 외부 요청 없는 151개 fixture를 통과했습니다. 첫 전체 배포에서는 Chrome 사용자 네임스페이스 생성이 기본 seccomp에서 거부됐고, 공식 Playwright v1.55.0 seccomp 프로필 적용 뒤에는 `cap_drop: ALL`로 비워진 capability bounding set 때문에 sandbox의 안전한 빈 디렉터리 `chroot`가 다시 거부됐습니다. adapter에만 `SYS_CHROOT`를 되돌린 뒤 sandboxed Chrome, sidecar `/readyz`, migration·log-init과 11개 장기 서비스 health, 로컬 접속을 모두 확인했습니다.

운영 구성은 Playwright v1.55.0 seccomp 프로필과 `SYS_CHROOT` 하나를 KORAIL adapter에만 적용합니다. `pwuser` 비루트 실행, 읽기 전용 루트 파일시스템, `cap_drop: ALL`, `no-new-privileges`는 유지하며, Chrome을 `--no-sandbox`로 실행하거나 `seccomp=unconfined` 또는 `SYS_ADMIN`을 주지 않습니다. 이전 headless 서울→부산 조회 3회는 모두 HTTP 423이었고 `wait_result` 단계의 `marker_unauthorized_tool` 보호 신호를 확인했습니다. 같은 OCI·ARM64 이미지의 Pydoll GUI/non-headless 1회 조회가 열차 13개를 정상 판독한 뒤, 기본 배포를 원격 화면 listener가 없는 내부 Xvfb non-headless 모드로 바꿨습니다. 전체 프로필 재배포 후 실제 배포된 sidecar HTTP 경계에서도 같은 열차 13개와 좌석 상태를 정상 판독했습니다. 이는 검증된 실행 모드를 기본 배포와 일치시킨 결과이며 보호 우회를 보장하지 않습니다. 보호 신호가 나타난 실행은 cooldown 중 재시도하지 않습니다.

같은 날 커밋 `fe4b364`를 OCI Ubuntu 20.04 ARM64의 별도 경로에 clean clone하고, 운영 `.env`를 복제하되 Compose 프로젝트명·host 포트·origin과 빈 named volume을 격리했습니다. README의 `bash ./scripts/ops.sh experimental` 최초 설치는 exit 0으로 끝났고 migration·log-init, 11개 장기 서비스 health, 웹 200, 최초 관리자 등록 뒤 gate 잠금과 새 세션 로그인까지 통과했습니다. 실제 읽기 호출은 재시도 없이 KORAIL 서울→부산 13개, SRT 수서→부산 12개를 반환했습니다. 검증 뒤 clean 프로젝트는 `stop`으로 정지하고 4개 named volume은 증거로 보존했으며, 기존 운영 프로젝트가 계속 healthy임을 확인했습니다. 이 결과는 OCI ARM64 범위이며 네이티브 Ubuntu `linux/amd64`, 외부 알림과 백업 복원 검증을 대신하지 않습니다.

### 모니터링

| Linux Bash | Windows PowerShell |
| --- | --- |
| `bash ./scripts/ops.sh monitoring` | `./scripts/ops.ps1 monitoring` |

Prometheus와 Grafana를 실행합니다. Grafana는 별도 관리자 비밀번호를 사용합니다.

### 내부 ntfy

| Linux Bash | Windows PowerShell |
| --- | --- |
| `bash ./scripts/ops.sh ntfy` | `./scripts/ops.ps1 ntfy` |

기본 접근 정책은 `deny-all`입니다. 외부에 그대로 공개하지 마세요.

## 철도 계정

설정 화면에서 KORAIL과 SRT 계정을 각각 저장할 수 있습니다.

- 로그인 방식과 아이디를 선택합니다.
- 비밀번호는 로그인 확인 요청에만 사용합니다.
- 성공한 값만 암호화해 저장합니다.
- 화면에는 마스킹된 아이디와 확인 상태만 표시합니다.

인증이 만료되면 해당 운영사의 예매 시도를 중단하고 다시 확인하도록 안내합니다. 비밀번호와 쿠키는 로그에 남기지 않습니다.

API는 시작할 때 활성 철도 계정의 로그인 session을 예열하고, 이후 30초마다 sidecar의 비밀값 없는 session 상태를
확인합니다. 같은 계정 generation의 `READY` session이 유효기간을 120초보다 많이 남겨 두고 있으면 아무 요청도
보내지 않습니다. session이 없거나 오래됐거나 계정 generation이 다르거나, sidecar 재시작으로 메모리 session이
사라졌거나, 남은 시간이 120초 이하이면 예열합니다. KORAIL은 기존 session을 공식 same-origin 요청으로 한 번
확인해 유효하면 검증 시각과 로컬 재사용 기한을 갱신하고, 유효하지 않으면 폐기한 뒤 새 로그인을 한 번만 시도합니다.
KORAIL `loginCheck`는 인증된 JSON만 성공 근거로 사용합니다. 명확한 403·429는 보호·호출 제한으로 처리하고,
200 비JSON이나 파싱할 수 없는 응답은 보호라고 단정하지 않지만 keepalive 성공으로도 인정하지 않습니다.
자격증명을 제출한 새 로그인 흐름에서만 제한된 시간 동안 실제 로그아웃 버튼 DOM을 추가로 확인합니다.

예열 실패는 60초부터 시작해 최대 900초까지 지수 backoff하며, 보호 응답은 처음부터 900초 동안 다시 시도하지
않습니다. `auth_required`·`provider_blocked`로 저장된 같은 계정 revision은 한 번만 복구를 시도하므로 비밀번호를
수정하거나 다시 로그인 확인하기 전까지 반복 로그인하지 않습니다. 이 유지 작업은 로그인 성공을 보장하거나
보호 조치를 우회하지 않습니다. 로그에서 `Provider runtime prewarm completed`와 outcome을 확인할 수 있지만, 실제
철도사 계정의 장시간 유지와 sidecar 재시작 뒤 재예열은 운영 환경에서 별도로 확인해야 합니다.

계정 상태가 실제 `provider_blocked`이면 같은 운영사의 좌석 관측도 sidecar 요청을 만들지 않고 인증 대기로
전환합니다. 900초 보호 backoff 중에는 계정 저장이나 컨테이너 재시작으로 반복 로그인을 유도하지 마세요. 현재
credential generation의 단일 재확인이 성공하면 관련 작업은 즉시 다시 스케줄됩니다. `auth_required`만으로는
로그인이 필요 없는 공개 좌석 관측을 전역 중단하지 않습니다.

## 알림 채널

지원하는 채널:

- Web Push
- Telegram
- Discord
- HTTPS Webhook

Web Push를 사용하려면 같은 실행에서 만든 VAPID private/public key 쌍과 실제 연락 가능한 subject를 설정해야 합니다. public key가 비어 있거나 잘못되면 기기 연결이 실패하고, private key가 비어 있거나 잘못됐거나 두 키가 같은 쌍이 아니면 실제 발송이 실패합니다. 생성과 `.env` 입력 방법은 [시작하기](GETTING_STARTED.md#web-push를-사용할-때선택)를 따릅니다.

VAPID 키 쌍은 일반 배포나 앱 업데이트 때 회전하지 않습니다. 불가피하게 교체하면 연결했던 모든 브라우저와 설치형 PWA에서 OS 알림을 껐다가 다시 켜 새 public key로 재구독하고, 기기별 시험 전송을 확인합니다. `localhost`가 아닌 다른 기기에서는 HTTPS 주소로 접속해야 Web Push를 연결할 수 있습니다.

채널을 저장한 뒤 설정 화면의 테스트 전송을 사용하세요. 설정 화면의 성공은 요청이 접수됐다는 뜻이며, 실제 기기나 외부 서비스에서 수신했는지는 직접 확인해야 합니다.

실제 상태 알림은 노선만 보내지 않고, 전이를 만든 후보를 정확히 연결할 수 있을 때 운영사·열차번호·날짜·실제 출도착 시각·좌석 등급과 예매 흐름을 함께 보냅니다. 결제보류 종료 알림은 공식 확인에 사용한 결제기한과 감시 복귀 여부를 유지합니다. Webhook은 같은 정보를 구조화된 필드로 받고, Telegram·Discord·Web Push는 사람이 읽을 수 있는 같은 한글 요약을 사용합니다. 오래된 데이터처럼 후보를 정확히 연결할 근거가 없으면 임의 열차를 고르지 않고 노선과 상태만 표시합니다.

Web Push는 Chrome·Edge·설치된 모바일 PWA마다 별도의 구독으로 연결합니다. 연결되지 않은 기기에서는 인증 뒤 모든 화면에 비차단 `OS 알림 켜기` 행동을 표시합니다. 사용자가 이 버튼을 누르면 설정 화면을 거치지 않고 브라우저 권한 요청, 서비스 워커 준비, 구독과 서버 채널 저장을 순서대로 진행합니다. 브라우저가 권한과 새 Push 구독에 직접 사용자 행동을 요구하므로 페이지 load effect에서 승인창을 강제로 열지 않습니다. 권한이 차단된 경우에는 코드로 반복 요청하지 않고 사이트 권한 변경을 안내합니다. 앱에서 현재 기기의 OS 알림을 명시적으로 끄면 그 브라우저 구독과 대응하는 서버 채널만 비활성화·해제하고 전역 연결 안내도 억제하며, 다른 기기의 활성 구독은 유지합니다. 설정 화면은 현재 기기의 연결 상태와 전체 활성 기기 수를 구분해 표시합니다. 설정 화면의 `시험`은 현재 기기 한 곳으로 보내고, 실제 대기 상태 변화는 연결된 모든 활성 기기로 각각 보냅니다. 한 push endpoint가 만료되어 영구 실패하더라도 해당 기기 채널만 비활성화하고 다른 기기의 발송은 계속합니다.

Web Push 알림을 누르면 외부 철도사 주소가 아니라 동일 출처의 레일웨잇 PWA를 우선 찾습니다. 실행 중인 창이 있으면 focus하고, 백그라운드 창이 전면에 보이지 않으면 PWA 범위의 시작 화면으로 navigate한 뒤 다시 focus합니다. navigate가 성공해도 재초점한 창이 visible이 아니면 성공으로 오인하지 않고 `openWindow`로 PWA를 다시 전면 전환합니다. 종료 중인 창의 focus·navigate가 실패하거나 창이 없는 경우도 같은 열기 경로를 사용합니다. 온라인 navigation은 새 `index.html`을 먼저 받아 같은 배포의 해시 bundle과 함께 열고, 네트워크가 실제로 실패할 때만 캐시된 app shell을 사용합니다. `/assets/`의 존재하지 않는 이전 해시 파일은 SPA 문서로 대체하지 않고 404로 처리합니다. 로그인 상태와 최신 대기 데이터는 항상 API 응답을 기다립니다. 앱이 화면에 떠 있을 때 Push가 도착하면 서비스 워커가 비밀값 없는 힌트만 전달하고, 화면은 API의 최신 대기 상태를 다시 읽은 뒤 `실시간 알림`을 갱신합니다.

2026년 8월 7일 Android 16/API 36 에뮬레이터에서는 설치형 PWA를 백그라운드로 두고 Android 설정 앱을 연 상태에서 서비스 워커 합성 알림을 눌렀을 때 기존 `SameTaskWebApkActivity`가 전면으로 복귀하는 것을 확인했습니다. 이는 `notificationclick`의 기존-client 복귀 경로 검증이며, 실제 push service 전달·완전 종료된 PWA 콜드 실행·갤럭시 폴드7 제조사 동작은 별도 실기기 항목으로 남깁니다.

앱을 사용 중이면 Android·iPhone·iPad 모두 같은 `실시간 알림` surface가 safe area 아래에 8초간 간략 팝업으로 나타납니다. `자세히`를 누르면 전체 목록을 펼칩니다. 8초가 지나면 간략 미리보기만 숨고 좌석 발견·현재 예매 진행·결제·인증처럼 직접 닫아야 하는 알림은 접힌 건수 안에 남습니다. 반면 미리보기의 X나 펼친 카드·그룹의 닫기는 해당 알림을 실제로 제거하고, 같은 브라우저에서 동일하거나 더 오래된 revision이 재접속 뒤 복원되지 않도록 제한된 ledger에 기록합니다. 사용자가 닫지 않은 현재 상태와 같은 작업의 더 최신 revision은 정상적으로 복원·표시합니다. 공식 확인 시각이 같아도 다른 진단 revision이 들어오면 현재 표시 알림은 새 내용으로 교체하지만, 이미 닫은 알림은 같은 시각 revision으로 다시 열지 않습니다. 이 화면 알림은 modal이 아니므로 페이지 스크롤, 현재 입력과 가상 키보드, 하단 탐색을 잠그지 않습니다.

iOS·iPadOS Web Push는 16.4 이상에서 사용자가 홈 화면에 설치한 PWA에 한해 지원됩니다. 어느 화면에서든 전역 `OS 알림 켜기` 버튼을 누른 사용자 행동 안에서 권한을 먼저 요청한 뒤 구독을 만들며, Apple Developer Program은 필요하지 않습니다. 홈 화면에 설치하지 않은 Safari 탭이나 기능 감지에 실패한 환경에서는 OS 알림 연결을 완료로 표시하지 않습니다.

Android 알림창 수신과 화면 위 팝업 알림은 별도 항목입니다. 팝업 표시 여부는 브라우저 또는 설치된 PWA의 알림 중요도, `화면에 팝업 표시`, 소리·진동, 방해 금지, 제조사 알림 정책과 알림 쿨다운의 영향을 받습니다. Web Push 코드만으로 높은 중요도의 알림 채널을 강제할 수 없습니다.

레일웨잇은 좌석 발견·예매 진행·결제·인증처럼 시간이 중요한 상태에 Web Push `Urgency: high`를 전달합니다. 설정 화면의 시험 알림도 실제 중요 상태와 같은 `status: seat_found` envelope를 사용해 같은 전달·표시 경로를 확인합니다. 서비스 워커는 이 상태에 진동·지속 표시 힌트를 요청하며, 같은 대기의 `좌석 발견 → 예매 진행` 갱신에서도 진동·재알림 힌트를 유지합니다. 웹 코드는 알림 소리를 직접 지정하지 않습니다. 전달 우선순위와 진동 힌트는 Android 알림 채널을 `긴급`으로 바꾸거나 소리·진동·화면 위 팝업을 보장하지 않으며, 최종 동작은 브라우저가 만든 채널과 운영체제·사용자 설정이 결정합니다.

Chrome Android는 사이트별 Web Push 채널을 `IMPORTANCE_DEFAULT`로 생성합니다. 이 등급은 소리는 낼 수 있지만 Android가 화면 위 팝업에 요구하는 `IMPORTANCE_HIGH`는 아닙니다. 따라서 앱 설정의 소리·진동·팝업이 모두 켜져 있고 알림창 수신도 정상인데 화면 위 팝업만 나오지 않는 경우, 레일웨잇 PWA 코드에서 더 올릴 수 있는 채널 우선순위가 없습니다. Web Push `Urgency`는 전달 시점만 조정하며 이 Android 채널 등급으로 전달되지 않습니다.

Android 알림창에는 도착하지만 화면 위 팝업이 없을 때는 다음 순서로 확인합니다. 삼성 One UI처럼 메뉴 이름이 다른 기기에서는 방금 받은 레일웨잇 알림을 길게 눌러 해당 카테고리 설정으로 바로 들어가는 방법이 가장 정확합니다.

1. 앱 알림 화면에서 `팝업`과 소리·진동이 허용되어 있는지 확인합니다.
2. 화면 아래 `알림 카테고리`를 열고 실제 레일웨잇 알림이 들어온 카테고리를 선택합니다.
3. 해당 카테고리를 `무음`이 아닌 알림 방식으로 두고, 중요도 또는 화면 위 표시가 제공되면 `긴급`·`팝업 표시`를 선택합니다. Android 8 이상에서 화면 위 팝업은 높은 중요도의 채널이어야 합니다.
4. 방해 금지를 끄고 화면을 잠금 해제한 상태에서 한 건만 다시 보냅니다. Android 15 이상에서는 짧은 시간에 반복된 알림을 최대 약 2분간 약화하는 알림 쿨다운도 시험 중에는 꺼서 비교합니다.
5. 여전히 알림창에만 남으면 Chrome 또는 설치된 PWA가 만든 채널의 중요도가 높은지 확인합니다. 웹 앱은 이미 생성된 Android 채널 중요도를 코드로 덮어쓸 수 없습니다.

위 설정이 모두 켜져 있는데도 같은 현상이면 PWA의 플랫폼 경계로 판정합니다. 레일웨잇은 별도 APK를 제공하지 않으므로 알림창 수신을 기준으로 사용하고, 앱을 보고 있는 동안에는 아래의 foreground 간략 팝업으로 상태를 확인합니다.

Apple도 일반 알림의 배너·알림센터·잠금화면 표시와 Focus 적용을 사용자와 운영체제가 결정합니다. PWA에는 네이티브 iOS의 Time Sensitive·Critical interruption level을 요청하는 표준 옵션이 없고 Web Push `Urgency`도 화면 우선순위가 아닙니다. 따라서 백그라운드 Apple 배너를 제품 코드로 강제하지 않으며, 앱 사용 중에는 레일웨잇의 foreground 간략 팝업을 사용합니다.

Webhook은 HTTPS 주소만 허용합니다. 사설망 주소, 이 컴퓨터를 가리키는 주소, 링크 로컬 주소, 클라우드 메타데이터 주소로의 요청은 차단합니다.

## 로그와 상태 확인

실시간 로그:

```console
docker compose -f compose.yml logs -f --tail=200
```

특정 서비스만 볼 수도 있습니다.

```console
docker compose -f compose.yml logs -f --tail=200 api worker maintenance-worker notification-worker srt-provider-adapter korail-browser-adapter
```

서비스별 파일 로그는 `logs/<service>/current.log`에 기록됩니다. 파일은 자동으로 회전하며 `logs/README.md`만 Git에 포함됩니다.
조회 lifecycle 메시지의 검증된 `event`, `request_id`, `provider_call_id`는 JSON 최상위 필드에도 복제됩니다.
`request_id`는 API·worker가 시작한 내부 sidecar 호출 한 번, `provider_call_id`는 cache miss 뒤 만들어진 실제
운영사 호출 후보 한 번을 뜻합니다. gate에서 폐기되면 I/O 없이도 ID가 존재하므로 실제 접근 여부는
`provider_query_started`로 판별합니다. singleflight에서는 여러 `request_id`가 같은 `provider_call_id`에
연결되는 것이 정상입니다.
두 값은 운영 추적 전용 임시 UUID이므로 metric label이나 인증·lease 식별자로 사용하지 않습니다.

알림 전달에 사용하는 URL은 path 자체에 자격정보가 포함될 수 있습니다. 애플리케이션이 관리하는 파일 로그와
container console에서는 `httpx`·`httpcore` 요청 레코드를 내보내지 않고, 닫힌 전달 성공·실패 분류만
기록합니다. 원시 URL 노출이 의심되면 다음 순서로 대응합니다.

1. 영향받은 Telegram·Discord·일반 webhook 자격정보를 공급자에서 먼저 회전합니다.
2. 수정 이미지를 전체 재빌드·재생성한 뒤 새 파일 로그와 `docker compose logs`에서 transport 레코드가
   생성되지 않는지 비밀값을 출력하지 않는 건수 검사로 확인합니다.
3. 로컬 회전 파일, Docker 로그, 외부 수집기와 백업 사본은 보존 의무를 확인한 뒤 승인된 절차로 정리합니다.
   로그 삭제는 복구가 어려운 작업이므로 대상과 보존 정책을 확인하기 전에는 실행하지 않습니다.

SRT의 accountless 읽기 조회에서 `event=provider_queue_entered`는 SRTrain이 공식 접속 대기를 시작했다는 뜻이고,
`event=provider_queue_released`는 대기 통과와 NetFunnel 완료 통지가 성공해 운영사 요청을 계속한다는
뜻입니다. `provider_queue_waiting_count_changed`는 검증된 대기 인원이 실제로 달라질 때만 INFO로 남습니다.
세 event의 같은 `provider_call_id`를 따라가면 vendor가 stdout에 출력하는 ID 없는 안내와 구분할 수 있습니다.
인증 예약 흐름도 같은 queue helper를 사용하지만 현재 read-only correlation 범위 밖이므로 이 필드는
`unavailable`이며, 예약 로그를 accountless 조회 lifecycle에 합치지 않습니다.
다만 `event=provider_call_timed_out`가 먼저 기록되면 동기 provider 작업은 백그라운드에서 마무리될 수
있습니다. 이때
`phase=provider_io upstream_still_running=true`이면 실제 종료 여부를
`event=provider_call_finished_after_timeout`에서 확인합니다. `phase=provider_gate_wait`는
`provider_call_abandoned_before_start`로 끝날 수 있고 이때 late 종료 event는 없습니다. 늦은 성공은 이미
timeout된 응답을 바꾸지 않지만 같은 실제 작업의 짧은 cache에는 저장됩니다.
로그에는 대기 인원·경과시간·고정된 결과 분류만 넣고 NetFunnel key와 응답 원문은 넣지 않습니다. 이
제한시간에는 로컬 provider gate 대기도 포함되므로, 실제 운영사 I/O 시작 여부는 같은 구간의
`event=provider_query_started`로 구분합니다. gate에서 만료되거나 모든 waiter가 사라진 작업에는 이 시작
event가 없어야 합니다.

기본 timeout 순서는 SRT 실제 조회 60초 < sidecar HTTP 90초, KORAIL main/sidecar 읽기 조회 80초 < sidecar
HTTP 90초입니다. 내부 deadline은 gate 대기를 포함하고 남은 KORAIL budget도 sidecar로 전달합니다. 값을
조정할 때도 실제 provider budget < 내부 HTTP timeout < 300초 execution lease 관계를 유지합니다.
KORAIL browser 작업이 deadline 취소에 즉시 응답하지 않는 경우에도 호출자는 bounded timeout으로 끝나지만,
해당 inflight owner와 drain은 실제 작업이 terminal이 될 때까지 소유권을 유지합니다. 이때 늦은 성공은 cache에
반영하지 않으며 caller timeout만으로 provider cooldown을 열지 않습니다.

SRT sidecar client는 accountless `observe`·`timetable-overlay`·`timetable-search`를 보내기 전에 인증된
`read_only_call_id` 등록을 먼저 확인합니다. 원 호출이 90초 외부 timeout이나 취소로 끝나도 client는 등록 ID를
버리지 않고 request-scoped status를 poll합니다. 상태의 의미는 다음과 같습니다.

- `pending`: 원 HTTP handler 또는 연결된 SRTrain provider call이 아직 끝나지 않았습니다.
- `terminal`: handler와 연결 provider call이 모두 끝났고 300초 bounded tombstone 안에서 확인됐습니다.
- `unknown`: 등록된 적이 없거나 tombstone이 만료돼 종료를 증명할 수 없습니다.

terminal tombstone은 만료 시간과 별개로 최근 4096개까지만 유지합니다. 한도를 넘어 제거된 ID도 `unknown`으로
닫혀 client가 성공으로 오판하지 않습니다.

status poll의 header에는 원 `request_id`가 아닌 새 correlation ID를 사용합니다. poll timeout, transport 오류,
401·5xx, malformed 응답과 같은 instance의 `unknown`은 모두 fail-closed로 재시도합니다. sidecar instance ID가
바뀐 경우에만 이전 process와 그 thread가 함께 종료된 것으로 보고 정리합니다. worker drain이 취소되더라도
terminal 확인까지 취소 전파를 보류하므로 `group_runtime`은 그 전에 execution lease를 명시적으로 release하지
않습니다. 이는 sidecar 전체 drain이 아니라 해당 client가 사전 등록한 읽기 호출만 기다리는 계약입니다. instance
교체 판정은 현재 Compose처럼 고정 내부 origin에 SRT sidecar가 단일 replica인 경우에만 안전합니다. 다중 replica나
load balancer를 도입하기 전에는 instance affinity와 terminal 저장소를 별도로 설계해야 합니다.

인증된 구형 직접 호출은 lifecycle header 없이도 호환 목적으로 수용하지만 request-scoped drain 대상이 아닙니다.
worker와 API에서 raw sidecar HTTP를 만들지 말고 canonical `SrtProviderAdapterClient`를 사용하세요.

계속되는 `event=provider_sidecar_drain_status_unavailable`은 상태를 확인할 수 없어 lease 정리가 대기 중이라는
뜻입니다. sidecar 인증 token·health·네트워크를 확인하되 worker를 반복 강제 종료하지 마세요. 등록 뒤 원 요청이
도착하지 않으면 sidecar는 60초 뒤 terminal tombstone으로 닫고, 전송 전 등록 자체가 실패한 client는 원 provider
요청을 보내지 않습니다.

현재 DB execution lease는 300초 고정이며 drain 중 자동 renewal은 없습니다. 따라서 SRTrain thread 또는 status
장애가 300초를 넘는 경우 명시적 release는 지연돼도 lease 자연 만료를 막지는 못합니다. sidecar의 provider gate는
같은 process의 실제 SRT 조회를 직렬화하지만 DB fencing 연장과 동일하지 않습니다. 또한 이 terminal 계약은
accountless 읽기 3개 endpoint만 다루며 로그인·예약·예약 확인 thread에는 적용되지 않습니다.

KORAIL은 SRT와 같은 공식 접속 대기를 기다려 통과하는 흐름이 아닙니다. 실제 cache-miss 조회는
`event=provider_query_started`와 `event=provider_query_completed`로 경계를 확인합니다. NetFunnel 등
보호 표면이 감지되면 `outcome=provider_access_restricted`로 조회를 중단하고 provider-wide cooldown을
적용하며, 보호 화면을 우회하거나 같은 요청에서 계속 진행하지 않습니다. 두 sidecar의 이 구조화 로그는
서비스별 파일과 Docker stdout/stderr에 함께 남습니다.

2026년 8월 13일 SRT sidecar 파일 로그 표본에서는 기존 8초 caller timeout 88건이 모두 실제 provider의
late success로 끝났고, timeout 뒤 완료까지 중앙값 2.455초·최대 11.621초였습니다. 한 대표 흐름도 공식 queue
통과 뒤 전체 8.485초에 성공해 caller보다 약 0.485초 늦었습니다. 이는 외부 30초 HTTP 실패가 아니라 내부
budget이 정상 queue 시간을 먼저 끊은 사례였습니다. 이에 먼저 기본 budget을 25/35초로 정렬하고, caller
deadline으로 source cooldown을 열지 않으며, 시작 전 만료 작업을 폐기하는 계약을 추가했습니다. 같은 날 Oracle
배포에서는 최신 이미지가 실행 중이어도 기존 `.env`의 명시적 8/30초가 새 기본값을 덮어써 전체 약 14.4초의 정상
queue·조회가 8초에 먼저 끊기는 현상을 다시 확인했습니다. 현재 기본 계약은 60/90초와 300초 execution lease이며,
기존 `.env`에 두 timeout 키가 있으면 값을 함께 갱신한 뒤 전체 서비스를 재생성해야 합니다. 이 표본은 기존 장애
원인과 late 종료를 확인한 근거이고 새 배포의 실제 혼잡 시간대 correlation 동작 확인을 대신하지 않습니다.

KORAIL 정기점검 시간에는 성공 경로 스모크를 반복하지 않습니다. 공식 HTTPS KORAIL host의
`rejectservice_job.html` 또는 결과 행 없이 `서비스 일시중지`와 `승차권 예약 및 발매서비스` 문구가 함께 보이면
점검·서비스 장애로 즉시 닫습니다. sidecar 로그는 원문 URL·본문을 남기지 않고
`outcome=provider_unavailable stage=<closed-stage> trigger=maintenance_page|service_outage_page
cooldown_seconds=<seconds>`만 기록합니다. 안내 본문의 종료시각은 파싱하지 않으며
`SEAT_STATUS_PROVIDER_UNAVAILABLE_COOLDOWN_SECONDS`(기본 300초)의 제한된 provider-wide cooldown 뒤 한
요청만 복구 probe로 허용합니다. 이미 browser gate에 기다리던 서로 다른 query도
hold를 다시 확인해 `event=provider_query_skipped reason=queued_provider_cooldown`으로 끝나야 합니다.

sidecar는 이 전용 오류에만 기존 호환 503 body `source_unavailable`과 숫자형 `Retry-After`를 함께 보냅니다.
main API는 두 근거가 정확히 맞을 때만 별도 Redis key `korail-browser-outage`에 같은 TTL의 provider hold를
기록합니다. 일반 503·DOM timeout은 계속 exact query별 30~300초 backoff이고, 과거
`korail-browser/source_unavailable` 값도 전역 장애로 되살리지 않습니다. worker는 outage hold를 조회 전과 첫
실패 직후 확인해 남은 KORAIL 그룹을 같은 시각까지 미루며 오류 관측·예약 후보를 만들지 않습니다. 관리자
상태의 공개 cause는 보호조치가 아닌 기존 `source_unavailable`이며 화면에는 `일시 불가`와 `조회 대기`로
표시합니다. cooldown 중에도 서로 다른 query의 `provider_query_started`가 반복되면 구 이미지 혼재, Redis 연결,
sidecar/API의 hold 전파 로그를 확인하고 수동 반복 조회로 우회하지 않습니다.

2026년 8월 13일 01:30~04:30 KST 점검에서 수정 전 배포본은 공식 페이지 이동을 약 28초 뒤
`outcome=source_unavailable stage=wait_result`로 닫았습니다. 01:30~02:17 KST의 DB에는 KORAIL
`ERROR / provider_unavailable` 관측 115건과 신규 예약 시도 0건이 기록되어 좌석 발견이나 예매로 잘못 전이하지
않는 fail-closed 동작은 확인했습니다. 반면 서로 다른 query별 backoff만 적용되어 02:05~02:17 KST에 실제 조회
시작 29건이 직렬로 이어진 것이 이번 provider-wide 분류의 직접 근거입니다. 점검 종료와 hold 해제 뒤에는 읽기
조회를 한 번만 실행하고 `provider_query_completed outcome=success`, fresh `official_provider` 관측과 신규 예약
시도 없음까지 확인하기 전에는 성공 경로 운영 검증을 완료로 표시하지 않습니다.

같은 날 03:14 KST에 위 분류와 전역 hold를 포함한 experimental profile 전체를 재빌드·재생성했고, migration과
log-init은 종료 코드 0, 장기 서비스 12개는 모두 healthy였습니다. 새 sidecar가 준비된 03:18 KST 이후 운영
KORAIL은 점검 페이지가 아니라 정상 공식 응답을 반환했고, `korail-official-page-browser` 관측과 성공 lifecycle이
기록됐으며 같은 구간의 신규 KORAIL 예약 시도는 0건이었습니다. 따라서 배포와 정상 응답 fail-safe는 확인했지만,
실제 운영 점검 페이지에서 `outcome=provider_unavailable`·`Retry-After`·`korail-browser-outage` TTL이 함께 열리는
live 경로는 재현하지 못했습니다. 이 경로는 fixture·동시 query 경쟁조건 회귀로만 검증된 상태이며, 다음 실제
점검에서 첫 분류 1회와 뒤따르는 skip을 확인할 때까지 성공 경로 1회 확인 항목과 구분해 미완료로 둡니다.

같은 날 04:40 KST에는 provider-wide hold가 이전 좌석 cache보다 먼저 적용되는 최종 안전 순서를 포함해 profile
전체를 다시 재빌드·재생성했습니다. migration·log-init은 다시 종료 코드 0이었고 장기 서비스 12개가 모두
healthy였습니다. 배포 뒤 KORAIL 공식 관측은 `AVAILABLE` 12건, `LIMITED` 7건, `SOLD_OUT` 29건,
`WAITLIST_AVAILABLE` 2건으로 정상 처리됐고 신규 KORAIL 예약 시도와 sidecar 예약 endpoint 호출은 모두 0건이었습니다.
정상 페이지가 반환됐으므로 `korail-browser-outage` key는 열리지 않았으며, 이 확인도 실제 점검 페이지의 live
분류·hold 검증을 대신하지 않습니다.

관리자 화면의 `설정 → 로그·진행 상태 → 최근 진행 기록`은 원시 관측 로그가 아니라 최근 24시간의 작업 흐름과
확인이 필요한 오류 중 최신 20건을 빠르게 확인하는 사건 중심 요약입니다. 전체 좌석 관측은 처리량·오류율·
신선도 집계에 계속 사용하지만, 최근 목록에는 좌석 조회 오류·확인 불가·자료 만료만 관측 행으로 표시하고
반복적인 매진·가용·잔여석 부족 관측은 제외합니다. 예매 시도 outcome, 공식 확인의 닫힌 진단 분류, 로그인 확인 필요, 운영사 요청 제한,
대기 상태 변경, 알림 전달, 결제기한 경과나 공식 미결제 보류 부재 뒤 감시 복귀·일회성 종료, 공식 결제 완료
확인은 계속 구분합니다. 근거가 있는 행에는 열차번호·KST 운행일 요일/출발시각·좌석등급을 표시합니다.
이 화면은 내부 watch/candidate ID, 노선 원문, provider 오류 원문과 outbox payload를 의도적으로 제외하므로,
더 깊은 장애 분석이 필요할 때만 아래 sanitized 서비스 로그를 함께 확인합니다. 계정 저장 요청처럼 영속되지
않은 HTTP 실패와 저장되지 않은 provider 세부 실패 사유는 목록에 항상 남지 않으며 추정하지 않습니다.

### 예매 시도와 공식 결과 확인 진단

예매 장애는 `진행 단계`, `예매 명령의 terminal`, `읽기 전용 공식 확인`을 분리해서 봅니다.
`reservation_requested`는 예매 버튼 click 호출이 오류 없이 반환됐다는 뜻일 뿐, 예약이 성립했거나 결제보류가
생겼다는 뜻이 아닙니다. terminal이 불명확하면 같은 명령을 다시 보내지 않고 `UNKNOWN`으로 닫은 뒤 공식
예약 내역만 읽습니다. `INCONCLUSIVE`는 공식 목록에서 성공·부재 어느 쪽도 확정하지 못했다는 뜻이며 예매 실패,
결제 실패 또는 미결제를 뜻하지 않습니다.

REST의 `candidates[].latest_reservation_attempt`와 SSE의 `watch.reservation_result`,
`watch.reservation_reconciled`는 다음 provider-neutral 필드를 공통 운영 진단에 사용합니다.

- `result_reason_code`: 예매 명령 결과의 닫힌 원인 코드
- `confirmation_outcome`, `confirmation_observed_at`: 마지막 읽기 전용 공식 확인 결과와 관측 시각
- `confirmation_diagnostic_code`: `INCONCLUSIVE`를 공식 조회 불가, 계정 확인 맥락 불일치, 대상 기록 모호,
  공식 근거 부족, 과거·미분류로 나눈 닫힌 진단 코드. 다른 confirmation outcome에서는 비어 있어야 합니다.
- `reconciliation_attempt_count`, `next_reconcile_at`: 최초 확인 뒤 완료한 재확인 횟수와 다음 목표 시각

재확인 SSE에는 적용 후 `outcome`, `payment_actionable`, 유효 `payment_deadline`과 재확인 대상 attempt의
시작·종료 시각, 진행 단계, 확인된 좌석도 함께 남습니다. `payment_actionable=true`일 때만 현재 결제 안내를
갱신합니다. 명시적으로 지난 최신 기한은 기존 미래 기한보다 우선하고, 기한 미제공 공식 보류는 결제 상태를
숨기지 않습니다. 단계·좌석은 event의 같은 attempt 값만 사용하며 같은 후보의 더 최신 attempt로 보충하지
않습니다. 저장 시각은 UTC offset을 포함해 전달하므로 offset 없는 시각이 보이면 구버전 event인지 확인합니다.

웹은 예약 전이 SSE의 payload `watch_id`와 `aggregate_id`가 같고 `candidate_id`가 canonical watch에 등록된
후보일 때만 알림을 만듭니다. 누락·불일치는 현재 선택 후보로 보충하지 않습니다. 다중 후보 홈 행의 최신 시도
상세도 attempt 소유 candidate ID와 provider가 정확히 일치할 때만 표시하고, 근거가 없으면 현재 행의 열차에
원인을 붙이지 않습니다. `confirmation_outcome=confirmed_paid`가 결제 attempt에 있으면 외부 watch 상태가 아직
`payment_required`여도 긴급 결제 카드와 공식 결제 CTA를 숨깁니다. 결제 완료 알림은 닫힌 terminal SSE 또는
canonical 상태 전이에서만 만들고 재확인 근거만으로 앞서 합성하지 않습니다.

`UNKNOWN`의 불명확 공식 확인은 최초 확인과 별도로 최대 6회 재확인합니다. 화면의 `6/6`은 예매 명령을 여섯
번 반복했다는 뜻이 아니라, 기존 attempt에 대해 읽기 전용 확인을 여섯 번 마쳤다는 뜻입니다. 원시 provider
reason·예외 문자열은 API, SSE, 알림과 DB attempt에 복사하지 않습니다. 화면과 설정의 `로그·진행 상태`는
`confirmation_diagnostic_code`를 다음처럼 안내합니다.

미래 결제기한이 있는 `PAYMENT_REQUIRED`도 최초 공식 확인과 별도로 기한 전 후속 읽기를 최대 6회 수행합니다.
첫 후속 읽기는 즉시 실행하고, 그 뒤에는 완료 횟수에 따라 30초, 30초, 2분, 5분, 10분 뒤에 실행합니다.
여섯 번째 뒤에는 기한 전 확인을 더 예약하지 않으며 기한 경계의 final read는 별도입니다.

- `official_read_unavailable`: 공식 내역을 불러오거나 응답을 신뢰할 수 있게 완료하지 못했습니다.
- `credential_context_mismatch`: 예매 시도와 공식 확인의 계정 generation·확인 맥락이 달라 연결하지 못했습니다.
- `official_record_ambiguous`: 비슷하거나 중복된 공식 기록 중 이번 시도에 속한 하나를 특정하지 못했습니다.
- `official_evidence_insufficient`: 공식 화면은 읽었지만 로딩·필수 표시·식별 근거가 부족해 상태를 확정하지 못했습니다.
- `unspecified`: 새 진단 코드 배포 전 이력 또는 알려지지 않은 미래 입력을 안전하게 표시하는 호환 분류입니다.

attempt의 최종 재확인 해소 상태는 `reconciliation_resolution`으로 따로 확인합니다.

- `confirmed_absent`: 공식 대상 부재가 연속 두 번 확인됐습니다. 이 확인보다 늦은 같은 후보의 공식
  `AVAILABLE`·`LIMITED` 관측에서 자동 복구 episode를 한 번 열 수 있습니다.
- `exhausted_unresolved`: 여섯 번째 확인까지 결론을 내리지 못했거나 마지막 확인 한 번만 `NOT_FOUND`여서
  자동 부재 확정에 부족합니다. `next_reconcile_at`은 비우고 무한 확인을 중단합니다.

현재 `UNKNOWN` confirmation이 `INCONCLUSIVE`인 상태로 재확인을 세 번 마치면, 여섯 번째까지의 자동
read-only 확인을 계속하면서도 홈에 제한적인 사용자 재확인을 제공할 수 있습니다. canonical
`manual_rearm_reason=unknown_result_unresolved`와 `manual_rearm_available=true`가 함께 있는지 확인합니다.
사용자는 정확한 열차·운행일·출발시각·좌석등급의 예약 부재를 공식 앱·홈페이지에서 확인해야 하며,
`POST /watches/{watch_id}/reservation-rearm`은 운영사를 호출하지 않고 승인 marker와 즉시 좌석 관측 목표만
저장합니다. 승인 뒤의 새 공식 `AVAILABLE`·`LIMITED` 관측에서만 1회 claim하고, claim 직전 최신 attempt,
계정 generation, confirmation 상태와 관측 provenance를 다시 검사합니다. 이 fence는 후보별이 아니라 watch
전체 attempt를 읽으므로 한 후보의 미해소 `UNKNOWN`이나 exact 결제 완료 근거를 다른 후보의 더 늦은 attempt와
새 공식 가용 관측이 우회하지 못해야 합니다. 수동 복구는 유일한 source attempt를 가리키는 child episode로
한 번만 소비하고, 그 child가 다시 `UNKNOWN`이면 같은 예외를 연쇄 적용하지 않습니다. read model이 더 오래된
source attempt 하나에만 수동 권한을 투영한 경우 웹도 그 candidate context를 버튼·대화상자에 사용해야 하며,
둘 이상의 attempt가 권한을 주장하면 버튼을 숨겨야 합니다. source가 `confirmed_absent`로 해소된 뒤에는 다른
후보의 fresh 가용성이 정상 최초 episode를 열 수 있어야 하고, 같은 source 후보만 별도 confirmed-absent 복구
episode를 한 번 사용하므로 해소된 과거 행을 watch-global blocker로 계속 세지 않는지도 확인합니다.

예약 시도의 인증·보호 terminal은 영속된 `reservation_requested` 진행 단계를 먼저 확인합니다. 이 단계 전의
`AUTH_REQUIRED`·`PROVIDER_BLOCKED`만 pre-dispatch 인증 실패로 남겨 재인증 뒤 `auth:` episode를 열 수 있습니다.
단계 뒤의 같은 terminal이나 최초 공식 확인 신호는 attempt를 `UNKNOWN`으로 보존하고 watch·계정을
`auth_required`로 멈춥니다. 동일 credential generation을 다시 인증해도 다음 작업은 기존 attempt의 read-only
reconciliation이어야 하며 새 reserve 호출은 0건이어야 합니다. legacy post-dispatch 인증 행도 이 진행 근거가
있으면 `UNKNOWN`으로 정규화하고 claim 경계가 예약 재전송을 거절하는지 확인합니다. 다만 같은 최초 확인에서
provider별 exact `CONFIRMED_PAYMENT_REQUIRED`·`CONFIRMED_PAID`를 얻었다면 이 양성 근거가 terminal의 인증·보호
신호보다 우선하는지 함께 확인합니다.

재확인 중 `AUTH_REQUIRED` 또는 `PROVIDER_BLOCKED`가 나오면 예약 상태 evidence 횟수는 증가하지 않습니다.
attempt의 원래 outcome·reason과 confirmation provenance는 보존하되 같은 credential generation의 계정을
인증 필요 상태로 원자적으로 멈추고 모든 수동 승인 marker를 지웁니다. 최신 `UNKNOWN`을 확인하던 활성 watch는
`auth_required`로 전이하지만, `PAYMENT_REQUIRED` watch는 사용자가 기존 결제 CTA와 기한을 잃지 않도록 상태·후보·
공식 인계 정보를 그대로 보존합니다. 운영사 계정의 동일 generation이 다시 인증된 뒤에는 공식 내역 읽기만
재개하며 `auth:` 예약 episode를 만들지 않습니다. 새 credential generation으로 교체됐다면 과거 attempt의
자동 재확인이나 예약을 재개하지 않습니다. 이미 `auth_required`·`provider_blocked`인 계정은 reconciliation
selector와 최종 잠금 경계에서 모두 제외되어 provider I/O가 반복되지 않아야 합니다.

provider 호출이 끝나기 전에 사용자가 watch를 삭제한 경합에서는 watch aggregate를 완전하게 다시 잠글 수
없으므로 watch 전이와 outbox를 합성하지 않습니다. 다만 같은 credential generation의 `AUTH_REQUIRED`·
`PROVIDER_BLOCKED` 근거는 provider 계정 행에 별도로 커밋되어야 합니다. 이후 같은 운영사의 다른 watch에서도
인증 복구 전 provider I/O가 0건인지 확인하고, 삭제된 watch용 결과 event가 새로 생겼다고 기대하지 않습니다.

공식 좌석 조회가 오래 걸린 경우에는 조회를 시작한 시각이 아니라 provider 예약 attempt를 claim하기 직전
시각으로 출발 여부를 다시 확인합니다. `actual_departure_at → estimated_departure_at → scheduled_departure_at →
departure_at` 순서의 최신 근거가 그 검사 시점에 이미 지났다면 attempt와 provider 예약 호출이 모두 0인지
확인하고 후보·watch를 만료 처리합니다.

재확인 중 exact `CONFIRMED_PAID`가 나오면 `PAYMENT_REQUIRED` watch를 `COMPLETED`로 끝냅니다. 모든 `UNKNOWN`은
`unknown_result_follow_up`으로 읽되, 좌석 상관이 없으면 음성 근거만 수용하고 결제완료·결제보류 응답은
`INCONCLUSIVE`로 강등합니다. `reservation_requested` 뒤 모든 승객의 비공개
`confirmation_correlation_seats`가 정확히 남은 경우에만 양성 근거를 받을 수 있습니다. KORAIL은 브라우저 보존 상태의 상관 좌석과 유일한
공식 발권 카드의 여정·객실·호차·좌석이 모두 일치한 paid 결과만 완료로 인정합니다. KORAIL 미결제 목록은
좌석등급·호차·좌석을 주지 않으므로 보류 확정이나 `reserved_seats` 승격에 사용하지 않습니다. SRT는 단발 예약
결과가 반환한 모든 승객의 정규화된 호차·좌석이 `UNKNOWN` fence에 보존된 경우에만 공식 record의 여정·객실·
인원·좌석 집합까지 비교합니다. 유일한 paid record는 완료로, 사용 가능한 unpaid 보류는 결제 필요로 확정하고
그때만 상관 좌석을 `reserved_seats`로 승격합니다. 불확실한 상관값은 공개 REST·SSE·UI와 outbox payload에
노출하지 않으며, 좌석 상관 근거가 없는 과거 KORAIL·일반 SRT `UNKNOWN`에 소급 적용하지 않습니다. 허용된
완료에서도 원래 outcome·reason은 감사 이력에 보존되고, 화면의 수동 확인과 재시도는 즉시 숨깁니다.
active pre-reservation의 `SCHEDULED`·`OFFICIAL_WAITLIST`·`SEAT_FOUND`·`RESERVING`에서 exact paid를 적용하면
서버가 먼저 `WATCHING`으로 정규화한 뒤 `COMPLETED`로 전이합니다. 따라서 이 경로를 포함해
`watch.payment_completed.from`은 실제 완료 직전 상태인 `payment_required` 또는 `watching`이어야 하며, 완료된
watch의 모든 후보가 종료됐는지와 live 목록이 오래된 `UNKNOWN` 수동 확인 조건만으로 다시 포함하지 않는지도
함께
확인합니다. provider I/O 동안 watch가 `PAUSED`·`AUTH_REQUIRED`·`COOLDOWN`으로 바뀐 보존 경합에서 late exact
`CONFIRMED_PAID`가 확인되면 watch 상태 자체는 유지하되 모든 후보·결제 안내·관측·재시도·수동 승인 상태를
닫습니다. 이 경로는 `confirmed_paid`의 `watch.reservation_reconciled`만 발행하고
`watch.payment_completed`나 수동 확인 알림을 만들지 않습니다. REST에서도 비공개 상관 좌석을 노출하지 않고
`manual_rearm_available=false`인지 확인합니다.

이 분류는 실패·성공·결제·취소를 새로 추정하지 않으며 자동 재예매 허가로 사용하지 않습니다. provider별 실제
실패 표면은 비밀값과 응답 원문을 제거한 서비스 로그에서만 더 좁힙니다. KORAIL은
`phase=official_read|evidence_normalization`과 안전하게 정규화한 `stage`를, SRT는
`phase=reservation_list`와 닫힌 `failure_stage=transport|provider_library|response_validation`을 사용합니다.

2026년 8월 13일 15:54:38 KST의 KORAIL 86편 대전→서울 일반실 사례는 다음처럼 확인했습니다.

- outbox에는 `authenticated_session_ready → target_rechecked → seat_selected → reservation_requested` 네 단계와
  15:54:40의 `UNKNOWN`, `manual_check_required=true`, `monitoring_resumed=true`, `retryable=false`가 남았습니다.
- 같은 시각 sidecar terminal은 내부 `source_unavailable:reserve_once`로 끝났고, 즉시 실행한 동일 세션 상세
  확인도 `INCONCLUSIVE`였습니다. 이 조합의 공개 원인 코드는
  `reservation_request_result_unknown`입니다.
- 15:55:31, 15:56:18, 15:57:04, 16:02:27, 16:17:53, 17:18:25 KST의 재확인 6회도 모두
  `INCONCLUSIVE`였습니다. DB의 최종 `reconciliation_attempt_count`는 6이고 `next_reconcile_at`,
  `payment_deadline`, post-deadline 확인 시각은 모두 비어 있습니다.
- 따라서 이 사례는 예매 클릭 뒤 결과와 공식 예약 내역을 끝내 확정하지 못한 경우입니다. 결제보류 생성,
  결제완료, 결제 실패 어느 것도 확인되지 않았으므로 공식 예약 내역을 사용자가 직접 확인해야 합니다.
- 이 자동화는 해당 흐름에서 결제 버튼이나 PG 요청을 실행하지 않았습니다. 카드·CVC·한도·결제 인증 실패를
  관측할 수 있는 단계 자체가 없었으므로 이를 결제 실패 원인으로 분류하지 않습니다.

이 과거 attempt에는 provider 원문 reason과 confirmation 진단 코드를 저장하지 않았으므로 기존 공개 원인 코드
`reservation_request_result_unknown`과 호환 진단 `unspecified`를 유지합니다. 현행 projection은 같은 종류의 click 후
`source_unavailable:*`·`browser_error:*`·`reservation_backend_error` terminal을 만나면 재요청 금지와
`UNKNOWN` 수동 확인 fence는 그대로 두고, 닫힌 원인 코드만 `provider_unavailable`로 구분합니다. 그 밖의
전달 여부·공식 결과 불명확은 계속 `reservation_request_result_unknown`입니다.

코드 대조에서 이 시간열과 가장 강하게 일치하는 결함은 예약 terminal과 읽기 전용 공식 확인이
`/ticket/login`에서 공식 `loginCheck`를 읽다가 `BrowserSourceUnavailable`을 받으면 정확한 인증 header까지
확인하지 못하던 경로였습니다. 정상 로그인 경로는 같은 불확실 응답 뒤에도 제한된 DOM 확인을 계속했으므로
인증 판정 정책이 서로 달랐습니다. 바깥 예약 actor도 내부 오류의 `stage`와 이미 발생한 진행 시각·click 사실을
`reserve_once`와 `false/false`로 덮어써 terminal이 durable outbox보다 덜 상세해졌습니다.

당시 로그에는 현재 route, 내부 stage, `loginCheck`의 상태·content type·parse/fetch 실패 종류나 CDP envelope가
남지 않았습니다. 따라서 위 코드 결함은 보존된 시간열의 가장 강한 설명이지만, 당시 실행된 정확한 leaf를
운영 증거만으로 확정할 수는 없습니다. 비밀번호 만료, 운영사 차단, 결제 실패로 단정할 근거도 없습니다.
수정된 공통 인증 정책은 공식 양성 응답이면 즉시 인증으로 판정하고, 공식 probe가 불명확할 때 정확한 인증
header가 양성인 경우에만 계속합니다. 둘 다 아직 불명확하면 예약 동작을 더 실행하지 않고 제한 시간 동안
읽기만 다시 확인한 뒤 원래 source 불가 stage를 보존합니다. 공식 probe 재호출은 0.25초, 0.5초, 이후 최대
1초 간격으로 제한하고 사이에는 로컬 header만 확인합니다. 공식 로그아웃과 header 부재가 함께 확인된 경우만
인증 필요로 닫으며, 명시적인 보호·호출 제한은 그대로 전파합니다. 공식 확인의 상세·발권·예약 목록 중 다른
surface가 정확한 결론을 주면 그 결론을 사용하지만, 모두 불완전하면 최초 typed source 오류를 보존합니다.
예약 actor는 외부 callback 유무와 관계없이 진행 단계를 누적하고 검증된 내부 stage와 click·단계 시각을
terminal에 보존합니다.

2026년 8월 15일 21:01~22:09 KST의 KORAIL 223편 서울→대전 특실 사례는 좌석 관측 누락이 아니라
`UNKNOWN` fence 이후 실행 누락으로 확인했습니다.

- 21:19~21:21의 공식 특실 좌석 관측 97건은 모두 `LIMITED`였고, 21:20:04 시간표 snapshot도
  `FIRST=LIMITED`였습니다. 화면의 `특실 매진 임박`은 이 값과 일치합니다.
- 특실은 21:01:34~21:23:10, 21:34:18~21:39:26, 21:48:16~22:08:58에 다시 행동 가능했습니다.
  중간에는 `SOLD_OUT` 구간도 있었지만 첫 시도 뒤 추가 예매 attempt는 없었습니다.
- 21:01:34의 유일한 시도는 durable progress에 `seat_selected`와 `reservation_requested`까지 남겼습니다.
  당시 배포된 sidecar는 뒤이은 `source_unavailable:reserve_once` terminal에 이 click 사실을 합산하지 못해
  `false/false`를 기록했고, main은 progress와 terminal의 모순 때문에 결과를 안전하게 `UNKNOWN`으로
  닫았습니다.
- 최초 확인과 21:02:08, 21:02:38, 21:03:09, 21:08:11, 21:23:11, 22:23:26의 재확인은 모두
  `INCONCLUSIVE`였습니다. 최종 count는 6, `next_reconcile_at`은 비어 있었고, 결제보류 종료용 수동 재개
  대상도 아니었습니다. 따라서 후속 `SOLD_OUT → LIMITED` 전이가 있어도 기존 UNKNOWN fence가 해제되지
  않았습니다.
- 현행 actor는 이미 발행한 progress와 click 사실을 terminal에 보존하지만, 예약 버튼 click 뒤 결과를
  확정할 수 없는 경우를 임의로 실패 처리하지 않고 `UNKNOWN`을 유지합니다. 이 과거 attempt에는 확인 전용
  좌석 상관 근거가 저장되지 않아 새 `unknown_result_follow_up` 직접 완료 대상도 아닙니다. 동일 유형이 출발
  전에 다시 발생하면 연속 공식 `NOT_FOUND` 뒤 새 좌석 관측 복구 또는 세 번째 불명확 확인부터 가능한 명시적
  사용자 1회 승인 경로가 담당합니다.

이 사례를 조사할 때는 카드의 좌석 문구만 비교하지 말고 candidate의 정확한 `seat_class`, 최신 공식
observation, attempt의 progress, `result_reason_code`, confirmation outcome·diagnostic·count·resolution,
`manual_rearm_reason`, source attempt marker와 계정 generation을 한 시간축으로 대조합니다.

예약·공식 확인 sidecar 호출은 임시 UUIDv4 `request_id`로 main과 sidecar 로그를 연결합니다. 이 값은
인증·Redis key·metric label이나 watch/candidate/attempt 식별자로 사용하지 않습니다. main worker의
`reservation_confirmation_classified`·`reservation_confirmation_persisted` 기록은 attempt ID, provider, purpose,
outcome, `confirmation_diagnostic_code`, source, 재확인 회차·다음 목표와
`phase=initial_confirmation|worker_reconciliation`을 남깁니다. sidecar는 같은 `request_id`에 KORAIL의
`phase`·`stage` 또는 SRT의 `phase`·`failure_stage`를 남깁니다. 예를 들어 KORAIL 목록 읽기 실패는
`stage=confirmation_reservation_list`, SRT 전송 실패는 `phase=reservation_list failure_stage=transport`로
검색합니다. `source_unavailable`, 명시적인 provider 차단, 응답 검증 실패는 구분하되 예외
원문·DOM·URL·응답 본문을 남기지 않습니다. 한 표면의 boolean이나 HTTP 200만으로 결론 내리지 말고 공개 진단
필드, outbox, 같은 `request_id`의 sanitized client·sidecar terminal을 함께 대조합니다. HTTP 200은 내부 endpoint가 정상
응답했다는 뜻이지 예매 성공을 뜻하지 않습니다. 잘못된 bearer와 잘못된 body가 함께 들어오면 인증 실패를
먼저 판정해 `401/unauthorized`로 기록하며, 인증된 요청의 schema 오류만 `422/request_validation`으로
분류합니다. 두 분기 모두 검증된 request ID를 응답과 sanitized 로그에 연결합니다.

2026년 8월 15일 운영 DB snapshot에는 전체 attempt 630건 중 confirmation이 있는 attempt가 309건이었고, 그중
마지막 outcome이 `INCONCLUSIVE`인 행은 19건이었습니다. 17건은 결제보류 후속 확인, 2건은 최초 결과가 불명확한
`UNKNOWN` 시도였습니다. 이 값은 현재 행의 snapshot이지 공식 읽기 호출별 실패율이 아닙니다. 해당 시각보다
sidecar 회전 로그 보존 범위가 짧고 기존 outbox에 진단·source·request ID가 없어서 19건의 과거 leaf 원인은
소급 복원하지 않습니다. 새 진단 계약 배포 뒤 자연 재발부터 위 닫힌 코드와 상관 로그로 판정합니다.

2026년 8월 14~15일 이 보강을 적용한 뒤 관련 API 회귀 1,122개와 전체 API 4,149개가 통과했고, 웹 전체 875개,
typecheck·build와 마지막 운영 현황 문구 회귀 9개도 통과했습니다. Windows `experimental` 운영 명령으로 전체
프로필을 다시 빌드·재생성한 결과 migration은 `0035_reservation_result_reason (head)`, `migration`과
`log-init`은 종료 코드 0, 장기 서비스 12개는 모두 `healthy`였습니다. 최초 재생성 뒤 40건과 최종 소스 기준
재생성 뒤 12건의 notification 파일 로그는 모두 애플리케이션 소유 레코드였고 `httpx`·`httpcore` 및 원시
`HTTP Request` 레코드는 파일과 container console에서 각각 0건이었습니다. 이는 배포된 로그 차단 경계
검증이며 실제 외부 알림 전달이나 KORAIL 자연
재발 검증을 대신하지 않습니다. 과거 회전 로그는 삭제하지 않았고, 당시 URL 경로 자격정보의 회전과 보관본
정리는 별도 운영 조치로 남아 있습니다.

2026년 8월 15일 닫힌 공식 확인 진단을 추가한 뒤 API 전체 `4,171 passed, 4 skipped`, 웹 전체
`898 passed`, Ruff·format ratchet·mypy·웹 lint·typecheck·production build를 통과했습니다. 첫 운영 migration은
새 CHECK 이름이 PostgreSQL의 63자 식별자 한도를 넘어서 종료 코드 1로 안전하게 중단됐고, transactional DDL로
DB는 `0035_reservation_result_reason`에 그대로 남았습니다. migration·ORM의 이름을 43자와 48자로 줄이고
PostgreSQL DDL compile·길이 상한 회귀를 추가한 뒤 실제 `0035 → 0036_confirmation_diagnostic` upgrade와 두
CHECK 생성을 확인했습니다. 최종 `experimental-rail` 전체 재빌드·재생성에서 migration·log-init은 종료 코드 0,
장기 서비스 12개는 모두 `running/healthy`, API·KORAIL·SRT ready 응답은 모두 HTTP 200이었습니다. 재기동 뒤
서비스 로그의 traceback·critical·error와 원시 `HTTP Request`·`httpx`·`httpcore` 레코드는 각각 0건이었습니다.
이 검증은 진단 저장·표시·배포 경계를 확인한 것이며, 다음 자연 발생 confirmation에서 provider별 `stage` 또는
`failure_stage`가 기대대로 남는지 확인하는 운영 항목은 별도로 유지합니다.

KORAIL 결제 뒤에도 `결제 필요`가 유지되면 `korail-browser-adapter`의 confirmation 결과 source를 먼저
확인합니다. `korail-reservation-list`의 `NOT_FOUND`만 반복되면 MyTicket 발권 카드가 양성 근거로 채택되지 않은
것입니다. 일반 승차권 수는 `/ticket/myticket/list`의 `.my-ticket__trn-ticket-ticket-num .data`, 단체 수량은
`.tck_group-count`에서 읽습니다. KORAIL 결제완료는 attempt에 보존된 호차·좌석과 발권 카드가 정확히 일치할
때만 허용합니다. 좌석이 보존되지 않은 기존 attempt는 같은 여정의 과거 승차권과 구분할 수 없으므로 발권
카드와 fresh 미결제 목록의 대상 부재가 함께 보여도 `INCONCLUSIVE`로 유지합니다. 실제 DOM을 진단할 때도
승차권번호·PNR·QR·카드 본문과 인증 상태를 trace·HAR·로그에 남기지 않습니다.
`/ticket/mypage/ticketinfo/history`는 반환을 포함한 거래 이력
화면이므로 이 장애의 대체 결제완료 source로 사용하지 않습니다.

새 판정기 배포 전에 재확인 한도를 이미 소진했거나 결제기한 최종 확인 뒤 `WATCHING`으로 복귀한 attempt를 DB
수정만으로 소급 완료 처리하지 않습니다. late-paid 경로도 현재 공식 목록에서 다시 읽은 fresh exact 승차권,
attempt의 비공개 좌석 상관 근거, watch 전체 row lock과 결제 근거 fence가 모두 맞을 때만 동작합니다. 이 조건을
충족하지 못한 과거 이력은 `PAYMENT_REQUIRED`나 `COMPLETED`로 되돌리지 않고 공식 화면에서 수동 확인합니다.

공유하면 안 되는 내용:

- `.env` 원문
- `docker compose config`의 평문 출력
- `docker inspect` 결과
- 비밀번호, 쿠키, 토큰
- live trace, HAR, 브라우저 저장 상태

## 백업과 복원

백업은 PostgreSQL 덤프를 만든 뒤 age로 암호화합니다. 암호화용 공개키와 복호화용 비밀키는 서로 다른 위치에 보관하세요.

수동 백업:

| Linux Bash | Windows PowerShell |
| --- | --- |
| `bash ./scripts/ops.sh backup` | `./scripts/ops.ps1 backup` |

복원:

| Linux Bash | Windows PowerShell |
| --- | --- |
| `bash ./scripts/ops.sh restore /backups/<파일>.dump.age` | `./scripts/ops.ps1 restore /backups/<파일>.dump.age` |

Linux 운영 스크립트는 `/backups/*.dump.age` 경로와 복호화 가능 여부를 먼저 읽기 전용으로 확인하고, 다른 백업·복원이 실행 중이면 서비스를 멈추기 전에 거절합니다. 사전검사가 통과하면 proxy·scheduler·worker·API를 단계적으로 중지하고 복원과 migration 성공 뒤 원래 실행 중이던 컨테이너만 다시 시작합니다. 중지 단계가 실패하면 기존 서비스를 복구하고, 데이터 복원이나 migration이 실패하면 추가 쓰기를 막기 위해 maintenance 상태를 유지합니다.

복원은 기존 데이터를 바꿀 수 있습니다. 운영 인스턴스에 적용하기 전에 별도 테스트 인스턴스에서 실제 복원을 확인하세요.

## KORAIL 브라우저 진단

기본 KORAIL Chromium 서비스는 내부 Xvfb를 사용하되 화면 listener 없이 실행됩니다. 브라우저 화면을 확인해야 할 때만 로컬 noVNC 진단 구성을 사용합니다.

- noVNC는 설치한 컴퓨터에서만 접속할 수 있도록 엽니다.
- 원본 VNC 포트는 외부에 게시하지 않습니다.
- 비밀번호 파일은 `secrets/`에 두고 Git에 올리지 않습니다.
- 진단 화면은 보호 응답을 우회하는 수단이 아닙니다.

진단이 끝나면 화면 없는 기본 구성으로 다시 시작하세요.

## 문제 해결

### 서비스가 시작되지 않음

1. `docker compose -f compose.yml config --quiet`
2. `docker compose -f compose.yml ps -a`
3. 데이터베이스 준비 작업, API, 작업자 로그 확인
4. 필수 환경변수와 포트 충돌 확인

### WSL 빌드 컨텍스트의 xattr 권한 오류

Windows 드라이브(`/mnt/c` 등)의 저장소에서 `failed to xattr ... .tmp-pytest-* : permission denied`가 나오면 BuildKit이 오래된 테스트 임시 디렉터리의 Windows ACL을 읽지 못한 상태입니다. `.dockerignore`에 포함된 경로도 전송 준비 중 metadata 조회에서 먼저 실패할 수 있습니다.

`git status --short --untracked-files=all`로 해당 경로가 Git 비추적 임시 디렉터리인지 확인한 뒤 그 경로만 Windows에서 권한을 복구하거나 저장소 밖으로 이동하세요. 접근 거부로 정리할 수 없으면 WSL 내부 파일시스템(`~/...`)의 clean clone에서 설치 절차를 다시 실행합니다. 이 문제를 고치기 위해 Compose volume을 삭제하거나 `down -v`를 실행하지 마세요.

### 제한적인 umask 뒤 Git 갱신 파일의 읽기 권한 오류

Linux 운영 계정의 `umask`가 `0077`처럼 제한적이면 fast-forward 갱신으로 새로 쓰인 tracked 파일이
`0600`으로 만들어질 수 있습니다. Docker build context가 이 권한을 보존하면 비루트 migration·API 사용자가
새 모듈을 읽지 못해 `Permission denied`와 함께 migration이 중단될 수 있습니다.

먼저 작업 트리가 clean이고 오류 경로가 방금 갱신된 tracked 파일인지 확인하세요. 해당 파일만 Git index의
일반 파일 모드(`0644`) 또는 실행 파일 모드(`0755`)로 복원한 뒤 `config --quiet`와 공식 운영 스크립트를
처음부터 다시 실행합니다. `.env`, `secrets/`, `logs/`, Compose volume에는 이 복구를 적용하지 않습니다.
저장소 전체에 `chmod -R`을 실행하거나 `down -v`, volume 삭제로 복구하지 마세요. 이 환경 차이를 자동으로
정규화하는 배포 전 검사는 아직 구현하지 않았습니다.

### 로그인할 수 없음

- 최초 관리자 등록이 이미 끝났는지 확인합니다.
- 접속 주소가 `AUTH_ALLOWED_ORIGINS`에 등록한 주소와 정확히 같은지 확인합니다.
- HTTPS에서 `AUTH_COOKIE_SECURE=true`인지 확인합니다.
- 서버 시간을 확인합니다.

### 열차가 보이지만 좌석을 등록할 수 없음

시간표와 좌석 정보는 서로 다릅니다. 좌석 상태의 근거가 없거나 감시 기능이 꺼져 있으면 등록 버튼을 열지 않습니다.

### 운행·예매 상태 관측 안내가 보임

- `관측 오류 · 재시도 예정`은 같은 후보의 최신 공식 관측이 오류로 끝난 상태입니다. provider·rail worker 로그와 다음 관측 목표를 함께 확인합니다.
- 활성 감시의 `관측 일시 대기`는 API가 공개한 `cooldown_until`까지 운영사 호출을 미룬 상태입니다. 일시정지·결제·완료·만료 뒤 남은 과거 cooldown은 표시하지 않습니다. 조회 대기 중 수동 반복 호출로 우회하지 않습니다.
- `관측 지연 · 응답 대기 중`은 활성 작업이 유휴 상태인데 브라우저 화면 시각 기준으로 `next_check_at`이 30초 넘게 지난 경우입니다. 이는 장애 확정이 아닌 지연 진단 신호입니다. 마지막 성공이 매진이어도 독립적으로 표시되므로 기기 시각, scheduler, rail worker 큐, provider별 단일 실행과 sidecar health를 확인합니다.
- 정상 성공 관측, 현재 처리 중인 요청, 다음 목표 30초 이내에는 과거 운행 projection의 현재형 문구와 만료 chip을 숨깁니다. 홈의 `최근 확인 HH:mm:ss`와 `다음 좌석 관측 목표 HH:mm:ss`를 비교해 실제 갱신 여부를 확인합니다.

### 알림이 오지 않음

- 채널이 활성 상태인지 확인합니다.
- 설정 화면에서 테스트 전송을 실행합니다.
- `notification-worker` 로그에서 발송 처리 결과를 확인합니다.
- Web Push는 브라우저의 알림 권한과 서비스 워커 상태를 확인합니다.
- Chrome·Edge·모바일 가운데 일부 기기만 받지 못하면 해당 기기에서 `OS 알림`을 다시 열어 현재 기기 상태와 전체 활성 기기 수를 확인합니다. 다른 기기에서 시험 버튼을 누른 결과로 현재 기기의 수신 여부를 판단하지 않습니다.
- 전체 활성 기기 수에 현재 기기가 포함되지 않으면 그 브라우저에서 연결을 다시 켭니다. 한 기기의 만료 구독이 비활성화되어도 다른 활성 기기의 구독을 덮어쓰거나 끄지 않습니다.
- Android 알림창에는 보이지만 화면 위 팝업이 없으면 해당 브라우저 또는 설치된 PWA의 알림 설정에서 알림 중요도와 `화면에 팝업 표시`를 확인하고, 방해 금지와 알림 쿨다운도 확인합니다.
- 좌석 발견 직후 예매 진행 알림을 연속으로 시험하면 Android 15 알림 쿨다운의 영향을 받을 수 있습니다. 단일 테스트 알림을 2분 이상 간격으로 비교하고, 해당 알림 카테고리가 `긴급`인지 확인합니다.
- 알림을 눌러도 PWA가 열리지 않으면 설치된 PWA와 브라우저를 완전히 종료한 경우와 이미 실행 중인 경우를 각각 시험하고, 서비스 워커가 최신 버전인지 확인합니다.
- 앱을 보고 있는 동안 `실시간 알림`이 갱신되지 않으면 네트워크 연결과 `/events` SSE 또는 대기 목록 갱신이 정상인지 확인합니다. Push 내용 자체를 현재 좌석 상태로 사용하지 않습니다.
- `실시간 알림`의 X로 닫은 과거 카드가 재접속 뒤 다시 나타나면 같은 브라우저 origin인지, 사이트 데이터가 삭제됐거나 localStorage가 차단되지 않았는지 확인합니다. 닫기 ledger는 기기 간 동기화하지 않으며 같은 subject의 더 최신 revision은 정상적으로 다시 표시합니다.
- KORAIL 예매 카드가 `자동 예매 요청 시작`이나 `철도사 응답·공식 결과 대기`에서 멈추면 main API의 `/events` 연결과 `watch.reservation_progressed`·`watch.reservation_result` outbox, 같은 시각의 `GET /watches` 최신 attempt를 함께 확인합니다. 최신 attempt가 아직 `PENDING`이면 worker·maintenance 경로를 점검하고, 이미 `finished_at`과 `NOT_AVAILABLE`·`FAILED`·`UNKNOWN`을 가진다면 canonical 목록 갱신이 같은 watch의 진행 카드를 결과 카드로 교체해야 합니다. cursor 없는 신규 `/events`는 현재 outbox tail에서 시작해야 하므로 과거 outbox 행 수에 비례해 최신 event가 늦어지면 구버전 API 이미지가 섞였는지 확인합니다. 진행 이벤트는 `authenticated_session_ready → target_rechecked → seat_selected → reservation_requested`의 누적 prefix여야 하며, 같은 시도의 중복·역순·미래 시각은 화면에서 거부됩니다. 빠른 단계가 한 SSE poll 안에 함께 전달되는 것은 정상입니다. Pydoll의 `Page load timeout ... LOAD_EVENT_FIRED` 뒤 `KORAIL direct navigation load signal timed out; validating current DOM`이 남으면 로드 완료 신호만 늦은 상태를 현재 DOM·정확 열차 검증으로 이어간 것입니다. 후속 진행 또는 결과 이벤트를 함께 확인하세요.
- `GET /api/v1/watches`가 `progress_stages cannot occur after finished_at`으로 500을 반환하면 KORAIL sidecar와 main API 사이의 짧은 wall-clock 역행으로 저장된 과거 attempt인지 확인합니다. 현행 코드는 결과 저장과 조회 투영에서 `finished_at`을 모든 진행 시각 이상으로 정규화하고, `0034_progress_terminal_time` 마이그레이션이 기존 행을 보정합니다. 배포 뒤 Alembic head가 `0034_progress_terminal_time`인지, terminal attempt 가운데 진행 시각이 `finished_at`보다 늦은 행이 0건인지 확인합니다. 진행 시각을 삭제하거나 임의로 현재 시각으로 덮어쓰지 않습니다.
- provider 호출, rail worker 또는 외부 알림 발송이 멈춰도 시작 후 5분이 지난 `PENDING`은 전용 `maintenance-worker`가 다음 30초 주기 안에 `UNKNOWN`과 수동 확인 상태로 닫습니다. 5분 30초가 지나도 진행 카드가 유지되면 scheduler의 `recover-stale-reservation-attempts`, `maintenance-worker`의 `maintenance` 큐 수신 상태, `watch.reservation_result_requires_manual_check` outbox를 확인합니다. 이 복구는 예약 POST를 다시 보내지 않으며, 새로고침 뒤에도 확인된 단계와 수동 확인 카드를 canonical REST에서 복원합니다. 출발시간 경과로 감시가 끝났다면 카드도 감시 재개를 주장하지 않고 종료 상태와 공식 결과 수동 확인을 표시합니다.
- sidecar의 `/v1/reserve-once/stream` 연결이 terminal frame 전에 끊긴 경우 예약 POST를 재전송하지 않습니다. sidecar는 이미 시작한 예약 task를 종료까지 보존하고, main API는 불확실 결과를 `UNKNOWN`으로 기록해 즉시 재예매를 차단합니다. sidecar의 `reserve-once stream completed` 로그에서 닫힌 outcome/reason과 `reservation confirmation completed`의 purpose/outcome/source를 확인합니다. 최초 공식 확인이 `NOT_FOUND` 또는 `INCONCLUSIVE`이면 최소 30초 뒤 읽기 전용 확인이 예약되어야 합니다. 최초 `NOT_FOUND` 뒤 reconciliation도 `NOT_FOUND`이면 부재 확인을 닫고, 최초 결과가 `INCONCLUSIVE`였다면 첫 `NOT_FOUND` 뒤 30초 후 다시 `NOT_FOUND`여야 닫습니다. 그 뒤 같은 후보에 새 공식 `AVAILABLE`·`LIMITED`가 관측된 경우에만 `confirmed-absent-retry:<attempt_id>` episode가 한 번 생성되며, 같은 episode 또는 그 복구 시도의 재귀 반복은 허용되지 않습니다.
- KORAIL 예약 팝업은 `KORAIL reservation dialog phase=... kind=... control_shape=... dialog_count=... action=...`처럼 원문 없는 닫힌 필드로 기록합니다. `reservation_information`, `post_request_notice` 또는 명시적 운영 동의가 필요한 `generic_acknowledgement`에서 `dismiss_succeeded`면 공식 단일 확인을 예매 전후 단계·유형별 한 번 닫고 최신 DOM을 다시 본 것입니다. `reservation_information_consent`·`delay_consent`의 `consent_accept_succeeded`는 운영자가 `KORAIL_RESERVATION_DIALOG_AUTO_ACTION_ENABLED=true`로 예약 안내와 지연배상 제한을 수락한 상태에서 정확한 `네`를 단계·유형별 한 번 누른 뒤 최신 DOM을 다시 보는 중입니다. 이후 `payment_required`면 정확한 결제 상세를 확인한 것입니다. `official_post_dialog_action_unresolved`, `official_notice_persisted`, `reservation_information_consent_persisted`, `delay_consent_persisted`, `...accept_result_unknown`, `...dismiss_result_unknown` 또는 버튼 판독 실패는 새 성공 근거가 없어 수동 확인으로 닫은 것이므로 다시 누르거나 예매 요청을 재전송하지 않습니다. 동의가 없거나 `kind=existing_reservation_choice|unknown`, `dialog_count=multiple`이면 추가 자동 동작을 하지 않습니다. `reserve-once stream completed outcome=action_required`를 좌석 미감지나 전송 실패로 바꾸지 말고 같은 시각의 최초 공식 확인을 함께 확인하며, `NOT_FOUND`이면 예약 요청을 반복하지 않습니다. 2026년 8월 13일 240편 사례 당시 대화상자의 정확한 DOM·문구는 확보하지 못했고, 새 정책 배포 뒤 실제 팝업의 자연 재발은 아직 운영 확인 전입니다. 검증을 위해 예약을 인위적으로 만들지 않으며 자연 재발 때 닫힌 로그와 최종 공식 확인만 대조합니다.
- `자동 예매 다시 시도`가 보이지 않으면 watch가 `WATCHING`, 자동 예매 정책, 출발 전 상태인지와 후보별 최신 attempt 가운데 서버가 정확히 하나에만 투영한 `manual_rearm_available`·`manual_rearm_reason`을 확인합니다. 결제보류 종료는 watch 전체의 최신 attempt에 공식 종료 근거가, `UNKNOWN`은 watch 전체에서 유일한 미해소 원본이 같은 계정 generation의 현재 `INCONCLUSIVE` 재확인 3회 이상 또는 `exhausted_unresolved`여야 합니다. 더 늦은 다른 후보 attempt가 있어도 유일한 원본 권한과 그 candidate context가 웹 대화상자에 이어져야 하고, 권한이 둘 이상이면 웹은 fail-closed로 버튼을 숨깁니다. 확인 요청이 409이면 다른 후보를 포함한 미해소 `UNKNOWN`·정확한 결제 근거, 진행 중 예약·미종료 보류·철도 계정 인증·provider capability·요청 reason과 필수 확인값을 먼저 점검합니다. 성공 시 candidate의 `manual_rearm_source_attempt_id`와 `manual_rearm_authorized_at`, `watch.manual_reservation_rearmed` outbox, 즉시 예약된 관측 작업을 순서대로 확인합니다. 버튼 성공만으로 provider 예약 호출이 생겨서는 안 되며 승인 시각 뒤의 같은 원본 후보 공식 `AVAILABLE`·`LIMITED` 관측과 source attempt를 가리키는 고유 manual child episode가 있어야 한 번 실행됩니다. child 결과가 다시 `UNKNOWN`이면 같은 수동 예외를 재귀 적용하지 않습니다.
- iPhone·iPad에서 OS 알림 연결이 보이지 않으면 iOS·iPadOS 16.4 이상인지와 홈 화면에서 PWA로 실행했는지 확인합니다. Safari 일반 탭의 상태를 설치형 PWA 수신 성공으로 기록하지 않습니다.

### 대기를 취소할 수 없음

`진행 중인 예매 요청은 취소할 수 없습니다` 또는 결제가 필요한 예약 안내가 나오면 운영사 요청이나 임시 예약이
이미 시작된 상태입니다. 외부 요청을 소급 취소하거나 로컬 기록을 먼저 지우지 말고, 화면에 남은 결과와 공식
예약 내역을 확인해 직접 결제 또는 취소합니다. 일반 감시 상태에서 취소가 성공했다면 이후 관측 결과는 저장·
자동예매 근거로 사용되지 않습니다. `내 예약`의 기록 삭제는 감시 취소와 별도이며 종료되지 않은 대기는 먼저
안전하게 취소해야 합니다.

### 보호 안내 또는 호출 제한

반복해서 요청하지 마세요. 서비스는 정해진 시간 동안 요청을 멈춥니다. 공식 앱이나 홈페이지에서 직접 확인하고, 문제가 계속되면 실험 기능을 끄세요.

## 검증

저장소 전체 검증:

| Linux Bash | Windows PowerShell |
| --- | --- |
| `bash ./scripts/ops.sh verify` | `./scripts/ops.ps1 verify` |

GitHub Actions에서는 Ubuntu에서 Bash 구문, `ops.sh config`, 운영 명령의 stop 순서·실패 복구·profile·복원 무파괴 계약 테스트, API, 웹, PostgreSQL 경합 계약을 확인하고 KORAIL Chromium 컨테이너 검증은 별도 workflow로 분리합니다. 핵심 검증은 Web의 Node Playwright와 API의 Python Playwright가 잠근 서로 다른 Chromium revision과 OS 의존성을 각각 설치하며, 한쪽의 `npx playwright install` 결과를 다른 쪽에서 재사용하지 않습니다. `experimental-browser-verify`는 `linux/amd64`에서 외부 요청 없이 고정 fixture로 실제 Chromium 실행 경계를 확인합니다. `linux/arm64` 이미지 빌드 계약, OCI ARM64 네이티브 non-headless 실행과 배포 sidecar의 실제 읽기 조회는 별도 검증으로 완료했습니다. 핵심 API의 loopback fixture direct-CDP 테스트는 일회용 GitHub-hosted VM의 해당 검증 step에서만 정확한 test opt-in으로 Chromium 내부 sandbox를 끕니다. 실험 브라우저 테스트 이미지와 격리 full-stack E2E는 Chromium 내부 sandbox를 끄는 대신 비루트 사용자, 읽기 전용 루트, capability 제거와 권한 상승 금지 경계를 유지하고 전용 fixture 네트워크만 사용합니다. 운영 Chromium 서비스에는 이 실행 래퍼와 opt-in 환경값을 적용하지 않습니다.

실제 외부 알림 채널, 공개 도메인, 철도사 계정, 실기기 동작은 자동 테스트와 별도로 확인합니다. Windows Chrome·Edge와 Android PWA를 각각 연결한 뒤 전체 활성 기기 수와 한 상태 변화의 세 기기 동시 수신은 아직 운영 환경에서 확인해야 합니다. Android에서는 PWA를 제거·재설치한 뒤 마스커블 런처 아이콘의 여백과 이전 아이콘 캐시를 먼저 확인합니다. 이어서 알림창 수신, 화면 위 팝업, 실행 중 PWA 포커스, 종료된 PWA 열기와 캐시된 첫 화면 표시 시간, 접속 중 `실시간 알림`을 확인합니다. 공식 인계는 기본 HTTPS 새 창 동작을 KORAIL·SRT 각각 확인하고, 코레일+ booking·ticket과 SRT main·ticket은 별도 QA 문서의 경로별 ADB·목적 화면·설치/미설치 브라우저 행렬을 통과하기 전까지 활성화하지 않습니다. SRT main과 고정 extra `btnNo=2` ticket을 서로 다른 목적지로 확인합니다. iPhone·iPad에서는 16.4 이상 홈 화면 설치, 사용자 탭에서의 권한 요청, foreground 간략 팝업, background 알림센터 수신, 알림 클릭 뒤 기존 PWA 포커스와 미실행 PWA 열기를 각각 확인합니다. Apple banner·Focus 결과는 foreground 팝업 검증과 구분합니다.
