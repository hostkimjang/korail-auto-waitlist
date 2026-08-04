# 아키텍처

`AUTH_REQUIRED`와 `PROVIDER_BLOCKED` 예약 시도는 결과 자체로 재시도하지 않습니다. provider session manager가 해당 시도 종료보다 새로운 `credential_version + last_authenticated_at` 성공 검증 세대를 만든 경우에만 그 세대를 새 episode identity로 사용해 한 번 재무장합니다. `UNKNOWN`도 결과 자체로 예약을 재호출하지 않으며, 아래의 bounded read-only 확인에서 exact `NOT_FOUND`가 확인된 최초 시도에 한해 별도 episode identity로 한 번만 재무장합니다. DB의 기존 attempt와 availability fence는 유지하므로 같은 검증 세대의 반복 관측·재시작·새로고침은 중복 호출을 만들지 않습니다.

Migration `0023_extend_unknown_reconcile`은 `reconciliation_attempt_count`의 DB 상한을 6으로 확장합니다. 이는 `UNKNOWN + INCONCLUSIVE`의 5분·15분·60분 지연 확인만 위한 변경이며, `PAYMENT_REQUIRED`의 빠른 3회와 결제기한 경과 후 최종 확인 1회는 늘리지 않습니다.

Migration `0026_unified_observation_interval`는 앞선 작업별 속도 실험을 현재 사용자 계약에서 폐기하고, 단일 `admin_accounts` 행에 전역 `seat_observation_interval_seconds` 하나를 영속합니다. `GET/PATCH /api/v1/preferences/ui`는 화면 표시 5~300초와 좌석 관측 1~600초를 서로 다른 필드로 읽고 저장합니다. 좌석 관측은 최초 설치 시 5초이며 작업별 모드나 별도 override 없이 모든 활성 작업의 다음 관측 목표에 같은 값을 사용합니다. 화면의 `timetable_refresh_interval_seconds`는 저장된 결과를 다시 읽는 주기이므로 provider 관측 주기가 아닙니다.

전역 관측값 저장은 활성 작업을 잠근 뒤 아직 due가 아니고 provider 실행 lease·보호 cooldown이 없는 작업의 `next_check_at`만 새 값으로 다시 계산합니다. 이미 due이거나 실행 중인 작업을 중복 enqueue하지 않고, 해당 관측이 끝날 때 DB의 최신 전역값으로 다음 시각을 계산합니다. 따라서 이 설정은 목표 cadence이며 provider cache·운영사별 단일 lease·circuit·backoff·cooldown을 무시하는 강제 호출 주기가 아닙니다.

현재 `process_due_watches` beat는 5초입니다. 이는 이미 계산된 `next_check_at` 도달 작업을 찾는 sweep 주기이며, 모든 좌석을 5초마다 조회한다는 뜻이 아닙니다.

## 목표와 경계

레일웨잇은 1인 관리자만 사용하는 self-hosted 서비스입니다. 사용자·조직·초대·역할·`owner_id`를 두지 않습니다. 기본 경로는 공식 시간표와 공식 예매 링크를 사용하고, 모든 결제 확정은 공식 플랫폼으로 넘깁니다.

## 구성

- `apps/web`: React·Vite PWA. 소비자 화면, 관리자 ID·비밀번호 인증, Web Push 구독, SSE 상태 갱신
- `apps/api`: FastAPI·SQLAlchemy·Alembic. 인증, 감시 작업, provider, 알림 채널, outbox
- `scheduler`: due watch와 outbox 전달 작업을 한 번만 발행
- `worker`: provider별 직렬 처리와 알림 전송. 초기 concurrency는 1
- `korail-browser-adapter`: KORAIL 전용 상주 sidecar. 인증 actor, 인증과 분리된 read-only 검색 lease, 에피소드당 1회 자동 예매·동일 세션 확인 경계를 소유
- `srt-provider-adapter`: SRT 전용 상주 sidecar. 계정 없는 검색 source와 인증·에피소드당 1회 자동 예매·공식 예약목록 확인 executor를 분리해 소유
- `postgres`: 감시·후보 열차·인증·알림·outbox·idempotency의 영속 상태
- `redis`: Celery broker와 worker coordination
- `caddy`: 유일한 외부 진입점. 웹과 `/api`를 같은 origin으로 제공하며 DB·Redis·API 포트를 외부에 직접 노출하지 않음

코드 구조는 배포 단위를 유지하는 모듈형 모놀리스를 목표로 합니다. 웹은 `app -> features -> api/domain/shared`, API는 `FastAPI·Celery·bootstrap -> application -> domain` 의존 방향을 사용하고 DB·provider·알림 구현은 application이 정의한 계약을 구현합니다. 빈 계층을 일괄 생성하지 않고 기능별 수직 슬라이스로 이동하며, 상세 디렉터리와 단계별 완료·rollback 기준은 [클린 구조 리팩터링 계획](REFACTORING_PLAN.md), import·트랜잭션·테스트 규칙은 [코드 컨벤션](CODE_CONVENTIONS.md)을 따릅니다.

현재 전환 단계에서 웹의 인증 endpoint는 `api/auth.ts`, 새 대기의 폼·KST 날짜·요일·역 교환과
provider별 예약 정책 보정은 `features/new-wait/newWaitForm.ts`가 소유합니다. 런타임 demo gate는
`shared/lib/runtimeConfig.ts`가 소유하고 모든 호출자는 실제 API 소유 모듈을 직접 import합니다.
기존 `api.js` barrel은 제거했으며 module-boundary 테스트가 같은 중앙 barrel의 재도입을 차단합니다.
API의 알림 채널 관리 HTTP route와 transport schema는 `notification_management/` 기능 패키지가
소유하고, 중앙 `schemas.py`는 같은 Pydantic class 객체를 다시 export합니다. 실시간 outbox 이벤트
stream인 `/events`는 알림 채널 CRUD와 수명주기가 다르므로 `event_stream/http.py`가 독립적으로
소유합니다. 중앙 `api.py`는 제거됐으며 아래 기능 router를 `main.py`가 명시적으로 조립합니다. 공개
endpoint·payload·관리자 인증·트랜잭션 계약은 이동 전과 같습니다. `App.jsx`와 API
`services.py`·`worker.py`·provider 경계의 추가 분리는 계속 남아 있습니다.

웹 전역 CSS 진입점 `styles.css`는 일반 규칙을 직접 소유하지 않고 `tokens -> base -> shell ->
features -> responsive` 순서의 다섯 경계를 import합니다. 첫 구조 분리는 기존 6,648줄의 selector·규칙·
media/container query·keyframes 순서를 바꾸지 않은 기계적 이동이며, 다섯 파일을 결합한 내용은 전환
전 Git blob 113,950바이트와 같습니다. feature-local `providerRuntimeStatus.css`는 기존 위치와 import
순서를 유지하고 전역 token을 사용합니다. 이후 feature별 CSS 재소유나 중복 selector 정리는 이
동작 보존 체크포인트와 섞지 않는 별도 슬라이스로 다룹니다.

웹의 알림 채널 transport·DTO 검증과 Web Push 브라우저 primitive는 `api/notifications.ts`, SSE 연결·
history cutoff·정리 계약은 `api/events.ts`가 소유합니다. strict
`features/settings/useNotificationChannelSettings.ts`는 인증된 채널 조회, 401 인증 만료 전달, focus 시
Web Push 상태 갱신과 listener 정리, 저장·활성화·시험·기기 연결 명령 및 logout reset을 조립합니다.
`App.jsx`는 인증·toast callback을 주입하고 등록할 watch의 채널 ID를 선택하는 상위 조립만 맡습니다. `NewWait`의
운영사별 역 카탈로그 요청·재시도·stale 응답 차단·선택한 역명/node ID 정합성은
`features/new-wait/useStationCatalog.ts`가 맡으며, TAGO 역 카탈로그를 운영사별 실제 운행이나 좌석
재고 근거로 승격하지 않습니다.

strict `features/settings/NotificationChannelSettings.tsx`는 알림 종류별 표시·편집·시험·활성화와
Web Push 기기 상태를 소유합니다. 채널별 pending key를 독립적으로 관리해 한 작업의 종료가 다른
저장·연결 상태를 지우지 않으며, 비밀 입력은 읽기 응답에서 복원하지 않고 성공·취소 때 지웁니다.
`api/notifications.ts`는 외부 JSON을 `unknown`에서 검증해 raw `config`를 버린 secret-free ViewModel만
반환하고 시험 전송은 `queued=true`와 비어 있지 않은 `event_id`를 함께 확인합니다. API는 채널 이름과
필수 설정 문자열을 공백까지 검증·정규화하고, DB가 timezone 정보를 잃어도 읽기 응답 시각을 UTC로
보정합니다. strict `features/settings/SettingsPage.tsx`는 설정 메뉴 union과 철도 계정·알림·화면 동작·
보안·시스템 section 조립을 소유합니다. 공용 제목 DOM은 `shared/ui/PageHeader.tsx`로 이동했으며 기존
class·section 순서·접근성 이름·44px 이상 행동 영역을 유지합니다. `initialSection`은 최초 mount에서만
상태를 정하고 `onSectionChange`는 사용자 선택에만 호출됩니다. strict
`features/settings/useProviderAccountSettings.ts`는 인증 뒤 철도 계정 로드, secret-free runtime 상태,
설정 section의 즉시·15초 polling, 계정 저장·삭제, watch 인증 전이 refresh와 fail-closed 계정 상태
selector를 소유합니다. App은 polling 활성 여부와 watch collection·화면 props를 연결하고 화면 환경설정
데이터와 mutation만 직접 조립합니다.

API의 알림 설정 검증·암호화·생성·수정·시험 전송 outbox 정책은
`notification_management/service.py`가 소유하고 HTTP 계층은 이 service의 오류만 transport 상태로
변환합니다. 알림 outbox의 소비와 전달 결과 기록은 FastAPI·Celery 비의존
`notification_management/delivery.py`가 소유합니다. 이 application 경계는 due `PENDING` 알림만
생성 시각 순으로 최대 50건 `FOR UPDATE SKIP LOCKED`로 선택하고, 누락·비활성 채널의 terminal 처리,
전달 전 attempt 증가, 최대 5회와 지수 backoff, 안전한 오류 범주, sent·failed·pending metric을 같은
기존 계약으로 유지합니다. 알림 전달 쪽 `worker.py`에는 기존 Celery task 이름과 실행·성공·실패
wrapper만 남았습니다. 여러 기능이 공유하는 outbox idempotency primitive는 `outbox.py`에 두며,
`services.py`의 import는 기존 worker와 테스트를 위한 identity-compatible 전환 경계입니다.

현재 delivery batch는 외부 알림을 전송하는 동안 선택한 outbox row lock과 DB transaction을 유지합니다.
예상하지 못한 예외로 batch가 rollback되면 이미 외부에 전달된 앞선 알림도 DB에서는 `PENDING`으로
돌아가 다음 주기에 다시 전달될 수 있습니다. 이는 이번 기계적 이동에서 행동을 바꾸지 않고 보존한
기존 at-least-once 성격이며, claim transaction과 전달 결과 transaction을 분리하려면 crash recovery와
중복 수신 정책을 함께 설계해야 하는 후속 부채입니다.

예약 시도 읽기 전용 정합화의 due 선택·공식 확인·credential generation 재검증·상태/outbox 적용은
FastAPI·Celery 비의존 `reservations/reconciliation_application.py`가 소유합니다. worker는 기존 task
이름과 `rail` queue, runtime dependency 조립만 유지합니다. 상태 적용 transaction은 실행 임대 행부터
`account -> watch -> candidate -> attempt` 순서로 잠그고, 도메인 잠금 뒤 같은 owner·scope·fencing
token·미만료 조건을 다시 확인합니다. 두 번째 확인 직후의 새 UTC 시각으로 due와 결제기한을
재평가하고 상태와 outbox를 함께 commit합니다. 잠금 대기 중 임대 epoch나 결제기한이 바뀐 늦은
공식 확인 결과는 저장하지 않으며, commit 뒤에만 adapter drain·소유 adapter close·임대 release를
수행합니다.

예약 선점·provider 호출·결과 적용은 worker-independent
`reservations/execution_application.py`가 소유합니다. 선점 transaction은 외부 provider의 인증된
계정 행부터 watch·candidate·provider circuit 순서로 잠그고, 상태·정책·circuit·계정·episode gate를
통과한 PENDING attempt와 transition·outbox를 provider I/O 전에 commit합니다. provider 예약 호출은
생성된 episode당 한 번만 수행하며, 결과 transaction은 필요한 경우 account를 먼저 잠근 뒤 watch·
candidate·attempt를 잠가 credential generation CAS와 결과·confirmation·outbox를 한 번에 commit합니다.
호출 중 상태가 바뀐 늦은 결과는 PENDING만 UNKNOWN으로 닫고 manual-check outbox와 함께 저장하며,
결과 적용 실패는 선점 claim을 남긴 채 부분 결과를 rollback합니다. worker에는 episode 계산, 실행 임대,
adapter 생성·drain·close·release, concrete service와 provider error/source dependency 조립만 남습니다.
application은 config·metric·observation·provider runtime·service 구현을 직접 import하지 않습니다.

due sweep의 선택·provider별 그룹 구성·task-scoped adapter 수명주기는 FastAPI·Celery 비의존
`observations/due_pipeline_application.py`가 소유합니다. worker는 설정에서 arm할 provider를 정하고
runtime dependency와 `WATCH_GROUPS` metric, 기존 Celery task 이름·`rail` route만 조립합니다. 같은
provider의 관측 그룹과 예약 정합화는 직렬이며 provider 간에는 병렬로 진행하고, 한 provider 실패가
다른 provider를 취소하지 않은 뒤 최초 실패를 전파합니다. provider 입력은 최초 등장 순서로 중복
제거해 adapter 획득·arm·close를 한 번만 수행하고, 각 그룹은 실제 시작 시각을 새로 읽습니다. sweep
전 만료 처리는 `Watch.id` 순서로 잠그고 후보 부분 만료·watch 상태·transition history·outbox를 한
transaction에서 commit하므로 stale 예약 복구 행의 유무가 만료 지속 여부를 바꾸지 않습니다.

watch 만료 수명주기는 worker-independent `watch_management/expiry_application.py`가 소유합니다.
due application이 연 UTC 시각과 DB session을 넘기고 worker는 `apply_watch_transition` dependency만
조립합니다. 만료 가능한 상태 9종을 `Watch.id` 순서로 다시 잠그며, 관측 가능한 후보가 있으면
`decide_operational_expiry`의 fresh 운행·booking-window 결정을 권위 경계로 사용합니다. 후보가 없는
legacy watch만 KST 서비스 날짜와 시간창을 사용하고 종료가 시작 이하이면 익일로 보정합니다.
후보 부분 만료, watch 상태, transition history와 outbox는 pass당 한 transaction으로 commit하며 예외
시 함께 rollback합니다. 이 application은 provider·실행 임대·설정·metric을 소유하지 않습니다.

웹 역 카탈로그 DTO 검증·identity 병합은 `api/stations.ts`, 시간표 query·provider 부분 실패·DTO→도메인
mapping은 `api/timetables.ts`, 좌석 등급과 provenance fail-closed 정규화는 `api/seatClasses.ts`가
소유합니다. canonical 시간표 mapper는 provider·열차번호·출발역·도착역과 timezone-aware 출도착
시각이 빠지거나 요청 provider와 어긋난 응답을 거부합니다. 선택 필드인 운임·출처·조회 시각·공식 URL은
검증에 실패하면 임의 값을 만들지 않고 `null|unknown`으로 강등하며 demo 시간표도 같은 mapper를
통과합니다. `NewWait`의 자동 검색·provider별 재시도·수동 전체 조회·cache-only 동기화는
`useTimetableSearch.ts`에서 하나의 query key와 stale 응답 차단 계약을 공유합니다.

strict `features/new-wait/TrainResultCard.tsx`는 열차 카드와 좌석 등급별 표현, provenance와 client
freshness에 따른 관측 유효성, `idle|pending|active|cancelling|error` 등록 상태 union의 표시·행동을
소유합니다. 공식 예매·예약대기 portal은 직접 import하지 않고 typed `OfficialHandoff` component를
주입받아 기존 focus·clipboard·공식 URL 경계를 유지합니다.

API의 `/timetables`, `/timetable-snapshots`, `/seat-status/refresh` HTTP 경계는
`timetable_management/http.py`, live→TAGO fallback·공식 confirmation/browser snapshot overlay·등록
capability·evidence 저장 orchestration은 FastAPI 비의존 `timetable_management/application.py`가
소유합니다. 역 카탈로그 `/stations`는 `timetable_management/catalog_http.py`, KORAIL snapshot
revision과 공식 화면 confirmation은 `timetable_management/official_evidence_http.py`가 맡습니다.
시간표 요청과 수명주기가 다른 `/seat-status/status`는 `seat_status_operations/http.py`가 source
cooldown 상태만 노출합니다.

웹의 watch 생성 payload·멱등 키·CRUD endpoint와 외부 응답 검증·ViewModel 투영은
`api/watches.ts`가 소유합니다. provider·status·날짜·후보 identity·선택적 시각·공식 URL을 경계에서
검증하고, 최신 좌석 관측은 source와 `observed_at < fresh_until` 계약이 모두 확인될 때만 공식 또는
mock 관측으로 투영합니다. `features/app/useWatchCollection.ts`는 canonical REST snapshot, SSE burst
병합, 예약정책 변경과 교차한 stale GET 차단, 인증·구독 lifecycle 세대 격리와 상태 전이 알림을
소유합니다. pause·resume·cancel·delete와 예약정책 변경은 strict
`features/app/useWatchMutations.ts`가 같은 canonical `MappedWatch`를 사용해 demo와 live 경로를
조립합니다. 실패 toast와 cancel 오류 재전파를 보존하고, 예약정책 변경은 mutation guard를 먼저 연
뒤 성공·실패 모두 guard 종료와 목록 refresh를 수행합니다. `App.jsx`에는 이 훅과 화면을 연결하는
조립만 남았으며 Home·새 대기 페이지와 알림·철도 계정 orchestration 추출 뒤 현재 417줄입니다. `fixtures/demoData.ts`의 typed
factory는 초기 demo 작업과 마법사 완료 결과도 같은 `MappedWatch` 계약으로 생성합니다.

strict `features/new-wait/NewWaitPage.tsx`는 여정·조건·열차 단계 렌더링, 역 카탈로그·시간표 조회·
좌석별 등록 hook 조립과 취소 흐름을 소유합니다. 공식 handoff는 다른 feature를 직접 import하지 않고
typed component prop으로 받아 `app -> feature` 의존 방향을 유지합니다. 등록 완료 뒤 canonical watch
collection 반영은 App 조립점이 담당하며, 공개 `NewWait` 호환 adapter는 실제 `OfficialHandoff`를
주입합니다. `useSeatWatchRegistration.ts`는 좌석별 등록·정확한 watch ID 취소·pending 중복 차단·
만료 evidence 재조회와 1회 재시도를 계속 소유합니다. App 조립점은 아직 `checkJs=false`인 JSX이므로
직접 caller props의 정적 검증은 최종 `App.tsx` 전환 때 완료하며, 현재는 App 통합 회귀 테스트가 이
연결 계약을 고정합니다.

strict `features/home/HomePage.tsx`는 `WatchManagementHero`와 결제 보류·활성 감시 목록 조립을
소유합니다. 페이지는 home 소유 컴포넌트와 shared UI만 직접 사용하고, production App에서 새 대기·
예약 목록·철도 계정 행동과 좌석 발견 action renderer를 구체 callback으로 주입받습니다. 실제
`OfficialHandoff` renderer와 `activeWatchHandoffTrain` 변환, 공개 `Home` 호환 adapter는 App에
유지합니다. 호환 adapter의 단일 `paymentWatch`와 optional refresh 계약은 명시 타입과 회귀 테스트로
고정했습니다.

strict `features/reservations/ReservationsPage.tsx`는 예약 요약·목록·새 대기 행동과 공식 예매/결제
handoff 조립을 소유합니다. `ReservationListWatch`를 직접 사용하고 App의 화면 이름 대신 구체적인
`onCreate` callback을 받습니다. 공식 URL이 있을 때만 사용자 클릭으로 `_blank`와
`noopener,noreferrer`를 사용해 열며, 기한 경과 결제는 결제 대기 집계와 CTA에서 제외하고 감사용
공식 확인 상태로 보존합니다. App의 production 경로는 이 strict 페이지를 직접 사용하고, 기존 공개
`Reservations({ onNavigate })` export는 얇은 adapter로 `onNavigate("new")` 계약을 유지합니다.

API의 watch CRUD·start·pause·cancel·mock-transition HTTP 경계와 즉시 처리 best-effort enqueue는
`watch_management/http.py`, 최신 observation·reservation attempt batch 조회와 결제 보류 read
projection은 `watch_management/read_model.py`가 소유합니다. 공개 endpoint·관리자 인증·트랜잭션과
commit 뒤 enqueue·멱등성·provider capability·outbox 정책은 이동 전과 같습니다. 이전 중앙
`api.py`의 잔여 6개 endpoint는 각각 `event_stream/http.py`의 SSE `/events`,
`provider_registry/http.py`의 `/providers`, `timetable_management/catalog_http.py`의 `/stations`,
`seat_status_operations/http.py`의 `/seat-status/status`,
`timetable_management/official_evidence_http.py`의 KORAIL snapshot revision과 공식 화면 confirmation
경계로 이동했습니다. SSE는 `Last-Event-ID`, `text/event-stream`, `Cache-Control: no-cache`,
`X-Accel-Buffering: no` 계약을 유지하고 history 조회와 각 poll마다 짧은 새 DB session을 열고 닫습니다.
관리자 인증 dependency 자체의 streaming response 수명과 outbox cursor의 commit 순서 의미는 이번
이동에서 바꾸지 않았으며 별도 수명주기·정책 슬라이스에서 검증할 부채로 남아 있습니다.

watch 정책 변경·start 뒤 즉시 처리 자격은 FastAPI 비의존 `watch_management/application.py`가
요청 정책·영속 상태·provider·인증 계정·reservation capability 순서로 fail-closed 판단합니다.
실제 broker enqueue는 service의 commit이 끝난 뒤 HTTP 계층이 best-effort로 호출하므로 잠금·outbox·
멱등성과 broker 실패 비차단 계약은 달라지지 않습니다.

## 주요 흐름

1. 관리자 계정이 없고 서버 운영자가 `AUTH_INITIAL_REGISTRATION_ENABLED=true`를 명시한 최초 접속에서만 관리자 ID와 비밀번호를 등록합니다. 정규화한 ID, Argon2id 비밀번호 해시, 인증 세션을 DB 트랜잭션으로 저장하고 등록 직후 앱에 진입합니다. 계정이 생기면 설정값과 무관하게 추가 등록은 닫히며, 이후에는 유효 세션이 없을 때만 ID·비밀번호 로그인 화면을 표시합니다.
2. 새 대기 화면은 TAGO 원본 식별자와 KORAIL 공개 역 안내의 교집합에서 `node_id·역명·도시`를 선택해 출발역·도착역을 구성하고, KTX(KORAIL)·SRT 복수 선택, 역 교환, 커스텀 달력, 가장 가까운 요일 빠른 선택, 30분 단위 출발 시간 범위를 한 여정 조건으로 만듭니다. 서비스일 끝 경계는 UI에 `다음 날 00:00`으로 표시하지만 API에는 같은 서비스일의 `23:59`로 정규화합니다. 이로써 23:30 이후 출발하거나 자정 뒤 도착하는 열차를 포함하면서, 같은 날짜의 `00:00`을 종료로 보내 역전된 시간창을 만들지 않습니다. 다음 날 00시 이후 출발 열차는 출발일 자체를 다음 날로 선택합니다. 운영사 선택은 이 공용 일반·고속열차 역 목록을 줄이지 않으며, 서울발 SRT나 수서발 KTX 같은 실제 운행 가능성은 선택 날짜의 시간표 결과로 판단합니다. SRT source가 지원하지 않는 구간은 카탈로그 단계가 아니라 SRT 실행 시점에서 빈 결과·`unsupported_route`로 fail-closed합니다. 역 검색 결과는 역명 완전 일치·접두 일치·포함을 지역명 일치보다 먼저 보여주며, 빈 검색어에서는 카탈로그 기본 순서를 유지합니다. 직접 입력 중에는 node ID를 지우고 공식 항목을 다시 선택할 때까지 다음 단계를 차단하며, 내부 수집원 이름은 화면에 표시하지 않습니다.
3. 웹이 선택한 운영사마다 출발·도착 역명과 KST `departure_from·departure_to`를 시간표 API에 전달하고, 공식 역 카탈로그를 선택한 정상 흐름에서는 출발·도착 node ID도 함께 전달합니다. API는 두 시각이 같은 서비스 날짜이고 종료가 시작보다 늦은지 검증하며, node ID가 제공된 경우에는 역명 쌍이 원본 식별자 카탈로그와 일치하는지도 검증합니다. node ID가 없어도 KORAIL Chromium·SRT live 주 경로는 역명·구간 조건으로 공식 시간표와 좌석 상태를 조회할 수 있습니다. 다만 이 결과에는 node-bound confirmation overlay를 적용하거나 `registration_evidence_id`를 발급하지 않으므로 시간표·좌석 표시는 유지하되 대기 등록은 fail-closed합니다. 검증 뒤 KORAIL은 서버 Chromium 공식 결과를, SRT는 운영사 live source를 시간표·좌석의 주 데이터 경로로 호출합니다. KORAIL은 KTX 계열만, SRT는 SRT 행만 정규화하고 요청 시간창의 양 끝을 포함해 필터링합니다. 운영사 live 조회가 성공하면 TAGO를 호출하지 않습니다. live source가 timeout·상류 장애·미활성 등으로 사용할 수 없을 때만 기존 TAGO 시간표 adapter를 fallback으로 호출하며, TAGO 응답에는 좌석 재고 근거가 없으므로 일반실·특실을 `unknown/not_observed`로 유지합니다. 웹은 운영사별 결과를 합쳐 정렬·중복 제거하며 화면 표시 개수로 자르지 않고, 한 운영사가 실패해도 다른 결과를 보존합니다. 3단계에서도 같은 달력으로 실제 출발일을 선택하거나 시간 범위를 바꾸어 즉시 다시 조회합니다. 성공한 시간표·좌석 응답은 API 프로세스의 동일 query snapshot cache에 최대 24시간·128개까지 보관합니다. `GET /api/v1/timetable-snapshots`는 cache miss를 404로 닫고, hit에서는 마지막 정상값을 먼저 반환합니다. 저장 뒤 60초가 지난 hit만 동일 query 백그라운드 재검증을 예약하며, 동시 요청은 한 작업으로 합치고 실패는 30초부터 최대 5분까지 backoff합니다. 재검증 loader도 기존 provider singleflight·cooldown·fail-closed 경계를 그대로 통과합니다. 웹의 기본 5초 자동 동기화는 이 stale-while-revalidate endpoint를 사용하고, 수동 원형 새로고침은 기존 정상 시간표 조회 경로를 한 번 실행합니다.

KORAIL Chromium 좌석 보강은 미래 서비스일에는 병결 보조편 identity를 보존하기 위해 00:00부터 읽습니다. 당일에는 공식 picker가 이미 지난 시각을 선택할 수 없으므로 KST 현재 hour와 요청 시작 중 늦은 시각을 사용하고, 시간창이 모두 지났으면 browser를 호출하지 않고 `unknown/not_observed(departure_window_elapsed)`로 닫습니다. 웹은 이를 공급원 장애인 `조회 지연`과 구분합니다. 모든 미관측 원인이 출발 시간 경과이면 3단계 요약을 `선택한 출발 시간대가 지났습니다`로 표시하고 서버 재조회 행동을 만들지 않습니다. 출발 시간 경과와 다른 미관측 원인이 섞인 경우에는 경과한 운영사를 제외하고 실제로 미확인인 운영사만 재조회 대상으로 계산합니다. 공식 입력에 이미 선택된 당일이 날짜 picker 링크 목록에서 빠져도 Pydoll은 현재 날짜 readback이 exact match하면 날짜를 다시 누르지 않고 시각만 선택합니다. 이 조정값도 서비스일·시간창을 포함한 exact query key에 들어가며 다른 날짜의 일반 실패 backoff를 공유하지 않습니다.
4. 결과 카드는 시간표와 좌석 상태를 다른 계약으로 다룹니다. 조회 시각·운임·소요시간은 열차 단위로 표시하되 내부 수집원 이름은 숨기고, 일반실(`standard`)·특실(`first`)은 각각 status·provenance·actions를 가진 2열 좌석 패널로 표시합니다. 좌석 source가 관측한 상태·provenance는 유지하지만, API는 `get_execution_provider(provider).capabilities().seat_monitoring=false`이면 `add_to_watch` action을 제거하고 `timetable_seat_evidence` ID를 발급하지 않습니다. 실행 capability까지 통과한 좌석만 짧게 유효한 불변 evidence ID를 받으며 응답은 `no-store`입니다.
5. 좌석 패널 행동은 관측 상태와 실행 capability를 함께 사용합니다. `available`, `limited`, `standing_plus_seat`는 공식 예매 인계와 함께, 유효한 evidence 및 `seat_monitoring=true`일 때 선택 좌석별 감시 등록을 제공합니다. `waitlist_available`은 공식 예약대기 인계를 제공할 수 있으며, `sold_out`의 취소표 대기와 `waitlist_available`의 대기 추가도 `seat_monitoring=true`일 때만 제공합니다. KORAIL·SRT는 각각의 3중 opt-in에서만 대기 행동과 evidence를 발급합니다. `reserve_once_before_payment` 자동 예매는 작업 전체 1회가 아니라 가용성 에피소드당 1회이며, 별도 운영사 플래그, 활성 암호화 계정, 작업별 정책까지 모두 충족할 때만 실행합니다. `not_offered`는 행동을 비활성화합니다. 일반적인 일부 미관측은 해당 운영사의 제한된 서버 재조회 fallback을 제공하지만, KORAIL sidecar가 보호 신호를 내부 HTTP 423 `provider_access_restricted`로 정규화한 경우 일반실·특실을 모두 `unknown/not_observed`로 유지하고 예매·대기 CTA와 재조회 버튼을 숨깁니다. 일반실과 특실은 `train_id + seat_class` 복합 identity로 독립 동작합니다. 상태·행동은 읽을 수 있는 이름과 의미 색상으로 함께 표시하고, 선택된 대기 행동은 `aria-pressed`와 44px 이상 행동 영역을 유지합니다.
6. 좌석 행동을 누르는 즉시 해당 `train + seat_class` 하나로 감시 작업 payload를 생성하고 생성 응답에 웹의 `startWatch`를 호출합니다. 별도 확인 단계, `등록 완료` 버튼, 운영사·좌석 등급 묶음은 없습니다. 각 작업은 출발·도착 node ID와 후보 열차 한 건의 열차번호·출도착 시각·좌석 등급, 서버 발급 `registration_evidence_id`를 `watch_candidates`에 기록합니다. 서버는 provider·두 node ID·정규화 열차번호·UTC 출발시각·승객 수·좌석 등급·유효기간과 발급 당시의 등록 허용 여부를 정확히 검증합니다. 따라서 `unknown`·`not_observed`·`add_to_watch`가 없던 좌석과 migration 전 evidence는 생성 단계에서 `422`로 닫힙니다. 생성 단계에서만 typed `registration_evidence_conflict/expired`가 발생하면 웹은 해당 운영사의 정상 `POST /seat-status/refresh`를 정확히 한 번 호출합니다. 새 응답의 provider·정규화 열차번호·출발시각·좌석등급이 같고 상태가 변하지 않은 신선한 공식 관측과 새 evidence ID가 있을 때만 생성·시작을 한 번 재시도합니다. identity·상태 변화, 만료된 공식 페이지 근거, 미관측·보호 응답, 갱신 실패, 같은 ID 재사용은 등록하지 않습니다. 생성은 evidence-bound, 시작은 watch-bound 멱등 키를 사용하므로 시작 응답이 유실된 뒤 사용자가 다시 눌러도 같은 작업 생성·시작을 재생하며 새 작업을 만들지 않습니다. 웹은 생성된 watch ID와 정규화한 작업별 `reservation_policy`를 좌석별 상태에 보존하고 등록된 같은 버튼을 다시 누르면 해당 ID의 cancel API를 호출합니다. 새 대기 화면을 다시 열면 활성 DB 작업의 provider·열차번호·출발시각 instant·좌석 등급을 현재 카드와 비교해 같은 좌석을 active로 hydrate하고, 실제 watch ID와 `notify_only|reserve_once_before_payment` 정책 표시를 복원합니다. 성공한 등록 snapshot과 홈 활동 목록은 현재 결과를 재조회해도 보존됩니다. 새 대기 화면에는 별도의 전역 자동화 모드 카드나 토글이 없습니다.

시간표 자동 동기화는 React page reload가 아닙니다. `reconcileTrainSnapshots`가 JSON API 경계에서 내용이 같은 열차 객체와 전체 배열의 기존 참조를 유지하고, 열차 카드는 `memo` 경계에서 열차 snapshot 또는 해당 좌석 등록 상태가 실제로 바뀔 때만 다시 렌더링합니다. 자동 타이머의 진행 시각·오류는 새로고침 컨트롤 내부 상태로만 관리합니다. 자동 동기화는 날짜·시간 입력 카드보다 작은 컴팩트 상태 헤더로 표시합니다. 원형 아이콘은 빠른 응답에도 최소 한 바퀴(800ms)를 회전하고 느린 응답은 다음 회전 경계에서 정지하며, 고정 폭의 `최근 갱신 HH:mm:ss` 노드를 유지해 매 갱신의 레이아웃 깜빡임을 줄입니다. 모션 감소 설정에서는 회전 animation을 사용하지 않습니다.
7. API는 작업·후보 열차와 `watch.created` outbox 이벤트를 같은 DB 트랜잭션에 기록합니다. 후보 identity와 우선순위는 DB 고유 제약으로 보호하고, 후보와 어긋나는 시간창·열차번호·좌석 등급 수정은 거부합니다.

Migration `0017`은 단일 관리자용 KORAIL·SRT 계정 설정을 `rail_provider_accounts`에 추가합니다. 회원번호·이메일·휴대전화 중 명시한 `login_method`, 로그인 ID와 비밀번호를 하나의 암호문으로 저장하고 `SECRET_ENCRYPTION_KEY`로만 복호화하며, 조회 API는 설정 여부·활성 여부·로그인 방식·마스킹한 ID·credential version·허용 목록의 최근 인증 상태와 시각만 반환합니다. `PUT /provider-accounts/{provider}`는 현재 DB에서 저장될 다음 credential generation을 읽은 뒤 외부 로그인 검증 동안 읽기 트랜잭션을 유지하지 않습니다. 검증 성공 뒤 계정 행을 다시 잠그고 검증한 generation이 현재 generation의 정확한 다음 값일 때만 암호문과 `authenticated` 상태를 commit합니다. 동시 계정 변경이 먼저 저장된 경우에는 uniqueness 제약을 포함한 compare-and-swap 경계에서 충돌로 닫아 최신 계정을 덮지 않습니다. KORAIL은 기존 재사용 session을 폐기하고 이 저장 예정 generation의 새 Pydoll CDP context에서 `open → 로그인 방식 탭 → 활성 tab panel의 ID·비밀번호·로그인 버튼 → 로그인`을 수행합니다. 공식 페이지가 ID와 비밀번호를 서로 다른 HTML `form`에 두고 로그인 버튼을 두 form 밖에 배치하므로 form 동일성을 가정하지 않고, 선택한 활성 panel 안에서 각 control이 하나일 때만 입력합니다. Pydoll의 다중 요소 조회가 실행 시점에 list 또는 async iterator 중 어느 형태를 반환해도 공용 DOM 경계에서 정규화하고, `await`가 필요한 텍스트 판정은 명시적 비동기 반복으로 처리합니다. 로그인 제출 뒤 React 헤더보다 서버 세션이 먼저 만들어질 수 있으므로 로그인 화면에서 `로그아웃` 행동과 공식 `loginCheck` boolean을 독립적으로 확인합니다. `loginCheck`는 명시적인 계정 검증 한 번당 최대 두 번만 확인하며, 어느 쪽이 먼저 성공하더라도 공식 검색 화면으로 이동해 같은 browser context의 세션을 다시 확인한 뒤에만 저장합니다. URL 이동이나 KORAIL 앱 로그인 알림만으로는 성공 처리하지 않습니다. 검증된 browser session은 같은 credential generation, 마지막 사용 기준 TTL, `login_method`·`login_id`·비밀번호의 SHA-256 fingerprint가 모두 일치할 때만 이후 예약에서 재사용합니다. fingerprint는 원문 credential과 함께 프로세스 메모리 안에서만 다루며 fingerprint 자체도 로그·응답·DB·artifact로 내보내지 않습니다. background·read-only 시간표 검색은 인증 session이 존재할 때 별도 ephemeral browser lease를 열고, 분리된 HTTP replay lease로 전환하더라도 인증 session을 소비·교체·폐기하지 않습니다. SRT는 명시한 방식으로 정규화한 일반 로그인 경로를 사용합니다. 두 검증 모두 시간표 검색·좌석 선택·예약을 호출하지 않습니다. 실패하거나 generation CAS가 충돌하면 새 비밀번호를 저장하지 않고 기존 암호문과 version을 유지합니다. 비밀번호와 원문 ID, 로그인 cookie·세션은 API 응답이나 DB 계정 행에 저장하지 않으며 secret-bearing validation 오류도 일반화한 `no-store` 응답으로 반환합니다. 인증 성공 시각은 마지막 성공을 뜻하므로 이후 `auth_required`, `provider_blocked`, `failed` 상태가 기록되어도 유지합니다. 새 대기 UI의 `reservation_policy`는 선택한 모든 운영사 계정이 로그인 확인·활성 상태이면 `reserve_once_before_payment`를 기본으로 보내며 사용자는 `notify_only`로 바꿀 수 있습니다. 계정이 없거나 비활성·미인증이면 `notify_only`로 강등합니다. 이 값과 계정 저장만으로 provider capability가 열리지는 않습니다.

KORAIL 검색 화면은 공개 `열차 조회` 제어를 먼저 렌더링하고 비동기 `GET /ebizweb/common/loginCheck` 뒤에 인증 헤더를 갱신합니다. 계정 검증은 로그인 화면에서도 헤더 수화와 무관하게 같은 브라우저 context의 공식 bundle 조건, 즉 `strResult=SUCC`이면서 `h_msg_cd`가 없는지를 page context boolean으로 제한 확인합니다. 이 값 또는 정확한 `로그아웃` 표시가 관측되면 검색 화면으로 이동해 `loginCheck`를 다시 확인하고, 검색 화면에서 false이거나 응답이 불완전하면 `a.btnGoLogout`·`button.logoutBtn`의 정확한 `로그아웃` 표시를 제한 시간 동안 추가 확인합니다. 모든 근거가 없으면 fail-closed 하며, loginCheck 응답 본문·사용자 정보·cookie는 Python 모델과 로그로 꺼내지 않습니다.

상주 sidecar 안에서도 시간표·좌석 검색과 인증이 필요한 예약 업무는 actor 경계를 공유하지 않습니다. KORAIL은 read-only 검색 actor의 ephemeral browser/HTTP replay lease와 인증·예약·공식 예약 확인 actor를 분리하고, SRT는 계정 없는 검색 source와 인증·예약·공식 예약 확인 executor를 분리합니다. 검색 실패·취소·lease 교체는 인증 session을 소비하거나 폐기하지 않으며, 공식 예약 확인은 검색 actor가 아니라 실제 시도와 같은 credential generation의 인증 actor에서만 실행합니다.

8. 공식 KORAIL·SRT 작업의 start 요청은 `draft → scheduled`를 만듭니다. KORAIL은 `EXPERIMENTAL_RAIL_ENABLED`, `KORAIL_BROWSER_ADAPTER_ENABLED`, `KORAIL_SEAT_MONITORING_ENABLED`, SRT는 `EXPERIMENTAL_RAIL_ENABLED`, `SRT_SEAT_STATUS_ENABLED`, `SRT_SEAT_MONITORING_ENABLED`가 모두 `true`일 때만 `seat_monitoring=true`가 됩니다. `reservation_once`는 이 관측 조건과 `KORAIL_RESERVATION_ONCE_ENABLED` 또는 `SRT_RESERVATION_ONCE_ENABLED`가 함께 켜진 경우에만 capability로 노출됩니다. 계정 없음·작업 `notify_only`·플래그 비활성 중 하나라도 있으면 예약 호출은 실행하지 않습니다. 반대로 로그인 확인 계정, `reserve_once_before_payment`, `reservation_once=true`를 모두 충족한 새 작업은 start 트랜잭션 commit 뒤 단일 작업 경로인 `process_watch_now`를 best-effort로 `rail` queue에 넣습니다. enqueue 실패는 이미 commit된 작업을 롤백하지 않으며, 5초 간격 `process_due_watches` beat가 영속 fallback입니다. 즉시 task도 주기 task와 같은 실행 임대·circuit·예약 fence를 통과합니다.

홈은 `draft`, `scheduled`, `watching`, `official_waitlist`, `seat_found`, `reserving`, `paused`, `cooldown`, `auth_required` 상태를 활동 중 대기로 분류합니다. API와 웹 모두 고정 표시 개수 제한을 두지 않고 해당 항목을 전부 렌더링하며 제목에 실제 전체 건수를 표시합니다. 각 상태는 색상 원형과 텍스트를 함께 사용합니다. 웹이 보이는 동안 관리자 화면 동작 주기로 `/watches`만 다시 읽고 SSE burst·`watch.seat_observed`·`watch.reservation_attempted`·`watch.reservation_result`·헤더 수동 새로고침을 같은 coordinator에서 병합하며, hidden 탭에서는 주기 요청을 멈추고 visible 복귀 시 즉시 갱신합니다. 헤더는 목록 데이터를 응답 즉시 반영하면서도 빠른 요청에는 최소 한 바퀴(800ms), 느린 요청에는 다음 회전 경계까지 원형 아이콘을 회전시킨 뒤 정지합니다. 마지막 성공 완료 시각은 고정폭으로 표시하며 provider 조회를 별도로 실행하지 않습니다. API는 각 후보에 최신 `SeatObservation`의 상태·출처·`observed_at`·`fresh_until`을 `latest_observation`으로 제공하고 후보별 시각의 최댓값도 `last_checked_at`으로 제공합니다. 작업 `updated_at`은 좌석 확인 시각의 대용이 아니며, 불변 `registration_evidence`는 등록 당시 감사 근거로만 보존합니다. 웹은 현재 priority 후보의 실제 `departure_at`·`arrival_at`을 시간창보다 우선하고, `latest_observation`이 신선하고 `available`·`limited`·`standing_plus_seat`일 때만 `예매` CTA를 표시합니다. 최초 REST snapshot은 알림 기준선으로만 사용하고, 이후 좌석 발견·예매 진행·결제 필요·인증 필요·예매 실패 후 감시 복귀를 하나의 `실시간 알림` surface에 넣습니다. 결제·수동 확인·인증·좌석 발견을 먼저 정렬하고 종류별 그룹·건수·펼치기·그룹 닫기를 제공하며, 같은 watch의 단계는 같은 subject 한 장으로 교체합니다. 동일 revision과 기존 카드보다 오래된 `revisionAt`은 카드와 live region 모두에서 무시합니다. 일반 알림은 30초, 진행·복구 알림은 60초, 행동 필요 알림은 수동으로 닫고, surface를 접을 때는 카드를 hidden 상태로 유지해 타이머를 중단하거나 재시작하지 않습니다. 예약 단계는 상태 전이로 증명된 `좌석 발견`, `에피소드당 1회 예매 처리`, `공식 결과 확인`, `감시 재개`만 표시합니다. `watch.reservation_result`의 후보 ID를 해당 후보의 열차번호·좌석·출도착 문맥과 결합하므로, 우선순위 첫 열차가 아니라 실제 시도한 열차를 안내합니다. 활동 행은 `.watch-list`의 inline size를 기준으로 container query를 적용해 운영사·시간, 상태·근거, 정책·행동을 독립 행으로 재배치합니다. 좁은 화면과 200% 확대에서는 `좌석 재발견마다 자동 예매`를 줄바꿈하되 정책 스위치와 pause·cancel의 44px 영역을 줄이지 않고, 동일 열차의 일반실·특실 스위치도 접근 가능한 이름의 좌석 등급으로 구분합니다. 좌석이 사라지면 CTA를 즉시 제거하고 작업은 출발시각까지 감시를 계속합니다. CTA는 정확한 잔여 유형을 추측하지 않고 좌석 등급·최근 관측 시각을 표시한 뒤 기존 `OfficialHandoff`를 통해 여정 복사와 운영사 고정 검색 진입점을 제공합니다. 열차별 공식 딥링크나 결과 자동 입력을 약속하지 않습니다. 현재 watch snapshot이 실제 `auth_required`일 때만 행 안에 컴팩트 경고와 `철도 계정` CTA를 렌더링하며, 과거 transition 이력만으로 인증 경고를 합성하지 않습니다. `payment_required` 중 미래의 timezone-aware 기한 또는 기한 미제공 건만 홈의 긴급 결제 대기와 결제 CTA에 포함합니다. 화면 시계가 기한을 넘으면 긴급 집계에서 제거하고 `00:00:00`을 남기지 않습니다. 서버가 공식 보류를 정리하기 전인 기한 경과 건은 `내 예약`에 `기한 경과 · 공식 확인 필요`와 실제 기한, 공식 확인 CTA로 보존합니다. 이는 결제 완료나 보류 소실을 추정하지 않는 표시 계약입니다. `completed`, `expired`와 과거 terminal `failed`는 활동 중 목록에 섞지 않고 `내 예약`의 전체 내역에서 확인합니다.
9. scheduler는 실행 capability가 있는 mock과 명시적 3중 opt-in KORAIL·SRT 작업만 due 대상으로 worker에 전달하고, 동일 provider·구간·날짜·시간창·좌석 등급은 `dedupe_key`로 묶습니다. mock은 외부 I/O 없이 실행됩니다. KORAIL·SRT는 provider/account DB 실행 임대와 fencing을 통과해 좌석을 관측하며, 추가 자동 예매 gate와 작업 정책까지 통과한 후보만 예약 호출 대상으로 선점합니다.
10. worker는 due 작업 처리 전에 KST 기준 여행일·시간창이 지난 활성 작업을 운영사와 관계없이 `expired`로 전이하고 다음 실행 시각을 제거합니다. 후보나 역 node ID가 없는 실행 작업은 provider를 호출하지 않고 `paused`로 중단합니다.
11. mock worker는 같은 조건의 후보 관측을 한 번으로 병합한 뒤 각 작업에 정규화 `SeatObservation`을 기록합니다. 관측·상태 전이·outbox는 한 트랜잭션으로 저장하고, 변화 없는 상태 벡터만 `unchanged_runs`에 반영해 backoff를 늘립니다. 관측 시각은 변화 fingerprint에서 제외합니다.
12. 좌석이 발견되면 `ReservationAttempt`를 provider 호출 전에 commit하고 각 후보 관측 전과 예약 직전에 circuit을 다시 확인합니다. 예약 preflight와 결과 기록은 모두 provider account 행을 먼저, watch 행을 나중에 잠급니다. 로그인 저장도 같은 provider account → watch 순서를 사용하므로 동시 로그인 검증과 예약 처리 사이의 PostgreSQL 교착을 피합니다. migration `0018_reservation_episodes`는 후보별 영구 unique를 `attempt_sequence`와 후보 범위의 고유 `episode_key`로 교체합니다. 첫 시도는 최초 에피소드, `NOT_AVAILABLE` 뒤 재시도는 이전 시도 종료 뒤 처음 기록된 확정 비가용 observation ID, `AUTH_REQUIRED` 뒤 재시도는 `credential_version + last_authenticated_at` 로그인 검증 세대를 에피소드 identity로 사용합니다. 최초 `UNKNOWN` 시도의 공식 확인이 exact `NOT_FOUND`로 끝난 경우에는 `confirmed-absent-retry:<attempt.id>`를 identity로 사용해 좌석이 계속 `AVAILABLE`인 같은 구간에서도 한 번만 재무장합니다. 이 DB 제약은 같은 근거의 중복 선점을 막으면서도, 보류가 없음을 확정한 `NOT_AVAILABLE` 뒤 좌석 소실→재출현, 이전 시도보다 새로운 로그인 재검증 또는 최초 `UNKNOWN`의 exact 부재 확인이라는 근거가 생기면 각각 제한적으로 재무장합니다. KORAIL·SRT 예약 adapter는 DB에서 실제로 불러와 외부 호출에 사용한 credential version을 내부 `ReservationResult.credential_version`으로 반환합니다. 결과가 돌아오면 예약 실행 application은 preflight에서 본 과거 값이 아니라 이 실제 사용 version으로 계정 상태를 CAS합니다. 따라서 호출 중 새 로그인이 검증·저장된 경우 늦게 도착한 과거 `AUTH_REQUIRED`·`PROVIDER_BLOCKED` 결과가 최신 `authenticated` 상태를 강등하지 않습니다. 성공은 `completed`가 아니라 `payment_required`까지만 전이하고, 더 낮은 우선순위 후보는 삭제하지 않고 `suppressed_by_priority`로 남깁니다. `FAILED`·`UNKNOWN`·`PROVIDER_BLOCKED`, 이미 지난 결제기한, worker 재시작 뒤 stale `PENDING → UNKNOWN`, `PAYMENT_REQUIRED`·`RESERVED`는 그 사실만으로 자동 재시도하지 않습니다. `UNKNOWN`은 read-only 확인이 exact `NOT_FOUND`로 끝난 최초 시도만 위의 별도 identity로 한 번 재무장하며, 재무장된 시도도 `UNKNOWN`이면 확인은 계속하되 추가 예약 호출은 만들지 않습니다. 결제기한 후 보류 소실이 확인된 `reserve_once_before_payment` 작업은 기존대로 같은 episode fence를 유지하며, 최종 확인 marker 뒤 확정 비가용→새 행동 가능 관측으로 새 episode가 생긴 경우에만 한 번 재무장합니다. `FAILED`는 로그인 실패의 확정 근거가 아니므로 저장된 인증 상태를 덮지 않고 실패·감시 복귀 outbox와 채널 알림을 남깁니다. 호출 중 사용자가 취소했거나 여행 자체가 만료된 경우에는 terminal watch를 되살리지 않습니다.
13. 상태 변경과 알림 요청이 outbox에 기록되며 worker가 채널별로 독립 재시도합니다. SSE가 웹에 상태 변경을 전달하고 웹은 실제 API 상태를 다시 조회합니다. 단, 수 초 안에 끝날 수 있는 `watch.reservation_attempted`, `watch.reservation_result`, `watch.reservation_result_requires_manual_check`는 목록 재조회만 기다리지 않고 이벤트의 `created_at`과 실제 후보 문맥으로 같은 watch 알림을 직접 생성·교체합니다. 카드에는 현재 단계의 KST `HH:mm:ss`를 표시하며, 자동 예매 작업의 일반 좌석 발견 알림은 실제 attempt가 아직 만들어지지 않은 관측을 예약 시작으로 표현하지 않습니다.

`NOT_AVAILABLE`은 보류 없음뿐 아니라 예약 시점의 확정 비가용 근거입니다. 다음 행동 가능 관측은 `not-available-retry:<attempt-id>`로 경쟁 소실 보정 시도를 한 번 열 수 있지만, 이 보정 시도도 `NOT_AVAILABLE`이면 같은 연속 관측에서 연쇄 재호출하지 않습니다. 이후에는 기존처럼 확정 비가용 observation과 새 행동 가능 observation이 순서대로 생긴 episode가 필요합니다.

Migration `0020_reservation_reconciliation`은 실제 사용 credential version과 정규화한 confirmation outcome·source·observed/reconciled 시각을 예약 시도에 보존합니다. Migration `0021_bounded_reservation_reconciliation`은 확인 횟수와 `next_reconcile_at`을 추가해 `PAYMENT_REQUIRED`·`UNKNOWN` 시도의 빠른 read-only 확인을 최대 3회로 제한합니다. `UNKNOWN`이 계속 `INCONCLUSIVE`이면 5분·15분·60분 지연 확인을 이어 총 6회까지만 수행하며, 이전 버전에서 이미 3회를 소모하고 `next_reconcile_at`이 비어 있는 legacy 시도도 첫 지연 확인 대상으로 복구합니다. `PAYMENT_REQUIRED`는 기존 빠른 3회 계약을 유지합니다. Migration `0022_post_deadline_reconciliation`은 nullable `post_deadline_reconciled_at`과 조회 index를 추가합니다. 결제기한이 경과한 `PAYMENT_REQUIRED`는 일반 확인 횟수를 소모한 뒤 marker가 없을 때 공식 예약목록을 한 번 최종 확인하고 marker를 기록합니다. 모든 확인은 provider/account 실행 임대와 실제 예매에 사용한 것과 같은 credential generation을 다시 검증한 뒤 인증·예약 actor에서만 수행합니다. KORAIL은 현재 상세 화면에 exact 근거가 없으면 공식 예약목록의 열차번호·구간·서비스일·출도착시각·인원과 유일한 미결제 행동을 대조하고, 목록에 없는 좌석 등급을 확인된 것으로 만들지 않습니다. 정상 로드된 공식 목록에서 exact 대상이 0건인 경우만 `NOT_FOUND`이며 중복 일치·인증·차단·불확실 로드는 `INCONCLUSIVE`입니다. SRT reserve 호출은 반환 예약의 열차·구간·서비스일·출발시각·ticket 좌석 등급·seat count가 요청과 모두 일치할 때만 직접 `CONFIRMED_PAYMENT_REQUIRED`로 보존하고, 불명확한 결과는 공식 예약목록과 exact match합니다. 미래 결제기한이 있는 exact 보류는 handoff를 복구합니다. 최종 확인에서 exact 행이 남아 있어도 그 행의 공식 결제기한이 확인 시각 이하이면 행동 가능한 결제 보류가 끝난 것으로 처리하고, exact `NOT_FOUND`와 같은 정책별 종료·감시 복귀 경로를 사용합니다. 이전 버전이 이 과거 기한 exact 행에 marker만 기록한 레거시 건은 한 번의 호환성 정리 read를 추가로 허용하고 횟수를 올려 반복을 막습니다. 최초 `UNKNOWN` 시도의 확인이 exact `NOT_FOUND`이면 `reserve_once_before_payment` 작업을 `watching`으로 돌리고 `confirmed-absent-retry:<attempt.id>` identity로 같은 연속 `AVAILABLE` 구간에서 딱 한 번 재무장합니다. 재무장된 시도가 다시 `UNKNOWN`이면 확인 결과와 관계없이 추가 예약 호출을 만들지 않습니다. 결제 보류 종료 뒤에도 기존 episode를 직접 재무장하지 않고 marker 뒤 확정 비가용→새 행동 가능 관측으로 새 episode가 생긴 경우에만 다시 시도합니다. `AUTH_REQUIRED`·`PROVIDER_BLOCKED`·기한 없는 exact 행·`INCONCLUSIVE`는 fail-closed로 중단합니다. 이 경로는 예약·취소·결제 호출을 만들지 않습니다.

홈 UI는 `watch.reservation_result`의 `retryable`, `manual_check_required`, `retry_condition`을 그대로 해석합니다. `NOT_AVAILABLE`은 새 가용성 에피소드가 생기면 다시 한 번 시도할 수 있다는 감시 재개 안내를 표시합니다. `UNKNOWN`은 예약 재호출 없이 공식 내역을 확인하는 동안 수동 확인 필요 상태를 유지하고, 최초 시도의 exact `NOT_FOUND`가 저장되어 한 번 재무장된 경우에만 서버의 새 재시도 근거를 따릅니다. `FAILED`·`PROVIDER_BLOCKED`는 자동 재시도 없이 수동 확인이 필요하다는 안내를 표시합니다. 로그인 재검증 성공 뒤 최신 watch가 더 이상 `auth_required`가 아니면 과거 `AUTH_REQUIRED` toast와 계정 경고를 제거하고 새 검증 세대의 감시 재개 상태로 교체합니다.

`GET /api/v1/watches`의 후보별 `latest_reservation_attempt`는 원래 outcome과 함께 `confirmation_outcome`·`post_deadline_reconciled_at`을 반환합니다. 원래 outcome이 `PAYMENT_REQUIRED`이면서 최종 confirmation이 `NOT_FOUND`이거나, exact `CONFIRMED_PAYMENT_REQUIRED`의 저장된 공식 기한이 marker 시각 이하일 때만 read projection을 `retryable=true`, `retry_condition=new_availability_episode`로 만듭니다. 웹 JSON 경계도 이 근거들을 검증한 경우에만 보류 종료 상태를 만들고, 홈에는 `결제 보류 종료 확인 · 감시 계속`과 매진 후 재발견 조건을 표시합니다. 필드가 없거나 서로 모순되면 기존 결제 필요·수동 확인 상태를 유지해 보류 소실을 추정하지 않습니다.

outbox의 사람이 읽을 수 있는 중복 방지 키가 DB의 128자 저장 계약을 넘으면 앞부분과 전체 키의
SHA-256을 결합한 고정 길이 키로 정규화합니다. 조회와 저장에 같은 정규화를 적용하므로 긴 상태
전이 token도 중복 방지 의미를 유지하며, 알림 생성 실패로 좌석 관측 트랜잭션이 롤백되지 않습니다.

### 운영 상태 집계

관리자 전용 `GET /api/v1/operations/summary`는 최근 24시간의 `seat_observations`, `reservation_attempts`, `watch_transition_history`, 알림 outbox 최종 처리 결과와 현재 watch·provider circuit 상태를 PostgreSQL에서 집계합니다. 응답은 `Cache-Control: no-store`이며 설정의 `로그·진행 상태` 화면에서만 사용합니다. 집계용 시각 컬럼에는 별도 인덱스를 두고, 원문 provider 응답·오류 문자열·outbox payload·내부 ID·역명·열차번호·URL은 응답에 포함하지 않습니다.

인증된 `GET /api/v1/seat-status/status`는 이 PostgreSQL 집계와 별도로 Redis 좌석 조회
cooldown을 읽습니다. KORAIL browser와 SRT live 각각의 `ready|cooldown`, 허용 목록인
`provider_access_restricted|source_unavailable` 원인, 남은 초만 반환하며 응답은
`Cache-Control: no-store`입니다. 이 상태는 시간표 요청의 좌석 보강 source에만 적용되고,
worker의 `ProviderCircuit` 상태나 수동 재개 계약으로 해석하지 않습니다. 설정 화면도
`좌석 조회 제공원 상태`를 provider circuit과 별도 섹션으로 표시합니다.

좌석 관측 오류율은 같은 시간창의 `status=error` 관측 수를 전체 좌석 관측 수로 나눈 값입니다. 알림 최종 실패율은 `processed_at`이 시간창 안인 최종 `failed` 수를 `sent + failed`로 나눈 값이며 처리 대기 항목은 분모에서 제외합니다. 두 분모가 0이면 비율은 `null`이고 화면은 `기록 없음`으로 표시합니다. 이 값들은 서버·HTTP 오류율이 아닙니다.

### 서비스 파일 로그

`file_logging`은 root·uvicorn·Celery logger에 서비스 전용 JSONL handler를 추가합니다. Compose가 `APP_LOG_FILE`과 `APP_LOG_SERVICE`를 주입하여 API(`logs/api`), worker(`logs/worker`), scheduler(`logs/scheduler`), experimental worker(`logs/experimental-rail`), KORAIL sidecar(`logs/korail-browser-adapter`)를 분리합니다. 각 행은 UTC timestamp, service, level, logger, 정제된 message와 선택적인 error type만 가지며, process lock으로 같은 파일의 size rotation을 직렬화합니다. 기본 보존은 `current.log`와 `.1`부터 `.4`까지입니다.

애플리케이션 file handler는 stdout/stderr handler를 대체하지 않습니다. Compose의 Docker `local` logging driver(최대 10 MiB, 3개 파일)는 파일 JSONL과 별도의 보존 계층입니다. `log-init`만 root로 `logs/` 하위 디렉터리를 생성하고 app 계열은 UID:GID `100:101`, sidecar는 `1001:1001` 소유·`0750` directory로 초기화합니다. app 계열 서비스는 같은 UID를 사용하므로 현재 bind mount는 이들 사이의 운영상 파일 분리를 제공하지만 상호 write 차단을 보장하는 권한 경계는 아닙니다. sidecar directory는 별도 UID로 Linux 권한 분리됩니다.

KORAIL sidecar는 안정판 Google Chrome의 새 임시 프로필에 고정 desktop viewport `1440×1000`을 적용한다. 역 검색 결과와 출발일 dialog는 비동기 렌더링 뒤 하나의 안정된 가시 대상일 때만 선택한다. 중복·누락·불안정 대상은 `source_unavailable`으로 닫고 클릭하지 않는다. 시간 slider도 하나의 slider와 대상 hour를 검증한다. 소유가 확인된 활성 화살표가 있으면 그 control만 누르고, 공식 `.slideWrap`처럼 화살표가 없으면 유일한 가시 `.slick-list` viewport를 실제 CDP pointer로 드래그한다. 창 변화와 전환 완료 뒤 활성 목표만 누르며 모호·비활성·무진전이면 후보 시간을 추정하거나 다른 control을 누르지 않고 fail-closed한다.

현재 API 요청과 DB 집계 성공은 해당 요청 시점의 상태만 증명합니다. worker·scheduler heartbeat와 HTTP·프로세스 오류는 아직 영속 수집하지 않으므로 화면에서 `확인 불가`와 수집 한계를 표시하며 정상이나 0%로 추정하지 않습니다. Prometheus 프로필의 프로세스 메모리 지표와 이 관리자용 영속 집계는 서로 다른 관측 경계입니다.

### 공식 역 카탈로그 계약

`GET /api/v1/stations?provider=...`는 `source`, `retrieved_at`, `catalog_scope`, `provider_membership`, 설명과 `stations[{node_id,name,city_code,city_name}]`를 반환합니다. 응답 목록은 TAGO 원본 역과 [KORAIL 공개 역 안내](https://www.korail.com/public/st_info/station_data.json)의 역명 교집합이며 `catalog_scope=intercity_station_guide_intersection`, `provider_membership=not_verified_by_source`로 명시합니다. 요청의 `provider`는 문맥일 뿐 각 역의 KORAIL/SRT 소속이나 정차 여부를 뜻하지 않습니다.

migration `0007`의 `station_catalog_cache` 단일 행은 TAGO 원본 식별자 목록과 화면용 교집합, 두 출처의 수집 시각, 24시간 `refresh_after`를 PostgreSQL에 보존합니다. 원본 목록은 시간표 요청의 node ID·역명 검증을 hydrate하고, 화면에는 교집합만 반환합니다. 광운대·노량진·신도림·서빙고·왕십리·옥수는 화면 목록에서 제외하며 KORAIL 역 안내에 서울·수서·대전·부산 sentinel이 없으면 입력을 거부합니다.

API replica는 DB lease를 경쟁하고 lease owner와 유효 시간으로 늦은 기록을 fencing합니다. 신선한 스냅샷이 있으면 재시작 직후 상류를 호출하지 않으며, stale 스냅샷은 즉시 반환하고 한 replica가 백그라운드 갱신합니다. TAGO 페이지 수집, KORAIL 역 안내, 교집합 중 하나라도 비거나 손상되면 마지막 정상 스냅샷을 보존하고, 정상 스냅샷이 없을 때만 `503`으로 fail-closed합니다. 화면용 교집합 실패 시 원본 TAGO 목록으로 조용히 되돌아가지 않습니다.

서울역과 수서역을 운영사 소속이라고 고정 해석하지 않습니다. 2026년 교차운행처럼 실제 운행은 바뀔 수 있으므로 공용 역 카탈로그와 날짜·구간 시간표의 의미를 분리합니다. SRT 시간표·좌석 source는 실행 시점에만 SRTrain 2.6.7 roster로 출발·도착역 조합을 검증하고, roster 밖 조합은 상류 호출 전에 빈 결과·`unsupported_route`로 닫습니다. 이는 해당 역에 SRT가 운행하지 않는다는 공식 판정이 아니라 현재 서버 source capability 경계입니다. roster가 공식 연동 명세로 교체되면 같은 경계에서 갱신합니다.

### 운영사 상주 런타임과 운행·예약 확인 경계

API lifespan은 station catalog preload와 별도로 상주 provider session manager를 시작합니다. manager는 DB의 모든 활성 철도 계정을 읽어 KORAIL·SRT sidecar에 startup prewarm을 한 번 요청하며, 이전 실행이 `auth_required` 또는 `provider_blocked`로 끝났더라도 복구 대상에서 제외하지 않습니다. 외부 I/O 중에는 DB transaction을 유지하지 않고, 성공한 prewarm만 credential generation CAS로 영속 `authenticated` 상태를 갱신한 뒤 같은 transaction에서 그 로그인 검증 세대보다 오래된 인증·운영사 제한 작업을 감시 상태로 복구합니다. 이후 30초 maintenance는 새 `(provider, credential_version, updated_at)` 복구 revision을 provider별 한 번 처리합니다. 같은 generation의 인증 actor가 이미 `ready`이고 로컬 재사용 가능하면 외부 로그인 없이 DB와 작업 상태만 동기화하고, 그렇지 않을 때만 bounded prewarm을 실행합니다. 같은 revision의 실패를 반복하지 않고 provider별 최신 revision만 메모리에 보존하며, tick 장애는 원문 없이 기록한 뒤 다음 tick을 계속합니다. `PROVIDER_BLOCKED` 예약 attempt와 availability episode fence는 삭제하지 않으므로 세션 복구 자체가 같은 좌석을 다시 예약하지 않습니다. 동시 credential 교체로 generation이 달라졌거나 prewarm이 실패·차단·인증 필요로 끝나면 기존 성공 시각과 영속 인증 상태를 덮지 않습니다. 각 인증 actor의 credential generation, 생성·검증·마지막 사용 후 경과 시간, 현재 프로세스의 남은 로컬 재사용 시간과 상태는 secret-free snapshot으로만 조회합니다. 이 telemetry는 운영사가 보장한 세션 수명이나 공개 고정 TTL이 아니며, 프로세스 재시작 시 복구되는 cookie 저장소도 아닙니다. 원문 ID·비밀번호·cookie·storage state·fingerprint는 snapshot과 prewarm registry에 들어가지 않습니다.

KORAIL read-only 시간표 검색은 인증 actor가 READY여도 별도 ephemeral browser lease를 획득하고, 성공한 공개 검색의 HTTP replay pool도 인증 session과 분리합니다. SRT sidecar도 `source`와 `executor`를 별도 객체로 보유합니다. 따라서 background 검색이 인증 actor의 generation·cookie를 빌려 쓰거나 폐기하지 않으며, 인증 actor는 로그인 검증·단발 예약·공식 보류 확인만 직렬화합니다. KORAIL warm 인증 actor는 credential generation·fingerprint와 로컬 재사용 기한이 일치할 때만 같은 Chromium context의 새 탭에서 strict 결과 URL로 바로 이동해 중복 공개 검색 화면 왕복을 줄입니다. 새 탭 격리와 exact target·좌석·단발 click latch는 유지하며 read-only HTTP replay나 예약 POST 재전송은 사용하지 않습니다.

Migration `0020_reservation_reconciliation`은 `ReservationAttempt`에 실제 호출 credential generation과 정규화한 공식 확인 outcome·source·observed time·reconciled time을 추가합니다. `0021`은 일반 확인 횟수와 다음 확인 시각을, `0022`는 기한 경과 후 최종 확인 marker와 index를 추가합니다. worker는 provider/account 실행 임대와 fencing 아래 같은 credential generation의 인증 actor만 사용합니다. `PAYMENT_REQUIRED`는 빠른 확인 최대 3회와 marker 없는 결제기한 경과 보류의 최종 확인 1회를 유지하고, 이전 버전이 과거 기한 exact 행에 marker만 남긴 경우 호환성 정리 read를 한 번 허용합니다. `UNKNOWN + INCONCLUSIVE`는 빠른 3회 뒤 5분·15분·60분 지연 확인을 더해 총 6회까지 읽기 전용으로 확인합니다. KORAIL은 동일 인증 session의 현재 상세 화면, SRT는 예약 목록을 정확한 열차·구간·서비스일·출발시각·좌석 등급에 맞춥니다. 정확히 하나의 미결제 보류와 미래 결제기한이 확인된 경우에만 `payment_required` handoff를 복구합니다. `NOT_FOUND` 또는 exact 행의 공식 기한이 최종 확인 시각 이하인 결과는 정책별 종료·감시 복귀로 처리하지만 기존 episode를 직접 재무장하지 않습니다. `INCONCLUSIVE`·기한 없는 exact 행·인증·차단·확인 호출 실패는 상태를 추정하거나 episode를 재무장하지 않습니다. 최초 `UNKNOWN`의 exact `NOT_FOUND`만 같은 연속 가용 구간에서 `confirmed-absent-retry:<attempt.id>`로 한 번 재무장하며, 재무장된 `UNKNOWN`은 더 이상 예약 호출하지 않습니다. reconciliation task는 예매·취소·결제 동작을 호출하지 않습니다.

Migration `0019_candidate_operational_state`는 `scheduled_departure_at`, `estimated_departure_at`, `actual_departure_at`, `delay_minutes`, 운행·예매창 상태와 provenance를 분리합니다. KORAIL `BrowserTrainSnapshot`은 정확한 `N분 지연 예상` 문구를, HTTP replay는 `h_expn_dpt_dlay_tnum`을 `delay_minutes`로 정규화합니다. 이 투영은 scheduled identity를 보존하고 `estimated_departure_at`만 갱신합니다. `sold_out`은 좌석 재고 관측이므로 예매창 `closed`로 승격하지 않습니다. fresh terminal provenance가 있는 출발·취소·예매창 종료만 즉시 만료시킵니다. 이 판정 전에 `Watch.travel_date`로 후보를 제외하지 않으므로 KST 자정 직전 다음 서비스일 열차도 신선한 공식 `closed` 근거를 즉시 반영합니다. fresh 지연·탑승·열린 예매창은 예정시각 경과보다 우선합니다. 상태가 unknown이거나 terminal 근거가 stale이면 예정 출발 뒤 최대 15분 동안만 제한 재평가하고, 그때까지 신선한 계속 운행 근거가 없으면 절대 horizon에서 fail-closed 만료합니다. 이는 앞선 `departure_at` 기반 호환 identity·시간창 설명보다 우선하는 현재 만료 계약입니다.

## 상태 계약

공식 KORAIL·SRT의 기본 흐름은 작업 생성 직후 start 요청을 거쳐 `draft → scheduled`가 됩니다. UI의 자동 시작 자체는 조회 capability를 바꾸지 않으며, 좌석 관측 근거가 없으면 `unknown`과 안전한 미관측 사유를 표시합니다. 사용자 시간표 요청에서 `SrtLiveSeatSource`는 계정 없는 검색 한 번을, KORAIL Chromium source는 공식 페이지의 렌더된 결과 판독 한 번을 수행합니다. 같은 source를 worker에 연결하는 것은 운영사별 3중 opt-in이 모두 켜진 경우로 제한합니다. 활성화 뒤 기존 `scheduled|seat_found|official_waitlist + next_check_at=null` 작업은 worker가 한 번 재무장하고, due 작업은 DB 실행 임대를 얻은 뒤 관측합니다. `sold_out`은 `watching`과 다음 관측 시각을 유지하고, `available`·`limited`·`standing_plus_seat`는 `seat_found`, `waitlist_available`은 `official_waitlist`로 전이합니다. `seat_found`와 `official_waitlist`는 종착 상태가 아니며 동일한 상태에서도 다음 관측을 예약합니다. 주기 결과에 따라 두 상태 사이를 이동하고, 모든 후보가 확정적인 비행동 상태로 바뀌면 `watching`으로 복귀합니다. 오류·미관측만으로 기존 발견 상태를 강등하지 않으며 같은 상태 반복은 전이·알림 outbox를 만들지 않습니다. 작업 감시 기한은 마지막 관측 후보의 출발시각과 사용자가 지정한 시간창 종료 중 이른 시점입니다. KORAIL worker는 공식 UI가 요구하는 후보 출발시의 시작 시간을 사용하고 같은 구간·서비스일·인원·시작 시각 후보를 singleflight로 합친 뒤 열차번호·KST 출발시각·좌석 등급을 다시 exact match합니다. Browser Companion snapshot은 기존 데이터 호환 범위에서만 읽습니다. 시간표 stale response를 거르는 웹 query key에는 provider·구간·날짜·시간창과 승객 수를 함께 넣습니다.

사용자 상태 전이와 수정은 commit 직전에 watch 행을 `FOR UPDATE`와 `populate_existing`으로 다시 읽습니다. 먼저 읽은 stale ORM 상태로 전이하거나 다른 수정 뒤 잘못된 dedupe key를 만드는 경합을 막습니다. 생성 idempotency는 unique 충돌이 outbox autoflush에서 발생해도 전체 새 작업을 rollback하고 먼저 생성된 resource를 다시 읽습니다. 동일 후보·동일 가용성 에피소드의 예약 횟수와 시도 순서는 이 애플리케이션 잠금과 별개로 `ReservationAttempt(candidate_id, episode_key)` 및 `(candidate_id, attempt_sequence)` DB unique constraint가 최종 보장합니다.

예약 동작은 4중 실행 gate, 활성 철도 계정, 작업별 `reserve_once_before_payment`를 모두 만족한 provider에서만 `seat_found → reserving → payment_required`로 진행할 수 있습니다. 홈의 활동 중 티켓은 `PATCH /watches/{id}`로 `notify_only`와 `reserve_once_before_payment`만 전환할 수 있고, 같은 상태에서 시간창·인원·후보 같은 여정 계약은 수정하지 못합니다. 화면 라벨은 작업 전체 1회로 오해되는 표현 대신 정책 범위를 드러내는 `좌석 재발견마다 자동 예매`를 사용하고, 보조 문구로 `가용성 에피소드당 1회 · 결제 전 중단`을 표시합니다. worker는 `ReservationAttempt`를 만들기 직전 계정 행을 잠가 최신 인증 상태를 다시 확인합니다. 정책 PATCH와 교차한 이전 `GET /watches` snapshot은 프런트 mutation epoch가 폐기하고 새 목록을 요청합니다. 이미 만들어진 `ReservationAttempt`와 episode fence는 정책을 껐다 켜도 삭제하지 않으며 정책 변경 자체는 새 시도 근거가 아닙니다. 후보 API는 최신 attempt의 outcome·시작/종료 시각·재시도 가능 여부·수동 확인 여부·재시도 조건을 함께 반환하고 홈 행은 이를 일시적인 toast와 별도로 계속 표시합니다. KORAIL은 관리형 Pydoll 세션, SRT는 프로세스 내 로그인 세션을 credential version별로 재사용하되 cookie·storage state는 DB·Redis·로그에 저장하지 않습니다. 예약 결과가 확정적으로 `AUTH_REQUIRED`여서 최신 transition reason이 `reservation_auth_required`인 작업은 이후 같은 운영사 로그인 재검증이 성공하고 `last_authenticated_at`이 이전 attempt 종료보다 새로울 때만 `scheduled`로 재개합니다. 후보는 다시 관측 가능 상태로 돌리고 그 로그인 검증 세대에서 한 번만 재무장하며, 기존 `ReservationAttempt` 행은 감사 이력으로 보존합니다. 결제·카드 입력·CVC·결제 인증은 호출하지 않습니다.

provider 해석은 사용자 시간표 조회와 worker 실행 역할을 분리합니다. API·작업 생성 서비스는
KORAIL Chromium·SRT live source를 주 시간표 경로로 사용하고, 주 경로를 사용할 수 없을 때만
`get_timetable_provider()`의 TAGO adapter와 공식 인계 URL을 fallback으로 사용합니다. worker는
`get_execution_provider()`만 사용해 좌석 관측·예약 capability를 판정합니다. 개발 mock은 두
역할을 모두 제공합니다. KORAIL·SRT 실행 registry는 각 세 환경변수의 교집합으로만 관측을
엽니다. 어느 gate든 꺼져 있으면 해당 adapter는 fail-closed입니다. 사용자 조회의 live 시간표·좌석
성공 하나만으로 worker capability가 승격되지는 않습니다. 기존 `get_provider()`는 시간표 registry의
호환 별칭일 뿐 실행 권한을 뜻하지 않습니다.

`provider_contracts.py`는 capability source, timetable, observation, reservation, confirmation,
lifecycle 역할을 구조적 Protocol로 정의합니다. request application은 `TimetableProvider`, worker와
각 application은 필요한 역할 또는 composition 계약만 의존하며 concrete registry·KORAIL/SRT 실행
모듈을 역참조하지 않습니다. `ProviderUnavailable`과 `RouteValidationError`는 이 계약 모듈의 단일
객체이고 `providers.py`는 기존 호출자를 위해 같은 객체를 다시 export합니다. 역할 view를 나눠도
runtime 객체를 역할별로 새로 만들지 않습니다. 하나의 task-scoped `ExecutionProvider` 인스턴스가
재무장·관찰·예약·정합화와 drain·close를 함께 수행해 인증 actor·cache·event loop 수명주기를
보존합니다. `RailProviderAdapter`와 concrete 구현·registry의 물리 분리는 호환성을 고정한 후의
별도 단계입니다.

외부 provider 관측 그룹은 `provider_execution_leases`의 `provider + account_scope` 복합 키로
직렬화합니다. 획득할 때마다 fencing token을 증가시키고, worker는 공식 호출 직전과 관측 결과
기록 직전에 현재 소유자·token·만료 시각을 다시 검증합니다. 임대가 만료되거나 다른 worker가
재획득한 뒤 돌아온 늦은 결과는 저장하지 않으며 `finally`에서 자신의 임대만 해제합니다. SRT의
계정 없는 실행 scope는 `anonymous/public`이고, 이 임대는 provider circuit·Redis cooldown·
후보별 DB 고유 제약을 대체하지 않고 함께 적용됩니다.

관찰 그룹의 use case는 `observations/group_application.py`가 소유합니다. 이 application은 작업
준비·source cooldown 연기·동일 요청 병합·provider 오류 정규화·작업별 관찰 저장과 상태 요약·
가용성 episode winner 선택을 수행하고 target만 받는 port로 예약 실행을 위임합니다. worker는
concrete provider adapter와 실행 임대의 수명주기만 조립합니다. 외부 provider의 prepare·연기·회로
확인·관찰 저장·회로 반영 transaction은 실행 임대 행을 먼저 잠근 뒤 watch·candidate·circuit을
잠급니다. 여러 watch를 연기할 때는 ID 순서로 `FOR UPDATE`하여 잠금 순서를 결정적으로 유지합니다.
SQLite 회귀와 PostgreSQL SQL compile로 이 계약을 확인했지만, 실제 두 PostgreSQL session의
takeover 대기와 다중 worker 교착 부재는 별도 운영·CI 검증 대상입니다.

SRT background query key는 정규화한 출발역·도착역·KST 서비스일·인원으로 구성합니다. 같은
키의 후보들은 `00:00–23:59` 하루 검색 하나를 singleflight와 TTL cache로 공유하므로 서로 다른
열차를 감시해도 같은 주기의 상류 호출을 중복하지 않습니다. 공유 결과를 후보에 기록할 때는
열차번호·서비스일·출발시각·좌석 등급 exact match를 다시 적용합니다.

Celery task마다 `asyncio.run()`이 별도 event loop를 만들기 때문에 SRT source·Redis client를
프로세스 전역 singleton으로 두지 않습니다. worker는 한 task 안에서 provider별 실행 adapter를
하나만 만들고 SRT 재무장과 여러 dedupe 그룹에 공유한 뒤 task 끝에 닫습니다. 동기 SRTrain 호출은
`asyncio.to_thread` task로 추적하며 asyncio timeout이 먼저 반환돼도 실제 thread가 끝날 때까지
provider semaphore를 보유합니다. 각 그룹은 pending 호출을 drain한 다음에만 provider/account
DB 임대를 해제하고, 마지막 adapter close가 Redis client를 정리합니다. 따라서 닫힌 loop에 묶인
client 재사용과 임대 밖의 늦은 provider 호출을 함께 방지합니다.

SRT 실행 adapter의 shared Redis cooldown도 외부 provider 실행 임대 안에서 판정합니다. worker는
임대를 획득한 뒤 preflight를 확인하고, 활성 TTL이 있으면 현재 fencing 소유권을 다시 검증한 뒤
그 그룹의 `next_check_at`과 `cooldown_until`을 만료 시각으로 함께 미룹니다. 이 경로는 upstream을
호출하거나 오류 `SeatObservation`을 만들지 않습니다. preflight 뒤 실제 observe 사이에 cooldown이
열린 경우에도 오류 결과를 저장하기 전에 source를 다시 확인해 같은 연기 경로로 보냅니다. TTL이
사라진 다음 due cycle은 stale `cooldown_until`을 제거하고 정상 관측·상태 전이를 재개합니다.

결정적 full-stack E2E는 `compose.fullstack-e2e.yml`의 임시 stack을 사용합니다. API·worker·
KORAIL Chromium sidecar와 fixture는 외부 egress가 없는 internal `app`·`data` network에 두고,
proxy만 별도 host network로 Playwright에 노출합니다. test 환경에서 정확한 내부 URL만 TAGO·역
안내·SRT 실행 fixture와 KORAIL HTML page로 허용하며 다른 환경·host·path는 설정 검증에서
거부합니다. sidecar와 page는 `app`에만 연결되고 `browser-egress`를 사용하지 않습니다.

KORAIL fixture는 좌석 snapshot API 응답을 직접 제공하지 않습니다. 실제 sidecar endpoint의
기본 Pydoll 엔진이 새 임시 Chromium 프로필과 WebDriver 없이 CDP로 통신합니다. 고정 HTML page의
보이는 컨트롤을 조작하고 렌더된 결과 DOM을 정규화하며, 같은 테스트 이미지에서 명시적
`playwright_direct_cdp` 엔진의 직접 Chromium lifecycle과 raw mouse 제출 회귀도 검증합니다. API는 구간·날짜·인원·열차번호·출발시각·
좌석 등급 exact match를 다시 적용해 KORAIL `official_provider` 상태와 공식 예매 CTA를 만듭니다.
격리 환경에서는 KORAIL 좌석 감시 3중 opt-in을 켜 매진 특실 대기 1건을 evidence-bound로
생성·시작하고, SRT 좌석 등급 3건을 더해 총 4건을 검증합니다. 실제 worker·scheduler가 KORAIL
`watching`과 SRT `watching`·`seat_found`·`official_waitlist`로 전이하는지 확인합니다. DB verifier는
좌석 관측, actionable 전이의 observation
연결, `anonymous/public` 실행 임대의 fencing·해제, 예약 시도 0건과 알림 outbox 생성·재시도를
검증합니다. 이 전체 경계의 실제 KORAIL·SRT provider 호출은 0건이며, fixture의
`official_provider` 표시는 실제 공식 좌석 snapshot 성공이나 운영 승인 증거가 아닙니다.

### 좌석 등급별 provider 계약

SRT 서버 좌석 source와 KORAIL 서버 Chromium 어댑터는 최초 `GET /timetables`와 미관측 운영사만 다시 확인하는 관리자 `POST /seat-status/refresh`의 같은 주 UI 경계에 연결됩니다. KORAIL 어댑터는 `experimental-rail`과 명시적 활성화 값이 모두 있을 때만 sidecar를 호출합니다. 기본 `pydoll` 엔진은 공식 역 자산을 24시간 TTL·singleflight로 읽고, 출발·도착을 동일 `stn_cd ↔ stn_nm` 레코드로 확인하면 편도·직통·성인 1명·일반석·KTX·KORAIL-only의 고정 25키 `/ticket/search/list` URL을 만듭니다. cold 조회는 navigation 전에 network capture를 시작하고 결과 화면을 한 번 직접 열어 역 선택 2회·날짜/시간 picker·조회 버튼을 생략합니다. 역 map을 받거나 일치시키지 못하면 업무 요청 전 기존 `https://www.korail.com/ticket/search/general`의 가시 컨트롤 경로를 사용합니다. 직접 navigation 뒤 timeout·보호·불명확 DOM에는 같은 호출에서 UI fallback을 수행하지 않습니다. 성공한 조회에서만 공식 동일 origin의 업무 POST multipart template과 해당 Pydoll context의 cookie를 구간별 메모리 lease로 넘기고 Chromium을 닫습니다. 최대 4개 bounded LRU pool은 서로 다른 활성 구간을 보존하고, 같은 구간의 후속 조회는 해당 lease 안에서 검증된 `txtGoAbrdDt`와 `txtGoHour`의 기존 byte span만 같은 길이로 바꿔 전송합니다. multipart를 재직렬화하거나 다른 필드·동적 값을 만들지 않습니다. sidecar의 전역 lock은 유지돼 pool의 여러 lease가 동시에 요청을 보내지 않습니다. 로그인 검증·예매·종료 전에는 pool 전체를 정리해 인증 context와 read-only cookie가 섞이지 않게 합니다. API는 직접 navigation·UI DOM·replay 결과 모두에서 구간·날짜·인원·열차번호·출발시각·일반실·특실을 다시 exact match하고, 미활성·불일치·불완전 응답은 `unknown`으로 fail-closed합니다. 인증 예약 actor도 로그인 확인 뒤 같은 strict 검색 navigation만 사용해 최초 입력을 줄이지만 읽기 전용 actor의 DOM·cookie·replay를 넘겨받지 않고 기존 후보·에피소드 fence와 결제 전 중단을 유지합니다. `KORAIL_BROWSER_ENGINE=playwright_direct_cdp`는 기존 직접 Chromium 경로를 유지합니다.

KORAIL 보호 판정에서 업무 document의 403과 본문 보호 marker는 즉시 중단·cooldown 대상입니다.
font·analytics 등 비업무 subresource의 독립 403은 가시 결과 DOM이 유효한 경우 보호 성공 근거로
사용하지 않습니다. 결과 목록은 KTX·KTX-산천·KTX-청룡 계열만 파싱하며, 같은 공식 DOM에 섞인
무궁화·ITX 등 비-KTX 행은 건너뜁니다. 따라서 별도 행 구조가 exact-match KTX 관측을 전체
실패시키지 않지만, 선택한 KTX 행 자체의 identity·DOM 계약 오류는 계속 fail-closed합니다.
HTTP replay도 `https://www.korail.com`의 기본 443 origin과 캡처된 `/web_s/` path만 허용하고
userinfo·fragment·redirect를 거부합니다. 브라우저가 만든 opaque query는 해석하거나 변경하지 않고
원문 URL의 일부로 메모리 lease에서만 보존합니다. 응답은 요청별 timeout, 2 MiB 상한, 최대 20페이지,
단조 증가하는 출발시각 cursor를 적용합니다. lease는 기본 최대 300초·20회로 제한하고 구간별
최대 4개 bounded LRU pool에서 재사용합니다. 수명·횟수 만료와 선택 구간 오류는 해당 lease만,
용량 초과는 가장 오래 사용하지 않은 lease만 폐기하고 cold UI로 시작합니다. 401, 동일
origin 로그인 경로 redirect, 명시적인 로그인 HTML로 session 만료가 확인된 경우에만 lease를
폐기한 뒤 같은 read-only 요청에서 cold 초기화를 최대 한 번 허용합니다. cookie 누락,
capture·response schema·cursor 불일치와 그 밖의 4xx는 같은 요청에서 재시도하지 않고
fail-closed합니다. 이미 403·429·보호 marker가 확인된 경우에도 cold retry 없이 기존
protection·rate-limit cooldown으로 전환합니다.

Browser Companion의 5분 pairing, 설치별 자격증명, 30초 1회 challenge와 2분 snapshot 계약은 기존 설치·저장 데이터의 호환을 위해 남아 있습니다. 새 대기 주 UI는 확장 설치나 snapshot 가져오기를 요구하지 않으며 `official_page_browser_companion` 값도 신규 주 경로로 생성하지 않습니다. 레거시 snapshot도 freshness와 identity exact match를 통과한 미관측 좌석에만 합치고 worker·예약·outbox에는 연결하지 않습니다.

API의 `SeatClass`는 `standard`, `first`, `infant`, `free`, `waitlist`, `any`를 정의합니다. 열차 결과의 `seat_classes`는 `any`를 제외한 등급마다 `status`, `provenance`, `actions`를 가집니다. 현재 웹의 선택 화면은 일반실과 특실을 독립 패널로 렌더링하며, `any`는 작업 생성 시 좌석 무관 선택에만 사용합니다.

- status는 `unavailable`, `unknown`, `available`, `limited`, `standing_plus_seat`, `not_enough_seats`, `sold_out`, `waitlist_available`, `reservation_completed`, `not_offered`, `departed`, `out_of_service`, `stale`, `error`를 표현합니다.
- provenance는 `not_observed`, `official_provider`, `official_page_browser_companion`, `user_confirmed_official_page`, `mock` 중 하나입니다. SRT source와 KORAIL 서버 Chromium 주 UI 경로는 `official_provider`를 사용하고, `official_page_browser_companion`은 레거시 snapshot 호환에만 사용합니다. 관측된 좌석 상태에는 source와 timezone이 있는 `observed_at`이 필요하고 공식 페이지 기반 값에는 `fresh_until`도 필수입니다.
- actions는 `official_check`, `add_to_watch`, `official_waitlist`, `retry_provider`로 제한합니다. 외부 행동은 HTTPS이면서 KORAIL은 `korail.com`·`letskorail.com`, SRT는 `srail.kr` 소유 도메인이어야 하고 내부 행동에는 URL을 허용하지 않습니다. 시간표 좌석 보강 뒤 API는 실행 provider의 `seat_monitoring`을 다시 확인해 `false`이면 `add_to_watch`와 registration evidence를 제거합니다. provider가 빈 actions를 반환하면 웹도 빈 배열을 보존해 허용되지 않은 CTA를 새로 만들지 않습니다.
- 좌석 등급별 운임은 `fare`와 `fare_currency=KRW`로 선택적으로 전달합니다. 관측 근거가 없는 좌석별 운임은 허용하지 않으며 TAGO의 열차 단위 `adult_fare`를 특실 운임으로 복제하지 않습니다.
- TAGO 공식 시간표 자체는 좌석 관측 근거가 아니므로 일반실·특실을 각각 `status=unknown`, `provenance.kind=not_observed`로 반환합니다. API와 웹은 미관측 사유를 `source_not_configured`, `provider_access_restricted`, `unsupported_route`, `passenger_count_not_supported`, `departure_window_elapsed`, `no_exact_match`, `source_unavailable`로 구분합니다. 활성화된 KORAIL·SRT 좌석 source에서 출발·도착역·KST 서비스 날짜·인원·정규화 열차번호·UTC 출발시각이 정확히 일치할 때만 해당 좌석을 `official_provider` 관측값으로 교체합니다. 지원하지 않는 구간·인원, 이미 지난 시간창, 일치 행 누락, 상류 장애는 해당 사유와 함께 `unknown`을 유지합니다.
- mock은 화면과 계약 테스트를 위한 provenance입니다. mock의 예약 가능·매진 등은 실제 철도 상태로 승격하지 않습니다.
- SRT source는 `srtrain-2.6.7-accountless`로 고정합니다. 구형 `korail2-0.4.0-accountless` 구현은 보호 응답 분류 회귀용으로 남지만 운영 기본에서 호출하지 않습니다. 실험 KORAIL browser source는 API와 별도 sidecar로 격리합니다. 기본 Pydoll cold 초기화와 명시적 `playwright_direct_cdp` 엔진은 격리 Chromium의 새 비영속 프로필에서 공식 화면의 접근 가능한 컨트롤만 조작합니다. Pydoll 시간 picker는 Slick 전환 중 같은 시각의 비활성 clone과 활성 원본이 함께 보이거나 발견 뒤 속성이 바뀔 수 있으므로, 숨은 24시간 catalog는 구조 검증에만 쓰고 같은 표시 문구의 모든 가시 후보를 매 반복 live DOM에서 다시 판정합니다. 날짜를 바꾼 직후 시간 disabled 상태가 이전 서비스일 기준으로 남을 수 있으므로 날짜가 다르면 `날짜 선택 → 적용 → 날짜 exact readback → picker 재개방 → 시간 선택`으로 분리합니다. 목표 시각이 현재 창 밖이면 시간 전용 화살표, `.slick-list`의 실제 CDP drag, 포커스 가능한 시간 viewport의 표준 좌우 키 순으로 제한된 사용자 입력을 시도하며, 각 단계는 시간 창이 요청 방향으로 이동했을 때만 성공으로 인정합니다. CSS 전환이 끝난 뒤 실제 활성 목표만 누르고 최종 `#startDate` 날짜·시각과 성인 1명을 exact readback하며, 여러 실제 활성 시각·기존 시각 불일치·창 무진전·속성 판독 실패는 fail-closed합니다. cold UI 검색은 한 번만 제출하고 결과 화면의 `더보기`를 최대 19회만 누릅니다. 성공한 Pydoll 요청에서 만든 HTTP replay lease는 같은 구간의 날짜·시작시각 변경에만 쓰며, 응답의 KTX 계열 구간·날짜·열차번호·출발시각과 일반실·특실 상태를 동일한 sidecar schema로 정규화합니다. source 응답, 원문 HTML, multipart template, cookie jar, 동적 path는 DB·파일·로그·지표에 저장하지 않고 lease 종료와 함께 폐기합니다. API와 sidecar는 32바이트 이상 내부 token으로 인증하며 HTTP client는 환경 proxy와 redirect를 신뢰하지 않습니다. 429는 rate-limit cooldown, 403·`-1405`·`-8002`·`-8003`·`macro_err1`·CAPTCHA·NetFUNNEL은 기본 5분의 별도 protection cooldown으로 분리합니다.

화면의 압축된 상태 chip은 관측 provenance가 유효할 때만 세부 status를 `예매 가능`, `매진`, `예약대기 가능`, `예매 불가` 중 하나로 묶습니다. `not_observed`이거나 provenance 계약이 불완전하면 status 문자열과 관계없이 실제 좌석 상태를 표시하지 않고, 미관측 사유에 맞는 `출처 설정 필요`, `조회 제한`, `미지원 구간`, `1인 조회만 지원`, `출발 시간 경과`, `일치 열차 없음`, `좌석 조회 실패` 안내로 구분합니다.

2026-07-29의 KORAIL 정상 단발 확인에서는 대전(`0010`)→서울(`0001`), 2026-07-30 12:00 조건의 공식 화면이 `CODE -8003`, 동일 정상 요청 응답이 `macro_err1`과 열차 0건을 반환했습니다. 이후 재시도·요청 변형·우회는 하지 않았습니다. 따라서 해당 결과는 `provider_access_restricted`인 미관측 근거일 뿐 실제 좌석 매진이나 예매 가능 상태가 아니며, 당시 KORAIL capability도 `false`였습니다.

2026-07-30의 별도 사용자 브라우저에서는 공식 승차권 검색 화면의 정상 결과 목록과 일반실·특실·매진 문구를 읽기 전용으로 확인했고 `-8003`·`macro_err1`은 보이지 않았습니다. 이 관찰 뒤 서버의 계정 없는 KORAIL 직접 source를 제거했고, 현재 주 UI는 서버 관리형 Chromium 어댑터의 보이는 UI 단발 조작 경로를 사용합니다. Browser Companion snapshot 경로는 이후 주 UI에서 제거되어 레거시 호환으로만 남았습니다. 로컬 fixture 기반 browser adapter 테스트 48건과 fail-closed 계약은 검증했습니다.

2026-07-31 동일한 대전→서울, 2026-08-01, 03:00–08:00 단발 비교에서 Windows PoC의 Pydoll은 전체 10행(KTX 계열 8행, ITX 1행, 무궁화 1행)을 읽었습니다. Linux sidecar의 기본 Pydoll 엔진은 HTTP 200과 KTX 계열 8행을 반환했고 `available`, `limited`, `sold_out` 좌석 상태를 정규화했습니다. 같은 조건의 기존 `playwright_direct_cdp` 엔진은 결과 단계에서 `marker_code_8003`을 감지해 내부 HTTP 423 `provider_access_restricted`로 닫혔습니다. 당시 인증된 새 대기 웹 UI에서도 KTX 계열 8행이 exact overlay됐고 2행은 미관측으로 닫혔습니다. 이후 `더보기` 확장을 적용한 최신 재검증에서는 시간표 10행의 일반실·특실 20개 상태가 모두 공식 관측으로 overlay되어 `예매 가능`·`매진 임박`·`매진` 및 상태별 CTA로 표시됐습니다. 이 결과는 요청 시점 단발 조회의 증거이며 background `seat_monitoring`과 실제 계정의 에피소드당 1회 자동 예매 장시간 안정성은 아직 별도 운영 검증이 필요합니다.

같은 날짜의 대전→서울, 2026-08-01, 12:00–18:00 입력에서 보였던 58개 미확인은 API DTO 변환이 아니라 Pydoll의 화면 밖 시간 이동 실패였습니다. `00:00` PoC는 첫 시간 창이라 이 분기를 실행하지 않습니다. 숨은 시간 링크 직접 클릭을 제거하고 가시 시간 viewport의 실제 CDP drag, Slick 전환 완료, 활성 12시 pointer click과 `#startDate` exact readback을 적용한 뒤 sidecar 단발 요청은 HTTP 200, KTX 계열 28행·좌석 등급 56개를 반환했습니다. API와 sidecar를 재생성한 뒤 최종 재검증한 실시간 상태는 `limited` 7개, `sold_out` 30개, `standing_plus_seat` 19개였으며 API가 소비하는 `BrowserSeatSearchResult` 계약으로 검증했습니다. 이번 단발은 로그인된 웹 overlay나 background 장시간 안정성의 완료 근거로 확대하지 않습니다.

같은 날 새 headed Playwright 세션에서 KORAIL 공식 홈을 열고 사용자에게 보이는 `열차조회`를 정상 클릭한 단발 확인은 결과 단계에서 즉시 `CODE -8003`으로 중단됐습니다. 저장 상태, User-Agent, header, stealth, proxy를 변경하지 않았고 추가 재시도도 하지 않았습니다. 이 결과는 정상 브라우저 엔진 자체가 서버 자동 조회의 안정성이나 허가를 보장하지 않는다는 운영 증거이며, 원본 요청 식별자와 응답 원문은 문서·로그에 보존하지 않습니다.

`timetable_seat_evidence`는 시간표에서 대기 추가가 허용된 순간의 표시 근거를 보존하는 append-only snapshot입니다. `unknown`·`not_observed`·mock·`add_to_watch`가 없는 좌석은 snapshot ID를 만들지 않으며, 발급된 행은 source·`observed_at`과 등록 허용 여부를 함께 고정합니다. migration 전 행은 허용되지 않은 것으로 취급합니다. 사용자 직접 확인값은 고정 source·freshness 제약을 추가로 만족해야 합니다. `watch_candidates`의 외래키를 통해 홈이 같은 좌석 등급과 근거를 읽지만 이 테이블을 `seat_observations`·상태 전이·알림·예약 입력으로 사용하지 않습니다. 따라서 등록 이후 유효창이 지나도 감사 표시는 남고, background 좌석 관측 capability는 바뀌지 않습니다.

열차 단위의 기존 `availability`는 호환용 요약 필드이고, 좌석 선택·CTA·대기 작업 분리의 기준은 `seat_classes`입니다.

## 공식 채널 인계

KORAIL·SRT 공식 예매 URL은 반복 폴링 대상이 아니라 사용자에게 넘기는 HTTPS 진입점입니다. 시간표 요청 시의 계정 없는 좌석 source, 조건 선입력 검색 주소, 예약·결제 페이지를 분리합니다. KORAIL `official_search_url`은 서버가 공식 `stn_cd ↔ stn_nm` identity에서 만든 strict 25키 일반검색 주소일 때만 시간표 DTO에 존재하며, 고정 `official_booking_url`을 덮지 않습니다. 웹도 동일 host·path·단일키·고정값·날짜·시각·4자리 코드 계약을 다시 검증하고 실패하면 고정 공식 진입점으로 강등합니다. 이 주소는 역·날짜·시각·성인 1명 조건을 복원하지만 현재 SPA가 `txtGoTrnNo`를 읽지 않으므로 특정 열차를 자동 선택하지 않습니다. 로그인정보, 회원번호, 쿠키, 인증 토큰과 `mutMrkVrfCd`·`srtJob`·`selectedTrainList` 같은 예약 연계값은 URL·QR·클립보드에 넣지 않습니다.

`-8002`·`-8003`, 403, CAPTCHA, NetFunnel 또는 비정상 접근 응답은 재시도 최적화의 입력이 아닙니다. 자동 접근을 중단하고 사용자 직접 확인으로 전환하는 전체 계약은 [공식 페이지 차단 시 정책 준수 대체 경로](research/OFFICIAL_HANDOFF_FALLBACKS.md)를 따릅니다.

기본 registry에서는 예약 capability를 열지 않습니다. 다만 관측 3중 opt-in에 운영사별 자동 예매 gate, 로그인 확인된 활성 계정, 작업의 `reserve_once_before_payment` 정책까지 모두 충족하면 KORAIL·SRT adapter가 후보·가용성 에피소드별 DB fence 아래 예매 요청을 한 번 수행해 `payment_required`까지만 전이합니다. 결제·결제정보 입력은 호출하지 않습니다. KORAIL 예약 화면은 exact 열차 행과 좌석 등급 안에서 활성 가격 control을 다시 검증합니다. 관측과 예약은 모두 `.price_box` 전체의 좌석 등급·가격·공식 상태 class를 같은 classifier로 해석하고, 내부 anchor가 가격만 표시하는 DOM도 부모 박스 identity 안에서만 연결합니다. `sold_out_soon`·`매진임박`은 `limited` 행동 가능 상태이고 `sold_out`·매진·예약대기·가격 없는 보조 링크는 클릭 대상이 아닙니다. 반응형 DOM이 동일한 좌석 등급·가격 문구의 동등 control을 중복 렌더하면 한 행동으로 접지만, 문구나 가격이 다르면 계속 모호성으로 닫고 클릭하지 않습니다. `NOT_AVAILABLE`은 보류 없음이 확정되고 이후 확정 비가용 관측 뒤 다시 행동 가능해진 새 에피소드에서만 한 번 재시도할 수 있습니다. `AUTH_REQUIRED`는 그 시도 종료보다 새로운 성공 로그인 검증 세대에서만 한 번 재무장합니다. `UNKNOWN`은 직접 재호출하지 않고 공식 예약 내역을 최대 6회 읽기 전용으로 확인하며, 최초 시도의 exact `NOT_FOUND`만 같은 연속 가용 구간에서 한 번 재무장합니다. 이 재무장 시도도 `UNKNOWN`이면 추가 호출하지 않습니다. 요청 중 취소·만료되거나 `FAILED`·`PROVIDER_BLOCKED`처럼 결과가 불명확하거나 보류 없음이 입증되지 않으면 재호출하지 않고 공식 예약 내역의 수동 확인 이벤트를 남깁니다. `policy.py`의 보호 신호 분류와 별도로 `provider_circuits` 영속 모델 및 worker preflight를 구현해, `open`은 `cooldown`, `manual_hold`·`half_open`은 `auth_required`로 provider 호출 전에 중단합니다. stale reservation attempt 복구 조회는 nullable `registration_evidence` outer join의 nullable 쪽을 잠그지 않도록 `with_for_update(of=(ReservationAttempt, WatchCandidate, Watch), skip_locked=True)`로 필요한 행만 잠가 PostgreSQL 오류를 피합니다. circuit 행은 재시작 뒤에도 DB에 남지만 관리자 조회·수동 재개 API와 cooldown 복구 sweep은 아직 없습니다. 실제 계정의 예약 성공과 결제대기 인계는 아직 운영 검증이 필요합니다. 근거와 미확인 탐지 영역은 [공식 페이지 자동화 감지 연구](research/OFFICIAL_AUTOMATION_DETECTION.md)에 기록합니다.

위 문단의 `PROVIDER_BLOCKED` 재호출 금지는 같은 로그인 검증 세대에 적용됩니다. 차단 시도 종료보다 새로운 성공 로그인 검증 세대가 영속 확인된 경우에만 `AUTH_REQUIRED`와 동일한 세대 fence로 그 세대에서 한 번 재무장합니다. `watch` 상태 복구만으로는 재무장하지 않습니다.

## 인증과 비밀값

전역 `admin_accounts` 단일 행만 저장하며 별도의 사용자 목록·가입·초대·역할 테이블은 만들지 않습니다. ID는 비교 가능한 정규형으로 저장하고 비밀번호 원문은 저장하거나 로그에 남기지 않으며 Argon2id 해시만 보관합니다. 최초 설정 API는 계정 0개와 명시적인 서버 측 등록 스위치가 모두 충족될 때만 열리고 첫 계정이 생성되면 추가 등록은 닫힙니다. 최초 등록 전 서비스는 기본 loopback 또는 Tailscale 안에서만 열어 최초 방문자의 관리자 선점을 막습니다. 등록과 로그인에는 직접 client IP 기준 제한을 적용합니다. session은 HttpOnly·Secure·SameSite Strict 쿠키이며 상태 변경에는 허용 origin과 CSRF 이중 검사를 적용합니다. 철도사 결제정보와 원문 세션 쿠키는 모델 자체에 존재하지 않습니다.

알림 channel config는 `.env`의 `SECRET_ENCRYPTION_KEY`로 암호화합니다. Web Push VAPID private key를 포함한 배포 비밀값도 `.env`에서 컨테이너 환경변수로 주입하며 API 응답이나 채널 config에는 포함하지 않습니다. `.env`는 Git·Docker build context·일반 DB 백업에서 제외하고 별도의 암호화 저장소에 보관합니다. VAPID private key는 URL-safe Base64 또는 환경변수 안의 PKCS8 PEM을 받으며, PEM은 원문을 파일로 만들지 않고 메모리에서 base64url DER로 정규화한 뒤 pywebpush에 전달합니다. worker는 전달 전에 VAPID P-256 key와 구독의 HTTPS endpoint·P-256 공개키·16-byte auth secret을 검증하고, 만료 구독·구독 거부·VAPID 인증/설정·push provider 장애를 비밀값 없는 오류 범주로 분리합니다. 알림 채널은 watch 소유 설정이 아니라 단일 관리자 전역 설정입니다. 상태 알림을 outbox에 적재하는 시점에 현재 `enabled=true`인 채널을 선택하므로 채널을 나중에 켜도 기존 활동 작업의 다음 상태 알림부터 적용되고, 끄면 이후 새 알림 전달 대상에서 제외됩니다. 미설정 채널의 UI 스위치는 설정 흐름을 시작하며 Web Push는 service worker readiness를 bounded timeout으로 기다린 뒤 권한·구독을 진행합니다. 웹의 `OS 알림` 상태는 서버 채널의 `enabled`만으로 결정하지 않고 현재 기기의 Notification 권한과 Push subscription도 함께 확인합니다. worker가 outbox payload를 push service에 전달하면 `/sw.js`가 foreground React tree와 독립적으로 `showNotification()`을 호출하고, 알림 클릭은 payload의 handoff URL 또는 앱 root를 엽니다. 따라서 접속 중 `실시간 알림` surface와 OS 알림은 서로 대체하지 않는 별도 표시 경계입니다.

## 호출 제한

실험 프로필이 활성화되더라도 API와 Chromium sidecar 각각 provider 동시 요청을 1개로 제한하고 동일 query는 singleflight·TTL cache로 병합합니다. sidecar는 `app` 내부망과 전용 egress network에만 연결되고 host 포트를 노출하지 않습니다. 기본 Pydoll 엔진은 cold UI 성공 뒤 browser를 닫고, official same-origin POST template과 cookie jar를 구간별 기본 최대 300초·20회인 프로세스 메모리 lease로만 유지합니다. pool은 최대 4개 bounded LRU이며 A→B→A 전환에서 아직 유효한 A lease를 재사용합니다. TTL·횟수·선택 구간 오류는 해당 lease만, 용량 초과는 가장 오래 사용하지 않은 lease만 전송 전에 닫고 새 UI 초기화를 수행합니다. 로그인 검증·예매·sidecar 종료는 모든 replay lease를 먼저 닫습니다. 401, 동일 origin 로그인 경로 redirect, 명시적인 로그인 HTML처럼 session 만료가 확인된 경우만 동일 read-only 요청에서 선택 구간의 cold 초기화 한 번으로 회복합니다. capture·response schema·cursor 불일치와 그 밖의 4xx는 같은 요청에서 재시도하지 않고 fail-closed하며, 403·429·보호 코드·CAPTCHA·NetFUNNEL·비정상 접근은 선택 lease 폐기와 cooldown 뒤 즉시 중단합니다. 기존 `playwright_direct_cdp` 엔진은 sidecar 내부 `127.0.0.1` CDP port만 검색별로 임시 사용하며 HTTP replay로 자동 전환하지 않습니다. 엔진은 시작 시 `KORAIL_BROWSER_ENGINE` 하나로 고정되고 보호·rate-limit 실패를 계기로 다른 엔진이나 cold UI로 fallback하지 않습니다. API의 sidecar 전체 대기 90초와 browser·HTTP 개별 요청 대기 25초는 분리하고, 일반 실패 backoff는 최대 300초로 제한합니다. readiness startup probe가 일시 실패하면 5초 간격의 단일 bounded probe로 자가 복구합니다. 기존 direct-CDP 엔진의 raw mouse release·detach와 browser/profile 정리는 반복 취소에도 별도 task로 완료하며, sidecar shutdown은 readiness를 먼저 내리고 진행 중인 singleflight 검색을 70초, 취소 정리를 10초의 bounded deadline으로 drain한 뒤 유휴 browser와 HTTP lease pool도 닫습니다. 수동 Chrome raw 요청 가져오기, NetFUNNEL key 계산·재사용, IP 회전, CAPTCHA 우회, User-Agent·header·TLS 지문 위장, proxy 회전 로직은 없습니다.

## 벤치마크 구현에서 채택한 경계

티캣과 레일픽의 공개 정책과 공식 Play 설치본 정적 분석을 조사한 결과, 티캣의 foreground service·WorkManager와 레일픽의 alarm·receiver·WorkManager 등 Android 단말 백그라운드 구성은 확인됐습니다. 레일픽은 여기에 Firebase 기반 설정·승차권 동기화와 FCM 알림을 결합합니다. 다만 실제 철도 요청과 자격증명 흐름은 확인하지 않았습니다. 레일웨잇은 모바일 앱이 아니라 개인 서버 상주 서비스이므로 실행 위치를 그대로 복제하지 않고 다음 구조적 패턴만 가져옵니다.

- KORAIL·SRT adapter와 공통 열차·좌석 등급별 상태 모델 분리
- 철도 자격증명과 일반 상태·알림 데이터 분리
- 조회·예약 데이터 플레인과 outbox 기반 알림 전달 분리
- 좌석 감지·예약 시도·결제 필요·결제 완료의 명시적 상태 전이
- 민감한 예약 내역 가져오기는 사용자 행동으로만 시작

로그인·예약 없이 정식 설치 앱 화면을 확인했을 때 티캣은 일반/특 좌석 등급과 매진·매진임박·입석+좌석, 벨 기반 자동 예매 안내를, 레일픽은 일반실·특실별 예약 가능/매진과 취소표 감시 행동을 한 카드에서 구분했습니다. 레일웨잇은 이 정보 구조만 참고하고, 상태 출처를 숨기는 표현이나 벨 아이콘 단독 행동은 채택하지 않습니다.

프레임워크, Android background component, 저장소와 WebView 등 정적 분석 결과 및 남은 미확인 사항은 [티캣·레일픽 구현 방식 연구](research/APP_IMPLEMENTATION_STUDY.md)에 근거 수준별로 기록합니다. 비공개 철도 endpoint나 호출 간격은 벤치마크 대상으로 삼지 않습니다.

정식 제휴 provider가 확보됐을 때 필요한 후보 열차·관측·예약 시도·상태 이력·provider circuit 모델과 mock 실행 기반은 구현했습니다. `approved_provider.py`는 명세별 인증·DTO 변환 transport와 도메인 adapter를 분리하고, 별도 승인 근거와 transport 지원값의 교집합만 capability로 노출합니다. transport 결과는 정규화 요청 fingerprint·단일 좌석 등급·고정 source가 모두 일치해야 수락합니다. 이 adapter는 기본 provider registry에 등록하지 않았고 현재 official capability도 활성화하지 않습니다. 남은 승인 adapter·운영 제어면·외부 요청 lease의 순서는 [리버싱 결과 접목 계획](research/APP_REVERSE_ENGINEERING_INTEGRATION_PLAN.md), 요구사항별 완료·미완료 증거는 [승인 Provider 연동 준비 상태 감사](research/APPROVED_PROVIDER_INTEGRATION_READINESS.md)를 따릅니다.

## 웹 Official Handoff 경계

`OfficialHandoff`는 열차 결과 카드와 외부 공식 채널 사이의 UI 경계다. `official_booking_url`은 고정 공식 진입·결제 주소로, `official_search_url`은 KORAIL 조건 선입력 검색 주소로 구분한다. 검색 주소는 정확한 25키 allowlist를 재검증한 경우에만 사용하고, 추가·중복·예약 연계 키, 비공식 host/path, 잘못된 날짜·시각·코드는 고정 공식 진입점으로 강등한다. 화면은 조건이 미리 입력되더라도 특정 열차 선택·좌석 확보·예매 성공이 아님을 명시한다. 인계 payload에는 사용자 인증·결제 정보를 전달하지 않는다.

컴포넌트는 React portal로 `document.body`에 dialog를 렌더한다. 넓은 화면은 modal, 작은 화면은 bottom sheet CSS를 사용한다. 열려 있는 동안 `.app-shell`을 `inert` 및 `aria-hidden`으로 만들어 배경 상호작용·접근성 트리 노출을 막고, dialog 안의 Tab/Shift+Tab 순환과 Escape 닫기를 처리한다. 닫을 때는 원래 실행 버튼에 초점을 복원한다. 외부 열기는 `window.open(..., "_blank", "noopener,noreferrer")`이며, 링크를 열었다는 이벤트는 상태 전이에 사용하지 않는다.

좌석 패널은 일반실·특실을 독립적으로 표시한다. `mock`은 UX 벤치마크 라벨, `official_provider`는 source·`observed_at`이 있는 서버 관측값 라벨, `user_confirmed_official_page`는 기존 확인 기록의 시각과 유효기간 라벨, 그 밖에는 미관측 원인 라벨을 사용한다. 기존 `official_page_browser_companion` 데이터는 호환 범위에서만 읽으며 신규 사용자 흐름은 생성하지 않는다. 어느 구분도 background 예약 capability가 연결됐다는 뜻은 아니다.

열차 카드의 고밀도 기준은 1440px에서 182px 높이이며, 320px 화면에서는 가로 넘침 없이 한 열로 재배치한다. 상태·provenance와 44px 행동 영역은 압축 과정에서도 유지한다.
