# 설치·운영 가이드

이 문서는 레일웨잇을 직접 운영할 때 필요한 명령과 확인 방법을 정리합니다. 처음 설치한다면 [시작하기](GETTING_STARTED.md)를 먼저 읽으세요.

## 운영 전 확인

- 실제 비밀값은 저장소 루트의 `.env`에만 둡니다.
- `.env`, `secrets/`, 백업, 로그, 브라우저 인증 상태는 Git에 올리지 않습니다.
- Compose 설정은 항상 `config --quiet`로 확인합니다.
- 기본 포트는 로컬 컴퓨터에만 열립니다.
- 인터넷에 공개할 때는 HTTPS와 보안 쿠키를 함께 설정합니다.

## 기본 명령

저장소 루트에서 실행합니다.

```powershell
./scripts/ops.ps1 config
./scripts/ops.ps1 up
./scripts/ops.ps1 status
./scripts/ops.ps1 logs
```

직접 Compose를 사용할 수도 있습니다.

```powershell
docker compose -f compose.yml config --quiet
docker compose -f compose.yml up -d --build
docker compose -f compose.yml ps
```

서비스를 중지할 때는 다음 명령을 사용합니다.

```powershell
docker compose -f compose.yml down
```

데이터를 보존하려면 `down -v`를 실행하거나 Docker 볼륨을 삭제하지 마세요.

## 환경 설정

전체 형식과 설명은 [.env.example](../.env.example)에 있습니다.

반드시 설정할 값:

- `POSTGRES_PASSWORD`
- `SECRET_ENCRYPTION_KEY`
- `AUTH_SESSION_SECRET`

처음 관리자 계정을 만들 때만 사용하는 값:

- `AUTH_INITIAL_REGISTRATION_ENABLED`

실제 시간표 검색에 필요한 값:

- 공공 시간표: `TAGO_SERVICE_KEY`

선택 기능에 필요한 값:

- Web Push: `WEBPUSH_VAPID_PRIVATE_KEY`, `WEBPUSH_VAPID_PUBLIC_KEY`
- 모니터링: `GRAFANA_ADMIN_PASSWORD`
- 암호화 백업: `BACKUP_AGE_IDENTITY`, `BACKUP_AGE_RECIPIENT`

비밀값을 바꿀 때는 기존 데이터와의 호환성을 먼저 확인하세요. 특히 `SECRET_ENCRYPTION_KEY`를 잃어버리면 저장된 철도 계정과 알림 비밀값을 복구할 수 없습니다.

## 첫 관리자 계정

관리자 계정이 하나도 없을 때만 새 계정을 만들 수 있습니다.

1. `.env`에서 `AUTH_INITIAL_REGISTRATION_ENABLED=true`로 시작합니다.
2. 화면에서 관리자 계정을 만듭니다.
3. 값을 다시 `false`로 바꿉니다.
4. API 서비스를 다시 만듭니다.

```powershell
docker compose -f compose.yml up -d --force-recreate api
```

여러 사용자를 위한 가입이나 초대 기능은 없습니다.

## 다른 기기에서 접속하기

### 로컬 컴퓨터

기본 주소는 `http://127.0.0.1`입니다. 기본 설정에서는 같은 네트워크의 다른 기기에서도 바로 접속할 수 없습니다.

### Tailscale

개인용으로 운영한다면 Tailnet 내부에서만 접속할 수 있도록 구성하는 방식을 권장합니다.

```powershell
tailscale serve --bg http://127.0.0.1:80
```

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

코드, Dockerfile, Compose 또는 실행 이미지를 바꿨다면 일부 서비스만 이전 버전으로 남겨 두지 말고 모두 같은 버전으로 다시 만듭니다.

```powershell
docker compose -f compose.yml config --quiet
docker compose -f compose.yml build
docker compose -f compose.yml up -d --force-recreate
docker compose -f compose.yml ps
```

확인할 항목:

- 데이터베이스 마이그레이션과 로그 초기화 서비스가 정상 종료했는지
- 장기 실행 서비스가 `healthy`인지
- `http://127.0.0.1/`이 열리는지
- API의 `/healthz`, `/readyz`가 정상인지
- 최근 로그에 반복되는 오류가 없는지

문서나 공개 이미지 파일만 바꾼 경우에는 전체 서비스 재배포가 필요하지 않습니다.

## 선택 프로필

### 철도사 실험 기능

```powershell
./scripts/ops.ps1 experimental
```

이 명령은 KORAIL Chromium과 SRT 연동 서비스를 포함한 `experimental-rail` 프로필 전체를 다시 빌드하고 생성합니다.

실험 기능은 기본적으로 꺼져 있습니다. 좌석 확인과 백그라운드 감시는 운영사별 설정을 모두 켜야 사용할 수 있으며, 예매 시도에는 로그인 확인을 마친 철도 계정과 별도 활성화가 추가로 필요합니다.

실험 기능은 안정적인 성공을 보장하지 않습니다. 보호 안내나 호출 제한이 나타나면 반복 실행하지 말고 중단 시간을 지키세요.

### 모니터링

```powershell
./scripts/ops.ps1 monitoring
```

Prometheus와 Grafana를 실행합니다. Grafana는 별도 관리자 비밀번호를 사용합니다.

### 내부 ntfy

```powershell
./scripts/ops.ps1 ntfy
```

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

채널을 저장한 뒤 설정 화면의 테스트 전송을 사용하세요. 설정 화면의 성공은 요청이 접수됐다는 뜻이며, 실제 기기나 외부 서비스에서 수신했는지는 직접 확인해야 합니다.

Webhook은 HTTPS 주소만 허용합니다. 사설망 주소, 이 컴퓨터를 가리키는 주소, 링크 로컬 주소, 클라우드 메타데이터 주소로의 요청은 차단합니다.

## 로그와 상태 확인

실시간 로그:

```powershell
docker compose -f compose.yml logs -f --tail=200
```

특정 서비스만 볼 수도 있습니다.

```powershell
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

```powershell
./scripts/ops.ps1 backup
```

복원:

```powershell
./scripts/ops.ps1 restore /backups/<파일>.dump.age
```

복원은 기존 데이터를 바꿀 수 있습니다. 운영 인스턴스에 적용하기 전에 별도 테스트 인스턴스에서 실제 복원을 확인하세요.

## KORAIL 브라우저 진단

기본 KORAIL Chromium 서비스는 화면 없이 실행됩니다. 브라우저 화면을 확인해야 할 때만 로컬 noVNC 진단 구성을 사용합니다.

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

### 보호 안내 또는 호출 제한

반복해서 요청하지 마세요. 서비스는 정해진 시간 동안 요청을 멈춥니다. 공식 앱이나 홈페이지에서 직접 확인하고, 문제가 계속되면 실험 기능을 끄세요.

## 검증

저장소 전체 검증:

```powershell
./scripts/ops.ps1 verify
```

실제 외부 알림 채널, 공개 도메인, 철도사 계정, 실기기 동작은 자동 테스트와 별도로 확인합니다.
