# 운영 가이드

운영사 제한 뒤 계정이 다시 `authenticated`로 검증되면 마지막 `reservation_provider_blocked` 전이가 재검증보다 오래된 작업을 감시로 복구합니다. 그 후보의 최신 attempt가 `PROVIDER_BLOCKED`이고 `last_authenticated_at`이 attempt 종료보다 새로울 때만 현재 로그인 검증 세대에서 자동 예매를 정확히 1회 다시 허용합니다. 계정 상태만 수동 변경하거나 같은 검증 시각을 반복 저장해서는 재무장되지 않습니다.

## 관측 속도 설정

`설정 > 화면 동작`은 서로 다른 두 주기를 저장합니다. `화면 표시 갱신`은 기본 5초·허용 5~300초이며 웹이 저장된 snapshot과 `/watches`를 다시 읽는 주기일 뿐 철도사 좌석 요청을 늘리지 않습니다. `좌석 관측 간격`은 최초 설치 기본 5초·허용 1~600초이며 모든 활성 작업이 같은 목표값을 사용합니다. 값이 작더라도 같은 운영사 직렬 큐, provider cache, circuit, backoff, rate-limit cooldown과 보호 cooldown은 그대로 적용됩니다. 운영 점검에서는 카드의 `최근 확인`과 `다음 관측 예정 HH:mm:ss`를 함께 확인하며 화면 갱신 완료를 실제 관측 성공으로 해석하지 않습니다.

결제기한이 지나도 웹 시계만으로 취소를 확정하지 않습니다. worker가 같은 credential generation의 공식 예약 정보를 최종 확인해 결제 보류가 더 이상 행동 가능하지 않다고 확정한 뒤에만 terminal hold-ended 이벤트와 외부 채널 알림을 생성합니다. 이때 웹 알림은 기존 결제 진행 카드를 같은 subject에서 교체하고 모든 step의 spinner를 종료합니다. `reserve_once_before_payment`는 감시로 복귀하지만 같은 episode에서 즉시 재예매하지 않으며, `notify_only`는 `expired`로 종료합니다.

## 권장 배포

개인 서버에는 Caddy만 80·443으로 노출하고 데이터베이스·Redis·API 포트는 공개하지 않습니다. 기본 접속은 Tailscale Serve를 권장하며 공개 도메인은 관리자 계정을 먼저 만든 뒤 허용 origin을 정확히 설정한 경우에만 사용합니다.

Compose 기본값은 Caddy도 `127.0.0.1`에만 bind합니다. Tailscale Serve는 이 loopback 주소를 사용합니다. 공개 도메인 운영에서만 `CADDY_HTTP_BIND=0.0.0.0:80`, `CADDY_HTTPS_BIND=0.0.0.0:443`을 설정하고 `AUTH_COOKIE_SECURE=true`를 함께 적용합니다.

로컬 평문 HTTP 점검에서는 `AUTH_COOKIE_SECURE=false`를 사용하지만, Tailscale Serve 또는 공개 HTTPS 주소에서는 반드시 `true`로 설정합니다. 접속 주소가 바뀌면 `AUTH_ALLOWED_ORIGINS`, `CORS_ORIGINS`도 같은 주소 기준으로 함께 갱신합니다.

## `.env` 비밀값

`.env.example`을 `.env`로 복사하고 주석에 적힌 형식대로 빈 값을 채웁니다. 필수 값은 `POSTGRES_PASSWORD`, `SECRET_ENCRYPTION_KEY`, `AUTH_SESSION_SECRET`이며, TAGO·Web Push·monitoring·backup/restore 값은 해당 기능을 사용할 때 설정합니다. 무작위 키는 서로 재사용하지 않습니다.

Compose는 더 이상 저장소의 `secrets/*.txt`나 `/run/secrets/*`를 읽지 않습니다. 기존 설치는 `./scripts/migrate-secrets-to-env.ps1`로 값 자체를 출력하지 않고 한 번 이전할 수 있습니다. `.env`가 이미 있으면 스크립트는 기본적으로 중단하며, `-Force`를 지정해도 기존 네트워크·백업 설정은 보존하고 대응되는 secret 변수만 병합합니다. 특히 기존 PostgreSQL volume은 `.env`만 바꿔도 DB role 비밀번호가 자동 변경되지 않으므로 기존 `postgres_password.txt`와 같은 값을 먼저 사용해야 합니다. `rail_credential_key.txt`는 `SECRET_ENCRYPTION_KEY`로 옮기며 사용되지 않던 `app_secret_key.txt`는 이관하지 않습니다.

`.env`는 Git과 Docker build context에서 제외되지만 Docker 환경변수는 호스트의 `docker inspect`와 Compose 렌더에서 보일 수 있습니다. 서버의 `.env` 읽기 권한을 관리자 계정으로 제한하고, 진단 자료·터미널 캡처·`docker compose config` 출력을 공유하지 않습니다. 검증은 `./scripts/ops.ps1 config`처럼 `--quiet` 경로만 사용합니다. `.env` 변경 후에는 컨테이너를 recreate해야 합니다.

DB dump에는 `.env`가 포함되지 않습니다. `SECRET_ENCRYPTION_KEY`를 잃으면 복원한 DB의 암호화된 알림 설정을 읽을 수 없으므로 `.env`를 DB dump와 분리된 암호화 저장소에 백업합니다. `BACKUP_AGE_IDENTITY`는 암호화 dump와 같은 위치에 두지 않습니다.

`SECRET_ENCRYPTION_KEY`는 알림 채널 설정뿐 아니라 설정 화면에서 등록한 KORAIL·SRT 로그인 ID와 비밀번호도 암호화합니다. 계정 API는 원문을 다시 반환하지 않고 마스킹한 ID와 credential version만 노출합니다. 로그인 중 받은 cookie·storage state·세션 token은 이 테이블, Redis, outbox, 로그에 저장하지 않습니다. 키를 바꾸려면 기존 암호문을 이전 키로 읽어 새 키로 다시 암호화하는 별도 절차가 필요하므로 `.env` 값만 먼저 교체하지 않습니다. 계정 삭제 API는 해당 운영사의 저장 credential을 제거하며, 이후 worker는 계정 없음으로 fail-closed해야 합니다.

## 시작과 상태 확인

```powershell
./scripts/ops.ps1 config
./scripts/ops.ps1 build
./scripts/ops.ps1 up
./scripts/ops.ps1 status
```

API는 `/healthz`, DB readiness는 `/readyz`, 웹은 Caddy의 `/healthz`로 확인합니다. scheduler는 반드시 한 개만 실행합니다. `worker`는 `rail` 큐만, `notification-worker`는 `notifications` 큐만 소비하며 두 worker의 concurrency는 각각 1로 유지합니다. 알림 채널의 느린 전송·재시도가 좌석 관측과 자동 예매 작업을 점유하지 않도록 두 큐를 같은 프로세스에서 함께 소비하지 않습니다. 한 due sweep의 KORAIL·SRT 파이프라인은 동시에 진행하지만, 같은 provider 안의 watch 그룹과 예약 재확인은 순차 실행합니다. 따라서 서로 다른 운영사는 병렬화하면서도 같은 계정의 브라우저·HTTP 인증 actor 및 provider execution lease는 직렬로 유지합니다. 한 provider가 예기치 않게 실패해도 다른 provider 작업은 완료한 뒤 worker 실패를 기록합니다.

### 기능·이미지 변경 배포

기능·코드 또는 Dockerfile·Compose·런타임 이미지 계약을 바꾼 배포에서는 과거 이미지가 일부 컨테이너에 남지 않도록, 현재 사용하는 Compose 프로필을 포함해 전체 서비스를 재빌드·재생성합니다. 단순 CSS·문서 변경에는 이 절차를 강제하지 않습니다.

```powershell
docker compose -f compose.yml config --quiet
docker compose -f compose.yml build
docker compose -f compose.yml up -d --force-recreate
docker compose -f compose.yml ps --all
docker compose -f compose.yml logs --tail=200 migration api worker scheduler web proxy
```

실험·monitoring 등 활성화한 프로필이 있으면 같은 `--profile` 인자를 위 모든 명령에 일관되게 넣습니다. `migration`은 `Exited (0)` 또는 `service_completed_successfully`여야 하고, 계속 실행되는 서비스는 `healthy`여야 합니다. 실패 또는 `unhealthy`가 있으면 원인을 해결한 뒤 다시 확인하며, 로그·상태 출력에는 비밀번호·cookie·token·connection URL 등 비밀값을 포함해 공유하지 않습니다. 이 재배포는 영속 데이터를 보존하는 방식이므로 `docker compose down -v`, volume 삭제, 무분별한 `down`을 사용하지 않습니다.

### 실험 자동 예매 운영 경계

`PROVIDER_BLOCKED`는 같은 로그인 검증 세대에서는 계속 차단합니다. 다만 차단 시도 종료보다 새로운 성공 로그인 검증 세대가 확인된 경우에는 `AUTH_REQUIRED`와 같은 세대 fence를 적용해 그 세대에서 한 번만 재무장합니다. 단순 상태 변경이나 같은 검증 시각 저장은 재무장 근거가 아닙니다.

기본 설정에서는 KORAIL·SRT의 자동 예매가 비활성입니다. 로그인 확인된 활성 철도 계정, 운영사별 관측·자동 예매 활성화 값, 작업의 `reserve_once_before_payment` 정책이 모두 충족한 경우에만 후보·가용성 에피소드별 DB fence 아래 예매 요청을 한 번 수행하고 `payment_required`에서 멈춥니다. 카드·CVC·결제 인증·자동 결제는 수행하지 않습니다. `NOT_AVAILABLE`은 예약 시점의 확정 비가용 근거이므로 그 시도 종료 뒤 처음 들어온 행동 가능 관측에는 `not-available-retry:<attempt-id>` 경쟁 소실 보정 시도를 한 번 허용합니다. 이 보정 시도도 `NOT_AVAILABLE`이면 같은 연속 관측으로 반복하지 않고, 이후 확정 비가용 observation과 새 행동 가능 observation이 순서대로 생긴 에피소드에서만 다시 재무장합니다. `AUTH_REQUIRED` 시도는 그보다 새로운 로그인 재검증 세대가 생긴 경우에만 한 번 재무장합니다. timeout·취소·만료·`FAILED`·`PROVIDER_BLOCKED`처럼 결과가 불명확하거나 보류 없음이 입증되지 않으면 같은 예매 요청을 재호출하지 않고 공식 예약 내역을 수동으로 확인합니다. `UNKNOWN`도 즉시 재호출하지 않으며, 아래의 읽기 전용 확인에서 최초 시도의 exact `NOT_FOUND`가 확인된 경우에만 같은 연속 `AVAILABLE` 구간에서 한 번 재무장합니다.

접속 중 웹은 `watch.reservation_attempted`를 목록 갱신 신호로만 쓰지 않고 즉시 `예매 진행 중` 알림으로 표시합니다. 따라서 수 초 안에 끝나는 `reserving` 전이도 REST snapshot 사이에서 사라지지 않습니다. `watch.reservation_result`와 `watch.reservation_result_requires_manual_check`는 같은 작업 카드를 결과 단계로 교체합니다. 카드 상단에는 현재 결과 발생 KST `HH:mm:ss`, 각 진행 단계 아래에는 좌석 발견·예매 시작·결과 시각을 각각 작게 표시합니다. 발견→시작은 `대기 N.N초`, 시작→결과는 `처리 N.N초`로 구분하고, 같은 watch의 결과 카드가 시작 카드를 교체할 때도 기존 단계 시각을 보존합니다. 자동 예매 작업에서 실제 attempt가 만들어지지 않은 fence 상태는 일반 `좌석을 찾았습니다` 알림으로 승격하지 않습니다.

로그인 확인 계정과 자동 예매 capability를 갖춘 `reserve_once_before_payment` 작업을 시작하면 API는 상태 commit 뒤 `process_watch_now`를 `rail` queue에 best-effort로 즉시 넣습니다. broker 장애로 enqueue가 실패해도 이미 시작한 작업을 실패로 되돌리지 않습니다. scheduler의 `process_due_watches`가 5초마다 DB의 due 작업을 다시 찾고 각 sweep도 5초 뒤 만료되므로 오래된 sweep이 queue에 쌓이지 않는 영속 fallback입니다. 출발 2~24시간 전은 unchanged backoff 없이 평균 60초, 2시간 이내는 평균 30초에 ±20% jitter로 관측하며, 24시간보다 먼 작업은 기존 5분·10분 base와 최대 3배 unchanged backoff를 유지합니다. source cache 기본 TTL은 20초이고 KORAIL HTTP lease는 기본 1800초·100회로 유지해 5분마다 반복되던 cold init을 줄입니다. provider execution lease는 120초로 API→adapter 90초 제한보다 길게 유지합니다. 즉시 처리 지연을 점검할 때는 start 응답의 성공 여부와 별개로 API의 enqueue 경고, `rail` worker 상태, scheduler 한 개의 beat 상태를 순서대로 확인합니다. 즉시 task를 수동으로 반복 제출해 보정하지 않습니다.

예약 결과가 `FAILED`·`UNKNOWN`이면 작업은 일반적으로 `watching`으로 돌아갑니다. `FAILED`는 실패·감시 복귀 이벤트와 활성 채널 알림을 남기며 저장된 로그인 성공 상태를 오류 하나로 무효화하지 않습니다. worker 재시작 뒤 5분 넘게 `PENDING`인 시도도 `UNKNOWN`으로 닫고 `watching`과 다음 관측 시각을 복구합니다. 이 결과들과 `PAYMENT_REQUIRED`·`RESERVED`·`PROVIDER_BLOCKED`는 기존 attempt와 수동 확인 근거를 유지하며 결과 자체만으로 자동 재시도하지 않습니다. `UNKNOWN`과 stale `PENDING → UNKNOWN`은 실제 예매에 사용한 credential generation의 인증 actor에서 공식 예약 내역을 빠르게 3회 확인하고, 계속 `INCONCLUSIVE`이면 5분·15분·60분 뒤까지 총 6회 확인합니다. 최초 `UNKNOWN`의 exact `NOT_FOUND`가 확인되면 `confirmed-absent-retry:<attempt.id>` 근거로 같은 연속 `AVAILABLE` 구간에서 한 번 재무장할 수 있지만, 재무장 시도도 `UNKNOWN`이면 추가 예약 호출은 금지합니다. 지난 기한 자체는 재예약 근거가 아니며, 기한 경과 `PAYMENT_REQUIRED`의 최종 exact `NOT_FOUND`가 확인되어도 같은 가용성 에피소드에서는 예약을 다시 호출하지 않습니다. 해당 작업은 그 뒤 확정 비가용 observation과 새 행동 가능 observation이 차례로 생긴 새 episode에서만 한 번 재시도할 수 있습니다. 단, 과거 버전에서 `payment_deadline`과 `post_deadline_reconciled_at`가 모두 누락된 `PAYMENT_REQUIRED`는 공식 exact `NOT_FOUND` 확인과 그 이후 행동 가능 관측이 모두 있을 때에만 `confirmed-absent-retry` 한 번을 허용합니다. begin 단계에서도 같은 DB 근거를 다시 확인하며, 새 시도 결과를 이용한 재연쇄는 차단합니다.

로그인 뒤 `설정 > 로그·진행 상태`에서 최근 24시간 처리량, 현재 대기 상태, 좌석 관측 오류율, 알림 최종 실패율, provider circuit과 식별정보를 제거한 최근 진행 기록을 확인할 수 있습니다. 별도 `좌석 조회 제공원 상태` 섹션은 KORAIL browser·SRT live의 현재 `ready|cooldown`, 정규화한 원인과 남은 시간을 보여줍니다. 새로고침 실패 시 화면은 마지막 정상 집계를 유지하고 오류를 함께 표시합니다. 분모가 없는 비율은 `기록 없음`이며 0%가 아닙니다.

`설정 > 화면 동작`의 열차 목록 동기화 간격은 관리자 계정에 영속되며 기본 5초, 허용 범위 5~300초입니다. 이 값은 새 대기 3단계의 snapshot 확인과 홈의 내부 `/watches` 갱신 주기이며, worker의 좌석 관측 주기나 공식 철도사 호출 간격을 뜻하지 않습니다. 서버 재시작 또는 API replica 변경으로 메모리 snapshot이 없으면 자동 동기화는 404 miss를 정상적으로 무시하고 현재 카드를 유지합니다. cache hit은 마지막 정상값을 즉시 반환하고 저장 뒤 60초가 지난 동일 query만 백그라운드에서 한 번 재검증합니다. 동시 탭·요청은 프로세스 내 singleflight로 합칩니다. KORAIL 공식 HTTP 응답이 성공하고 열차 목록이 비어 있으면 정상 빈 결과로 처리하며 cooldown을 열지 않습니다. 일반 source·DOM 실패는 출발·도착·서비스일·시간창·승객 수가 같은 exact query에만 30초부터 최대 5분까지 backoff하고, 403·423·429처럼 명시적인 접근 제한·rate-limit 신호만 Redis provider cooldown으로 모든 query에 공유합니다. 원형 수동 새로고침은 사용자가 명시적으로 기존 시간표 조회를 한 번 다시 실행하므로 같은 singleflight·cache·cooldown·보호 중단 규칙이 적용됩니다. 현재 snapshot cache와 revalidation singleflight는 API 단일 replica 운영을 전제로 하며 replica를 늘리기 전 Redis나 PostgreSQL 공유 cache로 옮겨야 합니다.

같은 `GET/PATCH /api/v1/preferences/ui`의 좌석 관측값은 관리자 계정에 영속됩니다. 저장 성공 뒤 아직 due가 아니고 실행 lease·cooldown이 없는 모든 활성 작업은 새 전역값으로 다음 관측 시각을 다시 계산합니다. 이미 due·실행 중이거나 cooldown인 작업은 현재 실행·보호 상태를 건드리지 않고 완료 또는 해제 뒤 최신값을 사용합니다. 운영 중 값이 반영되지 않은 것처럼 보이면 먼저 `GET /api/v1/preferences/ui`의 저장값, 작업의 `next_check_at`, provider lease와 `cooldown_until` 순서로 확인합니다. DB를 직접 수정하거나 화면 갱신 초를 낮춰 보정하지 않습니다.

홈의 활동 중 티켓마다 `감시만`/`좌석 재발견마다 자동 예매` 스위치가 있습니다. 이 조작은 티켓을 삭제하거나 다시 만들지 않고 `reservation_policy`만 갱신합니다. 자동 예매를 켜려면 같은 운영사의 저장 계정이 활성·로그인 확인 상태여야 하며, 실행 환경의 예약 capability gate도 별도로 충족해야 실제 시도가 열립니다. PATCH 중 이전 목록 조회가 늦게 도착해도 화면은 그 snapshot을 폐기하고 새 목록을 요청합니다. worker도 attempt fence 생성 직전에 계정 행을 잠가 최신 `authenticated` 상태를 다시 확인하며, 계정이 해제·실패 상태이면 attempt를 만들지 않고 작업을 `auth_required`로 닫습니다. 정책을 껐다 다시 켜도 기존 `reservation_attempts`와 episode fence는 보존되며, 정책 변경 자체로 같은 에피소드를 재시도하지 않습니다. 후보별 최신 시도 outcome·시각·재시도 조건은 홈 행에 계속 표시되므로 짧은 `reserving` 전이를 놓쳐도 좌석 확보 실패·수동 확인·계정 재검증 필요 여부를 확인할 수 있습니다. `reserving`, `payment_required`, 완료·만료·실패 상태에서는 정책을 변경하지 않습니다. 행 배치는 viewport가 아니라 `.watch-list` 컨테이너 폭으로 전환합니다. 1080px 이하에서는 상태와 정책·예매·제어 영역을 좌우 독립 영역으로 분리하고, 760px 이하에서는 상태와 행동을 각각 전체 행으로, 520px 이하에서는 운영사·시간·정책·예매·제어도 한 열로 쌓습니다. 정책 문구는 줄바꿈하되 스위치·일시정지·취소의 44px 영역을 축소하지 않으며, 같은 열차의 일반실·특실 정책 스위치는 접근 가능한 이름에 좌석 등급을 포함해 구분합니다.

`GET/PATCH /api/v1/preferences/ui`는 `timetable_refresh_interval_seconds`와 `preferences_updated_at`을 반환합니다. 웹은 이 snake_case DTO를 경계에서 검증한 뒤 화면 모델의 `updatedAt`으로 변환하므로 정상 응답을 형식 오류 알림으로 표시하지 않습니다.

기존 DB에는 배포 시 migration `0016_admin_ui_preferences`를 적용해야 합니다. migration은 단일 관리자 행에 `timetable_refresh_interval_seconds=5`와 갱신 시각을 추가하며 환경변수나 비밀값을 새로 요구하지 않습니다.

철도 계정 저장은 먼저 DB의 다음 credential generation을 정하고 그 generation으로 운영사 로그인을 검증합니다. 검증 뒤 계정 행을 다시 잠가 현재 generation의 정확한 다음 값일 때만 저장하므로, 검증 중 다른 변경이 먼저 반영되면 충돌 응답으로 닫고 최신 계정을 덮지 않습니다. KORAIL Chromium sidecar와 SRT provider sidecar는 각각 한 프로세스 안에서 provider별 인증·예약·공식 예약 확인 actor를 하나만 소유하고 동시 접근을 직렬화합니다. 같은 credential generation과 마지막 사용 기준의 로컬 재사용 한도가 모두 유효할 때만 재사용하며, version 변경·한도 만료·명시적인 `auth_required`에서는 기존 세션을 폐기한 뒤 허용된 한 번의 새 로그인을 수행합니다. KORAIL 계정 검증에서 만든 browser session은 같은 generation에 더해 `login_method`·`login_id`·비밀번호의 프로세스 메모리 SHA-256 fingerprint까지 일치할 때 예약과 공식 보류 확인에서 재사용합니다. fingerprint 자체도 비밀 파생값으로 보고 로그·응답·DB·artifact에 남기지 않습니다. background·read-only 시간표 검색은 별도 검색 actor의 ephemeral browser lease를 사용하고 HTTP replay lease로 전환할 때도 인증 actor의 session을 소비하거나 폐기하지 않습니다. 예약 adapter는 실제 외부 호출에 사용한 credential version을 내부 결과에 포함하고 worker는 이 값으로 계정 상태를 CAS합니다. preflight 뒤 credential이 바뀌어 실제 호출이 새 version을 사용해도 과거 preflight 값으로 오판하지 않으며, 결과 기록 전에 더 최신 계정이 저장됐다면 늦은 결과를 무시합니다. 로그인 저장과 예약 worker는 모두 provider account → watch 순서로 행을 잠가 PostgreSQL 교착을 피합니다. 컨테이너가 재시작되면 메모리 세션을 복구하지 않고 DB의 암호화 credential을 다시 읽어 새 세션을 검증합니다. 비밀번호·cookie·storage state·session token은 DB, Redis, 로그, artifact에 쓰지 않습니다. 이 동작은 격리 fixture 회귀로 검증했으며 실제 운영사 로그인 세션의 장시간 안정성은 별도 운영 확인 항목입니다.

### 운영사 상주 런타임과 prewarm

`experimental-rail` 프로필은 `korail-browser-adapter`와 `srt-provider-adapter`를 서로 다른 sidecar로 실행합니다. KORAIL sidecar는 read-only 시간표 검색 actor의 ephemeral browser/HTTP replay lease와 인증·예약·공식 예약 확인 actor를 분리하고, SRT sidecar는 계정 없는 검색 `source`와 인증·예약·공식 예약 확인 `executor`를 분리합니다. API·worker는 내부 Bearer token이 있는 고정 Compose origin으로만 호출합니다. 어느 검색 actor도 인증 actor의 cookie나 credential을 영속화하거나 검색 실패를 이유로 인증 세션을 교체하지 않습니다. 반대로 공식 예약 확인은 검색 actor로 우회하지 않고 실제 예약 시도에 사용한 credential generation의 인증 actor만 사용합니다.

API 시작 시 DB에서 `enabled=true`인 저장 계정을 마지막 상태가 `authenticated`인지 `auth_required`인지와 관계없이 한 번 prewarm합니다. 이 작업은 저장된 암호문을 메모리에서 복호화해 해당 sidecar의 현재 credential generation으로 새 인증 세션을 준비합니다. 성공한 경우에만 계정 행을 다시 잠가 generation이 여전히 같을 때 DB의 인증 상태와 마지막 검증 시각을 갱신하고 관련 `auth_required` 작업을 재개합니다. 실패·차단·세대 변경은 기존 성공 시각과 최신 계정 상태를 덮지 않습니다. 컨테이너 재시작 뒤에는 이전 프로세스의 cookie·storage state를 복구하지 않고 새 프로세스 메모리에서 다시 로그인 확인합니다.

인증된 `GET /api/v1/provider-runtime-status`는 운영사별 `cold|authenticating|ready|stale|auth_required|blocked`, credential generation, 생성·마지막 검증·마지막 사용 후 경과 초, `locally_reusable`, 현재 프로세스의 남은 로컬 재사용 초와 startup prewarm 결과만 `Cache-Control: no-store`로 반환합니다. 경과 시간과 남은 시간은 monotonic process telemetry이며 운영사가 공개한 고정 세션 TTL, 장시간 로그인 보장 또는 원격 세션 만료시각으로 해석하지 않습니다. 실제 계정으로 장시간 TTL·재시작·보호 응답 뒤 fail-closed를 확인하기 전에는 운영 완료로 표시하지 않습니다.

### 읽기 전용 공식 예약 확인과 reconciliation

예약 호출 결과가 `PAYMENT_REQUIRED` 또는 `UNKNOWN`이고 attempt에 실제 사용 credential version이 남아 있으면 worker는 같은 version의 활성 계정과 provider/account 실행 임대를 다시 확인한 뒤 공식 보류를 읽기 전용으로 확인합니다. 이 확인은 공개 시간표·계정 없는 좌석 source가 아니라 실제 예매에 사용한 동일 credential generation의 로그인된 인증 actor에서만 수행합니다. 세대가 달라졌거나 로그인 상태를 증명하지 못하면 예약·결제 상태를 쓰지 않고 `INCONCLUSIVE` 또는 `AUTH_REQUIRED`로 닫습니다. `PAYMENT_REQUIRED`는 빠른 확인을 최대 3회 수행하고, 결제기한이 경과한 뒤 `post_deadline_reconciled_at`이 없을 때 공식 예약목록을 한 번 최종 확인합니다. `NOT_FOUND` 또는 exact 행의 공식 기한이 이미 지난 결과는 행동 가능한 결제 보류 종료로 처리합니다. 이전 버전이 과거 기한 exact 행에 marker만 남긴 경우 호환성 정리 확인을 한 번 더 수행하고 횟수를 올려 반복하지 않습니다. `UNKNOWN + INCONCLUSIVE`는 빠른 3회 뒤 5분·15분·60분 지연 확인을 이어 총 6회까지만 수행합니다. 기존 데이터에서 `reconciliation_attempt_count=3`이고 다음 확인 시각이 비어 있는 `UNKNOWN + INCONCLUSIVE`도 첫 지연 확인 대상으로 자동 복구하므로 운영자가 횟수나 시각을 직접 수정하지 않습니다. 기한 없는 exact 행·`AUTH_REQUIRED`·`PROVIDER_BLOCKED`·끝까지 `INCONCLUSIVE`인 결과는 fail-closed로 종료합니다. KORAIL은 동일 인증 browser session의 현재 상세 화면을 먼저 읽고 exact 근거가 없으면 공식 예약목록으로 이동해 확인합니다. SRT 자동 예매 결과는 열차·구간·서비스일·출발시각·ticket 좌석 등급·seat count가 요청과 exact match할 때만 즉시 확정하고, 불명확한 결과만 같은 credential generation의 공식 예약목록에서 다시 확인합니다. 예매 버튼 재클릭, 예약 취소, 결제, 카드·CVC 입력은 이 작업의 범위가 아닙니다.

확인 결과는 `confirmation_outcome`, `confirmation_source`, `confirmation_observed_at`, `last_reconciled_at`, `reconciliation_attempt_count`, `next_reconcile_at`, `post_deadline_reconciled_at`에 원문 없이 기록합니다. 앞의 confirmation 필드는 migration `0020_reservation_reconciliation`, 일반 확인 횟수와 다음 실행 시각은 migration `0021_bounded_reservation_reconciliation`, 기한 경과 후 최종 확인 marker와 index는 migration `0022_post_deadline_reconciliation`에서 추가합니다. KORAIL 예약목록은 열차번호·출발역·도착역·서비스일·출도착시각·인원과 유일한 미결제 행동을 exact match하며, 목록에 좌석 등급이 없으면 등급 일치가 확인됐다고 기록하지 않습니다. 현재 상세 화면과 SRT 예약목록은 좌석 등급·인원까지 제공되는 범위에서 exact target과 대조합니다. KORAIL 점 구분 결제기한과 SRT 결제일·결제시각은 KST timezone-aware 값으로 저장합니다. 정확한 미결제 보류 하나가 확인된 경우에만 usable `payment_required` 인계를 복구합니다. 최초 `UNKNOWN` 시도의 확인에서 정상 로드된 공식 목록의 exact 대상 0건이 확인되면 `confirmed-absent-retry:<attempt.id>`를 사용해 같은 연속 `AVAILABLE` 구간에서 한 번 재무장합니다. 재무장 시도가 `UNKNOWN`이면 확인 결과가 다시 `NOT_FOUND`여도 추가 예약 호출을 만들지 않습니다. 결제기한 경과 `PAYMENT_REQUIRED`의 최종 exact `NOT_FOUND`는 기존대로 `notify_only` 작업을 `expired`로 종료하거나 `reserve_once_before_payment` 작업을 `watching`으로 복귀시키되, 같은 episode fence를 유지하고 marker 뒤 확정 비가용→새 행동 가능 관측이 생긴 경우에만 새 episode에서 시도합니다. `INCONCLUSIVE`·인증·차단 결과는 상태를 추정하지 않습니다. worker는 신규 due 관측·에피소드당 1회 자동 예매를 과거 reconciliation backlog보다 먼저 처리합니다. 현재 구현·로컬 계약 테스트와 별개로 실제 운영 KORAIL·SRT 예약목록의 장시간 안정성은 운영 확인 항목입니다.

운영 화면에서 과거 `좌석 임시 확보 · 결제 필요`가 계속 보이면 후보의 최신 attempt에서 `confirmation_outcome=NOT_FOUND`와 `post_deadline_reconciled_at`이 함께 반환되는지 확인합니다. 두 값이 모두 확인된 자동 예매 작업은 홈에서 `결제 보류 종료 확인 · 감시 계속`으로 표시되어야 하며, 같은 에피소드를 즉시 재예약하지 않고 다음 확정 비가용→행동 가능 관측을 기다려야 합니다. marker가 없으면 최종 보류 소실을 뜻하지 않으므로 결제 필요 또는 공식 내역 확인 상태를 임의로 바꾸지 않습니다.

로그인 중 웹 알림은 단일 `실시간 알림` surface에서 결제·수동 확인·인증·좌석 발견을 우선하고 진행·복구·일반 알림을 뒤에 둡니다. 동일 watch는 최신 revision 한 장만 유지하며 같은 revision이나 더 오래된 `revisionAt`을 다시 안내하지 않습니다. 종류별 건수·펼치기·그룹 닫기가 있고 surface 접기는 확인이나 삭제가 아닙니다. 접힌 동안 카드가 언마운트되지 않아 일반 30초와 완료된 단계형 복구 60초 타이머가 계속 흐릅니다. 진행 중 예매와 행동 필요 알림은 최종 result revision으로 교체되거나 사용자가 닫기 전에는 자동으로 닫지 않습니다. KORAIL result event에는 인증 세션 확인·대상 재확인·좌석 선택·예약 요청 중 실제 도달한 단계의 시각만 들어가며, 웹은 이를 KST 시각과 단계 사이 처리시간으로 표시합니다.

## 알림 채널 설정

`설정 > 알림 채널`의 스위치는 미설정 채널에서도 사용할 수 있습니다. `OS 알림`을 켜면 현재
브라우저의 service worker 준비와 알림 권한·구독을 진행하고, Telegram·Discord·범용 Webhook을
켜면 필요한 설정 입력 화면을 엽니다. 설정이 저장된 채널은 같은 스위치로 활성·비활성을 바꿉니다.
모바일에서도 별도 `연결` 버튼이 숨겨지지 않으며 스위치와 설정 행동 모두 최소 44px 영역을
유지해야 합니다.

`OS 알림`은 서버의 Web Push 채널과 현재 브라우저 프로필의 device subscription을 함께 뜻합니다.
접속 중 React 화면의 `실시간 알림`과는 별도 채널이므로 OS 알림을 꺼도 화면 알림은 유지되고, 반대로
화면을 닫아도 브라우저·운영체제가 background push를 허용하는 동안에는 OS 알림 영역으로 전달될 수
있습니다. 브라우저를 완전히 종료했거나 운영체제가 background 동작을 차단한 경우의 전달은 보장하지
않습니다. Desktop Chrome·Edge와 Android는 PWA 설치가 필수는 아니지만 같은 브라우저 프로필의
구독을 유지해야 합니다. iOS·iPadOS는 16.4 이상에서 홈 화면에 추가한 PWA를 열고 사용자 동작으로
권한을 허용해야 합니다.

OS 알림 연결이 계속 진행 중으로 남으면 HTTPS 또는 loopback secure context인지, production web
image에서 service worker가 등록됐는지, API의 VAPID public key가 준비됐는지 확인합니다. service
worker readiness는 bounded timeout 뒤 읽을 수 있는 오류로 종료해야 하며 무기한 대기하지 않습니다.
`localhost`와 `127.0.0.1`은 loopback 개발 origin으로 사용할 수 있지만 일반 LAN 평문 HTTP 주소는
secure context가 아니므로 사용할 수 없습니다. 브라우저에서 권한을 거부한 경우 운영자가 해당
origin의 알림 권한을 직접 복구한 뒤 다시 시도합니다.

VAPID key는 URL-safe Base64 쌍을 권장합니다. 기존 운영 환경에서 private key를 PKCS8 PEM으로
주입했다면 `.env` 한 줄의 줄바꿈을 `\n`으로 보존하며, API는 이를 메모리에서 pywebpush가 읽는
base64url DER 형식으로 정규화합니다. 잘못된 PEM은 원문을 로그에 남기지 않고
`webpush_vapid_key_invalid`로 닫힙니다. 이미 최종 `failed`가 된 과거 outbox 행은 재배포만으로
자동 재전송하지 않으므로 수정 배포 뒤 설정 화면의 `시험`으로 새 이벤트를 만들어 확인합니다.

전송 전 구독 형식이 잘못되면 `webpush_subscription_invalid`, push service의 404·410은
`webpush_subscription_expired`, 400은 `webpush_subscription_rejected`로 기록합니다. VAPID
401·403은 `webpush_vapid_auth_failed`, 키·subject 설정 오류는 각각
`webpush_vapid_key_invalid`·`webpush_vapid_configuration_invalid`, 그 밖의 상류 장애는
`webpush_provider_error`로 정규화합니다. 구독 만료·거부는 브라우저에서 OS 알림 연결을 다시
생성하고, VAPID 오류는 서버 키 쌍과 subject를 고친 뒤 이미지를 재빌드합니다. 어느 범주에도
endpoint·키·push provider 응답 본문을 로그나 outbox 오류에 기록하지 않습니다.

알림 채널은 작업별 snapshot이 아니라 단일 관리자 전역 설정입니다. 현재 활성화한 채널은 기존에
이미 활동 중이던 대기의 다음 상태 알림부터 사용되며, 비활성화한 채널에는 이후 새 outbox 전달을
시도하지 않습니다. 화면에서 `연결` 또는 활성 표시가 보이는 것만으로 실제 외부 전달 성공을
뜻하지 않으므로 각 채널의 테스트 전송과 수신 단말 확인은 별도 운영 smoke test로 수행합니다.
OS 알림 smoke test는 스위치를 켠 뒤 `시험`을 눌러 outbox가 최종 성공하고, 해당 기기의 운영체제
알림 영역에 제목·본문이 표시되며 알림을 누르면 레일웨잇 origin이 열리는 것까지 확인합니다. 같은
구독은 중복 생성하지 않고 재사용하며, 서버 채널만 켜져 있고 현재 기기 subscription이 없으면 UI는
`다시 연결 필요`로 표시해야 합니다.

## 서비스 파일 로그

기본 로그 경로는 Compose root의 `logs/`이며 서비스별 현재 파일은 `api/current.log`, `worker/current.log`, `notification-worker/current.log`, `scheduler/current.log`, `experimental-rail/current.log`, `korail-browser-adapter/current.log`입니다. `log-init`가 시작 전에 디렉터리를 만들기 때문에 이 서비스들이 기동하지 않으면 먼저 `docker compose logs log-init`을 확인합니다. Windows Docker Desktop에서는 Compose를 실행하는 Windows 계정과 Administrators만 `logs`에 접근하도록 ACL을 제한합니다. Linux 컨테이너 directory는 `0750`이며 app 계열은 UID:GID `100:101`, sidecar는 `1001:1001`입니다.

각 파일은 JSONL이며 기본 5 MiB에서 `current.log.1`부터 `.4`까지 회전합니다. 따라서 서비스당 최대는 약 25 MiB입니다. stdout/stderr도 유지되며 Docker `local` driver는 10 MiB·3파일 제한을 별도로 적용합니다. 파일 tail은 `Get-Content -Wait .\logs\api\current.log`, 컨테이너 실시간 출력은 `docker compose logs -f api`처럼 구분해 사용합니다. `.env`의 `APP_LOG_MAX_BYTES`, `APP_LOG_BACKUP_COUNT`, `APP_LOG_LEVEL`을 바꾼 뒤 `./scripts/ops.ps1 config`과 `./scripts/ops.ps1 up`으로 recreate합니다.

로그를 정리할 때는 해당 서비스를 중지한 뒤 service directory의 `current.log`, archive와 `.lock`을 함께 다룹니다. `logs/`는 Git·일반 진단 첨부·일반 DB 백업에 기본 포함하지 않습니다. rotation이 되지 않거나 파일이 생기지 않으면 host 디스크 여유, `log-init` 완료 상태, `logs/` ACL과 소유권을 확인합니다.

기본 설정 화면에는 레거시 Browser Companion 연결 코드·pairing UI를 표시하지 않습니다.
KORAIL 좌석 보강은 서버 관리형 Chromium sidecar 상태로만 설명하며, 확장 설치를 정상 운영
절차로 요구하지 않습니다. 레거시 API와 저장 데이터는 기존 설치 호환·철회 목적으로만 남습니다.

이 화면은 Docker 원문 로그 뷰어가 아닙니다. API·DB는 집계 요청 시점에만 확인하고, 영속 heartbeat가 없는 worker·scheduler와 영속 HTTP 오류율은 `확인 불가`로 표시합니다. 컨테이너 자체 상태는 계속 `./scripts/ops.ps1 status`와 healthcheck로, 프로세스 메트릭은 필요한 경우 monitoring 프로필로 별도 확인합니다. 관리자 화면에 원문 로그를 붙이거나 secret·URL·역·열차·내부 식별자를 노출하지 않습니다.

변경 검증의 결정적 전체 경계는 `apps/web`에서 `npm run test:e2e:fullstack`으로 실행합니다.
스크립트가 임시 Compose project·포트·비밀값·DB/Redis/Caddy volume을 만들고 실제 API·웹·
worker·scheduler·KORAIL Chromium sidecar와 내부 fixture를 기동하며 종료 시
`down --volumes --remove-orphans`를 수행합니다. 실행 뒤 같은 접두사의 컨테이너와 volume이 남지
않았는지 확인합니다. `TAGO_BASE_URL`, `KORAIL_STATION_DATA_URL`, `SRT_FULLSTACK_FIXTURE_URL`과
KORAIL test page override는 `ENVIRONMENT=test`와 각 고정 내부 URL 조합에서만 허용되므로 운영
`.env`에 넣지 않습니다.

이 검사는 KORAIL 좌석 snapshot fake stub을 사용하지 않습니다. 실제 sidecar가 `app` 내부망의
고정 HTML page에서 보이는 역·날짜·시간·조회 컨트롤을 Playwright로 조작하고, 가시 요소를 확인한
실제 mouse click 뒤 결과 DOM을 정규화합니다. API가 exact match한 KORAIL `official_provider`
상태와 공식 예매 CTA를 확인하고, 격리 환경에서 KORAIL 좌석 감시 3중 opt-in을 켜
KORAIL 매진 특실 대기 1건을 evidence-bound로 등록합니다. 그다음 같은 브라우저 흐름에서 SRT 좌석
등급 3건을 추가해 총 4건을 검증합니다. DB verifier는 KORAIL `watching`과 SRT
`watching`·`seat_found`·`official_waitlist` 전이, 운영사별 실행 임대의 fencing·해제,
예약 시도 0건, 알림 outbox 2건의 독립 생성과 최소 1회 재시도를 확인합니다. sidecar와 page는
`browser-egress` 없이 `app`에만 연결되므로 실제 철도사 endpoint 호출은 0건입니다. 합성 알림
채널도 외부 egress가 없으므로 이 결과는 실제 알림 전달 성공이 아니라 `pending|failed`
fail-closed와 재시도 계약만 증명합니다.

로컬 Vite 개발 서버는 저장소 루트 `.env`의 `VITE_DEMO_MODE=false`를 읽고 `/api`를 로컬 Caddy로 proxy합니다. 따라서 실제 TAGO 모드에서도 브라우저에 철도 API 키를 노출하지 않습니다. 실데이터 모드의 최초 설정은 다음 순서로 진행합니다.

1. Caddy의 기본 loopback bind나 Tailscale 접속만 유지하고 공개 도메인을 열지 않습니다.
2. `.env`의 `AUTH_INITIAL_REGISTRATION_ENABLED=true`로 API를 기동합니다.
3. 화면의 `초기 관리자 등록`에서 관리자 ID와 12자 이상의 비밀번호를 등록합니다.
4. 정규화한 ID·Argon2id 비밀번호 해시·세션이 PostgreSQL에 저장되고 앱에 진입한 것을 확인합니다.
5. `.env` 값을 `false`로 되돌리고 API·worker·scheduler를 재시작합니다.

계정이 이미 존재하면 스위치 값과 관계없이 추가 등록은 `409`로 닫힙니다. 이후 로그아웃 또는 세션 만료 때는 같은 ID와 비밀번호로 로그인합니다. 기존 인증 버전에서 업그레이드하면 이전 세션이 마이그레이션에서 무효화되므로, 공개 proxy를 중단한 상태에서 위 절차를 완료한 뒤 다시 공개합니다.

`AUTH_ALLOWED_ORIGINS`에는 포트를 포함한 실제 브라우저 origin을 적습니다. 기본 Vite 주소 `http://127.0.0.1:4173`을 사용하면 같은 값을 목록에 포함해야 하며, `localhost`와 `127.0.0.1`을 임의로 바꾸면 등록·로그인이 `untrusted or missing Origin`으로 거부됩니다.

## 역 카탈로그 스냅샷

migration `0007`은 TAGO 원본 역 식별자와 KORAIL 공개 역 안내 교집합을 `station_catalog_cache`에 함께 저장합니다. 두 출처는 24시간마다 갱신하며, 신선한 PostgreSQL 스냅샷이 있으면 API 재시작 때 상류 호출 없이 검증 카탈로그와 화면 목록을 복원합니다. 만료된 스냅샷은 요청에 즉시 제공하고 DB lease를 얻은 한 replica만 백그라운드 갱신합니다. lease가 만료된 이전 owner의 늦은 결과는 기록되지 않습니다.

수집 실패·빈 목록·손상 응답·KORAIL 역 안내 sentinel 누락·빈 교집합은 기존 정상 스냅샷을 덮지 않습니다. 기존 스냅샷이 없으면 `/api/v1/stations`는 `503`으로 닫히며, 화면용 교집합 대신 원본 TAGO 역을 노출하는 fallback은 없습니다. 운영 점검에서는 [KORAIL 공개 역 안내](https://www.korail.com/public/st_info/station_data.json)와 TAGO 양쪽의 접근 가능성을 확인하되 secret이나 원문 오류 응답을 로그에 남기지 않습니다.

## 프로필

- `docker compose --profile experimental-rail up -d`: 실험 worker와 KORAIL Chromium·SRT provider sidecar를 시작합니다. API 환경변수도 아래와 같이 명시적으로 켜야 하며 기본 프로필에는 포함되지 않습니다.
- `./scripts/ops.ps1 monitoring`: Prometheus·Grafana를 시작합니다.
- `./scripts/ops.ps1 ntfy`: 선택적인 내부 ntfy를 시작합니다.
- `./scripts/ops.ps1 backup`: 즉시 암호화 백업을 수행합니다.

웹은 새 작업을 생성 직후 시작하고, 2단계에서 작업별 `알림만 받기` 또는 `좌석 재발견마다 자동 예매(에피소드당 1회)`를 선택합니다. 선택한 모든 운영사에 로그인 확인된 활성 철도 계정이 있으면 새 작업은 후자를 기본으로 선택하며, 사용자는 언제든 `알림만 받기`로 바꿀 수 있습니다. 계정이 없거나 비활성·미인증이면 `알림만 받기`가 기본값이고 자동 예매 선택은 비활성화합니다. 기본 설정에서는 KORAIL·SRT background 감시와 자동 예매가 모두 꺼져 있습니다. 각 운영사에 필요한 세 값을 모두 명시했을 때만 `seat_monitoring`이 열리며, 여기에 `KORAIL_RESERVATION_ONCE_ENABLED=true` 또는 `SRT_RESERVATION_ONCE_ENABLED=true`, 설정 화면의 활성 철도 계정, 작업별 자동 예매 정책이 모두 있어야 예약 호출이 실행됩니다. 자동 시작 자체를 capability 활성화로 해석하지 않습니다.

시간표와 실행 provider registry도 코드에서 분리돼 있습니다. API·작업 생성은 시간표 registry,
worker는 실행 registry만 사용합니다. KORAIL·SRT 실행 registry는 각각 세 설정값의 교집합만
허용합니다. 좌석 카드가 실제 관측값을 표시했다는 사실 하나만으로 worker
capability가 열리지는 않습니다.

`SRT_SEAT_STATUS_ENABLED=true`만 지정하면 사용자가 `/timetables`를 조회할 때 수행하는 계정 없는 좌석 상태 보강만 활성화합니다. background worker 감시에는 `EXPERIMENTAL_RAIL_ENABLED=true`와 `SRT_SEAT_MONITORING_ENABLED=true`도 함께 필요합니다. KORAIL은 구형 korail2 0.4.0 경로를 시간표 API에서 제거했고 현재 주 UI와 선택적 background worker는 서버 관리형 표준 Chromium 어댑터를 사용합니다. background에는 `EXPERIMENTAL_RAIL_ENABLED=true`, `KORAIL_BROWSER_ADAPTER_ENABLED=true`, `KORAIL_SEAT_MONITORING_ENABLED=true`가 모두 필요합니다. 꺼져 있거나 실패하거나 cooldown이면 KORAIL 좌석을 추정하지 않고 `unknown`으로 닫습니다. Browser Companion은 주 UI에서 제거된 레거시 호환 경로입니다. 운영사별 자동 예매 gate가 꺼진 관측 경로는 로그인·예약을 수행하지 않으며, gate가 켜져도 자동 결제는 수행하지 않습니다.

2026-07-30 live sidecar 단발 운영 확인에서는 서울→부산, 2026-08-01, 09:00–12:00 조건이 위 UI 흐름을 통과했지만 sidecar가 공식 403 또는 화면 보호 문구를 감지해 내부 HTTP 423 `provider_access_restricted`로 정규화했습니다. 당시 기록만으로 어느 trigger였는지는 구분할 수 없으며 공식 KORAIL이 HTTP 423을 반환했다고 해석하지 않습니다. sidecar는 추가 요청 없이 즉시 중단하고 cooldown을 기록했습니다. 이 결과는 좌석 snapshot 성공이나 감시·예약 capability 활성화 근거가 아니므로 운영 화면은 해당 좌석을 `unknown`으로 유지해야 합니다.

### 철도 계정 연결과 로그인 확인

1. 관리자 로그인 뒤 `설정 > 철도 계정`으로 이동합니다.
2. KORAIL 또는 SRT 카드에서 `회원번호`, `이메일`, `휴대전화` 중 공식 계정에 맞는 방식을 선택합니다. 이메일·휴대전화는 운영사 회원정보에서 사전 인증된 값이어야 합니다.
3. 비밀번호는 설정 화면에만 입력하고 `로그인 확인 후 저장`을 누릅니다. 서버는 시간표 조회·예약 없이 로그인을 정확히 한 번만 시도합니다. KORAIL은 재사용 중인 검색·예약 세션을 닫고 새 Pydoll CDP context에서 입력한 계정을 검증합니다.
4. 성공 응답을 받은 경우에만 암호화 저장하고 `로그인 확인됨`을 표시합니다. 실패·접근 제한·timeout이면 입력한 비밀번호를 저장하지 않으며 기존 연결을 교체하지 않습니다.
5. 공식 화면은 비밀번호 5회 오류 시 로그인을 제한하므로 실패 버튼을 반복해서 누르지 않습니다. 실제 값은 채팅, `.env.example`, 로그, screenshot, fixture에 넣지 않습니다.

로그인 재검증에 성공하면 해당 운영사의 현재 `auth_required` 작업 가운데 최신 transition이 과거 `reservation_auth_required`이거나, 예약 attempt를 만들기 전 계정 preflight에서 멈춘 `provider_account_not_authenticated_before_reservation`이고 재검증보다 오래된 항목을 `scheduled`로 재개합니다. preflight 중단 경로에는 `ReservationAttempt`가 없으므로 후보 상태와 initial episode fence를 바꾸지 않고 다음 관측만 허용합니다. 기존 `ReservationAttempt`는 삭제하지 않지만 `last_authenticated_at > attempt.finished_at`인 새 성공 검증과 현재 credential version을 하나의 로그인 검증 세대로 사용해, 이전 결과가 `AUTH_REQUIRED`였던 후보만 그 세대에서 한 번 재무장합니다. 같은 재검증 세대의 반복 저장·새로고침은 다시 시도하지 않습니다. 웹의 홈 경고는 API가 지금도 `auth_required`로 반환한 작업에만 컴팩트하게 표시하며, 재검증 뒤에는 과거 인증 실패 toast를 제거하고 감시·예약 준비 재개 안내로 교체합니다. 재검증 뒤에도 경고가 남으면 `/watches` 최신 응답과 마지막 transition reason을 확인하고 작업을 새로 만들거나 예약 task를 반복 제출하지 않습니다.

API 재시작 시 startup prewarm도 활성·설정된 `auth_required` 계정을 다시 확인합니다. 성공한 경우에만 현재 credential generation CAS로 계정 상태를 `authenticated`로 갱신하고 위와 같은 작업 재무장을 같은 transaction에서 수행합니다. 실패·차단·인증 필요 결과는 기존 성공 시각을 덮지 않습니다. KORAIL SPA의 현재 URL이 잠시 `/ticket/login`에 머문 것만으로 인증 실패를 확정하지 않으며, 같은 browser context의 공식 login check와 인증 헤더가 모두 비인증일 때만 `auth_required`로 판정합니다.

KORAIL 계정 검증에는 `EXPERIMENTAL_RAIL_ENABLED=true`, `KORAIL_BROWSER_ADAPTER_ENABLED=true`, `KORAIL_BROWSER_ENGINE=pydoll`과 정상 sidecar/token이 필요합니다. 진단용 `playwright_direct_cdp` 엔진은 시간표·좌석 snapshot 전용이며 로그인 검증·예약은 `login_verification_not_ready`로 닫습니다. SRT 휴대전화는 화면에서 하이픈 유무와 관계없이 입력할 수 있지만 저장 경계에서는 숫자로 정규화하고 provider 호출 시 공식 전화 로그인 형태로 전달합니다. 카카오·애플·SNS·간편인증·모바일신분증·비회원 예매는 이 서버 계정 연결의 지원 범위가 아닙니다. 계정 입력 validation 실패 응답은 원문 ID·비밀번호를 반사하지 않으며 `Cache-Control: no-store`를 유지합니다.

KORAIL 로그인 확인이 `철도사 로그인 확인 응답을 받지 못했습니다`로 끝나면 API의 `PUT /api/v1/provider-accounts/korail` 상태와 같은 시각 sidecar의 `POST /v1/verify-login`을 먼저 대조합니다. sidecar가 HTTP 200인데 API가 503이면 연결 문제가 아니라 sidecar의 sanitized `failed` 결과입니다. `logs/korail-browser-adapter/current.log`에는 비밀번호·ID·cookie·외부 예외 원문 대신 코드가 소유한 `stage`만 기록됩니다. `browser_launch`, `load_page`, `authenticate` 같은 stage를 기준으로 이미지·공식 DOM 계약을 점검하며, 실패 버튼을 자동 재시도하지 않습니다.

sidecar가 정상적으로 검증 절차를 마치면 완료 로그에는 `outcome=authenticated` 또는 `outcome=auth_required`만 기록합니다. 공식 로그인 화면은 서버 세션이 만들어진 뒤에도 React 인증 헤더가 늦게 바뀔 수 있습니다. 현재 구현은 로그인 화면에서 정확한 `로그아웃` 표시와 공식 `loginCheck` boolean을 독립적으로 확인하고, 서버 부하를 제한하기 위해 로그인 화면의 session check는 명시적인 확인 요청 한 번당 최대 두 번만 실행합니다. 어느 근거가 먼저 관측돼도 검색 화면으로 이동해 같은 browser context의 인증 상태를 다시 확인한 뒤에만 성공으로 판정합니다. 보안 알림이 왔는데 API가 422라면 요청은 운영사까지 도달한 상태이지만 앱 알림만으로 웹 세션 완료를 확정하지 않습니다. `stage=login_page_official_session`의 `present`와 뒤이은 `stage=official_session|search_page`를 대조하고 실패 버튼을 반복 실행하지 않습니다. URL, 회원 식별자, 비밀번호, cookie는 이 진단 로그에 남기지 않습니다.

공식 검색 화면의 `열차 조회`는 인증 상태보다 먼저 나타날 수 있으므로 버튼 표시만으로 로그인 검증을 끝내지 않습니다. 같은 browser context의 `/ebizweb/common/loginCheck` 결과를 공식 bundle과 같은 조건으로 boolean 판정하고, 인증 헤더가 늦게 수화되는 경우에는 제한 시간 동안 `a.btnGoLogout` 또는 `button.logoutBtn`의 정확한 `로그아웃` 표시를 기다립니다. 완료 로그의 `stage=official_session|login_page|search_page`, `present=true|false`만으로 어느 단계까지 진행됐는지 구분하며 원문 session 응답은 기록하지 않습니다.

`./scripts/ops.ps1 experimental`은 KORAIL·SRT 내부 adapter token을 원문 출력 없이 준비하고 `experimental-rail` 프로필 전체를 build한 뒤 migration, API·web·proxy·worker·scheduler·experimental worker와 두 sidecar를 함께 강제 재생성합니다. 코드에 새 API route와 migration이 함께 추가된 배포에서 일부 process만 구버전으로 남기지 않습니다. 설정 화면이 `Not Found`를 표시하면 먼저 API 로그에서 해당 `/api/v1/...` 요청의 실제 404를 확인하고, `alembic_version`과 실행 container의 등록 route를 대조한 뒤 이 명령으로 배포합니다.

[SRT 통합회원 전환 공식 Q&A](https://etk.srail.kr/cms/article/view.do?pageId=TK0502000000&postNo=900)는 2026년 8월 운행분은 기존 SR 회원번호/SRT 앱, 9월 운행분은 통합앱·홈페이지의 통합회원번호를 사용한다고 안내합니다. 현재 SRTrain 2.6.7 기반 검증·예약 경로는 기존 SRT 일반 로그인 계약이므로 9월 이후 운행분의 통합회원 로그인을 검증 완료로 표시하지 않습니다.

## 서버 좌석 상태 조회

`.env`에서 필요한 운영사만 활성화합니다.

```dotenv
EXPERIMENTAL_RAIL_ENABLED=true
SRT_SEAT_STATUS_ENABLED=true
SRT_SEAT_MONITORING_ENABLED=true
SRT_SEAT_STATUS_CACHE_TTL_SECONDS=20
SRT_SEAT_STATUS_TIMEOUT_SECONDS=8
SEAT_STATUS_RATE_LIMIT_COOLDOWN_SECONDS=1800
SEAT_STATUS_PROTECTION_COOLDOWN_SECONDS=300
```

세 활성화 값은 SRT 계정 없는 좌석 관측을 worker에 연결합니다. worker는 기존
`scheduled + next_check_at=null` SRT 작업을 한 번 재무장하고, due 작업을 처리하기 전에
PostgreSQL provider/account 실행 임대를 획득합니다. 호출 전과 결과 기록 전에 fencing token을
검증하므로 임대가 만료됐거나 다른 worker가 재획득한 뒤 도착한 결과는 저장하지 않습니다.
같은 출발역·도착역·KST 서비스일·인원의 후보는 `00:00–23:59` 하루 조회 하나를
singleflight와 TTL cache로 공유하고, 후보별 열차번호·출발시각·좌석 등급을 exact match합니다.
`sold_out`은 `watching`을 유지하고 다음 조회를 예약하며, `available`·`limited`·
`standing_plus_seat`는 `seat_found`, `waitlist_available`은 `official_waitlist`로 전이합니다.
`seat_found`와 `official_waitlist`도 마지막 선택 열차 출발시각 또는 시간창 종료 전까지 due
대상으로 유지됩니다. 같은 상태가 반복되면 알림을 다시 만들지 않고, 예매 가능과 예약대기
관측이 바뀌면 두 상태 사이를 이동합니다. 모든 후보가 다시 매진 등 확정적인 비행동 상태가 되면
`watching`으로 복귀하며, 오류·미관측만으로 기존 발견 상태를 강등하지 않습니다. 과거 버전에서
두 상태와 함께 `next_check_at=null`로 남은 작업은 실행 provider 활성화 시 한 번 재무장됩니다.
SRT 예약·로그인·결제는 수행하지 않습니다.

Celery의 반복 작업은 실행마다 새 event loop를 사용합니다. KORAIL·SRT source와 async Redis client는
worker 프로세스 전역 cache가 아니라 현재 task가 소유하고, 같은 task의 같은 provider dedupe 그룹만
하나의 adapter를 공유합니다. asyncio timeout이 발생해도 실제 `to_thread` 상류 호출은 추적을
계속하며, 그룹 종료 시 그 호출을 drain한 뒤 provider/account 실행 임대를 해제합니다. 모든 그룹
처리가 끝나면 adapter와 Redis client를 닫습니다. 정리 오류 로그는 provider 범주만 남기고 원문
예외·URL·credential을 기록하지 않으며, 한 provider의 close 실패가 다른 provider 정리나 임대
해제를 건너뛰게 하지 않습니다.

2026-07-30 최신 이미지 재배포 뒤 20:31과 20:35의 서로 다른 due cycle에서 SRT
`sold_out` 관측이 각각 저장됐고, worker의 `Event loop is closed`·다른 loop Future 오류는 0건이었습니다.
확인 시점의 `anonymous/public` 실행 임대는 fencing token 64, owner·만료 시각 없음으로 해제돼
있었습니다. 이는 단기 수명주기 증거이며 장시간 운영·알림 전달 검증을 대신하지 않습니다.

shared SRT source cooldown이 활성화돼 있으면 worker는 provider/account 실행 임대를 획득한 상태에서
TTL을 확인하고, 현재 fencing token이 유효할 때만 due 그룹의 `next_check_at`과 `cooldown_until`을
만료 시각으로 미룹니다. 이때 SRT 상류 요청과 오류 `SeatObservation` 행은 모두 0건입니다.
preflight 직후 observe 사이에 cooldown이 새로 열린 경우도 오류 결과를 기록하기 전에 다시 확인해
같은 연기 경로로 처리합니다. TTL이 해제된 뒤 다음 due cycle에서는 이전 `cooldown_until` 표시를
지우고 정상 관측을 재개합니다. 이를 위해 cooldown을 수동 삭제하거나 작업을 재생성하지 않습니다.

Compose는 KORAIL direct source를 전달하지 않습니다. `KORAIL_BROWSER_BRIDGE_ENABLED`는 기존 Companion 설치·snapshot API 호환을 위한 설정일 뿐 새 대기 주 UI의 KORAIL 조회 경로를 켜지 않습니다. SRT source는 필요한 운영 환경에서 명시적으로 켭니다.

KORAIL 주 UI의 서버 관리형 브라우저 경로는 다음 값을 모두 설정한 뒤에만 실행합니다. 이 배포 경로는 여전히 `experimental-rail`로 분리되어 기본 Compose에서 비활성입니다. token은 API와 sidecar 사이에서만 쓰며 로그·진단·화면에 출력하지 않습니다.

```dotenv
EXPERIMENTAL_RAIL_ENABLED=true
KORAIL_BROWSER_ADAPTER_ENABLED=true
KORAIL_SEAT_MONITORING_ENABLED=true
KORAIL_BROWSER_ENGINE=pydoll
KORAIL_BROWSER_ADAPTER_TOKEN=<32바이트 이상 무작위 값>
KORAIL_BROWSER_ADAPTER_CACHE_TTL_SECONDS=20
KORAIL_BROWSER_ADAPTER_TIMEOUT_SECONDS=90
KORAIL_BROWSER_ACTION_TIMEOUT_SECONDS=25
KORAIL_BROWSER_SESSION_REUSE_TTL_SECONDS=1800
KORAIL_BROWSER_SESSION_REUSE_MAX_SEARCHES=100
```

Windows의 표준 운영 명령 `./scripts/ops.ps1 experimental`은 KORAIL browser와 SRT provider용 설정을 적용하고, 각 내부 token이 없거나 너무 짧을 때만 암호학적 난수로 생성해 `.env`에 보존합니다. 기존의 유효한 token은 바꾸지 않으며 값을 출력하지 않습니다. 이 명령은 `rail` queue를 실제 소비하는 기본 `worker`를 포함한 profile 전체를 recreate하므로 변경한 background flag와 sidecar revision이 함께 반영됩니다. 브라우저 확장이나 관리자 화면에서 연결 코드를 직접 입력하지 않습니다. `docker compose`를 직접 사용하는 다른 환경에서는 이 값을 수동으로 준비해야 합니다.

```powershell
docker compose --profile experimental-rail config --quiet
docker compose --profile experimental-rail build
docker compose --profile experimental-rail up -d --force-recreate
```

실행 임대의 잠금·fencing 구현을 바꾼 배포에서는 재생성 뒤 실제 PostgreSQL 두 세션 경합도 확인합니다.
검사는 임시 `execution-lease-verification:<uuid>` scope만 만들고 종료 시 삭제하며 credential·token·DSN을
출력하지 않습니다. guarded transaction이 끝나기 전 takeover가 완료되거나 token이 단조 증가하지
않으면 실패합니다.

```powershell
docker compose --profile experimental-rail run --rm --no-deps `
  --volume "${PWD}/apps/api/scripts/check_execution_lease_fencing_postgres.py:/tmp/check.py:ro" `
  api python /tmp/check.py
```

sidecar는 host 포트를 열지 않고 API 내부망과 별도 egress network에만 연결합니다. read-only root filesystem에서도 이미지의 `pwuser` UID/GID 1001이 쓸 수 있는 전용 HOME tmpfs와 `/tmp`를 제공합니다. `/healthz`는 프로세스 liveness만 나타내고, Compose healthcheck가 사용하는 `/readyz`는 공식 페이지를 열지 않은 채 선택한 엔진의 Chromium launch/close probe를 통과한 뒤에만 `200`을 반환합니다. startup probe가 일시적으로 실패하면 `/readyz`는 준비 전까지 `503`으로 닫고, 5초 간격·동시 1개·30초 제한으로 재probe해 성공을 캐시하므로 컨테이너 수동 재시작 없이 회복할 수 있습니다. API가 sidecar 전체 응답을 기다리는 기본 제한은 90초, sidecar의 각 UI 대기는 25초로 분리합니다.

Pydoll은 공식 역 자산을 24시간 TTL·singleflight로 읽고 같은 레코드의 4자리 코드와 역명을
확인한 경우 편도·직통·성인 1명·일반석·KTX·KORAIL-only의 고정 25키 결과 URL을 한 번 직접
엽니다. navigation 전에 HTTP replay capture를 시작하며 역 선택 2회·날짜/시간 picker·조회 버튼
입력은 생략합니다. 역 map을 받지 못하거나 일치시키지 못한 경우에만 업무 요청 전에 기존 가시
UI 입력으로 돌아갑니다. 직접 navigation 뒤 timeout·403·429·보호 신호·불명확 DOM에는 같은
호출에서 UI 입력이나 다른 엔진으로 다시 제출하지 않습니다. 결과가 여러 묶음이면 현재 결과
화면의 `더보기`만 최대 19회 누르며, 새 identity가 없으면 즉시 멈춥니다.

`KORAIL_BROWSER_ENGINE`의 기본값은 `pydoll`입니다. direct bootstrap을 만들 수 없는 요청에서만 WebDriver 없이 CDP로 `https://www.korail.com/ticket/search/general`의 보이는 역 링크·`기차역 조회` dialog·`날짜 선택` dialog·시간 링크·적용·열차 조회 버튼을 조작합니다. cold UI 조회 전에는 가시 `txtGoStart`·`txtGoEnd`, `#startDate`, 정확한 `총 1명`을 요청 조건과 다시 비교합니다. 성공한 direct 또는 UI 검색에서만 official same-origin `/web_s/` POST의 multipart template과 Pydoll cookie를 프로세스 메모리 lease로 넘깁니다. 같은 출발·도착 구간의 후속 날짜·시각 조회는 원문 multipart의 검증된 `txtGoAbrdDt` 8바이트와 `txtGoHour` 6바이트 span만 바꾸며, 나머지 필드·경계·순서를 그대로 둡니다. 새 body를 재직렬화하거나 동적 path·NetFUNNEL 값을 계산하지 않습니다.

자동예매의 인증 actor도 로그인 확인 뒤 같은 strict 결과 URL로 이동해 최초 역·날짜·시각 입력을 줄입니다. 같은 credential generation·fingerprint의 인증 actor가 로컬 재사용 가능한 warm 상태이면 동일 Chromium context의 새 탭 격리는 유지하면서 중복 공개 검색 화면 왕복을 생략하고 strict 결과 URL로 바로 이동합니다. cold·TTL 만료·계정 변경은 기존 공개 화면과 로그인 확인을 거치며 역 identity resolver가 없으면 가시 form 입력으로 돌아갑니다. 읽기 전용 검색 actor의 DOM·cookie·HTTP replay는 인증 actor로 전달하지 않으며 credential generation, 계정 lock, 후보·가용성 에피소드별 1회 fence, exact 열차·출발시각·좌석 control 검증과 결제 전 중단을 그대로 유지합니다. direct navigation 뒤 결과가 불명확하면 기존 규칙대로 실패·수동 확인으로 닫고 UI 방식으로 반복하지 않습니다.

HTTP lease는 `KORAIL_BROWSER_SESSION_REUSE_TTL_SECONDS`와 `KORAIL_BROWSER_SESSION_REUSE_MAX_SEARCHES`를 공유해 구간별 기본 최대 1800초·100회로 제한합니다. sidecar는 최근 구간을 최대 4개까지 bounded LRU pool로 보존하며 전역 직렬화는 그대로 유지합니다. 요청 URL은 userinfo·fragment가 없고 port가 생략됐거나 443인 `https://www.korail.com/web_s/...` origin만 허용하며 redirect를 따르지 않습니다. 브라우저가 만든 opaque query는 해석·재구성·변경하지 않고 캡처 URL 그대로 해당 메모리 lease에서만 보존합니다. 응답별 timeout과 2 MiB 상한, 전체 최대 20페이지, 응답 마지막 출발시각에 1분을 더한 단조 증가 cursor를 적용합니다. 구간·성인 1명·날짜·KTX 계열·열차번호·출발시각·일반실·특실 schema가 모두 맞을 때만 결과를 반환합니다. TTL·횟수 만료와 선택 구간 오류는 해당 lease만, 용량 초과는 가장 오래 사용하지 않은 lease만 outbound POST 전에 폐기하고 cold UI로 시작합니다. 로그인 검증·예매·sidecar 종료는 pool 전체를 먼저 정리합니다. 401, 동일 origin 로그인 경로 redirect, 명시적인 로그인 HTML로 session 만료가 확인된 경우에는 선택 lease를 닫고 같은 read-only 요청에서 cold UI 초기화를 최대 한 번 수행합니다. cookie 누락, capture·response schema·cursor 불일치와 그 밖의 4xx는 같은 요청에서 cold retry하지 않고 `source_unavailable`로 닫습니다. 403·429·`-1405`·`-8002`·`-8003`·`macro_err1`·CAPTCHA·NetFUNNEL 등 보호 신호에서도 cold retry 없이 선택 lease를 닫고 기존 cooldown으로 전환합니다. 수동 Chrome raw capture/import, 다른 엔진 fallback, 같은 보호 요청 재제출은 없습니다.

multipart template, cookie jar, opaque `/web_s/` path·query와 header 값은 각 lease 객체 밖으로 내보내지 않습니다. repr·DB·Redis·파일·artifact·stdout·서비스 로그·metric label에 저장하지 않고 별도 secret rotation이나 갱신 절차도 두지 않으며, lease 만료·LRU 축출·인증 전환·sidecar 종료에서 HTTP client와 함께 제거합니다. 내부 오류는 `session_invalid`, `invalid_capture`, `invalid_response`, `provider_access_restricted`, `rate_limited`, `source_unavailable`처럼 정규화한 낮은 cardinality reason과 stage만 남깁니다.

정상 lifecycle도 원문 없이 `lease_created`, `search_succeeded`, `lease_retired`, `cold_reinit` 이벤트와 검색 순번·TTL·횟수 제한만 `korail-browser-adapter/current.log`에 기록합니다. `APP_LOG_LEVEL=INFO`이면 `rail_waitlist` namespace의 INFO 이벤트가 파일 handler까지 전달되며, root logger의 기본 level은 바꾸지 않아 `httpx`·Pydoll 등 외부 라이브러리의 상세 요청 로그를 활성화하지 않습니다.

시간 선택에서 `departure_hour_navigate` 또는 `departure_hour_disabled`가 발생했지만 같은 컨테이너의 다른 시각 조회가 성공했다면 API 변환이나 재시작부터 의심하지 않습니다. 날짜를 바꾼 직후에는 공식 picker가 시간 enabled/disabled 상태를 이전 서비스일 기준으로 잠시 유지할 수 있습니다. 구현은 날짜가 다르면 날짜만 먼저 적용하고 `#startDate`의 날짜를 exact readback한 뒤 picker를 다시 열어 새 날짜 기준 시간 상태를 읽습니다. 공식 화면의 달력 월 slider와 시간 slider는 같은 Slick class를 쓰지만, sidecar는 `.slideWrap` 안의 시간 전용 control만 인정하며 dialog 전체의 월 화살표를 시간 이동에 사용하지 않습니다. 현재 가시 시간 링크가 자체·컨테이너·slide disabled 속성/class 없이 `aria-disabled=true`만 가진 경우에는 유일한 요청 시각 링크를 실제 pointer click합니다. 현재 가시 범위 밖이면 숨은 catalog 링크를 JavaScript로 직접 누르지 않습니다. 시간 전용 화살표가 진행되지 않으면 유일한 가시 `.slideWrap .slick-list`에 실제 CDP drag를 수행하고, 그것도 진행되지 않지만 viewport가 포커스 가능한 경우에만 표준 좌우 키를 한 번 보냅니다. 어느 경로도 시간 창의 방향성 있는 변화와 CSS 전환 완료가 확인되지 않으면 중단합니다. 적용 뒤 `#startDate`의 날짜와 시각을 exact match하며 클릭 무시·무진전·다른 시각 적용은 `departure_schedule_readback` 또는 `departure_hour_navigate`로 닫습니다. 공식 UI가 현재 창에서 이미 선택한 시각 하나를 soft-disabled로 표시할 때는 요청 날짜의 활성 day 클릭, picker 전 표시 시각 일치, 나머지 현재 창 시각 활성 조건까지 만족해야 클릭 없이 적용합니다. 내부 로그에는 요청값과 화면 문구 대신 후보 수와 live `disabled`·`aria-disabled`·CSS class, 보호 surface 수와 정규화 trigger만 제한적으로 남깁니다. 이 계약은 외부 호출 없는 날짜 변경 뒤 stale-disabled 재개방, 비활성 clone·현재/인접 시간 창·soft-aria pointer click·가로 드래그·키보드 이동·무진전·선택 시각·적용 후 exact readback Chromium fixture로 검증합니다.

예약 로그가 `seat_control_not_unique`이면 exact 열차 행은 찾았지만 같은 좌석 등급 문구를 가진 공식 anchor가 둘 이상 보였다는 뜻입니다. 현재 구현은 exact 행 안에서 요청 좌석 등급, 가격 표시, 비매진, visible·enabled 조건을 모두 만족한 실제 행동 control만 판정합니다. 실제 행동 control이 0개면 `seat_not_available`, 2개 이상이면 계속 `seat_control_not_unique`로 fail-closed하며 임의의 첫 번째 버튼을 누르지 않습니다.

`seat_not_available`이 반복되는데 같은 시각 코레일톡에서 좌석이 보인다면 먼저 sidecar revision을 확인합니다. 2026-08-04 이전 판정은 anchor 자체 문구가 좌석 등급으로 시작해야 했고 문자열에 `매진`이 포함되면 모두 제외해, 부모 `.price_box`에만 `일반실`이 있거나 `매진임박`인 좌석도 잘못 0개로 만들 수 있었습니다. 현재 판정은 exact 열차 행 안에서 부모 `.price_box`의 좌석 등급·가격·`sold_out_soon` class와 내부 가격 anchor를 함께 확인합니다. `매진임박`은 허용하되 매진·예약대기·다른 좌석 등급·가격 없는 링크·disabled control은 계속 거부합니다. 진단 로그에는 원문 DOM이나 가격을 남기지 않고 `seat_clicked`·`reservation_clicked`와 정규화 reason만 사용합니다.

기존 엔진이 필요한 진단 환경에서만 `KORAIL_BROWSER_ENGINE=playwright_direct_cdp`를 명시합니다. 이 경로는 `/tmp`의 새 임시 프로필로 Chromium을 Playwright 밖에서 직접 실행하고 sidecar 내부 `127.0.0.1` CDP에 연결한 뒤, 가시 열차 조회 버튼에 raw CDP `mousePressed → 100ms → mouseReleased`를 한 번 보냅니다. 브라우저 실행 중 자동 엔진 전환은 없으며 실패 뒤 다른 엔진으로 fallback하거나 같은 검색을 다시 제출하지 않습니다.

최종 버튼을 누른 뒤 요청 취소가 반복되어도 `mouseReleased`와 CDP session detach를 별도 정리
task로 끝까지 수행합니다. Chromium 정리 중 반복 취소가 들어와도 프로세스와 임시 프로필을 모두
회수한 뒤 최초 취소를 호출자에게 전달합니다. sidecar 종료 시에는 먼저 `/readyz`를 닫고 진행 중인
singleflight 검색을 최대 70초 기다린 뒤 취소하며, 취소 정리도 최대 10초까지만 기다리고 정규화된
오류를 남긴 뒤 lifespan을 종료합니다.

값을 바꾼 뒤에는 환경변수를 다시 읽도록 API 컨테이너를 recreate합니다.

```powershell
./scripts/ops.ps1 config
./scripts/ops.ps1 build
./scripts/ops.ps1 up
./scripts/ops.ps1 status
```

서버는 운영사별 동시 요청을 1개로 제한하고 동일한 운영사·구간·날짜·시간창·인원 조건은 singleflight로 한 번만 실행합니다. `GET /api/v1/timetables`는 KORAIL Chromium·SRT live source를 주 시간표 경로로 사용합니다. 주 경로가 정상 응답하면 TAGO를 호출하지 않고 열차 identity·시각·운임·좌석 상태를 함께 반환합니다. 주 경로가 timeout·상류 장애·미활성 등으로 사용할 수 없을 때만 TAGO 시간표 adapter를 fallback으로 한 번 호출합니다. 정상 결과는 운영사별 TTL 동안 API 프로세스 메모리에 보관합니다. API replica마다 cache와 singleflight가 따로 생기므로 현재 운영 조건은 API replica 1개이며, 2개 이상으로 늘리려면 공유 cache나 단일 fetch coordinator를 먼저 도입합니다.

공식 live 주 경로는 `origin_node_id·destination_node_id`가 없어도 역명·구간으로 조회합니다. 이 경우 반환된 좌석 상태를 예매 가능이나 매진으로 추정하지는 않지만, source가 제공한 유효한 `official_provider` 관측값 자체는 화면에 표시합니다. node ID가 없으면 DB의 node-bound confirmation overlay를 적용하지 않고 `registration_evidence_id`도 발급하지 않으므로 대기 생성은 허용되지 않습니다. 정상 역 카탈로그 선택 흐름처럼 node ID가 모두 제공되고 검증된 경우에만 기존 evidence 발급 계약으로 이어집니다.

SRT live 결과는 열차번호·종류·구간·서비스 날짜·출도착시각이 명확한 항목만 `official_provider` 시간표로 정규화합니다. KORAIL 서버 Chromium 결과도 공식 화면의 구간·날짜·성인 1명·열차번호·종류·출도착시각·좌석 등급이 모두 일치할 때만 `official_provider`로 정규화합니다. 레거시 Browser Companion snapshot은 서버 수신 뒤 2분이 지나지 않고 같은 exact match를 통과할 때만 `official_page_browser_companion`으로 읽습니다. exact match가 없거나 응답이 불완전하면 매진으로 추정하지 않고 원인에 맞는 `unknown`을 반환합니다. TAGO fallback은 운행 시간표만 제공하므로 `timetable_source=TAGO`를 유지하고 좌석 provenance를 `not_observed`로 닫습니다.

sidecar 결과는 UI DOM과 HTTP replay를 구분하지 않고 구간·날짜·인원과 각 열차의 정규화 번호·KST 출발시각을 API가 다시 exact-match합니다. 업무 document·replay의 403, 429, `-1405`·`-8002`·`-8003`·`macro_err1`·CAPTCHA·NetFUNNEL 보호 marker는 즉시 중단하고 cooldown 동안 browser나 HTTP lease를 다시 열지 않습니다. font·analytics 같은 비업무 subresource 403만으로는 보호 응답으로 판정하지 않으며, KTX·KTX-산천·KTX-청룡 행만 엄격히 파싱합니다. 무궁화·ITX 등 비-KTX 행은 건너뛰지만 선택한 KTX 행의 날짜·인원·identity·좌석 schema가 맞지 않으면 상태를 추정하지 않고 `source_unavailable`로 닫습니다. 브라우저 실행 또는 CDP 연결이 실패해도 다른 backend로 자동 전환하지 않습니다. 실제 단발 Pydoll 좌석 snapshot과 03:00·05:00 조건의 운영 웹 overlay·상태별 CTA는 확인했습니다. HTTP replay의 장시간 운영 안정성과 실제 만료·보호 전이는 별도 운영 확인 항목입니다.

SRT 역 목록과 시간표 후보는 SRTrain 2.6.7의 현재 역 roster를 capability allowlist로 함께 사용합니다. 따라서 대전→서울처럼 TAGO 공용 시간표가 `SRT` 행을 반환해도 현재 서버 source가 목적지를 조회하지 못하는 조합은 SRT 열차 카드와 좌석 미지원 카드로 노출하지 않고 빈 결과로 닫습니다. 이는 실제 SRT 운행 여부의 공식 판정이 아니며, roster를 불러오지 못하면 전체 TAGO 역 목록으로 fallback하지 않고 SRT 요청을 실패시킵니다.

429가 확인되면 `SEAT_STATUS_RATE_LIMIT_COOLDOWN_SECONDS`, `CODE -8002`, `CODE -8003`, `macro_err1` 또는 동등한 보호 응답이 확인되면 `SEAT_STATUS_PROTECTION_COOLDOWN_SECONDS` 동안 같은 운영사의 새 요청을 보내지 않습니다. KORAIL의 일반 timeout·상류·DOM 장애는 exact query별 프로세스 메모리에서 30초부터 최대 300초까지 지수 backoff하므로 오늘의 지난 시간창 실패가 다음 서비스일 조회를 막지 않습니다. 명시적인 접근 제한·rate-limit cooldown 중에는 신선한 cache가 있을 때만 그 결과를 재사용하고, 없으면 원인에 맞는 `unknown`으로 닫습니다. provider-wide cooldown은 Redis TTL로 API 재시작과 replica 사이에 공유하며 Redis 장애 때는 프로세스 메모리 fallback으로 계속 차단합니다. cache·singleflight는 프로세스 로컬입니다.

이 사용자 요청 조회는 시간표 응답 한 번을 보강할 뿐 그 자체로 scheduler·worker 감시, 알림, 예약 시도 또는 결제를 시작하지 않습니다. 유효한 evidence와 실행 capability가 있을 때는 `available`·`limited`·`standing_plus_seat`를 포함한 선택 좌석을 감시 작업으로 등록할 수 있지만, `startWatch`는 별도 실행 registry를 사용하며 KORAIL·SRT 각각의 3중 opt-in이 모두 켜진 경우에만 해당 background 관측이 시작됩니다. `reservation_once`는 추가 운영사 플래그가 켜진 실행 provider에서만 노출되고, 활성 철도 계정과 작업별 `reserve_once_before_payment`가 있어야 DB 고유 fence 아래 실제 호출됩니다. 결제와 결제정보 입력·저장은 호출하지 않습니다. 원문 응답, 세션, 대기열 키, 운영사 요청 식별자는 로그·지표·DB에 기록하지 않습니다.

최초 시간표 조회는 `GET /api/v1/timetables`에서 자동 보강합니다. 일부 운영사의 좌석만 미관측이면 웹은 해당 운영사에 한해 관리자 인증이 필요한 `POST /api/v1/seat-status/refresh`를 호출합니다. 이 endpoint도 같은 exact-match·singleflight·cache·Redis cooldown을 사용하고 `Cache-Control: no-store`로 응답합니다. 새 호출을 강제로 만드는 우회 endpoint가 아니므로 cooldown 중에는 상류를 호출하지 않습니다.

인증된 `GET /api/v1/seat-status/status`는 KORAIL browser와 SRT live의 현재 Redis cooldown만
조회합니다. 응답은 `ready|cooldown`, 허용된 원인 `provider_access_restricted|source_unavailable`,
남은 초만 담고 `Cache-Control: no-store`를 사용합니다. 이는 시간표 좌석 보강 source의
일시 상태이며 PostgreSQL에 영속되는 worker `ProviderCircuit`과 별개이므로 두 섹션을 합쳐
해석하거나 이 API로 worker를 재개하지 않습니다.

2026-07-30 실제 단발 smoke에서 당시 KORAIL 계정 없는 source는 보호 응답을 반환해 `unknown/provider_access_restricted`로 닫혔습니다. 당시 릴리스는 Redis에 6시간 provider cooldown을 기록했고 API 컨테이너를 재시작한 뒤에도 TTL이 유지됨을 확인했습니다. 2026-07-31부터 현재 기본 보호 cooldown은 5분이며, 과거 릴리스가 만든 `korail-browser` Redis 키는 배포 시 한 번 제거했습니다. 같은 날 서버 Chromium sidecar는 서울→부산, 2026-08-01, 09:00–12:00 조건에서 공식 메인 UI의 역 선택·비동기 월 전환 달력·날짜·시간 회전 선택·적용·실제 마우스 조회까지 진행했으나 보호 신호를 내부 HTTP 423 `provider_access_restricted`로 정규화하고 중단했습니다. 당시 기록은 공식 403과 화면 marker를 구분하지 못합니다. 현재 sidecar는 원문 body·URL·header 대신 허용 목록의 trigger와 실행 stage만 내부 경고 로그에 남기며 외부 API에는 일반화 reason만 반환합니다. 추가 요청 없이 cooldown으로 닫았으며 예매 가능·매진 또는 좌석 snapshot 도달을 완료로 주장하지 않습니다. 같은 날 SRT 수서→부산 12:00–18:00 요청은 14개 열차, 일반실·특실 28개 좌석 등급을 서버에서 관측해 매진 상태와 취소표 대기 CTA까지 자동 반영했고 브라우저 warning/error는 0건이었습니다.

Browser Companion은 기존 설치를 위한 레거시 호환 경로입니다. 필요할 때만 `KORAIL_BROWSER_BRIDGE_ENABLED=true`로 기존 pairing·credential·snapshot API를 유지합니다. `apps/korail-browser-companion`의 확장 build와 2분 `official_page_browser_companion` exact-match 계약은 남아 있지만 새 대기 주 UI는 확장 설치, 연결 코드, 공식 결과 가져오기 버튼을 제공하지 않습니다. 레거시 snapshot의 전송·refresh 실패를 성공으로 표시하지 않으며 freshness·구간·날짜·인원·열차 identity가 맞지 않으면 미관측 상태를 유지합니다.

2026-07-30 새 headed Playwright 세션에서 KORAIL 공식 홈의 사용자 가시 `열차조회`를 정상 클릭한 단발 확인도 결과 단계에서 `CODE -8003`으로 즉시 중단됐습니다. 브라우저 저장 상태·User-Agent·header를 바꾸거나 stealth·proxy를 사용하지 않았고 추가 재시도도 하지 않았습니다. 진단 문서에는 원본 요청 식별자·경로·응답 원문을 기록하지 않습니다.

2026-07-31 대전→서울 단발 재검증에서는 첫 이미지가 예전 `/ticket/main` 계약을 사용해 `station_trigger`, 다음 이미지가 새 URL을 쓰면서도 예전 표시 입력을 읽어 `pre_submit_identity_check`에서 각각 실패했습니다. `/ticket/search/general`과 가시 `txtGoStart`·`txtGoEnd` 입력, `총 1명` 링크 계약으로 수정한 기존 `playwright_direct_cdp` 엔진은 09:00–12:00 pre-submit exact read-back까지 통과했지만, 동일하게 결과 단계의 `marker_code_8003`을 감지해 HTTP 423으로 닫혔습니다.

이후 Windows PoC와 Linux sidecar를 같은 대전→서울, 2026-08-01, 03:00–08:00 조건으로 한 번씩 비교했습니다. Windows PoC의 Pydoll은 전체 10행(KTX 계열 8행, ITX 1행, 무궁화 1행)을 읽었습니다. 기본 `pydoll` 엔진으로 재빌드한 Linux sidecar는 HTTP 200과 KTX 계열 8행을 반환했고 `available`, `limited`, `sold_out` 상태를 포함했습니다. 같은 조건의 `playwright_direct_cdp`는 `wait_result/marker_code_8003`으로 HTTP 423을 반환했습니다. 따라서 Pydoll 실행 엔진과 KTX 전용 변환 계층의 단발 성공은 확인됐습니다. 당시 인증된 새 대기 웹 화면에서는 KTX 계열 8행이 exact overlay되고 2행은 미관측으로 유지됐습니다. `더보기` 확장 적용 후 같은 조건을 다시 실행한 최신 검증에서는 시간표 10행의 일반실·특실 20개 상태가 모두 공식 관측으로 표시됐고 상태별 공식 예매·취소표 대기 CTA가 노출됐습니다.

이어진 05:00–09:00 단발 검증에서는 달력 월 화살표와 시간 slider를 분리하고 soft-aria 시간
링크를 실제 pointer click하도록 수정한 이미지를 사용했습니다. 대전→서울, 2026-08-01의 KTX
계열 16행과 일반실·특실 32개가 모두 공식 관측으로 표시됐으며 `예매 가능`, `매진 임박`,
`입석+좌석`, `매진` CTA가 화면에 반영됐습니다. sidecar 요청은 HTTP 200이었고 일부 미확인
경고와 수동 재조회 버튼은 사라졌습니다. 이 결과 역시 장시간 반복 조회의 운영 승인을 뜻하지
않으며 background 장시간 안정성은 아직 운영 완료로 기록하지 않습니다.

이어진 12:00–18:00 진단에서 초기 58개 좌석 미확인은 API 변환이 아니라 sidecar의
`departure_hour_navigate`였습니다. `00:00` PoC는 시간 carousel을 이동하지 않으므로 동일 분기를
검증하지 않습니다. 안정판 Google Chrome 이미지에서 숨은 링크 직접 클릭을 제거하고 가시
`.slick-list`의 실제 CDP drag, CSS 전환 완료, 활성 12시 click과 `#startDate` exact readback을
적용했습니다. 동일 대전→서울, 2026-08-01 단발 sidecar 요청은 HTTP 200, KTX 계열 28행·좌석
등급 56개를 반환했습니다. API와 sidecar 재생성 뒤 최종 재검증한 실시간 상태는 `limited` 7개,
`sold_out` 30개, `standing_plus_seat` 19개로 정규화됐습니다.
API 프로세스 cache와 Redis cooldown에는 활성 항목이 없었으므로 이 시점의 미확인 원인은 변환이나
남은 cooldown이 아닙니다. 이번 작업에서는 로그인된 웹 overlay를 다시 검증하지 않았습니다.

## 웹 실제 사용 시나리오 검증

API·웹 기능 변경 뒤 저장소 루트에서 `./scripts/ops.ps1 verify`를 실행합니다. 이 명령은 먼저
`docker compose config --quiet`를 실행하고, 외부 네트워크가 없는 browser sidecar 테스트
컨테이너에서 역·날짜·시간·조회·결과 DOM fixture 흐름을 실제 Chromium으로 검증합니다. 이어서
API 전체 pytest·Ruff, 웹 typecheck·Vitest·Playwright E2E·production build를 수행합니다. API
검증에는 Python 3.12와 `uv`, sidecar 검증에는 Docker가 필요합니다.

```powershell
cd apps/web
npm ci
npx playwright install chromium
cd ../..
./scripts/ops.ps1 verify
```

브라우저 adapter만 다시 확인할 때는 `./scripts/ops.ps1 verify-browser`를 사용합니다. 이 검사는
`korail-browser-adapter-test` 이미지를 만들고 로컬 fixture만 열며 KORAIL 공식 사이트에는
접속하지 않습니다. GitHub Actions의 `make verify`도 같은 테스트를 필수 단계로 실행합니다.

웹의 SSE 연결은 API outbox의 과거 이벤트를 다시 받을 수 있습니다. 화면은 연결 시각보다 오래된
이벤트를 초기 REST snapshot과 중복된 replay로 간주해 무시하고, 현재 이벤트가 짧은 시간에 여러
건 도착해도 `/watches`와 `/notifications/channels` 재조회는 한 번으로 병합합니다. 새 대기 3단계가
`시간표 조회 중`에 머물 때 API access log에 `/api/v1/timetables`가 없고 위 두 GET만 연속되면,
브라우저가 최신 web image를 사용하는지 먼저 확인합니다. 정상 계약은 홈 최초 각 1회, 현재 SSE
burst당 각 1회이며 과거 outbox 행 수만큼 반복하지 않습니다.

기본 E2E는 외부 철도사 대신 로컬 고정 API만 사용하며 데스크톱·모바일 Chromium에서 다음
사용자 흐름을 검증합니다.

1. KORAIL·SRT, 출발역·도착역, 달력 날짜를 선택합니다.
2. 조건 단계를 지나 시간표와 좌석 상태를 조회하고 시간 범위를 바꿔 다시 조회합니다.
3. `예매 가능`은 공식 예매, `매진`은 취소표 대기, `예약대기 가능`은 예약대기,
   `provider_access_restricted`는 두 좌석 등급 모두 미관측이며 예매·대기 CTA와 재조회가
   표시되지 않음을 확인합니다.
4. 같은 SRT 열차의 일반실과 특실을 독립적으로 즉시 등록합니다.
5. 홈에서 두 작업이 잘림 없이 모두 나타나고 등록 당시 좌석 근거가 보존되는지 확인합니다.

GitHub Actions `repository-verify`는 API·웹·Compose 변경에서 외부 철도사 호출 없이 같은
결정적 검증을 실행합니다. 실제 SRT 스모크는 `RUN_LIVE_PROVIDER_SMOKE=1`,
`E2E_BASE_URL`과 인증 수단을 운영자가 명시한 경우에만 `npm run test:e2e:live:srt`로
실행합니다. 인증 수단은 저장소 밖 절대경로의 `E2E_STORAGE_STATE` 또는 실행 셸에만 설정한
`E2E_ADMIN_USERNAME`·`E2E_ADMIN_PASSWORD` 쌍입니다. SRT만 선택해 내일 수서→부산
12:00–14:00 한
여정을 한 번 조회하고, `official_provider` 좌석 상태와 상태별 공식 예매·취소표 대기·예약대기
CTA를 확인합니다. 세션 파일은 secret으로 취급해 커밋·로그·artifact에 포함하지 않습니다.
좌석을 관측하지 못하거나 보호 상태가 표시되면 재시도하지 않습니다. 두 live smoke 모두
trace·video·screenshot을 끄고 provider·구간·시간창·요청 횟수·정규화 원인만 텍스트
artifact에 남깁니다. KORAIL 양성 스모크는 같은 gate를 적용한
`RUN_LIVE_KORAIL_PROVIDER_SMOKE=1`을 추가로 명시하고 `npm run test:e2e:live:korail`로
별도 실행합니다. 먼저 `/api/v1/seat-status/status`를 조회해 cooldown이면 시간표를 0회로 유지한
채 허용된 원인과 남은 시간만 sanitized artifact에 남기고 양성 snapshot 미확인으로 실패합니다. `ready`일 때만
저장소 밖 storage-state 파일의 존재·읽기·JSON 구조와 로그인 세션을 확인합니다. 이어 KORAIL만
선택한 서울(`NAT010000`)→부산(`NAT014445`)·내일 KST·12:00–18:00·성인 1명 query를 정확히
한 번 보내고 SRT 시간표 호출은 0건이어야 합니다. `/api/v1/providers`는 KORAIL의
`enabled`·`timetable`·`official_booking_link`·`seat_monitoring=true`,
`official_waitlist_link`·`reservation_once=false`를 정확히 반환해야 합니다. 같은 열차의 일반실·특실은
`korail-official-page-browser` source, 신선하고 timezone이 있는 `observed_at`, 상태와 capability에
맞는 공식 KORAIL HTTPS CTA를 가져야 하며 둘 중 하나 이상은 행동 가능한 관측 상태여야 합니다.
이 read-only 검사는 매진·예약대기 좌석의 `add_to_watch`와 신선한 registration evidence만 확인하며,
예매 가능 좌석의 감시 등록이나 예약 시도는 만들지 않습니다. 제품 경로에서는 유효한 evidence와
실행 capability가 있으면 예매 가능·매진 임박·입석+좌석도 감시 등록할 수 있습니다. 공통 flag만으로
KORAIL spec을 실행하지 않아 두 운영사를 한 명령에서 연속 호출하지
않습니다.

두 live spec은 공통 인증 helper를 사용해 provider preflight보다 먼저 관리자 인증을 완료합니다.
storage state가 유효하면 우선 사용하고, 함께 제공한 ID·비밀번호는 session이 만료돼 로그인 화면이
보일 때만 정확히 한 번 사용합니다. storage state를 명시하지 않은 경우에는 자격증명 쌍만으로
로그인할 수 있습니다. 한쪽 credential만 설정하거나 명시한 storage-state 파일이 상대경로·저장소
내부·누락·손상 상태이면 공식 시간표 요청 전에 고정된 구성 오류로 중단합니다. 초기 관리자 등록
화면에서는 계정을 자동 생성하지 않습니다. password는 trim하지 않으며 username·password·파일
경로·cookie는 artifact와 오류 문구에 넣지 않습니다. trace·video·screenshot은 계속 끄고
`DEBUG=pw:api`도 사용하지 않습니다. 이 값은 Playwright 실행 프로세스 전용이므로 Compose
`.env`에 보존하지 말고 `finally`에서 `Remove-Item Env:E2E_ADMIN_USERNAME,Env:E2E_ADMIN_PASSWORD`
로 지웁니다.

migration `0010`과 `0014` 적용 뒤 `/api/v1/timetables`는 유효한 관측 provenance를 유지하되, 검증된 출발·도착 node ID가 모두 있고 실행 provider의 `seat_monitoring=true`와 `add_to_watch`를 함께 가진 공식 좌석 등급에만 짧게 유효한 `registration_evidence_id`를 발급합니다. node ID가 없거나 실행 capability가 `false`이면 API가 `add_to_watch`와 ID를 제거하므로 관측된 상태와 공식 인계 CTA는 남아도 작업은 생성할 수 없습니다. `unknown`·`not_observed`·mock·비허용 행동은 ID 없이 반환하며, `0014` 이전에 저장된 evidence는 생성에 사용할 수 없습니다. 작업 생성은 이 ID와 provider·출도착 node ID·정규화 열차번호·UTC 출발시각·승객 수·좌석 등급·발급 당시 등록 허용 여부가 모두 일치해야 합니다. 만료는 HTTP `409`와 machine-readable `registration_evidence_conflict/expired`로, 누락·identity·허용 여부 불일치는 `422`로 닫힙니다. 생성 단계의 만료에 한해서만 웹이 같은 운영사를 `POST /seat-status/refresh`로 한 번 갱신하고, 동일 열차·출발시각·좌석등급·상태의 새 공식 관측과 새 ID를 확인한 뒤 생성·시작을 한 번 재시도합니다. 공식 페이지 기반 관측은 고정 source와 아직 지나지 않은 `fresh_until`도 확인합니다. 보호 응답·미관측·상태 또는 identity 변화·갱신 실패에는 재시도하지 않으며, `startWatch` 실패에는 만료 복구를 적용하지 않습니다. 생성 요청은 evidence ID에, 시작 요청은 watch ID에 결박된 안정적인 멱등 키를 재사용하므로 시작 응답이 유실된 뒤 같은 좌석을 다시 등록해도 서버는 기존 작업을 반환하고 새 작업을 만들지 않습니다. 새 대기 화면을 다시 열면 웹은 활성 DB watch 후보의 provider·열차번호·UTC instant 출발시각·좌석 등급을 현재 열차와 대조해 버튼을 복원하며, 취소에는 그 watch의 실제 ID만 사용합니다. 같은 발급 구간의 동일 내용은 재사용해 불필요한 행 증가를 줄입니다. `GET`이 발급 snapshot을 기록하므로 DB migration과 쓰기 가능 상태가 필요하지만 watch·outbox·관측·예약 행은 만들지 않습니다. 홈의 상태 문구는 이 등록 snapshot을 읽고 작업 `updated_at`을 좌석 확인 시각으로 대체하지 않습니다.

활성 후보는 등록 당시 identity인 `scheduled_departure_at`, 지연 관측으로 계산한 `estimated_departure_at`, 실제 출발 관측 시각인 `actual_departure_at`, `delay_minutes`를 분리해 보존합니다. KORAIL DOM에서는 정확한 `N분 지연 예상`, HTTP replay에서는 `h_expn_dpt_dlay_tnum`만 지연 분으로 받아 scheduled identity를 유지한 채 estimated departure를 갱신합니다. `sold_out`은 좌석 재고가 없다는 뜻일 뿐 예매창 종료 근거가 아닙니다. 신선한 source·관측시각·`fresh_until`을 갖춘 `departed_origin`·`cancelled` 또는 닫힌 예매창만 예정시각과 무관한 즉시 만료 근거입니다. sweep은 `travel_date`로 작업을 먼저 거르지 않고 각 후보의 운영 상태를 판정하므로, KST 22:30에 등록한 다음 날 00:30 열차도 fresh `closed` 근거가 있으면 즉시 만료됩니다. 반대로 신선한 `delayed`·`boarding`·열린 예매창은 예정시각이 지났더라도 감시를 유지합니다. 운행 상태가 `unknown`이거나 terminal provenance가 stale이면 예정 출발 뒤 최대 15분만 제한 재확인하고, 신선한 계속 운행 근거가 없으면 절대 horizon으로 만료합니다. `seat_found`·`official_waitlist`도 이 규칙을 따르며 KST 자정이나 도착시각으로 만료를 앞당기거나 늦추지 않습니다.

## 장애 대응

- `429`: `Retry-After`를 우선하고 없으면 장시간 cooldown으로 전환합니다.
- 업무 document의 `403`, CAPTCHA, 보호 marker, 비정상 접근: provider 작업을 중단하고 수동 재개합니다. 비업무 subresource 403만 확인된 경우에는 가시 업무 DOM의 별도 보호 신호를 함께 확인합니다.
- 인증 실패: 반복 로그인하지 않고 `auth_required`로 전환합니다.
- worker 재시작: PostgreSQL outbox와 Celery broker에서 미처리 작업을 다시 처리합니다.
- 알림 실패: 채널별로 독립 재시도하며 최대 횟수 이후 실패 상태로 보존합니다.

`provider_circuits`는 provider별 `closed`, `open`, `half_open`, `manual_hold`를 영속화합니다. worker는 요청 전에 이 상태를 확인하며, `open` 작업은 `cooldown`, 수동 확인이 필요한 상태는 `auth_required`로 중단합니다. 현재 관리자용 circuit 조회·수동 재개 명령과 자동 cooldown 복구 sweep은 구현되지 않았습니다. 따라서 운영 DB에서 circuit을 임의로 수정해 재개하지 말고, 허가된 external adapter를 도입하기 전에 감사 이력이 남는 관리 기능을 먼저 추가해야 합니다.

좌석 조회 제공원의 Redis cooldown은 위 `provider_circuits`와 다른 제어면입니다. KORAIL에서는 명시적인 접근 제한·rate-limit cooldown만
시간표 좌석 보강 요청을 상류 호출 전에 provider-wide로 막으며, 만료 전에는 화면의 수동 재조회 동작도
제공하지 않습니다. 상태 확인은 `GET /api/v1/seat-status/status`와 설정의 별도 섹션을 사용합니다.

Chromium sidecar의 메모리 backoff도 일반 source·DOM 실패는 exact query별로 격리합니다. key는 출발·도착·
서비스일·시간창·승객 수이며 30초부터 최대 300초까지 증가합니다. 같은 key의 재호출은 정규화한
`query_backoff` stage로 닫지만 다른 날짜·구간은 즉시 실행할 수 있습니다. 403·423·429로 확인된
접근 제한과 rate-limit만 sidecar 전역 cooldown을 엽니다. 따라서 worker의
`departure_date_disabled` 한 건 뒤 사용자 API 요청까지 연속 503이 되는 경우에는 최신 이미지 적용 여부를
먼저 확인합니다.

당일 새벽 조회는 sidecar 로그의 UTC 시각을 KST로 변환해 함께 판정합니다. 예를 들어 `17:47Z`는 다음 날
`02:47 KST`입니다. 이때 요청이 05:00–09:00이면 API는 05시를 browser 검색 시작으로 보내야 하며, 미래
서비스일에만 00시 시작을 사용합니다. 로그에 가시 시간창 `before=(2,3,4,5,6)` 뒤 00시 방향 이동과
`departure_hour_navigate`가 보이면 당일 KST 시작 보정이 적용되지 않은 구버전입니다. 자정 직후 현재 날짜가
공식 입력에 이미 선택돼 picker 링크에서 빠진 경우에는 날짜를 다시 찾지 않고 현재 날짜 exact readback을
유지해야 합니다. 선택 시간창이 모두 지난 경우 browser 호출 없이
`unknown/not_observed(departure_window_elapsed)`로 닫습니다. 이 사유만 남은 3단계 요약은
`선택한 출발 시간대가 지났습니다`로 표시하고 서버 재조회 버튼을 숨기므로, 날짜나 시간 범위를
바꿔 다시 조회합니다. 다른 미관측 사유가 함께 있으면 지난 시간창은 재조회 대상에서 제외하고 실제로
응답이 미확인인 운영사만 제한적으로 재조회합니다.

Compose의 API·worker·두 provider sidecar는 저장소 소스를 bind mount하지 않고 이미지에 복사해 실행합니다.
따라서 좌석 조회 코드나 날짜 전환 정책을 수정한 뒤에는 `./scripts/ops.ps1 experimental` 또는 위의
profile 전체 build·force-recreate 명령으로 이미지를 재빌드·재생성해야 합니다. 로그의 최신
서비스 시작 시각이 수정 파일 시각보다 이르면 웹 화면이 새 동작을 보이지 않는 것이 정상이며, 이 상태를
코드 회귀로 오판하거나 Redis cooldown을 수동 삭제하지 않습니다.

`GET /timetables` 장애는 운영사별로 주 경로와 fallback을 분리해 확인합니다. KORAIL은 Chromium
sidecar의 공식 timetable search, SRT는 provider sidecar의 live timetable search가 주 경로입니다.
주 경로가 성공하면 TAGO 상태와 무관하게 결과가 반환되어야 합니다. 주 경로가 사용할 수 없어 TAGO로
fallback한 경우에만 TAGO 상태가 응답에 영향을 줍니다. TAGO가 HTTP 200 안에
`header.resultCode=01`과 서비스키 필수 메시지만 반환하면 정상 envelope가 아니며, 같은 운영사의 주
경로도 실패한 경우에만 해당 운영사를 fail-closed 503으로 반환합니다. 컨테이너에 키가 설정됐다는 사실과
상류가 키를 승인했다는 사실은 다릅니다. 키 원문·요청 URL·응답 본문은 로그에 남기지 않고, 설정 유무·
안전한 결과 코드·운영키 활성 상태만 공공데이터포털에서 확인합니다. 역 카탈로그는 별도의 PostgreSQL
스냅샷과 stale-while-refresh 계약을 사용하므로, 시간표 live 우선 전환과 관계없이 마지막 정상 TAGO 원본
식별자·KORAIL 역 안내 교집합을 계속 사용합니다.

mock은 짧은 due claim과 후보별 DB unique constraint로 중복 예약을 막습니다. SRT 외부 관측은 이와 별도로 `provider_execution_leases`의 `provider + account_scope` 임대와 단조 증가 fencing token을 사용합니다. 동일 scope를 한 worker만 소유하고 호출·기록 경계에서 소유권을 다시 확인하지만, 장시간 실제 운영의 다중 worker·DB failover 검증은 아직 필요합니다. 기본 운영의 worker concurrency 1 권장은 유지합니다.

정식 제휴 명세를 받더라도 endpoint·credential 이름·응답 필드를 추정해서 `.env`나 코드에 먼저
추가하지 않습니다. [승인 Provider 연동 준비 상태 감사](research/APPROVED_PROVIDER_INTEGRATION_READINESS.md)의
입력 목록을 서면 명세와 대조하고, 명세 transport·승인 근거·실행 lease·운영 제어면·sandbox
contract test가 모두 준비된 뒤에만 provider registry 등록을 검토합니다. 현재
`ApprovedProviderAdapter`는 registry에 등록되어 있지 않으며 KORAIL·SRT 예약 capability는 계속
`false`입니다. KORAIL·SRT background 관측은 승인 adapter seam과 별개인 각 3중 opt-in 실험
경로입니다. 새 provider metrics는 provider·operation·result와 duration만 기록하고
역, 열차, 계정, credential, 예약 식별자를 label로 사용하지 않습니다.

예약 시도 행을 선점한 뒤 worker가 종료되면 같은 후보·에피소드를 다시 예약하지 않습니다. 5분 넘게 `PENDING`인 attempt는 `UNKNOWN`으로 닫고 작업을 `watching`으로 복귀시키며 `reservation_attempt_result_unknown_after_restart` 수동 확인 이벤트를 남깁니다. 이는 인증 실패나 예약 실패 확정이 아니므로 운영자는 공식 예약 내역을 먼저 확인해야 합니다. `UNKNOWN`은 최대 6회의 읽기 전용 확인에서 최초 시도의 exact `NOT_FOUND`가 확인된 경우에만 같은 연속 가용 구간의 1회 재무장을 허용합니다. 이미 지난 결제기한은 감시를 계속하더라도 그 사실만으로 새 episode 예약을 허용하지 않습니다. 호출 중 사용자가 취소했거나 여행이 만료된 terminal 작업도 되살리지 않습니다.

`0005` migration은 구버전의 `reservation_attempted=true`만 남은 작업에서 어느 후보가 시도됐는지 알 수 없으므로 모든 기존 후보를 보수적으로 `UNKNOWN` attempt로 이관합니다. 업그레이드 뒤 해당 작업을 자동 재개하지 말고 공식 예약 내역을 확인합니다.

### 저장 계정과 결제 대기 복구 확인

- API가 재시작되면 저장된 활성 KORAIL·SRT 계정을 startup prewarm으로 한 번 검증합니다. 성공은 현재 credential generation과 일치할 때만 DB에 반영하고 관련 `auth_required` 작업을 재개합니다.
- 실행 중 새 `auth_required` 또는 `provider_blocked`가 저장되면 30초 maintenance가 해당 provider·credential generation·DB revision을 한 번만 복구합니다. 같은 generation의 상주 actor가 이미 `ready`이면 외부 로그인 없이 DB 인증 상태와 중단된 감시를 복구하고, 준비되지 않은 경우에만 저장 계정으로 bounded 재검증을 수행합니다. 같은 revision이 실패해도 반복 로그인하지 않으며, 새 revision이나 계정 변경 전에는 다시 시도하지 않습니다.
- `UNKNOWN + INCONCLUSIVE`는 빠른 확인 3회 뒤 5분·15분·60분 지연 확인을 이어 총 6회까지만 수행합니다. 기존 `reconciliation_attempt_count=3` legacy 시도도 자동으로 첫 지연 확인에 편입됩니다. 최초 시도의 exact `NOT_FOUND`만 같은 연속 `AVAILABLE` 구간에서 1회 재무장하며, 재무장 시도가 다시 `UNKNOWN`이면 추가 호출하지 않습니다.
- `payment_required`인데 결제기한이 없거나 이미 지난 행은 worker가 같은 credential generation의 인증 actor에서 공식 예약목록을 읽기 전용으로 재확인합니다. 빠른 확인은 최대 3회이며, 결제기한이 경과한 뒤 `post_deadline_reconciled_at` marker가 없을 때 최종 확인을 한 번 추가합니다. exact 보류가 미래의 새 기한을 제공하면 기한과 handoff를 보정합니다. exact 행이 남아 있어도 그 행의 공식 기한이 확인 시각 이하이면 결제 행동은 끝난 것으로 정리합니다. 이전 버전이 이런 행에 marker만 남긴 경우에는 호환성 정리 확인을 한 번 더 허용하고 횟수를 올려 반복하지 않습니다. 기한 없는 행·인증·차단·불확실 응답은 fail-closed로 남깁니다.
- 최종 exact `NOT_FOUND` 또는 공식 기한이 지난 exact 미결제 행이면 `notify_only` 작업은 이력을 보존한 `expired`로 종료하고, `reserve_once_before_payment` 작업만 `watching`으로 복귀합니다. 감시 복귀는 즉시 자동 재예매 허가가 아닙니다. 기존 `PAYMENT_REQUIRED` episode fence를 유지하고, 최종 확인 marker 뒤 확정 비가용 관측과 새 행동 가능 관측이 차례로 생긴 경우에만 새 episode에서 한 번 재시도합니다.
- 웹은 timezone-aware 결제기한이 지난 순간 해당 건을 홈 `결제 대기` 집계와 결제 CTA에서 제외하고 `00:00:00`을 남기지 않습니다. 서버 정리가 끝나기 전에는 `내 예약`에 `기한 경과 · 공식 확인 필요`, 실제 기한, `공식 확인 열기`로 보존합니다. 이 표시는 결제 완료나 공식 목록 부재를 추정하지 않습니다.
- 운영 확인은 `GET /api/v1/provider-accounts`, `GET /api/v1/provider-runtime-status`, 작업 상태와 `reservation_attempts.reconciliation_attempt_count`, `next_reconcile_at`, outcome·confirmation outcome, episode key의 고정 접두사만 비밀값 없이 대조합니다. legacy `count=3` 행을 수동 갱신하지 말고 다음 지연 확인 시각이 자동 생성되는지 확인합니다. 로그에서 ID·비밀번호·cookie·token 원문이나 전체 attempt ID를 출력하지 않습니다.

2026-08-03 운영 표본에서는 SRT 335(대전→부산, 2026-08-04 13:59 일반실)의 기존 `UNKNOWN + INCONCLUSIVE + reconciliation_attempt_count=3` 시도가 migration `0023` 배포 뒤 자동으로 지연 확인 대상에 편입됐습니다. 같은 credential generation의 공식 예약목록에서 21:07:36 exact `NOT_FOUND`가 저장된 뒤 정확한 작업을 정상 `process_watch_now` 경로로 처리했으며, 21:09:51 신선한 `AVAILABLE` 관측에서 `confirmed-absent-retry:<attempt.id>` sequence 2가 한 번만 생성됐습니다. 이 시도는 21:09:55 `PAYMENT_REQUIRED + CONFIRMED_PAYMENT_REQUIRED`와 21:19 결제기한으로 확정됐습니다. 이 표본은 불명확한 결과를 즉시 재호출하지 않고 공식 목록 부재 근거 뒤에만 한 번 재무장하는 계약을 실제 SRT 계정에서 확인한 것이며, 끝까지 `INCONCLUSIVE`인 경우의 무호출 경계와 재무장 시도가 다시 `UNKNOWN`인 운영 표본은 로컬 회귀 계약으로만 검증된 상태입니다.

## 백업과 복원

백업은 PostgreSQL custom dump를 age 공개키로 암호화하고 평문 파일을 남기지 않습니다. 복원은 `RESTORE_CONFIRM=RESTORE`가 없으면 실행되지 않습니다. `ops.ps1 restore`는 복원 전에 실행 중이던 Caddy·API·worker·scheduler·실험 worker를 내려 새 요청과 background write를 차단합니다. 복원과 migration이 모두 성공한 경우에만 원래 실행 중이던 서비스를 복구하며, 하나라도 실패하면 maintenance 상태를 유지합니다.

```powershell
./scripts/ops.ps1 backup
./scripts/ops.ps1 restore /backups/<파일>.dump.age
```

백업 성공은 파일 생성만으로 승인하지 않습니다. 별도 테스트 데이터베이스에 정기적으로 복원하고 데이터와 migration head를 확인해야 합니다.

## 로그와 개인정보

로그·지표 label에 관리자 ID, 역명, 열차번호, 전화번호를 넣지 않습니다. 비밀번호, 알림 token, webhook URL, Push endpoint, cookie, 카드정보를 로그로 출력하지 않습니다.

`/api/v1/operations/summary`의 최근 진행 기록은 허용된 `kind`, `status`, `level`, `error_category`, `provider`, 시각만 반환합니다. DB의 `reason`, `last_error`, outbox payload와 각 행 ID를 전달하지 않으며, 새로운 오류 분류를 화면에 추가할 때도 고정 allowlist와 redaction 회귀 테스트를 먼저 갱신합니다.
