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

## 알림 채널

지원하는 채널:

- Web Push
- Telegram
- Discord
- HTTPS Webhook

Web Push를 사용하려면 같은 실행에서 만든 VAPID private/public key 쌍과 실제 연락 가능한 subject를 설정해야 합니다. public key가 비어 있거나 잘못되면 기기 연결이 실패하고, private key가 비어 있거나 잘못됐거나 두 키가 같은 쌍이 아니면 실제 발송이 실패합니다. 생성과 `.env` 입력 방법은 [시작하기](GETTING_STARTED.md#web-push를-사용할-때선택)를 따릅니다.

VAPID 키 쌍은 일반 배포나 앱 업데이트 때 회전하지 않습니다. 불가피하게 교체하면 연결했던 모든 브라우저와 설치형 PWA에서 OS 알림을 껐다가 다시 켜 새 public key로 재구독하고, 기기별 시험 전송을 확인합니다. `localhost`가 아닌 다른 기기에서는 HTTPS 주소로 접속해야 Web Push를 연결할 수 있습니다.

채널을 저장한 뒤 설정 화면의 테스트 전송을 사용하세요. 설정 화면의 성공은 요청이 접수됐다는 뜻이며, 실제 기기나 외부 서비스에서 수신했는지는 직접 확인해야 합니다.

Web Push는 Chrome·Edge·설치된 모바일 PWA마다 별도의 구독으로 연결합니다. 연결되지 않은 기기에서는 인증 뒤 모든 화면에 비차단 `OS 알림 켜기` 행동을 표시합니다. 사용자가 이 버튼을 누르면 설정 화면을 거치지 않고 브라우저 권한 요청, 서비스 워커 준비, 구독과 서버 채널 저장을 순서대로 진행합니다. 브라우저가 권한과 새 Push 구독에 직접 사용자 행동을 요구하므로 페이지 load effect에서 승인창을 강제로 열지 않습니다. 권한이 차단된 경우에는 코드로 반복 요청하지 않고 사이트 권한 변경을 안내합니다. 앱에서 현재 기기의 OS 알림을 명시적으로 끄면 그 브라우저 구독과 대응하는 서버 채널만 비활성화·해제하고 전역 연결 안내도 억제하며, 다른 기기의 활성 구독은 유지합니다. 설정 화면은 현재 기기의 연결 상태와 전체 활성 기기 수를 구분해 표시합니다. 설정 화면의 `시험`은 현재 기기 한 곳으로 보내고, 실제 대기 상태 변화는 연결된 모든 활성 기기로 각각 보냅니다. 한 push endpoint가 만료되어 영구 실패하더라도 해당 기기 채널만 비활성화하고 다른 기기의 발송은 계속합니다.

Web Push 알림을 누르면 외부 철도사 주소가 아니라 동일 출처의 레일웨잇 PWA를 우선 찾습니다. 실행 중인 창이 있으면 앞으로 가져오고, 종료 중인 창의 focus가 실패하거나 창이 없으면 PWA 범위의 시작 화면을 엽니다. 온라인 navigation은 새 `index.html`을 먼저 받아 같은 배포의 해시 bundle과 함께 열고, 네트워크가 실제로 실패할 때만 캐시된 app shell을 사용합니다. `/assets/`의 존재하지 않는 이전 해시 파일은 SPA 문서로 대체하지 않고 404로 처리합니다. 로그인 상태와 최신 대기 데이터는 항상 API 응답을 기다립니다. 앱이 화면에 떠 있을 때 Push가 도착하면 서비스 워커가 비밀값 없는 힌트만 전달하고, 화면은 API의 최신 대기 상태를 다시 읽은 뒤 `실시간 알림`을 갱신합니다.

2026년 8월 7일 Android 16/API 36 에뮬레이터에서는 설치형 PWA를 백그라운드로 두고 Android 설정 앱을 연 상태에서 서비스 워커 합성 알림을 눌렀을 때 기존 `SameTaskWebApkActivity`가 전면으로 복귀하는 것을 확인했습니다. 이는 `notificationclick`의 기존-client 복귀 경로 검증이며, 실제 push service 전달·완전 종료된 PWA 콜드 실행·갤럭시 폴드7 제조사 동작은 별도 실기기 항목으로 남깁니다.

앱을 사용 중이면 Android·iPhone·iPad 모두 같은 `실시간 알림` surface가 safe area 아래에 8초간 간략 팝업으로 나타납니다. `자세히`를 누르면 전체 목록을 펼칩니다. 미리보기를 숨기거나 8초가 지나도 좌석 발견·현재 예매 진행·결제·인증처럼 직접 닫아야 하는 알림은 접힌 건수 안에 남고, 앱을 다시 열면 서버의 현재 예매 진행·결제·인증 상태를 복원합니다. 이 화면 알림은 modal이 아니므로 페이지 스크롤, 현재 입력과 가상 키보드, 하단 탐색을 잠그지 않습니다.

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
docker compose -f compose.yml logs -f --tail=200 api worker notification-worker
```

서비스별 파일 로그는 `logs/<service>/current.log`에 기록됩니다. 파일은 자동으로 회전하며 `logs/README.md`만 Git에 포함됩니다.

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

### 로그인할 수 없음

- 최초 관리자 등록이 이미 끝났는지 확인합니다.
- 접속 주소가 `AUTH_ALLOWED_ORIGINS`에 등록한 주소와 정확히 같은지 확인합니다.
- HTTPS에서 `AUTH_COOKIE_SECURE=true`인지 확인합니다.
- 서버 시간을 확인합니다.

### 열차가 보이지만 좌석을 등록할 수 없음

시간표와 좌석 정보는 서로 다릅니다. 좌석 상태의 근거가 없거나 감시 기능이 꺼져 있으면 등록 버튼을 열지 않습니다.

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
- iPhone·iPad에서 OS 알림 연결이 보이지 않으면 iOS·iPadOS 16.4 이상인지와 홈 화면에서 PWA로 실행했는지 확인합니다. Safari 일반 탭의 상태를 설치형 PWA 수신 성공으로 기록하지 않습니다.

### 보호 안내 또는 호출 제한

반복해서 요청하지 마세요. 서비스는 정해진 시간 동안 요청을 멈춥니다. 공식 앱이나 홈페이지에서 직접 확인하고, 문제가 계속되면 실험 기능을 끄세요.

## 검증

저장소 전체 검증:

| Linux Bash | Windows PowerShell |
| --- | --- |
| `bash ./scripts/ops.sh verify` | `./scripts/ops.ps1 verify` |

GitHub Actions에서는 Ubuntu에서 Bash 구문, `ops.sh config`, 운영 명령의 stop 순서·실패 복구·profile·복원 무파괴 계약 테스트, API, 웹, PostgreSQL 경합 계약을 확인하고 KORAIL Chromium 컨테이너 검증은 별도 workflow로 분리합니다. `experimental-browser-verify`는 `linux/amd64`에서 외부 요청 없이 고정 fixture로 실제 Chromium 실행 경계를 확인합니다. `linux/arm64` 이미지 빌드 계약, OCI ARM64 네이티브 non-headless 실행과 배포 sidecar의 실제 읽기 조회는 별도 검증으로 완료했습니다. 실험 브라우저 테스트 이미지와 격리 full-stack E2E는 GitHub runner의 사용자 네임스페이스 제한 때문에 Chromium 내부 sandbox만 끄지만, 비루트 사용자, 읽기 전용 루트, capability 제거와 권한 상승 금지 경계를 유지하고 전용 fixture 네트워크만 사용합니다. 운영 Chromium 서비스에는 이 실행 래퍼와 opt-in 환경값을 적용하지 않습니다.

실제 외부 알림 채널, 공개 도메인, 철도사 계정, 실기기 동작은 자동 테스트와 별도로 확인합니다. Windows Chrome·Edge와 Android PWA를 각각 연결한 뒤 전체 활성 기기 수와 한 상태 변화의 세 기기 동시 수신은 아직 운영 환경에서 확인해야 합니다. Android에서는 PWA를 제거·재설치한 뒤 마스커블 런처 아이콘의 여백과 이전 아이콘 캐시를 먼저 확인합니다. 이어서 알림창 수신, 화면 위 팝업, 실행 중 PWA 포커스, 종료된 PWA 열기와 캐시된 첫 화면 표시 시간, 접속 중 `실시간 알림`을 확인합니다. 공식 인계는 기본 HTTPS 새 창 동작을 KORAIL·SRT 각각 확인하고, 코레일+ booking·ticket과 SRT main·ticket은 별도 QA 문서의 경로별 ADB·목적 화면·설치/미설치 브라우저 행렬을 통과하기 전까지 활성화하지 않습니다. SRT main과 고정 extra `btnNo=2` ticket을 서로 다른 목적지로 확인합니다. iPhone·iPad에서는 16.4 이상 홈 화면 설치, 사용자 탭에서의 권한 요청, foreground 간략 팝업, background 알림센터 수신, 알림 클릭 뒤 기존 PWA 포커스와 미실행 PWA 열기를 각각 확인합니다. Apple banner·Focus 결과는 foreground 팝업 검증과 구분합니다.
