# Rail Waitlist API

개인용 KORAIL·SRT 예약 대기 보조 서비스의 FastAPI 백엔드입니다. 단일 관리자만을
전제로 하며 사용자, 소유자, 조직, 권한 모델을 만들지 않습니다.

## 안전 경계

- 공식 provider는 [TAGO 열차정보 API](https://www.data.go.kr/data/15098552/openapi.do)의
  역 목록과 시간표만 조회합니다. 서비스 키가 없으면 합성 결과 대신 `503`을 반환합니다.
- 합성 시간표와 예약 상태 전이는 `mock` provider에서만 제공합니다.
- 환경변수가 없는 `Settings`의 `EXPERIMENTAL_RAIL_ENABLED` 기본값은 `false`로 fail-closed합니다.
  저장소의 `.env.example`은 표준 Compose 운영을 위해 `experimental-rail`과 KORAIL Chromium·SRT provider
  sidecar의 좌석 감시 gate를 명시적으로 켭니다. 예매 capability는 별도 운영사 gate까지 충족될 때만
  열립니다. CAPTCHA 우회, 자동 결제, 프록시/IP/계정 회전 기능은 없습니다.
- KORAIL·SRT 철도 계정은 `회원번호 / 이메일 / 휴대전화` 방식과 ID·비밀번호를 암호화해 저장합니다. 저장 전에 로그인만 한 번 확인하고 성공한 값만 commit하며, 원문 credential·cookie·세션은 API 응답과 로그에 노출하지 않습니다.
- 카드, CVC, 간편결제·결제 인증정보를 위한 데이터 모델은 없습니다.
- 알림 채널 secret은 암호화해서 저장하고 API 응답과 로그에는 돌려주지 않습니다.
- webhook은 HTTPS만 허용하고 loopback·사설망·link-local·메타데이터 주소와 해당 주소로
  해석되는 DNS 결과를 전송 직전에 차단합니다. redirect도 따르지 않습니다.

## 단일 관리자 인증

- 첫 ID·비밀번호 등록은 관리자 계정이 하나도 없을 때만 가능합니다. 가입·초대·사용자 API는 없습니다.
- 최초 등록 화면은 기본적으로 닫혀 있습니다. 서버 운영자가
  `AUTH_INITIAL_REGISTRATION_ENABLED=true`로 시작한 동안에만 등록할 수 있으며, 등록 직후 값을
  `false`로 되돌리고 API 컨테이너를 재시작해야 합니다. 이미 계정이 있으면 설정값과 무관하게
  추가 등록은 `409`로 차단됩니다.
- ID는 앞뒤 공백 제거와 소문자 정규화 후 저장하고, 비밀번호는 평문이 아닌 Argon2id hash만 저장합니다.
- 등록과 세션 생성은 한 트랜잭션으로 처리하며, 이미 관리자가 있거나 동시 등록이 충돌하면 `409`로
  닫습니다. 기존 passkey 설치를 업그레이드한 경우 새 관리자 계정을 최초 1회 등록해야 합니다.
- 세션 쿠키 `rail_admin_session`은 random+HMAC 서명, HttpOnly, Secure, SameSite=Strict입니다.
- 상태 변경 요청은 허용된 `Origin`과 `rail_csrf` 쿠키 값을 담은 `X-CSRF-Token` 헤더가 필요합니다.
- `AUTH_ALLOWED_ORIGINS`, `AUTH_SESSION_SECRET`을 실제 도메인에 맞게
  설정해야 하며, TLS가 없는 개발 환경에서만 `AUTH_COOKIE_SECURE=false`를 사용합니다.

## 개발

Linux Bash:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
export DATABASE_URL='sqlite+aiosqlite:///./dev.db'
export AUTO_CREATE_SCHEMA=true
python -m uvicorn rail_waitlist.main:app --reload
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
$env:DATABASE_URL="sqlite+aiosqlite:///./dev.db"
$env:AUTO_CREATE_SCHEMA="true"
.venv\Scripts\python -m uvicorn rail_waitlist.main:app --reload
```

운영에서는 `DATABASE_HOST/PORT/NAME/USER/PASSWORD`와 `REDIS_URL`,
32바이트 이상의 `SECRET_ENCRYPTION_KEY`를 환경 변수로 지정하고 컨테이너 시작 전에
`alembic upgrade head`를 실행합니다.

TAGO는 `TAGO_SERVICE_KEY`를 사용합니다. Web Push VAPID private/public
키는 각각 `WEBPUSH_VAPID_PRIVATE_KEY`, `WEBPUSH_VAPID_PUBLIC_KEY` 환경 변수로 전달하며,
`WEBPUSH_VAPID_SUBJECT`에는 실제 연락 가능한 `mailto:` 또는 HTTPS URI를 사용합니다. private key를 알림 채널 요청에 넣지 않습니다.
새 VAPID 키는 한 줄 base64url 형식을 권장합니다. 기존 PEM private key를 옮길 때는
`WEBPUSH_VAPID_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"`처럼
줄바꿈을 `\n`으로 이스케이프하면 런타임에서 PEM 줄바꿈으로 복원합니다.
private/public key는 반드시 같은 P-256 키 쌍이어야 하며, 기존 브라우저 구독을 유지하려면 배포 때 재생성하지 않습니다. 생성·교체와 보안 컨텍스트 절차는 [시작하기](../../docs/GETTING_STARTED.md#web-push를-사용할-때선택)를 따릅니다.

가상환경을 활성화한 뒤 Celery worker와 beat는 각각 다음 명령으로 실행합니다.

```console
celery -A rail_waitlist.worker.celery_app worker --loglevel=INFO --concurrency=1
celery -A rail_waitlist.worker.celery_app beat --loglevel=INFO
```

## API 계약

- `GET /api/v1/providers`, `GET /api/v1/stations`, `GET /api/v1/timetables`
- `POST/GET/PATCH/DELETE /api/v1/watches`
- `POST /api/v1/watches/{id}/start|pause|cancel`
- `GET/POST/PATCH/DELETE /api/v1/notifications/channels`
- `POST /api/v1/notifications/channels/{id}/test-send`
- `GET /api/v1/events` (SSE, `Last-Event-ID` 지원)
- `GET /api/v1/auth/status`, `POST /api/v1/auth/register|login|logout`
- `GET /api/v1/notifications/web-push/public-key`
- `GET /metrics`, `/healthz`, `/readyz`

auth와 health를 제외한 `/api/v1` API는 관리자 세션이 필요합니다. 공식 provider capability의
`official_waitlist_link=false`는 TAGO가 예약대기 자동 API를 제공한다는 뜻이 아님을 명시합니다.
새 대기 웹이 생성 직후 start API를 호출해도 운영사별 세 환경변수 opt-in이 모두 켜지지 않으면
KORAIL·SRT의 `seat_monitoring=false`를 유지합니다. 승인된 관측 adapter와 3중 opt-in이 있을 때만
`seat_monitoring=true`가 되며, `reservation_once`는 별도 운영사 gate, 로그인 확인된 활성 계정,
작업별 `reserve_once_before_payment` 정책까지 모두 충족할 때만 true가 됩니다. 이 경로의 예약 호출은
후보별 DB 고유 fence 아래 한 번만 실행하고 결제 전에 멈추며 자동 결제는 제공하지 않습니다. timeout·취소·만료처럼 결과가 불명확하면 같은 예약 요청을 재호출하지 않고 공식 예약 내역의 수동 확인으로 전환합니다.

`GET /api/v1/stations?provider=korail|srt`는 TAGO 도시·역 목록의 원본 `node_id`, 역명, 도시와
[KORAIL 공개 역 안내](https://www.korail.com/public/st_info/station_data.json)의 역명 교집합만 반환합니다.
응답은 `catalog_scope=intercity_station_guide_intersection`,
`provider_membership=not_verified_by_source`이며, 이 필터는 화면에서 역을 찾기 위한 기준이지
운영사 소속이나 선택 날짜의 실제 운행 증거가 아닙니다.

migration `0007`의 PostgreSQL 스냅샷은 원본 TAGO identity 목록과 화면용 교집합을 함께 저장합니다.
원본 identity는 시간표 요청의 node ID·역명 검증을 hydrate하고 API 응답에는 교집합만 사용합니다.
신선한 스냅샷으로 재시작하면 상류 호출이 없고, 24시간이 지난 스냅샷은 즉시 반환하면서 DB lease를
획득한 한 replica가 갱신합니다. lease owner와 유효 시간으로 늦은 쓰기를 fencing하며, 갱신 실패·빈
응답·손상 응답은 마지막 정상 스냅샷을 덮지 않습니다. 정상 스냅샷이 없거나 화면용 교집합을 만들지
못하면 원본 목록으로 되돌아가지 않고 `503`으로 닫습니다. 화면 목록에서는 광운대·노량진·신도림·
서빙고·왕십리·옥수를 제외하고 KORAIL 역 안내의 서울·수서·대전·부산 sentinel을 검증합니다.

`GET /api/v1/timetables`의 KORAIL·SRT 요청에는 `origin_node_id`와 `destination_node_id`가 모두
필수입니다. 역명과 ID 쌍을 공식 카탈로그로 검증한 뒤에만 TAGO를 조회하며, 누락·동일 ID·이름 불일치는
`422`로 닫습니다. 이름만 받는 조회는 외부 요청이 없는 `mock`에만 허용합니다. 같은 역 ID·날짜의
KORAIL·SRT 동시 요청은 provider와 무관한 원본 시간표 캐시와 single-flight를 공유해 TAGO 원본 조회를
한 번만 수행한 뒤 KORAIL은 KTX 계열, SRT는 SRT 계열로 각각 필터링합니다. 이 캐시도 현재 API 프로세스
메모리 기준입니다.

쓰기 요청은 선택적으로 `Idempotency-Key` 헤더를 받을 수 있습니다. 동일 키와 동일 작업은
기존 결과를 재사용하며, 다른 작업에 키를 재사용하면 `409`를 반환합니다.

## 검증

Linux Bash:

```bash
. .venv/bin/activate
python -m pytest
```

Windows PowerShell:

```powershell
.venv\Scripts\python -m pytest
```
