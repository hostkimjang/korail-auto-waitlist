# 클린 구조 리팩터링 계획

> 보관 문서: 이 파일은 리팩터링 과정과 단계별 검증 기록입니다. 현재 시스템 구조는
> [시스템 구조](ARCHITECTURE.md), 현재 코드 규칙은 [코드 작성 규칙](CODE_CONVENTIONS.md)을 따릅니다.

## 목적과 판단 기준

레일웨잇은 현재 기능을 유지한 채 배포 단위를 나누지 않는 **모듈형 모놀리스**로 정리합니다. 전면 재작성, 마이크로서비스 전환, 새 상태 관리 프레임워크 도입이 목표가 아닙니다. 각 단계는 동작 가능한 수직 슬라이스여야 하며, 좌석·예약·인증의 안전 계약을 바꾸는 작업은 단순 이동과 분리해 검증합니다.

이 문서에서 사용하는 상태는 다음과 같습니다.

- **확인됨**: 현재 파일, 테스트, 문서 또는 실행 결과로 확인한 사실
- **목표**: 앞으로 적용할 구조와 의존 규칙
- **운영 미검증**: 코드·테스트가 있어도 실제 계정, 외부 채널, 장시간 운전에서 확인하지 않은 항목

코드 작성 규칙은 [코드 컨벤션](CODE_CONVENTIONS.md), 웹 전환의 상세 순서는 [웹 TypeScript·모듈 분리 계획](WEB_TYPESCRIPT_MIGRATION.md), 운영 검증 상태는 [체크리스트](../CHECKLIST.md)를 함께 봅니다.

## 확인된 현재 구조와 hotspot

2026-08-04 구조 진단 착수 시점의 줄 수입니다. 줄 수 자체를 실패 기준으로 사용하지 않고, 한 파일에 서로 다른 변경 이유가 모였음을 보여주는 탐색 지표로만 사용합니다.

| 영역 | 파일 | 진단 기준 줄 수 | 함께 모여 있는 대표 책임 |
|---|---|---:|---|
| 웹 | `apps/web/src/App.jsx` | 2,100 | shell, 인증, SSE, 화면 전환, 작업·알림 상태, 새 대기 조립 |
| 웹 | `apps/web/src/api.js` | 1,185 | fetch·CSRF, DTO 처리, 여러 feature endpoint |
| 웹 | `apps/web/src/styles.css` | 6,670 | token, 기본 요소, shell, feature, 반응형 규칙 |
| API | `korail_pydoll_browser.py` | 3,839 | browser lifecycle, DOM 탐색, 검색·로그인·예약 흐름 |
| API | `services.py` | 1,999 | application 정책, HTTP 오류, 트랜잭션, provider·알림 조정 |
| API | `worker.py` | 1,971 | Celery 진입, due 처리, 관측·예약·reconciliation pipeline |
| API | `schemas.py` | 1,444 | 여러 기능의 transport schema |
| API | `providers.py` | 1,300 | provider 계약, 구현 선택, capability |
| API | `api.py` | 1,197 | 여러 기능의 route와 HTTP 변환 |

웹에는 이미 `main.tsx`, strict TypeScript 설정과 `domain/`, `api/`, `features/`, `shared/`의 기능별 경계가 존재합니다. `api.js` barrel과 확인된 `api -> feature` 역의존은 제거됐고 `auth`, `home`, `new-wait`, `official-handoff`, `reservations`, `settings`의 leaf 컴포넌트·hook·순수 함수도 점진 분리됐습니다. 현재 진입 조립도 strict `App.tsx`로 전환됐으며, 남은 JS/JSX 테스트와 DTO·도메인·ViewModel 혼용, feature CSS 추가 분리는 후속 정리가 필요합니다.

백엔드는 FastAPI·SQLAlchemy·Pydantic·Celery와 provider별 구현이 단일 Python package의 평면 모듈로 배치되어 있습니다. DB unique, idempotency, outbox, provider lease, credential generation 같은 안전 불변식은 이미 있으므로 구조 이동 중 보존해야 하며, 먼저 model과 migration 전체를 재배치하지 않습니다.

## 목표 저장소 구조

현재의 배포·운영 단위는 유지합니다.

```text
korail-auto-waitlist/
  apps/
    web/                         React·Vite PWA
    api/                         FastAPI·Celery 모듈형 모놀리스
    korail-browser-companion/    기존 사용자 보조 경계
  docs/                          설계·운영·안전·연구 문서
  infra/                         배포·프록시·운영 설정
  scripts/                       저장소 검증·운영 보조 스크립트
  compose.yml
  CHECKLIST.md
```

Nx, Turborepo 또는 별도 package workspace는 실제 빌드 공유 문제가 생기기 전에는 도입하지 않습니다.

### 웹 목표 구조

```text
apps/web/src/
  app/                           shell, navigation, 전역 live sync 조립
  domain/                        provider, station, seat, timetable, watch 계약
  api/                           client, DTO validator, mapper, endpoint
  features/
    auth/
    home/
    new-wait/
    reservations/
    settings/
    notifications/
    official-handoff/
  shared/
    ui/                          공용 접근성 표현 컴포넌트
    lib/                         React·I/O 없는 순수 함수
  hooks/                         둘 이상의 기능이 같은 계약으로 쓰는 hook만
  fixtures/                      명시적인 demo·mock 데이터
  styles/                        tokens, base, shell, feature, responsive entry
  main.tsx
```

```text
app -> features -> api/domain/shared
api -> domain/shared
domain/shared/lib -> React·네트워크·feature 의존 금지
```

화면 컴포넌트는 orchestration, 표시 컴포넌트는 typed props와 접근성 표현을 담당합니다. 외부 JSON은 `unknown -> validator -> DTO -> mapper -> domain/ViewModel` 순서로 바꿉니다. `NewWait`는 여정·조건·열차·확인 단계, 역 카탈로그, 시간표 검색, 선택 우선순위와 등록 상태를 독립적으로 설명할 수 있는 경계로 분리하되 하나의 거대한 controller hook으로 다시 합치지 않습니다.

### API 목표 구조

```text
apps/api/src/rail_waitlist/
  bootstrap/                     FastAPI·Celery 생성과 dependency wiring
  common/                        clock, 공용 domain/application 오류
  watches/                       감시 수명주기와 후보 정책
  reservations/                  episode, attempt, confirmation 정책
  timetables/                    역·시간표·snapshot·evidence
  provider_accounts/             credential generation과 인증 상태
  notifications/                 outbox와 전달 정책
  operations/                    운영 상태·preferences·health read model
  providers/
    contracts.py                 역할별 Protocol과 정규화 결과
    registry.py                  설정·승인·구현의 capability 교집합
    tago/
    korail/
    srt/
  persistence/                   SQLAlchemy repository·UoW 구현
  tasks/                         얇은 Celery entrypoint와 pipeline 조립
```

각 기능에 빈 `domain/application/http/persistence` 디렉터리를 일괄 생성하지 않습니다. 복잡한 트랜잭션, 외부 I/O, 별도 정책이 있는 기능에 필요한 경계만 수직 슬라이스에서 만듭니다.

```text
FastAPI·Celery·bootstrap -> application -> domain
persistence·provider·notification 구현 -> application의 Protocol
```

FastAPI route는 인증·transport 검증·오류 변환, Celery task는 실행·retry 경계만 담당합니다. application은 유스케이스와 트랜잭션을 조정하고, domain은 프레임워크와 무관한 상태·결정 규칙을 소유합니다. bootstrap만 실제 구현을 조립합니다.

## 리팩터링 중 보존할 계약

다음 항목은 파일 이동을 이유로 의미를 바꾸지 않습니다.

- 한 운영사 조회 실패가 다른 운영사의 성공 결과와 사용자 선택을 지우지 않습니다.
- 조건 변경 뒤 늦게 도착한 자동·수동 조회 응답은 query key로 폐기합니다.
- 여정·날짜·시간·운영사 변경 시 오래된 열차·좌석 선택을 무효화합니다.
- KST 서비스 날짜, timezone-aware datetime, 시간창 양끝 포함과 다음 날 00:00 표시 경계를 유지합니다.
- TAGO 시간표와 좌석 재고를 분리하고 근거 없는 좌석은 `unknown/not_observed`로 닫습니다.
- 좌석 status, action, provenance, `observed_at`, `fresh_until`을 함께 검증하며 등록 당시 evidence와 최신 observation을 혼동하지 않습니다.
- KORAIL·SRT 실행 capability는 구현, 설정 gate, 승인 근거의 교집합이며 근거 없이 활성화하지 않습니다.
- 예약은 가용성 episode당 최대 한 번이고 idempotency, unique fence, lease fencing, credential generation CAS를 유지합니다.
- `UNKNOWN`, `AUTH_REQUIRED`, `PROVIDER_BLOCKED`, 결제기한 경과 후 확인의 제한 재무장·read-only reconciliation 규칙을 유지합니다.
- 계정과 watch의 잠금 순서, 비밀값 비영속, 검색 actor와 인증 actor의 분리를 유지합니다.
- 상태 변경과 outbox 이벤트의 원자성을 유지하며 알림 실패가 좌석 관측·예약 트랜잭션을 되돌리지 않습니다.
- 공식 채널 URL allowlist, 결제 전 중단, 자동 결제·보호장치 우회 금지 경계를 유지합니다.
- OfficialHandoff의 portal, `inert`, `aria-hidden`, focus trap·복원, body scroll 복원을 유지합니다.
- 320px, 200% 확대, 44px 행동 영역, 색상 외 상태 표현을 유지합니다.
- 비밀번호, cookie, token, 카드정보, credential fingerprint를 URL·로그·metric·SSE·outbox·fixture에 넣지 않습니다.

## 단계 ledger

| 단계 | 상태 | 범위 | 산출물 |
|---|---|---|---|
| 0. 기준선 | 완료 | 생성물·비밀값 분류, 검증 기준선, 안전한 branch·commit·tag | 재현 가능한 리팩터링 시작점 |
| 1. 규칙 고정 | 완료 | 컨벤션·계획 문서, formatter/lint/import-boundary 기준 | 이 문서와 자동 품질 gate |
| 2. 기계적 분리 | 진행 | demo fixture, 공용 API client, 작은 router/schema 이동 | 행동 변화 없는 작은 모듈 |
| 3. 웹 수직 슬라이스 | 진행 | `NewWait` form·station·timetable·registration 흐름 | feature별 TS/TSX와 회귀 테스트 |
| 4. 백엔드 정책 | 진행 | watch transition, reservation episode, reconciliation 결정 함수 | 프레임워크 비의존 domain 정책 |
| 5. 실행 경계 | 진행 | 최소 UoW/repository seam, worker pipeline 분리 | 얇은 route/task와 트랜잭션 테스트 |
| 6. provider 역할 | 진행 | timetable/observe/reserve/confirm/lifecycle 계약 분리 완료, 기본 stub 제거·승인 transport 남음 | capability와 adapter 역할별 검증 |
| 7. 웹 전환 종료 | 진행 | `App.tsx`, `allowJs=false`, CSS 단계 분리 | strict TS와 모듈 경계 완성 |
| 8. 고위험 sidecar | 완료 | Pydoll 응답 안전·인증 session·로그인 DOM·검색 actor/DOM·예약 actor/DOM·confirmation/replay 분리 | 기존 보호·exact identity·결제 전 중단 계약 보존 |

단계 상태는 코드·검증 결과와 함께 `CHECKLIST.md`에서 갱신합니다. 문서 작성만으로 구현 단계를 완료 처리하지 않습니다.

### 2026-08-04 첫 구조 슬라이스

- 기준선 완료: 루트 `output/`, Playwright 도구 상태, API pytest 임시 디렉터리와 cache를 Git 제외 대상으로 고정하고 `docker compose config --quiet`를 통과했습니다. 전체 통합 검증 뒤 `codex/clean-architecture`의 최초 commit `a5ab434`와 tag `clean-architecture-phase-1-baseline-20260804`를 만들었습니다.
- 웹 기계적 분리: `App.jsx`의 demo 데이터 책임을 `fixtures/demoData.ts`로, `api.js`의 공용 HTTP transport를 `api/client.ts`로 이동했습니다. settings API mapper의 `api -> feature` 역방향 의존을 제거하고, 알림 전용 UI와 결제기한 정책·hook·표시 UI를 실제 소유 경계로 옮겼습니다.
- 웹 경계 gate: `api`, `domain`, `shared`, feature 사이의 새 역방향 의존을 차단하는 ratchet 테스트를 추가했습니다. 착수 때 있던 허용 예외 11개를 위 슬라이스에서 모두 제거해 현재 allowlist는 비어 있습니다.
- API transport 분리: 운영 요약, UI preferences, 철도 계정·runtime 라우트와 Pydantic schema를 기능 패키지로 이동하고 중앙 `schemas.py`에는 객체 identity가 같은 compatibility re-export를 유지했습니다.
- API domain 분리: 예약 결과의 재시도·수동 확인 투영 정책을 `reservations/domain.py`로 이동하고 모든 `ReservationOutcome`을 표 기반 테스트로 고정했습니다. 모든 `domain.py`의 프레임워크·provider SDK import와 향후 application 모듈의 FastAPI import를 차단하는 gate도 추가했습니다.
- 확인된 검증: 웹 strict typecheck, Vitest 51개 파일·347건, production build와 Sites 4건을 통과했고 API 전체 pytest 949건과 Ruff 핵심 규칙·module boundary를 통과했습니다. `experimental-rail` 프로필 전체 이미지를 재빌드·강제 재생성한 뒤 migration·log-init exit 0, 장기 서비스 11개 healthy, 재생성 뒤 새 runtime 오류 표식 0건을 확인했습니다.

### 2026-08-04 두 번째 구조 슬라이스

- 웹 `NewWait` 모델: 초기 폼, KST 서비스 날짜, 날짜·요일 빠른 선택 동기화, 과거 날짜 보정,
  역명·node ID 원자적 교환, provider 토글과 `reserve_once_before_payment`의 fail-closed 보정을
  `features/new-wait/newWaitForm.ts`의 strict TypeScript 순수 함수로 분리했습니다. 역 카탈로그,
  시간표 검색, stale query 차단, 선택 우선순위와 등록 상태는 후속 슬라이스입니다.
- 웹 인증 API: 인증 상태·최초 관리자 등록·비밀번호 로그인·로그아웃을 `api/auth.ts`가 소유하고,
  기존 `api.js`에는 함수 객체 identity가 같은 compatibility re-export를 유지했습니다. `AuthGate`와
  `useAuthState`는 새 소유 모듈을 직접 사용합니다.
- API 알림 transport: Web Push 공개키, 알림 채널 CRUD와 시험 전송 route 및 관련 Pydantic schema를
  `notification_management/http.py`와 `notification_management/schemas.py`로 이동했습니다. 중앙
  `schemas.py`의 compatibility export identity를 보존했고, SSE `/events`는 기존 `api.py`에 남겼습니다.
- 자동 품질 gate: 웹은 `src`·`tests`·`e2e`·`scripts`·`worker`를 런타임별 전역으로 검사하고,
  전환 전에 존재한 effect/ref 부채 27건만 파일·규칙·위치·소스 행 해시 지문으로 고정합니다. API는
  전체 `E/F/I` 검사에 더해 현재 미포맷 파일의 경로와 개행 정규화 SHA-256을 고정한 format ratchet을
  사용합니다. 두 번째 슬라이스 당시 legacy 미포맷 69개를 격리했으며 새 미포맷 파일, 수정된 legacy 파일, stale 목록은
  모두 실패합니다.
- 확인된 검증: 웹 ESLint 오류 0·고정된 기존 경고 27, strict typecheck, Vitest 54개 파일·377건,
  production build, Sites 4건을 통과했습니다. 통합 작업 트리의 API 전체 pytest 955건도
  통과했습니다. `experimental-rail` 전체 이미지를 재빌드·강제 재생성한 뒤 migration·log-init
  exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 오류 표식 0건을
  확인했습니다.
- 남은 핵심 부채: 웹 `App.jsx`·`api.js`·`styles.css`, API `services.py`·`worker.py`와 provider 역할
  분리를 후속 수직 슬라이스로 진행합니다. 이번 완료는 전체 클린 구조 전환 완료를 뜻하지 않습니다.

### 2026-08-04 세 번째 구조 슬라이스

- 웹 알림 경계: 알림 채널 CRUD·시험 전송과 Web Push 수명주기를 `api/notifications.ts`로, SSE 이벤트
  종류·history cutoff·오류·close 계약을 `api/events.ts`로 이동했습니다. `App.jsx`는 두 소유 모듈을
  직접 사용하고 `api.js`에는 동일 함수 객체 compatibility export만 남겼습니다.
- `NewWait` 역 카탈로그: 운영사 key, demo/공식 source, 비동기 요청·재시도, provider 변경 뒤 stale
  응답 차단, 실패 시 역명/node ID 원자적 초기화를 `useStationCatalog.ts`로 옮겼습니다. 이 이동으로
  `App.jsx`의 기존 effect/ref ESLint 부채 2건을 제거해 고정 경고가 27건에서 25건으로 줄었습니다.
- API 알림 application: 설정 필수 필드·Webhook URL 검증, secret 암호화, 생성·수정과 시험 전송 outbox
  정책을 `notification_management/service.py`로 이동했습니다. 공용 outbox idempotency primitive는
  `outbox.py`로 분리했고, 중앙 `services.py`에는 기존 호출자를 위한 동일 함수 import만 유지했습니다.
- 확인된 검증: 웹 ESLint 오류 0·고정 기존 경고 25, strict·unused typecheck, Vitest 57개 파일·393건,
  production build와 Sites 4건을 통과했습니다. API 전체 pytest 958건, Ruff `E/F/I`, format ratchet과
  module boundary도 통과했습니다. `experimental-rail` 전체 이미지를 재빌드·강제 재생성한 뒤
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 최근 오류 표식 0건을
  확인했습니다.
- 남은 핵심 부채: `NewWait` 시간표 검색·등록·열차 선택, App shell의 작업 CRUD·SSE orchestration,
  `api.js`의 역·시간표·작업 API, 전역 CSS, API worker·provider 역할을 다음 슬라이스에서 분리합니다.

### 2026-08-04 네 번째 구조 슬라이스

- 웹 API 경계: 역 카탈로그 외부 DTO 검증·metadata tuple·identity 병합을 `api/stations.ts`, 시간표 검색
  조건·provider override·부분 실패·DTO mapping을 `api/timetables.ts`, 좌석 provenance와
  `unknown/not_observed` 정규화를 `api/seatClasses.ts`로 이동했습니다. `api.js`는 동일 구현 객체를
  다시 export하는 전환 경계로 축소했습니다.
- `NewWait` 시간표 상태: step 3 자동 조회, provider별 재시도, 좌석 fallback, 수동 전체 조회와
  cache-only 동기화를 `useTimetableSearch.ts`로 옮겼습니다. 모든 경로가 같은 query key를 사용해 조건
  변경 뒤 늦은 응답을 버리고, 한 provider 실패 시 다른 provider의 성공 열차와 상태를 보존합니다.
- API 시간표 경계: `/timetables`, `/timetable-snapshots`, `/seat-status/refresh`와 snapshot background
  session을 `timetable_management/http.py`로, live→TAGO fallback·overlay·evidence orchestration을
  FastAPI 비의존 `application.py`로 이동했습니다. cooldown 조회 `/seat-status/status`는 별도 수명주기로
  중앙 router에 유지했습니다.
- 확인된 검증: 웹 ESLint 오류 0·고정 경고 23, strict·unused typecheck, Vitest 60개 파일·406건,
  production build와 Sites 4건을 통과했습니다. API 전체 pytest 960건, 관련 회귀 37건, Ruff `E/F/I`,
  format ratchet과 module boundary를 통과했고 legacy 미포맷 baseline은 68개로 줄었습니다.
  `experimental-rail` 전체 이미지를 재빌드·강제 재생성한 뒤 migration·log-init exit 0, 장기 서비스
  11개 healthy, API·proxy health 200, 최근 오류 표식 0건을 확인했습니다.
- 남은 핵심 부채: `NewWait` 등록 상태·열차 선택 UI, watch API와 App shell 작업 orchestration,
  `api.js` compatibility 제거, 전역 CSS, API watch/reservation service와 worker·provider 역할 분리입니다.

### 2026-08-04 다섯 번째 구조 슬라이스

- 웹 watch API 경계: watch 생성 payload·evidence/watch 멱등 키·CRUD endpoint를
  `api/watches.ts`로 이동했습니다. 외부 JSON은 `unknown`에서 provider·status·날짜·후보 identity·
  optional timestamp·공식 URL을 검증하고, 명시적으로 정규화한 DTO 호환 필드와 ViewModel만
  반환합니다. 최신 좌석 관측은 허용 상태·provider에 맞는 source·timezone-aware 시각·
  `observed_at < fresh_until` 전체 tuple이 확인될 때만 공식 또는 mock 관측으로 투영합니다.
  `api.js`는 같은 함수 객체 compatibility export만 유지하며 108줄로 줄었습니다.
- App watch 상태 경계: canonical REST snapshot, SSE burst 병합, visibility polling, 상태 전이 알림,
  인증 만료, 예약정책 PATCH와 교차한 stale GET 차단을 `features/app/useWatchCollection.ts`로
  옮겼습니다. 구독 lifecycle epoch으로 이전 GET·SSE·401·refresh timer를 폐기하고 teardown 때
  대기 reservation event queue를 비웁니다. App shell에는 사용자 watch mutation 조립을 남겼으며
  `App.jsx`는 1,505줄로 줄었습니다.
- API watch transport와 read model: watch CRUD·start·pause·cancel·mock-transition 9개 endpoint,
  관리자 인증·멱등 header와 commit 뒤 best-effort 즉시 처리를 `watch_management/http.py`로,
  최신 observation·reservation attempt batch 조회와 결제 보류 projection을
  `watch_management/read_model.py`로 이동했습니다. 중앙 `api.py`는 212줄로 줄었고 공개 endpoint·
  트랜잭션·provider capability·outbox 계약은 그대로입니다.
- 교차 리뷰 보강: 폐기된 live-sync lifecycle의 늦은 응답과 SSE queue 세대 누출, 불완전한 최신 관측의
  공식 provenance 합성, raw DTO·malformed candidate 누출을 발견해 같은 슬라이스에서 회귀 테스트와
  fail-closed 경계를 추가했습니다. 백엔드 이동은 9개 route AST와 projection 576개 조합을 원본과
  대조해 동등성을 확인했습니다.
- 확인된 검증: 웹 ESLint 오류 0·고정 경고 22, strict·unused typecheck, Vitest 62개 파일·423건,
  production build와 Sites 4건을 통과했습니다. API 전체 pytest 964건, Ruff `E/F/I`, format ratchet
  68개와 module boundary를 통과했습니다. `experimental-rail` 전체 이미지를 재빌드·강제 재생성한 뒤
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 최근 오류 표식 0건을
  확인했습니다.
- 남은 핵심 부채: `NewWait` 등록·열차 선택 UI, App watch mutation과 나머지 shell 조립,
  `api.js` compatibility 제거, 전역 CSS, API watch application/service와 worker·provider 역할 분리입니다.

### 2026-08-04 여섯 번째 구조 슬라이스

- 웹 API barrel 종료: 좌석 재조회와 unknown DTO·provider 검증을 `api/timetables.ts`, demo runtime
  gate를 `shared/lib/runtimeConfig.ts`로 옮기고 production·test caller를 실제 owner import로
  전환했습니다. 사용처 없는 snapshot revision·provider frontend API와 production graph에서 접근할 수
  없던 Browser Companion 패널을 제거한 뒤 `api.js`를 삭제했고, module-boundary 테스트가 같은 중앙
  barrel의 재도입을 막습니다.
- `NewWait` 등록 상태: 좌석별 pending·cancelling·DB hydration, 정확한 watch ID 취소, `add_to_watch`
  capability, 만료 evidence 재조회 1회와 생성 재시도 1회를 `useSeatWatchRegistration.ts`로
  옮겼습니다. committed snapshot과 stable callback으로 최신 form·train을 사용하면서 render 중 ref
  변경을 제거해 App의 legacy hook warning 한 건도 없앴습니다. `App.jsx`는 1,446줄로 줄었습니다.
- API watch application: 정책 변경·start 뒤 즉시 처리 자격을 FastAPI 비의존
  `watch_management/application.py`로 이동했습니다. provider gate 뒤 계정, 그 뒤 reservation capability를
  확인하며 실제 enqueue는 기존 service commit 뒤 HTTP에 남겼습니다. create/update/transition의 잠금·
  트랜잭션·멱등성·outbox는 이동하지 않았습니다.
- 테스트 정리: compatibility 객체 identity 6건, dead snapshot API 1건, 접근 불가능 패널 1건을 제거하고
  좌석 재조회·runtime config·barrel 부재·등록 hook의 실제 계약 9건을 추가했습니다. 따라서 의미 없는
  테스트 삭제 근거를 남기면서 전체 웹 테스트는 63개 파일·424건으로 증가했습니다.
- 확인된 검증: 웹 ESLint 오류 0·고정 경고 19, strict·unused typecheck, Vitest 63개 파일·424건,
  production build와 Sites 4건을 통과했습니다. API 전체 pytest 980건, 관련 85건, Ruff `E/F/I`,
  format ratchet 68개와 module boundary를 통과했습니다. `experimental-rail` 전체 이미지를 재빌드·
  강제 재생성한 뒤 migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200,
  최근 오류 표식 0건을 확인했습니다.
- 남은 핵심 부채: `NewWait` 단계 렌더링·결과 카드, App watch mutation과 나머지 shell 조립,
  legacy JS/JSX와 전역 CSS, API watch transaction/service와 worker·provider 역할 분리입니다.

### 2026-08-04 일곱 번째 구조 슬라이스

- App watch mutation 경계: pause·resume·cancel·delete와 예약정책 변경을 strict
  `features/app/useWatchMutations.ts`로 이동했습니다. 이 훅은 별도 축약 record를 만들지 않고
  `api/watches.ts`의 canonical `MappedWatch`를 사용하며, demo의 immutable 상태 전이와 live API 응답
  교체, 실패 toast, cancel 오류 재전파를 보존합니다. 예약정책 변경은 기존 mutation guard를 먼저
  열고 성공·실패 모두 guard 종료, 목록 refresh, per-watch updating 상태 정리를 수행합니다.
  초기 fixture와 마법사 완료 결과도 `fixtures/demoData.ts`의 typed factory가 raw/camel 정책·공식 URL·
  후보 identity를 함께 채운 canonical 객체로 만듭니다. `App.jsx`에는 API·collection·화면을 연결하는
  조립만 남았고 현재 1,387줄입니다.
- API 중앙 router 종료: 기존 `api.py`의 잔여 6개 endpoint를
  `event_stream/http.py`, `provider_registry/http.py`, `timetable_management/catalog_http.py`,
  `seat_status_operations/http.py`, `timetable_management/official_evidence_http.py`의 두 router로 옮긴 뒤
  중앙 `api.py`를 삭제했습니다. `main.py`가 새 owner들을 기존 순서로 직접 등록하며 공개 경로와
  관리자 인증·`no-store`·멱등 header 계약을 유지합니다.
- SSE 수명주기 보강: `/events`는 `Last-Event-ID` history lookup과 각 poll에 새 DB session을 사용하고
  정상·오류 모두 poll session을 닫습니다. wire event·keepalive와 `text/event-stream`,
  `Cache-Control: no-cache`, `X-Accel-Buffering: no` 응답 header를 focused 계약 테스트로 고정했습니다.
- 확인된 검증: watch mutation 10건과 demo factory 2건을 포함해 웹 ESLint 오류 0·고정 경고 19,
  strict typecheck, Vitest 65개 파일·436건, production build와 Sites 4건을 통과했습니다. API 전체
  pytest 993건, 새 owner·SSE focused 13건, Ruff `E/F/I`와 format ratchet 68개도 통과했습니다.
  `experimental-rail` 전체 이미지를 재빌드·강제 재생성한 뒤 migration·log-init exit 0, 장기 서비스
  11개 healthy, API·proxy health 200, 최근 오류 표식 0건을 확인했습니다.
- 남은 핵심 부채: `NewWait` 단계 렌더링·결과 카드, App의 알림·화면 전환 shell, legacy JS/JSX와
  전역 CSS, API schema·application·services·worker와 provider 역할 분리입니다. SSE 관리자 인증
  dependency 수명과 `(created_at, id)` cursor의 commit-order 의미도 별도 정책 변경으로 검증합니다.

### 2026-08-05 여덟 번째 구조 슬라이스

- 웹 열차 결과 표현: App 안의 열차 카드와 좌석 등급 표현을 strict
  `features/new-wait/TrainResultCard.tsx`로 이동했습니다. 카드 metadata와 좌석 status·action,
  provenance/client freshness, `idle|pending|active|cancelling|error` 등록 상태 union을 typed props로
  고정하고, 공식 예매·예약대기 portal은 typed `OfficialHandoff` component 주입 경계로 연결했습니다.
  `App.jsx`는 1,150줄로 줄었고 고정 ESLint 경고도 18건으로 감소했습니다.
- 웹 시간표 경계 보강: `api/timetables.ts`의 canonical DTO mapper가 provider·열차번호·출발역·도착역과
  timezone-aware 출도착 시각을 필수 journey 계약으로 검증합니다. 선택 필드인 운임·source·조회 시각·
  공식 URL은 잘못된 값을 표시 계약으로 승격하지 않고 `null|unknown`으로 닫습니다. demo fixture도
  별도 화면 전용 shape를 만들지 않고 같은 mapper를 통과합니다.
- 알림 delivery application: worker의 outbox 소비 본문을 FastAPI·Celery 비의존
  `notification_management/delivery.py`로 옮겼습니다. due `PENDING`·허용 event type filter, 생성 시각
  순서, 50건 제한, `FOR UPDATE SKIP LOCKED`, 누락·비활성 채널 terminal 처리, 전달 전 attempt 증가,
  최대 5회·지수 backoff, 80자 안전 오류, decrypt poison 격리, sent·failed·pending metric 계약을
  보존했습니다. `worker.py`에는 기존 Celery task 이름과 실행·성공·실패 wrapper만 남아 1,851줄이며,
  직접 수정한 `worker.py`와 `test_worker.py`를 포맷해 legacy format 격리는 66개로 줄었습니다.
- 확인된 검증: 웹 ESLint 오류 0·고정 경고 18, strict typecheck, Vitest 66개 파일·443건, production
  build와 Sites 4건을 통과했습니다. API 전체 pytest 1,007건과 Ruff `E/F/I`, format ratchet,
  module boundary를 통과했습니다.
- 보존한 후속 부채: delivery batch는 외부 전송 중 선택 row lock과 transaction을 유지합니다. 예상하지
  못한 예외가 batch를 rollback하면 이미 전송된 앞선 알림이 다음 주기에 재전송될 수 있으므로,
  claim·전달 결과 transaction 분리와 crash recovery·수신자 dedupe를 별도 정책 슬라이스로 설계해야
  합니다. 이번 단계에서는 기존 동작을 바꾸지 않았습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 재빌드·강제 재생성한 뒤 migration·log-init exit 0,
  장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 오류 표식 0건을 확인했습니다.
- 남은 핵심 부채: `NewWait`의 나머지 단계·선택 우선순위, App의 알림·화면 전환 shell, legacy
  JS/JSX와 전역 CSS, API schema·services와 worker observation·reservation pipeline 및 provider 역할
  분리입니다.

### 2026-08-05 아홉 번째 구조 슬라이스

- 웹 알림 설정 경계: App/Settings에 있던 채널 목록·편집·Web Push 표시 상태를 strict
  `features/settings/NotificationChannelSettings.tsx`로 이동했습니다. 채널별 pending key가 동시 작업을
  격리하고, 공백 필수값·HTTPS URL을 제출 전에 막으며 비밀 입력은 읽기 응답에서 복원하지 않습니다.
  미설정 `configured=false`, 비활성 시험 전송, Web Push checking/권한/구독 상태, editor focus·ARIA와
  46×44px switch 행동 영역을 계약 테스트로 고정했습니다. `App.jsx`는 1,056줄입니다.
- 웹 알림 transport 보강: `api/notifications.ts`가 channel DTO를 `unknown`에서 secret-free ViewModel로
  투영하고, 시험 전송의 `queued=true + event_id`, Web Push 공개키의 base64url·65바이트 uncompressed
  P-256 형식을 검증합니다. demo 연결도 실제 UI 상태와 같은 `supported/granted/subscribed`로 갱신합니다.
- 예약 정합화 application: worker의 due SQL·read-only 확인 orchestration·credential generation 재검증·
  상태/outbox commit을 `reservations/reconciliation_application.py`로 이동했습니다. worker에는 기존
  Celery task 이름·`rail` route와 dependency 조립만 남아 1,589줄입니다. 임대 행을 먼저 잠근 뒤
  `account -> watch -> candidate -> attempt` 순서를 사용하고 도메인 잠금 뒤 같은 epoch를 다시 검증하며,
  fresh `reconciled_at`으로 due와 결제기한을 재평가합니다. commit 뒤 drain·owned close·release 순서를
  유지하고 apply 실패는 상태와 outbox를 함께 rollback합니다.
- 서버 알림 경계 보강: 채널 이름과 필수 config 문자열을 trim한 뒤 빈 값을 거부하고, SQLite처럼 DB가
  timezone metadata를 잃은 경우에도 읽기 DTO는 UTC timestamp를 반환합니다. raw config·token·URL은
  읽기 응답이나 검증 오류에 포함하지 않습니다.
- 확인된 검증: 웹 ESLint 오류 0·고정 경고 18, strict typecheck, Vitest 66개 파일·475건, production
  build와 Sites 4건을 통과했습니다. API 전체 pytest 1,025건, Ruff `E/F/I`, format ratchet 65개와
  module boundary를 통과했습니다. focused 검증은 예약 정합화·임대·worker·Celery·알림 API 110건,
  알림 설정·transport·Web Push·반응형 계약 50건을 통과했습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 재빌드·강제 재생성한 뒤 migration·log-init exit 0,
  장기 서비스 11개 healthy, API·proxy health 200, 최근 오류 표식 0건을 확인했습니다. 실제 PostgreSQL
  두 세션 검사에서 guarded transaction 동안 만료 후 takeover가 대기하고, commit 뒤 fencing token이
  1 증가하며 이전 epoch가 거부되는 것도 확인했습니다.
- 남은 핵심 부채: PostgreSQL 경합 검사를 CI의 격리 DB job으로 상시 실행하는 작업, 같은 알림 종류의
  복수 채널을 허용할지에 대한 제품 계약, `NewWait` 나머지 단계와 App shell, legacy JS/JSX·전역 CSS,
  worker observation/reservation pipeline과 provider 역할 분리입니다.

### 2026-08-05 열 번째 구조 슬라이스

- 웹 설정 페이지 경계: 설정 section 상태와 철도 계정·알림·화면 동작·보안·시스템 조립을 strict
  `features/settings/SettingsPage.tsx`로 이동하고, 반복 사용하던 제목 DOM은
  `shared/ui/PageHeader.tsx`로 분리했습니다. 기존 class·DOM·section 순서·접근성 이름·모바일 행동
  영역을 유지하고 `initialSection`의 mount-only 의미, 사용자 선택 때만 발생하는 callback, 비밀 입력
  비복원과 submission 전달, `App.jsx`의 `Settings` 호환 export를 계약 테스트로 고정했습니다.
  `App.jsx`는 1,010줄이며 고정 ESLint 경고는 18건입니다.
- due pipeline application: due SQL, provider별 watch grouping, 신규 관측 우선·예약 정합화 후순위,
  provider 내부 직렬·provider 간 병렬 fan-out, task-scoped adapter 재사용·정리를 FastAPI·Celery 비의존
  `observations/due_pipeline_application.py`로 이동했습니다. worker에는 설정 기반 arm 목록, runtime
  dependency 조립, metric과 기존 Celery task 이름·`rail` route를 남겨 1,470줄로 줄였습니다.
- 리뷰 보정: provider 입력을 최초 등장 순서로 중복 제거해 adapter 덮어쓰기·누수와 동일 provider
  병렬 실행을 차단했습니다. 만료 pass는 `Watch.id` 순서로 잠근 뒤 `apply_watch_transition`을 사용해
  후보 부분 만료·상태·이력·outbox를 한 번에 commit합니다. 이로써 무관한 stale 예약 시도 유무에
  따라 부분 만료가 commit 또는 rollback되던 기존 UoW 결함을 제거했습니다.
- 확인된 검증: 독립 웹 리뷰는 P0~P2 회귀 없음, 보정 뒤 API 재리뷰는 P0~P3 지적 없음이었습니다.
  실제 DB 계약 테스트가 reconciliation outcome·credential·due·external provider와 watch status·
  `next_check_at`, `finished_at`·`created_at` 순서를 포함·제외 행으로 검증합니다. 웹 ESLint 오류 0·고정
  경고 18, strict typecheck, Vitest 67개 파일·479건, production build, Sites 4건을 통과했습니다. API
  전체 pytest 1,034건, Ruff `E/F/I`, format ratchet 65개, module boundary와 `git diff --check`를
  통과했습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 남은 핵심 부채: `NewWait` 나머지 단계·App shell과 Home·Reservations·Auth의 strict 경계,
  legacy JS/JSX, 전역 CSS 분리, worker의 더 깊은 observation/reservation 정책과 provider 역할 분리입니다.

### 2026-08-05 열한 번째 구조 슬라이스

- 전역 CSS 1차 경계: 6,648줄 `styles.css`를 import-only 진입점으로 바꾸고 `:root` token은
  `styles/tokens.css`, reset·기본 요소·focus는 `base.css`, app·navigation·page·공용 button shell은
  `shell.css`, 화면 규칙과 그 위치의 local media/container query·keyframes는 `features.css`, 파일
  말미의 교차 화면 breakpoint와 공통 reduced-motion은 `responsive.css`로 이동했습니다. selector·
  선언·중복을 정리하지 않고 원래 규칙 순서를 보존했으며, separator 빈 줄만 `git diff --check`에
  맞게 다음 파일 시작에 둡니다. 전환 전 Git blob과 다섯 파일 결합은 각각 113,950바이트이며
  byte-for-byte 같습니다.
- 구조·브라우저 계약: `responsiveLayoutContract.test.ts`는 import 순서, 각 파일 시작·끝 경계와
  dependency 없는 brace-depth block 추출로 정확한 rule/container 선언을 검사합니다. 신규
  `responsive-css.spec.ts`는 정상 Home API mock과 긴 자동예매 정책을 사용해 1,440×1,000,
  320×844, 720×500(1,440×1,000의 200% 확대 reflow 등가)에서 부모·실제 자식의 자체 overflow와
  viewport 경계, watch 내부 네 영역 비겹침, 모바일 fixed navigation 가림, booking CTA·switch·
  icon·하단 navigation의 실제 44×44px를 Chromium으로 확인합니다. 미처리 API·console·page error는
  0건이어야 하며 새 spec은 기본 `test:e2e`와 `verify` 경로에 포함됩니다.
- 독립 리뷰: 최초 리뷰의 기본 gate 누락, `overflow-x: clip` 허위 통과, 오류 상태 mock, y축·44px·
  CSS 블록 경계 공백을 모두 보정했습니다. 세 차례 보정 재리뷰 뒤 P0~P3 지적 사항이 없음을
  확인했습니다.
- 확인된 검증: ESLint 오류 0·고정 경고 18, strict typecheck, Vitest 67개 파일·481건, production
  build, Sites 4건, 기본 Playwright E2E 14건을 통과했습니다. responsive focused는 desktop·mobile
  프로젝트 합계 6건, 기존 mocked journey는 4건을 통과했고 `git diff --check`도 통과했습니다.
- 검증 범위: CSS 결합이 기존과 바이트 단위로 같고 320px·200% 확대 reflow 등가 Chromium 계약을
  확인했습니다. 실제 headed Chrome의 native 200% zoom과 Step 3·Official Handoff geometry를 이
  슬라이스에서 새로 수동 확인했다고 기록하지 않습니다. CSS·테스트·문서만 바뀌어 Compose 이미지
  재빌드·재생성은 수행하지 않습니다.
- 남은 핵심 부채: `features.css`의 기능별 재소유·중복 정리, 실제 native 200%와 Step 3·handoff의
  확장 geometry 회귀, legacy JS/JSX와 App의 화면 조립, API worker/provider의 더 깊은 분리입니다.

### 2026-08-05 열두 번째 구조 슬라이스

- 예약 페이지 경계: App에 있던 예약 화면 조립을 strict
  `features/reservations/ReservationsPage.tsx`로 이동했습니다. 페이지는 typed
  `ReservationListWatch` 목록과 구체적인 `onCreate`·`onDelete` callback만 받고, 공용
  `PageHeader`, `ReservationSummary`, `ReservationList`를 조립합니다. 기존 DOM·class·문구·순서와
  빈 목록·요약·정렬·기한 경과·terminal 삭제 표현은 그대로 유지했습니다. `App.jsx`는 998줄입니다.
- 공식 handoff와 호환 경계: optional 공식 URL은 없으면 열지 않고, 있으면 사용자 클릭 뒤
  `_blank`·`noopener,noreferrer`로만 엽니다. production App은 `ReservationsPage`를 직접 사용하되,
  기존 공개 `Reservations`의 `{ watches, onNavigate, onDelete }` props는 App의 얇은 adapter에서
  `onNavigate("new")`로 변환해 유지합니다. 최초 alias 방식이 이 구 props를 깨뜨린다는 독립 리뷰
  P2를 행동 테스트와 함께 보정한 뒤 재리뷰 지적 사항이 없음을 확인했습니다.
- 테스트 재소유: App의 예약 집계·공식 CTA, 기한 경과, terminal 삭제 테스트를
  `ReservationsPage.test.tsx`로 옮기고 strict page `onCreate`, legacy wrapper navigation, 공식 URL
  보안 옵션을 보강했습니다. App에는 실제 하단 navigation에서 예약 페이지로 진입하는 smoke를
  남겼고 테스트 선언 수는 줄지 않았습니다.
- 확인된 검증: 웹 ESLint 오류 0·고정 경고 18, strict typecheck, Vitest 68개 파일·484건,
  production build, Sites 4건, 기본 Playwright E2E 14건과 `git diff --check`를 통과했습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 남은 핵심 부채: App shell·Home·Auth와 `NewWait` 나머지 단계, legacy JS/JSX, feature CSS 추가
  재소유, API expiry·reservation·observation pipeline과 provider 역할 분리입니다.

### 2026-08-05 열세 번째 구조 슬라이스

- watch expiry application: worker의 만료 가능 상태·후보 상태·KST legacy deadline과 ordered expiry
  pass를 `watch_management/expiry_application.py`로 이동했습니다. due application은 기존 session과
  UTC 시각을 넘기고 worker는 `WatchExpiryDependencies(apply_watch_transition=...)`와 private wrapper만
  조립합니다. `worker.py`는 1,395줄입니다.
- UoW·정책 보존: `Watch.id` 오름차순 조회 뒤 상태를 다시 확인하며 각 행을 `FOR UPDATE`로 잠급니다.
  후보가 있으면 `travel_date`를 별도 gate로 쓰지 않고 fresh delay/open·closed·unknown horizon과
  실제 출발 시각을 기존 `decide_operational_expiry`로 판단합니다. 후보 없는 legacy watch는
  `Asia/Seoul` 시간창과 자정 교차 익일 보정을 사용합니다. 후보 부분 만료·watch 상태·이력·outbox는
  pass당 한 commit에 묶고 예외 시 명시적으로 모두 rollback합니다.
- 테스트 재소유: worker의 세부 expiry 5함수·6케이스를 새 owner로 이동하고 ID 처리 순서·PostgreSQL
  `FOR UPDATE` compile 계약·전체 rollback·후보 없는 KST 자정 경계 4건을 추가했습니다. worker에는
  due dependency wiring, 전체 활성 상태의 만료·이력·알림 outbox, 늦은 예약 결과 fencing 같은 통합
  계약을 유지했습니다. 전체 테스트 수는 이전 단계보다 4건 순증했습니다.
- 의존성 경계: expiry application을 worker-independent 목록에 넣고 Celery·FastAPI·worker 역의존과
  config·metrics·providers·provider execution lease 직접 import를 module-boundary test로 차단했습니다.
  독립 리뷰는 P0~P3 지적 사항 없이 완료됐습니다.
- 확인된 검증: API focused 91건, 전체 pytest 1,038건, Ruff `E/F/I`, format ratchet 65개,
  module boundary와 `git diff --check`를 통과했습니다. 전체 pytest의 기존 Starlette/httpx 전환 경고
  1건은 이번 변경과 무관한 dependency 부채로 남아 있습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 남은 핵심 부채: reservation execution application, watch-group observation application, provider
  역할별 계약, App Home·shell·Auth와 `NewWait` 나머지 strict 경계입니다.

### 2026-08-05 열네 번째 구조 슬라이스

- Home 페이지 경계: App의 `WatchManagementHero`와 결제 보류·활성 감시 목록 조립을 strict
  `features/home/HomePage.tsx`로 이동했습니다. production App은 구체적인 `onCreate`·
  `onViewReservations`·`onOpenRailAccounts`와 typed 좌석 발견 action renderer를 주입하며,
  호환 props를 명시한 `App.jsx`는 976줄입니다.
- 공식 handoff와 호환 경계: 실제 `OfficialHandoff` renderer와 `activeWatchHandoffTrain` 변환은 App에
  남겼고, 공개 `Home` adapter의 navigation·단일 `paymentWatch`·optional refresh 계약을 보존했습니다.
  `HomeCompatibilityProps`로 JavaScript 기본값의 잘못된 협소 추론을 바로잡아 런타임과 공개 타입을
  일치시켰습니다.
- 테스트 재소유: Home의 결제 URL fail-closed·사용자 행동 뒤 안전한 새 창 열기·기한 경과 fallback과
  좌석 발견 renderer 주입 계약을 `HomePage.test.tsx`로 옮겼습니다. legacy 단일 결제 대상과 refresh
  callback 생략·제공 계약을 보강하고, 실제 handoff 매핑은 App 통합 경계에 유지했습니다.
- 독립 리뷰: 최초 P3 테스트 보강 권고를 반영한 뒤 전체 diff를 다시 검토했으며 P0~P3 잔여 지적
  사항이 없음을 확인했습니다.
- 확인된 검증: 웹 ESLint 오류 0·고정 경고 18, strict typecheck, Vitest 69개 파일·491건, production
  build, Sites 4건, 기본 Playwright E2E 14건을 통과했습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 남은 핵심 부채: reservation execution application, watch-group observation application, provider
  역할별 계약, App shell·Auth와 `NewWait` 나머지 strict 경계입니다.

### 2026-08-05 열다섯 번째 구조 슬라이스

- 예약 실행 application: worker의 예약 target·공식 확인·인증 상태 투영·선점·provider 실행·결과 적용을
  FastAPI·Celery 비의존 `reservations/execution_application.py`로 이동했습니다. 최소
  `ReservationExecutionTarget`과 `ReservationProvider` protocol, runtime dependency bundle을 두고,
  worker는 observation target 변환과 concrete service·provider 오류·SRT exact source 조립만 맡습니다.
  각 dependency callback도 실제 positional·keyword·return 계약을 명시한 `Protocol.__call__`로 고정하고,
  인증 상태 갱신 port는 composition root가 `commit=False`를 봉인하는 `None` 반환 adapter로 연결했습니다.
  `worker.py`는 1,108줄, 새 application은 540줄입니다.
- transaction·잠금 보존: claim은 외부 provider에서 `account -> watch -> candidate -> circuit`, 결과는
  필요한 경우 `account -> watch -> candidate -> attempt` 순서로 잠급니다. PENDING attempt·transition·
  claim outbox를 provider I/O 전에 commit하고 예약 호출은 생성된 episode당 한 번만 수행합니다. 결과
  CAS·confirmation·상태·outbox는 별도 transaction으로 commit하며 실패하면 부분 결과만 rollback하고
  선점 claim은 보존합니다. 늦은 결과는 terminal 상태를 되살리지 않고 UNKNOWN·manual-check로 닫습니다.
- 테스트 재소유·보강: 기존 confirmation 2건과 outcome 표 테스트를 새 owner로 옮기고 PostgreSQL
  `FOR UPDATE` compile, 기존 request hash parity, 독립 session의 claim·outbox 가시성, 상태 gate를 다시
  통과한 동일 episode의 attempt/outbox/provider 1회 fence, 결과 transaction rollback과 result outbox
  부재, provider 호출 중 credential generation 4→5 교체 뒤 stale 결과 CAS 보존을 직접 검증했습니다.
  테스트 파일은 82개로 유지되고 전체 수집은 1,038건에서 1,042건으로 4건 증가했습니다.
- 의존성 경계: application의 worker·Celery·FastAPI 역의존과 config·metric·observation·provider account·
  실행 임대·provider registry·services·SRT concrete source 직접 import를 차단했습니다. worker에는 due
  observation·episode 계산·provider lease·adapter 생성/drain/close/release가 남아 있습니다.
- 확인된 검증: focused pytest 80건, 보강 focused 18건, 전체 pytest 1,042건, Ruff `E/F/I`, format
  ratchet 64개, module boundary와 `git diff --check`를 통과했습니다. 독립 보정 재리뷰 뒤 P0~P3 잔여
  지적 사항이 없음을 확인했습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: SQLite application 테스트와 PostgreSQL SQL compile 계약은 확인했지만, 동일 episode의
  여러 process 동시 실행, 로그인 저장과 예약 실행의 실제 교착 부재, credential 교체와 늦은 결과의
  실제 PostgreSQL concurrent scheduling은 아직 운영·CI 실DB 검증 항목입니다.
- 남은 핵심 부채: watch-group observation application, provider 역할별 계약, App shell·Auth와
  `NewWait` 나머지 strict 경계입니다.

### 2026-08-05 열여섯 번째 구조 슬라이스

- 관찰 그룹 application: worker의 provider 확인, 작업 준비·주기 선점, source cooldown 연기,
  동일 조건 조회 병합, provider 오류 정규화, 작업별 관찰 저장·상태 요약, 예약 episode 선택과
  예약 실행 위임을 FastAPI·Celery 비의존 `observations/group_application.py`로 이동했습니다.
  `ObservationTarget`, 최소 `ObservationAdapter`와 실제 호출 서명을 고정한 dependency protocol을
  사용하며, 예약 delegate는 adapter를 노출하지 않고 target만 받도록 composition closure로
  연결했습니다. `worker.py`는 실행 임대 획득·해제, concrete adapter registry, drain·close,
  due/Celery·설정·metric 조립만 남은 498줄입니다.
- 실행 임대·잠금 보강: 외부 관찰 호출 전과 저장·예약 위임 직전에 현재 임대를 다시 확인합니다.
  prepare·cooldown 연기·회로 확인·관찰 저장·회로 반영 transaction은 실행 임대 행을 먼저
  `FOR UPDATE`한 뒤 watch·candidate·circuit을 잠가 stale owner의 쓰기를 막고 잠금 순서를
  일관되게 유지합니다. 여러 작업을 한 번에 연기할 때는 `Watch.id` 순서로 잠그며 PostgreSQL
  compile 계약에서 `ORDER BY watches.id FOR UPDATE`를 고정했습니다.
- 테스트·경계: 새 owner 테스트에서 동일 요청 1회 조회와 작업별 관찰 투영, 일치 좌석 등급 부재의
  fail-closed 오류, episode가 묶인 winner 위임, 잠긴 임대 상실 시 mutation 0건, 관찰 저장 실패의
  전체 rollback, 다중 작업의 결정적 PostgreSQL lock SQL을 검증했습니다. worker의 테스트 전용
  target alias·episode·defer wrapper는 제거했고 테스트가 실제 owner를 직접 참조합니다. 새 application은
  worker·Celery·FastAPI 역의존과 config·database·metric·provider registry·provider account·실행 임대·
  services·예약 application·KORAIL/SRT concrete 실행 모듈 직접 import를 경계 테스트로 차단합니다.
- 확인된 검증: focused pytest 74건, 전체 pytest 1,048건, Ruff `E/F/I`, format ratchet 64개,
  module boundary와 `git diff --check`를 통과했습니다. 전체 pytest에는 기존 Starlette/httpx
  deprecation 경고 1건만 남았습니다. 독립 재감사에서 P0·P1 잔여 지적은 없었습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: SQLite 회귀와 PostgreSQL SQL compile은 확인했지만, 두 실제 PostgreSQL session에서
  lease takeover가 guarded transaction commit까지 대기하는지와 다중 worker 교착 부재는 아직
  운영·CI 실DB 검증 항목입니다.
- 남은 핵심 부채: provider 역할별 계약, 실제 PostgreSQL 실행 임대 경합 검증, App shell·Auth와
  `NewWait` 나머지 strict 경계입니다.

### 2026-08-05 열일곱 번째 구조 슬라이스 A

- provider 역할 계약: `provider_contracts.py`에 capability source, timetable, observation,
  reservation, confirmation, lifecycle의 최소 `Protocol`과 composition 경계용 실행·정합화 계약을
  추가했습니다. `ProviderUnavailable`과 `RouteValidationError`도 이 모듈의 단일 canonical 객체로
  이동하고 `providers.py`가 같은 객체를 다시 export해 기존 import와 예외 catch identity를
  보존합니다.
- 의존 방향: timetable registry는 `TimetableProvider`, 실행 registry는 `ExecutionProvider`를 반환하며,
  due pipeline·관찰 그룹·예약 실행·정합화 application과 worker annotation을 실제 소비 역할로
  좁혔습니다. concrete `RailProviderAdapter` 계층, registry 분기, capability 계산과 같은 task에서
  운영사별 adapter 객체 하나를 arm→여러 관찰 그룹→정합화까지 공유하는 수명주기는 변경하지
  않았습니다. drain·close callback은 `ProviderLifecycle`을 받아 역할별 좁은 view와도 타입 방향이
  맞도록 했습니다.
- 경계·회귀: provider 계약 모듈은 표준 라이브러리와 domain·schema·confirmation import만 허용하는
  allowlist를 적용했습니다. due·정합화 application에는 provider registry와 KORAIL/SRT concrete runtime
  모듈의 역의존을 차단했습니다. 기존 `RailProviderAdapter`·`get_provider()`·concrete class identity는
  호환 경계로 유지합니다. KORAIL·SRT 각각 기본, 관찰 3중 opt-in, 예약 4중 opt-in의 공개 capability
  golden matrix에서 timetable/link는 유지하고 seat monitoring·reservation만 기존 교집합으로 열리는지
  6개 조합을 검증했습니다.
- 확인된 검증: provider·application·worker focused pytest 166건, 전체 pytest 1,056건, Ruff `E/F/I`,
  format ratchet 63개, module boundary와 `git diff --check`를 통과했습니다. 전체 pytest에는 기존
  Starlette/httpx deprecation 경고 1건만 남았습니다. 독립 재감사에서 P0·P1 잔여 지적은 없었습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: Python Protocol의 구조적 대입 가능성은 설계·회귀·AST 경계로 검토했지만 API에
  mypy/pyright gate는 아직 없습니다. 이번 단계는 역할별 새 runtime 객체를 만들거나 execution adapter의
  가짜 timetable/station 메서드를 제거한 단계가 아니며, concrete base·구현 파일 분리는 후속입니다.
- 남은 핵심 부채: provider execution base와 concrete 구현·registry의 물리 분리, Python 정적 타입 gate
  도입 판단, 실제 PostgreSQL 실행 임대 경합 검증, `NewWaitPage`와 App shell의 strict 경계입니다.

### 2026-08-05 열일곱 번째 구조 슬라이스 B

- 새 대기 페이지 소유권: 여정·조건·열차 단계 렌더링과 station catalog·timetable search·좌석별
  registration hook 조립을 strict `features/new-wait/NewWaitPage.tsx`로 이동했습니다. production
  App은 이 페이지를 직접 사용하고 실제 공식 handoff component를 typed prop으로 주입합니다.
  `new-wait`는 `app`이나 `official-handoff` feature를 역으로 import하지 않습니다.
- 호환·타입 경계: 기존 공개 `NewWait`는 동일한 optional 기본값과 실제 `OfficialHandoff`를 주입하는
  얇은 adapter로 보존했습니다. 등록 완료·취소 callback을 공개 타입으로 고정하고,
  `TimetableSearchForm`과 내부 폼의 불필요한 `Record<string, unknown>` index signature를 제거해 외부
  응답은 경계 mapper에서만 열고 기능 폼은 닫힌 계약으로 유지합니다. App은 976줄에서 639줄로
  줄었습니다.
- 테스트 소유권: 새 대기 행동 28건을 `NewWaitPage.test.tsx`로 이동하고 App에는 조립 통합과 legacy
  adapter 계약을 남겼습니다. 두 파일의 관련 테스트는 55건에서 56건으로 한 건 늘었으며 stale 응답,
  provider 부분 성공, station name/node ID 정합성, exact watch ID 취소, evidence 1회 복구,
  키보드·초점·ARIA·320px 계약을 보존합니다.
- 확인된 검증: ESLint 오류 0개·고정된 기존 경고 17개, strict typecheck, Vitest 70개 파일·492건,
  production build, Sites 4건, 기본 Playwright E2E 14건을 통과했습니다. 독립 재감사에서는 P0~P2
  잔여 지적이 없었습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: CSS·DOM 순서·selector는 변경하지 않았습니다. `App.jsx`는 아직 `checkJs=false`이므로
  production caller props의 정적 검증은 최종 `App.tsx` 전환 전까지 남으며, 즉시 등록→collection
  반영→재진입 hydrate→정확한 ID 취소 App 통합 테스트로 현재 runtime seam을 고정했습니다.
- 남은 핵심 부채: App의 알림·화면 전환 조립과 Auth·OfficialHandoff 최종 feature 경계, App과 legacy
  JS/JSX 테스트의 TSX 전환, provider concrete base·구현·registry 물리 분리, 실제 PostgreSQL 실행 임대
  경합 검증입니다.

### 2026-08-05 열여덟 번째 구조 슬라이스 A

- 알림 채널 application hook: App의 채널 목록·Web Push 상태, 인증 뒤 조회와 401 전달, focus 갱신·
  cleanup, 저장·활성화·시험·기기 연결 명령을 strict
  `features/settings/useNotificationChannelSettings.ts`로 이동했습니다. hook은 auth·app feature를
  import하지 않고 인증 만료와 toast를 callback으로 받으며 transport는 `api/notifications.ts`를 직접
  사용합니다.
- 행동 보존: 기존 저장의 kind 기준 upsert, toggle의 ID 기준 행 교체·순서 보존, Web Push 비활성화의
  서버 channel disable→브라우저 subscription 해제→상태 재조회 순서, 시험 전송 전 permission·subscription
  fail-closed와 logout의 채널 목록 초기화를 그대로 유지했습니다. hook reset은 Web Push 화면 상태도
  `checking`으로 함께 되돌려 재로그인 전 stale 기기 표시를 막습니다. 같은 kind 복수 채널 중 비대상
  행을 toggle이 지우지 않는 계약도 회귀 테스트로 고정했습니다. 오류는 `unknown`에서 안전한 사용자
  문구로 정규화합니다.
- 테스트 소유권: 최초 live 조회, unmount 뒤 stale 결과 폐기, 401 인증 만료, focus refresh·listener
  cleanup, Web Push 상태 읽기 실패, create/update 실패, 비활성화 순서, 시험 전송 fail-closed, 기존 demo
  toggle·test-send·connect 경로, reset과 성공 create/update·toggle·시험·신규/기존 Web Push 연결의
  17건을 새 hook 테스트가 소유합니다. App은 이 controller와 watch 등록·logout을 연결하는 composition
  root로 남으며 639줄에서 538줄로 줄었습니다.
- 확인된 검증: ESLint 오류 0개·고정된 기존 경고 16개, strict typecheck, Vitest 71개 파일·509건,
  production build, Sites 4건, 기본 Playwright E2E 14건을 통과했습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: 이번 단계는 CSS·DOM·transport payload·demo 정책을 바꾸지 않은 구조 이동입니다. 개발
  demo에서 Telegram·Webhook editor 저장이 기존처럼 live create/update API를 호출하는 문제, 같은 알림
  종류의 복수 채널 허용 여부, mutation 401의 인증 만료 전달과 logout 뒤 늦은 mutation을 폐기할 epoch
  fence는 별도 행동·제품 계약으로 남겼습니다.
- 남은 핵심 부채: provider account·UI preference orchestration, typed app navigation과 Auth 경계,
  App·legacy JS/JSX의 TSX 전환, provider compatibility facade 기반 물리 분리, 실제 PostgreSQL 실행 임대
  경합 검증입니다.

### 2026-08-05 열여덟 번째 구조 슬라이스 B

- 철도 계정 application hook: App의 계정·runtime 상태, 최초 인증/demo 로드, 설정의 runtime polling,
  저장·삭제, watch 인증 전이 refresh와 Home account status 투영을 strict
  `features/settings/useProviderAccountSettings.ts`로 이동했습니다. feature는 App view를 import하지 않고
  `runtimePollingEnabled` boolean과 toast를 주입받으며 API owner를 직접 사용합니다.
- 상태 계약: `loaded`와 `loading`을 내부에서 분리해 아직 확인하지 못한 계정은 Home에서 `null`로
  fail-closed하고, 계정 없음·미설정·비활성만 `not_checked`로 표시합니다. polling은 인증된 live 세션의
  철도 계정 section에서만 즉시 한 번과 15초 주기로 실행하며 cleanup 뒤 멈춥니다. runtime 조회 실패는
  마지막 성공 상태를 지우지 않습니다.
- 명령 계약: 저장은 provider별 pending, 성공 계정 맨 앞 이동·동일 provider 제거, runtime best-effort
  refresh와 오류 rethrow를 유지합니다. 삭제는 행 순서와 login method를 보존하고 configured·enabled·
  masked ID·credential version·auth status·시각만 기존 값으로 초기화합니다. logout reset은 계정뿐 아니라
  runtime·loading·pending 화면 상태도 비웁니다.
- demo 타입 경계: `demoProviderAccounts`와 `demoProviderRuntimeStatuses`를 각각 canonical
  `ProviderAccount[]`·`ProviderRuntimeStatus[]`로 고정하고 `loginMethod: null`을 명시했습니다. credential
  원문이나 비밀번호는 fixture·hook state·toast에 저장하지 않습니다.
- 테스트 소유권: 인증 전 I/O 차단, live 초기 성공·실패·unmount stale 차단, demo, 인증 전이,
  polling 즉시/15초/cleanup/실패 보존, live·demo 저장/삭제와 pending·오류, selector·reset의 17건을
  새 hook 테스트가 소유합니다. 기존 App의 인증 복구 재조회와 최신 account status 통합 계약도
  유지했으며 App은 538줄에서 417줄로 줄었습니다.
- 확인된 검증: ESLint 오류 0개·고정된 기존 경고 12개, strict typecheck, Vitest 72개 파일·526건,
  production build, Sites 4건, 기본 Playwright E2E 14건을 통과했습니다. 독립 재감사에서 도입
  P0~P3 잔여 지적은 없었습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: 초기 live effect의 상태 시작만 sync effect warning을 새로 만들지 않도록 한 microtask 뒤
  수행하며 이후 account→loaded→runtime 순서는 같습니다. session/request epoch, runtime latest-wins,
  provider별 복수 pending과 mutation 401 전달은 기존 선행 부채로 후속 안전성 슬라이스에 남겼습니다.
- 남은 핵심 부채: UI preference orchestration, typed app navigation과 Auth 경계, App·legacy JS/JSX의
  TSX 전환, provider compatibility facade 기반 물리 분리, 실제 PostgreSQL 실행 임대 경합 검증입니다.

### 2026-08-05 열여덟 번째 구조 슬라이스 C

- UI preference application hook: App의 기본 환경설정, live 인증 뒤 조회, demo/live 저장, saving 상태와
  logout reset을 strict `features/settings/useUiPreferencesSettings.ts`로 이동했습니다. hook은 API
  ViewModel만 사용하고 App·watch·new-wait feature를 import하지 않습니다.
- 소비 경계: App은 `timetableRefreshIntervalSeconds` 하나를 watch collection polling과 새 대기 시간표
  refresh에 함께 전달하고, 전체 preferences·saving·save는 Settings에 전달합니다. 서버에 저장하는
  `seatObservationIntervalSeconds`를 클라이언트 polling 주기로 잘못 사용하지 않습니다.
- 행동 보존: 기본값은 화면 5초·좌석 관측 5초·epoch 시각이며 인증 전과 demo에서는 GET을 호출하지
  않습니다. live GET의 늦은 성공·오류는 effect cleanup 뒤 폐기하고, 저장은 live PATCH 또는 demo local
  timestamp 결과를 상태·반환값으로 사용합니다. 성공 toast, 원본 오류 rethrow와 finally saving 해제를
  유지하며 reset은 saving도 false로 정리합니다.
- 테스트 소유권: canonical 기본값, unauth/demo I/O 차단, live load 성공·Error/unknown 실패,
  unmount·auth lifecycle stale 결과 폐기, live/demo 저장, Error/unknown rethrow, reset과 callback identity의
  15건을 새 hook 테스트가 소유합니다. 기존 AppLive·TimetableRefreshSettings·Settings 회귀도 유지했으며
  App은 417줄에서 384줄로 줄었습니다.
- 확인된 검증: ESLint 오류 0개·고정된 기존 경고 12개, strict typecheck, Vitest 73개 파일·541건,
  production build, Sites 4건, 기본 Playwright E2E 14건을 통과했습니다. 독립 재감사에서 코드 회귀
  P0~P3는 발견되지 않았습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: App이 아직 `checkJs=false`라 non-default 간격이 두 consumer에 전달되고 logout reset되는
  조립은 owner·consumer 테스트와 소스 검토로 분리 확인했으며 단일 App 통합 테스트로는 고정하지
  않았습니다. initial GET/save 교차, concurrent save, logout 뒤 late 결과와 mutation 401은 기존 선행
  부채로 후속 안전성 슬라이스에 남겼습니다.
- 남은 핵심 부채: typed app navigation과 Auth·OfficialHandoff 조립 경계, App·legacy JS/JSX의 TSX
  전환, provider compatibility facade 기반 물리 분리, 실제 PostgreSQL 실행 임대 경합 검증입니다.

### 2026-08-05 열아홉 번째 구조 슬라이스 A

- typed navigation: `app/useAppNavigation.ts`가 `home|new|reservations|settings` union, settings의 mount
  초기 section과 runtime polling용 활성 section, stable navigate·section callback을 소유합니다. settings
  이동은 기본 `notifications` 또는 명시 section을 두 상태에 함께 적용하고, 다른 view 이동은 section을
  보존하며 모든 navigate가 기존 smooth scroll을 정확히 한 번 수행합니다.
- app shell: `app/AppShell.tsx`가 desktop sidebar, mobile header, bottom navigation과 overlay slot을
  strict props로 소유합니다. `.app-shell`의 `aside → main → bottom nav → overlay`, main의
  `mobile-header → page` 순서와 nav 4개 문구·아이콘·class·ARIA·Tailscale badge를 그대로 유지했습니다.
  `AppNotificationCenter`는 BottomNav 다음의 shell 내부 direct child로 남아 공식 dialog inert 범위가
  바뀌지 않습니다.
- 조립 방향: top-level app은 settings의 `SettingsSection`을 type-only로 참조하며 어떤 feature도 app을
  역으로 import하지 않습니다. App은 navigation hook을 인증 return보다 위에서 호출해 logout·AuthGate
  전환 중 view 상태를 보존하고, page 조건부 조립과 기존 공개 compatibility export를 유지합니다.
- 테스트 소유권: shell DOM·overlay 순서, named navigation 두 곳의 항목·active class·callback·mobile
  header와 navigation 초기값·settings default/explicit 전이·section 보존·smooth scroll·callback identity의
  10건을 새 owner 테스트가 소유합니다. 기존 App·Settings·Home·responsive·official inert 회귀도
  유지했으며 App은 384줄에서 321줄로 줄었습니다.
- 확인된 검증: ESLint 오류 0개·고정된 기존 경고 12개, strict typecheck, Vitest 75개 파일·551건,
  production build, Sites 4건, 기본 Playwright E2E 14건을 통과했습니다. 독립 재감사에서 도입
  P0~P3 코드 회귀는 발견되지 않았습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: Settings 화면에서 같은 Settings nav를 다시 누를 때 mount-only UI section과 polling용 active
  section이 어긋나는 기존 문제, `aria-current`, URL/history routing, mobile 알림 버튼 동작과 reduced-motion
  scroll은 이번 구조 이동에서 바꾸지 않고 후속 행동·접근성 슬라이스로 남겼습니다.
- 남은 핵심 부채: Auth·OfficialHandoff 조립과 App.tsx 전환, legacy JS/JSX·allowJs 제거, provider
  compatibility facade 기반 물리 분리, 실제 PostgreSQL 실행 임대 경합 검증입니다.

### 2026-08-05 열아홉 번째 구조 슬라이스 B

- strict App 전환: forwarding shim 없이 `App.jsx`를 `App.tsx`로 교체하고 main·App 통합·Home·예약·
  설정 테스트 import를 extensionless 경로로 통일했습니다. production caller props가 strict 검사를
  받으며 `App.tsx`는 page/controller 조립과 watch 등록 완료만 소유하는 246줄 진입점입니다.
- app 조립 경계: `app/AppAuthenticationBoundary.tsx`가 loading·AuthGate·인증 child 선택만 담당하고,
  모든 controller hook은 이전처럼 인증 분기 위에서 호출됩니다. `app/useAppLogout.ts`는 live 요청 뒤
  성공·실패 공통 cleanup 순서와 demo 요청 생략을, `app/HomeSeatFoundOfficialHandoff.tsx`는 Home watch의
  공식 인계 train 변환·KST 출발 시각·route fallback·CTA 조립을 소유합니다.
- compatibility와 selector: `app/AppCompatibility.tsx`가 Home·NewWait·Reservations·PaymentHero adapter를
  typed props로 소유하고, Settings·WatchRow·OfficialHandoff·seat evidence는 원본 identity로 다시
  export합니다. active 상태 판정은 `features/app/watchSelectors.ts`로 이동했습니다.
- canonical 경계 강화: 좌석별 등록 completion이 전달하는 열차는 watch 생성에 필요한 이름·열차번호·
  출도착 시각·표시 시각·공식 URL을 canonical timetable 계약으로 보장합니다. generic watch snapshot의
  임의 index signature는 실제 전이 탐지 필드로 좁혔고, watch 생성 API는 canonical payload와 기존
  부분 객체 계약 테스트를 overload로 함께 보존합니다.
- 테스트 소유권: 인증 경계 4건, logout 4건, Home 공식 인계 3건, compatibility·selector 6건을 새 strict
  owner 테스트가 소유합니다. 기존 live `DEMO_MODE` hoisted mock 순서, 공개 export identity, portal inert,
  등록·취소·Home·예약·설정 회귀는 그대로 유지했습니다. feature→app 역의존 ratchet과 선택 열차 필수
  필드 fail-closed 테스트도 추가했습니다.
- 확인된 검증: ESLint 오류 0개·고정된 기존 경고 12개, strict typecheck, Vitest 79개 파일·570건,
  production build, Sites 4건, 기본 Playwright E2E 14건을 통과했습니다. build의 기존 500kB 초과 chunk
  warning은 이번 구조 이동에서 새로 만들거나 숨기지 않았습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: `App.test.jsx`를 포함한 잔여 JS/JSX 테스트와 `allowJs=true`는 아직 남아 있습니다. 명시적
  logout 요청 실패는 로컬 cleanup 뒤 원본 오류를 재전파하고, 401은 명시적 logout과 달리 feature
  상태를 보존하며, 재인증 시 기존 view로 복귀하는 현재 계약은 행동 변경 없이 유지했습니다. 설정
  버튼부터 AuthGate까지의 실제 `<App>` logout 관통은 logout hook과 Settings owner 테스트로 나눠
  검증했으며 단일 통합 테스트로는 아직 고정하지 않았습니다.
- 남은 핵심 부채: legacy JS/JSX 테스트·allowJs 제거, provider compatibility facade 기반 물리 분리,
  실제 PostgreSQL 실행 임대 경합 검증과 설정 resource 요청 epoch 안전성입니다.

### 2026-08-05 스무 번째 구조 슬라이스 A

- provider base 소유권: `provider_adapters/base.py`가 공식 URL map과 공통 `RailProviderAdapter` ABC의
  capability guard, provider mismatch, 기본 observation/reservation/confirmation·drain·close 정책을
  소유합니다. `provider_adapters/execution.py`는 승인된 실행 구현이 없는 경우의 명시적
  `FailClosedExecutionAdapter`만 먼저 소유합니다.
- compatibility facade: 기존 `providers.py`는 base·fail-closed class와 URL map을 wrapper/subclass 없이
  직접 import해 같은 객체로 다시 export합니다. 따라서 기존 import, `isinstance`, ApprovedProviderAdapter
  상속, canonical provider 예외 catch identity와 registry 반환 타입은 유지됩니다. adapter package의
  `__init__.py`는 대형 barrel을 만들지 않습니다.
- 수명주기 보존: TAGO client와 process singleton, Official/Mock/Experimental, KORAIL/SRT 실행 adapter,
  credential loader 기본 binding과 모든 registry 함수는 이번 슬라이스에서 이동하지 않았습니다.
  연속 실행 registry 호출의 fresh adapter·lazy source와 KORAIL/SRT 시간표 adapter의 같은 TAGO singleton
  공유를 회귀 테스트로 고정했습니다.
- 의존성 경계: module-boundary gate가 `provider_adapters/** -> providers.py` 역의존을 차단합니다.
  facade와 canonical class·URL map 객체 identity, ApprovedProviderAdapter base, fail-closed capability를
  새 owner 테스트 5건으로 확인했으며 `providers.py`는 1,284줄에서 1,155줄로 줄었습니다.
- 확인된 검증: provider/contracts/approved/due-pipeline/worker 집중 pytest 151건, API 전체 pytest
  1,061건, Ruff `E/F/I`, format ratchet 63개와 `git diff --check`를 통과했습니다. 기존
  Starlette/httpx deprecation 경고 1건은 유지됐고 독립 재감사에서 도입 P0~P3 회귀는 없었습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: class의 `__module__`은 의도대로 새 canonical owner 경로로 바뀌었으며 저장소 안에서
  pickle·qualname 의존 사용처는 확인되지 않았습니다. 가짜 timetable/stations 추상 stub 제거는 행동
  계약 변경이므로 이번 물리 이동에 섞지 않았습니다.
- 남은 핵심 부채: timetable support·TAGO singleton과 Official/Mock adapter, 운영사 실행 adapter,
  provider registry application의 단계별 물리 이동, 실행 전용 역할 base 설계, Python 정적 타입 gate와
  실제 PostgreSQL 실행 임대 경합 검증입니다.

### 2026-08-05 스무 번째 구조 슬라이스 B

- timetable support: `provider_adapters/timetable_support.py`가 역명 공백·`역` suffix 정규화, naive/aware
  시각의 KST 서비스일 변환과 역순·다른 날짜 fail-closed, 공식 시간표 fallback의 일반실·특실
  `unknown/not_observed` 좌석·공식 확인/감시 등록 action 투영을 소유합니다.
- TAGO parser: `provider_adapters/tago.py`가 immutable `TagoPage`와 response envelope·result code·body·
  pagination metadata·items·양수 page 값 검증을 소유합니다. city-code의 명시적 unpaginated 예외 외에는
  기존처럼 누락 metadata를 닫고, parser는 canonical `ProviderUnavailable`만 사용합니다.
- compatibility facade: `providers.py`가 다섯 함수·class를 wrapper 없이 직접 import해 기존 공개 경로와
  객체 identity를 유지합니다. TagoClient는 아직 facade에 있어 runtime global 참조, cache·singleflight,
  기본 singleton 위치와 monkeypatch seam은 변하지 않았고 `providers.py`는 1,155줄에서 1,043줄로
  줄었습니다.
- 테스트·의존성: facade/canonical 다섯 객체 identity를 owner 테스트에 추가하고, 기존 malformed
  envelope·pagination, KST offset·inclusive window·역명·미관측 좌석 회귀를 유지했습니다.
  `provider_adapters/** -> providers.py` 역의존 gate도 그대로 적용됩니다.
- 확인된 검증: 관련 focused pytest 110건, API 전체 pytest 1,062건, Ruff `E/F/I`, format ratchet 63개와
  `git diff --check`를 통과했습니다. 기존 Starlette/httpx deprecation 경고 1건은 유지됐고 독립
  재감사에서 도입 P0~P3 회귀나 필수 테스트 공백은 발견되지 않았습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: `TagoPage.__module__`은 새 canonical owner로 바뀌었지만 저장소에 영속 pickle·qualname
  의존 사용처는 없습니다. 네트워크·cache·singleton을 소유하는 TagoClient 이동은 다음 슬라이스로
  분리했습니다.
- 남은 핵심 부채: TagoClient와 process singleton·Official timetable adapter, Mock adapter, 운영사
  execution adapter, registry application의 단계별 이동과 실행 전용 역할 base·정적 타입 gate입니다.

### 2026-08-05 스무 번째 구조 슬라이스 C

- TAGO runtime 소유권: `provider_adapters/tago.py`가 parser에 이어 `STATION_CITY_HINTS`, `TagoClient`,
  private singleton binding과 `default_tago_client()`를 소유합니다. network 요청, 역 카탈로그 TTL,
  raw-day 시간표 cache·singleflight와 KTX/SRT 필터 동작은 정책 변경 없이 그대로 이동했습니다.
- 공식 시간표 소유권: `provider_adapters/timetable.py`가 `OfficialTimetableAdapter`를 소유하며 SRT
  roster 선검증·미지원 노선 빈 결과·roster 장애의 canonical `ProviderUnavailable`·공식 URL 인계를
  유지합니다. 기본 client는 module-qualified canonical factory에서 받고 명시적 client 주입은
  factory를 호출하지 않습니다.
- compatibility facade와 의존성: `providers.py`는 `TagoClient`, public factory와 공식 시간표 class를
  wrapper 없이 직접 re-export하되 private singleton을 복제하지 않습니다. production의 API bootstrap,
  역 카탈로그와 Approved·시간표 application은 canonical owner를 직접 import하고 registry만 facade에
  남겼습니다. adapter→facade 역의존은 0건이며 `providers.py`는 1,043줄에서 504줄로 줄었습니다.
- 수명주기 회귀: canonical singleton reset 뒤 owner·facade factory와 KORAIL·SRT registry가 같은 객체를
  공유하고, 명시적 client가 기본 factory를 우회하는 계약을 owner 테스트로 고정했습니다. 기존 raw-day
  cache·singleflight, TAGO malformed/pagination, 역 카탈로그, SRT route 회귀도 함께 통과했습니다.
- 확인된 검증: 관련 focused pytest 151건, API 전체 pytest 1,063건, Ruff `E/F/I`, format ratchet
  61개와 `git diff --check`를 통과했습니다. 수정한 legacy 파일 2개를 formatter로 정리해 ratchet
  allowlist를 63개에서 61개로 줄였습니다. 기존 Starlette/httpx deprecation 경고 1건은 유지됐고
  독립 재감사와 추가 회귀 21건·identity/boundary 14건에서 P0~P3 회귀는 발견되지 않았습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: canonical class의 `__module__` 경로는 의도대로 바뀌었지만 저장소에 pickle·qualname
  의존 사용처는 없습니다. Mock·운영사 실행 adapter와 registry를 이동하거나 가짜 기본 stub를
  제거하는 행동 변경은 이번 물리 이동에 섞지 않았습니다.
- 남은 핵심 부채: Mock adapter, KORAIL/SRT execution adapter, provider registry application의 단계별
  물리 이동과 실행 전용 역할 base·Python 정적 타입 gate입니다.

### 2026-08-05 스무 번째 구조 슬라이스 D

- Mock 소유권: `provider_adapters/mock.py`가 좌석 등급별 fixture helper와 `MockProviderAdapter`의
  capability·시간표·역·관측·예약 계약을 함께 소유합니다. 외부 I/O, singleton, mutable instance
  상태가 없는 경계만 원본 AST와 같은 동작으로 이동해 execution 수명주기 변경과 분리했습니다.
- compatibility facade와 registry: `providers.py`는 public-looking helper와 class를 wrapper 없이 직접
  re-export합니다. 시간표·실행·legacy registry는 모두 canonical class를 반환하고 반복·상호 호출마다
  새 인스턴스를 만들며, 기존 facade subclass와 unbound base method 호출 identity를 유지합니다.
  `providers.py`는 504줄에서 358줄로 줄었습니다.
- 회귀 계약: facade/canonical helper·class identity, mock capability 전체 bit, 세 registry의 canonical
  type과 pairwise fresh identity를 owner 테스트 3건으로 추가했습니다. 기존 40분 inclusive 시간창,
  좌석 action·fare·provenance, 역 카탈로그, UTC 관측 시각·5분 freshness, 20분 결제 기한·공식 인계,
  provider mismatch와 API·snapshot·worker subclass 회귀도 함께 통과했습니다.
- 확인된 검증: 관련 focused pytest 218건, API 전체 pytest 1,066건, Ruff `E/F/I`, format ratchet
  61개와 `git diff --check`를 통과했습니다. 기존 Starlette/httpx deprecation 경고 1건은 유지됐고
  독립 재감사에서 원본 AST 동일, 역의존·순환 import 0건과 도입 P0~P2 회귀 없음을 확인했습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: node ID partial·same·unknown·name mismatch의 mock 전용 parameterized 테스트와 capability
  note·전체 node/city fixture·정확한 5분/20분 간격 고정은 아직 없습니다. 이번 이동에서 바뀐 동작은
  아니며 AST 동일성과 기존 관통 테스트로 보존을 확인했지만 후속 계약 강화 항목으로 남깁니다.
- 남은 핵심 부채: Experimental, KORAIL/SRT execution adapter, provider registry application의 단계별
  물리 이동과 실행 전용 역할 base·Python 정적 타입 gate입니다.

### 2026-08-05 스무 번째 구조 슬라이스 E

- 공통 실행 소유권: `provider_adapters/execution.py`가 기존 fail-closed adapter에 더해
  `ProviderCredentialLoader`와 DB session 기반 기본 credential loader를 소유합니다. 두 운영사
  constructor의 기본 인자는 이 canonical 함수 객체에 정의 시점 binding된 상태를 유지합니다.
- 운영사별 실행 소유권: KORAIL background 관측·예약·확인은
  `provider_adapters/korail_execution.py`, SRT 관측·예약 executor·예약 확인은
  `provider_adapters/srt_execution.py`가 소유합니다. 서로 다른 source factory, capability gate,
  credential generation, reservation executor 선택과 종료 정책을 한 대형 모듈에 합치지 않았습니다.
- 수명주기 보존: registry는 KORAIL·SRT 모두 호출마다 canonical fresh adapter를 만들고 capability
  조회 전 `_source=None`을 유지합니다. SRT sidecar 비활성 경로는 process singleton 예약 executor를
  공유하고 explicit executor는 기본 factory를 우회합니다. 주입 source는 drain만 하고 close·clear하지
  않으며, 소유 source만 drain·close 뒤 `None`으로 reset하는 계약을 owner 테스트로 고정했습니다.
- compatibility facade: `providers.py`는 credential type·loader와 두 운영사 class를 wrapper 없이 직접
  re-export하고 registry 함수는 이번 단계에 남겼습니다. public import·constructor default identity,
  task-scoped fresh adapter와 worker cleanup 순서는 유지되며 `providers.py`는 358줄에서 119줄로
  줄었습니다.
- 확인된 검증: worker·due pipeline·예약 실행/정합화를 포함한 focused pytest 232건과 owner/lifecycle
  pytest 77건, API 전체 pytest 1,070건, Ruff `E/F/I`, format ratchet 61개와 `git diff --check`를
  통과했습니다. 기존 Starlette/httpx deprecation 경고 1건은 유지됐고 독립 재감사에서 이동 전후
  AST 동일, 역의존·순환 import 0건과 도입 P0~P2 회귀 없음을 확인했습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: concrete class와 loader의 `__module__`은 새 canonical owner 경로로 바뀌었지만 저장소에
  pickle·qualname 의존 사용처는 없습니다. credential loader를 runtime monkeypatch하는 기존 seam도
  없으며 명시적 `credential_loader=`·source·executor 주입 계약을 유지했습니다.
- 남은 핵심 부채: `ExperimentalRailAdapter`와 provider registry/capability application의 물리 이동,
  실행 전용 역할 base와 Python 정적 타입 gate입니다.

### 2026-08-05 스무 번째 구조 슬라이스 F

- Experimental 소유권: 외부 구현이 없는 표시 adapter를 `provider_adapters/experimental.py`로 옮겨
  설정 flag·안전 capability와 timetable/stations `NotImplementedError` 계약을 실행 adapter와
  분리했습니다. facade는 canonical class를 wrapper 없이 같은 객체로 다시 export합니다.
- registry application: `provider_registry/application.py`가 시간표·실행 adapter 선택, historical
  timetable alias와 public capability 병합을 소유합니다. Settings는 호출 시작에서 한 번 결정해 모든
  하위 factory에 같은 객체로 전달하고, 실행 capability에서는 `seat_monitoring`, `reservation_once`,
  `note` 세 필드만 official timetable capability에 병합합니다.
- 순서·수명주기: capability 반환 순서는 KORAIL official → SRT official → Mock → KORAIL experimental
  → SRT experimental로 고정했습니다. execution adapter 호출별 fresh·lazy 수명주기, KORAIL·SRT
  timetable의 같은 TAGO singleton, Mock 세 registry fresh 반환과 `get_provider()`의 timetable 역할을
  유지했습니다.
- canonical 소비와 facade: provider registry HTTP, services, timetable/watch application과 worker는
  canonical registry를, station visibility와 timetable HTTP는 canonical provider 예외를 직접 import합니다.
  production의 `providers.py` import는 0건이며 63줄의 facade에는 동일 객체 re-export만 남았습니다.
  boundary gate가 production→facade, adapter→registry/facade, registry→HTTP/service/worker 역의존을
  자동 차단합니다.
- 회귀 계약: facade/canonical Experimental class·registry 함수 4개의 identity를 owner 테스트로
  고정했습니다. 별도 registry golden test는 `get_settings()` 1회, 동일 Settings 전달, adapter 호출·결과
  순서, official safe merge와 Mock 무변환을 검증합니다. 기존 소비 모듈 attribute monkeypatch seam도
  그대로 통과했습니다.
- 확인된 검증: API·worker·watch를 포함한 focused pytest 254건, API 전체 pytest 1,072건, Ruff
  `E/F/I`, format ratchet 60개와 `git diff --check`를 통과했습니다. `station_visibility.py`와 새 worker
  import를 formatter로 정리해 legacy allowlist를 61개에서 60개로 줄였습니다. 기존 Starlette/httpx
  deprecation 경고 1건은 유지됐고 독립 재감사 152건·HTTP boundary 9건에서 이동 전후 AST·identity와
  P0~P3 회귀 없음을 확인했습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: facade 함수를 재할당해 canonical registry 내부 호출을 바꾼다는 기존 seam은 없으며,
  앞으로 registry 조립을 대체하는 테스트는 canonical application을 patch합니다. class·function의
  `__module__` 변경에 의존하는 pickle·qualname 사용처도 없습니다.
- 남은 핵심 부채: 가짜 timetable/stations 기본 stub 제거와 capability 부분 실패 정책의 별도 행동
  슬라이스, 실행 전용 역할 base, Python 정적 타입 gate와 Mock 세부 fixture 계약 강화입니다.

### 2026-08-05 스물한 번째 구조 슬라이스 A

- UI preference application: 전역 화면·좌석 관측 간격 저장과 idle 활성 작업 재스케줄 orchestration을
  `ui_preferences/application.py`로 이동했습니다. HTTP는 관리자 인증·계정 `FOR UPDATE`와 transport를,
  application은 설정 투영·lease 조회·활성 작업 잠금·다음 관측 시각 계산·commit/refresh를 소유합니다.
- 잠금·안전 계약: 활성 provider 실행 lease를 먼저 조회하고, 후보를 eager load한 활성 작업을
  `FOR UPDATE`로 읽습니다. 실행 중 provider, 이미 due인 작업, 미래 cooldown이 있는 작업은 건너뛰며
  후보 없는 legacy 작업은 KST 날짜·시작 시각 fallback을 사용합니다. legacy split interval payload를
  무시하고 timezone-aware UTC로 저장하는 기존 계약도 유지했습니다.
- canonical 소비와 호환: UI preference HTTP는 canonical application을 직접 import하고 중앙
  `services.py`는 wrapper 없이 같은 함수 객체만 다시 export합니다. application의 FastAPI·legacy
  services 역의존을 module-boundary gate로 차단했습니다.
- 회귀 계약: owner와 compatibility export identity를 고정하고, 기존 leased·idle·후보 상태별 재계산에
  이미 due, 미래 cooldown, 후보 없는 KST fallback 사례를 추가했습니다. 이동 함수와 helper의 AST,
  잠금·쿼리·commit 순서는 독립 재감사에서 동일했고 P0~P3 회귀가 없었습니다.
- 확인된 검증: UI preference·API·boundary·정책 focused pytest 84건, API 전체 pytest 1,073건, Ruff
  `E/F/I`, format ratchet 60개와 `git diff --check`를 통과했습니다. 기존 Starlette/httpx deprecation
  경고 1건은 유지됐습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: `services.py`의 `_utc_instant`는 예약 정책에서도 사용하므로 이번 슬라이스에서는 UI owner에
  같은 작은 helper를 유지해 무관한 정책 이동을 섞지 않았습니다. 남은 API router·schema와
  `services.py` use case, UI preference의 요청 epoch·401 안전성은 후속 슬라이스입니다.

### 2026-08-05 스물한 번째 구조 슬라이스 B

- 좌석 도메인 타입 owner: `SeatClassId`, normalized provenance·action·좌석 등급 모델을
  `domain/seatClasses.ts`로 이동했습니다. API mapper와 시간표 API, 열차 결과 카드가 같은 타입을
  직접 공유하되 화면 feature는 API mapper를 타입 저장소로 import하지 않습니다. 기존
  `api/seatClasses.ts`의 공개 타입 경로는 canonical 타입의 compatibility re-export로 유지했습니다.
- API 경계 보존: `api/seatClasses.ts`에는 외부 `unknown` 판별, status/provenance 검증, 공식 URL allowlist,
  사용자 확인 TTL, registration evidence와 action 정규화를 그대로 유지했습니다. normalized 객체의
  `Record<string, unknown>` 확장과 raw field spread도 바꾸지 않아 기존 추가 필드 보존 계약이 같습니다.
- strict 테스트 owner: `api.test.js`에 섞여 있던 좌석 정규화 12개 선언·14개 실행 케이스를
  `seatClassesApi.test.ts`로 이동했습니다. 원본 파일과 새 파일의 테스트 선언 합계 54개를 유지했고,
  feature→`api/seatClasses` 타입 역의존 재도입을 boundary 테스트로 차단했습니다. canonical과 compatibility
  타입 동일성도 strict `expectTypeOf` 계약으로 고정했습니다.
- 확인된 검증: ESLint 오류 0개·고정된 legacy warning 12개, strict typecheck, 최종 focused Vitest 77건,
  전체 Vitest 80개 파일·571건, production build, Sites 4건, 기본 Playwright E2E 14건과
  `git diff --check`를 통과했습니다. build의 기존 500 kB 초과 chunk 경고는 유지됐습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: runtime 정규화 로직과 공개 반환값은 바꾸지 않은 타입·테스트 소유권 이동입니다.
  provenance kind, action kind, seat status의 더 좁은 판별 union과 `api.test.js`의 나머지 42개 선언은
  후속 strict TypeScript 슬라이스로 남겼습니다.

### 2026-08-05 스물한 번째 구조 슬라이스 C

- 앱 surface CSS owner: `features.css` 끝의 `.toast`부터 마지막 `@keyframes toast-in`까지 480행을
  `styles/app-surfaces.css`로 이동했습니다. toast, 실시간 알림 center, 인증·복구·loading은 개별 제품
  feature 내부가 아니라 앱 경계 surface라는 같은 변경 이유를 가집니다.
- cascade 보존: `styles.css`는 `tokens → base → shell → features → app-surfaces → responsive`의 여섯
  경계를 순서대로 import합니다. 이동 블록 안의 selector·선언·상대 순서는 같고 `toast-step-spin`과
  `toast-in`도 소비 surface와 함께 이동했습니다. operations skeleton reduced-motion은 `features.css`,
  공통 responsive override는 마지막 `responsive.css`에 그대로 남겼습니다.
- 구조 회귀: CSS contract 테스트가 여섯 파일의 import·결합 순서, feature 파일의 operations 종료점,
  app surface의 `.toast` 시작과 `toast-in` 종료, 두 animation owner를 고정합니다. selector 정리,
  CSS Modules 전환, responsive 안의 누적 toast 규칙 병합은 수행하지 않았습니다.
- 확인된 검증: app surface·알림·인증 focused Vitest 5개 파일·48건, ESLint 오류 0개·고정된 legacy
  warning 12개, strict typecheck, 전체 Vitest 80개 파일·571건, production build, Sites 4건, 기본
  Playwright E2E 14건과 `git diff --check`를 통과했습니다. 기존 500 kB 초과 chunk 경고는 유지됐습니다.
- 운영 검증 범위: CSS·구조 테스트·문서만 바뀐 슬라이스이므로 저장소 규칙에 따라 Compose 이미지
  재빌드·재생성은 수행하지 않았습니다. 나머지 4,476행 feature CSS의 실제 기능 owner 분리는 후속입니다.

### 2026-08-05 스물한 번째 구조 슬라이스 D

- strict mypy ratchet: test extra에 `mypy>=1.20,<2`를 추가하고 lock의 mypy 1.20.2로 Python 3.12
  `strict=true`를 실행합니다. 최초 대상은 오류 0인 `provider_contracts.py`, provider base·execution·
  experimental·KORAIL execution·timetable adapter와 registry application 7개 파일입니다.
- 범위 원칙: registry의 `TimetableProvider`·`ExecutionProvider` 반환 분기가 concrete adapter의
  Protocol 구조 적합성을 정적으로 증명합니다. `ignore_missing_imports`, 오류 코드 비활성화,
  `type: ignore` 같은 suppression은 추가하지 않았습니다. 전체 API strict dry-run은 48개 파일
  302개 오류이므로 이번 완료 범위는 provider 경계 7개 파일뿐입니다.
- 재현 가능한 검증: Makefile과 PowerShell `verify-api`가 가장 먼저 `uv lock --check`를 실행해
  stale lock을 테스트 전에 차단하고, 마지막에 `uv run --frozen --extra test mypy`를 실행합니다.
  Makefile은 기존 pytest → Ruff `E/F/I` → format ratchet 순서를 유지합니다. mypy는 test extra에만
  있어 production API·browser Compose 이미지의 설치 dependency를 늘리지 않습니다.
- 확인된 검증: `uv lock --check`, strict mypy 7개 파일 오류 0, provider focused pytest 30건,
  API 전체 pytest 1,073건, Ruff `E/F/I`, format ratchet 60개, PowerShell parser와 Make recipe
  tab·명령 순서, `git diff --check`를 통과했습니다. 기존 Starlette/httpx deprecation 경고 1건은
  유지됐고 Windows 환경에 `make`가 없어 실제 `make -n` 대신 recipe를 정적으로 검증했습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 독립 리뷰: 선행 non-frozen pytest가 stale lock을 갱신한 뒤 마지막 frozen mypy가 통과할 수 있다는
  P2를 발견해 두 verify 경로의 첫 단계에 lock check를 추가했습니다. 그 밖의 P0/P1/P3 회귀는 없었습니다.
- 남은 범위: Mock의 넓은 상태·URL 타입, SRT executor 공통 gateway Protocol, TAGO JSON `Any` 경계,
  timetable support URL 타입을 owner별 오류 0 슬라이스로 고친 뒤 ratchet 목록을 확장합니다.

### 2026-08-05 스물두 번째 구조 슬라이스 A

- 역 카탈로그 strict 테스트 owner: legacy `api.test.js`의 연속된 역 카탈로그 6개 선언·10개 실행을
  기존 `stationsApi.test.ts`로 이동했습니다. provider별 요청·node ID 병합, KORAIL+SRT 공용 목록,
  provider 503·불완전 row, metadata tuple 5개, scope 누락·빈 목록, exact mock tuple 계약을 유지합니다.
- 타입 경계: payload는 `unknown`, fetch 입력은 `RequestInfo | URL`, metadata table은 readonly tuple로
  검사합니다. production `api/stations.ts`와 공개 export, station DTO·domain/ViewModel 타입에는 손대지
  않았고 기존 strict owner 3건과 중복 제거도 하지 않았습니다.
- 테스트 수 ratchet: 이동 전 legacy API 46실행 + station owner 3실행과 이동 후 legacy API 36실행 +
  station owner 13실행이 모두 49건입니다. 두 파일의 테스트 선언 합계도 45개로 유지했습니다.
- 확인된 검증: owner focused 49건, station 소비 hook/page 33건, ESLint 오류 0개·고정된 legacy warning
  12개, strict typecheck, 전체 Vitest 80개 파일·571건, production build와 `git diff --check`를
  통과했습니다. 기존 500 kB 초과 chunk 경고는 유지됐습니다.
- 운영 검증 범위: production source와 runtime bundle 행동을 바꾸지 않은 테스트 owner·문서 이동이므로
  기본 E2E와 Compose 재배포는 반복하지 않았습니다. 다음 후보는 timetable 12개, watch 21개이며
  auth 2개·events 1개는 전용 strict owner와의 중복을 별도 판단합니다.

### 2026-08-05 스물두 번째 구조 슬라이스 B

- operational projection owner: `services.py`의 `_BOOKING_OPEN_OBSERVATIONS`,
  `OperationalProjectionCandidate`, `apply_operational_projection`을 본문·타입 변경 없이
  `observations/operational_projection_application.py`로 이동했습니다. services는 Protocol과 함수를
  wrapper 없이 직접 다시 export해 기존 import와 runtime identity를 보존합니다.
- 범위 제한: 예약 DB query 정책인 `_RESERVATION_RETRY_EDGE_OBSERVATIONS`와
  `record_seat_observation`의 observation flush·outbox·상태 전이·transaction은 services에 남겼습니다.
  services는 1,692줄에서 1,632줄, 새 owner는 71줄입니다.
- 원자성 계약: watch/candidate 잠금 뒤 projection → observation flush → `watch.seat_observed` outbox →
  watch 요약·주기 종료 → commit 순서는 같습니다. 별도 session 회귀가 commit 때 projection·observation·
  outbox가 모두 영속되고 rollback 때 셋 모두 사라지는 같은 SQLAlchemy UoW를 검증합니다.
- 상태·의존성 회귀: booking open 5상태, waitlist, departed, out-of-service, sold-out no-op, delay 투영과
  compatibility identity를 고정했습니다. boundary gate는 새 owner의 services·worker·Celery·FastAPI·
  SQLAlchemy·model·outbox·provider registry/facade 역의존을 차단합니다.
- mypy ratchet 확장: 새 owner가 단독 strict 오류 0이므로 기존 7개 파일 목록에 즉시 추가해 현재
  scoped strict gate를 8개 파일로 확장했습니다. `WatchCandidate`의 candidate Protocol 구조 적합성도
  정적으로 확인했습니다.
- 확인된 검증: operational·boundary·service observation focused pytest 59건, API 전체 pytest
  1,083건, Ruff `E/F/I`, format ratchet 60개, strict mypy 8개 파일 오류 0, `uv lock --check`와
  `git diff --check`를 통과했습니다. 기존 Starlette/httpx deprecation 경고 1건은 유지됐고 독립
  AST·identity·transaction 재감사에서 P0~P3 지적이 없었습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: transaction 테스트는 동일 UoW의 commit/rollback을 검증하지만 PostgreSQL process 경합이나
  중단 내구성을 새로 증명하지는 않습니다. `SeatObservationResult` DTO annotation의 domain 타입
  재설계와 `record_seat_observation` application 이동은 별도 슬라이스로 남겼습니다.

### 2026-08-05 스물두 번째 구조 슬라이스 C

- 시간표 strict 테스트 owner: legacy `api.test.js`의 연속된 시간표 12개 선언·12개 실행을
  `timetablesApi.test.ts`로 이동했습니다. production mapper, provider·구간·날짜·시간 query와 승객 수,
  provider별 1회 요청·병합·필터·정렬·중복 제거, 화면 결과 상한 없음, 요일·역 identity·시간 범위 검증,
  provider scoped 503와 부분 성공 계약을 그대로 유지합니다.
- strict 전환: fetch 입력은 `RequestInfo | URL`, provider key는 좁은 union/Record, filter form은
  `TimetableSearchForm`, 배열·mock call은 명시적 guard로 검사합니다. `any`, `@ts-ignore`, 무근거
  assertion은 추가하지 않았습니다. watch 테스트가 계속 쓰는 `mapTimetable` legacy import는 유지했습니다.
- 테스트 수 ratchet: 이동 전 legacy API 36 + timetable owner 8과 이동 후 legacy API 24 + timetable
  owner 20이 모두 44개 선언·44개 실행입니다. 기존 owner와 의미가 겹치는 테스트도 삭제·합치지 않았고
  이름·순서·inline fixture·assertion의 기계적 비교가 일치합니다.
- 확인된 검증: owner focused 44건, production consumer 5개 파일·44건, ESLint 오류 0개·고정된 legacy
  warning 12개, strict typecheck, 전체 Vitest 80개 파일·571건, production build와
  `git diff --check`를 통과했습니다. 기존 500 kB 초과 chunk 경고는 유지됐습니다.
- 운영 검증 범위: production source와 runtime 행동을 바꾸지 않은 test/docs-only 슬라이스이므로 기본
  E2E와 Compose 재배포는 반복하지 않았습니다. legacy API에는 watch 21개·auth 2개·events 1개 선언이
  남아 있으며 시간표 DTO·domain·ViewModel 물리 분리는 별도 production 슬라이스입니다.

### 2026-08-05 스물두 번째 구조 슬라이스 D

- payment-hold application: `services.py`의 `_utc_instant`, `payment_hold_end_reason`,
  `is_payment_hold_ended`를 본문·타입 변경 없이 `reservations/payment_hold_application.py`로
  이동했습니다. services는 private helper와 두 public 함수를 직접 alias해 confirmation·reconciliation의
  timezone 처리와 기존 public import identity를 보존합니다.
- canonical 소비와 범위: `watch_management/read_model.py`는 보류 종료 reason을 canonical owner에서
  직접 import합니다. reconciliation interval, confirmed-absent/retry-edge, begin reservation,
  confirmation·reconciliation DB write와 transition/outbox는 services에 남았습니다. services는
  1,632줄에서 1,606줄, 새 owner는 40줄입니다.
- 정책 matrix: 비결제 outcome, marker 누락, exact `NOT_FOUND`, confirmed payment hold의 기한=marker·
  미래 기한, 무관한 confirmation, naive/aware UTC 동일 instant, boolean helper와 services identity를
  고정했습니다. 기존 watch read/API payload, 만료 보류 retry-edge·outbox 회귀도 유지했습니다.
- 의존성·타입 gate: 새 owner는 domain·ORM entity·confirmation enum만 읽고 FastAPI·schema·SQLAlchemy
  API·outbox·services·worker·provider runtime을 import하지 않습니다. strict 오류 0을 확인해 mypy
  ratchet을 8개에서 9개 파일로 즉시 확장했습니다.
- 확인된 검증: payment hold·read model·reservation execution/reconciliation/API focused pytest 89건,
  API 전체 pytest 1,091건, Ruff `E/F/I`, format ratchet 60개, strict mypy 9개 파일 오류 0,
  `uv lock --check`와 `git diff --check`를 통과했습니다. 기존 Starlette/httpx deprecation 경고
  1건은 유지됐고 독립 AST·identity·timezone 재감사에서 P0~P3 지적이 없었습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.
- 검증 범위: 새 policy는 DB write·lock·transaction을 옮기지 않았으므로 account → watch → candidate →
  circuit lock → hold 판정 → retry-edge 조회 → savepoint → transition/outbox → caller commit 순서는
  같습니다. legacy services symbol을 임의 monkeypatch해 canonical 내부 호출까지 바꾼다는 새 seam은
  지원하지 않으며 현재 그런 production/test 소비자는 없습니다.

### 2026-08-05 스물두 번째 구조 슬라이스 E

- watch strict 테스트 owner: legacy `api.test.js`의 watch mapper·생성 payload·REST mutation 21개 선언·
  21개 실행을 기존 `watchesApi.test.ts`로 이동했습니다. 이름·순서·fixture·47개 assertion과 기존
  strict owner의 10개 선언·13개 실행을 삭제하거나 합치지 않았습니다.
- strict 전환: fetch 입력·옵션을 `RequestInfo | URL`과 `RequestInit`으로 고정하고 mock call·배열은
  존재 여부를 확인한 뒤 사용합니다. payload override는 `Record<string, unknown>`에서 시작하며 `any`,
  non-null assertion, `@ts-ignore`, 무근거 type assertion은 추가하지 않았습니다. 서로 다른 evidence·
  provenance 계약을 가진 legacy fixture와 기존 `WATCH_DTO`도 임의 통합하지 않았습니다.
- 테스트 수 ratchet: 이동 전 legacy API 24개 실행 + watch owner 13개 실행과 이동 후 legacy API 3개
  실행 + watch owner 34개 실행이 모두 37건입니다. 두 파일 선언 합계도 34개로 유지했습니다. legacy
  파일에는 auth 2개와 events 1개 및 필요한 helper만 남았습니다.
- 확인된 검증: API+watch owner focused 37건, watch 인접 16건, production consumer 7개 파일·57건,
  ESLint 오류 0개·고정된 legacy warning 12개, strict typecheck, 전체 Vitest 80개 파일·571건,
  production build와 `git diff --check`를 통과했습니다. 기존 500 kB 초과 chunk 경고는 유지됐습니다.
- 운영 검증 범위: production source와 runtime 행동을 바꾸지 않은 test/docs-only 슬라이스이므로 기본
  E2E와 Compose 재배포는 반복하지 않았습니다. 커진 `watchesApi.test.ts`의 mapper·payload·transport
  물리 분리와 auth/events strict owner 이동은 테스트 손실 없이 별도 슬라이스로 진행합니다.

### 2026-08-05 스물두 번째 구조 슬라이스 F

- watch transition notification application: `services.py`의 `add_watch_notifications` 본문·메시지·
  enabled 전역 채널 조회·dispatch outbox 생성을
  `notification_management/watch_transition_application.py`로 이동했습니다. services는 함수를 wrapper
  없이 같은 객체로 다시 export하며 기존 `apply_watch_transition`의 호출 위치를 유지합니다.
- 경계와 순서: status·updated time → transition history → idempotency → `watch.status_changed` outbox →
  `notification.dispatch_requested` outbox 순서를 보존합니다. lock·commit·rollback과 상태 전이 허용
  정책은 services에 남겼으므로 status/history와 두 outbox가 같은 UoW에서 함께 commit되거나 rollback됩니다.
  services는 1,606줄에서 1,493줄, 새 owner는 126줄입니다.
- 전달 계약: 단일 관리자에 속한 현재 `enabled=true`인 전역 채널을 모두 `created_at, id` 순으로 선택하고 disabled
  채널과 watch의 legacy 채널 snapshot은 전달 권한에서 제외합니다. 상태·reason 9분기, 결제기한 유무와
  KST 표시, 비알림 상태 조기 반환, transition-token dedupe, canonical/services identity를 고정했습니다.
- 의존성·타입 gate: 새 owner는 SQLAlchemy session·domain·ORM model·outbox primitive만 사용하며
  FastAPI·Celery·worker·provider·security·services를 역참조하지 않습니다. transaction·row lock 호출
  부재를 AST로 검사하고 strict mypy ratchet을 9개에서 10개 파일로 확장했습니다.
- 확인된 검증: 신규 owner·boundary focused pytest 23건, 상태·만료·예약·관찰 인접 focused 80건,
  API 전체 pytest 1,107건, Ruff `E/F/I`, format ratchet 60개, strict mypy 10개 파일 오류 0,
  `uv lock --check`와 `git diff --check`를 통과했습니다. 기존 Starlette/httpx deprecation 경고 1건은
  유지됐습니다.
- 독립 리뷰: 기존 순서는 정확하고 P0~P2 회귀는 없었습니다. 원자성 테스트가 outbox 종류를 set으로만
  비교해 생성 순서를 고정하지 못한다는 P3를 발견해 두 canonical seam의 호출 순서 assertion을
  추가했고, 보강 뒤 focused 23건을 다시 통과했습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.

### 2026-08-05 스물세 번째 구조 슬라이스 A

- 마지막 legacy API test owner: `api.test.js`의 관리자 등록·로그인 2개 선언을 기존 strict
  `authApi.test.ts`, replay SSE 1개 선언을 `eventsApi.test.ts`로 이동한 뒤 빈 legacy 파일을
  삭제했습니다. production `auth.ts`·`events.ts`와 공개 export는 변경하지 않았습니다.
- 계약 보존: 등록 요청에 legacy bootstrap header를 보내지 않는 유일한 assertion, 로그인 endpoint·
  credentials/body, SSE의 old/current/future와 reservation event 전달·close를 그대로 유지했습니다.
  기존 strict owner와 의미가 겹치는 테스트도 이번 이동에서 삭제·병합하지 않았습니다.
- strict 전환: `vi.fn<typeof fetch>`, mock call 존재 검사, `Headers`, typed `FakeEventSource`와 unknown
  event guard를 사용했습니다. `any`, non-null/type assertion, suppression은 추가하지 않았습니다.
- 테스트 수 ratchet: 이동 전 legacy API 3실행 + auth 5실행 + events 2실행과 이동 후 auth 7실행 +
  events 3실행이 모두 10건이며 선언 합계도 9개입니다. 전체 Vitest 실행은 571건을 유지하고 파일은
  legacy owner 삭제로 80개에서 79개가 됐습니다.
- 확인된 검증: auth/events focused 10건, 관련 consumer 7개 파일·26건, ESLint 오류 0개·고정된 legacy
  warning 12개, strict typecheck, 전체 Vitest 79개 파일·571건, production build, Sites 4건, 기본
  Playwright E2E 14건과 `git diff --check`를 통과했습니다. 기존 500 kB 초과 chunk 경고는 유지됐습니다.
- 운영 검증 범위: production source와 runtime 행동을 바꾸지 않은 test/docs-only 슬라이스이므로 Compose
  재배포는 수행하지 않았습니다. `allowJs` 제거 전 남은 포함 범위는 `setup.js`,
  `eslintRatchet.test.js`, `App.test.jsx`, `sw.test.js` 네 파일입니다.

### 2026-08-05 스물세 번째 구조 슬라이스 B

- operations CSS owner: `features.css` 끝의 `.operations-dashboard`부터 마지막 reduced-motion block까지
  447줄을 `styles/operations.css`로 이동했습니다. `operations-*` selector의 production consumer는
  strict `features/settings/SystemStatusDashboard.tsx` 하나이며 공용 UI나 다른 feature 사용은 없습니다.
- cascade 보존: 전역 import는 `tokens → base → shell → features → operations → app-surfaces → responsive`
  일곱 경계입니다. HEAD의 4,476줄 `features.css`와 분리 후 `features.css` 4,029줄 +
  `operations.css` 447줄이 줄·Git clean-filter blob 기준으로 정확히 같습니다. `operations-shimmer`와
  reduced-motion override도 소비 owner와 함께 이동했습니다.
- 리뷰 보정: 최초에는 앞 639줄을 Home owner로 분리했지만 status/provider/countdown/empty selector가
  reservations·settings·new-wait에도 쓰이는 P2 암묵 의존을 독립 리뷰에서 확인해 전부 되돌렸습니다.
  전용 tail만 다시 분리한 뒤 독립 재리뷰에서 P0~P3 잔여 지적이 없었습니다.
- 구조 회귀: import exact order, feature owner 마지막 selector, operations 시작 selector·event list·
  keyframe·reduced-motion EOF를 strict 구조 테스트로 고정했습니다. operations dashboard 인접 focused
  2개 파일·14건도 통과했습니다.
- 확인된 검증: ESLint 오류 0개·고정된 legacy warning 12개, strict typecheck, 전체 Vitest 79개 파일·
  571건, production build, Sites 4건, 기본 Playwright E2E 14건과 `git diff --check`를 통과했습니다.
  기존 500 kB 초과 chunk 경고는 유지됐습니다.
- 운영 검증 범위: CSS·구조 테스트·문서만 변경했으므로 Compose 재배포는 수행하지 않았습니다. 남은
  `features.css`의 new-wait·reservation·settings/shared selector 재소유와 중복 정리는 별도입니다.

### 2026-08-05 스물세 번째 구조 슬라이스 C

- observation cycle application: `services.py`의 `latest_observation_fingerprint`와
  `finish_observation_cycle`을 `observations/cycle_application.py`로 이동했습니다. services는 두 함수를
  wrapper 없이 같은 객체로 다시 export하고 worker dependency 조립은 canonical owner를 직접 사용합니다.
  services는 1,493줄에서 1,417줄, 새 owner는 99줄입니다.
- fingerprint 계약: 후보 priority 순서와 각 후보의 최신 `observed_at DESC, id DESC` 상태 vector만
  기존 `json.dumps(sort_keys=True, separators=(",", ":"), default=str)` + SHA-256으로 hash합니다.
  관측 timestamp만 달라진 경우 같은 fingerprint이며, Unicode·`None`·복수 후보 matrix에서 기존
  public `request_hash`와 byte-compatible digest를 확인했습니다. public helper 자체는 이동하지 않았습니다.
- 주기 정책: 같은 vector이면 `unchanged_runs + 1`, 변화면 0으로 초기화합니다. watching·
  official_waitlist·seat_found만 관리자 전역 관측 간격 또는 기본 5초와 첫 활성 후보 출발시각을 사용하고,
  후보가 없으면 KST 여행일·시각을 UTC로 바꿉니다. terminal 상태는 `next_check_at=None`으로 닫습니다.
- UoW·경계: group application의 실행 임대 검증 → watch 잠금 → 이전 fingerprint → observation flush·
  seat/status/notification outbox → cycle finish → commit과 예외 rollback 순서를 유지했습니다. 새 owner에는
  lock·commit·rollback·outbox·provider/runtime 역의존이 없고, services/worker canonical identity를
  고정했습니다. strict mypy ratchet은 10개에서 11개 파일로 확장했습니다.
- 확인된 검증: 신규 owner·boundary focused pytest 20건, observation group·worker 인접 focused 27건,
  독립 감사 focused 30건, API 전체 pytest 1,119건, Ruff `E/F/I`, format ratchet 60개, strict mypy
  11개 파일 오류 0, `uv lock --check`와 `git diff --check`를 통과했습니다. 기존 Starlette/httpx
  deprecation 경고 1건은 유지됐고 독립 리뷰 P0~P3 잔여 지적은 없었습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.

### 2026-08-05 스물네 번째 구조 슬라이스 A

- 소형 legacy test 전환: `tests/setup.js`, `eslintRatchet.test.js`, `sw.test.js`를 각각 strict `.ts`로
  전환하고 Vitest `setupFiles`를 `./tests/setup.ts`로 원자적으로 갱신했습니다. production source와
  배포 경계의 실제 `public/sw.js`는 변경하지 않았습니다.
- 계약 보존: ESLint ratchet은 3개 선언·`it.each` 6행을 포함한 8개 실행과 가상 JSX/MJS/TSX/TS/JS
  경로·rule ID를 그대로 유지합니다. service worker test는 VM에서 실제 runtime script를 실행하고
  backend message/official URL과 explicit title/body/URL의 두 push 계약을 유지합니다.
- strict 경계: `ESLint.LintResult`의 빈 결과, unknown service-worker listener, `waitUntil`의
  `Promise<unknown>`을 명시적으로 guard합니다. `any`, type/non-null assertion, suppression은 추가하지
  않았고 setup의 cleanup·window scroll/open mock은 본문 그대로 이동했습니다.
- 확인된 검증: 대상 focused 2개 파일·10건, 유일한 남은 legacy `App.test.jsx` 32건, ESLint 오류 0개·
  고정된 legacy warning 12개, strict typecheck, 전체 Vitest 79개 파일·571건, production build와
  `git diff --check`를 통과했습니다. 기존 500 kB 초과 chunk 경고는 유지됐습니다.
- 운영 검증 범위: test/config/docs-only 슬라이스이므로 Sites·기본 E2E·Compose 재배포는 반복하지
  않았습니다. `allowJs=true`, `checkJs=false`, Vitest JSX include는 29개 선언·32개 실행을 가진
  `App.test.jsx`가 남아 있어 아직 제거하지 않습니다.

### 2026-08-05 스물네 번째 구조 슬라이스 B

- idempotency application: `services.py`의 `request_hash`, `get_idempotent_resource`,
  `remember_idempotency`를 53줄 `idempotency/application.py`로 이동하고 FastAPI 비의존
  `IdempotencyConflict`를 도입했습니다. services는 세 함수를 wrapper 없이 같은 객체로 다시 export하고
  내부 watch 생성·전이도 canonical binding을 사용해 1,417줄에서 1,383줄로 줄었습니다.
- canonical 소비와 HTTP 경계: official page confirmation persistence는 services facade 대신 새 owner를
  직접 import합니다. watch create/start/pause/cancel과 official evidence HTTP만 payload mismatch를 기존
  detail의 409로 변환하며, official confirmation의 별도 incomplete batch `ValueError → 409` 계약도
  유지합니다. transition core 본문과 row lock·commit 순서는 변경하지 않았습니다.
- hash 호환: mapping order와 Pydantic JSON뿐 아니라 동적 `__getattr__`로 `model_dump`를 제공하는 기존
  duck-typed 객체도 legacy `hasattr` 판정과 같은 compact JSON + SHA-256 결과를 냅니다. strict typing은
  `hasattr` 직후의 근거 있는 Protocol cast만 사용하며 suppression이나 broad input cast는 없습니다.
- UoW·동시성: application은 record를 caller session에 추가할 뿐 lock·commit·rollback을 소유하지
  않습니다. watch/outbox 또는 official confirmation batch와 같은 UoW의 commit/rollback, watch 4개 HTTP
  409, official concurrent same-key replay의 record/batch 1건을 회귀 테스트로 고정했습니다.
- 타입·경계 gate: 새 owner의 FastAPI·services·worker·schema·outbox/runtime 역의존과 transaction/row-lock
  소유를 차단하고 official confirmation→services 역의존도 금지했습니다. strict mypy ratchet은 11개에서
  12개 파일로 확장했습니다.
- 확인된 검증: 최종 신규 owner·boundary focused pytest 19건, 선행 HTTP/concurrency focused 24건,
  API 전체 pytest 1,129건, Ruff `E/F/I`, format ratchet 60개, strict mypy 12개 파일 오류 0,
  `uv lock --check`와 `git diff --check`를 통과했습니다. 기존 Starlette/httpx deprecation 경고 1건은
  유지됐습니다.
- 독립 리뷰: runtime Protocol이 동적 `model_dump`를 legacy와 다르게 처리하는 P3를 발견해 `hasattr`과
  grounded cast로 복원하고 동적 fixture를 추가했습니다. 보정 뒤 P0~P3 잔여 지적이 없었습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API·proxy health 200, 재생성 뒤 최근 안전한
  오류 표식 0건을 확인했습니다.

### 2026-08-05 스물네 번째 구조 슬라이스 C

- 마지막 Vitest JS/JSX 경계: `App.test.jsx`를 strict `App.test.tsx`로 전환하고 공개 props 타입 기반
  fixture와 배열·DOM·clipboard·`window.open` guard를 추가했습니다. 테스트 이름·순서와 이동 전후
  29개 선언·32개 실행·120개 assertion은 그대로 유지했습니다.
- TypeScript gate: `check-eslint-ratchet.mjs`의 실제 세 export를 표현하는 `.d.mts` 선언 경계를 추가하고
  `allowJs`·`checkJs`를 제거했습니다. Vitest discovery는 `tests/**/*.test.{ts,tsx}`만 포함하며 별도
  `test:sites`가 소유하는 Sites worker와 실제 service worker·worker·빌드/ESLint `.js`·`.mjs` 런타임
  경계는 기존 형식과 lint 범위를 유지합니다.
- 안전한 strict 전환: `any`, type/non-null assertion, suppression 없이 nullable DOM과 fixture 누락을
  fail-fast helper로 좁혔습니다. production source·ESLint warning baseline·service worker·Sites 산출물은
  변경하지 않았습니다.
- 확인된 검증: App focused 32건, ESLint ratchet focused 8건, 전체 Vitest 79개 파일·571건,
  ESLint 오류 0개·고정된 legacy warning 12개, strict typecheck, production build, Sites 4건과
  `git diff --check`를 통과했습니다. 기존 500 kB 초과 chunk 경고는 유지됐습니다.
- 운영 검증 범위: test/typecheck discovery/docs-only 슬라이스로 사용자 동작과 배포 runtime을 바꾸지
  않아 기본 E2E와 Compose 재배포는 반복하지 않았습니다.

### 2026-08-05 스물네 번째 구조 슬라이스 D

- watch transition policy: 98줄 `watch_management/transition_policy.py`가 no-op/rejected/allowed 판별
  union, `NextCheckPolicy`, 13×13 허용표 판단과 reason·transition token·status event dedupe identity를
  소유합니다. 순수 owner는 기존 `domain.py` 상태표만 읽고 FastAPI·SQLAlchemy·model·provider·clock·
  transaction을 import하거나 호출하지 않습니다.
- orchestration 보존: `apply_watch_transition`과 `transition_watch`의 public owner·identity는 계속
  `services.py`입니다. idempotency replay → policy 순서, no-op/거절의 무변경·provider 0회, 허용 전이의
  status → UTC now 1회 → updated/cooldown → SCHEDULED capability 1회와 next-check preserve/clear/재무장
  의미를 그대로 유지했습니다. services는 이 application orchestration을 포함한 1,399줄입니다.
- artifact·UoW 보존: reason의 None/빈 값 기본값·160자 절단·공백 보존, history → idempotency → status
  outbox → transition notification 순서와 token/dedupe 문자열을 고정했습니다. caller의 watch row lock과
  commit/refresh, 예외 rollback은 이동하지 않았습니다.
- 회귀·경계: 357줄 owner 테스트가 13×13 전체 matrix·상태 집합 completeness·다음 확인 정책·identity와
  replay/provider 지연 조회를 검증합니다. module-boundary gate는 새 owner의 runtime·transport·DB 역의존과
  async/clock/transaction 소유를 차단하고 strict mypy ratchet은 12개에서 13개 파일로 확장했습니다.
- 확인된 검증: 인접 focused pytest 406건, API 전체 pytest 1,481건, Ruff `E/F/I`, format ratchet 60개,
  strict mypy 13개 파일 오류 0, `uv lock --check`, 독립 리뷰 P0~P3 지적 없음과 `git diff --check`를
  통과했습니다. 기존 Starlette/httpx deprecation 경고 1건은 유지됐습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API health·ready와 proxy health 200, 재생성 뒤
  최근 안전한 오류 표식 0건을 확인했습니다.

### 2026-08-05 스물다섯 번째 구조 슬라이스 A

- 단일 CSS owner: `features.css`의 trigger 18줄·dialog 174줄과 `responsive.css`의 760px 57줄·340px
  9줄을 값·순서 변경 없이 263줄 `features/new-wait/officialSeatConfirmation.css`로 이동했습니다.
  `features.css`는 4,029→3,837줄, `responsive.css`는 1,384→1,318줄로 줄었습니다.
- 소비·공용 경계: `official-confirmation-*` 14개 class는 `OfficialSeatConfirmation.tsx`만 소비합니다.
  `.copy-status`, `.provider-chip`, `.icon-button`·`.button` primitive는 각각 기존 surface/token/shell
  owner에 남겼습니다. 이 컴포넌트는 현재 production graph에 조립되지 않은 dormant feature이므로
  실제 사용자 화면에서 dialog가 검증됐다고 표현하지 않습니다.
- cascade 계약: 전역 import를 `tokens → base → shell → features → operations → app-surfaces →
  officialSeatConfirmation → responsive` 여덟 경계로 확장했습니다. 구조 테스트가 새 owner의 시작,
  760px/340px media, legacy owner 잔존 0과 exact import order를 고정합니다. HEAD 네 원본 구간과 새
  owner의 행 단위 비교는 263/263 일치했습니다.
- 확인된 검증: owner·CSS 구조 focused Vitest 2개 파일·10건, 전체 Vitest 79개 파일·571건,
  ESLint 오류 0개·고정 legacy warning 12개, strict typecheck, production build, Sites 4건, 기본
  Playwright E2E 14건과 `git diff --check`를 통과했습니다. 기존 500 kB 초과 chunk 경고만 유지됐습니다.
- 운영 검증 범위: CSS·구조 테스트·문서만 변경해 Compose 재배포는 생략했습니다. 기본 E2E는 전역
  cascade와 기존 1,440px·320px·720px reflow를 확인했으며 dormant dialog 자체의 geometry를 직접 열어
  검증한 것은 아닙니다.

### 2026-08-05 스물다섯 번째 구조 슬라이스 B

- watch transition application: 198줄 `watch_management/transition_application.py`가 아홉 typed port와
  `WatchTransitionDependencies`, FastAPI 비의존 `WatchTransitionRejected`, transition artifact orchestration을
  소유합니다. dependency 조립만으로 provider를 조회하지 않고 application도 lock·commit·refresh·rollback을
  호출하지 않습니다.
- facade·transport 경계: `services.apply_watch_transition`은 기존 signature와
  `rail_waitlist.services` 함수 identity를 유지하는 wrapper입니다. 호출 시점의 request hash·idempotency·
  policy·provider·clock·outbox·notification globals를 캡처해 기존 monkeypatch seam을 보존하고,
  `WatchTransitionRejected`만 exact detail의 HTTP 409로 변환합니다. idempotency/provider/DB 예외는 그대로
  전파하며 `transition_watch`의 `FOR UPDATE`·404·commit·refresh는 변경하지 않았습니다.
- 실행 계약: replay → missing resource fall-through → policy, no-op/rejected 조기 종료, allowed status →
  UTC clock 1회 → updated/cooldown → SCHEDULED capability 1회, next-check preserve/clear/재무장과 observation
  history → idempotency → status outbox → notification 순서·reason/token/dedupe를 그대로 유지했습니다.
  services는 1,399줄에서 1,368줄로 줄었습니다.
- 테스트·경계: 순수 13×13/identity는 103줄 policy 테스트에 남기고 orchestration은 361줄 application
  owner 테스트로 이동했습니다. 이동 전 351회 상당을 policy 343 + application 10으로 보존·보강했고
  missing replay와 transport-independent rejection을 추가했습니다. boundary gate는 FastAPI·runtime
  역의존과 transaction/lock/refresh 소유를 차단하며 strict mypy ratchet은 13→14개 파일입니다.
- 확인된 검증: 인접 focused pytest 499건, API 전체 pytest 1,484건, Ruff `E/F/I`, format ratchet 60개,
  strict mypy 14개 파일 오류 0, `uv lock --check`, 독립 리뷰 P0~P3 지적 없음과 `git diff --check`를
  통과했습니다. 기존 Starlette/httpx deprecation 경고 1건은 유지됐습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API health·ready와 proxy health 200, 재생성 뒤
  최근 안전한 오류 표식 0건을 확인했습니다.

### 2026-08-05 스물다섯 번째 구조 슬라이스 C

- watch core value owner: 52줄 `domain/watch.ts`가 KORAIL/SRT/MOCK provider, 13개 status,
  standard/first/any seat class와 balanced/focused observation mode의 canonical 타입·guard를 소유합니다.
  provider만 기존처럼 trim+uppercase하고 status/seat는 exact match합니다. module-boundary test가 네 타입의
  중복 선언을 차단하며 `api/watches.ts`와 `ActiveWatchList.tsx`의 기존 type path는 re-export로 보존합니다.
- explicit read DTO: 145줄 `api/watchReadDto.ts`가 외부 `unknown`의 watch identity·실제 달력 날짜와
  candidate identity·timezone-aware 출도착·seat·정수 priority를 검증합니다. top-level arbitrary key는
  버리고 잘못된 개별 후보만 drop하며 evidence·latest observation/attempt·operational 값은 명시적인
  `unknown` 필드로 기존 projector에 전달합니다. 기존 한글 `ApiError`와 fail-closed 기본값은 유지합니다.
- 호환 범위: `api/watches.ts`는 804→729줄로 줄었지만 `MappedWatch`, `mapWatch`, payload builder, CRUD
  transport와 Vitest mock module path는 이동하지 않았습니다. `fixtures/demoData.ts`와 Home 표시 타입만
  canonical domain owner를 직접 사용합니다. normalized domain snapshot·feature ViewModel 분리는 후속입니다.
- 테스트: 신규 DTO 4개 선언과 canonical owner boundary 1개를 추가했습니다. 기존
  `watchesApi.test.ts`의 30개 선언·33개 실행·76개 assertion은 그대로이며 focused 6개 파일·66건을
  통과했습니다. 독립 리뷰에서 observation mode owner ratchet 누락 P3를 발견해 네 번째 타입도 고정했고
  보정 뒤 P0~P3 잔여 지적은 없었습니다.
- 확인된 검증: 전체 Vitest 80개 파일·576건, ESLint 오류 0개·고정 legacy warning 12개, strict
  typecheck, production build와 `git diff --check`를 통과했습니다. 기존 500 kB 초과 chunk 경고만
  유지됐습니다. Sites·E2E는 배포 경계·화면·CSS·endpoint를 바꾸지 않은 parser/type 이동이라 반복하지
  않았습니다.
- 운영 검증: 같은 작업의 후속 API 코드 슬라이스와 함께 `experimental-rail` 전체 이미지를 build한 뒤
  volume 삭제 없이 force-recreate했습니다. migration·log-init exit 0, 장기 서비스 11개 healthy,
  API health·ready와 proxy health 200, 재생성 뒤 최근 안전한 오류 표식 0건을 확인했습니다.

### 2026-08-05 스물다섯 번째 구조 슬라이스 D

- production OfficialHandoff CSS owner: `features.css`의 공식 인계 selector 187줄과
  `responsive.css`의 760px override 48줄을 값·선언·cascade 순서 변경 없이 237줄
  `features/official-handoff/officialHandoff.css`로 이동했습니다. `features.css`는 3,837→3,650줄,
  `responsive.css`는 1,318→1,270줄로 줄었습니다. 아직 다른 화면이 소비하는
  `.official-handoff-note`는 legacy owner에 남겼습니다.
- cascade·접근성 회귀: 전역 import를 `tokens → base → shell → features → operations → app-surfaces →
  officialHandoff → officialSeatConfirmation → responsive` 아홉 경계로 확장했습니다. 구조 테스트는 새
  owner 시작·760px media·legacy modal selector 잔존 0과 exact import order를 고정합니다. 기본
  Playwright는 1,440px·320px·720px에서 실제 dialog의 viewport 경계·가로 overflow 없음·44px 행동 영역·
  app shell inert·Escape 뒤 trigger focus 복원을 확인합니다.
- 확인된 검증: CSS 구조 focused Vitest 2개 파일·13건, 전체 Vitest 80개 파일·576건, ESLint 오류 0개·
  고정 legacy warning 12개, strict typecheck, production build, Sites 4건, 기본 Playwright E2E 14건,
  독립 리뷰의 닫기 접근성 이름 보정 뒤 P0~P3 잔여 없음과 `git diff --check`를 통과했습니다. 기존
  500 kB 초과 chunk 경고만 유지됐습니다.
- 운영 검증 범위: CSS 이동 자체에는 별도 Compose 재배포가 필요하지 않지만, 같은 작업의 코드
  슬라이스와 함께 최신 통합 tree를 전체 build·force-recreate해 health를 확인했습니다.

### 2026-08-05 스물다섯 번째 구조 슬라이스 E

- watch update application: 273줄 `watch_management/update_application.py`가 dedupe key·outbox·clock·
  채널 검증·focused 정원 검증의 다섯 typed dependency port와 `WatchCommandNotFound`·
  `WatchCommandConflict`·`WatchCommandValidationError`를 소유합니다. 대상 행을 candidate와 함께 다시
  잠그고 활성 작업 수정 필드·채널·focused 정원·후보/시간창 정합성, mutation → due 재무장 → dedupe →
  `watch.updated` outbox → commit → refresh 순서를 보존합니다. outbox 실패 시 commit·refresh하지 않습니다.
- facade·경계: `services.update_watch`와 두 validator는 호출 시점 dependency 조립과 404·409·422 HTTP
  변환만 담당하며 기존 public module/function identity와 monkeypatch seam을 보존합니다. create watch도
  같은 validator wrapper를 계속 사용합니다. application은 FastAPI·services·worker·provider runtime에
  의존하지 않고 boundary test는 `FOR UPDATE`와 명령 transaction·commit·refresh 소유를 고정합니다.
  `services.py`는 1,368→1,253줄로 줄었습니다.
- 테스트·정적 경계: 신규 owner 15건이 stale 재조회·`selectinload`·`populate_existing`·행 잠금,
  상태별 수정 제한, 후보/시간창·채널·focused 정원, 별도 clock 호출, outbox 원자성과 기존 monkeypatch
  seam을 검증합니다. strict mypy ratchet은 14→15개 파일로 확장했습니다.
- 확인된 검증: owner·module-boundary focused pytest 28건, API 전체 pytest 1,500건, Ruff `E/F/I`,
  format ratchet 60개, strict mypy 15개 파일 오류 0, `uv lock --check`, 독립 리뷰 P0~P3 지적 없음과
  `git diff --check`를 통과했습니다. 기존 Starlette/httpx deprecation 경고 1건은 유지됐습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API health·ready와 proxy health 200, 재생성 뒤
  최근 안전한 오류 표식 0건을 확인했습니다.

### 2026-08-05 스물다섯 번째 구조 슬라이스 F

- watch projection owner: 432줄 `api/watchProjection.ts`가 `MappedWatchCandidate`·
  `SeatFoundObservation`·`ReservationCandidateContext`·`MappedWatch`와 DTO→공용 application read model
  투영을 소유합니다. 상태·좌석 label, 최신 관측 source/freshness, 등록 evidence, operational·reservation
  attempt, 후보 우선순위와 공식 URL fail-closed 정책을 본문 변경 없이 이동했습니다.
- transport·호환 경계: `api/watches.ts`는 729→324줄로 줄고 create payload·멱등 키·CRUD transport만
  유지합니다. 기존 `api/watches`의 타입과 `mapWatch` export path는 같은 함수 객체를 다시 export해
  App·NewWait·Home·mutation·fixture 호출자와 Vitest mock 경로를 보존합니다. `MappedWatch`를 Home 전용
  ViewModel로 내리거나 구조적으로만 호환되던 handoff 타입을 합치는 변경은 후속 단계로 분리했습니다.
- 의존성·회귀: owner 선언 단일성과 `watchReadDto → watchProjection → watches CRUD` 방향, 역방향·feature
  의존 부재와 compatibility 함수 identity를 구조 테스트로 고정했습니다. 독립 리뷰는 이동 전후
  `mapWatch` 본문 동일성과 공개 계약을 확인했고 P0~P3 지적은 없었습니다.
- 확인된 검증: focused Vitest 6개 파일·59건, 전체 Vitest 81개 파일·579건, ESLint 오류 0개·고정
  legacy warning 12개, strict typecheck, production build, Sites 4건, 기본 Playwright E2E 14건과
  `git diff --check`를 통과했습니다. 기존 500 kB 초과 chunk 경고만 유지됐습니다.
- 운영 검증: 같은 작업의 API 코드 슬라이스와 함께 `experimental-rail` 전체 이미지를 build한 뒤 volume
  삭제 없이 force-recreate했습니다. migration·log-init exit 0, 장기 서비스 11개 healthy, API
  health·ready와 proxy health 200, 재생성 뒤 최근 안전한 오류 표식 0건을 확인했습니다.

### 2026-08-05 스물다섯 번째 구조 슬라이스 G

- 예약 화면 CSS owner: `features.css`의 `.reservation-summary`·`.reservation-list`·
  `.reservation-item`·`.reservation-payment-deadline` 기본 90줄과 `responsive.css`의 760px 27줄을
  값·선언·순서 변경 없이 118줄 `features/reservations/reservations.css`로 이동했습니다. 공용
  `.countdown`, `.button`, `.status-pill`은 기존 owner에 남겼고 `features.css`는 3,650→3,560줄,
  `responsive.css`는 1,270→1,243줄로 줄었습니다.
- cascade·실화면 회귀: 전역 import를 `tokens → base → shell → features → operations → app-surfaces →
  reservations → officialHandoff → officialSeatConfirmation → responsive` 열 경계로 확장했습니다. 구조
  테스트는 새 owner 시작·760px media·legacy 잔존 0과 exact import order를 고정하고, 원본 기본 90줄·
  반응형 27줄과 새 owner의 행 단위 동등성을 확인했습니다.
- E2E 보강: 처음에는 `seat_found` 한 건만 있어 결제기한과 항목 행동을 렌더링하지 않는 P2 공백이
  있었습니다. 홈 활성 목록에는 섞이지 않는 기한 경과 `payment_required` fixture를 추가해 두 예약 항목,
  `.reservation-payment-deadline`, `공식 확인 열기` 44px 행동과 page/root overflow를
  1,440px·320px·720px 두 브라우저 프로젝트에서 직접 확인했고 재리뷰 P0~P3 잔여가 없습니다.
- 확인된 검증: CSS owner 구조 Vitest 1개 파일·5건, 전체 Vitest 81개 파일·579건, ESLint 오류 0개·
  고정 legacy warning 12개, strict typecheck, production build, Sites 4건, responsive E2E 6건과 기본
  Playwright E2E 14건, `git diff --check`를 통과했습니다. 기존 500 kB 초과 chunk 경고만 유지됐습니다.
- 운영 검증 범위: CSS 이동 자체에는 별도 Compose 재배포가 필요하지 않지만, 같은 작업의 코드
  슬라이스와 함께 최신 통합 tree를 전체 build·force-recreate해 health를 확인했습니다.

### 2026-08-05 스물다섯 번째 구조 슬라이스 H

- reservation attempt policy: 53줄 `reservations/attempt_policy.py`가 confirmed-absent retry episode
  prefix, 결제 보류 종료 뒤 새 episode를 허용하는 비가용 edge 상태와 exact negative confirmation 판정을
  소유합니다. 모델 대신 `ConfirmedAbsentRetrySource` field Protocol을 사용해 ORM·SQL·FastAPI·runtime
  역의존을 없앴고, observation group의 중복 prefix도 canonical policy import로 교체했습니다.
- reservation attempt claim application: 183줄 `reservations/attempt_claim_application.py`가 existing
  episode replay, 최신 attempt·retry 자격·새 관측 edge, savepoint add/flush와 `IntegrityError` 승자 재조회,
  candidate/watch mutation·`RESERVING` 전이·attempt outbox를 소유합니다. savepoint만 열고 caller의
  transaction에 참여하며 commit·rollback·refresh는 호출하지 않습니다.
- compatibility seam: `services.begin_reservation_attempt`은 기존 signature와 module identity를 유지하고
  호출 시점의 transition·outbox·payment-hold·confirmed-absent globals와 actionable status를 typed
  dependency로 조립합니다. execution application의 provider I/O 전 claim commit과 mock HTTP UoW는
  바뀌지 않았으며 `services.py`는 1,253→1,131줄로 줄었습니다.
- 테스트·정적 경계: policy matrix, exact identity alias, canonical observation import, application claim·
  replay·event, wrapper monkeypatch seam과 begin_nested/flush·commit/rollback/refresh 부재를 owner/boundary
  테스트로 고정했습니다. strict mypy ratchet은 15→17개 파일로 확장했습니다.
- 확인된 검증: 최신 owner·module-boundary pytest 29건, 관련 reservation/worker pytest 75+2건,
  API 전체 pytest 1,516건, Ruff `E/F/I`, format ratchet 60개, strict mypy 17개 파일 오류 0,
  `uv lock --check`, 독립 리뷰 P0~P3 지적 없음과 `git diff --check`를 통과했습니다. 기존
  Starlette/httpx deprecation 경고 1건은 유지됐습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API health·ready와 proxy health 200, 재생성 뒤
  최근 안전한 오류 표식 0건을 확인했습니다.

### 2026-08-05 스물다섯 번째 구조 슬라이스 I

- timetable refresh settings CSS owner: `.refresh-preference-*`의 기본 selector 구간과 760px override를
  선언·값·상대 순서 변경 없이 181줄 `features/settings/timetableRefreshSettings.css`로 이동했습니다.
  여러 설정 surface가 공유하는 `.setting-row*`와 바로 뒤 `NewWait`의 `.step-three-*`는 기존 owner에
  남겼습니다. `features.css`는 3,560→3,401줄, `responsive.css`는 1,243→1,222줄로 줄었습니다.
- cascade·구조 계약: 전역 import를 `tokens → base → shell → features → operations → app-surfaces →
  reservations → timetableRefreshSettings → officialHandoff → officialSeatConfirmation → responsive`
  열한 경계로 확장했습니다. owner 시작·desktop selector exact order·760px media 종료·legacy 잔존 0과
  원본 행 단위 동등성을 구조 테스트로 고정했습니다.
- 실화면 회귀: 기본 responsive E2E가 `설정 → 화면 동작`을 열어 card·fields·actions·두 input wrapper와
  input control·저장 행동의 viewport/내부 overflow를 1,440px·320px·720px 두 브라우저 프로젝트에서
  확인합니다. input wrapper와 저장 행동은 44px 이상을 직접 검증합니다.
- 확인된 검증: focused Vitest 4개 파일·23건, 전체 Vitest 81개 파일·579건, ESLint 오류 0개·고정
  legacy warning 12개, strict typecheck, production build, Sites 4건, responsive E2E 6건과 기본 E2E
  14건, self-review P0~P3 없음과 `git diff --check`를 통과했습니다. 기존 500 kB 초과 chunk 경고만
  유지됐습니다.
- 운영 검증 범위: CSS 이동 자체에는 별도 Compose 재배포가 필요하지 않지만, 같은 작업의 코드
  슬라이스와 함께 최신 통합 tree를 전체 build·force-recreate해 health를 확인했습니다.

### 2026-08-05 스물다섯 번째 구조 슬라이스 J

- reservation attempt result application: 285줄 `reservations/attempt_result_application.py`가 다섯
  typed dependency와 FastAPI 비의존 `ReservationAttemptAlreadyCompleted`를 소유합니다. attempt의
  outcome·credential·완료 시각·confirmation, 기한 경과 UNKNOWN fence, 정상 보류·낮은 우선순위 후보
  억제, 감시 재개·auth/blocked/failed 전이와 기존 outbox payload/dedupe를 같은 순서로 이동했습니다.
- facade·UoW: `services.complete_reservation_attempt`은 호출 시점의 transition·outbox·clock·result
  policy·confirmation recorder를 조립하고 이미 끝난 attempt만 exact `reservation attempt was already
  completed` HTTP 409로 변환합니다. application은 caller transaction에 참여하며 lock·commit·rollback·
  refresh를 소유하지 않습니다. `record_reservation_confirmation`은 같은 canonical 함수 객체로
  re-export하고 provider/URL/timezone 오류 타입과 문구를 유지했습니다. `services.py`는
  1,131→956줄로 줄었습니다.
- 정적 wiring 보정: 첫 Protocol의 가변 `*args/**kwargs`가 실제 service signature 호환성을 가릴 수 있다는
  P3를 반영해 idempotency key·reason·observation 선택 인자를 정확히 명시하고, services 조립 전용 strict
  mypy witness를 추가했습니다. owner/UoW boundary와 strict mypy ratchet은 17→18개 파일로 확장됐고
  재리뷰 P0~P3 잔여가 없습니다.
- 확인된 검증: owner 11건, 최신 owner·module-boundary pytest 29건, 관련 reservation 68건·worker 핵심
  6건, API 전체 pytest 1,530건, Ruff `E/F/I`, format ratchet 60개, strict mypy 18개 파일과 wiring witness
  오류 0, `uv lock --check`, `git diff --check`를 통과했습니다. 기존 Starlette/httpx deprecation 경고
  1건은 유지됐습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API health·ready와 proxy health 200, 재생성 뒤
  최근 안전한 오류 표식 0건을 확인했습니다.

### 2026-08-05 스물다섯 번째 구조 슬라이스 K

- KORAIL sidecar runtime owner: 178줄 `korail_sidecar/runtime.py`가 `ReadinessGate`, engine enum·env
  parser, Playwright/Pydoll client factory·Pydoll lazy import, readiness probe 선택과 automation 조립을
  소유합니다. 환경값 이름·기본값·범위·오류 문구, cache/cooldown과 retry/timeout/fail-closed 동작은
  바꾸지 않았고 기존 service logger namespace도 보존했습니다.
- compatibility facade: `korail_browser_adapter_service.py`는 FastAPI route·lifespan·internal bearer·
  credential redaction·reservation/confirmation DTO 변환을 계속 소유하면서 runtime 객체를 같은 identity로
  re-export합니다. `create_adapter_app` lifespan은 호출 시점 service globals를 사용하고 두 모듈의 표준
  `time` 객체가 같아 기존 factory·probe·`time.monotonic` monkeypatch path가 유지됩니다. service는
  603→453줄로 줄었습니다.
- 경계·검토 보정: runtime의 FastAPI/facade 역의존과 public/private compatibility identity를 새 테스트로
  고정했습니다. 첫 리뷰가 발견한 표준 mypy allowlist 누락 P2와 `from fastapi import ...`를 놓치는 AST
  P3를 보정해 Import/ImportFrom root를 모두 검사하고 strict ratchet을 18→19개 파일로 확장했습니다.
  Pydoll module은 fresh runtime import에서 로드되지 않으며 재리뷰 나머지 P0~P3는 없습니다.
- 확인된 검증: runtime/browser automation pytest 64건, reserve·confirmation endpoint pytest 108건,
  API 전체 pytest 1,530건, Ruff `E/F/I`, format ratchet 60개, strict mypy 19개 파일 오류 0,
  `uv lock --check`, `git diff --check`를 통과했습니다. 기존 Starlette/httpx deprecation 경고 1건은
  유지됐습니다.
- 운영 검증: `experimental-rail` 전체 이미지를 build한 뒤 volume 삭제 없이 force-recreate했습니다.
  migration·log-init exit 0, 장기 서비스 11개 healthy, API health·ready와 proxy health 200, 재생성 뒤
  최근 안전한 오류 표식 0건을 확인했습니다.
- 남은 Phase 8 범위: sidecar HTTP/lifespan owner, Pydoll DOM driver/parser, read-only 검색·replay lifecycle,
  인증 actor와 예약·확인 application은 후속 수직 슬라이스에서 의존 방향과 fixture gate를 함께 고정합니다.

### 2026-08-05 스물여섯 번째 구조 슬라이스

- 활동 중 대기 행: `ActiveWatchList.tsx`의 API/domain 해석을 strict
  `features/home/activeWatchViewModel.ts`로 옮겼습니다. owner는 정책 switch 가능 여부, 인증 요약,
  결제 보류 종료·수동 확인·공식 인계의 표시 판단을 소유하고 목록 컴포넌트는 typed presentation과
  사용자 행동만 조립합니다. 결제 필요 목록은 `features/home/paymentRequiredViewModel.ts`에서 production
  watch와 legacy snake_case 입력을 camelCase 표시 계약으로 정규화해 Home과 목록 컴포넌트에 전달합니다.
  `ReservationPolicyControl`의 기본·760px 반응형 selector는 별도 CSS owner로 이동했으며, 공용 selector와
  cascade 순서는 유지했습니다.
- 예약 정합화: `reservations/reconciliation_policy.py`가 bounded retry 상한·간격을, FastAPI·Celery
  비의존 `reconciliation_state_application.py`가 confirmation 결과의 상태 전이·outbox 적용을 소유합니다.
  상위 reconciliation application은 due 선택, execution lease, credential generation 확인과 provider actor
  lifecycle만 조립합니다. 기존 service facade·worker task·HTTP 계약은 호환 경계로 보존했습니다.
- KORAIL sidecar: FastAPI route·lifespan·internal bearer·DTO 정규화는 `korail_sidecar/http.py`로 이동했고,
  service facade는 compatibility dependency를 호출 시점에 조립합니다. Pydoll 동일 세션 보류 판독은
  `korail_pydoll_confirmation_reader.py`가 좁은 snapshot Protocol과 상세→공식 목록 fallback을 소유합니다.
  보호·인증·복수/불완전 일치는 fail-closed이며 예약·취소·결제 호출은 만들지 않습니다. 읽기 전용
  route별 replay lease·TTL·횟수·LRU·capture/install·폐기·close는 strict
  `korail_pydoll_http_replay.py` manager로 이동하고 Pydoll client에는 검색/session actor 조립만 남겼습니다.
- PostgreSQL fencing: acceptance script가 독립 session뿐 아니라 spawn한 holder·takeover process, 별도
  backend PID, 실제 lock wait, commit 뒤 fencing token +1과 stale epoch 거부를 확인하도록 확장했습니다.
  격리 PostgreSQL 16 service를 사용하는 `postgres-execution-lease-fencing` CI job이 migration 뒤 이를
  상시 실행합니다. 이는 execution lease에 한정하며 watch/candidate/circuit 전체 경합은 아직 별도 범위입니다.
- 확인된 검증: 웹은 Vitest 83개 파일·593건, ESLint 오류 0·기존 baseline warning 12개, strict typecheck,
  production build, Sites 4건, 기본 E2E 14건을 통과했습니다. API는 focused owner·boundary 회귀, Ruff
  `E/F/I`, format ratchet 59개, strict mypy 25개 파일, `uv lock --check`와 전체 pytest 1,566건을
  통과했습니다. 기존 Starlette/httpx deprecation warning 1건은 유지됐습니다.
- 통합 운영 검증: `experimental-rail`에서 `config --quiet` → 전체 build → `up -d --force-recreate`를
  수행했고 migration·log-init 2개 정상 종료, 장기 서비스 11/11 healthy, API health·ready와 proxy health
  200, 재생성 뒤 최근 안전한 오류 표식 0건을 확인했습니다.
- 남은 Phase 8 범위: sidecar DOM driver·parser와 read-only UI 검색 orchestration, 인증 actor와 단발
  예약 workflow의 더 작은 owner 분리는 계속 미완료입니다. 이 상태를 HTTP·confirmation reader·replay
  manager 분리만으로 완료라고 표현하지 않습니다.

### 2026-08-05 스물일곱 번째 구조 슬라이스

- Reservations ViewModel: `features/reservations/reservationViewModel.ts`가 production `MappedWatch`와
  legacy snake_case 입력을 camelCase 표시 계약으로 변환합니다. 목록·요약·페이지는 raw deadline/URL을
  읽지 않고, 공개 `Reservations` 함수·`ReservationListWatch` 타입 경로와 콜백·정렬·감사 행을
  보존합니다. legacy 공식 URL도 공용 allowlist를 통과한 KORAIL·SRT HTTPS만 CTA로 만듭니다.
- Pydoll read-only search actor: `korail_pydoll_contracts.py`가 snapshot 계약을,
  `korail_pydoll_search_actor.py`가 search lock→replay→direct/UI→capture→submit/result→install→검색
  session cleanup→finalize 순서를 소유합니다. browser facade는 인증·예약 session과 전체 close를 조립하고
  기존 공개 export·호출 시점 monkeypatch seam을 유지합니다.
- PostgreSQL observation fencing: 격리 opt-in script가 실제 observation application holder의 lease lock과
  takeover wait/token +1, stale prepare·defer·persist·circuit·apply·전체 process의 기록 0건, 정·역순 두
  process의 8회 무교착 실행을 검증합니다. 검증 대상 관찰 경로는 모두 lease를 먼저 잠그며,
  downstream 순서는 prepare watch→circuit, defer watch `id` 정렬, persist
  watch→candidate/observation, circuit check는 circuit, apply는 watch→circuit 순서입니다. CI PostgreSQL
  16 job은 generic lease 검사 뒤 이 검사를 실행합니다.
- 확인된 검증: 웹 Vitest 84개 파일·599건, ESLint 오류 0·기존 warning 12개, strict typecheck,
  production build, Sites 4건, 기본 E2E 14건을 통과했습니다. API는 전체 pytest 1,568건, Ruff `E/F/I`,
  format ratchet 59개, strict mypy 27개 파일, `uv lock --check`를 통과했고 기존 Starlette/httpx
  deprecation warning 1건만 유지됐습니다. 두 PostgreSQL acceptance script는 CI 경로와 compile을
  확인했고 observation 검사는 별도 임시 PostgreSQL 16 빈 DB에서 37.9초로 통과했습니다.
- 통합 운영 검증: `experimental-rail` 전체 build·force-recreate 뒤 migration·log-init 2개 exit 0,
  장기 서비스 11/11 healthy, API health·ready와 proxy health 200, 최근 안전한 오류 표식 0건을
  확인했습니다.
- 남은 핵심 범위: Pydoll 공용 page safety, 인증 session actor, 단발 예약 workflow, DOM driver 세분화와
  웹 watch snapshot/live reservation notice의 DTO·ViewModel 분리, PostgreSQL 동일 episode·로그인·
  credential 교차 수용 검증입니다.

### 2026-08-05 스물여덟 번째 구조 슬라이스

- 웹 watch read model: `api/watchProjection.ts`가 required camelCase `WatchReadModel`·
  `WatchCandidateReadModel`, 기존 snake_case 공개 입력 `MappedWatch`·`MappedWatchCandidate`, mapper가
  반환하는 교차 타입 `ProjectedWatch`·`ProjectedWatchCandidate`를 구분합니다. 후보 property와 배열은
  readonly이며 production mapper와 demo producer가 priority 순서의 같은 객체·같은 배열에 양쪽 필드를
  기록합니다. 기존 공개 object literal과 `mapWatch` identity는 유지합니다.
- 웹 lifecycle·ViewModel: `features/app/watchLifecycleSnapshot.ts`가 REST/SSE 상태 전이에 필요한 typed
  camelCase snapshot을 소유합니다. inline projector 변경은 polling/SSE lifecycle을 재시작하지 않으면서
  이후 REST와 SSE에 최신 projector를 사용합니다. Home 활동·결제와 Reservations ViewModel, NewWait
  registration hydration은 canonical model만 읽고 loose snake 입력은 명시한 compatibility adapter로
  격리합니다.
- Pydoll 구조 분리: 공용 보호 판정은 `korail_pydoll_page_safety.py`, 인증 값·session lifecycle은
  `korail_pydoll_auth_contracts.py`·`korail_pydoll_auth_actor.py`, 로그인 DOM은
  `korail_pydoll_login_driver.py`가 소유합니다. 읽기 전용 검색은 actor와 DOM driver, 단발 예약은
  contracts·actor·DOM driver로 나눴습니다. 기존 facade의 public class identity와 생성 전·후 monkeypatch
  seam을 유지하고 검색 actor/driver는 exact 역·날짜·시각 선택·성인 1명 readback·KTX projection,
  예약 actor/driver는 성인 1명 identity와 좌석·예매 click 각 1회·결제/취소 비호출을 보존합니다.
- PostgreSQL reservation/credential fencing: 결과 transaction이 실제 호출 credential generation과 현재
  account generation을 row lock 아래 비교해 과거 결과의 watch·candidate·attempt·outbox·payment write를
  모두 차단합니다. 격리 수용 script는 동일 episode spawn process의 provider 호출 1회, claim/result가
  account를 기다리는 동안 독립 probe의 target row `FOR UPDATE NOWAIT`, credential 교체 직후와 늦은
  payment 결과 처리 후의 watch·candidate·attempt·outbox·payment snapshot이 완전히 동일한지를 실제
  PostgreSQL 16에서 검증합니다. CI는 generic lease→observation→reservation 순서로
  세 검사를 실행합니다.
- 확인된 검증: 웹 Vitest 85개 파일·615건, ESLint 오류 0·기존 warning 12개, strict typecheck,
  production build, Sites 4건, 기본 E2E 14건을 통과했습니다. API는 전체 pytest 1,605건, Ruff `E/F/I`,
  format ratchet 58개, strict mypy 35개 파일, `uv lock --check`를 통과했고 기존 Starlette/httpx
  deprecation warning 1건만 유지됐습니다. 신규 reservation/credential acceptance는 별도 임시
  PostgreSQL 16에서 migration 뒤 통과했습니다.
- 통합 운영 검증: `experimental-rail` 전체 build·force-recreate 뒤 migration·log-init 2개 exit 0,
  장기 서비스 11/11 healthy, API health·ready와 proxy health 200, 최근 안전한 오류 표식 0건을
  확인했습니다. build가 exit 0으로 끝난 직후 Docker Desktop이
  `http2: server: error reading preface ... file has already been closed` 경고를 한 줄 남겼지만, 이어진 전체
  재생성과 health 검증이 통과했고 재생성 뒤 5분 로그의 `ERROR|CRITICAL|Traceback` 표식은 0건이었습니다.
- 남은 항목은 실제 운영 계정·외부 알림 채널·공개 도메인·장시간 failover처럼 로컬 구조 리팩터링과
  분리된 운영 확인, 그리고 설정·알림 등 다른 feature의 DTO/ViewModel·mutation epoch 개선입니다.

### 2026-08-06 스물아홉 번째 구조 슬라이스

- observation recording owner: 기존 `services.record_seat_observation` 본문을 127줄
  `observations/recording_application.py`로 이동했습니다. 새 owner는 operational projection → observation
  add/flush → candidate 상태 → `watch.seat_observed` outbox → 선택적 watch 전이 순서를 보존하고 호출자의
  transaction에 참여하며 commit·rollback·refresh·lock을 직접 소유하지 않습니다.
- compatibility와 production wiring: `services.py`는 기존 signature·module identity를 유지하는 wrapper에서
  호출 시점의 projection·outbox·watch transition 전역을 typed dependency로 조립합니다. worker는 services의
  관측 facade를 통하지 않고 canonical owner를 직접 주입하며, observation group의 잠금·요약·최종 commit
  소유권은 바뀌지 않았습니다. `services.py`는 714→676줄로 줄었습니다.
- 상태 정책 보존: `AVAILABLE`·`LIMITED`·`STANDING_PLUS_SEAT`은 `seat_found`,
  `WAITLIST_AVAILABLE`은 공식 대기 전이, 비가용 상태는 `observed`로 유지했습니다. 예약 claim과 그룹 요약의
  유사 상수는 계약 동일성을 별도 검증하지 않은 채 공용화하면 정책 변경이 될 수 있어 이번 이동에서는
  의도적으로 합치지 않았습니다.
- 경계·회귀 검증: 새 owner의 runtime 역의존과 자체 UoW 종료·lock 유입을 AST gate로 차단하고, 호출 순서,
  outbox payload·dedupe identity, 전이 이유·유예, services monkeypatch seam과 worker canonical wiring을
  회귀 테스트로 고정했습니다. strict mypy ratchet은 35→36개 파일로 확장했습니다.
- 품질 게이트 복구: 앞선 KORAIL GUI 진단 변경 뒤 ratchet과 어긋나 있던 신규 2개 파일과 수정 legacy
  테스트 1개를 의미 변경 없이 Ruff 포맷하고 stale allowlist 항목을 제거했습니다. 확인된 검증은 focused
  pytest 77건, API 전체 pytest 1,619건·skip 1건, Ruff `E/F/I`, format ratchet legacy 57개,
  strict mypy 36개 파일, `uv lock --check`와 독립 리뷰 지적 0건입니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config와 전체 image build를 통과한 뒤 volume
  삭제 없이 force-recreate했습니다. 첫 재생성에서 KORAIL adapter가 noVNC secret 46바이트를 거부하는
  기존 로컬 설정 오류를 로그로 확인했고, 값은 출력하지 않은 채 진단 문서의 생성 절차로 정확한 8 printable
  ASCII 바이트로 교체해 실패 경계를 재검증했습니다. 최종 migration·log-init 2개 exit 0, 장기 서비스
  11/11 healthy, API health·ready와 HTTP proxy health·noVNC page 200, 최근 치명 로그 표식 0건입니다.
  전체 build 종료 뒤 Docker Desktop named-pipe HTTP/2 경고가 있었으나 이어진 재생성·health는 정상입니다.
- 남은 구조 부채: `services.create_watch` 이동은 다음 슬라이스에서 이어서 완료합니다. provider circuit,
  인증 복구와 worker 조립점 축소는 동시성·실패 정책을 섞지 않도록 이후 별도 슬라이스로 유지합니다.

### 2026-08-06 서른 번째 구조 슬라이스

- watch create owner: 기존 `services.create_watch` 본문을 269줄
  `watch_management/create_application.py`로 이동했습니다. 새 owner는 payload hash·replay 단락 → focused
  capacity → experimental gate → channel·official evidence 검증 → aggregate 생성 → idempotency·outbox
  transaction 순서를 그대로 소유하고 FastAPI·config·provider registry·outbox 구현에는 의존하지 않습니다.
- transaction·경합 보존: `add/flush` 뒤 try 범위에서 idempotency record → `watch.created` outbox → commit,
  정상 경로 refresh 순서를 유지했습니다. outbox autoflush 또는 commit의 `IntegrityError`만 rollback한 뒤
  동일 key/hash winner를 재조회하며, key나 winner가 없으면 원 예외를 다시 발생시킵니다. evidence row에
  새 lock을 추가하지 않았고 begin·savepoint·`FOR UPDATE` 소유권도 바꾸지 않았습니다.
- evidence·오류 계약: official provider, 역 node, 정규화 열차번호, UTC 초 단위 출발, 승객 수, 좌석 등급의
  7필드 exact identity와 naive evidence UTC 보정, eligibility 우선·identity·만료 판정 순서를 유지했습니다.
  application은 transport 독립 오류를 내고 services facade가 기존 403·422 및 구조화된 만료 409로만
  변환합니다. `IdempotencyConflict`, DB·provider 및 예상 밖 오류는 기존처럼 상위 경계로 전달됩니다.
- compatibility·크기: `services.create_watch`는 기존 signature·module identity를 유지하고 호출 시점의
  idempotency·capacity·settings·channel·dedupe·provider URL·outbox·clock globals를 typed dependency로
  조립합니다. production route의 import와 응답 계약도 유지했으며 `services.py`는 676→578줄로 줄었습니다.
- 경계·회귀 검증: 새 owner의 runtime 역의존과 create UoW 범위를 AST gate로 고정했습니다. 정상 생성
  순서·UTC candidate·outbox identity, 즉시 replay 단락, outbox autoflush/commit 충돌의 rollback·winner,
  winner 부재 원예외, fail-closed 오류, services current-global seam과 HTTP mapping을 직접 검증했습니다.
  focused pytest 140건, API 전체 pytest 1,634건·skip 1건, Ruff `E/F/I`, format ratchet legacy 57개,
  strict mypy 37개 파일, `uv lock --check`와 독립 리뷰 blocker 0건을 통과했습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config → 전체 image build → volume 삭제 없는
  force-recreate를 다시 수행했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy,
  API health·ready와 HTTP proxy health·noVNC page 200, 재생성 뒤 최근 치명 로그 표식 0건입니다. build
  종료 뒤 Docker Desktop named-pipe HTTP/2 경고는 재현됐지만 실제 재생성과 모든 health는 정상입니다.
- 남은 구조 부채: provider circuit 생성·복구, 인증 상태 회복, worker composition과 `schemas.py`·`models.py`
  transport/persistence hub 축소를 각각 별도 수직 슬라이스로 이어갑니다. 이번 작업에서 유사 정책 상수나
  registration evidence schema를 함께 재설계하지 않았습니다.

### 2026-08-06 서른한 번째 구조 슬라이스

- provider auth recovery owner: 기존 `services.resume_watches_after_verified_provider_login`의 116줄 정책을
  141줄 `provider_account_management/auth_recovery_application.py`로 이동했습니다. 새 owner는 provider별
  `AUTH_REQUIRED` ID 조회 → 상태 조건 재조회와 watch `FOR UPDATE` → 최신 transition → candidate별 최신
  reservation attempt → 주입된 watch transition 순서를 소유하며 services·worker·provider runtime에는
  역의존하지 않습니다.
- reason·시간 계약: `reservation_auth_required`·`reservation_provider_blocked`와 preflight reason은 최신
  transition 시각이 인증 시각 이하일 때만 복구하고 naive 시각은 UTC로 간주합니다. 기존
  `reservation_unknown`은 인증 시각 gate 없이 복구하는 동작을 그대로 유지했습니다. 최종 transition reason
  4종과 provider block 전용 reason도 바꾸지 않았습니다.
- 예약 fence 보존: auth/provider-block은 최신 attempt가 `AUTH_REQUIRED`·`PROVIDER_BLOCKED`, unknown은
  `UNKNOWN`인 failed candidate만 `observed`로 되돌립니다. preflight는 attempt 생성 전 경로이므로 candidate를
  바꾸지 않으며, 불일치·활성 후보와 durable ambiguous-result fence를 삭제하거나 재무장하지 않습니다.
- UoW·compatibility: owner는 commit·rollback·refresh·flush를 호출하지 않고 계정 upsert/runtime caller의
  transaction에 참여합니다. `services`는 기존 signature·module identity를 유지하고 호출 시점의
  `apply_watch_transition`을 typed port로 주입합니다. 계정 write+recovery+commit과 기존 broad
  `IntegrityError` generation-conflict 경계는 유지됐으며 `services.py`는 578→480줄로 줄었습니다.
- 경계·회귀 검증: application의 FastAPI·services·worker·provider runtime·concrete watch management 역의존과
  자체 transaction 종료를 AST/import gate로 막고 watch row lock 존재를 고정했습니다. 네 허용 reason,
  auth/preflight 시각 차단, unknown 무시각-gate, 상태 재조회 실패, candidate/attempt matrix, current-global
  services seam과 예외 무변환을 직접 검증했습니다. focused pytest 54건, API 전체 pytest 1,647건·skip 1건,
  Ruff `E/F/I`, format ratchet legacy 57개, strict mypy 38개 파일, `uv lock --check`와 독립 리뷰 blocker
  0건을 통과했습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config → 전체 image build → volume 삭제 없는
  force-recreate를 수행했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy, API
  health·ready와 HTTP proxy health·noVNC page 200, 재생성 뒤 최근 치명 로그 표식 0건입니다. build 종료 뒤
  Docker Desktop named-pipe HTTP/2 경고는 있었지만 실제 재생성과 모든 health는 정상입니다.
- 남은 구조 부채: provider circuit은 현재 OPEN/HALF_OPEN/MANUAL_HOLD 전이 writer가 없는 상태이므로 복구
  정책을 새로 만들지 않고 CLOSED 초기 row의 경합 안전 생성 owner만 별도 슬라이스로 이동합니다. 이후
  provider account/runtime의 services transition facade 의존, worker composition, 작은 schema feature와
  model mapper 순으로 진행합니다.

### 2026-08-06 서른두 번째 구조 슬라이스

- provider circuit persistence owner: 기존 `services.get_or_create_provider_circuit`의 조회·초기 생성 본문을
  `provider_circuit/application.py`로 이동했습니다. 기존 행은 `OPEN`·`HALF_OPEN`·`MANUAL_HOLD`를 포함한
  상태·reason·generation·manual flag를 바꾸지 않고 반환하며, 없는 행만 `CLOSED`, generation 0,
  `manual_resume_required=False`로 초기화합니다. 새 owner는 circuit 상태 전이·cooldown 복구 정책을 만들지
  않고 최초 행 persistence만 소유합니다.
- UoW·잠금 계약: `lock=True`는 최초 조회와 insert 충돌 뒤 winner 재조회 모두 같은 `FOR UPDATE` 쿼리를
  사용합니다. 생성은 호출자 transaction 안의 `begin_nested()` savepoint에서 add·flush하고, 잡는 예외는
  `IntegrityError`뿐이며 winner가 없으면 같은 예외를 다시 발생시킵니다. owner는 commit·rollback·refresh를
  소유하지 않습니다. PostgreSQL의 없는 행 조회 자체가 gap lock을 제공한다고 표현하지 않았고, 현재 테스트가
  실제 두 세션의 동시 INSERT와 READ COMMITTED winner visibility까지 증명한다고도 기록하지 않습니다.
- compatibility·production wiring: `services.py`는 기존 signature·module identity와 호출 시점 application
  monkeypatch seam을 유지하는 얇은 wrapper를 남겼습니다. worker의 관측·예약 dependency assembly는 중앙
  facade가 아니라 canonical circuit owner를 직접 주입합니다. observation·reservation 쪽 lock 순서와 최종
  commit 소유권은 바꾸지 않았으며 `services.py`는 480→458줄로 줄었습니다.
- 경계·회귀 검증: circuit owner가 services·worker·provider adapter/registry·관측·예약 runtime을 import하지
  못하도록 module boundary를 추가하고 savepoint·flush·`FOR UPDATE`는 요구하되 outer UoW 종료는 금지했습니다.
  기존 상태 무변경, CLOSED 기본값, savepoint 호출 순서, insert 충돌 winner·winner 부재 원예외, lock SQL,
  services 호환 seam과 worker의 두 canonical wiring을 직접 고정했습니다. focused pytest 48건, API 전체
  pytest 1,655건·skip 1건, Ruff `E/F/I`, format ratchet legacy 57개, strict mypy 39개 파일,
  `uv lock --check`, `git diff --check`와 독립 리뷰 blocker 0건을 통과했습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config → 전체 image build → volume 삭제 없는
  force-recreate를 수행했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy, 컨테이너 내부
  API `/healthz`·`/readyz`, HTTP proxy `/healthz`, noVNC page가 모두 200이었고 최근 치명 로그 표식은
  0건입니다. build 종료 뒤 Docker Desktop named-pipe HTTP/2 경고는 있었지만 build exit 0과 재생성·health는
  정상입니다.
- 문서·후속 범위: `ARCHITECTURE.md`와 strict mypy owner 수를 현재 구조에 맞췄습니다. 제품 상태·운영 절차가
  바뀌지 않은 순수 책임 이동이므로 `CHECKLIST.md`는 검토 후 의도적으로 수정하지 않았습니다. 다음 슬라이스는
  worker에 남은 stale 예약 시도 복구의 PENDING cutoff·세 테이블 `SKIP LOCKED`·UNKNOWN fence·outbox·조건부
  commit 계약을 `reservations` owner로 이동하고, 그 뒤 watch transition command와 작은 schema/model
  feature를 다룹니다.

### 2026-08-06 서른세 번째 구조 슬라이스

- stale reservation attempt recovery owner: 기존 worker의 복구 본문을
  `reservations/stale_attempt_recovery_application.py`로 이동했습니다. 새 owner는 provider 구분 없이
  `PENDING`이고 `started_at <= now - 5분`인 attempt를 선택하고, attempt·candidate·watch를 inner join한
  canonical SQL query를 함께 소유합니다. worker에는 동일한 2인자 private wrapper와 due pipeline dependency
  identity를 남겼습니다.
- 잠금·UoW 계약: PostgreSQL `FOR UPDATE OF reservation_attempts, watch_candidates, watches SKIP LOCKED`를
  유지하고 joined-load되는 nullable registration evidence는 SQL에 남더라도 잠금 대상에서는 제외합니다.
  선택된 모든 attempt를 `UNKNOWN`과 입력 `now`의 `finished_at`으로 완결하고, 행이 있을 때만 전체 처리 뒤
  한 번 commit합니다. 빈 sweep은 commit하지 않고 명시적 rollback·begin·savepoint·refresh도 새로 추가하지
  않았습니다.
- 상태·outbox 보존: `RESERVING` watch는 candidate를 `observed`로 둔 채
  `stale_reservation_attempt_requires_manual_check` 이유로 `WATCHING` 전이하고, 기존 `next_check_at`이 없을
  때만 `now`를 설정합니다. `EXPIRED`는 candidate를 `expired`, 그 밖에는
  `reservation_attempted` candidate만 `observed`로 되돌립니다. attempt마다 기존
  `watch.reservation_attempt_recovery_required` event·payload와 attempt ID 기반 dedupe key를 그대로 씁니다.
  `UNKNOWN`은 새 예약을 허용하는 실패가 아니라 durable ambiguous-result fence로 유지됩니다.
- compatibility·경계: wrapper는 호출 시점의 worker `apply_watch_transition`·`add_outbox_event`와 stale window를
  typed dependency로 조립하므로 기존 monkeypatch seam과 due pipeline 호출 계약을 보존합니다. 새 owner가
  worker·services·outbox 구현·watch management·provider runtime에 역의존하지 못하도록 import gate를
  추가하고, 자체 recovery transaction의 `FOR UPDATE`·commit은 요구하되 다른 transaction primitive는
  금지했습니다. 앞선 provider circuit gate도 독립 리뷰 P3를 반영해 outbox·watch/provider runtime 등
  persistence 밖 역의존을 추가 차단했습니다.
- 회귀·품질 검증: 다섯 상태 조합, 기존 next-check 보존, UNKNOWN·finished-at, transition reason,
  recovery outbox·dedupe, 빈 sweep 무-commit, worker current-global seam과 canonical PostgreSQL SQL을 직접
  검증했습니다. focused pytest 32건, API 전체 pytest 1,663건·skip 1건, Ruff `E/F/I`, format ratchet
  legacy 57개, strict mypy 40개 파일, `uv lock --check`, `git diff --check`와 독립 리뷰 P0~P3 지적 0건을
  통과했습니다. 실제 PostgreSQL 다중 worker의 `SKIP LOCKED` 경쟁은 이번에도 compile 계약까지만 확인한
  운영 검증 공백으로 남깁니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config → 전체 image build → volume 삭제 없는
  force-recreate를 다시 수행했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy, 컨테이너
  내부 API `/healthz`·`/readyz`, HTTP proxy `/healthz`, noVNC page 200, 최근 치명 로그 표식 0건입니다.
  build 종료 뒤 Docker Desktop named-pipe HTTP/2 경고는 있었지만 build exit 0과 재생성·health는 정상입니다.
- 문서·후속 범위: `ARCHITECTURE.md`와 strict mypy owner 수를 동기화했습니다. 제품 동작·운영 절차가
  바뀌지 않은 책임 이동이므로 `CHECKLIST.md`는 검토 후 수정하지 않았습니다. 다음 후보는 services의 watch
  transition command/UoW와 `watch_management/http.py`의 services 역의존을 같은 호환 슬라이스에서 정리한 뒤,
  독립성이 높은 관리자 인증 schema/model부터 중앙 허브를 줄이는 순서입니다.

### 2026-08-06 서른네 번째 구조 슬라이스

- watch transition command owner: `services.transition_watch`가 소유하던 row lock과 command transaction을
  61줄 `watch_management/transition_command_application.py`로 이동했습니다. 새 owner는 입력 watch의 ID로
  `populate_existing=True` 재조회와 단일 `FOR UPDATE`를 수행하고, transport 비의존 not-found를 발생시킨
  뒤 주입된 transition을 호출합니다. 성공 경로의 commit→refresh 순서를 보존하고 begin·savepoint·flush·
  rollback이나 예외 복구는 새로 추가하지 않았습니다. `SKIP LOCKED`·`NOWAIT`도 사용하지 않습니다.
- feature runtime 조립: 78줄 `watch_management/transition_runtime.py`가 idempotency·policy·provider
  capability·identity·outbox·notification·clock port를 canonical owner에서 조립합니다. commit하지 않는
  configured transition과 locking command를 구분해 노출합니다. HTTP route는 feature command를 직접
  사용하고 not-found/rejected만 기존 404/409로 변환하며, worker는 configured non-committing transition을
  기존 module-global 이름으로 주입합니다.
- provider 인증 복구 wiring: `provider_account_management/auth_recovery_runtime.py`가 canonical auth recovery와
  feature transition runtime을 조립합니다. provider account 저장과 provider runtime의 prewarm/restore는
  순환 의존을 피하는 local import를 유지하되 더 이상 `services.resume...`를 거치지 않습니다. production의
  HTTP·worker·provider account/runtime에서 transition/auth recovery legacy services symbol 역의존이 없음을
  AST gate로 고정했습니다.
- compatibility 보존: `services.apply_watch_transition`과 `services.transition_watch`는 기존 signature·함수
  identity·`rail_waitlist.services` module identity를 유지합니다. 호출 시점 services globals를 조립하는
  monkeypatch seam과 rejected 409, lock 사이 삭제 404 변환도 그대로 남겼습니다. canonical command는 apply·
  commit·refresh 단계의 예외를 같은 객체로 전파하며 자체 rollback하지 않습니다.
- 회귀·경계 검증: PostgreSQL dialect compile lock SQL, `populate_existing`, apply→commit→refresh 순서, replay 결과 refresh,
  missing row, apply/commit/refresh 실패의 무-rollback, services current-global seam, HTTP 404/409와 production
  runtime identity를 직접 검증했습니다. 독립 리뷰에서 HTTP start capability fixture가 과거 services seam을
  패치해 즉시 enqueue를 놓치는 회귀와 같은 stale seam 2곳을 추가 발견했고, 세 테스트 모두 canonical
  `transition_runtime.get_execution_provider`를 패치하도록 이관했습니다. 최종 관련 pytest 494건과 seam
  회귀 3건, API 전체 pytest 1,678건·skip 1건, Ruff `E/F/I`, format ratchet legacy 56개, strict mypy
  43개 파일, `uv lock --check`, import-order smoke, `git diff --check`와 독립 리뷰 최종 P0~P3 지적 0건을
  통과했습니다. 실제 PostgreSQL 두 세션의 동시 watch transition 경합은 이번 슬라이스에서 별도로 실행하지
  않았습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config → 전체 image build → volume 삭제 없는
  force-recreate를 수행했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy, 새 canonical
  owner의 worker image import, 컨테이너 내부 API `/healthz`·`/readyz`, HTTP proxy `/healthz`, noVNC page
  200과 최근 치명 로그 표식 0건을 확인했습니다. build 종료 뒤 Docker Desktop named-pipe HTTP/2 경고는
  있었지만 build exit 0과 재생성·health는 정상입니다.
- 문서·후속 범위: `ARCHITECTURE.md`와 strict mypy owner 수를 현재 구조에 맞췄습니다. 사용자 상태·API·운영
  절차는 바뀌지 않아 `CHECKLIST.md`는 검토 후 수정하지 않았습니다. 다음 중앙 허브 슬라이스는 production
  fanout과 ORM 관계 위험이 낮은 관리자 인증 schema 3개와 독립 `AdminAccount`·`AdminSession` mapper를
  canonical `admin_auth` owner로 이동하고 중앙 modules에는 동일 객체 compatibility export를 남깁니다.

### 2026-08-06 서른다섯 번째 구조 슬라이스

- 관리자 인증 schema owner: `AuthStatus`, `UsernamePasswordCredentials`, `LoginResult`를
  `admin_auth/schemas.py`로 이동했습니다. 기존 `ApiModel.from_attributes`, 사용자명 strip·casefold와 길이·
  pattern, 비밀번호 길이, naive session expiry의 UTC 보정, `LoginResult`의 기존 datetime 해석을 그대로
  보존했습니다. 중앙 `schemas.py`는 새 owner의 같은 클래스 객체만 compatibility alias로 노출합니다.
- 관리자 인증 mapper owner: 관계가 없는 `AdminAccount`·`AdminSession` 선언을
  `admin_auth/models.py`로 이동했습니다. 컬럼 순서·타입·nullable·unique/index·check constraint·Python 및
  server default·timezone 선언을 바꾸지 않았고, 중앙 `models.py`도 같은 class·Table·mapper 객체를
  재노출합니다. canonical-first와 legacy-first 독립 프로세스에서 SQLAlchemy 경고를 오류로 승격해 mapper가
  각각 한 번만 등록되는지 확인했습니다.
- production 의존과 bootstrap: 인증 route, observation cycle, UI preference application/HTTP가 canonical
  owner를 직접 import하도록 바꾸고 AST 경계 gate로 중앙 schema/model 역의존을 차단했습니다. main과
  Alembic은 기존처럼 중앙 `models.py`를 import해 전체 metadata를 등록합니다. 개별 feature model만 import한
  부분 registry에서 schema bootstrap을 실행하지 않는 현재 계약은 문서에 명시했습니다.
- migration 무변경 증거: migration 파일 diff는 0건이고 0006·0016·0025·0026 회귀가 통과했습니다. 재빌드한
  PostgreSQL 환경의 `alembic check`는 작업 전과 같은 기존 드리프트 8건만 보고했으며 `admin_accounts`의 새
  operation은 0건, 기존 token unique 차이를 제외한 예상 밖 `admin_sessions` operation은 0건입니다. 따라서
  현재 known drift를 숨기거나 `alembic check` 성공으로 기록하지 않았습니다. 현재 head는
  `0026_unified_observation`입니다.
- 확인된 검증: 관련 pytest 140건, API 전체 pytest 1,696건·skip 1건, Ruff `E/F/I`, format ratchet legacy
  54개, strict mypy 45개 파일, `uv lock --check`, 새 owner format check, `git diff --check`와 독립 리뷰
  P0~P3 지적 0건을 확인했습니다. 첫 전체 pytest의 5분 제한 종료 뒤 남은 프로세스를 정리하고 단독으로
  재실행한 결과 8분 32초에 정상 완료했으며, 최장 항목은 이번 owner가 아닌 기존 KORAIL 실브라우저 fixture
  계열이었습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config → 전체 image build → volume 삭제 없는
  force-recreate를 수행했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy, API와 worker
  image의 canonical/legacy 동일 객체 import, 컨테이너 내부 `/healthz`·`/readyz`, HTTP proxy `/healthz`,
  noVNC page 200과 최근 치명 로그 표식 0건을 확인했습니다. build 종료 뒤 Docker Desktop named-pipe
  HTTP/2 경고는 있었지만 build exit 0과 재생성·health는 정상입니다.
- 문서·후속 범위: `ARCHITECTURE.md`의 owner·metadata bootstrap 계약과 strict mypy 수를 동기화했습니다.
  API payload·DB schema·사용자 상태·운영 절차가 바뀌지 않은 구조 이동이므로 `CHECKLIST.md`는 검토 후
  수정하지 않았습니다. 다음 중앙 허브 후보 감사에서는 production consumer가 1곳이고 이동 뒤 feature의
  중앙 schema 의존이 사라지는 `EventRead` → `event_stream/schemas.py`를 최우선으로 정했습니다. 그다음은
  FK·relationship·enum 의존이 없는 `IdempotencyRecord` → `idempotency/models.py` 단일 mapper이며, 둘 다
  중앙 exact-object alias와 import-order·metadata 회귀를 같은 방식으로 유지합니다.

### 2026-08-06 서른여섯 번째 구조 슬라이스

- event stream schema owner: 중앙 `schemas.py`의 `EventRead`를 `event_stream/schemas.py`로 이동하고
  `event_stream/http.py`가 feature-local owner를 직접 import하도록 바꿨습니다. 필드 순서와 필수 여부,
  `ApiModel.from_attributes`, unconstrained 문자열·payload, aware/naive datetime을 별도 보정 없이 직렬화하는
  기존 계약을 보존했습니다. SSE 회귀는 `id`·`event` envelope뿐 아니라 `data:` JSON을 직접 파싱해 5개
  필드·payload·UTC `Z` 표현까지 검증합니다.
- idempotency mapper owner: FK·relationship·enum 의존이 없는 `IdempotencyRecord`를
  `idempotency/models.py`로 이동하고 application이 이를 직접 사용하도록 바꿨습니다. 6개 컬럼의 순서·타입·
  길이·nullable·PK, `uq_idempotency_scope_key`, index·FK·server default 부재를 그대로 유지했습니다. UUID
  문자열과 UTC-aware `created_at`을 생성하는 Python default 의미도 직접 회귀로 고정했습니다.
- compatibility·bootstrap: 중앙 `schemas.py`와 `models.py`는 두 canonical class와 동일한 객체만 alias로
  노출합니다. canonical-first·legacy-first 독립 프로세스를 `-W error`로 실행해 같은 Table·Base metadata와
  mapper 1개를 확인했습니다. event HTTP와 idempotency application의 relative import level을 AST로 고정하고,
  새 owner가 중앙 hub·transport·runtime으로 역의존하지 못하도록 module boundary를 추가했습니다.
- migration 무변경 증거: migration 파일 diff는 0건이며 `idempotency_records`를 만드는 기존 0001 계약을
  바꾸지 않았습니다. 재빌드한 PostgreSQL의 `alembic check`는 변경 전과 동일한 known drift 8건만
  보고했고 `idempotency_records` 신규 operation은 0건입니다. 이를 `alembic check` 성공으로 기록하지
  않았으며 현재 head는 `0026_unified_observation`입니다.
- 확인된 검증: 최종 feature-owner focused pytest 60건, 넓은 관련 pytest 134건, API 전체 pytest
  1,706건·skip 1건, Ruff `E/F/I`, format ratchet legacy 54개, strict mypy 47개 파일,
  `uv lock --check`, 새 owner format check와 `git diff --check`를 통과했습니다. 전체 suite는 9분 12초였고
  최장 20.61초 항목은 이번 owner가 아닌 기존 KORAIL 실브라우저 fixture였습니다. 독립 리뷰는 최종
  P0~P3 지적 0건입니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config → 전체 image build → volume 삭제 없는
  force-recreate를 수행했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy, API와 worker
  image의 canonical/legacy 동일 객체·mapper 등록, 컨테이너 내부 `/healthz`·`/readyz`, HTTP proxy
  `/healthz`, noVNC page 200과 최근 치명 로그 표식 0건을 확인했습니다. build 종료 뒤 Docker Desktop
  named-pipe HTTP/2 경고는 있었지만 build exit 0과 재생성·health는 정상입니다.
- 문서·후속 범위: `ARCHITECTURE.md`의 feature owner와 `CODE_CONVENTIONS.md`의 strict mypy 범위를
  동기화했습니다. API wire·DB schema·사용자 상태·운영 절차가 바뀌지 않은 구조 이동이므로 병행 중인
  `CHECKLIST.md` 사용자 변경은 보존하고 이번 슬라이스에서는 수정하지 않았습니다. 다음 후보 감사에서는
  pairing·credential·challenge·snapshot을 함께 소유하는 `browser_companion` bounded context를 선정했습니다.
  schema 12개와 내부 FK·relationship만 가진 mapper 5개를 같은 feature로 이동하되, 먼저 순환 방지용
  provider schema base와 열차 identity pure helper를 중립 경계로 분리하고 각 단계의 exact-object·wire·
  metadata 호환을 검증합니다. 별도 실행 서비스인 `korail_sidecar`와는 합치지 않습니다.

### 2026-08-06 서른일곱 번째 구조 슬라이스

- browser companion schema owner: 중앙 `schemas.py`의 KORAIL browser snapshot·pairing·credential·challenge
  transport schema 12개를 `browser_companion/schemas.py`로 이동했습니다. 열차 번호·노선·KST 운행일·좌석
  상태·승객 수·pairing UUID·요청 body hash의 검증 순서와 범위, snapshot 요청의 `extra="forbid"`, read
  DTO의 기존 datetime 해석을 그대로 보존했습니다. provider 공용 strict Pydantic base는
  `provider_schema_base.py`, 보호 표식과 공식 열차 번호 정규화는 side effect가 없는
  `official_rail_identity.py`로 분리해 feature가 중앙 schema hub를 역참조하지 않게 했습니다.
- browser companion mapper owner: snapshot batch·seat snapshot·pairing·credential·challenge mapper 5개를
  `browser_companion/models.py`로 이동했습니다. 컬럼 순서·타입·nullable·Python default, check·unique·index,
  FK 삭제 정책, batch↔snapshot relationship·cascade와 enum 저장 형식을 바꾸지 않았습니다. migration의
  `accepted_in_window` server default와 ORM의 Python default 차이, migration에만 이름이 있는 batch
  credential FK·challenge unique도 구조 이동 중 임의로 정리하지 않고 기존 계약으로 보존했습니다.
- compatibility·production wiring: 중앙 `schemas.py`와 `models.py`는 17개 canonical class와 같은 객체만
  alias로 다시 노출합니다. canonical-first·legacy-first 독립 프로세스를 `-W error`로 실행해 schema 내부
  타입 identity와 Table·Base metadata·mapper 단일 등록을 확인했습니다. browser bridge와 공식 근거 HTTP는
  feature owner를 직접 사용하고, 공식 열차 identity 소비자는 중립 helper를 import하도록 바꿨습니다. AST
  경계는 새 schema·model·base·helper의 중앙 hub·transport·runtime 역의존과 production import 회귀를
  차단합니다.
- migration 무변경 증거: migration 파일 diff는 0건입니다. 재빌드한 PostgreSQL의 `alembic check`는 작업
  전과 동일한 known drift 8건만 보고했고 browser companion 테이블 관련 신규 operation은 0건입니다.
  따라서 이를 `alembic check` 성공으로 기록하지 않았으며 현재 head와 기존 migration 계약을 유지합니다.
- 확인된 검증: 최종 owner·경계 pytest 90건, browser automation까지 포함한 관련 pytest 151건, API 전체
  pytest 1,759건·skip 1건, Ruff `E/F/I`, strict mypy 51개 파일, `uv lock --check`, 새 owner 8개 파일의
  format check와 `git diff --check`를 통과했습니다. 전체 suite는 8분 53초였습니다. 독립 리뷰는 발견한
  Ruff `ISC004` 한 건을 수정한 뒤 최종 P0~P3 지적 0건입니다. 진행 중에는 병행 수정 중인 Pydoll 파일의
  포맷 상태를 덮어쓰지 않고 격리했으며, 해당 병행 작업이 정리된 뒤의 전체 format ratchet 결과는 다음
  슬라이스 통합 검증에 기록했습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config → 전체 image build → volume 삭제 없는
  force-recreate를 수행했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy, 컨테이너의
  canonical/legacy schema·mapper identity, 내부 `/healthz`·`/readyz`, HTTP proxy `/healthz`, noVNC page 200과
  최근 치명 로그 표식 0건을 확인했습니다. build 종료 뒤 Docker Desktop named-pipe HTTP/2 경고는 있었지만
  build exit 0과 재생성·health는 정상입니다.
- 문서·후속 범위: `ARCHITECTURE.md`의 bounded context와 `CODE_CONVENTIONS.md`의 strict mypy 범위를
  동기화했습니다. API wire·DB schema·사용자 상태·운영 절차가 바뀌지 않은 순수 책임 이동이므로 병행 중인
  `CHECKLIST.md`의 모바일·PWA·Android 변경은 보존하고 이번 슬라이스에서 수정하지 않았습니다. 다음 최소
  후보는 이미 application package가 있는 `provider_circuit`의 독립 mapper를 feature owner로 옮겨 중앙
  `models.py` exact-object 호환 alias와 production 직접 import를 고정하는 작업입니다.

### 2026-08-06 서른여덟 번째 구조 슬라이스

- provider circuit mapper owner: 중앙 `models.py`의 독립 `ProviderCircuit` mapper를 기존 bounded context의
  `provider_circuit/models.py`로 이동했습니다. 9개 컬럼의 순서·타입·nullable, provider·state enum 이름과
  저장값, UUID·`CLOSED`·boolean·generation·UTC `updated_at` Python default와 onupdate, 5개 check와
  state/cooldown index를 그대로 보존했습니다. ORM의 무명 provider unique와 migration 0005의
  `uq_provider_circuit_provider` 이름 차이도 구조 이동 중 임의로 바꾸지 않았습니다.
- compatibility·production wiring: 중앙 `models.py`는 canonical class·Table·mapper와 같은 객체만 alias로
  다시 노출합니다. circuit application은 package-local model을 사용하고, services compatibility facade와
  운영 요약·관찰 그룹·예약 실행은 feature model을 직접 import합니다. 다섯 production consumer의 상대
  import level을 AST로 고정하고, 새 model이 application·중앙 hub·transport·runtime으로 역의존하지 못하도록
  module boundary를 추가했습니다. canonical-first·legacy-first 독립 프로세스는 `-W error`와
  `configure_mappers()` 아래 mapper 1개·같은 metadata table·relationship 부재를 확인합니다.
- migration 무변경 증거: migration 파일 diff는 0건입니다. 재빌드한 PostgreSQL의 `alembic check`는 작업
  전과 같은 known drift 8건만 보고했고 `provider_circuits` 관련 신규 operation은 0건입니다. 이를
  `alembic check` 성공으로 표현하지 않았으며 중앙 `models.py` metadata bootstrap 계약을 유지합니다.
- 확인된 검증: 구조·계약·경계 pytest 82건, persistence·operations·observation·reservation·worker 관련
  pytest 33건, API 전체 pytest 1,774건·skip 1건, Ruff `E/F/I`, strict mypy 52개 파일, `uv lock --check`,
  새 owner format check, format ratchet legacy 53개와 import-order/runtime smoke를 통과했습니다. 전체
  suite는 10분 2초였습니다. 독립 리뷰는 최종 P0~P3 지적 0건입니다. 수정한 legacy `operations.py`는
  전체 포맷 후 format allowlist에서 제거했습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config → 전체 image build → volume 삭제 없는
  force-recreate를 수행했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy, 컨테이너의
  canonical/legacy mapper identity, 내부 `/healthz`·`/readyz`, HTTP proxy `/healthz`, noVNC page 200과 최근
  치명 로그 표식 0건을 확인했습니다. build 종료 뒤 Docker Desktop named-pipe HTTP/2 경고는 있었지만 build
  exit 0과 재생성·health는 정상입니다.
- 문서·후속 범위: `ARCHITECTURE.md`의 mapper owner와 `CODE_CONVENTIONS.md`의 strict mypy 범위를
  동기화했습니다. DB schema·API wire·사용자 상태·운영 절차가 바뀌지 않아 `CHECKLIST.md`는 수정하지
  않았습니다. 다음 중앙 hub 후보는 공식 페이지 좌석 확인 schema 6개와 독립 mapper 1개를 기존 최상위
  module과 충돌하지 않는 feature 경계로 함께 옮기는 작업입니다.

### 2026-08-06 서른아홉 번째 구조 슬라이스

- 공식 페이지 좌석 확인 owner: 사용자 공식 화면 확인 transport schema 6개와
  `OfficialPageSeatConfirmation` mapper를 단수형 `official_page_confirmation/schemas.py`·`models.py`로
  이동하고, idempotent batch 저장·조회 결과 overlay 구현을 같은 bounded context의 `application.py`로
  옮겼습니다. provider·노선·열차 번호·시간대·승객 수·좌석 등급 검증 순서와 5분 freshness, server-owned
  source·clock, idempotency scope, 최신 근거 우선 overlay와 fail-closed action을 바꾸지 않았습니다.
- compatibility·production wiring: 중앙 `schemas.py`·`models.py`는 canonical schema·mapper와 같은 객체만
  alias로 노출합니다. 기존 복수형 `official_page_confirmations.py`는 두 application 함수와 두 상수를 정확히
  재노출하는 15줄 compatibility facade로 축소했습니다. 시간표 application과 공식 근거 HTTP는 canonical
  owner를 직접 import하고, schema·model·application·facade의 역의존 금지와 상대 import level을 AST
  경계 테스트로 고정했습니다. canonical-first·legacy-first 독립 프로세스는 `-W error` 아래 schema 내부
  타입 identity, mapper 1개, 같은 `Base.metadata` table과 `configure_mappers()` 성공을 확인합니다.
- migration 무변경 증거: migration 파일 diff는 0건이며 기존 0009의 컬럼·enum·check·unique·index 계약을
  유지했습니다. 재빌드한 PostgreSQL의 `alembic check`는 이전과 같은 known drift 8건을 보고했고
  `official_page` 관련 신규 operation은 0건입니다. exit 255를 성공으로 표현하지 않았으며 중앙
  `models.py` metadata bootstrap 계약을 유지합니다.
- 확인된 검증: 신규 schema·model 계약과 기존 공식 확인·module boundary pytest 119건, idempotency·시간표
  근거·migration 관련 pytest 28건, API 전체 pytest 1,812건·skip 1건, Ruff `E/F/I`, strict mypy 55개 파일,
  `uv lock --check`, 변경 범위 12개 파일 format check와 `git diff --check`를 통과했습니다. 전체 suite는
  9분 56초였고 독립 리뷰는 최종 P0~P3 지적 0건입니다. 전역 format ratchet는 병행 추가된 SRT fullstack
  fixture 3개가 아직 미포맷이라 실패했으며, 이번 owner 범위의 실패로 섞거나 해당 사용자 변경을 임의로
  포맷하지 않았습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config → 전체 image build → volume 삭제 없는
  force-recreate를 수행했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy, 컨테이너의
  canonical/legacy schema·mapper·application facade identity, 내부 `/healthz`·`/readyz`, HTTP proxy
  `/healthz`, noVNC page 200과 최근 치명 로그 표식 0건을 확인했습니다. build 종료 뒤 Docker Desktop
  named-pipe HTTP/2 경고는 있었지만 build exit 0과 재생성·health는 정상입니다.
- 문서·후속 범위: `ARCHITECTURE.md`의 bounded context owner와 `CODE_CONVENTIONS.md`의 strict mypy 범위를
  동기화했습니다. DB schema·API wire·사용자 상태·운영 절차가 바뀌지 않은 구조 이동이므로 병행 중인
  `CHECKLIST.md` 변경은 보존하고 수정하지 않았습니다. 다음 후보 감사에서는 이미 application package가
  있는 `notification_management`의 독립 `NotificationChannel` mapper를 선정했습니다. 7개 컬럼과 enum·
  Python default/onupdate를 보존해 `notification_management/models.py`로 이동하고, 중앙 exact alias와
  service·HTTP·delivery·watch transition·watch update의 canonical import를 계약 테스트로 고정합니다.

### 2026-08-06 마흔 번째 구조 슬라이스

- notification channel mapper owner: 중앙 `models.py`의 독립 `NotificationChannel` mapper를 기존
  `notification_management/models.py`로 이동했습니다. 테이블명과 7개 컬럼의 순서·타입·nullable,
  `NotificationKind` enum 이름과 DB 저장값, UUID·enabled·UTC datetime Python default,
  `updated_at.onupdate`를 그대로 보존했습니다. PK 외 check·unique·index·FK·relationship·server default가
  없는 기존 계약도 hardcoded fingerprint로 고정했습니다.
- compatibility·production wiring: 중앙 `models.py`는 canonical class·Table·mapper와 같은 객체만 alias로
  다시 노출합니다. notification service·HTTP·delivery·watch transition과 watch update는 feature model을
  직접 import하고, delivery의 `OutboxEvent`와 watch transition·update의 `Watch`만 아직 중앙 owner에서
  가져옵니다. canonical model의 중앙 hub·transport·runtime 역의존 금지와 다섯 production consumer의
  상대 import level을 AST로 고정했으며, watch update는 `notification_management.models` 외의 feature
  module에 의존할 수 없도록 별도 경계를 두었습니다. canonical-first·legacy-first 독립 프로세스는
  `-W error`와 `configure_mappers()` 아래 mapper 1개·같은 metadata table을 확인합니다.
- migration 무변경 증거: migration 파일 diff는 0건이며 기존 0001의 notification channel 컬럼·enum 계약을
  바꾸지 않았습니다. 재빌드한 PostgreSQL의 `alembic check`는 이전과 같은 known drift 8건만 보고했고
  `notification_channels` 관련 신규 operation은 0건입니다. exit 255를 성공으로 기록하지 않았으며 중앙
  `models.py` metadata bootstrap 계약을 유지합니다.
- 확인된 검증: mapper 계약·알림 관리·전달·watch notification·module boundary focused pytest 131건,
  API 전체 pytest 1,828건·skip 1건, Ruff `E/F/I`, strict mypy 56개 파일, `uv lock --check`, 변경 범위 9개
  파일 format check와 `git diff --check`를 통과했습니다. 전체 suite는 9분 59초였고 독립 리뷰는 추가
  알림·경계 153건과 watch update/service 44건을 확인한 뒤 최종 P0~P3 지적 0건입니다. 전역 format
  ratchet는 병행 추가된 `tests/fullstack/assert_worker_state.py`가 아직 미포맷이라 실패했으며, 이번 mapper
  범위의 실패와 섞거나 해당 사용자 변경을 임의로 포맷하지 않았습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config → 전체 image build → volume 삭제 없는
  force-recreate를 수행했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy, 컨테이너의
  canonical/legacy mapper identity, 내부 `/healthz`·`/readyz`, HTTP proxy `/healthz`, noVNC page 200과 최근
  치명 로그 표식 0건을 확인했습니다. build 종료 뒤 Docker Desktop named-pipe HTTP/2 경고는 있었지만
  build exit 0과 재생성·health는 정상입니다.
- 문서·후속 범위: `ARCHITECTURE.md`의 bounded context owner와 `CODE_CONVENTIONS.md`의 strict mypy 범위를
  동기화했습니다. DB schema·API wire·사용자 상태·운영 절차가 바뀌지 않아 병행 중인 `CHECKLIST.md`는
  수정하지 않았습니다. 다음 후보 감사에서는 관계가 없는 `RailProviderAccount` mapper를 기존
  `provider_account_management/models.py`로 옮기는 슬라이스를 선정했습니다. 9개 컬럼·4개 check·Python/
  server default와 5개 production consumer를 고정하되, migration 0017의 named unique constraint와 현재
  ORM의 unique index 차이가 known drift 8건 중 하나인 계약은 구조 이동에서 임의로 수정하지 않습니다.

### 2026-08-06 마흔한 번째 구조 슬라이스

- rail provider account mapper owner: 중앙 `models.py`의 `RailProviderAccount` mapper를 기존
  `provider_account_management/models.py`로 이동했습니다. 테이블명과 9개 컬럼의 순서·타입·nullable,
  KORAIL·SRT로 좁히는 4개 check, broad `Provider` enum 저장, provider unique index, UUID·boolean·generation·
  인증 상태·UTC datetime Python default, 세 개 server default와 `updated_at.onupdate`를 그대로 보존했습니다.
  FK·relationship이 없는 독립 mapper이며 package `__init__.py`는 순환 의존을 막도록 inert 상태를
  유지합니다.
- compatibility·production wiring: 중앙 `models.py`는 canonical class·Table·mapper와 같은 객체만 alias로
  다시 노출합니다. 최상위 provider account service와 provider runtime, 관찰 그룹, 예약 실행,
  reconciliation의 다섯 consumer는 feature model을 직접 import합니다. 쿼리·row lock·credential generation
  fence·commit/rollback 순서는 바꾸지 않았습니다. canonical model의 중앙 hub·transport·runtime 역의존,
  다섯 consumer의 상대 import level과 중앙 exact module alias를 AST 경계로 고정했습니다.
- migration 무변경 증거: migration 파일 diff는 0건이며 기존 0017의 테이블 계약을 바꾸지 않았습니다.
  재빌드한 PostgreSQL의 `alembic check`는 이전과 같은 known drift 8건을 보고했고, 이 중
  `rail_provider_accounts` 관련 항목은 migration의 `uq_rail_provider_accounts_provider` 제거 제안 정확히
  1건입니다. ORM은 기존처럼 같은 provider에 대한 unique index만 선언하므로 이 차이를 구조 이동에서
  임의로 수정하지 않았고, exit 255를 성공으로 기록하지 않습니다.
- 확인된 검증: mapper 계약·module boundary·migration 0017·provider account/runtime·관찰 그룹·예약 실행·
  reconciliation focused pytest 156건, API 전체 pytest 1,840건·skip 1건, Ruff `E/F/I`, strict mypy 57개
  파일, `uv lock --check`, 변경 범위 9개 파일 format check, format ratchet legacy 51개와
  `git diff --check`를 통과했습니다. 전체 suite는 9분 32초였고 독립 리뷰는 별도 관련 회귀 154건을
  확인한 뒤 최종 P0~P3 지적 0건입니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config → 전체 image build → volume 삭제 없는
  force-recreate를 수행했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy, 컨테이너의
  canonical/legacy mapper identity, 내부 `/healthz`·`/readyz`, HTTP proxy `/healthz`, noVNC page 200과 최근
  치명 로그 표식 0건을 확인했습니다. build 종료 뒤 Docker Desktop named-pipe HTTP/2 경고는 있었지만
  build exit 0과 재생성·health는 정상입니다.
- 문서·후속 범위: `ARCHITECTURE.md`의 mapper owner와 `CODE_CONVENTIONS.md`의 strict mypy 범위를
  동기화했습니다. DB schema·API wire·사용자 상태·운영 절차가 바뀌지 않아 병행 중인 `CHECKLIST.md`는
  수정하지 않았습니다. 다음 슬라이스는 중앙 model 추출만 반복하기보다 `services.py`에 남은 실제 정책·
  UoW를 다시 감사해 가장 작은 feature owner 이동 후보를 선정합니다.

### 2026-08-06 마흔두 번째 구조 슬라이스

- services 잔여 책임 감사: 현재 `services.py`에는 직접 `commit`·`rollback`·`flush`·savepoint·row lock을
  실행하는 UoW 본문이 0개이며, create·transition·auth recovery·circuit·observation·reservation·update는
  feature owner dependency 조립과 기존 HTTP 오류 변환 wrapper로 확인됐습니다. 단순 wrapper를 다른
  facade로 옮기는 대신 실제로 세 곳에 중복돼 있던 좌석 상태 분류 정책을 다음 최소 슬라이스로
  선정했습니다.
- observation status policy owner: `AVAILABLE`·`LIMITED`·`STANDING_PLUS_SEAT`의 일반 좌석 발견 집합과
  여기에 `WAITLIST_AVAILABLE`을 더한 실행 가능 집합을 순수 `observations/status_policy.py`로 옮겼습니다.
  단일 관측 recording, 관찰 그룹 요약, `services.begin_reservation_attempt` 조립이 같은 frozenset 객체를
  직접 사용합니다. `WAITLIST_AVAILABLE`은 예약 실행 가능 집합에는 포함되지만 일반 `SEAT_FOUND` 요약에는
  포함되지 않는 기존 `OFFICIAL_WAITLIST` 의미를 보존했습니다.
- compatibility·transaction 보존: `services.py`는 두 상수를 exact-object alias로 계속 노출하고 reservation
  claim wrapper는 호출 시점 module global을 읽어 기존 monkeypatch seam을 유지합니다. recording의
  projection → add → flush → outbox → 선택 transition, group의 lock·요약·cycle finish·commit/rollback,
  claim의 조회 → savepoint add/flush → 상태 변경 → transition → outbox와 execution의 provider I/O 전
  durable commit 순서는 바꾸지 않았습니다. 세 소비자의 canonical import와 로컬 재선언 금지, 새 pure
  owner의 DB·transport·provider·runtime 비의존을 계약·AST 경계로 고정했습니다.
- 확인된 검증: 정책·recording·group·reservation claim/execution·service 상태·module boundary focused
  pytest 163건, API 전체 pytest 1,849건·skip 1건, Ruff `E/F/I`, strict mypy 58개 파일,
  `uv lock --check`, 변경 범위 7개 파일 format check, format ratchet legacy 51개와 `git diff --check`를
  통과했습니다. 전체 suite는 9분 6초였고 독립 리뷰는 최종 P0~P3 지적 0건입니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config → 전체 image build → volume 삭제 없는
  force-recreate를 수행했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy, 컨테이너의
  세 consumer/canonical 정책 객체 identity, 내부 `/healthz`·`/readyz`, HTTP proxy `/healthz`, noVNC page
  200과 최근 치명 로그 표식 0건을 확인했습니다. DB metadata를 바꾸지 않은 슬라이스이며 재빌드한
  PostgreSQL의 `alembic check`는 이전과 같은 known drift 8건과 exit 255를 유지합니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 좌석 상태 분류 owner와 의미를 기록하고 `CODE_CONVENTIONS.md`의
  strict mypy 범위를 동기화했습니다. API wire·사용자 상태·운영 절차가 바뀌지 않아 병행 중인
  `CHECKLIST.md`는 수정하지 않았습니다. 다음 후보는 `worker.py`에 남은 실제 정책·UoW·런타임 조립을
  다시 감사해 단순 facade가 아닌 가장 작은 feature owner 이동으로 선정합니다.

### 2026-08-06 마흔세 번째 구조 슬라이스

- worker 잔여 책임 감사: expiry·stale recovery·observation·reservation·reconciliation은 이미 feature
  application 조립만 남았지만, `_arm_supported_provider_watches`는 provider capability 판단, SQL,
  `FOR UPDATE SKIP LOCKED`, 일정 갱신과 commit을 worker 안에서 직접 소유하고 있음을 확인했습니다. 이
  38줄 UoW를 다음 최소 책임으로 선정하고 단순 re-export가 아닌 실제 본문을 이동했습니다.
- watch arming application owner: `watch_management/arming_application.py`가 KORAIL·SRT와 seat-monitoring
  capability를 먼저 확인한 뒤, 동일 provider·official mode·`SCHEDULED → OFFICIAL_WAITLIST → SEAT_FOUND`
  순서의 허용 상태·`next_check_at IS NULL` 조건으로 watch를 잠급니다. 선택 행 모두에 같은 전달 시각을
  설정하고 행이 있을 때만 commit하며, outbox·상태 전이·provider network I/O는 만들지 않습니다. 지원하지
  않는 provider와 capability false는 provider resolver 또는 DB session을 열기 전에 0으로 종료합니다.
- compatibility·pipeline 순서: worker의 `_arm_supported_provider_watches`와 SRT wrapper는 이름·시그니처를
  유지하고 canonical application에 호출 시점의 `SessionFactory`·`get_execution_provider`를 주입합니다.
  따라서 기존 monkeypatch seam과 `_due_pipeline_dependencies()` callback identity를 보존합니다. due
  pipeline의 task-scoped adapter 생성 → arming → expiry → stale recovery → reconciliation/due 조회 순서와
  finally의 adapter close owner, 네 Celery task 이름은 바꾸지 않았습니다. AST 경계는 worker에서 직접
  `select`·`WatchStatus`·arming SQL을 제거하고 새 owner가 Celery·FastAPI·worker runtime으로 역의존하지
  못하도록 고정합니다.
- 확인된 검증: canonical arming·due pipeline·module boundary focused pytest 116건, worker wiring·Celery·
  SRT/KORAIL arming 추가 5건, API 전체 pytest 1,856건·skip 1건, Ruff `E/F/I`, strict mypy 59개 파일,
  `uv lock --check`, 변경 범위 4개 파일 format check, format ratchet legacy 51개와 `git diff --check`를
  통과했습니다. 전체 suite는 8분 59초였습니다. 독립 리뷰가 신규 fake session의 `__aenter__` 반환 타입을
  P3로 지적해 `Self`로 수정했고, 최종 default Ruff와 focused 회귀를 다시 통과했습니다. 기능·호환성
  P0~P2 지적은 없습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config → 전체 image build → volume 삭제 없는
  force-recreate를 수행했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy, worker image의
  canonical owner identity, 내부 `/healthz`·`/readyz`, HTTP proxy `/healthz`, noVNC page 200과 최근 치명
  로그 표식 0건을 확인했습니다. DB metadata를 바꾸지 않은 슬라이스이며 재빌드 PostgreSQL의
  `alembic check`는 이전과 같은 known drift 8건과 exit 255를 유지합니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 arming UoW와 due pipeline 순서를 기록하고
  `CODE_CONVENTIONS.md`의 strict mypy 범위를 동기화했습니다. API wire·사용자 상태·운영 절차가 바뀌지 않아
  병행 중인 `CHECKLIST.md`는 수정하지 않았습니다. 다음 큰 worker 후보인 `_process_watch_group`은 execution
  lease·adapter lifecycle 조립을 포함하므로 별도 감사와 더 넓은 순서 계약을 먼저 확보합니다.

### 2026-08-06 마흔네 번째 구조 슬라이스

- watch-group runtime 감사: `_process_watch_group`은 provider 미지정 시 group provider 조회, KORAIL·SRT
  execution lease 선획득, adapter 소유권 판정, canonical observation application 호출과 drain·close·release를
  worker 안에서 직접 조립하고 있었습니다. 기존 DB·상태 UoW는 이미 `observations/group_application.py`가
  소유하므로, 이 transaction 본문은 건드리지 않고 실행 자원 생명주기만 다음 최소 책임으로 선정했습니다.
- observation group runtime owner: 새 `observations/group_runtime.py`가 provider 판정 → 외부 provider lease
  획득 → adapter 생성 또는 재사용 → 관찰 위임 → drain → 직접 만든 adapter만 close → lease release 순서를
  소유합니다. provider가 없거나 group이 혼합되어 판정할 수 없으면 아무 runtime 자원도 만들지 않고, 외부
  provider lease를 얻지 못하면 전달된 adapter조차 drain하지 않는 기존 조기 종료를 보존합니다. adapter
  factory·관찰·drain·close 실패에서도 기존 중첩 `finally`의 뒤쪽 cleanup과 fresh UTC release 시각을 유지합니다.
- compatibility·pipeline 수명: worker `_process_watch_group`의 이름·시그니처와 due pipeline callback identity를
  유지하고, 호출할 때마다 현재 `SessionFactory`, group provider resolver, lease acquirer, provider getter,
  observation dependency factory·processor와 drain/close callback을 dependency bundle에 넣습니다. 따라서 기존
  monkeypatch seam은 그대로입니다. due pipeline이 provider별로 공유하는 adapter는 각 group 뒤 drain·lease
  release되고 task 종료 시 한 번만 close되며, 단건 즉시 실행에서 runtime이 만든 adapter는 group 안에서
  drain·close·release됩니다. opaque lease token은 실제 `ExecutionLeaseGrant`일 때만 current check에 전달해
  잘못된 token을 fail-closed로 거절합니다.
- 회귀·경계: 새 owner 격리 테스트는 provider 미결정, 명시 provider lookup 생략, KORAIL·SRT lease 미획득,
  MOCK lease 우회, supplied/owned adapter의 서로 다른 cleanup 순서, adapter factory·관찰·drain·close 오류와
  lease release, worker current-global wiring을 검증합니다. owner·module boundary·due pipeline focused pytest
  126건과 실제 SRT lease fencing·task-scoped adapter 재사용·cooldown DB 통합 6건을 통과했습니다. AST 경계는
  worker wrapper가 canonical runtime 호출과 dependency 조립 외 생명주기를 다시 소유하지 못하게 하고, 새
  runtime이 Celery·FastAPI·database·provider registry·services·worker로 역의존하지 못하게 고정합니다.
- 확인된 품질: API 전체 pytest 1,870건·skip 1건, Ruff `E/F/I`와 새 파일 default rule, strict mypy 60개 파일,
  `uv lock --check`, 변경 파일 format check, format ratchet legacy 51개와 `git diff --check`를 통과했습니다.
  전체 suite는 11분 12초였고 독립 리뷰는 최종 P0~P3 지적 0건입니다. 리뷰의 KORAIL 분기 잔여 위험은 같은
  lease 미획득 계약을 KORAIL·SRT 모두 parameterize해 보강했습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config와 전체 image build를 통과한 뒤 volume
  삭제 없이 force-recreate했습니다. 첫 시도는 Docker의 이전 web 교체 이름 경쟁으로 실패했지만, 현재
  container label·state를 확인한 뒤 같은 명령을 재시도해 정상 종료했습니다. 이후 KORAIL sidecar가 기본
  Compose 정의로 남은 것을 GUI 환경값·6080 binding 부재로 확인해 두 Compose 파일을 명시한 service 단위
  force-recreate로 바로잡았습니다. 최종 migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy, worker의
  canonical runtime identity, 내부 API `/healthz`·`/readyz`, HTTP proxy `/healthz`, 내부·호스트 noVNC page 200,
  안정화 구간 치명 로그 표식 0건을 확인했습니다. 전체 재생성 직후 Caddy에는 API DNS·연결 수렴 전 요청의
  502 기록이 있었으나, 안정화 뒤 proxy를 통한 `/openapi.json` 5회가 모두 200이고 같은 probe 구간 proxy
  error가 0건임을 별도로 확인했습니다. DB metadata는 바꾸지 않았고 PostgreSQL `alembic check`는 이전과
  같은 known drift 8건을 보고해 exit 1이며 성공으로 기록하지 않습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 group runtime owner와 supplied/owned adapter 수명을 기록하고
  `CODE_CONVENTIONS.md`의 strict mypy 범위를 60개로 동기화했습니다. API wire·사용자 상태·운영 절차가
  바뀌지 않아 병행 중인 `CHECKLIST.md`는 수정하지 않았습니다. 다음 worker 후보는 anonymous/public scope,
  2분 만료와 owner token 생성을 아직 직접 조립하는 `_acquire_execution_lease`이며, reconciliation과 group
  runtime이 공유하는 계약을 먼저 감사한 뒤 feature-owned runtime으로 옮길지 결정합니다.

### 2026-08-06 마흔다섯 번째 구조 슬라이스

- provider execution 감사: worker가 현재 `SessionFactory`로 service를 만들고 KORAIL·SRT에 공통
  `anonymous/public` scope, 호출마다 `uuid4().hex` token 1개, 입력 `now + 2분` 만료를 직접 조립하고
  있었습니다. 관찰 group과 예약 reconciliation이 같은 획득 callback을 사용하고 UI 설정이 활성 임대 행을
  조회하므로, 정책·service·독립 ORM mapper를 하나의 bounded context로 옮기는 수직 슬라이스로 정했습니다.
- canonical owner: 새 `provider_execution/contracts.py`가 repr에서 owner token을 숨기는 grant와 소비자용
  service·획득 Protocol을, `models.py`가 기존 6개 컬럼·복합 PK·4개 check·expiry index를 그대로 가진
  `ProviderExecutionLease`를 소유합니다. `lease_application.py`는 PostgreSQL·SQLite 원자적 upsert acquire,
  renew·release·current 확인, 호출자 transaction 안의 `FOR UPDATE` fence와 anonymous/public 획득 정책을
  소유합니다. takeover의 fencing token 단조 증가와 row-lock이 필요한 이유도 owner docstring에 남겼습니다.
- compatibility·runtime 조립: 중앙 `models.py`와 기존 `provider_execution_lease.py`는 canonical
  class·Table·mapper·service·함수와 같은 객체만 alias/re-export합니다. worker의 기존
  `_acquire_execution_lease(provider, now)` 이름·시그니처·monkeypatch seam은 유지하고 호출 시점의 현재
  `SessionFactory`만 dependency로 전달합니다. observation group과 reconciliation은 공용
  `AcquireExecutionLease` 계약을 직접 사용하며, UI preference 조회도 feature mapper와 scope를 직접
  import합니다. 획득 실패의 `(service, None)`, token factory·DB 오류 전파와 cleanup 순서는 바꾸지 않았습니다.
- 회귀·경계: 획득 인자·provider·scope·token 호출 횟수·정확한 2분 만료·busy/예외, mapper metadata·constraint·
  import 순서, central/facade identity, worker current-global 조립을 새 테스트로 고정했습니다. 독립 리뷰의 유일한
  P3인 observation runtime의 중복 lease acquirer Protocol도 canonical 계약으로 통합했습니다. 최종 focused
  pytest는 150건을 통과했고 리뷰에는 기능·호환성 P0~P2 지적이 없습니다.
- 확인된 품질: API 137개 테스트 파일을 파일 구간으로 모두 실행해 1,906건 통과·1건 skip을 확인했습니다.
  단일 `pytest -q`는 가장 느린 KORAIL browser 포함 구간만 6분 15초가 걸리는 현재 suite 특성 때문에
  10분 명령 제한을 넘겼으며, 같은 전체 파일을 분할한 결과에는 실패가 없습니다. Ruff `E/F/I`, format
  ratchet legacy 47개, strict mypy 68개 파일, `uv lock --check`, `git diff --check`를 통과했습니다. 이번
  작업에서 수정된 formatter 대상도 함께 정리해 stale legacy allowlist 4개를 제거했습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config와 전체 image build를 통과한 뒤 volume
  삭제 없이 force-recreate했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy, worker의
  canonical model/application identity와 KORAIL GUI override 적용, 내부 `/healthz`·`/readyz`, proxy
  `/healthz`·`/openapi.json`, noVNC page 200, 안정화 뒤 `/openapi.json` 5회 200·proxy 오류 표식 0건과 최근
  치명 로그 표식 0건을 확인했습니다. PostgreSQL `alembic check`는 이전과 같은 known drift 8건과 exit 1을
  유지하므로 성공으로 기록하지 않습니다.
- 문서·잔여 증거: `ARCHITECTURE.md`에 provider execution owner와 fencing/row-lock 계약을 기록하고
  `CODE_CONVENTIONS.md`의 strict 범위를 68개로 동기화했습니다. API wire·사용자 상태·운영 절차는 바뀌지
  않아 병행 중인 `CHECKLIST.md`는 수정하지 않았습니다. 실제 PostgreSQL 다중 세션 fencing acceptance는
  기존 스크립트에 격리 DB opt-in guard가 없어 현재 운영 Compose DB에서 재실행하지 않았고, 이번 확인은
  SQLite 회귀·dialect SQL 검토·Compose metadata bootstrap까지입니다. 다음 후보는 중앙 model/schema hub의
  남은 독립 계약 또는 worker의 다음 실제 정책/UoW이며 별도 감사 후 선정합니다.

### 2026-08-06 마흔여섯 번째 구조 슬라이스

- 다음 mapper 후보 비교 감사: 중앙 `models.py`의 `StationCatalogCache`와 `OutboxEvent`를 두 독립 감사로
  비교했습니다. station cache는 FK·relationship·index가 없는 독립 mapper이고 production 소비자가
  repository와 운영 요약 두 곳뿐입니다. 반면 outbox는 event stream·notification·metrics·reservation 등
  6개 production 모듈과 23개 테스트·스크립트에 걸친 공용 인프라이므로 이번 원자적 범위에서 제외했습니다.
- timetable model owner: `StationCatalogCache`를 새 `timetable_management/models.py`로 옮겼습니다. 기존
  `station_catalog_cache` 테이블명, 11개 컬럼 순서·타입·nullable·PK, `schema_version=2`와
  `station_count=0`의 Python/server default, `JSON(none_as_null=True)`, timezone 선언 5개, aware UTC
  `updated_at` default/onupdate, 이름과 SQL이 같은 check 7개를 보존했습니다. migration 0007과 revision chain은
  바꾸지 않았고 중앙 `models.py`는 canonical class·Table·mapper와 같은 객체만 alias로 노출합니다.
- production owner 경계: top-level `station_catalog_cache.py`의 repository/service와 `operations.py`의 freshness
  조회는 canonical mapper를 직접 import합니다. 독립 리뷰의 유일한 P3를 반영해 현재 두 소비자만 확인하지
  않고 전체 production Python 파일의 상대·절대 import를 해석하여 `StationCatalogCache`가 중앙 hub로
  되돌아가지 못하게 AST 경계를 확장했습니다. 중앙 재선언 부재와 canonical-first/legacy-first 단일 mapper
  등록도 고정했습니다. repository/service 본문은 strict mypy 오류 7건이 남아 있어 mapper 이동과 섞지 않고
  후속 슬라이스로 분리했습니다. 중앙 `models.py`는 619줄에서 573줄로 줄었습니다.
- 회귀·품질: model fingerprint·default/onupdate·constraint·metadata identity와 import-order, cache lease·takeover·
  stale/LKG·singleflight, 운영 요약, migration 0007, API·module boundary focused pytest 211건을 통과했습니다.
  최종 현재 트리의 API 138개 테스트 파일을 4구간으로 모두 실행해 1,915건 통과·1건 skip을 확인했습니다.
  Ruff `E/F/I`와 새 owner default rule, format ratchet legacy 47개, strict mypy 69개 파일,
  `uv lock --check`, `git diff --check`를 통과했습니다. 독립 리뷰는 최종 기능·DDL·호환성 P0~P3 지적 0건입니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config와 전체 image build를 통과한 뒤 volume
  삭제 없이 force-recreate했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy, worker
  image의 station/provider-execution canonical mapper identity와 KORAIL GUI override 적용, 내부
  `/healthz`·`/readyz`, proxy `/healthz`·`/openapi.json`, noVNC page 200, 안정화 뒤 OpenAPI 5회 200·proxy
  오류 표식 0건과 최근 치명 로그 표식 0건을 확인했습니다. PostgreSQL `alembic check`는 이전과 같은 known
  drift 8건과 exit 1을 유지하며 station mapper 이동으로 새 drift는 생기지 않았습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 timetable persistence owner와 아직 top-level인 repository/service
  책임을 구분하고 `CODE_CONVENTIONS.md`의 strict mypy 범위를 69개로 동기화했습니다. API wire·사용자 상태·
  운영 절차가 바뀌지 않아 병행 중인 `CHECKLIST.md`는 수정하지 않았습니다. 다음 후보는 mapper와 분리해
  `station_catalog_cache.py`의 strict 오류 7건을 먼저 해소한 뒤 timetable feature 안으로 repository/service를
  옮기거나, 더 넓은 `event_outbox` 경계를 별도 감사하는 작업입니다.

### 2026-08-06 마흔일곱 번째 구조 슬라이스

- timetable catalog application owner: top-level `station_catalog_cache.py`가 소유하던 상수·snapshot·
  repository·service와 refresh 수명주기 정책을 `timetable_management/catalog_application.py`로 옮겼습니다.
  `main.py`는 canonical service를 직접 조립하고, 기존 top-level 모듈은 상수와 세 class를 같은 객체로만
  다시 노출하는 27줄 compatibility facade로 축소했습니다. wrapper·subclass를 만들지 않아 task identity,
  shared TAGO client와 `_refresh_task` 수명주기를 그대로 보존했습니다.
- fail-closed·동시성 계약: 기존 UPDATE SQL은 바꾸지 않고 driver의 동적 `rowcount`를 `object`에서 검증하는
  helper로 lease 획득·success·failure fencing의 strict 타입을 닫았습니다. 저장 payload도 top-level JSON과
  visibility를 `object`에서 시작해 dict·timestamp·station schema·identity/display 부분집합을 검증한 뒤에만
  snapshot으로 복원합니다. 두 service instance의 DB lease winner polling, owner cancellation의 lease 해제,
  shutdown 후 신규 refresh 차단, collection timeout의 bounded category, 손상 payload 거부를 회귀로
  추가했습니다. cache freshness·stale 즉시 반환·LKG 보존·KORAIL/SRT 공유 합집합 계약은 바꾸지 않았습니다.
- application port 경계: `timetable_management/contracts.py`에 station catalog reader/timetable port와
  KORAIL·SRT timetable source, snapshot cache port를 좁은 Protocol로 정의했습니다. 시간표 application과
  catalog·timetable HTTP는 더 이상 FastAPI `app.state` 값을 `object`로 사용한 채 반환하지 않으며, nullable
  node ID는 실제 분기에서 좁혀 공식 확인 overlay와 evidence 저장의 기존 순서·조건을 보존합니다. contracts가
  catalog 구현을 역참조하지 않는 경계와 HTTP reader/cache runtime 적합성도 테스트로 고정했습니다.
- 회귀·품질: cache/application/model·migration 0007·시간표·API·module boundary focused pytest 225건을
  통과했고, 그중 facade/import/module boundary 별도 묶음 133건도 확인했습니다. 현재 트리의 API 139개
  테스트 파일을 4개 샤드로 모두 실행해 1,935건
  통과·1건 skip을 확인했습니다. Ruff `E/F/I`와 변경 owner default rule, format ratchet legacy 47개,
  strict mypy 75개 파일을 통과했습니다. 독립 최종 리뷰의 유일한 P3였던 timetable HTTP의 동적 snapshot
  cache 반환 경계도 좁은 runtime-checkable port로 닫고 해당 HTTP owner를 strict ratchet에 편입했습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config와 전체 image build를 통과한 뒤 volume
  삭제 없이 force-recreate했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy,
  KORAIL sidecar의 GUI flag·display와 worker image의 canonical service/model identity, 내부
  `/healthz`·`/readyz`, proxy `/healthz`·`/openapi.json`, noVNC page 200, 최근 치명 로그 표식 0건을
  확인했습니다. PostgreSQL `alembic check`는 이전과 동일한 removed table/index/constraint known drift
  8건과 exit 1을 유지하며 이번 catalog application 이동으로 새 drift는 생기지 않았습니다.
- 문서·후속 범위: `ARCHITECTURE.md`의 mapper/application/contracts 소유권과
  `CODE_CONVENTIONS.md`의 strict mypy 범위를 동기화했습니다. API wire·사용자 상태·환경변수·운영 절차가
  바뀌지 않아 병행 중인 `CHECKLIST.md`에는 이 구조 이동 항목을 추가하지 않았습니다. 다음 timetable 후보는
  중앙 `schemas.py`의 station transport 계약 또는 top-level `station_visibility.py`이며, 두 책임을 한 번에
  이동하지 않고 별도 감사 후 선택합니다.

### 2026-08-06 마흔여덟 번째 구조 슬라이스

- 다음 timetable 후보 비교 감사: 중앙 `schemas.py`의 `StationItem`·`StationCatalog` aggregate와 top-level
  `station_visibility.py`를 두 독립 감사로 비교했습니다. station schema 이동도 안전하지만 provider protocol·
  adapter·facade·HTTP 등 production 16개 파일을 함께 갱신해야 합니다. visibility는 production 소비자가
  `main.py`와 catalog application 두 곳뿐이고 독립 strict 오류가 0이므로 더 작은 원자적 owner 이동으로
  먼저 선택했습니다. 두 schema class 이동은 별도 후속 슬라이스로 분리했습니다.
- station visibility policy owner: KORAIL 공개 역 목록 fetch·schema 검증·정규화·TAGO 교집합 정책 전체를
  `timetable_management/station_visibility.py`로 import 경로만 바꿔 이동했습니다. HEAD의 기존 구현과 상대
  import 세 곳을 정규화한 본문이 완전히 같은지 확인했습니다. `main.py`와 catalog application은 canonical
  owner를 직접 사용하고 top-level `station_visibility.py`는 상수 7개·class 3개·함수 2개를 같은 객체로
  재노출하는 compatibility facade만 유지합니다. package `__init__.py`에는 광범위한 export를 추가하지
  않았습니다.
- 정책·호환 회귀: 공식 기본 HTTPS URL, connect 5초/나머지 10초 timeout, roster 250~400 inclusive,
  필수 `서울·수서·대전·부산`, NFKC·공백·casefold·`역` suffix·세 alias, 여섯 통근역 제외, 원본
  `StationItem` identity/order, redirect 차단과 bounded 예외를 보존했습니다. 최소·최대 경계, trim 뒤 중복
  코드·정규화 이름 거부, custom fixture URL 요청·공개 속성·snapshot provenance, legacy/canonical exact
  identity와 양쪽 import order를 추가로 고정했습니다. production이 top-level facade로 되돌아가지 못하도록
  상대 import를 해석하는 AST gate도 추가했습니다.
- 회귀·품질: visibility·catalog·provider·config·API·module boundary focused pytest 345건과 독립 최종 리뷰
  focused 181건을 통과했습니다. 현재 트리의 API 140개 테스트 파일을 4개 샤드로 모두 실행해 1,950건
  통과·1건 skip을 확인했습니다. Ruff `E/F/I`, format ratchet legacy 46개, strict mypy 77개 파일,
  `uv lock --check`를 통과했습니다. 독립 최종 리뷰의 코드 P0~P2 지적은 없었습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config와 전체 image build를 통과한 뒤 volume
  삭제 없이 force-recreate했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy,
  KORAIL sidecar GUI flag `true`·display `:99`, worker image의 visibility roster/loader와 catalog service
  legacy/canonical identity, canonical class owner를 확인했습니다. 내부 `/healthz`·`/readyz`, proxy
  `/healthz`·`/openapi.json`, noVNC page는 모두 200이고 최근 치명 로그 표식은 0건입니다. PostgreSQL
  `alembic check`는 이전과 같은 removed table/index/constraint known drift 8건과 exit 1을 유지해 이번
  visibility 이동으로 새 drift가 생기지 않았습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 visibility owner와 discoverability-not-ownership 의미를 기록하고
  `CODE_CONVENTIONS.md`의 strict 범위를 77개로 동기화했습니다. API wire·사용자 상태·환경변수·운영 절차는
  바뀌지 않아 병행 중인 `CHECKLIST.md`에는 구조 이력 항목을 추가하지 않았습니다. 다음 후보는
  `StationItem`·`StationCatalog`를 함께 `timetable_management/schemas.py`로 옮기고 중앙 exact alias를
  유지하는 transport aggregate 슬라이스입니다.

### 2026-08-06 마흔아홉 번째 구조 슬라이스

- station transport 후보 재감사: 중앙 `schemas.py`의 `StationItem`·`StationCatalog`는 Pydantic·domain
  `Provider`·공용 `ApiModel`만 의존하는 순수 transport aggregate이며 ORM metadata·transaction·provider
  runtime을 참조하지 않습니다. 별도 scope/membership type alias는 기존 공개 계약에 없으므로 새로 만들지
  않고 두 class를 한 단위로 이동하는 것이 안전하다고 확인했습니다.
- station transport owner: 두 class를 `timetable_management/schemas.py`로 본문 그대로 옮겼습니다. 필드
  순서, 길이 제약, Literal 값, aware datetime 검증과 중첩 `StationItem` identity를 보존했습니다. 중앙
  `schemas.py`는 class 재선언 없이 같은 객체를 exact alias로 노출하고 `providers.py`도 canonical
  `StationCatalog`를 직접 re-export합니다. provider 계약·adapter·roster·catalog HTTP/application·visibility의
  production 소비자는 모두 feature owner를 직접 사용합니다.
- 계약·모듈 경계: base·필드 순서·required/from-attributes, JSON schema fingerprint, timezone·wire round-trip,
  OpenAPI component와 `$ref`, central/canonical 양방향 import order를 새 회귀 테스트로 고정했습니다. 중앙
  class 재선언, production의 legacy station schema import, provider facade의 우회 re-export와 canonical
  schema의 runtime/legacy 역의존을 AST gate로 차단했습니다.
- 회귀·품질: station catalog·visibility·provider·API·module boundary focused pytest 335건과 독립 최종 리뷰
  focused 261건을 통과했습니다. 현재 트리의 API 141개 테스트 파일을 4개 샤드로 모두 실행해 1,969건
  통과·1건 skip을 확인했습니다. Ruff `E/F/I`, format ratchet legacy 45개, strict mypy 78개 파일,
  `uv lock --check`, `git diff --check`를 통과했습니다. 수정된 기존 SRT roster 테스트도 포맷해 legacy
  allowlist에서 제거했고, 독립 최종 리뷰의 P0~P2 지적은 없었습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config와 전체 image build를 통과한 뒤 volume
  삭제 없이 force-recreate했습니다. migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy,
  KORAIL sidecar GUI flag `true`·display `:99`와 X display 응답, worker image의 central/canonical/provider
  station transport identity와 canonical class owner를 확인했습니다. 내부 `/healthz`·`/readyz`, browser
  sidecar health, proxy `/healthz`·`/openapi.json`, noVNC page는 모두 200이고 최근 치명 로그 표식은 0건입니다.
  PostgreSQL `alembic check`는 이전과 같은 removed table/index/constraint known drift 8건과 exit 1을 유지해
  이번 transport 이동으로 새 drift가 생기지 않았습니다.
- 문서·후속 범위: `ARCHITECTURE.md`의 transport owner와 compatibility 경계,
  `CODE_CONVENTIONS.md`의 strict 범위를 78개로 동기화했습니다. API wire·사용자 상태·환경변수·운영 절차가
  바뀌지 않아 병행 중인 `CHECKLIST.md`에는 구조 이력 항목을 추가하지 않았습니다. 다음 슬라이스는 중앙
  schema/model 또는 top-level provider 구현 중 의존 방향과 회귀 표면이 가장 작은 후보를 다시 감사한 뒤
  하나만 선택합니다.

### 2026-08-06 쉰 번째 구조 슬라이스

- 다음 owner 병렬 감사: 중앙 schema는 production 소비자 1개인 `SeatStatusRefreshRequest`, 중앙 model은
  `TimetableSeatEvidence`, worker는 provider adapter cleanup 정책이 각각 가장 작은 후보였습니다.
  `services.py`에는 직접 commit·rollback·flush·lock UoW가 더 이상 없고 대부분 feature dependency 조립과
  HTTP 호환 wrapper이며, worker도 task/runtime shell을 제외한 큰 UoW가 이미 이동한 상태임을 재확인했습니다.
  직전 transport 이동을 반복하기보다 중앙 ORM hub를 실제로 줄이고 소비자 2곳·단방향 FK로 범위가 작은
  `TimetableSeatEvidence`를 이번 슬라이스로 선택했습니다.
- timetable evidence mapper owner: `TimetableSeatEvidence` 본문을
  `timetable_management/models.py`로 그대로 옮겼습니다. 중앙 `models.py`는 같은 class를 exact alias로
  노출하고 `WatchCandidate.registration_evidence_id` FK와 relationship은 기존 중앙 aggregate에 유지했습니다.
  evidence persistence와 watch create application은 canonical mapper를 직접 사용하며 behavior/fullstack
  테스트도 feature owner를 사용합니다. 원본과 이동 본문의 정규화 AST가 동일하고 migration 0010·0011·
  0014는 수정하지 않았습니다.
- mapper·관계 계약: 단일 class/mapper/Table identity, 18개 column의 순서·타입·nullable/default,
  Provider·SeatClass·SeatObservationStatus enum, timezone column, check 8개·unique 1개·identity index 1개,
  무외부-FK mapper와 `WatchCandidate → timetable_seat_evidence.id` 관계, aware default·provenance projection,
  canonical-first/legacy-first import order를 새 계약 테스트로 고정했습니다. production의 모든 evidence mapper
  import가 canonical owner로 해석되는지와 중앙 class 재선언 금지도 AST gate에 추가했습니다.
- 회귀·품질: mapper·timetable evidence·watch create·module boundary focused pytest 176건, API 64건,
  worker 62건, migration 0010·0011 2건을 통과했습니다. 현재 트리의 API 142개 테스트 파일을 4개 샤드로
  모두 실행해 1,978건 통과·1건 skip을 확인했습니다. Ruff `E/F/I`, format ratchet legacy 45개,
  strict mypy 78개 파일, `uv lock --check`, `git diff --check`를 통과했습니다. 독립 최종 리뷰는 원본 AST,
  metadata·relationship·import order와 migration fingerprint를 다시 확인했고 P0~P2 지적은 없었습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config와 전체 image build를 통과한 뒤 volume
  삭제 없이 force-recreate했습니다. 설정에 포함된 migration·log-init 2개 exit 0, 장기 서비스 11/11
  healthy이며 별도 실행 중인 browser validation 컨테이너는 건드리지 않았습니다. worker image에서
  central/canonical class·metadata Table·단일 mapper·WatchCandidate relationship identity와 canonical
  `__module__`, 앞선 station transport identity를 확인했습니다. GUI flag `true`·display `:99`와 X display,
  내부 API `/healthz`·`/readyz`, browser sidecar health, proxy `/healthz`·`/openapi.json`, noVNC page는 모두
  정상이고 최근 치명 로그 표식은 0건입니다. PostgreSQL `alembic check`는 이전과 같은 removed
  table/index/constraint known drift 8건과 exit 1을 유지해 이번 mapper 이동으로 새 drift가 생기지 않았습니다.
- 문서·후속 범위: `ARCHITECTURE.md`의 timetable model owner와 중앙 relationship 호환 경계를
  동기화했습니다. strict 대상 파일 수는 기존 `timetable_management/models.py` 안의 이동이라 78개로
  유지됩니다. API wire·DB schema·사용자 상태·환경변수·운영 절차가 바뀌지 않아 병행 중인
  `CHECKLIST.md`에는 구조 이력 항목을 추가하지 않았습니다. 다음 최소 후보는
  `SeatStatusRefreshRequest` transport 또는 provider execution lifecycle 오류 정책이며, 별도 슬라이스에서
  하나만 선택합니다.

### 2026-08-06 쉰한 번째 구조 슬라이스

- worker 잔여 정책 선택: 앞선 감사에서 `services.py`는 `find_watch` 외에는 feature wiring/HTTP facade,
  worker는 Celery task shell·dependency 조립 외에 adapter cleanup 실패 정책과 provider auth transaction
  adapter만 실제 독립 책임으로 남았음을 확인했습니다. DB·lock·outbox 의존이 없고 group runtime과 due
  pipeline이 공통으로 쓰는 `_drain_execution_adapter`·`_close_execution_adapter`의 오류 정책을 가장 작은
  다음 슬라이스로 선택했습니다.
- provider lifecycle owner: 일반 drain·close 예외를 삼키고 provider 값만 포함한 고정 warning을 남기는
  정책을 `provider_execution/lifecycle_runtime.py`로 옮겼습니다. exception 객체·원문을 logger에 전달하지
  않아 upstream 응답·credential 노출을 막고, `Exception`만 포착해 `asyncio.CancelledError`는 기존처럼
  전파합니다. worker의 두 기존 함수는 이름·시그니처를 유지하며 canonical 함수에 호출 시점 `LOGGER`를
  주입하는 compatibility wrapper만 담당합니다.
- cleanup·경계 회귀: 성공 drain/close 각 1회, 두 일반 실패의 정확한 categorical warning과 원문 비노출,
  두 cancellation 전파, worker current-global logger/canonical seam을 새 테스트로 고정했습니다. group
  runtime의 `drain → owned close → lease release` 중첩 finally와 due pipeline의 task-scoped close 순서는
  그대로이며, canonical owner가 Celery·database·config·metrics·provider registry·services·worker로
  역의존하지 못하도록 module boundary를 추가했습니다.
- 회귀·품질: lifecycle·watch group·worker·module boundary focused pytest 220건, worker 포맷 뒤 최종 관련
  회귀 209건을 통과했습니다. 현재 트리의 API 143개 테스트 파일을 4개 샤드로 모두 실행해 1,986건
  통과·1건 skip을 확인했습니다. Ruff `E/F/I`, format ratchet legacy 45개, strict mypy 79개 파일,
  `uv lock --check`, `git diff --check`를 통과했습니다. 독립 최종 리뷰는 cancellation, 로그 비노출,
  current logger seam, group/due cleanup과 순환 의존을 확인했고 P0~P2 지적은 없었습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config와 전체 image build를 통과한 뒤 volume
  삭제 없이 force-recreate했습니다. 설정에 포함된 migration·log-init 2개 exit 0, 장기 서비스 11/11
  healthy이며 별도 browser validation 컨테이너는 건드리지 않았습니다. worker image에서 두 lifecycle
  함수가 canonical module과 같은 객체인지, 앞선 station schema와 timetable evidence mapper identity가
  계속 유지되는지 확인했습니다. GUI flag `true`·display `:99`와 X display, 내부 API `/healthz`·`/readyz`,
  browser sidecar health, proxy `/healthz`·`/openapi.json`, noVNC page는 모두 정상이고 최근 치명 로그 표식은
  0건입니다. PostgreSQL `alembic check`는 이전과 같은 removed table/index/constraint known drift 8건과
  exit 1을 유지해 이번 runtime 이동으로 새 drift가 생기지 않았습니다.
- 문서·후속 범위: `ARCHITECTURE.md`의 lifecycle 오류·로그·cancellation owner와
  `CODE_CONVENTIONS.md`의 strict 범위를 79개로 동기화했습니다. 사용자 상태·API·DB schema·환경변수·운영
  절차가 바뀌지 않아 병행 중인 `CHECKLIST.md`에는 구조 이력 항목을 추가하지 않았습니다. 다음 후보는 중앙
  `SeatStatusRefreshRequest` transport 또는 provider auth reservation transaction adapter이며, 다음 실행에서
  다시 원자적으로 하나만 선택합니다.

### 2026-08-06 쉰두 번째 구조 슬라이스

- 예약 인증 transaction adapter 선택: worker의 예약 실행 dependency가 사용하던
  `_update_provider_auth_status_in_reservation_transaction`은 provider auth persistence 호출에 `commit=False`를
  고정하는 독립 UoW 정책이었습니다. row lock·credential generation fence·flush는 기존 persistence가,
  예약 결과의 최종 commit·rollback은 reservation execution application이 소유하므로 이 작은 경계만
  `provider_account_management` owner로 이동했습니다.
- canonical owner와 호환 seam: `provider_account_management/reservation_runtime.py`에 persistence Protocol과
  canonical adapter를 두고 exact credential version과 `commit=False`를 전달하게 했습니다. worker의 기존
  함수 이름은 호출 시점의 module-global `update_provider_auth_status`를 주입하는 wrapper로 유지해 기존
  monkeypatch·dependency seam과 예약 실행 callback identity를 보존했습니다. canonical owner는 worker,
  service, reservation runtime/application을 역참조하지 않습니다.
- transaction·경계 회귀: 전달 인자와 오류 전파, 실제 PostgreSQL flush 후 외부 rollback 시 인증 상태 복원,
  worker current-global 주입, canonical-first·worker-first import order를 새 계약 테스트로 고정했습니다.
  auth runtime·provider account·reservation execution·worker·module boundary focused 회귀를 통과했고, 독립
  리뷰도 row lock·flush·generation fence·외부 rollback·순환 의존을 다시 확인해 P0~P2 지적이 없었습니다.
- 회귀·품질: 당시 API 144개 테스트 파일을 4개 샤드로 모두 실행해 1,993건 통과·1건 skip을 확인했습니다.
  Ruff `E/F/I`, format ratchet legacy 45개, strict mypy 80개 파일과 `uv lock --check`를 통과했습니다.
  사용자 상태·API wire·DB schema·환경변수·운영 절차가 바뀌지 않아 병행 중인 `CHECKLIST.md`에는 구조 이력
  항목을 추가하지 않았습니다.

### 2026-08-06 쉰세 번째 구조 슬라이스

- top-level provider 후보 감사: `srt_live_timetable.py`, `korail_reservation_controls.py`,
  `srt_station_roster.py`와 TAGO adapter를 의존 방향·소비자 수·운영 위험으로 비교했습니다. 순수 projection이고
  production 소비자가 하나뿐인 SRT 시간표 mapper를 이번 최소 슬라이스로 선택했습니다. KORAIL 예약 제어는
  안전 정책, SRT roster는 cache·복수 소비자, TAGO는 넓은 adapter 경계라 후속 슬라이스로 남겼습니다.
- SRT projection owner: 기존 `map_srt_live_timetable`·`_seat_class` 본문을
  `timetable_management/srt_live_timetable.py`로 옮기고 시간표 application이 feature owner를 직접 사용하게
  했습니다. top-level 모듈은 두 함수 객체를 그대로 노출하는 exact alias facade입니다. strict typing을 위해
  공식 URL과 좌석 등급 타입을 명시했지만, 열차 순서·일반실 운임·UTC provenance와 `available`,
  `waitlist_available`, `sold_out`, `unknown`, `not_offered`의 action 계약은 바꾸지 않았습니다.
- 호환·경계 회귀: legacy/canonical exact identity, 전체 projection 필드와 순서, 좌석 상태 5종의 fail-closed
  action, 빈 입력과 양방향 import order를 고정했습니다. canonical owner의 application/runtime 역의존,
  production의 legacy facade 직접·module-style import, top-level 함수 재선언을 AST gate로 차단했습니다.
  독립 리뷰에서 발견한 module-style facade import 탐지 공백도 보완한 뒤 재검토해 P0~P2 잔여 지적이
  없었습니다.
- 회귀·품질: production 변경 기준 API 145개 테스트 파일을 4개 샤드로 모두 실행해 2,005건 통과·1건
  skip을 확인했고, 마지막 boundary 강화 뒤 projection·module boundary focused 155건도 통과했습니다.
  Ruff `E/F/I`, format ratchet legacy 45개, strict mypy 81개 파일과 `uv lock --check`를 통과했습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config와 전체 image build를 통과한 뒤 volume
  삭제 없이 force-recreate했습니다. 설정에 포함된 migration·log-init 2개는 exit 0, 장기 서비스 11/11은
  healthy입니다. worker image에서 auth adapter·dependency wrapper와 SRT legacy/canonical/application 심볼
  identity를 확인했습니다. GUI flag `true`·display `:99`·X display, 내부 API `/healthz`·`/readyz`, proxy
  `/healthz`·`/openapi.json`, noVNC page가 모두 정상이고 최근 치명 로그 표식은 0건입니다. PostgreSQL
  `alembic check`는 이전과 같은 removed table/index/constraint known drift 8건만 보고해 새 drift가 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 transaction adapter와 SRT projection owner를,
  `CODE_CONVENTIONS.md`에 strict 대상 81개를 동기화했습니다. 사용자 계약이 바뀌지 않아 병행 중인
  `CHECKLIST.md`는 수정하지 않았습니다. 다음 최소 후보는 provider-owned 경계와 production 소비자가 하나인
  `korail_reservation_controls.py`이며, 그 다음은 cache·복수 소비자 계약을 먼저 고정한 뒤
  `srt_station_roster.py`를 검토합니다.

### 2026-08-07 쉰네 번째 구조 슬라이스

- KORAIL 예약 control 정책 선택: top-level `korail_reservation_controls.py`의 production 소비자는 Pydoll
  reservation driver 한 곳이고, DB·설정·네트워크 I/O 없이 공식 seat-box classifier만 사용하는 순수
  정책임을 확인했습니다. owning price box 우선, control과 candidate 양쪽의 원화 가격, 요청 좌석 등급
  prefix와 `available`·`limited` allowlist가 실제 예약 클릭 전의 안전 fence이므로 동작 변경 없이 owner만
  이동했습니다.
- canonical owner와 facade: 본문을 `provider_adapters/korail_reservation_controls.py`로 옮기고 Pydoll
  reservation driver가 canonical owner를 직접 사용하게 했습니다. top-level 모듈은
  `booking_seat_control_key` 함수 객체 하나를 그대로 노출하는 exact alias facade이며 provider adapter
  package barrel은 추가하지 않았습니다. 원본과 새 owner의 실행 AST가 동일하고 기존 repository 안에는
  legacy module/function monkeypatch seam이 없음을 독립 감사로 확인했습니다.
- 클릭 안전·경계 회귀: price-only anchor와 authoritative box, `sold_out_soon`의 limited 허용, 매진·예약대기·
  좌석 등급 불일치·가격 없는 control의 fail-closed 거절을 유지했습니다. legacy/canonical exact identity와
  양방향 import order, driver canonical import, production legacy facade 금지, facade 재선언 금지와 canonical
  runtime-shell 역의존 금지를 고정했습니다. direct·relative·module-style import 6종 synthetic gate에서 빈
  relative module component 정규화 결함을 발견해 수정했고, 같은 resolver를 기존 SRT projection과 station
  visibility legacy gate에도 적용했습니다.
- 회귀·품질: control·reservation driver·module boundary focused pytest 165건을 통과했습니다. 현재 트리의
  API 145개 테스트 파일을 PostgreSQL 경합 없이 4개 샤드로 모두 실행해 2,018건 통과·1건 skip을
  확인했습니다. Ruff `E/F/I`, strict mypy 82개 파일과 `uv lock --check`를 통과했습니다. 수정한 기존 control
  테스트를 포맷하고 allowlist에서 제거해 format ratchet legacy는 44개로 줄었습니다. 독립 리뷰 3건은
  예약 클릭 fence·dedupe key·classifier 의존·순환·호환 import를 다시 확인했고 P0~P2 지적이 없었습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config와 전체 image build를 통과한 뒤 volume
  삭제 없이 force-recreate했습니다. migration·log-init 2개는 exit 0, 장기 서비스 11/11은 healthy입니다.
  worker image에서 legacy/canonical/driver 함수 identity와 허용·매진 거절 결과를 확인했습니다. GUI flag
  `true`·display `:99`·X display, 내부 API `/healthz`·`/readyz`, proxy `/healthz`·`/openapi.json`, noVNC page는
  모두 정상이고 최근 치명 로그 표식은 0건입니다. PostgreSQL `alembic check`는 컨테이너 exit 255와 이전과
  같은 removed table/index/constraint known drift 8건만 보고해 새 drift가 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 예약 control owner와 fail-closed fence를,
  `CODE_CONVENTIONS.md`에 strict 대상 82개를 동기화했습니다. API wire·사용자 상태·DB schema·환경변수·운영
  절차가 바뀌지 않아 병행 중인 `CHECKLIST.md`는 수정하지 않았습니다. 다음 후보는 cache·복수 production
  소비자의 roster 의미를 먼저 고정한 뒤 `srt_station_roster.py`를 provider owner로 옮기는 슬라이스입니다.

### 2026-08-07 쉰다섯 번째 구조 슬라이스

- SRT station roster owner: top-level `srt_station_roster.py`의 full policy를
  `provider_adapters/srt_station_roster.py`로 옮겼습니다. timetable adapter, SRT live seat source와 SRT
  reservation은 canonical owner를 직접 사용하고, top-level 모듈은 기존 5개 공개 심볼을 같은 객체로
  노출하는 exact alias facade만 유지합니다. direct·relative·module-style legacy import와 canonical owner의
  runtime/legacy 역의존은 AST boundary로 차단했습니다.
- roster 의미와 fail-closed: SRTrain 역 코드와 서울 교차운행 확장은 조회용 query-code 목록일 뿐 운영사
  소속·특정 날짜 운행·실제 정차 근거로 사용하지 않습니다. immutable mapping과 process-local `maxsize=1`
  cache, 실패 비cache, sentinel·alias·양방향 import order를 계약 테스트로 고정했습니다. 정규화된 같은 역
  이름에 다른 코드가 들어오면 roster 전체를 거절합니다. roster를 만들 수 없으면 live timetable은 provider
  검색 전에 `SrtLiveTimetableUnavailable`로 닫혀 TAGO fallback으로 이동하고, reservation은 검색·예약 호출
  없이 `FAILED`로 종료합니다. 독립 리뷰의 P0~P2 잔여 지적은 없습니다.
- retired native metadata 보존: 병행 native-push retirement 직후 PostgreSQL `alembic check`의 known drift가
  8건에서 15건으로 늘어난 원인을 감사해, migration 0027의 table 2개와 index 5개가 ORM metadata에서만
  빠진 것을 분리했습니다. `notification_management/models.py`에 `NativePushPairing`·
  `NativePushCredential`을 비활성 compatibility mapper로 복구하고 중앙 `models.py`는 exact alias만
  제공합니다. 이는 기존 hash row와 migration 비교 계약을 보존할 뿐 native route·설정·delivery를 다시
  활성화하지 않습니다. column·enum·timezone·index·FK·import order·중앙 bootstrap을 계약 테스트로
  고정했습니다.
- 회귀·품질: SRT roster·seat·reservation·boundary focused 233건, notification model·migration·boundary·
  service focused 210건과 보강된 model·migration·boundary 174건을 통과했습니다. 병행 retirement가 반영된
  현재 트리의 API 142개 테스트 파일을 PostgreSQL 경합 없이 4개 샤드로 실행해 2,029건 통과·1건 skip을
  확인했습니다. Ruff `E/F/I`, format ratchet legacy 44개, strict mypy 80개 파일과 `uv lock --check`를
  통과했습니다.
- 통합 운영 검증: GUI override를 포함한 `experimental-rail` config와 전체 image build를 통과한 뒤 volume
  삭제 없이 force-recreate했습니다. 설정 서비스는 13개, migration·log-init 2개는 exit 0, 장기 서비스
  11/11은 healthy입니다. worker image에서 roster 5개 alias·세 consumer binding·cache·정규화 충돌 거절,
  두 native mapper의 중앙 alias·table·index와 retired gate를 확인했습니다. GUI flag `true`·display `:99`·
  X display, 내부 API `/healthz`·`/readyz`, proxy `/healthz`·`/openapi.json`, noVNC page가 모두 정상이고 최근
  치명 로그 표식은 0건입니다. PostgreSQL `alembic check`는 exit 255와 기존 known drift 8건만 보고하여 native
  table/index 제거 제안 7건이 사라졌습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 roster와 비활성 mapper owner, `CODE_CONVENTIONS.md`에 strict 대상
  80개와 migration data-retirement 규칙, `POLICY_AND_SAFETY.md`와 `CHECKLIST.md`에 query-code의 제한된 의미와
  fail-closed 동작을 동기화했습니다. 다음 슬라이스는 현재 트리의 남은 top-level provider 구현을 다시
  inventory한 뒤 production 소비자와 안전 계약이 가장 좁은 owner 이동 후보부터 선택합니다.

### 2026-08-07 쉰여섯 번째 구조 슬라이스

- provider inventory와 후보 선택: KORAIL·SRT·TAGO production graph를 독립 감사했습니다. TAGO는 이미
  `provider_adapters/tago.py`가 canonical owner이고 top-level 구현이 없지만 parser·HTTP·cache·projection이
  한 파일에 모여 있어 후속 내부 분리 대상으로 남았습니다. KORAIL·SRT execution adapter는 반대로 canonical
  모듈이 top-level 실제 구현을 역참조했습니다. 그중 source 종료 실패 시 Redis client가 닫히지 않는 P2까지
  확인된 KORAIL execution을 이번 최소 슬라이스로 선택했습니다. P0·P1 동작 결함은 없었습니다.
- canonical owner와 compatibility: `KorailSeatObserver`, `ManagedKorailSeatObserver`,
  `KorailExecutionSourceConfig`, 3중 opt-in 정책과 task-local source factory를
  `provider_adapters/korail_execution.py`로 행동 그대로 옮겼습니다. top-level `korail_execution.py`는 기존 5개
  공개 심볼의 exact alias facade만 유지하고 worker도 canonical enablement policy를 직접 사용합니다.
  canonical adapter의 factory/class monkeypatch seam과 worker-local enablement seam은 유지했습니다. 6가지
  legacy import 형태, production canonical import, facade 무정의·정확한 alias와 양방향 import order를
  boundary/contract 테스트로 고정했습니다.
- resource cleanup 보강: 물리 이동 검증 뒤 별도 정책 단계로 `ManagedKorailSeatObserver.aclose()`를
  `source.close()` 후 `redis.aclose()` 순서로 유지하되 `finally`를 적용했습니다. 이제 source 종료가 실패해도
  Redis client는 닫히고 원래 source 오류는 다시 전파됩니다. 외부 주입 source를 adapter가 닫지 않는 borrowed
  ownership과 owned source drain·close·reset 계약은 바꾸지 않았습니다. 독립 최종 리뷰에서 P0~P2 잔여
  지적은 없었습니다.
- 회귀·품질: 이동 직후 기존 KORAIL provider·worker 회귀 131건, owner·facade·boundary 보강 뒤 309건,
  cleanup 실패 회귀까지 310건을 통과했습니다. 현재 API 143개 테스트 파일을 PostgreSQL 경합 없이 4개
  샤드로 실행해 2,043건 통과·1건 skip을 확인했습니다. Ruff `E/F/I`, format ratchet legacy 44개, strict
  mypy 80개 파일과 `uv lock --check`를 통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  worker image에서 5개 legacy/canonical identity·canonical `__module__`·worker binding, Compose 3중 opt-in
  true와 명시적 off의 resource 생성 전 거절, source 실패 뒤 Redis cleanup 순서를 확인했습니다. GUI flag
  `true`·display `:99`·X display, 내부 API `/healthz`·`/readyz`, proxy `/healthz`·`/openapi.json`, noVNC page는
  모두 정상이고 치명 로그 표식은 0건입니다. PostgreSQL `alembic check`는 exit 255와 기존 known drift 8건만
  보고했고 native 제거 제안은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 KORAIL task-local source owner와 종료 순서를,
  `CODE_CONVENTIONS.md`에 owned/borrowed resource cleanup 규칙을 동기화했습니다. 사용자 기능·API·DB schema·
  환경변수·운영 절차가 바뀌지 않아 `CHECKLIST.md`에는 구조 이력을 추가하지 않았습니다. 다음 최소 후보는
  동일한 legacy 역의존을 가진 SRT execution owner 이동이며, TAGO malformed row fail-closed parser는 물리
  분리와 정책 변경을 나눈 후속 슬라이스로 남깁니다.

### 2026-08-07 쉰일곱 번째 구조 슬라이스

- SRT source runtime 분리: top-level `srt_execution.py`가 소유하던 observer Protocol, managed local source,
  설정 snapshot, 3중 opt-in과 local·sidecar·fullstack fixture source 선택을
  `provider_adapters/srt_source_runtime.py`로 옮겼습니다. execution adapter와 source/runtime은 변경 이유가
  달라 KORAIL처럼 한 파일에 합치지 않고 sibling owner로 분리했습니다. canonical
  `provider_adapters/srt_execution.py`는 이 owner를 직접 사용하고, top-level 모듈은 기존 5개 공개 심볼의
  exact alias facade만 유지합니다.
- 조립·호환 계약: 실험 기능·request-time seat status·background monitoring 세 설정이 모두 켜져야 source를
  만듭니다. sidecar 경로는 정확한 내부 URL·timeout·token을 넘기고 local Redis/source를 만들지 않으며,
  local 경로는 호출마다 새 resource를 만듭니다. test 환경의 고정 fixture URL이 있을 때만 fixture factory와
  `fullstack-srt-fixture` source 이름을 주입합니다. 6가지 legacy import 형태와 production canonical import,
  facade 무정의·양방향 import order·`__module__`, canonical adapter의 module-local factory monkeypatch seam을
  계약 테스트로 고정했습니다.
- resource cleanup 보강: 동작 그대로 이동한 뒤 별도 정책 단계로 managed local source의 drain이 실패해도
  Redis client를 `finally`에서 닫도록 했습니다. 기존 drain→Redis 순서와 원래 drain 오류 전파를 유지합니다.
  독립 리뷰는 P0~P2가 없음을 확인했고, P3로 제안한 sidecar/local/fixed-fixture 분기 witness 4건도 외부 호출
  없이 추가했습니다.
- 회귀·품질: 이동 직후 기존 SRT 회귀 72건, owner·facade·boundary 보강 뒤 258건, cleanup 회귀 뒤 259건을
  통과했습니다. 현재 API 144개 테스트 파일을 PostgreSQL 경합 없이 4개 샤드로 실행해 2,057건 통과·1건
  skip을 확인했고, 이후 추가한 조립 분기 4건까지 포함한 final focused 263건도 통과했습니다. Ruff `E/F/I`,
  format ratchet legacy 44개, strict mypy 81개 파일과 `uv lock --check`를 통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  worker image에서 5개 legacy/canonical identity·canonical `__module__`, adapter sibling binding, Compose 3중
  opt-in과 sidecar 선택, 명시적 off의 source 생성 전 거절, drain 실패 뒤 Redis cleanup을 확인했습니다. GUI
  flag `true`·display `:99`·X display, 내부 API `/healthz`·`/readyz`, proxy `/healthz`·`/openapi.json`, noVNC
  page는 모두 정상이고 치명 로그 표식은 0건입니다. PostgreSQL `alembic check`는 exit 255와 기존 known drift
  8건만 보고했고 native 제거 제안은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 SRT source owner와 세 조립 경로·종료 보장을,
  `CODE_CONVENTIONS.md`에 strict 대상 81개를 동기화했습니다. 사용자 기능·API·DB schema·환경변수·운영
  절차가 바뀌지 않아 `CHECKLIST.md`에는 구조 이력을 추가하지 않았습니다. 다음 최소 후보는 TAGO response
  parser의 물리 분리이며 malformed row fail-closed는 별도 정책 단계로 진행합니다.

### 2026-08-07 쉰여덟 번째 구조 슬라이스

- TAGO response owner 분리: `TagoPage`와 `response_page()`를
  `provider_adapters/tago_response.py`로 행동 그대로 옮겼습니다. HTTP·pagination·cache runtime인
  `provider_adapters/tago.py`는 canonical 객체의 exact alias를 사용하고, 중앙 `providers.py`도 기존 import
  호환을 위해 같은 객체만 다시 노출합니다. production import는 canonical owner로 수렴하며, runtime의
  module-global parser monkeypatch seam과 세 import 순서의 identity·`__module__`을 boundary/contract 테스트로
  고정했습니다.
- 외부 JSON fail-closed 보강: 물리 이동 검증 뒤 parser 입력을 `object`에서 시작해 문자열 키 JSON object로
  단계별 narrowing하도록 바꿨습니다. `items.item` list에 object가 아닌 행이 하나라도 있으면 pagination
  완전성을 보존하기 위해 일부 행을 건너뛰지 않고 page 전체를 정확한 `ProviderUnavailable`로 거절합니다.
  boolean, 비유한 float와 소수 float인 pagination 숫자도 같은 canonical 오류 taxonomy로 닫습니다. city-code
  operation만 metadata 없는 응답을 허용하는 기존 예외는 유지했습니다.
- cache·재시도 계약: malformed city row는 24시간 city cache에, malformed timetable row는 raw-day cache에
  들어가지 않으며 실패한 inflight task도 제거됩니다. 두 경로 모두 다음 정상 응답을 실제로 다시 호출해
  성공하는 회귀를 추가했습니다. 이로써 downstream `row.get()`의 `AttributeError`와 일반 500 누출을 provider
  실패 경계로 바꾸면서 한 provider의 실패가 다른 provider의 검증된 결과를 지우지 않는 정책을 유지합니다.
- 회귀·품질: 최종 집중 회귀 282건을 통과했습니다. 현재 API 145개 테스트 파일을 PostgreSQL 경합 없이 4개
  샤드로 순차 실행해 2,094건 통과·1건 skip을 확인했습니다. Ruff `E/F/I`, format ratchet legacy 44개,
  strict mypy 82개 파일과 `uv lock --check`를 통과했습니다. 독립 최종 리뷰에서 P0·P1 잔여 지적은 없었고,
  발견된 pagination 숫자 P2도 같은 슬라이스에서 보강했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  worker image에서 canonical/runtime/facade identity와 malformed row·숫자 3종의 exact 오류를 확인했습니다.
  GUI flag `true`·display `:99`·X display, 내부 API `/healthz`·`/readyz`, proxy `/healthz`·`/openapi.json`, noVNC
  page가 모두 정상이고 최근 치명 로그 표식은 0건입니다. PostgreSQL `alembic check`는 exit 255와 기존 known
  drift 8건만 보고했고 native-push 제거 제안은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`와 `CODE_CONVENTIONS.md`에 parser owner·외부 aggregate 검증·strict 대상
  82개를, `POLICY_AND_SAFETY.md`와 `CHECKLIST.md`에 malformed page 미사용·미캐시 동작을 동기화했습니다.
  다음 단계는 중앙 `services.py`의 잔여 read UoW, `worker.py`의 실제 정책, 중앙 schema/model hub를 다시
  inventory하여 가장 작은 feature owner 이동부터 계속합니다.

### 2026-08-07 쉰아홉 번째 구조 슬라이스

- watch lookup owner: 중앙 `services.py`에 남아 있던 유일한 직접 DB 조회 `find_watch`를
  `watch_management/lookup_application.py`로 옮겼습니다. canonical owner는 호출자가 제공한 session에서
  `get(Watch, watch_id)`를 한 번 수행해 같은 ORM identity를 반환하고, 없으면 transport-independent
  `WatchLookupNotFound`를 발생시킵니다. commit·rollback·refresh·lock·projection·outbox는 소유하지 않아
  PATCH·transition command·DELETE의 기존 UoW와 경쟁 의미를 바꾸지 않습니다.
- transport·compatibility 경계: watch HTTP는 canonical owner를 직접 사용하고 feature-local helper에서만 기존
  404/detail을 변환합니다. 중앙 `services.find_watch`는 기존 signature·module identity·HTTP 오류 계약과 호출
  시점 monkeypatch seam을 유지하는 얇은 wrapper로 남겼습니다. GET·PATCH·DELETE·start·pause·cancel·
  mock-transition 7개 missing 경로, canonical/services/http 세 import order와 unexpected DB 오류 전파를
  회귀로 고정했습니다.
- 구조 재유입 차단: lookup owner의 `session.get` 1회와 transaction primitive 부재, FastAPI·runtime shell
  역의존 금지, services wrapper의 직접 query 제거를 AST boundary로 확인합니다. production 전체에서
  direct·relative·module-style `services.find_watch`를 다시 소비하지 못하게 해 새 lookup이 중앙 facade로
  되돌아가는 경로도 차단했습니다. 독립 리뷰의 P0~P2 잔여 지적은 없었고 P3 재유입 gate도 보강했습니다.

### 2026-08-07 예순 번째 구조 슬라이스

- timetable request schema owner: production 소비자가 하나인 `SeatStatusRefreshRequest`를 중앙
  `schemas.py`에서 `timetable_management/schemas.py`로 행동 그대로 옮겼습니다. timetable HTTP는 sibling owner를
  직접 import하고, 중앙 schema hub는 같은 Pydantic class 객체만 exact alias로 노출합니다. field 순서·필수
  필드·KORAIL/SRT literal·문자열 길이·승객 1~9명·시간 범위 validator와 OpenAPI component/requestBody `$ref`를
  그대로 유지했습니다.
- 정책 비변경 증거: naive datetime, 공백 보존·공백 node ID, 서로 같은 origin/destination node ID와 SRT 요청을
  기존처럼 수용합니다. 이 permissive 값의 강화는 물리 이동에 섞지 않고 별도 정책 작업으로 남겼습니다.
  canonical/legacy/http 세 import order, central 재선언 부재와 direct·relative·module-style 중앙 schema 접근
  금지를 계약·boundary 테스트로 고정했습니다. 독립 리뷰의 P0~P2 잔여 지적은 없습니다.
- 회귀·품질: 두 후속 슬라이스의 최종 구조 집중 회귀 220건을 통과했습니다. 현재 API 148개 테스트 파일을
  PostgreSQL 경합 없이 4개 샤드로 순차 실행해 2,136건 통과·1건 skip을 확인했습니다. 중간에 migration
  `0028_web_push_device_key`가 추가됐지만 `test_migration_0026.py`의 최신 head 기대값만 0027에 머문 1건을
  정확히 분리해 0028로 동기화했고, migration 0026·0027·0028과 마지막 전체 샤드를 다시 통과했습니다. Ruff
  `E/F/I`, format ratchet legacy 44개, strict mypy 83개 파일과 `uv lock --check`도 통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  worker image에서 lookup canonical binding·services 별도 wrapper·canonical/HTTP missing 오류와 schema 세 경로
  identity·SRT validation을 확인했습니다. GUI flag `true`·display `:99`·X display, 내부 API `/healthz`·
  `/readyz`, proxy `/healthz`·`/openapi.json`, noVNC page가 모두 정상이고 최근 치명 로그 표식은 0건입니다.
  PostgreSQL `alembic check`는 exit 255와 기존 known drift 8건만 보고했고 native-push 제거 제안은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 watch lookup과 timetable request schema owner를,
  `CODE_CONVENTIONS.md`에 strict 대상 83개를 동기화했습니다. API wire·사용자 상태·DB schema·환경변수·운영
  절차가 바뀌지 않아 `CHECKLIST.md`에는 구조 이력을 추가하지 않았습니다. worker 감사 결과 다음 최소 후보는
  `ObservationTarget`을 예약 target으로 바꾸는 `_reserve_winner` bridge의 reservation feature owner 이동입니다.

### 2026-08-07 예순한 번째 구조 슬라이스

- reservation execution bridge owner: `worker._reserve_winner`에 있던 `ObservationTarget` →
  `ReservationExecutionTarget` 변환과 canonical 실행 위임을 `reservations/execution_runtime.py`로 옮겼습니다.
  새 owner는 observations 구현을 import하지 않는 structural `ReservationWinnerTarget`만 요구하고 `priority`를
  제외한 실행 필드 13개를 exact-copy합니다. nullable `arrival_at`·`reservation_episode_key`, datetime·provider
  identity를 그대로 보존하며 timezone 정규화나 episode key 생성·조기 종료 정책을 추가하지 않았습니다.
- UoW·resource·compatibility 경계: 예약 claim·provider I/O·결과 transaction과 provider 오류 정규화는 기존
  `execution_application.py`, 공유 adapter의 lease·drain·close는 `observations/group_runtime.py`가 계속
  소유합니다. worker에는 기존 2인자 private wrapper를 남기고 호출할 때마다 현재 `SessionFactory`와 callback을
  `ReservationExecutionDependencies`로 조립합니다. 3개 import order, 13개 필드·`priority` 제외, nullable 값,
  dependency 객체·adapter identity, canonical/worker 예외 identity, lifecycle·transaction 비소유를 계약과 AST
  boundary로 고정했습니다. 사전 독립 감사 2건에서 확인한 episode `None` 조기 반환과 import-time dependency
  capture 위험도 이 계약에 반영했습니다.
- 회귀·품질: execution runtime·application·worker·provider account/circuit·module boundary 집중 회귀 282건과
  최종 runtime/boundary 197건을 통과했습니다. 현재 API 149개 테스트 파일을 PostgreSQL 경합 없이 4개 샤드로
  순차 실행해 2,145건 통과·1건 skip을 확인했습니다. Ruff `E/F/I`, format ratchet legacy 44개, strict mypy
  84개 파일과 `uv lock --check`도 통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  worker image에서 canonical/application/worker binding, 필드 13개와 nullable 값, `priority` 제외, 현재
  dependency factory, adapter·예외 identity를 확인했습니다. GUI flag `true`·display `:99`·X display, 내부 API
  `/healthz`·`/readyz`, proxy `/healthz`·`/openapi.json`, noVNC page가 모두 정상이고 최근 치명 로그 표식은
  0건입니다. PostgreSQL `alembic check`는 exit 255와 이전과 같은 removed index/table/constraint known drift
  8건만 보고했고 native-push 언급은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 bridge·UoW·resource owner를, `CODE_CONVENTIONS.md`에 strict 대상 84개를
  동기화했습니다. API wire·사용자 상태·DB schema·환경변수·운영 절차가 바뀌지 않아 `CHECKLIST.md`에는 순수
  구조 이력을 추가하지 않았습니다. 다음 worker 후보는 composition root를 유지한 채 provider arm 대상 선택을
  별도 정책으로 고정할 수 있는지 조사합니다.

### 2026-08-07 예순두 번째 구조 슬라이스

- due provider arm policy owner: `worker._process_due_watches`에 있던 SRT 기본·KORAIL 조건부 list 결정을 순수
  `observations/due_provider_policy.py`로 옮겼습니다. canonical 함수는 false에서 새 `[SRT]`, true에서 새
  `[SRT, KORAIL]` list를 정확한 순서로 반환하며 MOCK, capability 확인, 설정 조회, DB·adapter·metric 책임을
  갖지 않습니다. `providers_to_arm`은 due watch 전체 처리 allowlist가 아니므로 기존 due pipeline SQL과
  reconciliation provider 선택은 변경하지 않았습니다.
- runtime·compatibility 경계: worker가 매 sweep의 현재 `get_settings()`와 canonical KORAIL background 3중
  opt-in을 계속 평가하고 bool만 policy에 전달합니다. 현재-global gate·selector·pipeline·metric monkeypatch
  seam, 성공 뒤에만 metric 증가, gate 예외 identity와 pipeline 미호출, SRT-first 순서와 매 호출 fresh list를
  회귀로 고정했습니다. 두 독립 사전 감사에서 확인한 KORAIL reservation 설정 혼입, SRT capability 조건부화,
  set/tuple 변환, SQL allowlist 오해 위험도 boundary와 테스트에 반영했습니다.
- 회귀·품질: policy·due pipeline·worker·KORAIL owner·module boundary 집중 회귀 271건을 통과했습니다. 현재 API
  150개 테스트 파일을 PostgreSQL 경합 없이 4개 샤드로 순차 실행해 2,152건 통과·1건 skip을 확인했습니다.
  Ruff `E/F/I`, format ratchet legacy 44개, strict mypy 85개 파일과 `uv lock --check`도 통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  worker image에서 canonical binding, disabled `[srt]`, enabled fresh `[srt, korail]`, 현재 runtime gate와 target을
  확인했습니다. GUI flag `true`·display `:99`·X display, 내부 API `/healthz`·`/readyz`, proxy `/healthz`·
  `/openapi.json`, noVNC page가 모두 정상이고 최근 치명 로그 표식은 0건입니다. PostgreSQL `alembic check`는
  exit 255와 이전과 같은 removed index/table/constraint known drift 8건만 보고했고 native-push 언급은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 arm target policy와 non-allowlist 의미를, `CODE_CONVENTIONS.md`에 strict
  대상 85개를 동기화했습니다. API wire·사용자 상태·DB schema·환경변수·운영 절차가 바뀌지 않아
  `CHECKLIST.md`에는 순수 구조 이력을 추가하지 않았습니다. 중앙 schema hub 감사의 다음 최소 후보는 단일
  production 소비자와 명확한 feature owner를 가진 `SeatStatusSourceStatus`입니다.

### 2026-08-07 예순세 번째 구조 슬라이스

- seat status source schema owner: 중앙 `schemas.py`에 선언돼 있던 `SeatStatusCooldownCause`와
  `SeatStatusSourceStatus`를 `seat_status_operations/schemas.py`로 행동 그대로 옮겼습니다. feature HTTP는
  sibling owner를 직접 import하고 중앙 schema hub는 두 canonical 객체의 exact alias만 제공합니다. 필드 순서,
  KORAIL/SRT·source·ready/cooldown literal, nullable 기본값, retry 최솟값과 두 validator 오류 계약을 유지했습니다.
- 정책 비변경 증거: ready wire의 cause·retry null 포함, cooldown cause 2종, provider/source 교차 조합 허용,
  extra field 무시, 숫자 문자열·정수형 float·bool의 기존 int coercion과 `from_attributes=True`를 회귀로
  고정했습니다. provider/source 일치 validator나 strict int는 물리 이동에 섞지 않았습니다. canonical/legacy/
  HTTP 세 import order, 중앙 재선언 부재, production의 direct·relative·module-style legacy schema 재유입 금지,
  OpenAPI inline cause enum과 endpoint array `$ref`도 계약·AST boundary로 확인했습니다. 두 독립 사전 감사에서
  확인한 permissive 입력과 OpenAPI/model JSON 차이를 테스트 설계에 반영했습니다.
- 회귀·품질: schema·HTTP·API·feature route·module boundary 집중 회귀 285건을 통과했습니다. 현재 API 151개
  테스트 파일을 PostgreSQL 경합 없이 4개 샤드로 순차 실행해 2,174건 통과·1건 skip을 확인했습니다. Ruff
  `E/F/I`, format ratchet legacy 44개, strict mypy 86개 파일과 `uv lock --check`도 통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image에서 cooldown cause·class 중앙 alias, HTTP binding, canonical module·필드 5개·null wire·coercion·
  permissive pair·validator 거절과 OpenAPI `$ref`를 확인했습니다. GUI flag `true`·display `:99`·X display,
  내부 API `/healthz`·`/readyz`, proxy `/healthz`·`/openapi.json`, noVNC page가 모두 정상이고 최근 치명 로그
  표식은 0건입니다. PostgreSQL `alembic check`는 exit 255와 이전과 같은 removed index/table/constraint known
  drift 8건만 보고했고 native-push 언급은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 seat status source schema owner와 기존 상태 조합을,
  `CODE_CONVENTIONS.md`에 strict 대상 86개를 동기화했습니다. API wire·사용자 상태·DB schema·환경변수·운영
  절차가 바뀌지 않아 `CHECKLIST.md`에는 순수 구조 이력을 추가하지 않았습니다. 중앙 schema hub의 다음 작은
  후보는 watch registration conflict detail이며, 중앙 ORM hub는 관계 없는 `OutboxEvent`부터 별도 import-order·
  Alembic 계약을 설계해야 합니다.

### 2026-08-07 예순네 번째 구조 슬라이스

- watch registration conflict schema owner: 중앙 `schemas.py`의 `RegistrationEvidenceConflictDetail`을
  `watch_management/schemas.py`로 행동 그대로 옮겼습니다. 중앙 hub는 canonical class의 exact alias만 제공하고,
  유일한 production 소비자인 `services.create_watch`는 feature owner를 직접 import합니다. `code` 기본값,
  `reason=expired`, message 1~240자와 필드 순서·`ApiModel` 상속을 유지했습니다.
- transport·정책 비변경 증거: `WatchRegistrationEvidenceExpired`의 기존 409 변환 위치와 현재-global
  `create_watch_application` seam을 유지하고, wire는 `{"detail":{"code","reason","message"}}`로 한 번만
  감쌉니다. 공백 message, bytes→문자열, extra 무시, `from_attributes=True`를 그대로 수용하고 잘못된 literal·
  빈 문자열·241자·숫자 message는 기존처럼 거절합니다. 이 model과 409 response가 현재 OpenAPI에 등록되지 않은
  상태도 운영 smoke로 확인했으며, 구조 이동에 409 문서화를 섞지 않았습니다.
- 구조 재유입 차단: canonical/legacy/services 세 import order와 exact identity, 중앙 재선언 부재,
  services direct owner import, production의 direct·relative·module-style 중앙 schema 접근 금지, canonical leaf의
  FastAPI·DB·runtime 역의존 금지를 계약·AST boundary로 고정했습니다. 두 독립 사전 감사에서 확인한 이중 detail,
  strict base·trim 도입, max-length 보정과 순환 의존 위험을 테스트에 반영했습니다.
- 회귀·품질: schema·watch create·실제 timetable evidence 409·module boundary 집중 회귀 244건을
  통과했습니다. 현재 API 152개 테스트 파일을 PostgreSQL 경합 없이 4개 샤드로 순차 실행해 2,192건 통과·1건
  skip을 확인했습니다. Ruff `E/F/I`, format ratchet legacy 44개, strict mypy 87개 파일과 `uv lock --check`도
  통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image에서 중앙 alias·services binding·canonical module·필드 3개와 exact 409 detail, OpenAPI 비등록을
  확인했습니다. GUI flag `true`·display `:99`·X display, 내부 API `/healthz`·`/readyz`, proxy `/healthz`·
  `/openapi.json`, noVNC page가 모두 정상이고 최근 치명 로그 표식은 0건입니다. PostgreSQL `alembic check`는
  exit 255와 이전과 같은 removed index/table/constraint known drift 8건만 보고했고 native-push 언급은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 409 detail schema owner와 OpenAPI 비변경을, `CODE_CONVENTIONS.md`에
  strict 대상 87개를 동기화했습니다. API wire·사용자 상태·DB schema·환경변수·운영 절차가 바뀌지 않아
  `CHECKLIST.md`에는 순수 구조 이력을 추가하지 않았습니다. 다음 watch schema 후보는 독립 선언인
  `WatchUpdate`입니다.

### 2026-08-07 예순다섯 번째 구조 슬라이스

- watch update schema owner: 중앙 `schemas.py`의 독립 선언 `WatchUpdate`를 기존
  `watch_management/schemas.py`로 행동 그대로 옮겼습니다. 중앙 hub는 canonical class의 exact alias만
  제공하고, watch HTTP·update application·중앙 `services.update_watch` 호환 wrapper는 feature owner를 직접
  사용합니다. 10개 필드의 순서·nullable 기본값, 승객 1~9명, 목록 최대 20개, 집중 관찰 20~30초 제약과
  `payment_deadline`의 timezone 필수·UTC 정규화를 유지했습니다.
- 부분 수정·정책 비변경 증거: 빈 `{}`의 `model_fields_set`과 `exclude_unset=True` 결과는 기존처럼 비어 있고,
  JSON dict의 알려진 10개 필드와 알려지지 않은 필드에 명시한 `null`은 모두 거절합니다. 숫자 문자열·정수형
  float·bool coercion, timezone이 있는 time, 빈·중복·공백 목록 항목, `seat_class=any`, extra non-null 무시와
  `from_attributes=True`도 그대로 보존했습니다. 시간 범위·후보 일치·활성 작업 수정 가능 필드·집중 관찰 용량은
  update application 정책으로 남겼고, OpenAPI의 nullable 표현과 runtime의 명시적 `null` 거절 차이를 이번
  물리 이동에서 수정하지 않았습니다.
- 구조 재유입 차단: canonical/legacy/services/HTTP/update-application 다섯 import order와 exact identity,
  중앙 재선언 부재·exact alias, 세 production consumer의 canonical direct import, production 전체의
  direct·relative·module-style 중앙 schema 재유입 금지와 canonical leaf의 transport·runtime·persistence
  역의존 금지를 계약·AST boundary로 고정했습니다. 두 독립 감사와 이동 후 재검토에서 잔여 P0·P1 계약 변화나
  import cycle은 발견되지 않았습니다.
- 회귀·품질: 새 schema 계약 32건, 기존 update application 15건, 관련 API 5건과 전체 module boundary 202건을
  통과했습니다. 현재 API 153개 테스트 파일을 PostgreSQL 경합 없이 4개 샤드로 순차 실행해 2,229건 통과·1건
  skip을 확인했습니다. Ruff `E/F/I`, format ratchet legacy 44개, strict mypy 87개 파일과
  `uv lock --check`도 통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image에서 중앙 alias와 services·HTTP·update application binding, canonical module·필드 10개·빈 부분 수정
  결과를 확인했습니다. GUI flag `true`·display `:99`·X display, 내부 API `/healthz`·`/readyz`, proxy
  `/healthz`·`/openapi.json`, noVNC page가 모두 정상이고 최근 로그 440줄의 치명 표식은 0건입니다. PostgreSQL
  `alembic check`는 exit 255와 이전과 같은 removed index/table/constraint known drift 8건만 보고했고
  native-push 언급은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 부분 수정 schema owner와 생략/null 경계를,
  `CODE_CONVENTIONS.md`의 strict schema 범위에 부분 수정 요청을 동기화했습니다. API wire·사용자 상태·DB
  schema·환경변수·운영 절차가 바뀌지 않아 `CHECKLIST.md`에는 순수 구조 이력을 추가하지 않았습니다. 다음 watch
  schema 슬라이스는 서로 중첩되는 `WatchCandidateCreate`와 `WatchCreate`를 한 묶음으로 이동하고, 중앙 ORM
  hub의 `OutboxEvent`는 별도의 mapper·Alembic·import-order 계약을 먼저 설계합니다.

### 2026-08-07 예순여섯 번째 구조 슬라이스

- watch create schema pair owner: 중앙 `schemas.py`에서 서로 중첩된 `WatchCandidateCreate`와 `WatchCreate`를
  `watch_management/schemas.py`로 한 번에 옮겼습니다. 중앙 hub는 두 canonical class의 exact alias만 제공하고,
  watch HTTP·create application·중앙 `services.create_watch` 호환 wrapper는 `WatchCreate` owner를 직접
  사용합니다. candidate 6개·watch 17개 필드 순서와 default/default factory, nested annotation identity,
  문자열·목록·숫자 제약, enum과 POST OpenAPI component·request `$ref`를 유지했습니다.
- 검증·정책 비변경 증거: 후보 열차 번호 trim, aware datetime·도착 순서, watch의 KST 현재 날짜·route/time·
  node pair, official node 필수, 후보 identity·연속 priority·좌석 등급·KST 날짜/포함 시간 범위·열차 집합
  validator를 원문 AST와 동일하게 이동했습니다. MOCK 동일 node ID, official candidate의 evidence 누락을 schema
  단계에서 허용하는 경계, priority 2/1 입력 순서, 중복 top-level train number, 빈 candidates와 임의·중복 열차
  번호, 공백·중복 알림 ID, `seat_class=any`, 숫자 coercion과 extra 무시도 그대로 고정했습니다. official
  evidence·runtime gate·채널 존재·UoW는 create application 책임으로 남겼습니다.
- 구조 재유입 차단: canonical/legacy/services/HTTP/create-application 다섯 import order, 두 class와 nested
  annotation의 exact identity, 중앙 재선언 부재·exact alias, 세 production consumer의 sibling owner import,
  production 전체의 direct·relative·module-style 중앙 schema 재유입 금지와 canonical leaf의 transport·runtime·
  persistence 역의존 금지를 계약·AST boundary로 고정했습니다. 두 차례 독립 검토에서 남은 P0·P1 누락이나
  import cycle은 없었고, 테스트의 KST 미래 날짜를 모듈에서 한 번만 계산해 자정 경계 flake도 제거했습니다.
- 회귀·품질: 새 schema 계약 23건, 기존 create application 14건, 관련 API 8건과 전체 module boundary 209건을
  통과했습니다. 현재 API 154개 테스트 파일을 PostgreSQL 경합 없이 4개 샤드로 순차 실행해 2,259건 통과·1건
  skip을 확인했고, 자정 안정성 보강 뒤 schema 계약 23건을 다시 통과했습니다. Ruff `E/F/I`, format ratchet
  legacy 44개, strict mypy 87개 파일과 `uv lock --check`도 통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image에서 두 중앙 alias·세 consumer binding·nested candidate identity·필드 17/6개와 POST OpenAPI의 두
  component `$ref`를 확인했습니다. GUI flag `true`·display `:99`·X display, 내부 API `/healthz`·`/readyz`, proxy
  `/healthz`·`/openapi.json`, noVNC page가 모두 정상이고 최근 로그 353줄의 치명 표식은 0건입니다. PostgreSQL
  `alembic check`는 exit 255와 이전과 같은 removed index/table/constraint known drift 8건만 보고했고
  native-push 언급은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 생성 schema pair와 application 정책 경계를,
  `CODE_CONVENTIONS.md`의 strict schema 범위에 생성 요청을 동기화했습니다. API wire·사용자 상태·DB schema·
  환경변수·운영 절차가 바뀌지 않아 `CHECKLIST.md`에는 순수 구조 이력을 추가하지 않았습니다. 다음 작은 provider
  이동 후보는 KORAIL 예약 확인 leaf이며, 중앙 `OutboxEvent`는 `outbox_management/models.py` owner와 mapper·
  Alembic known-drift 계약을 준비한 뒤 별도 슬라이스로 진행합니다.

### 2026-08-07 예순일곱 번째 구조 슬라이스

- KORAIL 예약 확인 leaf owner: top-level `korail_reservation_confirmation.py`의 읽기 전용 evidence·
  normalizer·adapter와 상수 3개를 `reservations/provider_confirmation/korail.py`로 옮겼습니다. top-level
  모듈은 기존 7개 공개 심볼과 같은 객체만 노출하는 exact alias facade로 남기고, Pydoll browser·confirmation
  reader·KORAIL sidecar HTTP는 canonical owner를 직접 사용합니다.
- 결과 계약·정책 비변경 증거: non-KORAIL target 거절 뒤 provider 차단, 인증 필요, credential version
  불일치, 정확한 결제 대기 근거, 완료된 공식 예약 목록의 대상 부재, 그 밖의 불명확 결과 순서를 그대로
  유지했습니다. 같은 탭 상세 화면의 단순 부재와 불완전한 목록 조회는 `INCONCLUSIVE`로 닫고,
  `CONFIRMED_PAYMENT_REQUIRED`만 검증된 공식 예약 목록 handoff URL과 기존 payment deadline을 전달합니다.
  모든 결과의 자동 예약 재시도 금지 계약도 바꾸지 않았습니다.
- 구조 재유입 차단: legacy facade의 정확한 7개 alias와 canonical/legacy/세 consumer import order,
  production consumer의 canonical direct import, canonical leaf의 exact import allowlist와 transport·runtime·
  persistence 역의존 부재를 계약·AST boundary로 고정했습니다. 어댑터는 probe·normalizer를 각각 한 번 호출하고
  target·evidence identity 및 두 경계의 원래 예외 객체를 그대로 전파합니다. 독립 재검토에서 잔여 결함은
  발견되지 않았습니다.
- 회귀·품질: 이동 직후 현재 API 155개 테스트 파일을 PostgreSQL 경합 없이 4개 샤드로 순차 실행해
  2,289건 통과·1건 skip을 확인했습니다. 이후 exact import allowlist와 normalizer 오류 identity 회귀를
  추가해 새 owner와 전체 module boundary focused pytest 241건을 통과했습니다. Ruff `E/F/I`, format
  ratchet legacy 43개, strict mypy 88개 파일과 `uv lock --check`도 통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image에서 legacy/canonical 7개 identity, canonical `__module__`과 browser·reader·sidecar binding을
  확인했습니다. GUI flag `true`·display `:99`·X display, 내부 API `/healthz`·`/readyz`, proxy `/healthz`·
  `/openapi.json`, noVNC page가 모두 정상이고 최근 로그 396줄의 치명 표식은 0건입니다. PostgreSQL
  `alembic check`는 exit 255와 이전과 같은 removed table 3개·index 3개·constraint 2개의 known drift만
  보고했고 native-push 언급은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 KORAIL 읽기 전용 확인 owner와 fail-closed 판정 순서를,
  `CODE_CONVENTIONS.md`에 strict 대상 88개를 동기화했습니다. API wire·사용자 상태·DB schema·환경변수·
  운영 절차·안전 범위가 바뀌지 않아 `CHECKLIST.md`에는 순수 구조 이력을 추가하지 않았습니다. 다음 최소
  후보는 worker의 task-local event-loop/engine cleanup이며, 중앙 `OutboxEvent`는 mapper·Alembic·import-order
  계약을 먼저 설계한 뒤 별도 슬라이스로 진행합니다.

### 2026-08-07 예순여덟 번째 구조 슬라이스

- worker task cleanup runtime owner: `worker.py`의 작업 await와 `engine.dispose()` `try/finally`를 순수
  `worker_task_runtime.py`의 `run_task_isolated`로 옮겼습니다. owner는 DB·Celery를 import하지 않고 작업
  awaitable과 `dispose_engine` callback만 받습니다. worker의 기존 private `_run_isolated` 이름과 시그니처는
  호출 시점의 `engine.dispose`를 주입하는 얇은 wrapper로 유지하고, 네 공개 Celery task의 이름·route·
  `asyncio.run`·성공/실패 metric은 composition shell에 그대로 남겼습니다.
- cleanup 계약 비변경 증거: 작업 성공 뒤 반환, 작업 실패 identity 전파, 정리 단독 실패 identity 전파,
  작업과 정리가 모두 실패할 때 정리 예외가 최종이고 작업 예외가 `__context__`에 남는 Python `finally`
  우선순위를 고정했습니다. 실제 `Task.cancel()`에서도 operation 시작 뒤 정리가 정확히 한 번 실행되고
  `CancelledError`와 cancelled 상태가 보존됩니다. owner가 전역 engine을 import하지 않아 기존
  `worker.engine` call-time monkeypatch seam도 유지됩니다.
- 구조 재유입 차단: canonical owner의 함수·분기 내부까지 포함한 exact import allowlist를 `__future__`와
  `collections.abc`로 제한했습니다. worker wrapper에는 `try/finally`가 다시 들어가지 않고 operation identity와
  현재 disposer만 한 번 전달합니다. 네 task마다 유일한 `asyncio.run`의 직접 인자가
  `_run_isolated(...)`인지와 기존 Celery decorator name을 AST로 고정했습니다. 두 차례 독립 재검토 후 남은
  P0~P3 결함은 없습니다.
- 회귀·품질: 새 runtime과 전체 module boundary focused pytest 226건, 기존 worker 회귀 62건을 통과했습니다.
  현재 API 156개 테스트 파일을 PostgreSQL 경합 없이 4개 샤드로 순차 실행해 2,302건 통과·1건 skip을
  확인했고 리뷰 보강 뒤 focused 226건을 다시 통과했습니다. Ruff `E/F/I`, format ratchet legacy 43개,
  strict mypy 89개 파일과 `uv lock --check`도 통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  worker image에서 canonical binding·module, `operation -> dispose` 순서·결과 17과 네 Celery task name을
  확인했습니다. GUI flag `true`·display `:99`·X display, 내부 API `/healthz`·`/readyz`, proxy `/healthz`·
  `/openapi.json`, noVNC page가 모두 정상이고 최근 로그 384줄의 치명 표식은 0건입니다. PostgreSQL
  `alembic check`는 exit 255와 이전과 같은 removed object 8건만 보고했고 native-push 언급은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 task shell과 cleanup owner의 책임을, `CODE_CONVENTIONS.md`에
  task-local disposer 주입·실패 우선순위와 strict 대상 89개를 동기화했습니다. API wire·사용자 상태·DB
  schema·환경변수·운영 절차가 바뀌지 않아 `CHECKLIST.md`에는 순수 구조 이력을 추가하지 않았습니다. 다음
  SRT 예약 확인 leaf 이동은 가능하지만, 공식 목록 완료 근거가 암묵적이고 순수 identity normalizer가 큰
  `srt_seat_source.py`에 묶여 있어 물리 이동과 의미 강화를 별도 슬라이스로 나눕니다.

### 2026-08-07 예순아홉 번째 구조 슬라이스

- SRT identity formatter owner: `srt_seat_source.py`에 있던 열차 번호·날짜·시각 정규화 함수 3개를 순수
  `provider_adapters/srt_identity.py`로 함께 옮겼습니다. 좌석 source는 기존 공개 import 호환과 내부 lookup을
  위해 exact same-name alias를 유지하고, `srt_reservation.py`와 `srt_reservation_confirmation.py`는 canonical
  owner를 직접 사용합니다. 세 기존 모듈 attribute와 canonical 함수는 모든 import 순서에서 같은 객체입니다.
- 동작 비변경 증거: 세 함수는 `str(value)`를 한 번 호출한 뒤 Unicode `isdigit()` 문자를 보존하고 ASCII
  선행 0 제거, zero padding 또는 뒤쪽 길이 절단만 수행합니다. 빈값·`None`·숫자 없는 값, 8자를 넘는 날짜,
  5자·7자 시각과 유효하지 않은 날짜·시각도 기존처럼 포맷하며 검증 오류로 강화하지 않았습니다. `str()`
  변환 오류는 원래 예외 객체 그대로 전파합니다.
- 구조 재유입 차단: canonical owner의 함수·분기 내부까지 exact import allowlist를 `__future__` 하나로
  고정하고, source에는 로컬 normalizer 정의가 없으며 세 same-name alias만 존재하는지 검사합니다. 예약·확인
  consumer는 alias 없이 canonical을 직접 import합니다. legacy source에서 normalizer를 직접·wildcard로
  가져오거나 module binding으로 우회하는 상대·절대 7개 형식을 resolver와 synthetic 회귀로 차단하되,
  `SrtLiveSeatSource` 같은 다른 공개 class의 direct import는 허용합니다. 독립 재검토 후 남은 P0~P3 결함은
  없습니다.
- 회귀·품질: 새 owner 31건, 기존 SRT 좌석 source·예약·확인 83건과 리뷰 보강 뒤 owner·전체 module boundary
  focused 260건을 통과했습니다. 현재 API 157개 테스트 파일을 PostgreSQL 경합 없이 4개 샤드로 순차 실행해
  2,344건 통과·1건 skip을 확인했습니다. Ruff `E/F/I`, format ratchet legacy 42개, strict mypy 90개 파일과
  `uv lock --check`도 통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image에서 canonical/좌석 source/예약/확인 네 경로의 3개 함수 identity·canonical `__module__`과
  `28, 20260807, 123700` 대표 결과를 확인했습니다. GUI flag `true`·display `:99`·X display, 내부 API
  `/healthz`·`/readyz`, proxy `/healthz`·`/openapi.json`, noVNC page가 모두 정상이고 최근 로그 497줄의 치명
  표식은 0건입니다. PostgreSQL `alembic check`는 exit 255와 이전과 같은 removed object 8건만 보고했고
  native-push 언급은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 SRT identity owner와 formatter/validator 경계를,
  `CODE_CONVENTIONS.md`에 strict 대상 90개를 동기화했습니다. API wire·사용자 상태·DB schema·환경변수·운영
  절차가 바뀌지 않아 `CHECKLIST.md`에는 순수 구조 이력을 추가하지 않았습니다. 이제 SRT 예약 확인 leaf를
  `reservations/provider_confirmation/srt.py`로 이동할 수 있지만, 빈 목록을 공식 부재로 보는 현행 계약의
  의미 강화는 중복 예약 안전성과 연결되므로 물리 이동과 분리합니다.

### 2026-08-07 일흔 번째 구조 슬라이스

- SRT 예약 확인 leaf owner: top-level `srt_reservation_confirmation.py`의 record·evidence·probe·adapter,
  normalizer 2개와 상수 3개를 `reservations/provider_confirmation/srt.py`로 옮겼습니다. SRT reservation은
  canonical owner를 직접 사용하고, top-level 모듈은 confirmation 공개 심볼 9개의 exact alias facade로
  남겼습니다. 이전 모듈이 import로 노출하던 SRT identity formatter 3개도 `__all__`에는 넣지 않은 same-name
  compatibility attribute로 보존했습니다.
- 결과 계약·정책 비변경 증거: non-SRT target 거절 뒤 provider 차단, 인증 필요, credential generation 불일치,
  trip 부재, exact match 중복·결제 완료, 정확한 미결제 1건 순서를 그대로 유지했습니다. 결제 기한이 잘못된
  값이어도 exact 미결제 hold는 기한 없이 확정하며 handoff URL은 호출 시점에 공식 host를 다시 검증합니다.
  reserve 결과는 provider를 다시 호출하지 않고 redacted record 복사본을 같은 현재 normalizer에 전달합니다.
  현행 evidence에는 공식 목록 완료 증명 필드가 없어 credential이 일치하는 빈 records가 `NOT_FOUND`가 되는
  기존 의미도 물리 이동에서 바꾸지 않았습니다.
- 구조 재유입 차단: canonical owner의 함수·분기 내부 exact import allowlist를 stdlib 5개와 domain·SRT
  identity·공통 confirmation 세 경계로 고정했습니다. facade는 confirmation 9개와 identity compatibility
  3개 import 집합을 구분하고, production consumer는 alias 없이 canonical을 직접 사용합니다. legacy facade
  direct·wildcard·module binding의 상대·절대 7형식을 resolver와 synthetic 회귀로 차단했습니다. adapter의
  target/evidence identity·단일 호출과 probe/normalizer 원래 예외 전파도 고정했습니다.
- 회귀·리뷰 보정: 새 owner 22건, 기존 SRT confirmation·reservation·provider adapter 68건과 최종
  identity·confirmation·전체 module boundary focused 293건을 통과했습니다. 최초 facade에서 기존 identity
  module attribute 3개가 빠진 P1을 독립 리뷰와 전체 shard가 5건으로 재현했고, same-name compatibility alias와
  exact boundary를 보강한 뒤 독립 최종 리뷰에서 남은 P0~P3가 없음을 확인했습니다.
- 전체 품질: 현재 API 158개 테스트 파일을 수정 후 PostgreSQL 경합 없이 4개 샤드로 다시 순차 실행해
  2,377건 통과·1건 skip을 확인했습니다. Ruff `E/F/I`, format ratchet legacy 42개, strict mypy 91개 파일과
  `uv lock --check`도 통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image에서 facade 9개 exact identity, identity formatter 3개 compatibility, reservation binding,
  canonical `__module__`과 빈 목록 `not_found`를 확인했습니다. GUI flag `true`·display `:99`·X display,
  내부 API `/healthz`·`/readyz`, proxy `/healthz`·`/openapi.json`, noVNC page가 모두 정상이고 최근 로그
  427줄의 치명 표식은 0건입니다. PostgreSQL `alembic check`는 exit 255와 이전과 같은 removed object
  8건만 보고했고 native-push 언급은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 SRT confirmation owner와 현행 빈 목록 근거 한계를,
  `CODE_CONVENTIONS.md`에 strict 대상 91개를 동기화했습니다. API wire·사용자 상태·DB schema·환경변수·운영
  절차가 바뀌지 않아 `CHECKLIST.md`에는 순수 구조 이력을 추가하지 않았습니다. 빈 공식 목록을 재무장 근거로
  사용할 수 있는 기존 안전 부채는 explicit completed-list proof와 downstream attempt policy를 함께 다루는
  별도 의미 변경 슬라이스로 남깁니다.

### 2026-08-07 일흔한 번째 구조 슬라이스

- Outbox persistence mapper owner: 중앙 `models.py`의 독립 `OutboxEvent` 선언을
  `outbox_management/models.py`로 옮겼습니다. `outbox.py`, event stream HTTP, 알림 delivery, `main.py`,
  운영 조회와 예약 실행 application은 canonical owner를 직접 사용하고, 중앙 `models.py`는 기존 import와
  Alembic metadata bootstrap을 위해 같은 class·Table·mapper 객체만 exact alias로 노출합니다.
- DB 계약 비변경 증거: 기존 `outbox_events`의 12개 컬럼 순서·타입·nullable·PK, unnamed `dedupe_key`
  unique, 단일 컬럼 index 5개, `OutboxStatus` 이름 저장값 3개, Python default 5개와 server default 없음까지
  고정했습니다. FK·check constraint·relationship은 없고 어떤 import 순서에서도 중앙 metadata의 table과
  mapper가 한 번만 등록됩니다. Alembic bootstrap은 중앙 mapper 등록 import 뒤 `Base.metadata`를 계속
  사용합니다.
- 구조 재유입 차단: 중앙 exact alias 외 production code의 canonical import 사용처를 6개로 고정하고,
  그 밖의 production module이 중앙 alias나 별도 선언을 새로 사용하지 못하도록 AST 경계를 추가했습니다.
  독립 리뷰에서 구현 P0~P2는 없었고 index 컬럼·unique 여부, check constraint 부재, 예상 외 Python default
  부재와 enum DB 저장 표현까지 계약 테스트를 보강했습니다.
- 회귀·품질: owner·전체 module boundary focused pytest 258건과 최종 owner pytest 9건을 통과했습니다.
  현재 API 159개 테스트 파일을 PostgreSQL 경합 없이 4개 샤드로 순차 실행해 2,395건 통과·1건 skip을
  확인했습니다. Ruff `E/F/I`, format ratchet legacy 42개, strict mypy 92개 파일과 `uv lock --check`도
  통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image에서 legacy/canonical identity, canonical module, 중앙 metadata·단일 mapper, 컬럼·index·enum·
  default·FK·relationship 계약을 확인했습니다. GUI flag `true`·display `:99`·X display, 내부 API
  `/healthz`·`/readyz`, proxy `/healthz`·`/openapi.json`, noVNC page가 모두 정상이고 최근 로그 520줄의 치명
  표식은 0건입니다. PostgreSQL `alembic check`는 exit 255와 이전과 같은 removed object 8건만 보고했으며
  `outbox_events`와 native-push 언급은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 Outbox mapper owner와 중앙 metadata 호환 경계를,
  `CODE_CONVENTIONS.md`에 strict 대상 92개를 동기화했습니다. API wire·사용자 상태·DB schema·환경변수·운영
  절차·안전 범위가 바뀌지 않아 `CHECKLIST.md`에는 순수 구조 이력을 추가하지 않았습니다. 중앙 `Watch` 등
  관계 graph가 있는 mapper는 관계 양쪽과 metadata import-order 계약을 먼저 설계한 뒤 별도 슬라이스로
  진행합니다.

### 2026-08-07 일흔두 번째 구조 슬라이스

- KORAIL sidecar wire contract owner: top-level `korail_reservation_contract.py`의 Literal 계약 5개와
  Pydantic 모델 8개를 `korail_sidecar/contracts.py`로 옮겼습니다. sidecar HTTP, KORAIL browser seat source와
  provider login verification은 canonical owner를 직접 사용하고, top-level 모듈은 같은 계약 객체를 노출하는
  compatibility facade로 남겼습니다.
- 동작·호환 비변경 증거: 모든 모델의 `extra="forbid"`, `SecretStr` redaction, credential·session 상태,
  aware datetime, 서로 다른 역·시각, confirmation handoff shape와 예약 진행 순서 validator를 그대로
  유지했습니다. 기존 13개 의미 심볼의 identity와 canonical `__module__`뿐 아니라 wildcard import로 노출되던
  dependency attribute까지 포함한 공개 표면 23개를 고정했습니다. facade는 import 호환 경계이고 속성 재할당을
  production dependency injection으로 전달하는 비공식 monkeypatch seam은 저장소 사용처가 없어 canonical
  direct import 원칙을 유지했습니다.
- 구조 재유입 차단: canonical owner의 import를 stdlib·Pydantic으로 한정하고 facade에는 로컬 class·함수를
  두지 않았습니다. 세 production consumer의 exact canonical import와 legacy facade의 direct·wildcard·module
  binding 7형식 재유입 차단을 AST로 고정했습니다. 다섯 import 순서에서도 13개 계약 identity와 8개 class
  module이 같습니다. 독립 리뷰에서 구현 P0~P2는 없었습니다.
- 회귀·품질: 새 owner와 전체 module boundary focused pytest 274건, KORAIL browser source·automation·예약·
  confirmation·provider login 관련 pytest 200건을 통과했습니다. 현재 API 160개 테스트 파일을 PostgreSQL
  경합 없이 4개 샤드로 순차 실행해 2,420건 통과·1건 skip을 확인했습니다. Ruff `E/F/I`, format ratchet
  legacy 40개, strict mypy 93개 파일과 `uv lock --check`도 통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image에서 facade/canonical identity, 8개 model module·config, 공개 표면 23개와 세 consumer binding을
  확인했습니다. GUI flag `true`·display `:99`·X display, 내부 API `/healthz`·`/readyz`, proxy `/healthz`·
  `/openapi.json`, noVNC page가 모두 정상이고 최근 로그 466줄의 치명 표식은 0건입니다. PostgreSQL
  `alembic check`는 exit 255와 이전과 같은 removed object 8건만 보고했으며 `outbox_events`와 native-push
  언급은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 sidecar wire contract owner와 facade 범위를,
  `CODE_CONVENTIONS.md`에 strict 대상 93개를 동기화했습니다. API wire·사용자 상태·DB schema·환경변수·운영
  절차·안전 범위가 바뀌지 않아 `CHECKLIST.md`에는 구조 이력을 추가하지 않았습니다. 다음 중앙 ORM 이동은
  `Watch`·`WatchCandidate`·`SeatObservation`·`ReservationAttempt`·`WatchTransitionHistory`가 하나의 양방향
  관계 graph이므로 개별 class가 아니라 공동 persistence owner 슬라이스로 설계합니다.

### 2026-08-07 일흔세 번째 구조 슬라이스

- watch persistence aggregate owner: 중앙 `models.py`의 `Watch`·`WatchCandidate`·`SeatObservation`·
  `ReservationAttempt`·`WatchTransitionHistory`와 `utcnow`를 `watch_management/models.py`로 함께 옮겼습니다.
  다섯 mapper는 양방향 관계·candidate self-reference·관측·예약 시도·전이 이력·문자열 정렬식이 한 graph를
  이루므로 class별로 쪼개지 않았습니다. 중앙 `models.py`는 모든 canonical owner를 metadata에 등록하고 기존
  import 호환을 위해 같은 다섯 class와 함수를 exact alias로만 노출합니다.
- DB·mapper 계약 비변경 증거: 다섯 table의 column 순서·type/length/timezone·nullable·PK, index·unique·
  CheckConstraint 이름과 SQL 식, FK target·`ondelete`, Python scalar/callable default·server default·onupdate,
  enum class·이름 저장값, 13개 relationship의 target·back-populates·cascade·lazy·passive-delete·order·명시적
  FK·remote side와 legacy `reservation_attempt` property를 고정했습니다. 외부 등록 근거 관계는
  `timetable_management/models.py`의 `TimetableSeatEvidence` canonical mapper를 직접 사용합니다. 다섯 import
  순서에서 `configure_mappers()`를 실행해 중앙 metadata의 table·mapper가 각각 한 번만 등록됨을 확인했습니다.
- 구조 재유입 차단: watch·reservation·observation·notification·provider account·UI preference·worker 등
  production consumer 28개를 canonical symbol import로 전환했습니다. owner의 import를 symbol 단위 exact
  allowlist로 제한하고 중앙 alias·별도 선언·module-style 우회를 전체 production AST 검사로 차단했습니다.
  관측 기록·인증 복구·stale 예약 복구 application은 watch feature를 models 경계로만 참조합니다. 독립 리뷰에서
  구현 P0~P2는 없었고, 리뷰가 찾은 constraint SQL·non-enum type·scalar default·module import 차단의 P3 테스트
  빈틈을 보강한 뒤 집중 owner·module boundary pytest 309건을 통과했습니다.
- 회귀·품질: 기존 watch 생성·전이·수정, 관측 기록·group, 예약 claim·result·execution·reconciliation,
  service watch state와 worker focused pytest 187건을 통과했습니다. 현재 API 161개 테스트 파일을 PostgreSQL
  경합 없이 4개 샤드로 순차 실행해 2,467건 통과·1건 skip을 확인했습니다. Ruff `E/F/I`, format ratchet
  legacy 40개, strict mypy 94개 파일과 `uv lock --check`도 통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image에서 다섯 legacy/canonical identity·canonical module·중앙 metadata·단일 mapper, 13개 relationship,
  외부 등록 근거 target과 `utcnow` identity를 확인했습니다. GUI flag `true`·display `:99`·X display, 내부 API
  `/healthz`·`/readyz`, proxy `/healthz`·`/openapi.json`, noVNC page가 모두 정상이고 최근 로그 619줄의 치명
  표식은 0건입니다. PostgreSQL `alembic check`는 exit 255와 이전과 같은 removed object 8건만 보고했으며
  watch graph table·`outbox_events`·native-push 언급은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 watch aggregate owner와 중앙 metadata 호환 경계를,
  `CODE_CONVENTIONS.md`에 결합 mapper graph 공동 이동 원칙과 strict 대상 94개를 동기화했습니다. API wire·
  사용자 상태·DB schema·환경변수·운영 절차·안전 범위가 바뀌지 않아 `CHECKLIST.md`에는 순수 구조 이력을
  추가하지 않았습니다. 다음 최소 후보는 `ReservationConfirmationOutcome` enum을 경량 confirmation 계약
  owner로 옮겨 watch persistence가 중앙 `schemas.py`까지 끌어오는 의존을 제거하는 슬라이스입니다.

### 2026-08-07 일흔네 번째 구조 슬라이스

- confirmation outcome leaf contract owner: top-level `reservation_confirmation.py`의
  `ReservationConfirmationOutcome` enum만
  `reservations/provider_confirmation/contracts.py`로 옮겼습니다. target·result·공식 URL host 검증은 의존
  범위가 더 크므로 이번 물리 이동에 섞지 않았습니다. top-level 모듈은 기존 import 호환을 위해 canonical enum
  객체를 same-name exact alias로 노출합니다. watch persistence와 예약·관측·KORAIL/SRT confirmation 등
  production consumer 15개는 새 leaf owner를 직접 사용합니다.
- 동작·DB 계약 비변경 증거: 다섯 member의 이름·값·순서, `StrEnum` 의미와 pickle identity를 유지했습니다.
  `ReservationAttempt.confirmation_outcome`은 canonical enum class, nullable, `native_enum=False`, 대문자 member
  name 저장값 5개와 기존 SQLAlchemy type name `reservationconfirmationoutcome`을 그대로 사용합니다. 중앙
  `ReservationAttempt` alias·table metadata·단일 mapper도 유지됩니다. canonical/legacy/models/schema/
  watch-schema 우선 다섯 import 순서에서 `configure_mappers()`를 실행해 enum·table·mapper identity를
  확인했습니다.
- 의존·구조 재유입 차단: leaf owner import를 `__future__`·`enum.StrEnum`으로 제한하고 상위
  `reservations`·`provider_confirmation` package도 docstring만 가진 passive 경계로 고정했습니다. canonical
  contract만 import한 새 process에는 top-level confirmation과 중앙 `schemas.py`가 로딩되지 않습니다.
  15개 consumer의 direct symbol import를 정확히 고정하고 legacy·canonical module-style, wildcard와 다른
  owner 재유입을 production AST로 차단했습니다. 독립 리뷰에서 P0~P2 결함은 없었고, package passivity·
  schema-first import order·Enum type name의 P3 테스트 빈틈을 보강한 뒤 owner·module boundary pytest 326건을
  통과했습니다.
- 회귀·품질: confirmation owner·integration, 예약 attempt policy/result, payment hold, reconciliation state와
  service watch state focused pytest 201건을 통과했습니다. 현재 API 162개 테스트 파일을 PostgreSQL 경합
  없이 4개 샤드로 순차 실행해 2,496건 통과·1건 skip을 확인했습니다. Ruff `E/F/I`, format ratchet legacy
  40개, strict mypy 95개 파일과 `uv lock --check`도 통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config와 전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image에서 canonical/legacy enum identity·canonical module·다섯 member, schema hub 미로딩, ORM enum
  class·type name·non-native 저장 이름, 중앙 attempt alias와 단일 mapper를 확인했습니다. GUI flag `true`·
  display `:99`·X display, 내부 API `/healthz`·`/readyz`, proxy `/healthz`·`/openapi.json`, noVNC page가 모두
  정상이고 최근 로그 380줄의 치명 표식은 0건입니다. PostgreSQL `alembic check`는 exit 255와 이전과 같은
  removed object 8건만 보고했으며 confirmation enum·watch graph table·`outbox_events`·native-push 언급은
  없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 공통 confirmation outcome owner와 watch persistence의 경량 의존을,
  `CODE_CONVENTIONS.md`에 leaf contract 원칙과 strict 대상 95개를 동기화했습니다. API wire·사용자 상태·DB
  schema·환경변수·운영 절차·안전 범위가 바뀌지 않아 `CHECKLIST.md`에는 순수 구조 이력을 추가하지
  않았습니다. 다음 confirmation 이동은 공식 host allowlist를 provider-neutral 정책 owner로 먼저 분리한 뒤
  target·result·adapter 계약을 함께 다루며, 그 전에는 중앙 schema hub 이동과 섞지 않습니다.

### 2026-08-07 일흔다섯 번째 구조 슬라이스

- official URL policy owner: 중앙 `schemas.py`의 `OFFICIAL_HOST_ROOTS`·`is_official_provider_host`와 top-level
  confirmation의 `require_official_handoff_url`을 `provider_registry/official_url_policy.py`로 옮겼습니다.
  중앙 schema는 roots와 host predicate의 same-object alias만 남기고 기존 Pydantic validator 본문은 그대로
  유지합니다. KORAIL·SRT confirmation owner는 handoff validator를 canonical policy에서 직접 가져옵니다.
  KORAIL·SRT·MOCK roots, HTTPS·userinfo·provider scope, apex·subdomain·대소문자·trailing-dot와 malicious suffix
  거절, 원래 문자열 반환 및 기존 예외 종류·문구를 바꾸지 않았습니다.
- full confirmation contract owner: `ReservationConfirmationTarget`·`ReservationConfirmationResult`·read-only
  `ReservationConfirmationAdapter`를 기존 enum owner인 `reservations/provider_confirmation/contracts.py`로
  옮겼습니다. production consumer 18개는 canonical owner를 직접 사용하고 `services.py`도 top-level
  confirmation 의존을 제거했습니다. target의 redacted identity·provider·route·aware time·arrival·seat·승객·
  credential 검증과 result의 source·시간대·확정/URL/deadline 조합·자동 재예약 금지 계약은 이전 AST와
  동일합니다. canonical/KORAIL/SRT/policy 우선 import는 중앙 `schemas.py`나 legacy facade를 로딩하지 않습니다.
- compatibility·안전 증거: top-level `reservation_confirmation.py`는 semantic 계약 4개와 URL validator의
  exact alias를 제공하고, 기존 wildcard dependency attribute `StrEnum`을 포함한 공개 표면 15개를 유지합니다.
  canonical round-trip뿐 아니라 이동 전 legacy Target·Result·Outcome protocol-4 golden pickle도 같은 객체로
  복원됩니다. legacy facade의 validator attribute 재할당은 canonical Result 내부에 전달되지 않으며 공식
  allowlist를 우회하지 못하는 fail-closed import-only 계약으로 고정했습니다. 독립 리뷰에서 처음 빠졌던
  `StrEnum` P2와 pickle·monkeypatch·dynamic import·scanner P3를 보강한 뒤 남은 P0~P3는 없습니다.
- 구조 재유입 차단: policy·contracts exact import allowlist, `provider_registry`·`reservations`·
  `provider_confirmation` passive package, 중앙 schema exact alias, 18개 contract consumer와 4개 policy consumer를
  AST로 고정했습니다. legacy confirmation의 direct·wildcard·module·package attribute·`importlib` 접근과 중앙
  schema URL policy의 direct·module·package attribute 재진입을 production 전체에서 차단합니다. owner·전체
  module boundary focused pytest 430건과 기존 schema·confirmation·provider·reconciliation 회귀 274건을
  통과했습니다.
- 회귀·품질: 이전 GUI Compose API image와 현재 로컬 `app.openapi()`를 정규화해 SHA-256이 동일하고 paths
  35개·component schemas 69개가 같음을 확인했습니다. 현재 API 164개 테스트 파일을 PostgreSQL 경합 없이
  4개 샤드로 순차 실행해 2,600건 통과·1건 skip을 확인했습니다. Ruff `E/F/I`, format ratchet legacy 40개,
  strict mypy 96개 파일과 `uv lock --check`도 통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` 전체 image를 다시 build하고 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image의 canonical-first import에서 중앙 schema와 legacy facade 미로딩, confirmation 계약 4개·공식 URL
  정책 6개 identity, legacy 공개 표면 15개와 OpenAPI SHA-256·paths 35개·component schemas 69개를 확인했습니다.
  GUI flag `true`·display `:99`·X display, 내부 API·proxy·noVNC HTTP가 모두 정상이고 최근 로그 699줄의 치명
  표식은 0건입니다. PostgreSQL `alembic check`는 exit 255와 이전과 같은 remove operation 8건만 보고했으며
  이번 confirmation·watch graph·outbox·native enum 관련 새 drift는 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 provider-neutral URL policy·full confirmation contract·facade 경계를,
  `CODE_CONVENTIONS.md`에 하위 정책 선분리와 compatibility seam 규칙·strict 대상 96개를 동기화했습니다. API
  wire·사용자 상태·DB schema·환경변수·운영 절차·안전 의미가 바뀌지 않아 `CHECKLIST.md`에는 순수 구조 이력을
  추가하지 않았습니다. 다음 중앙 schema 슬라이스는 `ObservationErrorCategory`·`SeatObservationRequest`·
  `SeatObservationResult`를 `observations/contracts.py`로 함께 옮겨 `services.py`·`worker.py`와 KORAIL/SRT
  provider consumer의 중앙 schema 의존을 줄입니다.

### 2026-08-07 일흔여섯 번째 구조 슬라이스

- observation contract owner: 중앙 `schemas.py`에 있던 `ObservationErrorCategory`·`SeatObservationRequest`·
  `SeatObservationResult`를 `observations/contracts.py`로 함께 옮겼습니다. 정의 본문 AST는 이동 전과 동일하며
  request의 식별자 trim·route·aware time·구체 seat class·승객 수, result의 source·freshness·오류 category·
  지연 시간과 strict extra 거절을 바꾸지 않았습니다. canonical owner는 `domain`·공통 Pydantic base만 읽고,
  단독 import 시 중앙 `schemas.py`가 로딩되지 않습니다.
- compatibility·소비자 전환: 중앙 schema hub는 세 계약의 same-name exact alias만 남깁니다. 따라서 기존
  `rail_waitlist.schemas` import, 이동 전 Request·Result pickle, `ReservationRequest`의 직접 base와
  `WatchCandidateLatestObservationRead.error_category` annotation은 모두 canonical 객체로 복원됩니다. 관찰
  application 3개, provider 공통 계약·adapter·source, KORAIL/SRT 경계와 `services.py`·`worker.py`를 포함한
  production consumer 17개는 canonical owner를 직접 사용합니다.
- 구조 재유입 차단: owner exact import allowlist, passive `observations` package, 중앙 exact alias·local definition
  금지와 17개 consumer별 direct symbol set을 AST로 고정했습니다. 중앙 schema를 통한 direct·wildcard·module·
  package attribute·`importlib`·builtin `__import__`·`getattr`·alias propagation 접근을 production 전체에서
  차단합니다. 독립 리뷰는 P0~P2가 없었고 처음 발견한 scanner P3를 보강한 뒤 owner·전체 boundary pytest
  408건을 통과했습니다.
- wire·회귀 증거: 재빌드 전 실행 중이던 이전 image와 로컬의 request JSON schema SHA-256
  `18280886a4c9405adc9655885d760d1d286e4fc00f9cf6448e5db56180bb2152`, result SHA-256
  `74ba9139d30530ca93bef20c976df4c3ab3cc52c9bac9254f69d7d841298ad0d`가 각각 동일합니다. API OpenAPI도
  `5940f44b6baa50bcd00f7de035b9c1c5f176fd3f1fe4d7719485a2b2f6fca25e`·paths 35개·schemas 69개,
  SRT sidecar OpenAPI도 `228c2dc75eae0ee602c078943e6ccb18dcf868a83bdcbbe69d707aa310ae3095`·paths
  7개·schemas 35개로 동일합니다. 관련 provider·observation·worker focused pytest 572건을 통과했습니다.
- 전체 품질: 현재 API 165개 테스트 파일을 PostgreSQL 경합 없이 4개 샤드로 순차 실행해 2,643건 통과·
  1건 skip을 확인했습니다. Ruff `E/F/I`, format ratchet legacy 40개, strict mypy 97개 파일과
  `uv lock --check`도 통과했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config·전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image의 canonical-first import에서 중앙 schema 미로딩, 세 contract identity·canonical module,
  `ReservationRequest` base·read model annotation·SRT 중첩 model identity와 두 JSON schema·API/SRT OpenAPI
  해시를 다시 확인했습니다. GUI flag `true`·display `:99`·X display, 내부 API `/healthz`·`/readyz`, proxy
  `/healthz`·`/openapi.json`, SRT `/readyz`, noVNC page가 모두 정상이고 최근 로그 657줄의 치명 표식은 0건입니다.
  PostgreSQL
  `alembic check`는 exit 255와 이전과 같은 removed object 8건만 보고했으며 관찰 계약 관련 새 drift는 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 관찰 입출력 owner와 중앙 compatibility hub 경계를,
  `CODE_CONVENTIONS.md`에 strict 대상 97개를 동기화했습니다. API wire·사용자 상태·DB schema·환경변수·운영
  절차·안전 의미가 바뀌지 않아 `CHECKLIST.md`에는 순수 구조 이력을 추가하지 않았습니다. 다음 중앙 schema
  슬라이스는 관찰 request를 상속하는 `ReservationRequest`와 예약 진행/result 계약을 reservation bounded
  context로 함께 옮겨 `services.py`·provider consumer의 중앙 hub 의존을 더 줄입니다.

### 2026-08-07 일흔일곱 번째 구조 슬라이스

- reservation transport contract owner: 중앙 `schemas.py`의 `ReservationRequest`·
  `ReservationProgressStageName`·`ReservationProgressStage`·`ReservationResult`를
  `reservations/contracts.py`로 함께 옮겼습니다. 네 정의의 AST는 이동 전과 동일하며 request의 관찰 계약 상속·
  identity·aware arrival, stage Literal·aware time, result의 source·credential·결제 기한·공식 handoff·진행 근거
  순서와 temporary hold 의미를 바꾸지 않았습니다. owner는 canonical 관찰 계약, provider-neutral URL 정책과
  공통 Pydantic base만 직접 참조하며 단독 import 시 중앙 `schemas.py`를 로딩하지 않습니다.
- compatibility·소비자 전환: 중앙 schema hub는 네 계약의 same-name exact alias만 남깁니다. 이동 전
  Request·Stage·Result pickle과 Result에 중첩된 Stage도 canonical 객체로 복원되고, 네 progress Literal 값·
  `ReservationRequest`의 canonical 관찰 base·Result의 중첩 annotation identity를 유지합니다. provider 계약·
  adapter, 예약 application, KORAIL/SRT executor와 watch HTTP를 포함한 production consumer 15개는 canonical
  owner를 직접 사용합니다. 중앙 compatibility alias의 URL roots·predicate attribute 재할당은 canonical
  validator에 전달되지 않으며 공식 allowlist를 약화할 수 없는 fail-closed import-only seam으로 고정했습니다.
- 구조 재유입 차단: owner exact import allowlist, passive `reservations` package, 중앙 exact alias·local definition
  금지와 15개 consumer별 direct symbol set을 AST로 고정했습니다. 공용 scanner가 중앙 schema의 direct·wildcard·
  module·package attribute·`importlib`·builtin `__import__`·`getattr`·alias propagation 재유입을 production 전체에서
  차단합니다. 독립 감사와 별도 독립 리뷰 모두 최종 P0~P3가 없었고 owner·전체 boundary·기존 schema pytest
  486건, 실제 consumer 회귀 816건을 통과했습니다.
- wire·전체 회귀: 재빌드 전 이전 image와 로컬의 ReservationRequest JSON schema SHA-256
  `a7e1fc73f390dc757e1b54d52e6eb9c25fd146137350eff0593847651d80f35a`, ProgressStage
  `0baecd0c7d4c2842b48b1cf996d2baa17aba86c430d0710a0e56b3a93560cf13`, Result
  `a70c3bd0909b96d47b17842729da1018ad92cdd1ad5baeb237761f03ffb7cf1b`가 각각 동일합니다. API OpenAPI는
  `5940f44b6baa50bcd00f7de035b9c1c5f176fd3f1fe4d7719485a2b2f6fca25e`·paths 35개·schemas 69개,
  SRT sidecar OpenAPI는 `228c2dc75eae0ee602c078943e6ccb18dcf868a83bdcbbe69d707aa310ae3095`·paths
  7개·schemas 35개로 동일합니다. 현재 API 166개 테스트 파일을 4개 샤드로 순차 실행해 2,694건 통과·
  1건 skip을 확인했습니다.
- 통합 운영 검증: GUI override `experimental-rail` config·전체 image build를 통과한 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image에서 canonical-first 중앙 schema 미로딩, 네 contract identity·canonical module, 관찰 request base·
  progress Literal·Result 중첩 Stage·SRT 중첩 request/result와 URL policy identity, 세 JSON schema와 API/SRT
  OpenAPI 해시를 다시 확인했습니다. GUI flag `true`·display `:99`·X display, 내부 API·SRT readiness, proxy와
  noVNC HTTP가 모두 정상이고 최근 로그 664줄의 치명 표식은 0건입니다. PostgreSQL `alembic check`는 예상된
  exit 255와 기존 removed object 8건만 보고했으며 added/column/type/null/default 또는 예약 계약 관련 새
  drift는 없습니다.
- 품질·후속 범위: Ruff `E/F/I`, format ratchet legacy 40개, strict mypy 98개 파일과 `uv lock --check`를
  통과했습니다. `ARCHITECTURE.md`와 `CODE_CONVENTIONS.md`를 동기화했으며 API wire·사용자 상태·DB schema·
  환경변수·운영 절차·안전 의미가 바뀌지 않아 `CHECKLIST.md`에는 순수 구조 이력을 추가하지 않았습니다. 다음
  중앙 schema 슬라이스는 남은 timetable/seat availability/read model transport를 bounded context별로 다시
  분류한 뒤, 외부 API component를 유지할 수 있는 가장 작은 aggregate부터 진행합니다.

### 2026-08-07 일흔여덟 번째 구조 슬라이스

- timetable·seat availability transport owner: 중앙 `schemas.py`의 `SeatAvailabilityStatus`·
  `SeatAvailability`·`SeatAvailabilityNotObservedReason`·`SeatAvailabilityProvenance`·
  `SeatAvailabilityAction`·`SeatClassAvailability`·`TimetableSeatEvidenceRead`·`TimetableItem`을
  `timetable_management/schemas.py`로 함께 옮겼습니다. 여섯 class 본문 AST와 두 Literal 값은 이동 전과
  동일하며, `TimetableItem.availability` default factory·중첩 좌석 class identity, aware evidence time,
  공식 host·KORAIL 검색 URL, `unknown/not_observed`와 status·provenance 조합의 fail-closed 검증을 바꾸지
  않았습니다.
- compatibility·소비자 전환: 중앙 schema hub는 여덟 계약의 same-name exact alias만 남깁니다. 이동 전
  `TimetableItem`·`TimetableSeatEvidenceRead` pickle과 중첩 provenance·action·좌석 class도 canonical 객체로
  복원됩니다. provider 공통 계약·adapter, KORAIL/SRT source, 시간표 application·cache·evidence와 watch 등록
  정책을 포함한 production consumer 28개는 canonical owner를 직접 사용합니다. 중앙 compatibility module의
  공식 host predicate와 KORAIL 검색 URL validator attribute 재할당은 canonical validator에 전달되지 않아
  공식 URL 검증을 약화시키지 않는 fail-closed import-only seam으로 고정했습니다.
- 구조 재유입 차단: owner exact import allowlist·class body, passive timetable package, 중앙 exact alias·local
  definition 금지와 28개 consumer별 symbol set을 AST로 고정했습니다. 공용 scanner가 중앙 schema의 direct·
  wildcard·module·package attribute·`importlib`·builtin `__import__`·`getattr`·alias propagation 재유입을
  production 전체에서 차단합니다. sibling `browser_companion.schemas`·`official_page_confirmation.schemas`를
  이름만으로 중앙 hub라고 오인하던 기존 coarse rule은 제거하고 exact owner allowlist와 전체 scanner가 실제
  경계를 검사하도록 했습니다. 독립 리뷰의 이 오탐과 KORAIL validator 비전파 증거 지적을 보강한 뒤 focused
  owner·boundary pytest 66건을 통과했습니다.
- wire·전체 회귀: 이동 전후 JSON schema SHA-256은 `SeatAvailability`
  `0f5e1f68db4379e07e18ea3683b3e0a2ca988a09e0b398d2720e49c628c9ba09`, provenance
  `bc3861c4a0370f59224436d6a093aa9a48655b070a6720fee6e8c8637fb7e4ff`, action
  `cd9d4994ce7fa58e6297016ef0fec66b1ee1f53e20bfff40b3986479ef532865`, class availability
  `0ecebb4b97181c81f7cda68b3a559ca46667afcced4562de3564e10eb70e5a57`, evidence read
  `3813275f7384824c6e1291187d873b09a86cc8404b3d370041678d6300d0ded1`, timetable item
  `76e920981a216210e9a480ff15f73400e526070b64d3106152948fac6d4b1fb9`로 각각 동일합니다. API OpenAPI는
  `5940f44b6baa50bcd00f7de035b9c1c5f176fd3f1fe4d7719485a2b2f6fca25e`·paths 35개·schemas 69개,
  SRT sidecar OpenAPI는 `228c2dc75eae0ee602c078943e6ccb18dcf868a83bdcbbe69d707aa310ae3095`·paths
  7개·schemas 35개로 유지됩니다. 현재 API 167개 테스트 파일을 4개 샤드로 순차 실행해 2,760건 통과·
  1건 skip을 확인했습니다.
- 품질·통합 운영: Ruff `E/F/I`, format ratchet legacy 39개, strict mypy 98개 파일과 `uv lock --check`를
  통과했습니다. GUI override `experimental-rail` config·전체 image build 뒤 volume 삭제 없이
  force-recreate했고, 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image에서 canonical-first 중앙 schema 미로딩, 여덟 contract identity·module·default factory와 여섯
  schema·API/SRT OpenAPI 해시를 다시 확인했습니다. GUI flag·display·X display, 내부 API health/readiness·SRT
  readiness, proxy health/OpenAPI와 noVNC HTTP가 모두 정상이고 최근 로그 795줄의 치명 표식은 0건입니다.
  PostgreSQL `alembic check`는 예상된 exit 255와 기존 removed object 8건만 보고했으며 새 shape drift는
  없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 시간표·좌석 transport aggregate owner와 compatibility 경계를,
  `CODE_CONVENTIONS.md`에 default factory·중첩 schema aggregate 이동 규칙을 동기화했습니다. API wire·사용자
  상태·DB schema·환경변수·운영 절차·안전 의미가 바뀌지 않아 `CHECKLIST.md`에는 순수 구조 이력을 추가하지
  않았습니다. 다음 중앙 schema 슬라이스는 watch read aggregate를 `watch_management/schemas.py`로 옮겨
  `TimetableSeatEvidenceRead`를 canonical owner에서 직접 참조하게 합니다.

### 2026-08-07 일흔아홉 번째 구조 슬라이스

- watch read transport owner: 중앙 `schemas.py`의 `WatchCandidateLatestReservationAttemptRead`·
  `WatchCandidateRead`·`WatchCandidateLatestObservationRead`·`WatchRead`를
  `watch_management/schemas.py`로 함께 옮겼습니다. `WatchRead → WatchCandidateRead → 최신 관찰·예약 시도`의
  중첩 identity와 기존 forward reference를 한 owner에 유지하고, 등록 근거는 canonical
  `TimetableSeatEvidenceRead`, 관찰 오류는 canonical `ObservationErrorCategory`를 직접 참조합니다. 운행
  provenance의 모두 있음·모두 없음 분기 뒤 시각 비교에는 strict mypy가 이해할 수 있는 명시적 non-None
  guard를 더했으며 실행 결과와 JSON schema는 이동 전과 같습니다.
- compatibility·소비자 전환: 중앙 schema hub는 네 read 계약의 same-name exact alias만 남깁니다. 이동 전
  중앙 경로의 중첩 `WatchRead` protocol-4 pickle은 후보·최신 관찰·최신 예약 시도까지 모두 canonical 객체로
  복원됩니다. production 직접 소비자는 watch HTTP와 read model 두 곳이며 둘 다 feature-local `WatchRead`를
  사용합니다. canonical-first import에서는 중앙 `schemas.py`가 로딩되지 않고 최초 중첩 validation에서 기존
  Pydantic forward reference가 canonical class로 정상 rebuild됩니다.
- 구조 재유입 차단: 중앙 exact alias·local definition 금지, owner class set, HTTP·read model의 정확한 소비자
  집합을 AST로 고정했습니다. 중앙 schema의 direct·wildcard·module·package attribute·`importlib`·builtin
  `__import__`·`getattr`·alias propagation 재유입을 production 전체에서 검사합니다. canonical
  `timetable_management.schemas`를 이름만으로 중앙 hub라고 오인하던 watch schema coarse rule에서는
  `schemas` 문자열 검사를 제거하고 exact owner·전체 scanner가 실제 경계를 판별하게 했습니다. owner·전체
  module boundary focused pytest 525건과 기존 watch API·application focused pytest 212건을 통과했습니다.
- wire·전체 회귀: JSON schema SHA-256은 latest reservation attempt
  `f3fc3bef358e56d63d164525402fe0711f7eb55456ca01320200859f6069c256`, candidate
  `7bf5c984986dc51395893cd93b7c47bd40f4956d4d1b318504fa6c2c278f8bbd`, latest observation
  `1f7c9505bb3c708c90db32b6a615aa83cc447000e6ee7d00d315963c7a1dc8e1`, watch
  `ded44924eb9637f8b09c3b55783fcef4db5f89b109ea88adbeb85505bd45bd33`로 각각 동일합니다. API OpenAPI는
  `5940f44b6baa50bcd00f7de035b9c1c5f176fd3f1fe4d7719485a2b2f6fca25e`·paths 35개·schemas 69개,
  SRT sidecar OpenAPI는 `228c2dc75eae0ee602c078943e6ccb18dcf868a83bdcbbe69d707aa310ae3095`·paths
  7개·schemas 35개로 유지됩니다. 현재 API 168개 테스트 파일을 4개 샤드로 순차 실행해 2,812건 통과·
  1건 skip을 확인했습니다.
- 품질·통합 운영: Ruff `E/F/I`, format ratchet legacy 39개, strict mypy 98개 파일과 `uv lock --check`를
  통과했습니다. GUI override `experimental-rail` config·전체 image build 뒤 volume 삭제 없이
  force-recreate했고, 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image에서 canonical-first 중앙 schema 미로딩, 네 read identity·중첩 runtime validation과 네 schema·
  API/SRT OpenAPI 해시를 다시 확인했습니다. GUI flag·display·X display, 내부 API health/readiness·SRT
  readiness, proxy health/OpenAPI와 noVNC HTTP가 모두 정상이고 최근 로그 536줄의 치명 표식은 0건입니다.
  PostgreSQL `alembic check`는 예상된 exit 255와 기존 removed object 8건만 보고했으며 새 shape drift는
  없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 watch read aggregate owner와 관찰·시간표 canonical dependency를
  동기화했습니다. wire·사용자 상태·DB schema·환경변수·운영 절차·안전 의미가 바뀌지 않아
  `CHECKLIST.md`에는 순수 구조 이력을 추가하지 않았습니다. 중앙 `schemas.py`에 남은 실제 class는
  `ProviderCapabilities`·`HealthResponse`·`ErrorPolicyResult` 세 개이며, 다음 슬라이스는 provider capability를
  registry-owned contract로 분리하는 것이 가장 작고 안전합니다.

### 2026-08-07 여든 번째 구조 슬라이스

- provider capability contract owner: 중앙 `schemas.py`의 `ProviderCapabilities`를 leaf
  `provider_registry/contracts.py`로 옮겼습니다. owner는 `domain.Provider`와 공통 Pydantic base만 참조하며
  registry application·adapter·설정·FastAPI를 역참조하지 않습니다. 필드 순서, 다섯 필수 bool, `experimental`
  false·`enabled` true·`note` null 기본값과 느슨한 bool coercion을 바꾸지 않았습니다.
- compatibility·소비자 전환: 중앙 `schemas.py`와 top-level `providers.py`는 같은 canonical class의 exact alias만
  남깁니다. 기존 중앙 경로 protocol-4 pickle은 canonical 객체로 복원되고 새 pickle은 owner module을
  사용합니다. provider protocol·approved provider, adapter 7개와 registry application·HTTP 등 production
  소비자 11곳은 canonical leaf를 직접 사용합니다. 사용 흔적이 없던 중앙·providers facade class attribute
  재할당은 production owner에 전달되지 않는 import-only seam으로 고정했습니다.
- 경계·wire 증거: owner exact import/class, passive package, 두 facade alias, 11개 운영 소비자와 central schema의
  direct·wildcard·module·package attribute·`importlib`·builtin `__import__`·`getattr`·alias propagation 재유입을
  AST로 고정했습니다. JSON schema SHA-256은
  `529fc42899fcab38cf8ce6e3757599f2b0a403c2bd2c32f4f1bfcd4782b15dd9`·862 bytes로 동일하고,
  API OpenAPI는 `5940f44b6baa50bcd00f7de035b9c1c5f176fd3f1fe4d7719485a2b2f6fca25e`·35/69,
  SRT는 `228c2dc75eae0ee602c078943e6ccb18dcf868a83bdcbbe69d707aa310ae3095`·7/35로 유지됩니다.

### 2026-08-07 여든한 번째 구조 슬라이스

- reservation attempt production runtime: `reservations/attempt_runtime.py`가 claim과 result application에 필요한
  transition·outbox·payment hold·retry source·seat status·clock·result policy·confirmation dependency를 호출
  시점에 조립합니다. runtime은 `commit`·`rollback`·`refresh`나 FastAPI를 알지 않고 caller-owned transaction을
  그대로 유지합니다. 외부 provider 호출 전 durable claim의 별도 commit과 호출 후 result UoW, account → watch
  → candidate → attempt 잠금 순서를 바꾸지 않았습니다.
- production 전환·compatibility: worker와 watch HTTP는 canonical begin/complete runtime을 직접 사용하고,
  read model은 결과 정책을 `reservations/domain.py`에서 직접 읽습니다. worker는 outbox·retry source·confirmation
  recorder와 `RailProviderAuthStatus`도 각 canonical owner에서 가져와 `services.py` import를 완전히 제거했고,
  사용되지 않던 private `_as_utc`를 삭제했습니다. HTTP는 `ReservationAttemptAlreadyCompleted`와 transition
  rejection만 기존 409/detail로 변환합니다. 중앙 services의 begin/complete wrapper와 호출 시점 monkeypatch
  seam은 외부 import 호환용으로 남지만 production dependency로는 사용하지 않습니다.
- 경계·회귀 증거: runtime dependency factory의 호출 시점 identity, begin/complete 인자·confirmation 전달,
  domain conflict 원형 전파와 transaction primitive 미호출을 테스트했습니다. owner exact import, worker·HTTP의
  exact consumer, read model policy와 worker의 services 전면 금지, legacy services direct·module·dynamic 재유입을
  production 전체에서 고정했습니다. owner·runtime·module boundary focused pytest 547건, 기존 예약·worker·API
  focused 225건을 통과했습니다. 현재 API 170개 테스트 파일을 4개 샤드로 순차 실행해 2,862건 통과·1건 skip을
  확인했습니다.
- 품질·통합 운영: Ruff `E/F/I`, format ratchet legacy 39개, strict mypy 100개 파일과 `uv lock --check`를
  통과했습니다. GUI override `experimental-rail` config·전체 image build 뒤 volume 삭제 없이
  force-recreate했고, 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image에서 capability owner·두 facade identity, runtime dependency와 worker·HTTP·read model identity,
  capability schema·API/SRT OpenAPI 해시를 다시 확인했습니다. GUI flag·display·X display, 내부 API·SRT,
  proxy와 noVNC HTTP가 모두 정상이고 최근 로그 584줄의 치명 표식은 0건입니다. PostgreSQL `alembic check`는
  예상된 exit 255와 기존 removed object 8건만 보고했으며 새 shape drift는 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 capability leaf와 reservation attempt runtime/UoW 경계를,
  `CODE_CONVENTIONS.md`에 canonical runtime·transport error 규칙과 strict 100개 파일을 동기화했습니다. wire·
  사용자 상태·DB schema·환경변수·운영 절차·안전 의미가 바뀌지 않아 `CHECKLIST.md`에는 순수 구조 이력을
  추가하지 않았습니다. 중앙 `schemas.py`의 실제 class는 `HealthResponse`·`ErrorPolicyResult` 두 개만 남았고,
  worker의 중앙 schema·services 의존은 모두 제거됐습니다.

### 2026-08-07 여든두 번째 구조 슬라이스

- watch command production runtime: `watch_management/command_runtime.py`가 create/update application의
  idempotency·outbox·provider URL·설정 gate·채널 및 집중 관찰 검증·dedupe·UTC clock dependency를 호출
  시점마다 새로 조립합니다. provider URL은 실제 사용 시점까지 지연하고 experimental gate도 호출할 때 현재
  settings를 읽습니다. runtime은 FastAPI·Celery·DB session factory를 import하지 않고 transaction primitive를
  호출하지 않으며, create의 replay 우선순위·IntegrityError winner 복구와 update의 row lock·commit·refresh는
  기존 application owner에 그대로 남습니다.
- production 전환·compatibility: watch HTTP는 canonical create/update runtime을 직접 사용합니다. HTTP helper는
  create forbidden 403, create·channel validation 422, 만료 evidence의 기존 구조화 409, 집중 관찰 용량 conflict
  409와 update not-found 404·conflict 409·validation 422를 exact detail로 변환하고, idempotency conflict 409와
  예상 밖 오류 전파도 유지합니다. 중앙 `services.py`의 기존 wrapper·validator와 module-global monkeypatch
  seam은 외부 import 호환용으로 남겼지만 production 전체에서 `services` hub를 직접·module·alias·dynamic
  import로 다시 사용하는 경로는 0개로 고정했습니다.
- 경계·회귀 증거: runtime factory의 현재 global identity와 lazy provider URL·clock, create/update 인자 전달,
  application 오류 원형 전파, HTTP status/detail과 예상 밖 오류 비변환을 테스트했습니다. exact import와 HTTP
  단일 consumer, FastAPI·transaction primitive 금지, watch schema/model canonical owner와 legacy services hub
  우회 접근 차단을 AST로 고정했습니다. focused pytest 657건을 통과했고, 현재 API 171개 테스트 파일을 4개
  샤드로 순차 실행해 2,898건 통과·1건 skip을 확인했습니다.
- 품질·통합 운영: Ruff `E/F/I`, format ratchet legacy 39개, strict mypy 101개 파일과 `uv lock --check`를
  통과했습니다. GUI override `experimental-rail` config·전체 image build 뒤 volume 삭제 없이
  force-recreate했고, 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy입니다.
  API image에서 runtime과 HTTP의 exact identity, raw validator dependency, services 미로딩과 transaction
  primitive 미호출을 다시 확인했습니다. API OpenAPI는
  `5940f44b6baa50bcd00f7de035b9c1c5f176fd3f1fe4d7719485a2b2f6fca25e`·35/69로 유지되고 API·KORAIL·
  SRT readiness, proxy health/OpenAPI, noVNC와 GUI X display가 정상입니다. 최근 로그 849줄의 치명 표식은
  0건이며 PostgreSQL `alembic check`는 기존 removed operation 8건만 보고해 새 shape drift가 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 command runtime과 HTTP 오류 경계를, `CODE_CONVENTIONS.md`에 strict
  101개 파일을 동기화했습니다. wire·사용자 상태·DB schema·환경변수·운영 절차·안전 의미가 바뀌지 않아
  이번 순수 구조 이력은 `CHECKLIST.md`에 추가하지 않았습니다. production graph에서 중앙 `services.py` 소비자는
  0개가 됐고, 중앙 `schemas.py`에는 `HealthResponse`·`ErrorPolicyResult` 두 실제 class가 남았습니다. 다음
  중앙 정리는 production 미사용인 failure classifier/result만 별도 owner로 이동하고 dedupe·cadence를 함께
  끌고 가지 않는 슬라이스로 진행합니다.

### 2026-08-07 여든세 번째 구조 슬라이스

- provider failure policy owner: 중앙 `schemas.py`의 `ErrorPolicyResult`와 top-level `policy.py`의
  `RATE_LIMIT_COOLDOWN`·`BLOCK_COOLDOWN`·`PROTECTION_SIGNALS`·failure 분류·cooldown 계산을
  `watch_management/provider_failure_policy.py`로 함께 옮겼습니다. owner는 `WatchStatus`, 공통 Pydantic base와
  표준 datetime만 참조하고 provider circuit·HTTP·DB·중앙 schema·top-level policy를 역참조하지 않습니다.
  dedupe key와 전역 관찰 cadence는 별도 변경 이유이므로 top-level `policy.py`에 그대로 남겼습니다.
- compatibility·정책 증거: 중앙 `schemas.py`의 model과 top-level `policy.py`의 여섯 심볼은 canonical 객체의
  exact alias입니다. 기존 중앙 class pickle과 top-level function pickle은 canonical 객체로 복원되고, facade
  속성 재할당은 canonical 함수 global을 바꾸지 않는 import-only 경계로 고정했습니다. 429의 1,800초 cooldown,
  보호 신호의 300초·수동 재개·공식 인계, 인증 실패와 미지 오류의 상태·reason, 문자열 정규화와 기존의
  side-effect-only `now` 평가를 바꾸지 않았습니다. 현재 canonical operational consumer는 0개입니다.
- 경계·회귀 증거: 필드 순서·네 required 필드·단일 false 기본값, JSON schema SHA-256
  `d21af6b8f892cebc281fcdc5920ed73782e010daccdcaa3e17a1eec721c54f6c`·723 bytes,
  legacy pickle, 세 import 순서·passive package, exact facade identity와 31개 failure 입력·cooldown 분기를
  고정했습니다. owner leaf import·정의, compatibility consumer와 production consumer 0개, 중앙 schema 및
  top-level moved-symbol의 direct·wildcard·module·alias·`getattr`·`importlib`·`__import__` 재유입을 AST로
  차단했습니다. focused pytest 632건을 통과했고, 현재 API 172개 테스트 파일을 4개 샤드로 순차 실행해
  2,973건 통과·1건 skip을 확인했습니다.
- 품질·통합 운영: Ruff `E/F/I`, format ratchet legacy 39개, strict mypy 102개 파일과 `uv lock --check`를
  통과했습니다. GUI override `experimental-rail` config·전체 image build 뒤 Docker Desktop replacement-name
  경합을 컨테이너·네트워크만 정리하는 `down --remove-orphans`로 복구했고, 여섯 named volume을 보존한 채
  최종 `up -d --force-recreate`를 성공시켰습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기
  서비스 11/11 healthy입니다. API image에서 canonical-first 중앙 hub 미로딩, 두 facade exact identity,
  legacy pickle, schema hash와 중앙 실제 class `HealthResponse` 하나를 다시 확인했습니다. API OpenAPI는
  `5940f44b6baa50bcd00f7de035b9c1c5f176fd3f1fe4d7719485a2b2f6fca25e`·35/69로 유지되고 API·KORAIL·
  SRT readiness, proxy health/OpenAPI, noVNC와 GUI X display가 정상입니다. 최근 로그 993줄의 error marker
  5건은 모두 재생성 중 기존 SSE 연결의 `context canceled`이고 예상 밖 치명 오류는 0건입니다. PostgreSQL
  `alembic check`는 기존 removed operation 8건만 보고해 새 shape drift가 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 provider failure leaf와 비활성 compatibility 경계를,
  `CODE_CONVENTIONS.md`에 strict 102개 파일을 동기화했습니다. wire·사용자 상태·DB schema·환경변수·운영
  절차·안전 의미가 바뀌지 않아 이번 순수 구조 이력은 `CHECKLIST.md`에 추가하지 않았습니다. 중앙
  `schemas.py`의 실제 class는 `HealthResponse` 하나만 남았고, 다음 중앙 schema 슬라이스는 health transport
  owner를 정한 뒤 legacy alias와 OpenAPI를 보존하는 범위로 진행합니다.

### 2026-08-07 여든네 번째 구조 슬라이스

- health transport contract owner: 중앙 `schemas.py`의 마지막 실제 class였던 `HealthResponse`를
  `health/schemas.py`로 행동 그대로 옮겼습니다. owner는 공통 Pydantic base만 참조하며 FastAPI entrypoint·
  중앙 schema hub·설정·DB를 역참조하지 않습니다. `status`·`experimental_rail_enabled`의 필드 순서와 두 필드가
  모두 필수인 계약, 기존 Pydantic 설정을 바꾸지 않았습니다.
- compatibility·소비자 전환: `main.py`의 `/health`는 canonical owner를 직접 사용하고, 중앙 `schemas.py`는
  기존 import·pickle 호환을 위해 같은 class 객체의 exact alias만 제공합니다. canonical-first import에서는
  중앙 `schemas.py`가 로딩되지 않으며 production graph의 canonical 소비자는 `main.py` 한 곳입니다. 이 이동으로
  중앙 `schemas.py`에는 실제 class 선언이 하나도 남지 않아 classless compatibility facade가 됐습니다.
- 경계·회귀 증거: owner class와 import 경계, passive package, canonical·legacy exact identity, 양방향 import
  순서, legacy pickle, JSON schema와 `/health`의 OpenAPI component를 고정했습니다. 중앙 schema의 direct·
  wildcard·module·package attribute·alias·`getattr`·`importlib`·`__import__` 재유입도 AST로 차단했습니다.
  focused pytest 672건과 API 전체 pytest 3,003건 통과·1건 skip, Ruff `E/F/I`, format ratchet legacy 39개,
  strict mypy 103개 파일, `uv lock --check`를 통과했습니다.
- 품질·통합 운영: GUI override `experimental-rail` config·전체 image build 뒤 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy이며
  여섯 named volume을 보존했습니다. API image에서 canonical-first 중앙 hub 미로딩, facade·`main.py` exact
  identity, legacy pickle, class 선언 0개와 schema SHA-256
  `88d6782be9ff2711a6fd7ef9e513dfc47271446cf01a72e17cd5f2c91261b020`·234 bytes를 확인했습니다. API
  OpenAPI는 `5940f44b6baa50bcd00f7de035b9c1c5f176fd3f1fe4d7719485a2b2f6fca25e`·35/69·
  83,000 bytes로 유지되고 API health·ready, KORAIL·SRT readiness, proxy health·OpenAPI와 noVNC HTTP가
  모두 200입니다. KORAIL GUI flag·`:99` display·X server는 정상이고 Chrome headless flag는 없습니다. 최근
  로그 444줄의 치명 표식은 0건이며 PostgreSQL `alembic check`는 기존 removed operation 8건만 보고해 새
  shape drift가 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 health transport owner와 classless 중앙 facade를,
  `CODE_CONVENTIONS.md`에 strict 103개 파일을 동기화했습니다. API wire·사용자 상태·DB schema·환경변수·
  운영 절차·안전 의미가 바뀌지 않아 이번 순수 구조 이력은 `CHECKLIST.md`에 추가하지 않았습니다. 다음 단계는
  중앙 `models.py`의 compatibility facade 완료 상태를 감사하고, 남은 top-level provider 구현을 기능 owner로
  이동할 최소 수직 슬라이스를 선정합니다.

### 2026-08-07 여든다섯 번째 구조 감사

- 중앙 model hub 완료 상태: `models.py`는 11개 canonical model module을 먼저 등록하고 ORM class 23개와
  `utcnow`를 exact alias로 노출하는 38줄 compatibility facade입니다. AST와 격리 import로 local class·
  `Table`·명령형 mapper 정의가 모두 0개이고, `Base.metadata` table 23개·registry mapper 23개·class별 mapper
  1개임을 확인했습니다. 구형 `rail_waitlist.models.<Class>` global과 `utcnow`도 canonical 객체로 복원됩니다.
- 소비자·남은 범위: production `src`의 중앙 import는 전체 metadata 등록용 `main.py` 한 곳이고 Alembic
  `env.py`도 같은 bootstrap 계약을 사용합니다. 다만 PostgreSQL fencing 운영 검증 script 3개는 아직 중앙
  mapper import를 사용하며, 현재 source boundary scanner가 `scripts`와 dynamic import를 포괄하지 않는 공백도
  있습니다. 따라서 중앙 파일의 실제 model 이동은 끝났지만 production-like consumer의 canonical 수렴은 별도
  P2 후속으로 남겼습니다. 이번 감사만으로 코드·wire·DB schema를 바꾸지 않아 `CHECKLIST.md`는 수정하지
  않았습니다.

### 2026-08-07 여든여섯 번째 구조 슬라이스

- KORAIL search bootstrap 책임 분리: top-level 304줄 구현을 세 canonical owner로 이동했습니다. 값 객체
  `KorailStationIdentity`는 runtime을 모르는 `provider_registry/korail_search_contracts.py`, 25-key 공식 검색
  URL 생성·검증은 순수 `provider_registry/korail_search_url_policy.py`, 공식 역 JSON fetch·schema 검증·TTL
  cache·single-flight·HTTP client 수명주기는 `provider_adapters/korail_search_bootstrap.py`가 소유합니다. 처음
  검토한 단일 adapter owner는 timetable transport의 adapter 역의존을 만들고, adapter가 registry policy까지
  참조하는 구조도 기존 contract-leaf 경계를 넘으므로 채택하지 않았습니다.
- compatibility·동작 보존: top-level `korail_search_bootstrap.py`는 세 owner의 지원 심볼 12개를 exact alias로
  조립합니다. 기존 identity·catalog·exception protocol-4 pickle은 각각 canonical 객체로 복원되고 canonical-first
  import는 legacy facade와 중앙 `schemas.py`를 로딩하지 않습니다. HTTPS·정확한 KORAIL host/path·명시적 port·
  userinfo·fragment 금지, 중복 없는 정확히 25개 key와 고정값, ASCII 4자리 역 code·실제 날짜·정시 형식,
  역 roster 250~400개·sentinel·중복 거절, connect 5초/전체 10초·redirect 금지와 성공 결과만 cache하는 계약을
  바꾸지 않았습니다. concurrent cold fetch는 한 번으로 합쳐지고 실패 refresh는 cache하지 않으며 주입한
  HTTP client는 닫지 않는 동작을 회귀 테스트에 추가했습니다.
- 소비자·경계 증거: 브라우저 automation·mode smoke·Pydoll facade/search actor·sidecar runtime·중앙 및
  timetable schema 등 production 7곳은 필요한 contract·policy·adapter owner를 직접 사용합니다. transport→
  adapter, adapter→registry runtime, production→legacy facade 재유입을 AST로 차단하고 세 owner의 정확한 정의·
  import와 canonical consumer 집합을 고정했습니다. owner·transport·boundary focused pytest 661건, 실제 KORAIL
  browser·Pydoll·sidecar·schema focused 274건, API 전체 pytest 3,034건 통과·1건 skip을 확인했습니다.
- 품질·통합 운영: Ruff `E/F/I`, format ratchet legacy 38개, strict mypy 106개 파일과 `uv lock --check`를
  통과했습니다. GUI override `experimental-rail` config·전체 image build 뒤 volume 삭제 없이
  force-recreate했고, 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy이며 여섯
  named volume을 보존했습니다. API image에서 세 owner·facade·중앙/timetable validator exact identity,
  canonical-first hub 미로딩, 세 legacy pickle, 25-key URL SHA-256
  `cabfe813bc94028cdee8eea44349ea7a93d0f3d3df67be72102894b22b779ffc`·437 bytes를 확인했습니다. API
  OpenAPI는 `5940f44b6baa50bcd00f7de035b9c1c5f176fd3f1fe4d7719485a2b2f6fca25e`·35/69·
  83,000 bytes로 유지되고 API health·ready, KORAIL·SRT readiness, proxy health·OpenAPI와 noVNC HTTP가
  모두 200입니다. KORAIL GUI flag·`:99` display·X server는 정상이고 Chrome headless flag는 없습니다. 최근
  로그 511줄의 치명 표식은 0건이며 PostgreSQL `alembic check`는 기존 removed operation 8건만 보고해 새
  shape drift가 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 contract·URL policy·resolver의 세 경계와 top-level facade를,
  `CODE_CONVENTIONS.md`에 strict 106개 파일을 동기화했습니다. API wire·사용자 상태·DB schema·환경변수·운영
  절차·안전 의미가 바뀌지 않아 이번 순수 구조 이력도 `CHECKLIST.md`에 추가하지 않았습니다. 다음 작은 후속은
  중앙 model hub를 사용하는 운영 검증 script 3개와 scripts 포함 재유입 ratchet을 canonical owner로
  수렴시키는 작업입니다.

### 2026-08-07 여든일곱 번째 구조 슬라이스

- 운영 검증 script의 canonical model 수렴: execution lease fencing script는
  `provider_execution/models.py`, observation fencing script는 outbox·circuit·execution lease·watch owner,
  reservation credential fencing script는 outbox·provider account·circuit·watch owner의 mapper를 직접
  사용합니다. 중앙 `models.py` import를 제거했을 뿐 transaction·lock·process 경합·격리 database opt-in과
  acceptance 동작은 바꾸지 않았습니다.
- 중앙 facade aggregate 증거: 새 계약 테스트가 중앙 `models.py`의 local class/function·`Table`·명령형 mapper
  정의 0개, 24개 exact alias, metadata table 23개·registry mapper 23개·class별 mapper 1개를 고정합니다. 구형
  `rail_waitlist.models.<symbol>` pickle global 24개도 모두 canonical class/function 객체로 복원됩니다. scripts
  scanner는 direct·wildcard·module·package attribute·alias·`getattr`·`importlib`·`__import__` 재유입을 차단합니다.
- 검증·통합 운영: central model·boundary·PostgreSQL acceptance focused pytest 633건, 최종 API 전체 pytest
  3,048건 통과·1건 skip을 확인했습니다. Ruff `E/F/I`, format ratchet legacy 38개, strict mypy 106개 파일과
  `uv lock --check`, `git diff --check`를 통과했습니다. GUI override `experimental-rail` config·전체 image build와
  volume 삭제 없는 force-recreate 뒤 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11
  healthy, 여섯 named volume 보존을 확인했습니다. API image에서도 class 정의 0개·table/mapper 23개와 대표
  legacy global identity, API health·ready, KORAIL·SRT readiness, proxy health·OpenAPI·noVNC HTTP가 정상입니다.
  최근 로그 339줄의 치명 표식은 0건이며 `alembic check`는 기존 removed operation 8건만 보고했습니다.
- 문서·후속 범위: `ARCHITECTURE.md`와 `CODE_CONVENTIONS.md`에 중앙 hub의 bootstrap 전용 역할과 scripts
  canonical import 규칙을 동기화했습니다. wire·DB schema·환경변수·운영 절차·안전 의미가 바뀌지 않아
  `CHECKLIST.md`에는 순수 구조 이력을 추가하지 않았습니다. 다음 단계는 남은 top-level KORAIL/SRT/TAGO
  구현을 다시 전수 조사해 compatibility facade가 아닌 실제 구현 중 가장 작은 owner 이동을 고릅니다.

### 2026-08-07 여든여덟 번째 구조 슬라이스

- KORAIL direct-CDP lifecycle owner: top-level `korail_direct_cdp.py`와
  `korail_chromium_launch.py`의 실제 구현을 각각 `korail_sidecar/direct_cdp.py`와
  `korail_sidecar/chromium_launch.py`로 옮겼습니다. browser automation은 canonical owner를 직접 사용하고,
  두 top-level 모듈은 기존 의미 심볼뿐 아니라 dependency attribute·wildcard 표면도 같은 객체로 유지하는
  compatibility facade입니다. 기존 `DirectCdpLaunchError` pickle도 canonical class로 복원됩니다.
- 동작·안전 보존: 새 임시 profile과 loopback CDP, 허용 목록 기반 child process 환경, debugging port 검증,
  정상 종료→terminate→kill 순서와 취소 중 cleanup 완료, 본문 오류 우선순위를 바꾸지 않았습니다.
  `KORAIL_BROWSER_TEST_DISABLE_SANDBOX=true`인 격리 browser-test 환경에서만 `--no-sandbox`를 추가하며,
  logger 이름 `rail_waitlist.korail_direct_cdp`와 기존 환경변수 의미도 그대로입니다. process 조기 종료,
  non-positive startup timeout, CDP 연결 실패 뒤 process/profile 정리와 cleanup 단독 실패 회귀를 추가했습니다.
- 경계·품질: canonical owner의 정의·전체 import allowlist와 정확한 production consumer, 두 legacy facade의
  local runtime 정의 0개, exact identity·양방향 import 순서와 production-wide legacy 재유입 차단을
  고정했습니다. 최종 API 전체 pytest는 3,093건 통과·1건 skip이며 Ruff `E/F/I`, format ratchet legacy
  37개, strict mypy 108개 파일, `uv lock --check`와 `git diff --check`를 통과했습니다. 독립 재리뷰에서
  처음 발견한 legacy wildcard 표면 축소와 absolute import 감시 누락을 보완한 뒤 P0~P3 잔여 지적 0건을
  확인했습니다.
- 통합 운영: GUI override `experimental-rail` config와 전체 image build를 통과하고 volume 삭제 없이
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy,
  named volume 6개 보존을 확인했습니다. API health·ready, KORAIL·SRT readiness, proxy health·OpenAPI와
  noVNC HTTP는 모두 200이고 최근 로그의 치명 표식은 0건입니다. PostgreSQL `alembic check`는 기존 removed
  operation 8건만 보고했으며 새 added/new operation은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`에 sidecar subprocess lifecycle owner와 facade 범위를,
  `CODE_CONVENTIONS.md`에 strict 대상 108개를 동기화했습니다. 기능·API·DB schema·환경변수·운영 절차·안전
  의미가 바뀌지 않아 이번 순수 구조 이력은 `CHECKLIST.md`에 추가하지 않았습니다. 다음 production 슬라이스는
  top-level SRT sidecar client를 옮기기 전에 wire contract가 `srt_reservation.py`의 session actor state를
  역참조하는 선행 의존을 분리할지, client/HTTP composition부터 이동할지 별도 contract audit로 정합니다.

### 2026-08-07 여든아홉 번째 구조 슬라이스

- SRT sidecar owner 분리: provider 공통 `ProviderCredentials`·`RailLoginMethod`는
  `provider_account_management/contracts.py`, SRT session actor 상태·snapshot과 wire Pydantic 계약·내부
  HTTP client는 `srt_sidecar/session_contract.py`·`contracts.py`·`client.py`로 옮겼습니다. 서비스는 공통
  port, typed provider 조립, 환경·Redis/live source runtime, FastAPI transport를 `ports.py`·`application.py`·
  `runtime.py`·`http.py`로 나눴습니다. top-level contract/client는 exact alias facade이며 service 모듈은 기존
  Compose `app` entrypoint와 지연 초기화 wrapper만 유지합니다.
- 호환·안전 계약: 기존 공개 surface, `ProviderCredentials`·session actor·client 예외의 구형 pickle 복원,
  Pydantic JSON schema와 7개 route·35개 component OpenAPI fingerprint를 고정했습니다. token 확인, validation
  redaction, `no-store`, login 오류 taxonomy, source drain→Redis close, 환경변수 기본값·범위와 Redis cooldown
  의미는 이동 전과 같습니다. 독립 리뷰에서 확인한 canonical runtime 우회와 legacy runtime·예외 재할당
  회귀는 runtime factory override와 요청 시점 exception dependency로 보정했습니다.
- 집중 검증: contract/client 이동의 SRT 인접 793건, service 경계 119건, 리뷰 보정 뒤 최종 39건을
  통과했습니다. 사용자의 과도한 테스트 방지 요청에 따라 API 전체 pytest는 다시 실행하지 않았습니다.
  Ruff `E/F/I`, format ratchet legacy 37개, strict mypy 116개 파일과 `uv lock --check`, 최종
  `git diff --check`를 확인했습니다.
- 통합 운영: GUI override `experimental-rail` config와 전체 image build를 통과하고 volume 삭제 없이 최종
  force-recreate했습니다. 중간 재생성의 Docker Desktop replacement-name 충돌은 project label과 `Created`
  상태를 확인한 임시 컨테이너 2개만 제거해 복구했으며 named volume 6개는 보존했습니다. 최종 설정 서비스
  13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy이고 API health·ready, KORAIL·SRT
  readiness, proxy health·noVNC는 모두 200입니다. KORAIL GUI flag·`:99` X display와 새 SRT owner 3개·route
  7개의 container identity, 최근 로그 치명 표식 0건을 확인했습니다. `alembic check`는 기존 removed operation
  8건만 보고하고 added/new operation은 없습니다. 외부 철도사 호출은 하지 않았습니다.
- 문서·후속 범위: `ARCHITECTURE.md`와 `CODE_CONVENTIONS.md`를 현재 owner·strict 116개에 맞췄습니다. 기능·
  API·DB schema·환경변수·운영 절차·안전 의미가 바뀌지 않아 `README.md`, `OPERATIONS.md`,
  `POLICY_AND_SAFETY.md`, `CHECKLIST.md`는 수정하지 않았습니다. 후속은 top-level에 실제 구현으로 남은 SRT
  reservation·seat source와 TAGO 경계를 다시 inventory해 가장 작은 수직 슬라이스부터 계속합니다.

### 2026-08-07 아흔 번째 구조 슬라이스

- SRT 예약 actor owner 이동: top-level `srt_reservation.py`의 실제 예약 actor·credential 검증·기본 executor
  구현을 `srt_sidecar/reservation.py`로 옮겼습니다. provider adapter·로그인 검증·sidecar service·worker와
  운영 fencing script는 canonical owner를 직접 사용하고, top-level 모듈은 기존 49개 공개 이름을 동일 객체로
  제공하는 assignment-only compatibility facade로 축소했습니다.
- 호환·안전 계약: actor 상태·session 재사용·NetFunnel 재시도·예약 확인·공식 인계 URL 동작은 바꾸지
  않았습니다. 네 owner 심볼의 canonical `__module__`, 양방향 import 순서, process singleton, 기존
  `rail_waitlist.srt_reservation` 경로로 저장된 class·function pickle 네 개의 복원을 고정했습니다. facade의
  dependency 재할당은 canonical owner에 전파되지 않는 명시적 계약이며 외부 철도사 호출은 하지 않았습니다.
- 집중 검증·리뷰: 새 owner 계약, 기존 SRT 예약, provider adapter singleton, 관련 module boundary를 묶어
  92건 통과했습니다. `uv lock --check`, Ruff `E/F/I`, format ratchet legacy 37개, strict mypy 117개 파일과
  `git diff --check`를 확인했고 독립 리뷰의 P0~P3 잔여 지적은 0건입니다. 과도한 테스트를 피하라는 요청에
  따라 API 전체 pytest는 다시 실행하지 않았습니다.
- 통합 운영: GUI override `experimental-rail` config와 전체 image build를 통과하고 volume 삭제 없이 한 번
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy,
  named volume 6개 보존을 확인했습니다. API·KORAIL·SRT readiness와 proxy health·OpenAPI·noVNC HTTP는
  모두 200이고, KORAIL `GUI_ENABLED=true`·`:99` X display도 정상입니다. API image에서 새 owner·facade·
  service·adapter singleton identity와 production import의 legacy facade 미로딩을 확인했습니다. 최근 로그
  547줄의 치명 표식은 0건이며 `alembic check`는 기존 removed operation 8건만 보고하고 새 added/new
  operation은 없습니다.
- 문서·후속 범위: `ARCHITECTURE.md`와 `CODE_CONVENTIONS.md`에 canonical owner와 strict 117개를
  동기화했습니다. 기능·API·DB schema·환경변수·운영 절차·안전 의미가 바뀌지 않아 `CHECKLIST.md`에는 순수
  구조 이력을 추가하지 않았습니다. 다음 슬라이스는 중앙 `schemas.py`를 아직 사용하는 production 3곳과
  script 2곳을 각 기능 schema owner로 수렴시킵니다.

### 2026-08-07 아흔한 번째 구조 슬라이스

- 중앙 schema 소비자 수렴: `provider_accounts.py`·`provider_runtime.py`·예약 실행 application은 계정 read·
  upsert·auth status를 `provider_account_management/schemas.py`에서 직접 사용합니다. observation fencing
  script는 `observations/contracts.py`와 `provider_registry/contracts.py`, reservation credential fencing
  script는 계정 schema와 `reservations/contracts.py`를 직접 사용합니다. 중앙 `schemas.py`의 classless exact
  alias와 외부 compatibility 표면은 바꾸지 않았습니다.
- 재유입 경계 보강: 중앙 facade의 현재 공개 심볼을 AST에서 구하고 production `src`와 운영 script 모두에서
  direct·wildcard·module/package attribute·alias 전파·`getattr`·`importlib`·`__import__` 접근을 차단합니다.
  독립 감사 두 건이 공통으로 지적한 기존 scripts 미포함 P3를 이 전체 hub gate로 보완했습니다. feature 내부의
  sibling `.schemas` import는 정확한 상대 경로 해석으로 허용합니다.
- 최소 검증: 새 detector 11개 입력, production·script 전체 재유입 gate, 계정 schema compatibility를 합쳐
  14건 통과했습니다. 변경 파일 Ruff `E/F/I`와 저장소 strict mypy 117개 파일을 통과했습니다. standalone
  strict 진단에서 확인된 기존 비대상 타입 부채 29건은 이번 import-only 슬라이스에 섞어 수정하지 않았고,
  요청대로 API 전체 pytest와 PostgreSQL acceptance script는 실행하지 않았습니다.
- 통합 운영: GUI override `experimental-rail` config와 전체 image build를 통과하고 volume 삭제 없이 한 번
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy,
  named volume 6개 보존을 확인했습니다. API·KORAIL·SRT readiness와 proxy health·OpenAPI·noVNC HTTP는
  모두 200이고 최근 로그 357줄의 치명 표식은 0건입니다. API image에서 세 production consumer의
  canonical-first import가 중앙 hub를 로딩하지 않으며 legacy/canonical class identity가 같은 것도 확인했습니다.
- 문서·후속 범위: `ARCHITECTURE.md`와 `CODE_CONVENTIONS.md`에 중앙 schema facade의 외부 호환 전용 역할과
  src·scripts 재유입 금지 규칙을 동기화했습니다. wire·DB schema·환경변수·운영 절차·안전 의미가 바뀌지 않아
  `CHECKLIST.md`에는 순수 구조 이력을 추가하지 않았습니다. 다음 실제 구현 후보는 top-level
  `srt_seat_source.py`를 `provider_adapters/srt_seat_source.py` owner로 이동하는 별도 수직 슬라이스입니다.

### 2026-08-07 아흔두 번째 구조 슬라이스

- SRT seat source owner 이동: top-level 848줄의 accountless 좌석 관찰·공식 시간표 구현을
  `provider_adapters/srt_seat_source.py`로 옮겼습니다. main, SRT execution source runtime, sidecar runtime과
  timetable application은 canonical owner를 직접 사용합니다. top-level 모듈은 기존 public 38개와 private
  13개 attribute를 같은 객체로 제공하는 assignment-only compatibility facade입니다.
- 동작·호환 보존: query 6요소 cache key, 동일 key singleflight, instance-wide provider semaphore, timeout 뒤
  provider thread drain, drain 후 Redis close, 성공 시 failure reset, 보호·429·일반 실패별 cooldown, query마다
  새 accountless client 생성, 공식 station code·NetFunnel cache, fixture 명시 시에만 source 주입하는 계약은
  이동 전과 같습니다. 세 dataclass/exception과 private cache class의 구형 pickle global, canonical
  `__module__`, 다섯 import order, facade dependency 재할당 비전파를 고정했습니다.
- 경계·리뷰: owner 정의 18개와 exact import allowlist, 네 production consumer, legacy facade의 public/private
  전체 재유입 금지, observation·timetable contract 직접 import를 고정했습니다. 독립 리뷰에서 제기된 roster
  예외 정규화는 이번 이동 전 기준에 이미 있던 동작임을 확인했고, 실제 P3인 private facade 재유입 공백은
  public/private 합집합 gate로 보완했습니다. cache·singleflight·cooldown·drain·import cycle·pickle·monkeypatch
  및 type-only `_cast` 보정에서는 추가 회귀를 찾지 못했습니다.
- 최소 검증: SRT seat source 행동 27건, 새 owner/facade 14건과 관련 경계·runtime 조립을 합쳐 109건을
  통과했고, 리뷰 보정 뒤 legacy owner gate 5건만 다시 확인했습니다. `uv lock --check`, 전체 Ruff `E/F/I`,
  format ratchet legacy 37개, strict mypy 118개 파일을 통과했습니다. 요청대로 API 전체 pytest와 외부 SRT
  호출은 실행하지 않았습니다.
- 통합 운영: GUI override `experimental-rail` config·전체 image build를 통과하고 volume 삭제 없이 한 번
  force-recreate했습니다. 설정 서비스 13개, migration·log-init 2개 exit 0, 장기 서비스 11/11 healthy,
  named volume 6개를 보존했습니다. API·KORAIL·SRT readiness와 proxy health·OpenAPI·noVNC는 모두 200이고
  KORAIL GUI flag·`:99` X display도 정상입니다. API image에서 canonical-first production import의 legacy
  facade 미로딩과 대표 public/private 객체 identity를 확인했으며 최근 로그 275줄의 치명 표식은 0건입니다.
- 문서·후속 범위: `ARCHITECTURE.md`와 `CODE_CONVENTIONS.md`를 canonical seat source와 strict 118개에
  맞췄습니다. 기능·API·DB schema·환경변수·운영 절차·안전 의미가 바뀌지 않아 `CHECKLIST.md`에는 순수 구조
  이력을 추가하지 않았습니다. 다음 단계는 `services.py`의 14개 composition/UoW wrapper를 feature runtime과
  HTTP 예외 변환 경계로 나누는 작은 후보부터 다시 감사합니다.

### 2026-08-07 아흔세 번째 구조 슬라이스

- 잔여 service 책임 감사: `services.py`의 451줄·14개 함수에는 직접 commit·rollback·flush·refresh, row lock,
  query 또는 provider 호출 정책이 남아 있지 않았고 production source 소비자도 0곳이었습니다. 운영 검증
  script 두 곳이 쓰는 다섯 legacy 심볼과 FastAPI 예외 변환·호출 시점 module-global 주입 호환을 보존했으며,
  호환 wrapper를 이름만 바꾼 다른 facade로 이동하지 않았습니다.
- due sweep runtime owner: worker에 남아 있던 enablement gate → provider arm 대상 선택 → pipeline dependency
  조립 → pipeline 실행 → 성공 group metric 기록 순서를 `observations/due_runtime.py`로 옮겼습니다. owner는
  Celery·settings·DB·metric·provider registry를 import하지 않고 명시적인 dependency bundle만 받습니다.
  pipeline 또는 앞 단계가 실패하면 같은 예외를 전파하고 metric을 기록하지 않는 기존 계약도 유지합니다.
- worker 호환 경계: `_process_due_watches`는 호출 시점의 module global을 runtime dependency로 조립하는 얇은
  wrapper만 남겼습니다. 따라서 기존 monkeypatch seam, 공개 Celery task 이름, queue routing과 beat schedule은
  바뀌지 않았습니다. due pipeline의 DB UoW·adapter lifecycle·watch 처리 정책도 기존 owner에 남습니다.
- 최소 검증: runtime 성공·gate 실패·pipeline 실패와 worker/Celery 위임·task 계약·import 경계를 합친 고유
  16건을 통과했습니다. AST 호출 순서를 표현하던 테스트의 과도한 가정 1건은 정확한 호출 집합 검증으로
  바로잡은 뒤 해당 노드만 재확인했습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 37개,
  strict mypy 119개 파일을 통과했고 독립 읽기 전용 리뷰의 P0~P3 지적은 0건이었습니다. 요청대로 API 전체
  pytest와 외부 철도 호출은 실행하지 않았습니다.
- 통합 운영: GUI override `experimental-rail` config와 전체 image build를 통과하고 volume 삭제 없이 한 번
  force-recreate했습니다. 설정 서비스 13개 중 migration·log-init 2개는 exit 0, 장기 서비스는 11/11 healthy,
  named volume 6개를 보존했습니다. API·KORAIL·SRT readiness와 proxy health·OpenAPI·noVNC는 모두 200이고,
  KORAIL GUI flag·`:99` X display와 API image의 canonical due runtime 객체 연결도 정상입니다. 최근 Compose
  로그 965줄에서 traceback·fatal·panic·unhandled·critical 표식은 0건이었습니다.
- 문서·후속 범위: `ARCHITECTURE.md`와 `CODE_CONVENTIONS.md`를 새 runtime owner와 strict 119개에 맞췄습니다.
  기능·API·DB schema·환경변수·운영 절차·안전 의미가 바뀌지 않아 이번 슬라이스에서는 `CHECKLIST.md`에 새
  항목을 추가하지 않았습니다. 다음은 services와 worker에 중복된 reconciliation state dependency 조립을
  별도 runtime으로 수렴시킨 뒤, top-level KORAIL Pydoll의 작은 contract leaf 묶음을
  `korail_sidecar/pydoll/` owner로 이동합니다.

### 2026-08-08 아흔네 번째 구조 슬라이스

- reconciliation state runtime owner: transition·outbox·confirmation recorder·UTC 변환의 production 조립과
  transport-free 상태 적용 호출을 `reservations/reconciliation_state_runtime.py`로 옮겼습니다. 명시 dependency가
  있으면 같은 객체를 사용하고 없을 때만 호출 시점의 feature global을 조립합니다. runtime과 기존 state
  application은 commit·rollback·flush·refresh·lock을 소유하거나 예외를 변환하지 않습니다.
- worker·service 호환 경계: worker는 현재 네 module-global을 canonical factory override로 만드는 thin callback을
  reconciliation orchestrator에 주입합니다. `services.py`도 같은 factory에 현재 globals를 넘긴 뒤 기존
  application alias를 호출하므로 양쪽 monkeypatch seam이 유지됩니다. service는 기존과 같이
  `ReservationReconciliationNotEligible`만 동일 detail의 HTTP 409로 변환하고, provider I/O 이후 lease fence와
  최종 commit은 `reconciliation_application.py`에 남습니다.
- 회귀 보정·리뷰: 첫 독립 리뷰에서 정상 production 객체는 같지만 worker-local 네 callback의 호출 시점 교체가
  canonical runtime seam으로 바뀐 P3를 찾았습니다. worker thin callback이 exact dependency bundle을 넘기도록
  보완했고, 재리뷰에서 P0~P3 잔여 지적은 0건이었습니다. 순환 의존, application branch signature, 예외 identity와
  UoW 경계에도 회귀가 없음을 확인했습니다.
- 최소 검증: runtime 현재-global·예외 identity, services 409 wrapper, worker delegate·Celery 계약, 대표 lock-wait
  reconciliation과 module boundary의 고유 7건을 통과했습니다. P3 보정 뒤 직접 영향 노드 4건만 다시 통과했고,
  `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 37개, strict mypy 120개 파일을 통과했습니다. 요청대로
  API 전체 pytest와 외부 철도 호출은 실행하지 않았습니다.
- 통합 운영: GUI override `experimental-rail` config와 전체 image build를 통과하고 volume 삭제 없이
  force-recreate했습니다. 첫 `up`은 Docker daemon이 교체 중인 이전 container ID를 찾지 못해 API·worker류를
  `created`에 남겼지만, live `ps`와 startup 로그에서 migration·log-init exit 0, DB·Redis·KORAIL·Web healthy,
  SRT Uvicorn startup을 확인했습니다. 재빌드·재생성 없이 같은 프로필 `up -d`로 남은 서비스만 시작한 뒤 설정
  서비스 13개, 장기 서비스 11/11 healthy, 완료 서비스 2개 exit 0, named volume 6개를 확인했습니다.
  API·KORAIL·SRT readiness와 proxy health·OpenAPI·noVNC는 모두 200이고, KORAIL GUI flag·`:99` X display와
  API image의 runtime/factory/thin callback 객체 연결도 정상입니다. 최근 Compose 로그 507줄의
  traceback·fatal·panic·unhandled·critical 표식은 0건입니다.
- 문서·후속 범위: `ARCHITECTURE.md`와 `CODE_CONVENTIONS.md`를 새 runtime owner와 strict 120개에 맞췄습니다.
  기능·API·DB schema·환경변수·운영 절차·안전 의미가 바뀌지 않아 이번 슬라이스에서도 `CHECKLIST.md`에 새
  항목을 추가하지 않았습니다. 다음은 top-level KORAIL Pydoll의 작은 contract leaf 묶음을
  `korail_sidecar/pydoll/` owner로 이동합니다.

### 2026-08-08 아흔다섯 번째 구조 슬라이스

- Pydoll contract owner 이동: top-level 인증 38줄, 공용 페이지 49줄, 예약 54줄 구현을 각각
  `korail_sidecar/pydoll/auth_contracts.py`, `page_contracts.py`, `reservation_contracts.py`로 옮겼습니다. 49줄
  계약은 검색뿐 아니라 인증·로그인·page safety·예약에서도 사용하므로 `search_contracts`가 아니라
  `page_contracts`로 명명했습니다. package `__init__`은 re-export 없는 passive namespace입니다.
- canonical 소비 경계: 공용 페이지 9곳, 인증 6곳, 예약 3곳의 production import를 새 owner로 전환했습니다.
  browser도 auth/reservation 타입을 actor 재-export 경유 없이 owner에서 직접 받고, 기존 browser·actor 공개
  alias는 같은 canonical 객체를 계속 노출합니다. owner 간 의존은 reservation → auth 한 방향뿐이고 production
  source의 legacy 세 모듈 재진입은 0곳입니다.
- 호환 facade: top-level 세 파일은 정의 없는 assignment-only facade이며 이동 전 암묵적 public surface인
  page 9개, auth 6개, reservation 12개를 exact alias로 유지합니다. 11개 class/function의 `__module__`은 새
  owner이고, 과거 top-level global을 담은 대표 class·enum·function pickle 6개도 facade를 통해 같은 canonical
  객체로 복원됩니다. `KorailCredentialInput(repr=False)`와 reservation request의 secret field도 유지됩니다.
- 최소 검증·리뷰: 새 owner 테스트와 기존 auth/reservation facade identity, module dependency boundary를 합친
  16건을 통과했습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 37개, strict mypy 120개
  파일을 통과했습니다. 독립 리뷰의 P0~P3 지적은 0건이며, 리뷰 중 제기된 `annotations` attribute 가설은
  Python 3.12에서 세 owner가 동일 `_Feature` binding을 제공한다는 실행 증거로 철회됐습니다. 요청대로 API
  전체 pytest와 큰 Pydoll browser suite, 외부 KORAIL 호출은 실행하지 않았습니다.
- 통합 운영: GUI override `experimental-rail` config·전체 image build·force-recreate를 한 번에 통과했습니다.
  설정 서비스 13개 중 migration·log-init 2개는 exit 0, 장기 서비스는 11/11 healthy, named volume 6개를
  보존했습니다. API·KORAIL·SRT readiness와 proxy health·OpenAPI·noVNC는 모두 200이고 KORAIL GUI flag·
  `:99` X display도 정상입니다. API image에서 browser canonical-first import가 legacy facade를 로딩하지 않고
  page/auth/reservation 대표 객체가 browser·facade와 exact identity임을 확인했습니다. 최근 Compose 로그
  395줄의 traceback·fatal·panic·unhandled·critical 표식은 0건입니다.
- 문서·후속 범위: `ARCHITECTURE.md`와 `CODE_CONVENTIONS.md`를 nested Pydoll owner에 맞췄습니다. 기능·API·
  DB schema·환경변수·운영 절차·안전 의미가 바뀌지 않아 이번 슬라이스에서도 `CHECKLIST.md`에 새 항목을
  추가하지 않았습니다. 다음 작은 실제 구현 후보는 top-level `korail_pydoll_page_safety.py`입니다.

### 2026-08-08 아흔여섯 번째 구조 슬라이스

- Pydoll page-safety owner 이동: top-level 보호 판정 구현을
  `korail_sidecar/pydoll/page_safety.py`로 옮겼습니다. network evidence는 수집 순서대로 각 응답의
  rate-limit → 일반 보호 판정을 적용하고 body evidence를 마지막에 처리합니다. 일반적인 보호 문구는 보호
  surface가 있거나 network evidence가 없을 때만 차단하고, 보호 예외 전에는 정규화된 snapshot만 경고로
  남기는 기존 fail-closed 순서와 sanitization을 그대로 유지했습니다.
- canonical·호환 경계: browser는 nested owner를 직접 사용하는 유일한 production consumer입니다. top-level
  `korail_pydoll_page_safety.py`는 기존 공개 11개와 비공개 `_log_protection_snapshot`을 같은 객체로 노출하는
  assignment-only facade입니다. canonical-first import는 legacy facade에 재진입하지 않고, browser module-global
  guard를 client 생성 뒤 교체하는 기존 monkeypatch seam과 이동 전 두 함수 pickle global 복원도 유지합니다.
- 최소 검증·리뷰: page-safety 행동·순서·owner/facade·import 경계와 browser 호출 시점 seam을 합친 고유 25건을
  통과했습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 36개, strict mypy 120개 파일을
  통과했습니다. 첫 독립 리뷰의 P3 지적은 client 생성 뒤 guard 교체 회귀 테스트가 없다는 점이었고, 해당
  테스트를 추가한 뒤 재리뷰에서 P0~P3 잔여 지적은 0건이었습니다. 동시에 들어온 무관한 예약 재시도·알림
  테스트 두 파일은 내용 변경 없이 Ruff format만 적용하고 이미 형식이 맞아진 stale allowlist 한 줄을
  제거했습니다. 요청대로 API 전체 pytest, 큰 Pydoll browser suite와 외부 KORAIL 호출은 실행하지 않았습니다.
- 통합 운영: GUI override `experimental-rail` config, 전체 image build와 volume 삭제 없는 force-recreate를 한 번
  통과했습니다. 빌드 종료 직후 Docker Desktop pipe preface 경고가 있었지만 명령은 exit 0이었고, 재생성 뒤
  설정 서비스 13개 중 migration·log-init 2개는 exit 0, 장기 서비스는 11/11 healthy, named volume 6개를
  보존했습니다. API·KORAIL·SRT readiness와 proxy health·OpenAPI·noVNC는 모두 200이고, KORAIL GUI flag·
  `:99` X display와 API image의 canonical-first import·browser/facade exact identity도 정상입니다. 최근 Compose
  로그 431줄에서 traceback·fatal·panic·unhandled·critical 표식은 0건이었습니다.
- 문서·후속 범위: `ARCHITECTURE.md`와 `CODE_CONVENTIONS.md`를 canonical page-safety owner와 strict 120개에
  맞췄습니다. 기능·API·DB schema·환경변수·운영 절차·안전 의미가 바뀌지 않아 이번 슬라이스에서도
  `CHECKLIST.md`에 새 항목을 추가하지 않았습니다. 다음 후보는 top-level `korail_seat_source.py`의 provider
  adapter owner 이동 범위와 호환 표면을 먼저 감사합니다.

### 2026-08-08 아흔일곱 번째 구조 슬라이스

- KORAIL accountless seat-source owner 이동: `korail_seat_source.py`의 `korail2` transport, 정규화·좌석 mapping,
  timetable overlay, singleflight·TTL cache·provider gate, cooldown과 provenance/action 조립을 행동 그대로
  `provider_adapters/korail_seat_source.py`로 옮겼습니다. DB·UoW 책임이나 다른 KORAIL browser 구현과의 의존이
  없어 통째 이동했고, 내부 책임 분해나 timeout 정책 변경은 섞지 않았습니다.
- dormant 운영 경계: production 직접 import는 `main.py` 한 곳으로 canonical owner에 수렴하지만 생성된
  `app.state.korail_seat_source`의 production read/call consumer는 0곳입니다. 실제 request-time·background
  KORAIL 조회는 기존 browser source·execution 경계를 계속 사용합니다. 이번 이동에서 dormant source를 새 경로에
  연결하거나 제거하지 않았고, timeout된 `to_thread` 호출의 drain·close 소유권은 재활성화 결정 전에 별도
  보강할 후속 항목으로 남겼습니다.
- exact facade·타입 경계: top-level 파일은 기존 public 34개와 private 6개를 같은 객체로 노출하는
  assignment-only facade입니다. 대표 구형 pickle 5개, owner/legacy/main import order, canonical-first legacy
  비재진입과 `main.KorailLiveSeatSource` identity를 고정했습니다. strict 전환에서 드러난 외부 `korail2`의 stub
  부재만 정확한 `import-untyped`으로 표시하고, `HTTPAdapter.send` override signature, validated URL과 `SeatClass`
  enum 타입을 명시했습니다. provider 호출·예외·cache·cooldown 순서는 바꾸지 않았습니다.
- 최소 검증·품질: 기존 행동과 owner/facade 계약 23건, KORAIL timetable schema 경계 1건, 공용 provider legacy·
  canonical consumer 경계 13건의 고유 37건을 통과했습니다. 타입 보정 뒤 직접 영향 노드 4건만 다시 통과했고,
  `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 35개, strict mypy 121개 파일을 통과했습니다. 독립
  읽기 전용 재검토의 P0~P3 지적은 0건입니다. 요청대로 API 전체 pytest, HTTP·worker·sidecar suite와 외부
  KORAIL 호출은 실행하지 않았습니다.
- 통합 운영: GUI override `experimental-rail` config, 전체 image build와 volume 삭제 없는 force-recreate를 한 번
  통과했습니다. 빌드 종료 직후 Docker Desktop pipe preface 경고가 있었지만 명령은 exit 0이었고, 재생성 뒤
  설정 서비스 13개 중 migration·log-init 2개는 exit 0, 장기 서비스는 11/11 healthy, named volume 6개를
  보존했습니다. API·KORAIL·SRT readiness와 proxy health·OpenAPI·noVNC는 모두 200이고 KORAIL GUI flag·
  `:99` X display도 정상입니다. API image에서 main·seat-source·page-safety canonical-first import가 legacy facade에
  재진입하지 않고 대표 객체가 exact identity임을 확인했습니다. 최근 Compose 로그 408줄에서
  traceback·fatal·panic·unhandled·critical 표식은 0건이었습니다.
- 문서·후속 범위: `ARCHITECTURE.md`와 `CODE_CONVENTIONS.md`에 canonical owner, dormant 경계와 strict 121개를
  동기화했습니다. 기능·API·DB schema·환경변수·운영 절차·좌석 근거가 바뀌지 않아 이번 슬라이스에서도
  `CHECKLIST.md`에 새 항목을 추가하지 않았습니다. 다음은 이 dormant source를 제거할지 재사용할지 결정하고,
  재사용한다면 pending provider thread 수명주기를 먼저 보강합니다. 그 뒤 1,000줄이 넘는
  `korail_browser_seat_source.py`를 transport·policy·runtime 수직 슬라이스로 나눕니다.

### 2026-08-08 아흔여덟 번째 구조 슬라이스

- dead wiring 제거: production read/call consumer가 0곳이던 accountless `KorailLiveSeatSource`의 `main.py`
  import·construction·`app.state` 저장을 제거했습니다. true로 설정해도 효과가 없던
  `korail_seat_status_enabled`, cache TTL, timeout 설정도 함께 제거했습니다. Settings의 `extra="ignore"` 계약으로
  이전 배포의 세 환경변수가 남아 있어도 시작을 막거나 제거된 attribute를 다시 만들지 않습니다. 실제 KORAIL
  request-time·background 조회, browser/SRT가 공유하는 Redis와 rate-limit·protection cooldown은 그대로입니다.
- KORAIL sidecar client owner: `korail_browser_seat_source.py`의 transport protocol, 내부 failure와 189줄 HTTP
  client를 `korail_sidecar/client.py`로 옮겼습니다. 정확한 내부 origin 허용 목록, `follow_redirects=False`,
  `trust_env=False`, credential의 wire-boundary 원문 변환, search·login·reserve·confirmation의 429/403/423
  분류와 session-state non-200 generic failure를 행동 그대로 유지했습니다. source 본체는 1,017줄에서 839줄로
  줄었고 cache·cooldown·login·reservation·observation·timetable 정책은 아직 같은 owner에 남습니다.
- compatibility·의존 경계: 기존 source module은 `BrowserAdapterTransport`, `HttpBrowserAdapterTransport`,
  `_AdapterFailure`를 canonical 객체의 exact alias로 노출하고 wildcard로 보이던 `Protocol`, `urlsplit`, `httpx`
  dependency attribute도 유지합니다. 구형 pickle 3개, owner/legacy import order와 source 생성 시점의
  module-global transport 교체 seam을 고정했습니다. canonical client production consumer는 기존 source 한 곳뿐이고
  accountless canonical source의 production consumer는 0곳입니다.
- 최소 검증·리뷰: 새 client owner·HTTP 정책 12건과 dead wiring·source exception identity·canonical consumer
  경계를 합친 고유 29건을 통과했습니다. 첫 독립 리뷰에서 current architecture 문서 불일치 P2와 잔존 env·
  main-first owner 미로딩·session-state 403/423/500·login credential wire 검증의 P3를 찾았습니다. 문서와 기존
  테스트 노드를 보강한 뒤 직접 영향 6건만 다시 통과했습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format
  ratchet legacy 35개, strict mypy 122개 파일을 통과했습니다. 동시에 들어온 무관한 `test_worker.py` 변경은
  내용 수정 없이 Ruff format만 적용했으며 해당 테스트는 실행하지 않았습니다. 요청대로 API 전체 pytest와 외부
  KORAIL 호출은 실행하지 않았습니다.
- 통합 운영: GUI override `experimental-rail` config, 전체 image build와 volume 삭제 없는 force-recreate를 한 번
  통과했습니다. 빌드 종료 직후 Docker Desktop pipe preface 경고가 있었지만 명령은 exit 0이었고, 재생성 뒤
  설정 서비스 13개 중 migration·log-init 2개는 exit 0, 장기 서비스는 11/11 healthy, named volume 6개를
  보존했습니다. API·KORAIL·SRT readiness와 proxy health·OpenAPI·noVNC는 모두 200이고 KORAIL GUI flag·
  `:99` X display도 정상입니다. API image에서 폐기 state·설정 부재, 실제 browser source 활성, canonical client와
  legacy transport exact identity를 확인했습니다. 최근 Compose 로그 458줄에서
  traceback·fatal·panic·unhandled·critical 표식은 0건이었습니다.
- 문서·후속 범위: `ARCHITECTURE.md`, `CODE_CONVENTIONS.md`와 `CHECKLIST.md`를 production 조립 제거, canonical
  client와 strict 122개에 맞췄습니다. 다음은 `KorailBrowserSeatSource`에 남은 query/provider cooldown·singleflight
  runtime과 timetable projection을 행동 변경 없는 별도 owner로 나누고, 그 뒤 TAGO timetable projection과
  `korail_browser_automation.py`의 contract/protection leaf를 순서대로 분리합니다.

### 2026-08-08 아흔아홉 번째 구조 슬라이스

- KORAIL browser projection owner: `korail_browser_seat_source.py`의 열차번호 정규화, 좌석 등급별 action·공식
  provenance·요금 생성, 기존 시간표 item overlay·`not_observed` 갱신과 성공한 공식 검색 결과의 시간 범위 투영을
  `timetable_management/korail_browser_projection.py`로 옮겼습니다. SRT의 pure timetable projection과 같은
  feature 경계이며 transport·clock·오류·cache·cooldown·singleflight에는 의존하지 않습니다. source 본체는
  839줄에서 725줄로 줄었고 KST picker 시작 시각과 요청·실패 상태 전이는 그대로 남습니다.
- compatibility·정책 보존: 기존 `_normalize_train_number`, `_seat_class`, class static overlay helper 2개와 구형
  pickle 4개를 canonical 객체로 복원합니다. 이동 전 wildcard surface인 검색 URL·browser snapshot·좌석 schema
  6개도 exact alias로 유지합니다. primary timetable과 기존 item overlay는 호출 시점 source-global normalizer·
  seat projector 교체 seam을 보존하며, URL 행동이 없는 `sold_out`은 이동 전처럼 URL을 선검증하지 않습니다.
- 최소 검증·리뷰: 새 owner의 관대한 train identity, inclusive window·URL·요금·action·UTC provenance,
  alias·pickle·wildcard·호출 시점 seam 7건과 기존 primary URL·overlay·KST window·결합 열차번호·불일치
  fail-closed·canonical consumer를 합친 고유 23건을 통과했습니다. 독립 리뷰가 공개 surface 누락 P2와 overlay
  projector·불필요한 URL 선검증 P3를 찾았고, 수정 뒤 직접 영향 9건을 다시 통과한 후 잔여 P0~P3 0건을
  확인했습니다. API 전체 pytest와 외부 KORAIL 재호출은 실행하지 않았습니다.
- 품질·통합 운영: `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 35개, strict mypy 123개 파일을
  통과했습니다. GUI override `experimental-rail` config와 전체 image build, volume 삭제 없는 최종
  force-recreate 뒤 migration·log-init 2개는 exit 0, 장기 서비스는 11/11 healthy입니다. API·KORAIL·SRT
  readiness와 proxy health·OpenAPI·noVNC는 모두 200이고 `DISPLAY=:99`, X display와 canonical projection image
  identity도 확인했습니다. named volume 6개를 보존했고 최근 로그 347줄에서 fatal 표식은 0건, KORAIL Redis
  cooldown은 없었습니다.
- 별도 결함 진단: 화면의 23:00 KORAIL 열차 `다시 등록` 오류는 이번 projection 이동이나 sidecar 장애가
  아닙니다. 같은 시각 KORAIL timetable·seat snapshot·reserve-once·confirmation은 모두 200이었지만
  `POST /api/v1/watches`만 422였습니다. 자정을 넘어 도착하는 후보의 익일 `00:xx` 도착 시각을 웹 payload가
  날짜 없는 `time_to`로 축약해 `time_from=23:00`보다 앞선 것으로 API가 거절하는 기존 경계 결함입니다. 이번
  구조 이동에는 섞어 수정하지 않고 `CHECKLIST.md` 미완료 항목으로 기록했습니다.
- 문서·후속 범위: `ARCHITECTURE.md`, `CODE_CONVENTIONS.md`, `CHECKLIST.md`를 canonical projection owner와 strict
  123개에 맞췄습니다. 다음은 provider adapter의 query/provider cooldown·singleflight runtime을 private seam·
  취소·drain 순서와 함께 분리하고, 이후 TAGO timetable projection을 진행합니다.

### 2026-08-08 백 번째 구조 슬라이스

- KORAIL browser query runtime owner: `korail_browser_seat_source.py`의 process-local TTL cache, query-key
  singleflight, provider semaphore, query-local backoff, provider-wide protection/rate-limit cooldown, inflight cleanup과
  drain을 `provider_adapters/korail_browser_query_runtime.py`의 `KorailBrowserQueryRuntime`으로 옮겼습니다. sidecar
  서버가 아니라 API-side provider 실행 정책이라는 배포·의존 경계를 따르며 transport 생성·종료는 source가 계속
  소유합니다. source 본체는 725줄에서 650줄로 줄었고 login·reservation·observation·timetable 조립은 이동하지
  않았습니다.
- 실행 순서·호환 보존: shared cooldown 조회까지 state lock을 유지하고 cache→query cooldown→provider cooldown→
  inflight 순서를 지킨 뒤 created task를 `asyncio.shield`합니다. load는 provider gate를 얻은 후 현재 transport를
  조회하고 route/date/passenger identity 검증 뒤 cache를 갱신하며, `finally`에서 task identity가 일치할 때만
  inflight를 제거합니다. source wrapper는 `_load`, transport, monotonic, store와 설정을 실제 사용 시점에 다시
  조회합니다. private type 3개·300초 상수·`asyncio`/`dataclass` wildcard, method 4개를 포함한 구형 pickle 7개,
  `_open_cooldown` 호출과 `_query_cooldowns` read seam을 유지했습니다.
- 최소 검증·리뷰: 새 owner의 alias·pickle·owner-first import·reverse dependency·late load/transport lookup·provider
  gate·waiter cancellation/shield·drain·public close 순서 5건과 기존 singleflight/cache, protection cooldown,
  일반 실패 300초 상한, 다른 service date 비오염, legacy generic shared hold 무시, shared protection preflight,
  response identity mismatch·canonical consumer를 합친 고유 22건을 통과했습니다. 독립 실행 순서 리뷰와
  compatibility 리뷰 모두 잔여 P0~P3 0건이었고 신규 테스트 범위도 과도하지 않다고 확인했습니다. API 전체
  pytest와 외부 KORAIL 재호출은 실행하지 않았습니다.
- 품질: `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 35개, strict mypy 124개 파일을 통과했습니다.
- Compose 통합: GUI override의 `experimental-rail` 프로필로 `config --quiet`, 전체 build, `up -d
  --force-recreate`를 실행했습니다. `log-init`·`migration`은 exit 0, 장기 서비스는 11/11 healthy였고 호스트
  `/healthz`·`/openapi.json`·noVNC와 컨테이너 내부 API·KORAIL·SRT readiness가 모두 200이었습니다.
  KORAIL sidecar의 `DISPLAY=:99`와 X 서버, canonical runtime·compatibility alias의 이미지 반영, Compose named
  volume 6개 보존, 최근 치명 로그 0건, KORAIL provider cooldown 부재(`TTL -2`)도 확인했습니다.
- 문서·후속 범위: `ARCHITECTURE.md`, `CODE_CONVENTIONS.md`, `CHECKLIST.md`를 canonical query runtime과 strict
  124개에 맞췄습니다. 다음은 KORAIL source에 남은 login·reservation·observation 정책 중 독립 leaf를 감사하고,
  목표 순서에 따라 TAGO timetable projection 및 `korail_browser_automation.py` contract/protection 분리를
  진행합니다.

### 2026-08-08 백한 번째 구조 슬라이스

- KORAIL sidecar confirmation runtime owner: `korail_browser_seat_source.py`의 예약 확인 wire request 생성,
  sidecar failure·wire 결과의 provider-neutral confirmation 변환을
  `reservations/provider_confirmation/korail_sidecar_runtime.py`의
  `confirm_korail_sidecar_reservation`으로 옮겼습니다. disabled·non-KORAIL은 sidecar를 호출하지 않고,
  protection·rate-limit만 `PROVIDER_BLOCKED`, 일반 failure·잘못된 요청/응답은 `INCONCLUSIVE`로 닫습니다. 정확한
  attempt·candidate·열차·구간·aware datetime·좌석·인원·credential generation을 그대로 전달하며 예약·결제·
  재시도·cooldown·transport 종료는 새 owner가 소유하지 않습니다. source 본체는 650줄에서 607줄로 줄었습니다.
- compatibility·lifecycle 보존: source의 `confirm_reservation` signature·module·qualname과 구형 method pickle을
  thin wrapper로 유지하고, 이동 뒤에도 wildcard surface의 confirmation request/result/outcome은 exact alias입니다.
  source 생성 뒤 canonical runtime 함수, transport, train normalizer와 failure type을 교체해도 호출 시점 값을
  사용하며 fallback clock은 결과마다 한 번만 호출합니다. query drain 뒤 transport close, KST service date,
  search cache·singleflight·cooldown 순서는 이번 owner 이동에서 바꾸지 않았습니다.
- 최소 검증·리뷰: 새 owner의 exact wire·유효 결과, 비활성/non-KORAIL 선차단, protection·rate-limit·일반 failure,
  직접 `ValueError`·request `ValidationError`·invalid wire fail-closed, clock 횟수, legacy wrapper late seam·pickle·
  owner-first import·reverse dependency와 기존 confirmation integration·canonical consumer를 합친 focused 21건을
  통과했습니다. 두 독립 리뷰가 구현 P0~P2 0건과 late seam 테스트 증거 P3를 찾았고, 기존 테스트 두 노드를
  중심으로 관련 테스트를 보강한 뒤 재리뷰에서 잔여 P0~P3 0건을 확인했습니다. API 전체 pytest와 외부 KORAIL
  호출은 실행하지
  않았습니다.
- 품질·통합 운영: `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 35개, strict mypy 125개 파일을
  통과했습니다. GUI override `experimental-rail` 프로필의 `config --quiet`, 전체 image build와 volume 삭제 없는
  `up -d --force-recreate` 뒤 migration·log-init은 exit 0, 장기 서비스는 11/11 healthy였습니다. 호스트
  health·OpenAPI·noVNC와 컨테이너 내부 API·KORAIL·SRT readiness는 모두 200이고 `DISPLAY=:99` X server,
  API·KORAIL image의 canonical owner/wrapper, named volume 6개 보존, 최근 치명 로그 0건, KORAIL Redis cooldown
  부재(`TTL -2`)를 확인했습니다.
- 중앙 허브 완료 감사: 중앙 `schemas.py`는 local 정의 없이 76개 exact alias이고 production consumer가 0곳이며,
  `models.py`도 local 정의 없이 24개 exact alias와 의도적인 main·migration metadata bootstrap만 남았습니다.
  `services.py`의 14개 함수는 production consumer·transaction primitive·직접 SQL 없이 canonical owner를 호출하는
  compatibility wrapper이고, `worker.py`는 직접 transaction/SQL 없이 Celery entrypoint·metric·async isolation·
  dependency 조립만 남은 composition root입니다. 따라서 이 네 파일을 억지로 definition-free로 만들지 않고 현재
  완료 상태를 유지합니다.
- 문서·후속 범위: `ARCHITECTURE.md`, `CODE_CONVENTIONS.md`, `CHECKLIST.md`를 read-only confirmation runtime과
  strict 125개에 맞췄습니다. 다음 수직 슬라이스는 TAGO raw timetable row의 grade·시각·범위·운임·unknown seat
  projection만 `timetable_management` owner로 이동하고 HTTP·pagination·malformed page fail-closed·raw-day
  cache·singleflight는 `TagoClient`에 유지합니다. 그 뒤 top-level KORAIL browser contract/protection leaf를
  진행합니다.

### 2026-08-08 백두 번째 구조 슬라이스

- TAGO timetable projection owner: `provider_adapters/tago.py`의 검증된 raw-day row에 대한 KORAIL/KTX·SRT
  grade 필터, KST 시각 파싱, inclusive 출발 범위, 운임 정규화, `TimetableItem` 생성을
  `timetable_management/tago_timetable_projection.py`의 `project_tago_timetable_rows`로 옮겼습니다. TAGO 시간표를
  좌석 재고로 승격하지 않고 aggregate availability는 `unavailable`, 일반실·특실은
  `unknown/not_observed(source_not_configured)`로 유지합니다. source runtime은 445줄에서 411줄로 줄었고 새 순수
  owner는 90줄입니다.
- parser·runtime·호환 보존: object가 아닌 row가 섞인 page는 projection 전에 page 전체를 거절하며 실패 결과를
  cache하지 않습니다. 검증된 dict 내부의 잘못된 출발·도착 시각만 해당 열차에서 제외하고 잘못된 운임은
  `None`으로 닫습니다. `TagoClient.timetable`은 역·서비스 날짜 검증, HTTP·pagination, raw-day cache·동일 key
  singleflight·shield와 기존 signature·module·qualname·pickle을 계속 소유합니다. top-level `tago.py`의
  `SeatAvailability`·`TimetableItem`·`ZoneInfo`·unknown-seat projector wildcard surface와 호출 시점 owner·timezone·
  keyword-only `reason=` callback seam도 유지했습니다.
- 경계·최소 검증: 새 owner는 bare import 0개와 exact `ImportFrom` allowlist를 사용하며 domain `Provider`와
  feature-local timetable schema만 참조합니다. application·Celery·HTTP·provider runtime·cache·TAGO adapter
  역의존을 금지하고 production consumer는 `provider_adapters/tago.py` 한 곳으로 고정했습니다. invalid departure·
  arrival 행 skip, invalid fare `None`, provider filter·양 끝 포함·unknown provenance, legacy pickle/wildcard·late
  seam·owner-first import와 기존 malformed page 미캐시·정상 retry, pagination, service-date cache 분리,
  KORAIL/SRT singleflight 및 전체 timetable schema consumer/canonical consumer 경계를 합친 focused 52건을
  통과했습니다. API 전체 pytest와 실제 TAGO·철도사 외부 호출은 실행하지 않았습니다.
- 독립 리뷰: 첫 리뷰가 positional reason callback P2와 URL/schema 검증·boundary·direct schema consumer P3를,
  두 번째 리뷰가 exact allowlist의 bare import 공백과 application/cache runtime 금지 범위 P2/P3를 찾았습니다.
  `reason=` Protocol, `TimetableItem.model_validate`, exact import allowlist·bare import 0개, 확장 boundary와 direct
  schema consumer를 보강한 뒤 최종 재리뷰에서 잔여 P0~P3 0건을 확인했습니다.
- 품질·통합 운영: `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 35개, strict mypy 126개 파일을
  통과했습니다. GUI override `experimental-rail` 프로필의 `config --quiet`, 전체 image build와 volume 삭제 없는
  `up -d --force-recreate` 뒤 migration·log-init은 exit 0, 장기 서비스는 11/11 healthy였습니다. 호스트
  health·OpenAPI·noVNC와 컨테이너 내부 API·KORAIL·SRT readiness는 모두 200이고 `DISPLAY=:99` X server,
  API·KORAIL image의 canonical projection/wrapper, named volume 6개 보존, 최근 치명 로그 0건, KORAIL Redis
  cooldown 부재(`TTL -2`)를 확인했습니다.
- 문서·후속 범위: `ARCHITECTURE.md`, `CODE_CONVENTIONS.md`, `CHECKLIST.md`를 TAGO projection의
  pre-projection whole-page fail-closed·시간표/좌석 분리와 strict 126개에 맞췄습니다. 다음은 top-level
  `korail_browser_automation.py`에서 transport와 무관한 browser request/result·protection contract leaf를 먼저
  분리하고 Playwright/CDP/search lifecycle은 별도 슬라이스로 유지합니다.

### 2026-08-08 백세 번째 구조 슬라이스

- KORAIL browser contract·protection owner: top-level `korail_browser_automation.py`의 transport-neutral 요청·
  열차 snapshot·결과 Pydantic 계약, 오류 계층, client protocol을 `korail_sidecar/browser_contracts.py`로,
  HTTP·DOM text 보호 판정을 `korail_sidecar/browser_protection.py`로 옮겼습니다. automation은 1,358줄에서
  1,225줄로 줄었고 새 contract·protection owner는 각각 161줄·90줄입니다. stateful search orchestration,
  Playwright/CDP lifecycle, DOM parser와 공식·fixture URL 상수는 기존 owner에 남겼습니다.
- 소비자·호환 경계: sidecar·Pydoll·HTTP replay·projection·query runtime 등 production consumer는 canonical
  leaf를 직접 사용합니다. top-level automation은 이동한 기존 심볼의 exact alias를 유지해 wildcard surface,
  protocol-0 legacy pickle과 owner-first·legacy-first import 순서를 보존합니다. 두 owner는 내부 함수 import까지
  검사하는 exact allowlist를 사용하고, production·script의 legacy moved-symbol 재진입과 Pydoll page-safety의
  wildcard·extra symbol·alias 변경을 AST 경계로 차단합니다.
- 보호 정책 일치: 독립 실행경계 리뷰에서 HTTP replay의 별도 marker가 `비정상 접근`을 일반 source failure로
  낮추고 business POST 403을 subresource로 기록하던 P1·P2를 확인했습니다. replay 전용 bare error-code 문맥과
  기존 `이용 제한`·`미허가`를 canonical classifier에 합치고, primary POST 403을 `http_403_business`로 구분했습니다.
  legacy trigger는 shared sanitized trigger로 정규화하고 알 수 없는 값은 `marker_abnormal_access`로 닫으므로
  보호 근거는 query-local backoff가 아니라 기존 provider-wide cooldown과 no-retry 경계를 사용합니다.
- 최소 회귀·품질: 이동 직후 contract·protection·page-safety·sidecar failure·module boundary focused 55건을,
  독립 리뷰 보강 뒤 replay status·marker와 exact boundary focused 26건을 통과했습니다. API 전체 pytest와 실제
  KORAIL·TAGO 외부 호출은 실행하지 않았습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy
  33개, strict mypy 128개 파일을 통과했고 두 차례 독립 재리뷰에서 잔여 P0~P3 0건을 확인했습니다. GUI override
  `experimental-rail` 프로필의 `config --quiet`, 전체 image build와 volume 삭제 없는
  `up -d --force-recreate` 뒤 migration·log-init은 exit 0, 장기 서비스는 11/11 healthy였습니다. 호스트
  health·OpenAPI·noVNC와 컨테이너 내부 API·KORAIL·SRT readiness는 모두 200이고 `DISPLAY=:99` X server,
  API·KORAIL image의 canonical contract identity와 replay protection 분류, named volume 6개 보존, 최근 치명
  로그 0건, KORAIL Redis cooldown 부재(`TTL -2`)를 확인했습니다.
- 문서·후속 범위: `ARCHITECTURE.md`, `CODE_CONVENTIONS.md`, `CHECKLIST.md`를 transport-neutral browser leaf,
  replay protection 의미와 strict 128개에 맞췄습니다. 다음 수직 슬라이스는 Pydoll 검색 snapshot의 row 집계·
  expansion 종료 정책을 stateful browser lifecycle에서 먼저 분리하고, Playwright/CDP launch·cleanup은 이후
  별도 슬라이스로 유지합니다.

### 2026-08-08 백네 번째 구조 슬라이스

- Pydoll search snapshot policy owner: `korail_pydoll_browser.py`의 열차 종류·번호·경로 공백 정규화 identity,
  중복 제거, page snapshot 병합과 보호 근거 기반 더보기 중단 함수 4개를
  `korail_sidecar/pydoll/search_snapshot_policy.py`로 옮겼습니다. 새 owner는 61줄이고 browser facade는
  1,842줄에서 1,799줄로 줄었습니다. DOM snapshot 추출·더보기 click·성장 polling·timeout과 actor의 19회 상한·
  전후 response safety guard는 effectful search driver/actor 책임으로 유지했습니다.
- 기존 동작·호환 보존: row identity는 kind·train number·route의 whitespace만 정규화하고 첫 위치를 유지한 채
  같은 identity의 마지막 candidate row로 갱신합니다. candidate body, protection surface stable union, network
  sorted union을 유지하고, network evidence·명시적 marker·행 없는 generic marker·visible generic surface에서만
  확장을 중단합니다. browser의 기존 private 이름 4개는 canonical 함수의 exact alias이며 protocol-0 pickle,
  signature와 session 생성 시점 callback capture를 그대로 보존합니다. search driver의 keyword-only callback과
  생성 뒤 `_snapshot`·`_wait_for_result_growth` monkeypatch 계약도 바꾸지 않았습니다.
- 경계·최소 회귀: owner는 browser protection classifier와 sibling page contract만 참조하고 browser·actor·driver·
  DOM·HTTP·Playwright를 역참조하지 않습니다. canonical production consumer는 browser composition shell 한 곳,
  다른 production·운영 script의 legacy private helper 재진입은 0건으로 고정했습니다. whitespace identity·
  first-position/last-wins·보호 surface/network 병합·generic/non-generic stop, legacy alias/pickle/passive import,
  callback capture와 기존 expand stalled/network 동작을 합친 focused 27건을 최종 기준으로 사용합니다. API 전체
  pytest와 실제 KORAIL 외부 호출은 실행하지 않습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet
  legacy 33개, strict mypy 129개 파일을 통과했고 세 독립 읽기 전용 리뷰에서 이번 이동의 잔여 P0~P3 0건을
  확인했습니다. GUI override `experimental-rail` 프로필의 `config --quiet`, 전체 image build와 volume 삭제 없는
  `up -d --force-recreate` 뒤 migration·log-init은 exit 0, 장기 서비스는 11/11 healthy였습니다. 호스트
  health·OpenAPI·noVNC와 컨테이너 내부 API·KORAIL·SRT readiness는 모두 200이고 `DISPLAY=:99` X server,
  API·KORAIL image의 canonical snapshot policy identity, named volume 6개 보존, 최근 치명 로그 0건, KORAIL
  Redis cooldown 부재(`TTL -2`)를 확인했습니다.
- 문서·후속 범위: `ARCHITECTURE.md`, `CODE_CONVENTIONS.md`, `CHECKLIST.md`를 새 snapshot policy owner와 strict
  129개에 맞췄습니다. 다음 별도 정책 슬라이스에서는 누적 전체 기준의 반복 window·정체 판정으로 불필요한
  더보기 재클릭을 막고, 최신 candidate의 `url`·`title`·`reservation_rows` envelope를 보존하며 expansion stop과
  page-safety가 하나의 순수 차단 판정을 사용하게 합니다. 이 행동 변경을 검증한 뒤 Pydoll Chromium
  launch·cleanup과 session lifecycle 분리를 진행합니다.

### 2026-08-08 백다섯 번째 구조 슬라이스

- 누적 더보기 상태: `search_snapshot_policy.py`가 중복 제거된 초기 snapshot, 지금까지 본 row identity와 window,
  누적 snapshot을 `SearchExpansionState`로 소유합니다. driver는 DOM 조회·click·polling만 수행하고 매번 누적
  identity를 growth 기준으로 넘깁니다. candidate는 반복·정체 여부를 판단하기 전에 반드시 병합하므로
  A→B→A처럼 window가 되돌아와도 두 번째 A의 최신 좌석 행과 보호 근거를 보존한 채 추가 재클릭을 멈춥니다.
  `max_actions <= 0`, 더보기 없음, click 상한과 기존 timeout·session port signature는 그대로입니다.
- snapshot·안전 판정 계약: 병합 기준을 `dataclasses.replace(candidate, ...)`로 바꿔 최신 body·URL·title·
  reservation rows를 한 envelope로 보존하고, row·보호 surface·network evidence만 누적합니다. network evidence는
  CDP 수신부터 snapshot·merge까지 최초 관찰 순서를 유지합니다. `page_safety.py`의 side-effect 없는
  `classify_pydoll_page_block`이 rate-limit, main-document 403, non-generic marker, generic surface/no-row를 한 번만
  판정하고 assertion은 기존 sanitized 로그·typed 예외·stage로 변환하며 expansion stop은 같은 결과만 읽습니다.
  polling은 deadline 직전에 읽은 마지막 snapshot도 신규 행·차단 근거부터 판정한 뒤 종료합니다.
- 호환·회귀: browser private helper 4개는 canonical exact alias이고 protocol-0 pickle, constructor-time callback,
  `expand_results`·`_wait_for_result_growth` signature와 actor의 `expand_results` guard stage를 유지합니다. 관련
  page-safety·snapshot policy·driver·browser·정확한 module boundary focused 54건과 마지막 driver 재확인 10건을
  통과했고 세 독립 읽기 전용 리뷰의 최종 잔여 P0~P3는 0건입니다. 참고로 전체 module-boundary 파일을 추가로
  실행했을 때 이번 변경 노드는 통과했지만, 현재 병행 구조 변경의 self-alias·consumer 기대값 5건은 기존
  불일치로 남아 이번 완료 수치에 포함하지 않았습니다. API 전체 pytest와 실제 KORAIL 외부 호출은 실행하지
  않았습니다.
- 품질·통합 운영: `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 33개, strict mypy 129개 파일과
  `git diff --check`를 통과했습니다. GUI override `experimental-rail`의 `config --quiet`, 전체 image build와 volume
  삭제 없는 `up -d --force-recreate` 뒤 migration·log-init 2개는 exit 0, 장기 서비스는 11/11 healthy입니다.
  호스트 health·OpenAPI·noVNC와 컨테이너 내부 API·KORAIL·SRT readiness는 모두 200이고 `DISPLAY=:99` X server,
  image 내부의 network 순서·latest envelope·rate-limit 분류와 private alias identity를 확인했습니다. named volume
  6개를 보존했고 최근 로그 455줄의 치명 표식과 KORAIL cooldown key는 각각 0건입니다.
- 별도 결함: 화면의 23:00 KORAIL `다시 등록` 실패는 이 리팩터링의 adapter 장애가 아닙니다. 같은 시각
  `reserve-once`는 200이었고 `POST /watches`만 익일 00시대 도착을 같은 날짜 `time_to`로 축약해 422였습니다.
  `CHECKLIST.md`의 미완료 자정 교차 payload 항목을 유지하며, 다음 lifecycle 구조 슬라이스와 섞지 않고 별도
  수직 슬라이스에서 고칩니다.

### 2026-08-08 백여섯 번째 구조 슬라이스

- Pydoll Chromium lifecycle owner: `korail_pydoll_browser.py`에 있던 optional Pydoll import, Chromium options·
  binary, browser 시작, 현재 tab과 network listener ownership, tab 교체와 close fallback을
  `korail_sidecar/pydoll/chromium_lifecycle.py`로 분리했습니다. 새 owner는 456줄이고 browser composition shell은
  1,799줄에서 1,722줄로 줄었습니다. lifecycle은 `NEW`·`STARTING`·`READY`·`ROTATING`·`CLOSING`·`FAILED`·
  `CLOSED` 상태를 명시하며 첫 tab/listener와 교체 tab/listener가 모두 준비된 뒤에만 binding을 commit합니다.
  enable 또는 callback 부착 실패·취소는 새 tab만 rollback하고 기존 binding을 유지합니다.
- 정리·오류 우선순위: listener 제거와 owner가 켠 network event 해제, retired tab 재회수, browser
  `__aexit__` → `stop` → `close` fallback을 반복 cancellation에도 끝냅니다. 전체 fallback 실패는 handle을 지운
  거짓 `CLOSED`로 만들지 않고 `FAILED`로 보존하며 한 production close 안에서 한 번 더 시도한 뒤에도 실패하면
  sanitized `browser_close`로 표면화합니다. search/auth/reservation actor는 수동 context exit에 현재 exception
  metadata를 넘겨 본문 오류·취소를 보존하고, client close는 search/auth 두 owner 중 하나가 실패해도 둘 다
  정리한 뒤 첫 오류를 다시 전달합니다. probe·launch 실패 경로도 cleanup 오류가 원래 진단을 덮지 않습니다.
- session·검색 호환: 구체 `_PydollSession`, `_PydollSessionContext`, session/context Protocol, 기본 factory와
  DOM actor/driver 조립은 기존 module·세 인자 constructor에 남겼습니다. assignable `_browser`·`_tab`, callback
  id·network ownership과 `_replace_tab`·listener·close private method는 lifecycle delegate로 유지했습니다.
  `_configure_chromium_options`, `_set_chromium_binary`, `_finish_owned_cleanup`, `probe_pydoll_chromium`도 canonical
  객체의 exact alias입니다. 재사용 direct 검색은 fresh tab 교체 뒤 HTTP replay capture offset을 새 tab log
  길이로 다시 잡고 한 번만 navigation해 이전 tab offset으로 첫 business request를 건너뛰지 않습니다.
- import·readiness 경계: owner top-level은 browser contract와 공용 Chromium test-launch 정책만 참조하고
  `Chrome`·`ChromiumOptions`·`NetworkEvent`는 실행 지점에서만 lazy import합니다. 기존 taxonomy대로 기본 runtime
  import 실패는 `browser_import`, 실제 listener import·부착 실패는 `browser_launch`로 닫습니다. readiness는
  외부 navigation·listener 없이 동일 options·browser start·cleanup만 확인하는 launch-only 계약을 유지합니다.
  canonical production consumer는 browser shell과 runtime의 lazy readiness probe 두 곳이며, production·script의
  legacy lifecycle 재진입은 0건입니다.
- 최소 회귀·독립 리뷰: passive import, GUI/headless options·sandbox, enter/start/listener 실패·취소, 원자적 tab
  rollback/commit, 반복 cancellation, FAILED handle·bounded retry, fresh capture rebase, actor primary-error 보존,
  두 owner cleanup과 exact module boundary·기존 driver callback seam을 합친 focused 53건을 통과했고 1건은 환경
  의존 smoke라 skip됐습니다. API 전체 pytest와 실제 KORAIL 외부 요청은 실행하지 않았습니다. 독립 리뷰가
  capture offset, 거짓 CLOSED, rollback phase, actor 예외 가림, sibling cleanup 중단을 순차적으로 찾아 보강했고,
  두 최종 읽기 전용 재리뷰에서 잔여 P0~P3 0건을 확인했습니다.
- 품질·통합 운영: `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 33개, strict mypy 130개 파일을
  통과했습니다. GUI override `experimental-rail`의 `config --quiet`, 전체 image build와 volume 삭제 없는
  `up -d --force-recreate` 뒤 migration·log-init은 exit 0, 장기 서비스는 11/11 healthy였습니다. 호스트 HTTP
  health·OpenAPI와 noVNC, 컨테이너 내부 API·KORAIL·SRT readiness는 모두 200이고 `DISPLAY=:99` X server와
  API·KORAIL image의 lifecycle exact alias 네 개, named volume 6개 보존, 최근 치명 표식 0건을 확인했습니다.
- 문서·후속 범위: `ARCHITECTURE.md`, `CODE_CONVENTIONS.md`, `CHECKLIST.md`를 Pydoll lifecycle owner와 strict
  130개에 맞추고, 이미 완료된 누적 snapshot 개선을 미완료라고 적던 모순도 제거했습니다. 환경변수·readiness
  범위·noVNC 운영 절차는 바뀌지 않아 README와 `OPERATIONS.md`는 수정하지 않았습니다. 다음 구조 슬라이스는
  browser shell에 남은 DOM snapshot/evaluation 책임과 다른 top-level provider 잔여를 먼저 감사합니다. 자정
  통과 watch 등록 422는 기존 미완료 항목대로 별도 행동 변경 슬라이스에서 처리합니다.

### 2026-08-08 백일곱 번째 구조 슬라이스

- Pydoll confirmation reader owner: top-level `korail_pydoll_confirmation_reader.py`의 인증된 같은 세션 상세→
  공식 예약 목록 fallback, exact identity·결제 대기 표식·인증·보호 응답 fail-closed 판독 370줄을
  `korail_sidecar/pydoll/confirmation_reader.py`로 행동 변경 없이 이동했습니다. 이동 전후 class/function 17개의
  AST 본문은 동일하며, browser lifecycle·credential lock·예약·취소·결제 동작은 새 owner에 들어가지 않습니다.
  `korail_pydoll_browser.py`는 canonical reader와 결제기한 parser를 직접 import하고 기존 module-global
  monkeypatch seam을 유지합니다.
- 호환·의존 경계: top-level 파일은 기존 공개 22개와 private 13개를 같은 객체로 노출하는 41줄 assignment-only
  facade가 됐습니다. wildcard 표면, canonical `__module__`, legacy Protocol/public reader/private parser pickle
  global과 canonical·legacy·browser 우선 import 순서를 보존했습니다. `pydoll/__init__.py`는 passive namespace를
  유지하고 production consumer는 1,722줄 browser composition shell 한 곳뿐입니다. production과 운영 script가
  legacy reader를 direct·module alias·package attribute·dynamic import 형태로 재진입하지 못하도록 기존 공용
  AST detector로 경계를 고정했습니다.
- 최소 회귀·품질: owner/compatibility 12건, 같은 세션 상세·목록 fallback 핵심 행동 6건, canonical contract·
  protection consumer·import-order 경계 28건을 통과했습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet
  legacy 33개, strict mypy 130개 파일도 통과했습니다. API 전체 pytest와 실제 KORAIL 외부 호출은 요청대로
  실행하지 않았습니다. 첫 읽기 전용 리뷰에서 import 문법 일부만 보던 legacy 재진입 검사의 사각지대를 찾아
  공용 detector 기반 production·script 검사로 보강했습니다.
- 통합 운영: GUI override `experimental-rail`의 `config --quiet`, 전체 image build와 volume 삭제 없는
  `up -d --force-recreate`를 통과했습니다. migration·log-init은 exit 0, 장기 서비스는 11/11 healthy이고 호스트
  HTTP health·OpenAPI·noVNC와 컨테이너 내부 API·KORAIL·SRT readiness는 모두 200입니다. KORAIL adapter는
  `KORAIL_BROWSER_GUI_ENABLED=true`, `DISPLAY=:99`, 동작하는 X server를 유지하며 API·adapter image 모두 legacy·
  browser·canonical reader identity가 일치합니다. named volume 6개를 보존했고 최근 치명 로그 표식은 0건입니다.
- 별도 결함: 화면의 23:00 출발·익일 00:07 도착 KORAIL `다시 등록` 실패는 confirmation reader 이동이나
  headless 전환 문제가 아닙니다. 이전 진단대로 `POST /watches`가 익일 도착을 같은 서비스 날짜의 date-less
  `time_to`로 축약해 422가 되는 별도 payload 결함이며 `CHECKLIST.md`의 운영 확인 항목을 미완료로 유지합니다.
  이번 구조 이동에는 API payload 행동 변경을 섞지 않았습니다.

### 2026-08-08 백여덟 번째 구조 슬라이스

- 중앙·provider 잔여 재감사: `services.py` 453줄과 `worker.py` 469줄에는 직접 SQL·provider I/O·commit·rollback·
  flush·refresh·row lock이 0건이며 각각 legacy HTTP 오류 변환/dependency composition과 Celery task/metric/async
  isolation composition root만 남았습니다. 중앙 `schemas.py` 133줄은 선언 0개의 exact schema hub,
  `models.py` 38줄은 선언 0개의 mapper bootstrap/exact hub입니다. SRT top-level은 exact facade 또는 Compose
  entrypoint이고 TAGO 구현은 이미 `provider_adapters` owner에 있으므로 줄 수만 보고 재분해하지 않기로 했습니다.
  다음 실제 잔여는 top-level KORAIL 구현임을 current AST·consumer 감사로 다시 확인했습니다.
- Pydoll HTTP replay manager owner: route별 lease·TTL·최대 검색 횟수·bounded LRU·capture/install/finalize·폐기와
  replay client cleanup 315줄을 `korail_sidecar/pydoll/http_replay.py`로 행동 변경 없이 이동했습니다. manager는
  browser/tab lifecycle과 인증 session state를 모르며, read-only search actor가 capture Protocol과 client
  factory를 주입합니다. browser shell은 route-cache 상수, search actor는 manager/factory를 canonical owner에서
  직접 가져오고 production consumer는 이 두 곳뿐입니다.
- 호환·안전 경계: top-level `korail_pydoll_http_replay.py`는 기존 공개 32개와 private 2개를 같은 객체로
  노출하는 40줄 assignment-only facade가 됐습니다. Protocol 3개·manager·private lease dataclass의 canonical
  `__module__`, wildcard 표면, 구형 pickle global과 canonical·legacy·browser·search actor 우선 import 순서를
  보존했습니다. 운영 로그 집계를 바꾸지 않도록 canonical logger도 기존
  `rail_waitlist.korail_pydoll_http_replay` 이름을 유지합니다. production·script의 direct·wildcard·module alias·
  package attribute·dynamic import legacy 재진입은 공용 AST detector로 차단합니다.
- 최소 회귀·품질: replay route lease·protection mapping·deferred LRU 4건, search actor 3건, exact facade·pickle·
  import-order·consumer와 module boundary를 합친 focused 19건을 통과했습니다. `uv lock --check`, 전체 Ruff
  `E/F/I`, format ratchet legacy 33개와 strict mypy 130개 파일도 통과했습니다. API 전체 pytest와 실제 KORAIL
  외부 호출은 요청대로 실행하지 않았습니다. 두 독립 읽기 전용 리뷰의 current baseline 기준 최종 잔여 P0~P3는
  0건입니다.
- 통합 운영: GUI override `experimental-rail`의 `config --quiet`, 전체 image build와 volume 삭제 없는
  `up -d --force-recreate`를 통과했습니다. migration·log-init은 exit 0, 장기 서비스는 11/11 healthy이고 호스트
  HTTP health·OpenAPI·noVNC와 컨테이너 내부 API·KORAIL·SRT readiness는 모두 200입니다. API·KORAIL image의
  legacy·search actor·canonical manager identity, route-cache 상수와 기존 logger 이름이 일치하고 adapter는
  `KORAIL_BROWSER_GUI_ENABLED=true`, `DISPLAY=:99`, 정상 X server를 유지합니다. named volume 6개를 보존했고 최근
  치명 로그 표식은 0건입니다.
- 문서·후속 범위: `ARCHITECTURE.md`, `CODE_CONVENTIONS.md`, `CHECKLIST.md`를 새 manager owner에 맞췄습니다.
  기능·환경변수·운영 절차는 바뀌지 않아 README와 `OPERATIONS.md`는 수정하지 않았습니다. 다음 KORAIL 구조
  슬라이스는 manager가 아직 참조하는 884줄 core `korail_http_replay.py`와 공용 검색 결과 parser의 owner 방향을
  먼저 감사합니다. 자정 교차 watch 등록 422는 기존 미완료 항목대로 별도 행동 변경 슬라이스에 남깁니다.

### 2026-08-08 백아홉 번째 구조 슬라이스

- 검색 결과 순수 정책 owner: `korail_browser_automation.py`에 함께 있던 공식 열차 종류·단일 성인 운임·지연
  예상·KST 자정 교차 시각·좌석 상태 판독을 78줄 `korail_sidecar/search_result_policy.py`로 이동했습니다.
  Playwright browser shell, Pydoll read-only search actor, core replay와 예약 control은 canonical owner를 직접
  사용합니다. 역·열차번호 정규화와 HTTP structured payload 전용 판독은 실패 계약이 달라 공용화하지 않았고,
  browser shell은 1,178줄로 줄었습니다.
- core HTTP replay owner와 호환: capture·same-origin business URL·multipart route/passenger/date/hour 검증,
  cookie materialize, 요청별 lease, 20페이지·2 MiB 상한, JSON row의 fail-closed 파싱과 typed failure를 886줄
  `korail_sidecar/http_replay.py`가 소유합니다. browser shell·search actor·Pydoll replay manager 세 consumer는
  canonical owner를 직접 사용하고 legacy automation 역의존은 제거했습니다. top-level `korail_http_replay.py`는
  기존 public 60개·private 34개 전부를 같은 객체로 노출하는 98줄 assignment-only facade입니다. 정의 객체의
  canonical `__module__`, 구형 pickle global, 다섯 import 순서와 `httpx`·`httpcore` INFO 로그 억제를 보존했습니다.
- 최소 회귀·품질: core replay·Pydoll manager·browser parser·reservation control과 두 owner/facade·pickle·
  consumer/module boundary를 합친 focused 102건을 통과했습니다. API 전체 pytest와 실제 KORAIL 외부 호출은
  실행하지 않았습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 33개와 strict mypy 132개
  파일도 통과했습니다. 독립 구조 리뷰와 테스트·문서 리뷰에서 발견한 legacy module alias·package attribute·동적
  import 탐지 누락을 공용 AST boundary로 보강한 뒤 최종 P0~P3 잔여는 0건입니다.
- 통합 운영: GUI override `experimental-rail`의 `config --quiet`, 전체 image build와 volume 삭제 없는
  `up -d --force-recreate`를 통과했습니다. migration·log-init은 exit 0, 장기 서비스는 11/11 healthy이고 호스트
  health·OpenAPI·noVNC와 컨테이너 내부 API health/readiness·KORAIL·SRT readiness는 모두 200입니다. API와
  KORAIL image에서 legacy/core·browser policy/canonical identity가 일치하며 adapter는
  `KORAIL_BROWSER_GUI_ENABLED=true`, `DISPLAY=:99`, 정상 X server를 유지합니다. named volume 6개를 보존했고
  최근 로그 758줄의 치명 표식은 0건입니다.
- 문서·후속 범위: `ARCHITECTURE.md`, `CODE_CONVENTIONS.md`, `CHECKLIST.md`를 두 canonical owner와 strict
  132개에 맞췄습니다. 기능·API·환경변수·운영·안전 계약은 바뀌지 않아 README·`OPERATIONS.md`·
  `POLICY_AND_SAFETY.md`는 수정하지 않았습니다. 다음 KORAIL 구조 슬라이스는 1,178줄
  `korail_browser_automation.py`의 Playwright DOM session/transport 책임과 compatibility surface를 먼저
  감사합니다. 자정 교차 watch 등록 422는 기존 미완료 항목대로 별도 행동 변경 슬라이스에 남깁니다.

### 2026-08-08 백열 번째 구조 슬라이스

- browser 검색 owner 분리: singleflight·짧은 cache·browser 직렬 gate·provider 보호/호출 제한 cooldown·질의별
  backoff·shutdown drain을 211줄 `korail_sidecar/search_coordinator.py`로 이동했습니다. 공식 검색 URL 두 개는
  4줄 `korail_sidecar/browser_page_contracts.py`, Playwright direct-CDP session·폼 조작·DOM 결과 판독은 973줄
  `korail_sidecar/playwright/client.py`가 소유합니다. 검색 순수 정책 owner는 표시 열차 종류와 출발 identity
  판독까지 포함해 92줄이 됐고, top-level `korail_browser_automation.py`는 119줄 import-only facade로 줄었습니다.
- 호환·의존 경계: facade는 기존 public 64개·private 4개를 동일 객체로 노출하고, 구형 pickle global 12개,
  wildcard 표면, canonical·legacy·runtime 우선 import 순서와 기존
  `rail_waitlist.korail_browser_automation` logger 이름을 보존합니다. Playwright는 타입 검사 또는 실제 probe/search
  실행 지점에서만 import하고 passive package import에는 optional browser backend가 필요하지 않습니다. production
  consumer는 canonical owner를 직접 사용하며 direct·wildcard·module/package attribute·동적 import 형태의 legacy
  재진입을 공용 AST 경계로 차단합니다.
- 최소 회귀·품질: coordinator·Playwright fixture·검색 정책·URL guard·runtime/direct-CDP/projection·owner/facade·
  module boundary를 합친 focused 72건을 통과했습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet의
  legacy 33개 격리와 strict mypy 136개 파일도 통과했습니다. API 전체 pytest와 실제 KORAIL 외부 요청은 요청대로
  실행하지 않았습니다. 두 독립 읽기 전용 리뷰에서 구조·테스트 범위의 잔여 P1/P2는 0건이었습니다.
- 통합 운영: GUI override `experimental-rail`의 `config --quiet`, 전체 image build와 volume 삭제 없는
  `up -d --force-recreate`를 통과했습니다. migration·log-init은 exit 0, 장기 서비스는 11/11 healthy이고 호스트
  health·OpenAPI·noVNC와 컨테이너 내부 API health/readiness·KORAIL·SRT readiness는 모두 200입니다. adapter는
  `KORAIL_BROWSER_GUI_ENABLED=true`, `DISPLAY=:99`, 정상 X server를 유지하고 facade의 coordinator·Playwright
  client·공용 URL·검색 정책 identity와 public 64개, 기존 logger 이름이 새 image에서도 일치합니다. Compose project
  volume 6개를 보존했고 최근 로그 448줄의 치명 표식은 0건입니다.
- 문서·후속 범위: `ARCHITECTURE.md`, `CODE_CONVENTIONS.md`, `CHECKLIST.md`를 새 owner와 strict 136개에
  맞췄습니다. 기능·API·환경변수·운영·안전 계약은 바뀌지 않아 README·`OPERATIONS.md`·
  `POLICY_AND_SAFETY.md`는 수정하지 않았습니다. 다음 KORAIL 구조 슬라이스는 canonical Playwright client에 아직
  함께 있는 session/lifecycle, 검색 폼 driver, 보호 대기, 결과 reader 책임의 실제 분리 경계를 먼저 감사합니다.
  23:00 출발·익일 00:07 도착 watch 등록 422는 이 구조 이동이나 GUI/headless 문제가 아니라 date-less
  `time_to` payload의 별도 결함이므로 기존 미완료 체크리스트를 유지합니다.

### 2026-08-08 백열한 번째 구조 슬라이스

- Playwright 결과 reader owner: 공식 결과 row의 열차 종류·번호·구간·시간 범위, 일반실/특실 column fallback,
  좌석·운임·지연과 KST snapshot 변환 168줄을 `korail_sidecar/playwright/result_reader.py`로 행동 변경 없이
  이동했습니다. reader는 `_ResultReaderHost` protocol로 현재 인원·출발 입력과 좌석 helper만 받고 client를
  역참조하지 않습니다. Playwright `Page`·`Locator`도 `TYPE_CHECKING`에서만 import해 passive package import에
  optional browser backend를 요구하지 않습니다.
- client·호환 경계: `playwright/client.py`는 973줄에서 856줄로 줄었고 기존 `_read_result`·`_seat_boxes`·
  `_read_seat_status` 메서드는 새 owner를 호출하는 얇은 wrapper로 남겼습니다. 따라서 직접 호출과 class/instance
  monkeypatch seam, `read_result` stage 보강 순서를 유지합니다. `ROUTE_HEADING`과 역·열차번호 normalizer는 client와
  top-level facade가 새 owner의 동일 객체를 exact alias하며, facade public 64개·private 4개, 구형 pickle global
  12개, wildcard·import-order와 기존 logger 이름은 그대로입니다.
- 최소 회귀·품질: owner/facade·검색 정책 consumer·module boundary 20건과 CDP mouse cleanup·공식 fixture 403/429·
  rolling date·보호 surface·submit identity 대표 행동 8건, 합계 focused 28건을 통과했습니다. `uv lock --check`,
  전체 Ruff `E/F/I`, format ratchet legacy 33개와 strict mypy 137개 파일도 통과했습니다. API 전체 pytest와 실제
  KORAIL 외부 호출은 요청대로 실행하지 않았습니다. 두 독립 읽기 전용 리뷰에서 잔여 P1/P2는 0건이었습니다.
- 통합 운영: GUI override `experimental-rail`의 `config --quiet`, 전체 image build와 volume 삭제 없는
  `up -d --force-recreate`를 통과했습니다. migration·log-init은 exit 0, 장기 서비스는 11/11 healthy이고 호스트
  health·OpenAPI·noVNC와 컨테이너 내부 API health/readiness·KORAIL·SRT readiness는 모두 200입니다. adapter는
  GUI mode와 `DISPLAY=:99`, 정상 X server를 유지하며 새 reader와 client wrapper·legacy alias identity가 image
  안에서도 일치합니다. Compose project volume 6개를 보존했고 최근 로그 436줄의 치명 표식은 0건입니다.
- 전체 목표 재감사와 후속 범위: `services.py`·`worker.py`에는 직접 SQL·transaction·row lock이 없고 중앙
  `schemas.py`는 classless compatibility hub, `models.py`는 metadata registry/exact hub입니다. 최상위 SRT는
  Compose entrypoint shell 외 구현 이동이 끝났고 최상위 TAGO 구현도 0개입니다. 반면 최상위 KORAIL은 27개 중
  11개 파일 약 5,972줄에 실제 정의가 남아 있으므로 전체 목표는 계속 진행합니다. 다음 수직 슬라이스는 client의
  약 606줄 검색 form/CDP click 책임을 먼저 옮기고, 이어서 top-level `korail_pydoll_search_driver.py`를 canonical
  Pydoll owner로 이동합니다. 기능·API·환경변수·운영·안전 계약은 바뀌지 않아 README·`OPERATIONS.md`·
  `POLICY_AND_SAFETY.md`는 수정하지 않았고, 자정 교차 watch 등록 422도 별도 행동 결함으로 유지합니다.

### 2026-08-08 백열두 번째 구조 슬라이스

- Playwright 검색 form owner: 역 선택·비동기 유일 결과 대기, submit 전 exact identity 확인, 달력 월·일과 시간
  slider navigation, 실제 CDP mouse press/hold/release·detach의 17개 구현을 727줄
  `korail_sidecar/playwright/search_form.py`로 이동했습니다. `_SearchFormHost` protocol을 통해 기존 client 메서드를
  다시 호출하므로 class/instance monkeypatch와 subclass override seam을 유지하고 client를 역참조하지 않습니다.
  Playwright `Page`·`Locator`·`CDPSession`은 `TYPE_CHECKING`에서만 import합니다.
- client·실패 계약: `playwright/client.py`는 856줄에서 351줄로 줄었고 기존 private 메서드 17개는 같은 서명과
  `staticmethod`를 유지하는 얇은 wrapper입니다. 검색 orchestration의 `submit_search` stage와 `choose_origin`·
  `choose_destination`·`choose_departure` 등 세부 stage, 반복 취소 중 cleanup 완료, body·취소·cleanup 오류 우선순위,
  mouse release 뒤 detach 순서를 바꾸지 않았습니다. form owner는 기존 logger 이름을 독립적으로 얻어 client·
  coordinator·legacy facade와 같은 Logger 객체를 사용합니다.
- 호환·최소 회귀: top-level facade에는 새 내부 심볼을 추가하지 않아 public 64개·private 4개, 구형 pickle global
  12개, wildcard·import-order와 passive optional backend 계약이 그대로입니다. owner/facade·17 owner 함수/17
  wrapper·reader host seam·검색 정책/browser consumer·역의존 경계 23건, CDP cleanup과 form fixture 대표 행동
  11건, monkeypatch된 client orchestration 재호출 1건을 합친 focused 35건을 통과했습니다. API 전체 pytest와
  실제 KORAIL 외부 호출은 요청대로 실행하지 않았습니다.
- 품질·통합 운영: `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 33개와 strict mypy 138개 파일을
  통과했습니다. GUI override `experimental-rail`의 `config --quiet`, 전체 image build와 volume 삭제 없는
  `up -d --force-recreate` 뒤 migration·log-init은 exit 0, 장기 서비스는 11/11 healthy였습니다. 호스트 health·
  OpenAPI·noVNC와 컨테이너 내부 API health/readiness·KORAIL·SRT readiness는 모두 200이고 GUI mode·
  `DISPLAY=:99`·X server, form 함수 17개·client wrapper 17개·logger identity도 새 image에서 일치합니다. Compose
  project volume 6개를 보존했고 최근 로그 410줄의 치명 표식은 0건입니다.
- 문서·후속 범위: `ARCHITECTURE.md`, `CODE_CONVENTIONS.md`, `CHECKLIST.md`를 새 form owner와 strict 138개에
  맞췄습니다. 독립 리뷰에서 host 재디스패치를 직접 실행하는 회귀가 빠진 점을 찾아 대표 orchestration 1건으로
  보강했습니다. 다음 수직 슬라이스는 실제 정의가 남은 최상위 KORAIL 11개 중 production consumer가 명확한
  610줄 `korail_pydoll_search_driver.py`를 `korail_sidecar/pydoll` owner로 이동합니다. 기능·API·환경변수·운영·
  안전 계약은 바뀌지 않아 README·`OPERATIONS.md`·`POLICY_AND_SAFETY.md`는 수정하지 않았고, 자정 교차 watch
  등록 422도 별도 행동 결함으로 유지합니다.

### 2026-08-08 백열세 번째 구조 슬라이스

- Pydoll 검색 DOM owner: 역·날짜·시간 form 조작과 정확한 readback, 단일 submit latch, 결과 polling·보호 판정,
  더보기 19회 상한과 snapshot 병합을 맡던 610줄 구현을 642줄
  `korail_sidecar/pydoll/search_driver.py`로 이동했습니다. protocol/class 6개와 PEP 695 type alias 6개는 이
  canonical module만 정의하며, 기존 stage 이름과 timeout·fail-closed 정책은 바꾸지 않았습니다.
- 조립·호환 계약: `korail_pydoll_browser.py`는 canonical driver를 직접 import하고 `port=self`와 동적 lambda
  callback으로 session 생성 뒤의 class/instance monkeypatch seam을 그대로 해석합니다. top-level
  `korail_pydoll_search_driver.py`는 37줄 definition-free facade이며 기존 공개 29개·private 0개, wildcard와
  구형 pickle global 12개를 canonical 객체 identity로 복원합니다. canonical·legacy·browser 어느 순서로
  import해도 owner는 하나이고 production은 legacy facade에 재진입하지 않습니다.
- 최소 회귀·품질: driver 행동·snapshot 정책과 owner/facade·pickle·import-order·browser consumer·역의존 경계를
  묶은 focused 44건을 통과했습니다. API 전체 pytest와 실제 KORAIL 외부 호출은 요청대로 실행하지 않았습니다.
  `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 33개, strict mypy 139개 파일도 모두 통과했습니다.
- GUI Compose·후속 범위: GUI override `experimental-rail`의 `config --quiet`, 전체 image build와 volume 삭제 없는
  `up -d --force-recreate` 뒤 migration·log-init은 exit 0이고 장기 서비스 11/11이 healthy입니다. 호스트
  health·OpenAPI·noVNC와 컨테이너 내부 API health/readiness·KORAIL·SRT readiness는 모두 200이며 GUI mode,
  `DISPLAY=:99`, X server와 image 내부 29/0 facade·browser canonical identity를 확인했습니다. project volume
  6개를 보존했고 최근 로그 558줄의 치명 표식은 0건입니다. 최상위 KORAIL 실제 정의는 10개 파일 약 5,362줄로
  줄었으며 다음 슬라이스는 production consumer가 browser 하나인 545줄 `korail_pydoll_reservation_driver.py`입니다.
  API·환경변수·운영·안전 계약이 그대로여서 README·`OPERATIONS.md`·`POLICY_AND_SAFETY.md`는 수정하지 않았고,
  자정 교차 watch 등록 422도 별도 행동 결함으로 유지합니다.

### 2026-08-08 백열네 번째 구조 슬라이스

- Pydoll 예약 DOM owner: 정확 열차 row·좌석 등급·단일 action control, 로그인 뒤 선택 보존, 예매 1회 latch와
  보호·동의·결제 대기 terminal 판독의 545줄 구현을 582줄
  `korail_sidecar/pydoll/reservation_driver.py`로 이동했습니다. class/protocol 6개, PEP 695 type alias 2개와
  private helper 4개는 이 canonical module만 정의합니다. 실제 click은 좌석 선택과 예매 요청 두 곳뿐이고
  `결제하기`는 exact detail 화면을 확인하는 read-only marker이므로 자동 결제 금지 계약을 바꾸지 않았습니다.
- 조립·호환 계약: `korail_pydoll_browser.py`는 canonical driver를 직접 import합니다. `port=self`와 세 lambda는
  session 생성 뒤 monkeypatch를 동적으로 해석하고, 기존 bound execute-script·clock·sleep·logger capture는
  그대로입니다. top-level `korail_pydoll_reservation_driver.py`는 46줄 definition-free facade로 기존 public
  34개·private 4개를 같은 객체로 노출하며 legacy `__all__` 없음, 구형 pickle global 12개와
  canonical·legacy·browser import-order, passive optional backend 계약을 보존합니다.
- 최소 회귀·품질: driver seam 4건, 동일 열차·좌석·선택 보존·예매 latch·보호/terminal 대표 행동 13건,
  facade·pickle·import-order·consumer 18건과 contract/module boundary 3건을 합친 focused 38건을 통과했습니다.
  API 전체 pytest와 실제 KORAIL 외부 호출은 요청대로 실행하지 않았습니다. `uv lock --check`, 전체 Ruff
  `E/F/I`, format ratchet legacy 33개, strict mypy 140개 파일도 모두 통과했습니다.
- GUI Compose·후속 범위: GUI override `experimental-rail`의 `config --quiet`, 전체 image build와 volume 삭제 없는
  `up -d --force-recreate` 뒤 migration·log-init은 exit 0이고 장기 서비스 11/11이 healthy입니다. 호스트
  health·OpenAPI·noVNC와 컨테이너 내부 API health/readiness·KORAIL·SRT readiness는 모두 200이며 GUI mode,
  `DISPLAY=:99`, X server와 image 내부 34/4 facade·browser canonical identity를 확인했습니다. project volume
  6개를 보존했고 최근 로그 354줄의 치명 표식은 0건입니다. 최상위 KORAIL 실제 정의는 9개 파일 약 4,817줄로
  줄었으며 다음 슬라이스는 production consumer가 browser 하나인 490줄 `korail_pydoll_search_actor.py`입니다.
  API·환경변수·운영·안전 계약이 그대로여서 README·`OPERATIONS.md`·`POLICY_AND_SAFETY.md`는 수정하지 않았고,
   자정 교차 watch 등록 422도 별도 행동 결함으로 유지합니다.

### 2026-08-08 백열다섯 번째 구조 슬라이스

- Pydoll read-only 검색 actor owner: 검색 lock·persistent session lease, replay-first 선택, direct/UI 경로,
  capture·install·discard·finalize와 warm session 제출 전 1회 cold retry를 맡던 490줄 구현을 533줄
  `korail_sidecar/pydoll/search_actor.py`로 이동했습니다. protocol/dataclass 5개와 runtime Callable alias 3개는 이
  canonical module만 정의하며, 보호·rate-limit·source 실패와 취소 시 fail-closed session 폐기, non-persistent
  context exception metadata와 replay handoff 소유권을 그대로 유지합니다.
- 조립·호환 계약: `korail_pydoll_browser.py`는 canonical actor를 직접 import합니다. top-level
  `korail_pydoll_search_actor.py`는 51줄 definition-free facade이며 기존 public 40개·private 3개, legacy
  `__all__` 없음, 구형 pickle global 5개와 runtime alias 3개를 같은 객체로 복원합니다. canonical·legacy·browser
  import 순서와 optional Pydoll backend의 지연 import 계약도 보존했습니다.
- 최소 회귀·품질: owner/facade·pickle·import-order·consumer 경계 14건, actor와 warm retry·post-submit
  no-retry·replay handoff·반복 취소 cleanup 대표 행동 13건, 관련 HTTP replay·검색 정책·module boundary 7건을
  합친 focused 34건을 통과했습니다. API 전체 pytest와 실제 KORAIL 외부 호출은 요청대로 실행하지 않았습니다.
  `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 33개와 strict mypy 141개 파일도 통과했습니다.
- GUI Compose·후속 범위: GUI override `experimental-rail`의 `config --quiet`, 전체 image build와 volume 삭제 없는
  `up -d --force-recreate`를 통과했습니다. migration·log-init은 exit 0이고 장기 서비스 11/11이 healthy입니다.
  호스트 health·OpenAPI·noVNC와 컨테이너 내부 API health·KORAIL·SRT readiness는 모두 200이며 GUI mode,
  `DISPLAY=:99`, X server와 image 내부 40/3 facade·actor/alias identity·optional backend 지연 import를
  확인했습니다. project volume 6개를 보존했고 최근 로그 943줄의 치명 표식은 0건입니다. 최상위 KORAIL 실제
  정의는 8개 파일 약 4,327줄로 줄었으며 다음 슬라이스는 production consumer가 browser 하나인 374줄
  `korail_pydoll_login_driver.py`입니다. API·환경변수·운영·안전 계약이 그대로여서 README·`OPERATIONS.md`·
  `POLICY_AND_SAFETY.md`는 수정하지 않았고, 자정 교차 watch 등록 422도 별도 행동 결함으로 유지합니다.

### 2026-08-08 백열여섯 번째 구조 슬라이스

- Pydoll 로그인 DOM owner: login navigation, 유일 method tab·active panel 안의 exact identifier/password/submit,
  post-submit header·공식 session 확인과 bounded polling을 맡던 374줄 구현을 402줄
  `korail_sidecar/pydoll/login_driver.py`로 이동했습니다. protocol/class/function 8개와 PEP 695 type alias 4개는
  이 canonical module만 정의합니다. 중복·불일치 control은 credential 입력 전에 fail-closed하고, protection·
  rate-limit·source 오류와 취소는 그대로 전파하며 일반 browser 오류만 secret-free stage로 변환하는 계약을
  유지했습니다.
- 조립·호환 계약: `korail_pydoll_browser.py`는 canonical driver를 직접 import하고 `port=self`와 호출 시점
  callback을 주입해 session 생성 뒤의 instance/class monkeypatch seam을 보존합니다. top-level
  `korail_pydoll_login_driver.py`는 34줄 definition-free facade이며 기존 public 25개·private 1개, legacy
  `__all__` 없음과 구형 pickle global 12개를 같은 객체로 복원합니다. canonical·legacy·browser import 순서와
  optional Pydoll backend의 지연 import도 바뀌지 않았습니다.
- 최소 회귀·품질: owner/facade·pickle·import-order·consumer 18건, 기존 driver seam·기분류 오류 전파 7건, method 선택·유일성·
  in-place 로그인·strict session boolean·보호 판정·safe stage 대표 행동 13건, contract/module boundary 2건을
  합친 focused 40건을 통과했습니다. API 전체 pytest와 실제 KORAIL 외부 호출은 요청대로 실행하지 않았습니다.
  `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 33개와 strict mypy 142개 파일도 통과했습니다.
- GUI Compose·후속 범위: GUI override `experimental-rail`의 `config --quiet`, 전체 image build와 volume 삭제 없는
  `up -d --force-recreate`를 통과했습니다. migration·log-init은 exit 0이고 장기 서비스 11/11이 healthy입니다.
  호스트 health·OpenAPI·noVNC와 컨테이너 내부 API health·KORAIL·SRT readiness는 모두 200이며 GUI mode,
  `DISPLAY=:99`, X server와 image 내부 25/1 facade·driver/attempt/step identity·optional backend 지연 import를
  확인했습니다. project volume 6개를 보존했고 최근 로그 397줄의 치명 표식은 0건입니다. 최상위 KORAIL 실제
  정의는 7개 파일 약 3,953줄로 줄었으며 다음 슬라이스는 production 조립 경계가 명확한 432줄
  `korail_pydoll_auth_actor.py`입니다. API·환경변수·운영·안전 계약은 바뀌지 않아 README·`OPERATIONS.md`·
  `POLICY_AND_SAFETY.md`는 수정하지 않았습니다. 자정 교차 watch 등록 422는 별도 분석에서 create/update 시간창
  계약 불일치까지 확인했지만 이번 구조 이동에는 섞지 않았고 `CHECKLIST.md` 미완료 항목으로 유지합니다.

### 2026-08-08 백열일곱 번째 구조 슬라이스

- Pydoll 인증 session actor owner: credential version·secret-free fingerprint 결합, 인증 상태 전이, auth lock,
  persistent session의 sliding TTL·검색 시작 횟수 상한과 cleanup을 맡던 432줄 구현을 465줄
  `korail_sidecar/pydoll/auth_actor.py`로 이동했습니다. protocol/class/function과 PEP 695 type alias 9개는 이
  canonical module만 정의합니다. active pointer를 cleanup 전에 비우는 소유권, 인증 중 취소의 `STALE` 전이·
  원예외 재전파, 보호·rate-limit의 `BLOCKED` 전이와 non-persistent context exception metadata를 유지했습니다.
- 조립·호환 계약: `korail_pydoll_browser.py`와 `korail_pydoll_reservation_actor.py` 두 production consumer는
  canonical actor를 직접 import하며 actor는 이 조립 모듈을 역참조하지 않습니다. top-level
  `korail_pydoll_auth_actor.py`는 38줄 definition-free facade로 기존 public 30개·private 0개, legacy
  `__all__` 없음과 구형 pickle global 9개, runtime Callable alias 3개를 같은 객체로 복원합니다. canonical·
  legacy·browser·reservation actor import 순서와 optional Pydoll backend의 지연 import도 바뀌지 않았습니다.
- 최소 회귀·품질: owner/facade·pickle·import-order·consumer 경계 20건, auth actor 기본 seam·재사용 횟수 상한·
  인증 중 취소 5건, credential 격리·TTL·상태 전이 대표 행동 7건, contract/module boundary 2건을 합친 focused
  34건을 통과했습니다. API 전체 pytest와 실제 KORAIL 외부 호출은 요청대로 실행하지 않았습니다. `uv lock
  --check`, 전체 Ruff `E/F/I`, format ratchet legacy 33개와 strict mypy 143개 파일도 통과했습니다.
- GUI Compose·후속 범위: GUI override `experimental-rail`의 `config --quiet`, 전체 image build와 volume 삭제 없는
  `up -d --force-recreate`를 통과했습니다. migration·log-init은 exit 0이고 장기 서비스 11/11이 healthy입니다.
  호스트 health·OpenAPI·noVNC와 컨테이너 내부 API health·KORAIL·SRT readiness는 모두 200이며 GUI mode,
  `DISPLAY=:99`, X server와 image 내부 30/0 facade·actor/factory/state/fingerprint identity·optional backend
  지연 import를 확인했습니다. project volume 6개를 보존했고 최근 로그 550줄의 치명 표식은 0건입니다. 최상위
  KORAIL 실제 정의는 6개 파일 약 3,524줄로 줄었으며 다음 작은 owner 후보는 324줄
  `korail_pydoll_reservation_actor.py`입니다. API·환경변수·운영·안전 계약은 바뀌지 않아 README·
  `OPERATIONS.md`·`POLICY_AND_SAFETY.md`는 수정하지 않았습니다. 자정 교차 watch 등록 422는 이번 구조 이동과
  분리해 `CHECKLIST.md` 미완료 항목으로 유지합니다.

### 2026-08-08 백열여덟 번째 구조 슬라이스

- Pydoll 단일 예약 actor owner: public direct URL과 auth lock의 경계, credential 폐기·lease·인증·검색·예약,
  exact form/열차 identity, bounded expansion과 non-persistent context cleanup을 맡던 324줄 구현을 동일한 324줄
  `korail_sidecar/pydoll/reservation_actor.py`로 이동했습니다. Protocol/class/function 6개와 PEP 695 type alias
  6개는 이 canonical module만 정의합니다. 좌석·예약 click 이후 불확실한 결과를 재시도하지 않고, 취소는
  `STALE` 폐기 후 원예외를 전파하며 보호·rate-limit은 `BLOCKED` 폐기 후 정적 결과로 변환하는 계약을 유지했습니다.
- 조립·호환 계약: `korail_pydoll_browser.py`는 actor와 두 identity helper를 canonical owner에서 직접 import하고,
  owner는 browser·예약 DOM driver·검색 actor 같은 상위/동급 조립 모듈을 역참조하지 않고 auth lifecycle owner에
  단방향 의존합니다. top-level `korail_pydoll_reservation_actor.py`는 54줄
  definition-free facade이며 기존 public 37개·private 0개, 좁은 `__all__` 8개와 구형 pickle global 12개를
  같은 객체로 복원합니다. canonical·legacy·browser import 순서와 optional Pydoll backend의 지연 import도
  바뀌지 않았습니다.
- 최소 회귀: owner/facade·pickle·import-order·consumer 경계 19건, 기존 actor seam과 신규 취소 cleanup 4건,
  유일 열차·보호 차단·인증 실패·click 이후 불확실성 대표 행동 4건, auth/driver/contract/module 경계 10건을
  합친 focused 37건을 통과했습니다. API 전체 pytest와 실제 KORAIL 외부 호출은 요청대로 실행하지 않았습니다.
  `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 32개와 strict mypy 144개 파일도 통과했습니다.
- GUI Compose·후속 범위: GUI override `experimental-rail`의 `config --quiet`, 전체 image build와 volume 삭제 없는
  `up -d --force-recreate`를 통과했습니다. migration·log-init은 exit 0이고 장기 서비스 11/11이 healthy입니다.
  호스트 health·OpenAPI·noVNC와 컨테이너 내부 API health·KORAIL·SRT readiness는 모두 200이며 GUI mode,
  `DISPLAY=:99`, X server와 image 내부 37/0 facade·`__all__` 8·actor/helper identity·optional backend 지연
  import를 확인했습니다. project volume 6개를 보존했고 최근 로그 408줄의 치명 표식은 0건입니다. 최상위 KORAIL
  실제 정의는 5개 파일 약 3,200줄로 줄었습니다. 가장 작은 잔여 파일은 69줄
  `korail_browser_adapter_service.py`이지만 composition/entrypoint 역할인지 먼저 판별한 뒤 이동 여부를 정합니다.
  API·환경변수·운영·안전 계약은 바뀌지 않아 README·`OPERATIONS.md`·`POLICY_AND_SAFETY.md`는 수정하지
  않았습니다. 자정 교차 watch 등록 422는 이번 구조 이동과 분리해 `CHECKLIST.md` 미완료 항목으로 유지합니다.

### 2026-08-08 백열아홉 번째 구조 슬라이스

- adapter deployment root 감사: 69줄 `korail_browser_adapter_service.py`는 route·lifespan·bearer·DTO·readiness
  정책을 이미 `korail_sidecar/http.py`와 `runtime.py`에 위임하고, import-time file logging과 호출 시점 dependency
  조립, 전역 ASGI `app`만 소유합니다. Dockerfile의 Uvicorn target도 이 module path를 직접 사용하므로 새 owner와
  facade로 다시 나누지 않고 의도적인 top-level composition root로 유지했습니다.
- 경계 ratchet: 기존 canonical HTTP factory identity·signature·호환 global capture·route owner·runtime 역의존
  8건에 로컬 정의 `create_adapter_app` 하나, 허용 canonical import 집합, 인자 없는 `app = create_adapter_app()` 한
  번과 exact Docker CMD를 검증하는 1건을 추가해 focused 9건을 통과했습니다. entrypoint도 strict mypy 대상에
  추가해 145개 파일 오류 0을 확인했습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 32개도
  통과했습니다.
- 운영·후속 범위: production source·Dockerfile·Compose·runtime image 계약은 바뀌지 않고 테스트·typing ratchet·
  문서만 변경해 Compose 재빌드는 실행하지 않았습니다. 최상위 실제 정의는 5개 파일 약 3,200줄로 유지됩니다.
  253줄 `korail_browser_mode_smoke.py`는 `main` guard와 private capture를 가진 로컬 CLI root이고 production source
  consumer가 없어 우선 이동 후보에서 제외했습니다. 다음 실제 feature owner 후보는 main router와 시간표
  application이 소비하는 549줄 `korail_browser_bridge.py`입니다. README·`OPERATIONS.md`·`POLICY_AND_SAFETY.md`는
  변경할 기능·운영·안전 계약이 없어 수정하지 않았습니다.

### 2026-08-08 백스무 번째 구조 슬라이스

- browser companion HTTP owner: 549줄 `korail_browser_bridge.py`에 함께 있던 admin·public router,
  extension-origin credential 검증, one-time pairing·body-bound challenge, credential budget과 snapshot 저장
  UoW를 434줄 `browser_companion/http.py`로 이동했습니다. challenge를 payload 검증 전에 소모하는 순서,
  conditional update의 동시 제출 1회 계약, pairing·challenge·snapshot freshness와 no-store 응답을 바꾸지
  않았습니다.
- snapshot overlay owner: 최신 exact route·passenger·열차·UTC 초·좌석 class snapshot만 고르고 이미 관측된
  좌석은 보존하는 read-only 합성 정책을 134줄 `browser_companion/snapshot_overlay.py`로 분리했습니다.
  `fresh_until == now` 제외, companion provenance의 observed/fresh 시각과 상태별 공식 확인·대기 등록 action을
  대표 회귀로 고정했습니다. overlay에는 commit·rollback·add·update·delete가 없어 timetable caller의 UoW에
  참여만 합니다.
- 호환·consumer 경계: top-level 파일은 정의·type alias·assignment가 없는 119줄 facade로 바꾸고 기존 public
  77개·private 7개, `__all__` 부재, 로컬 global 16개의 legacy pickle 복원을 exact identity로 유지했습니다.
  `main.py`는 canonical HTTP router 두 개를, timetable application은 canonical overlay 하나를 직접 사용하며
  production·script의 legacy 재진입은 0입니다. source 상수에는 기존 문자열을 좁은 Literal로 표시했지만
  runtime 값과 API wire는 바꾸지 않았습니다.
- 검증: owner·pickle·import-order 8건, 대표 HTTP/auth/challenge/overlay/시간표 행동 8건과 수정된 module
  boundary parameter를 합친 focused 66건을 실행해 모두 통과했습니다. API 전체 pytest와 실제 KORAIL 외부
  요청은 사용자 지침에 따라 실행하지 않았습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet
  legacy 32개와 strict mypy 148개 파일도 통과했습니다.
- 통합 운영·후속 범위: GUI override를 포함한 `experimental-rail` 프로필의 config 검증, 전체 build와
  `--force-recreate`를 마쳤습니다. migration·log-init exit 0, 장기 서비스 11/11 healthy, host/API/adapter
  health 200, adapter `DISPLAY=:99`와 X display 접근, API·adapter의 새 image 일치, 기존 project volume 6개
  보존, 재생성 뒤 로그 434줄의 fatal 표식 0건을 확인했습니다. 최상위 실제 정의는 4개 파일 2,651줄이고,
  adapter·smoke 의도적 root 2개 322줄을 제외한 이동 후보는 seat source 607줄과 Pydoll browser 1,722줄입니다.
  다음 작은 후보는 `korail_browser_seat_source.py`입니다. snapshot 422 validation detail이 거절된 입력값을
  되돌릴 수 있는 redaction 공백은 이번 구조 이동에 섞지 않고 `CHECKLIST.md`의 미완료 hardening으로
  기록했습니다. README·`OPERATIONS.md`·`POLICY_AND_SAFETY.md`는 변경할 계약이 없어 수정하지 않았습니다.

### 2026-08-08 백스물한 번째 구조 슬라이스

- source owner 재감사: 607줄 `korail_browser_seat_source.py`의 장기 canonical 위치는 provider adapter가
  맞지만, constructor의 transport와 예약·projection의 module-global normalizer/failure를 호출 시점에 교체하는
  기존 late-dispatch 계약이 확인됐습니다. class를 단순 alias facade로 통째 이동하면 이 계약이 깨지므로 wholesale
  이동은 남기지 않고, top-level class를 compatibility composition wrapper로 유지한 채 독립 정책부터
  추출했습니다. dormant direct-korail2 accountless source와도 병합하지 않았습니다.
- KORAIL browser reservation policy: 지원 provider·도착 시각·1명·좌석 등급 확인, KST service date와 주입된
  열차 번호 normalizer를 이용한 sidecar request 생성, credential `SecretStr` 경계, adapter failure와
  payment/auth/manual action/provider block/unavailable/post-click 결과 및 진행 시각 투영을 132줄
  `provider_adapters/korail_browser_reservation_policy.py`로 옮겼습니다. 이 owner는 I/O·async·transport·logger·
  retry·cooldown·결제를 소유하지 않습니다.
- 호환 wrapper: source의 `reserve_once`는 현재 `_normalize_train_number`와 `_AdapterFailure`, transport를 실제 호출
  시점에 사용하고 canonical 함수 세 개를 static identity로 보관합니다. 임시 owner module 이름은 class 조립 뒤
  삭제해 기존 module public 56개·private 10개와 `__all__` 부재를 그대로 유지했습니다. source 본체는 607줄에서
  549줄로 줄었고 기존 class·method pickle 경로도 바뀌지 않았습니다. sidecar client owner의 오래된 테스트 기대는
  실제 canonical `browser_contracts` dependency와 일치하도록 한 줄 보정했습니다.
- 검증: 새 순수 policy의 request·failure·wire result priority 18건과 시간표·KST·login·reservation outcome/progress·
  coupled identity·singleflight·cooldown·observation 및 transport/projection/confirmation late seam 대표 14건을
  합친 focused 32건을 통과했습니다. API 전체 pytest와 실제 KORAIL 외부 요청은 사용자 지침에 따라 실행하지
  않았습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 32개와 strict mypy 149개 파일도
  통과했습니다.
- 통합 운영·후속 범위: GUI override를 포함한 `experimental-rail` profile의 config 검증, 전체 build와
  `--force-recreate`를 마쳤습니다. migration·log-init exit 0, 장기 서비스 11/11 healthy, host/API/adapter
  health 200, `DISPLAY=:99` X 접근, 새 API image와 policy static identity, project volume 6개 보존, 최근 로그
  370줄의 fatal 표식 0건을 확인했습니다. 최상위 실제 정의는 4개 파일 2,593줄로 줄었고 이동 후보는 source
  549줄과 Pydoll browser 1,722줄입니다. 다음에는 source의 auth/session 또는 observation request/result 순수
  정책을 같은 방식으로 분리한 뒤 legacy-global seam 폐기 여부를 별도 판단합니다. README·`OPERATIONS.md`·
  `POLICY_AND_SAFETY.md`는 바뀐 사용자/API/DB/환경/운영 계약이 없어 수정하지 않았습니다.

### 2026-08-08 백스물두 번째 구조 슬라이스

- KORAIL browser observation policy: `KorailBrowserSeatSource.observe`에 함께 있던 KORAIL·1명·지원 좌석
  등급 판정, KST service date·정각 picker 입력과 23:59:59 조회 request 생성, 정규화 열차 번호와 KST 초 단위
  출발 시각의 첫 exact match, 좌석 등급별 상태·관측 시각·0~30초 freshness·지연 투영을 100줄
  `provider_adapters/korail_browser_observation_policy.py`의 두 순수 함수로 옮겼습니다. 이 owner는
  async·transport·logger·singleflight·cooldown·오류 clock을 소유하지 않습니다.
- 호환 wrapper: source의 `observe`는 요청 생성과 성공 projection을 기존 search `try` 바깥에서 호출하고,
  `_search`, `_ProviderCooldown`, `_AdapterFailure`, `_open_cooldown`, 실패 분류와 UTC 오류 관측 생성을 그대로
  소유합니다. picker와 train normalizer는 호출 시점에 주입해 기존 late-dispatch를 유지했습니다. canonical 함수
  두 개는 class static identity로 보관하고 임시 owner module 이름은 삭제해 public 56개·private 10개와
  `__all__` 부재, 기존 class/method 경로를 보존했습니다. source는 549줄에서 520줄로 줄었습니다.
- 검증: 새 pure owner의 dependency·surface, KST request, unsupported no-call, exact standard/first 결과,
  freshness clamp·delay, train/time mismatch와 picker·normalizer 호출 시점 seam 13건, 기존
  singleflight·identity/transport failure·shared cooldown 3건,
  source surface 1건, observation/browser contract boundary 19건을 합친 focused 36건을 통과했습니다. API 전체
  pytest와 실제 KORAIL 외부 요청은 사용자 지침에 따라 실행하지 않았습니다. `uv lock --check`, 전체 Ruff
  `E/F/I`, format ratchet legacy 32개와 strict mypy 150개 파일도 통과했습니다. browser contract 소비자
  ratchet에서 이미 canonical contract를 직접 재노출하던 예약 actor 호환 facade 누락도 현재 구조에 맞게
  보정했습니다.
- 통합 운영·후속 범위: GUI override를 포함한 `experimental-rail` profile의 config 검증, 전체 build와
  `--force-recreate`를 마쳤습니다. migration·log-init exit 0, 장기 서비스 11/11 healthy, host/API/adapter
  health 200, adapter X display 접근, API의 새 policy static identity, API·adapter image 일치, project volume
  6개 보존, 최근 로그 417줄의 fatal 표식 0건을 확인했습니다. 최상위 실제 정의는 4개 파일 2,564줄이며 이동
  후보는 source 520줄과 Pydoll browser 1,722줄입니다. 다음에는 source의 login/session orchestration을 별도
  정책으로 분리할 수 있는지 감사하되, transport·normalizer late-dispatch 폐기는 행동 변경 슬라이스로 분리합니다.
  README·`OPERATIONS.md`·`POLICY_AND_SAFETY.md`는 사용자/API/DB/환경/운영 계약이 바뀌지 않아 수정하지
  않았습니다.

### 2026-08-08 백스물세 번째 구조 슬라이스

- KORAIL browser auth policy: `verify_login`과 `prewarm_login`에 중복되던 provider credential의 sidecar
  request 생성, login method·문자열 credential generation·`SecretStr` identifier/password 경계, wire 4상태와
  code-owned invalid/blocked/failed 결과 투영을 48줄
  `provider_adapters/korail_browser_auth_policy.py`의 세 순수 함수로 옮겼습니다. 이 owner는 async·transport·
  `_AdapterFailure`·logger·retry·cooldown을 소유하지 않으며 session-state는 별도 순수 정책이 없는 7줄
  passthrough/fallback이라 source에 유지했습니다.
- 호환 wrapper: source의 verify/prewarm은 disabled일 때 credential을 읽지 않고 FAILED로 닫습니다. request
  builder와 각 transport await는 기존 `(ValueError, ValidationError)` try 안에, 성공 result projection은 try 밖에
  두었습니다. 호출 시점의 current transport와 module-global `_AdapterFailure`를 사용해 protection/rate만
  PROVIDER_BLOCKED로 분류하고 나머지는 FAILED로 유지합니다. canonical 함수 세 개는 class static identity로
  보관하고 임시 owner 이름을 삭제해 public 56개·private 10개와 `__all__` 부재, 기존 method 경로를
  보존했습니다. source는 520줄에서 511줄로 줄었습니다.
- 검증: 새 pure owner의 dependency·surface, SecretStr digest/redaction, invalid request, wire 4상태, code-owned
  실패 3상태, verify/prewarm 호출 시점 transport·replacement failure seam, disabled·validation no-retry와 성공
  projection try 경계 16건, 기존 login·generation·wire secret 대표 3건, sidecar/provider canonical contract
  boundary 16건을 합친 focused 35건을 통과했습니다. API 전체 pytest와 실제 KORAIL 외부 요청은 사용자 지침에
  따라 실행하지 않았습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 32개와 strict mypy
  151개 파일도 통과했습니다. contract consumer ratchet에서 기존 same-name compatibility alias와 앞선 reservation
  policy consumer 누락도 현재 구조에 맞게 보정했습니다.
- 통합 운영·후속 범위: GUI override를 포함한 `experimental-rail` profile의 config 검증, 전체 build와
  `--force-recreate`를 마쳤습니다. migration·log-init exit 0, 장기 서비스 11/11 healthy, host/API/adapter
  health 200, adapter X display 접근, API의 auth policy static identity, API·adapter image 일치, project volume
  6개 보존, 최근 로그 343줄의 fatal 표식 0건을 확인했습니다. 최상위 실제 정의는 4개 파일 2,555줄이며 이동
  후보는 source 511줄과 Pydoll browser 1,722줄입니다. 다음에는 source의 남은 timetable/overlay wrapper와 이미
  분리된 projection·query runtime 사이 경계를 재감사하고, 단순 passthrough는 억지 owner로 만들지 않습니다.
  README·`OPERATIONS.md`·`POLICY_AND_SAFETY.md`는 사용자/API/DB/환경/운영 계약이 바뀌지 않아 수정하지
  않았습니다.

### 2026-08-08 백스물네 번째 구조 슬라이스

- KORAIL browser batch overlay projection: source의 검색 성공 뒤에 남아 있던 snapshot identity dict 생성,
  입력 item별 exact lookup과 `no_exact_match`, matched item의 일반실/특실·공식 검색 URL 투영을 기존 canonical
  `timetable_management/korail_browser_projection.py`의 `project_overlay_items`로 옮겼습니다. 모든 snapshot을
  먼저 정규화한 뒤 item 순서대로 처리하고 동일 normalized train+KST-second identity는 기존 dict comprehension과
  같이 마지막 snapshot이 이깁니다. unmatched item은 unknown/not-observed 좌석만 갱신하고 기존 observed·
  non-unknown 좌석과 item 순서를 보존합니다. projection owner는 198줄에서 260줄로 확장됐습니다.
- 호환 wrapper: source의 `overlay`는 empty/disabled/passenger/date/window 판정, KST picker, request 생성,
  `_search`, provider/adapter 오류와 cooldown을 계속 소유합니다. 순수 projection 호출은 기존 search try 밖에 두고,
  호출 시점 `_normalize_train_number`, `_seat_class`, `self._overlay_item`, `self._mark_not_observed`, `KOREA`를
  명시적으로 주입했습니다. 새 canonical 함수는 class static identity로 보관하며 기존 helper 4개의 legacy pickle,
  module public 56개·private 10개와 `__all__` 부재를 유지했습니다. source는 511줄에서 492줄로 줄었습니다.
- 검증: normalizer 4종, primary projection, duplicate-last-wins·snapshot-before-item normalize 순서·mixed exact/no-match·
  existing observed preservation, legacy alias/pickle와 source late projector seam의 projection owner 8건, source exact·
  09032/9032·no-match 3건, observation first-row seam 1건, canonical provider owner consumer boundary 12건을 합친
  focused 24건을 통과했습니다. API 전체 pytest와 실제 KORAIL 외부 요청은 사용자 지침에 따라 실행하지
  않았습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 32개와 strict mypy 151개 파일도
  통과했습니다.
- 통합 운영·후속 범위: GUI override를 포함한 `experimental-rail` profile의 config 검증, 전체 build와
  `--force-recreate`를 마쳤습니다. migration·log-init exit 0, 장기 서비스 11/11 healthy, host/API/adapter
  health 200, adapter X display 접근, API의 overlay projection static identity, API·adapter image 일치, project
  volume 6개 보존, 최근 로그 344줄의 fatal 표식 0건을 확인했습니다. 최상위 실제 정의는 4개 파일 2,536줄이며
  이동 후보는 source 492줄과 Pydoll browser 1,722줄입니다. 다음에는 source의 timetable/overlay request window
  결정을 하나의 순수 policy로 설명할 수 있는지 감사하되, query runtime passthrough와 7줄 session state를 줄 수
  목적의 owner로 만들지 않습니다. README·`OPERATIONS.md`·`POLICY_AND_SAFETY.md`는 사용자/API/DB/환경/운영
  계약이 바뀌지 않아 수정하지 않았습니다.

### 2026-08-08 백스물다섯 번째 구조 슬라이스

- KORAIL browser KST 조회 시작 정책: source에 남아 있던 `_browser_departure_from`의 미래 service date
  00:00, 오늘의 요청 시작·현재 정각 중 늦은 시각, 과거·종료된 창 제외와 종료 경계 포함 결정을 29줄
  `provider_adapters/korail_browser_window_policy.py`의 순수 함수로 옮겼습니다. 새 owner는 datetime과
  `ZoneInfo`만 사용하며 browser DTO·transport·query runtime·cache·cooldown을 역참조하지 않습니다.
- 호환 wrapper: 기존 class method와 호출자 경로는 source에 유지하고, 매 호출의 `self._now()`와 module KST
  timezone을 class static canonical 함수에 명시적으로 주입합니다. observation의 current picker late-dispatch와
  overlay 조회 불가 reason을 위한 별도 두 번째 clock 읽기도 그대로입니다. canonical static hook 한 줄이 추가돼
  source는 492줄에서 493줄이 됐지만 module public 56개·private 10개, `__all__` 부재와 기존 method 경로를
  보존했습니다.
- 검증: 순수 owner·source static identity·exact runtime surface·canonical consumer와 미래/오늘/과거·종료·
  경계 포함 7분기·UTC→KST·호출 시점 clock/timezone seam 11건, source의 미래·오늘·지난 창 대표 3건,
  observation picker seam 1건을 합친 focused 15건을 통과했습니다. API 전체 pytest와 실제 KORAIL 외부 요청은
  사용자 지침에 따라 실행하지 않았습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 32개와
  strict mypy 152개 파일도 통과했습니다.
- 통합 운영·후속 범위: GUI override를 포함한 `experimental-rail` profile의 config 검증, 전체 build와
  `--force-recreate`를 마쳤습니다. migration·log-init exit 0, 장기 서비스 11/11 healthy, host proxy·내부
  API·adapter health 200, adapter `DISPLAY=:99`와 X 접근, API container의 window policy static identity,
  API 계열 service의 단일 image identity, project volume 6개 보존, 최근 로그 295줄의 fatal 표식 0건을
  확인했습니다. 최상위 실제 정의는 4개 파일 2,537줄이며 source 493줄은 순수 정책이 빠진 provider 조립·I/O
  wrapper로 남습니다. 다음에는 이를 줄 수 목표로 더 쪼개지 않고 composition root 여부를 감사한 뒤, 실제 큰
  이동 후보인 Pydoll browser 1,722줄의 orchestration owner 경계를 검토합니다. README·`OPERATIONS.md`·
  `POLICY_AND_SAFETY.md`는 사용자/API/DB/환경/운영 계약이 바뀌지 않아 수정하지 않았습니다.

### 2026-08-08 백스물여섯 번째 구조 슬라이스

- source 책임 동결 감사: 493줄 `korail_browser_seat_source.py`에는 transport·cooldown store·query runtime
  조립과 lifecycle, canonical auth·관측·예약·시간창·projection·confirmation 호출 순서, provider별 오류
  변환만 남았습니다. mutable query 상태와 순수 결정은 이미 각 owner가 소유하며 별도 상태 aggregate는 없습니다.
  따라서 이 파일을 app-level composition root라고 과장하지 않고, API와 Celery가 생성하는 stateful provider
  adapter composition shell 겸 기존 import 호환 경계로 유지합니다.
- 분리하지 않은 근거: 7줄 session-state passthrough와 source-owned UTC 관측 오류 clock은 독립 정책이 아니고,
  overlay와 primary timetable은 원본+`not_observed` 반환과 예외 fallback이라는 서로 다른 계약을 가집니다.
  class를 통째로 이동하면 construction-time transport와 호출 시점 failure·normalizer·seat projector·timezone·
  confirmation late-dispatch 및 method pickle 경로를 복잡한 forwarding facade로 다시 만들어야 하므로 구조 이득이
  없습니다. timetable application의 concrete KORAIL unavailable 예외 의존은 향후 provider-neutral live timetable
  failure 계약 슬라이스로 남깁니다.
- 타입·검증: source 자체를 strict mypy의 153번째 파일로 편입하면서 간접 facade attribute로 보이던 public
  transport/projection alias 9개를 실제 canonical leaf에서 직접 import하고 관측 오류 status를 domain enum으로
  명시했습니다. runtime identity와 public 56개·private 10개, `__all__` 부재는 기존 owner surface 4건으로
  재확인했고 strict 오류 0을 확인했습니다. API 전체 pytest는 추가하지 않았습니다.
- 운영·후속 범위: source import wiring 변경의 전체 build·재생성 검증은 바로 다음 127번째 코드 슬라이스와
  합쳐 한 번만 수행했습니다. 이 단계 직후 source는 506줄, 최상위 실제 정의는 4개 파일 2,550줄이었으며 다음
  실제 owner 후보를 1,722줄 Pydoll browser 안의 순수 시간 picker 상태 정책으로 확정했습니다. README·
  `OPERATIONS.md`·`POLICY_AND_SAFETY.md`는 사용자/API/DB/환경/운영 계약이 바뀌지 않아 수정하지 않았습니다.

### 2026-08-08 백스물일곱 번째 구조 슬라이스

- Pydoll 시간 picker 순수 상태 owner: `_PydollSession`에 섞여 있던 현재 hour window·서명, soft ARIA/DOM
  disabled, 정확한 24시간 catalog·5+5 인접 window·선택 완료·control log와 module disabled-class 판정 9개를
  135줄 `korail_sidecar/pydoll/search_hour_policy.py`로 옮겼습니다. owner는 search driver의
  `SearchHourCandidate`·`SearchControlState`만 읽고 DOM/CDP·clock·sleep·logger·network·credential·cleanup을
  참조하지 않습니다.
- 호환·consumer 경계: `_PydollSession`의 private callable 8개와 module `_has_disabled_class`는 canonical
  함수의 exact alias로 유지했습니다. search driver의 `port=self`, 기존 browser public 82개·private 29개와
  `__all__` 부재, 이동 전 protocol-0 pickle 9개, owner/browser import order와 optional Pydoll backend lazy
  import를 보존합니다. browser는 1,722줄에서 1,615줄로 줄었고 새 owner의 production consumer는 browser
  한 곳뿐입니다.
- 검증: owner boundary·surface/pickle/import order·consumer와 window/signature, soft ARIA/DOM fail-closed,
  exact catalog/adjacent/selected 상태를 묶은 신규 6건, 기존 search-driver candidate/dependency와 live control
  대표 3건, 외부 KORAIL이 아닌 로컬 Chromium의 soft-ARIA·24시간 catalog·인접 disabled window 3건을 합친
  focused 12건을 통과했습니다. API 전체 pytest와 실제 KORAIL 외부 요청은 사용자 지침에 따라 실행하지
  않았습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 32개와 strict mypy 154개 파일,
  `git diff --check`도 통과했습니다.
- 통합 운영·후속 범위: GUI override를 포함한 `experimental-rail` profile 전체 build와 staged
  `--force-recreate`를 완료했습니다. 첫 CLI가 5분 drain 유예보다 먼저 종료되고 임시 `log-init` 이름 충돌이
  발생했지만 동일 Compose project의 생성된 컨테이너를 idempotent `up -d`로 이어서 기동했으며 volume은
  삭제하지 않았습니다. 최종 migration·log-init exit 0, 장기 서비스 11/11 healthy, host proxy·내부 API·
  adapter health 200, adapter `DISPLAY=:99`와 X 접근, API container의 hour policy/browser/source identity,
  API 계열 단일 image identity, project volume 6개 보존, 최근 로그 298줄의 fatal 표식 0건을 확인했습니다.
  최상위 실제 정의는 4개 파일 2,443줄이며 다음 큰 후보는 1,615줄 Pydoll browser의 concrete DOM/session
  조립입니다. 이미 분리한 search driver와 중복되지 않는 owner 경계를 먼저 감사합니다. README·
  `OPERATIONS.md`·`POLICY_AND_SAFETY.md`는 사용자/API/DB/환경/운영 계약이 바뀌지 않아 수정하지 않았습니다.

### 2026-08-08 백스물여덟 번째 구조 슬라이스

- Pydoll live DOM 상태 owner: browser에 섞여 있던 frozen control 상태, visible element 수집, live
  `aria-disabled`·`disabled`·자기/상위/slide class 판독과 bounded CSS token 정규화를 111줄
  `korail_sidecar/pydoll/live_dom.py`로 옮겼습니다. owner는 search/reservation control contract, element의
  query·script port와 기존 hour disabled-class 정책만 읽으며 tab·clock·sleep·network·credential·cookie·
  browser cleanup을 참조하지 않습니다.
  판독 실패는 기존처럼 `read_error` 상태로 닫고 `CancelledError`는 그대로 전파합니다.
- 호환·consumer 경계: browser의 `_ControlState`, module sanitizer, `_PydollSession` control reader와 visible
  collector는 canonical 객체의 exact alias이고, 기존 `_visible_elements`는 호출 시점 tab을 주입하는 thin
  wrapper로 남겼습니다. browser public 82개·private 29개와 `__all__` 부재, 이동 전 protocol-0 pickle 4개,
  owner/browser import order, optional Pydoll backend lazy import를 보존했습니다. canonical production consumer는
  browser 한 곳뿐이고 browser는 1,615줄에서 1,540줄로 줄었습니다. 알 수 없는 `aria-disabled`가 다른 disabled
  근거 없이 `other`로 남는 기존 정책은 이번 이동에서 바꾸지 않고 별도 fail-closed 보강 항목으로 남겼습니다.
- 검증: owner·surface/pickle/import order·consumer, list·async iterable visibility와 detached node, live state·
  class bound·read error·취소 전파를 묶은 신규 6건, 기존 live-control·예약 driver late seam·async generator
  대표 3건, 외부 KORAIL이 아닌 로컬 Chromium의 duplicate hour control·soft-ARIA 2건을 합친 focused 11건을
  통과했습니다. API 전체 pytest와 실제 KORAIL 외부 요청은 사용자 지침에 따라 실행하지 않았습니다.
  `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 32개와 strict mypy 155개 파일,
  `git diff --check`를 통과했습니다.
- 통합 운영·후속 범위: GUI override를 포함한 `experimental-rail` profile의 config, 전체 build와
  `--force-recreate`를 완료했습니다. migration·log-init exit 0, 장기 서비스 11/11 healthy, host proxy·내부
  API·adapter health 200, adapter `DISPLAY=:99`와 X 접근, API container의 live DOM/browser identity 4개,
  API 계열 단일 image identity, 기존 volume 보존과 최근 로그 655줄의 fatal 표식 0건을 확인했습니다.
  최상위 실제 정의는 4개 파일 2,368줄이며 다음 후보는 1,540줄 Pydoll browser의 carousel/CDP·concrete
  session 경계입니다. 취소 중 mouse release와 tab lifecycle이 결합된 고위험 범위이므로 다음 슬라이스도
  먼저 독립 owner 경계를 감사합니다. README·`OPERATIONS.md`·`POLICY_AND_SAFETY.md`는 사용자/API/DB/환경·
  운영 계약이 바뀌지 않아 수정하지 않았습니다.

### 2026-08-08 백스물아홉 번째 구조 슬라이스

- Pydoll 시간 carousel 저수준 input owner: browser에 섞여 있던 unique viewport mouse drag, keyboard
  fallback과 CDP mouse payload 3개를 181줄
  `korail_sidecar/pydoll/search_hour_carousel_input.py`로 옮겼습니다. search driver의
  arrow→drag→keyboard→실패와 live window readback 순서는 그대로이고, owner는 매 호출 `port=self`의 현재
  visible helper·mouse dispatcher·tab만 사용해 tab lifecycle·URL·page text·network/cookie·credential·
  logger를 참조하지 않습니다.
- 취소·호환 경계: mouse는 bounds 검증 뒤 move→press→10회 move→release 순서와 press 이후
  `asyncio.shield` release를, keyboard는 focus exact-true와 left/right key down·up, 일반 오류 `False`·취소
  전파를 이동 전 그대로 유지했습니다. browser의 기존 세 메서드는 같은 module·qualname의 thin wrapper로
  남기고 canonical static hook에 매 호출 위임해 기존 method pickle 3개와 instance/class monkeypatch를
  보존했습니다. browser public 82개·private 29개와 `__all__` 부재, owner/browser import order, optional
  Pydoll backend lazy import, canonical production consumer browser 한 곳도 고정했습니다. 반복 취소 중 release
  task 완료를 끝까지 기다리는 보강은 이번 순수 이동과 섞지 않고 별도 안전 항목으로 남겼습니다.
- 검증: owner exact boundary·wrapper/pickle·consumer/import-order, CDP 좌표 반올림과 현재 tab, next/prev
  drag·bounds·failure·단일 취소 release, keyboard 방향·fail-closed·취소를 묶은 신규 6건, 기존 search-driver
  dependency 1건과 외부 KORAIL이 아닌 로컬 Chromium mouse·keyboard 2건을 합친 focused 9건을
  통과했습니다. API 전체 pytest와 실제 KORAIL 외부 요청은 사용자 지침에 따라 실행하지 않았습니다.
  `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 32개와 strict mypy 156개 파일을 통과했습니다.
  browser는 1,540줄에서 1,438줄로 줄었고 최상위 실제 정의는 4개 파일 2,266줄입니다.
- 통합 운영·후속 범위: GUI override `experimental-rail`의 config와 전체 build를 통과했습니다. 첫 raw
  `--force-recreate` CLI가 5분 drain 유예에서 timeout되며 Redis 교체본과 기존 container가 잠시 같은 volume을
  사용해 worker가 `MISCONF`를 관측했습니다. volume 삭제 없이 기존 container를 제거하고 생성된 교체본을
  canonical 이름으로 이어 기동한 뒤 영향받은 worker 3개를 재시작했습니다. 최종 Redis는 단일 instance이고
  RDB/AOF 상태 `ok`, migration·log-init exit 0, 장기 서비스 11/11 healthy, host proxy·내부 API·adapter
  health 200, `DISPLAY=:99`와 X 접근, container의 owner/browser static identity 3개, API 계열 단일 image,
  active volume 4개 보존, 복구 뒤 로그 373줄의 fatal 표식 0건을 확인했습니다. 다음에는 1,438줄 browser의
  window polling·animation·bounded diagnostic metadata·arrow readback 또는 concrete session 조립 중 실제
  독립 owner 경계를 다시 감사합니다. README·`POLICY_AND_SAFETY.md`는 사용자/API/DB/환경/안전 계약이
  바뀌지 않아 수정하지 않았습니다.

### 2026-08-08 백서른 번째 구조 슬라이스

- Pydoll 시간 carousel authoritative observation owner: browser에 섞여 있던 visible/raw candidate 수집,
  window 진행 안정화·animation settle, bounded 실패 metadata·로그와 arrow 판독 여섯 책임을 255줄
  `korail_sidecar/pydoll/search_hour_carousel_observation.py`로 옮겼습니다. exact `NN시` candidate의 DOM
  순서·중복을 보존하고, 방향에 맞게 진행한 같은 window가 두 번 연속 관찰된 뒤 animation 결과까지 같을 때만
  이동 성공으로 승인합니다. owner는 search candidate/control contract와 browser source 오류만 알고 URL·page
  text·network/cookie·credential·tab lifecycle을 참조하지 않습니다.
- 호환·의존 경계: browser의 기존 여섯 메서드는 같은 module·qualname의 thin wrapper로 남아 canonical static
  hook에 `port=self`, logger, monotonic clock, sleep와 sanitizer를 호출 시점에 주입합니다. 이동 전 protocol-0
  method pickle 6개, browser public 82개·private 29개와 `__all__` 부재, owner/browser import order, optional
  Pydoll backend lazy import와 canonical production consumer browser 한 곳을 고정했습니다. search driver의
  arrow→drag→keyboard→실패 순서와 schedule/date commit·선택 readback은 이동하지 않았습니다.
- 검증: owner exact boundary·wrapper/pickle·consumer/import-order, visible/raw candidate, stable progress 2회와
  timeout reset, animation settle·오류·취소, bounded metadata·로그와 exact-one arrow를 묶은 신규 6건과 외부
  KORAIL이 아닌 로컬 Chromium의 24시간 catalog·time-owned arrow·no-progress·인접 disabled window 4건을 합친
  focused 10건을 통과했습니다. API 전체 pytest와 실제 KORAIL 외부 요청은 사용자 지침에 따라 실행하지
  않았습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 32개와 strict mypy 157개 파일을
  통과했습니다. browser는 1,438줄에서 1,345줄로 줄었고 최상위 실제 정의는 4개 파일 2,173줄입니다.
- 통합 운영·후속 범위: `scripts/ops.ps1 experimental`로 전체 profile build와 단계적 drain·강제 재생성을
  완료했습니다. 이 스크립트가 아직 `compose.korail-gui.yml`을 포함하지 않아 adapter가 headless로 생성된 것을
  확인한 뒤, 같은 새 image의 adapter만 두 Compose 파일과 `--no-deps`로 다시 생성했습니다. 최종
  migration·log-init exit 0, 장기 서비스 11/11 healthy, host proxy·내부 API·adapter health 200,
  `DISPLAY=:99`와 X 접근, container의 owner/browser static identity 6개, API 계열 단일 image, active volume
  4개 보존, Redis RDB/AOF 상태 `ok`, 최근 로그 1,482줄의 fatal 표식 0건을 확인했습니다. GUI override를
  운영 스크립트가 직접 선택하는 개선은 체크리스트에 미완료로 남겼습니다. 다음에는 1,345줄 browser의
  schedule/date commit·선택 readback 또는 concrete session 조립 중 독립 owner 경계를 다시 감사합니다.
  README·`POLICY_AND_SAFETY.md`는 사용자/API/DB/안전 계약이 바뀌지 않아 수정하지 않았습니다.

### 2026-08-08 백서른한 번째 구조 슬라이스

- Pydoll schedule selection commit owner: browser에 남아 있던 전체 날짜·시간 readback, 날짜-only readback과
  시간 후보 click 뒤 live `current` marker 확인을 83줄
  `korail_sidecar/pydoll/search_schedule_commit.py`로 옮겼습니다. 앞의 두 경로는 일시적인
  `BrowserSourceUnavailable`만 bounded polling 안에서 다시 읽고 timeout은 고정
  `departure_schedule_readback`으로 닫습니다. 시간 후보는 한 번만 click하고 같은 element의 container에 정확한
  `current` token이 생긴 경우만 성공하며 marker timeout은 `False`입니다. 일반 오류와 `CancelledError`는 이동
  전처럼 전파합니다.
- 호환·호출 순서: browser의 기존 세 메서드는 같은 module·qualname의 wrapper로 남아 canonical static hook에
  `port=self`, 현재 monotonic clock·sleep·source 오류 타입과 timeout getter를 호출 시점에 주입합니다. 첫 코드
  리뷰에서 timeout 값을 owner 진입 전에 읽어 click과 clock의 기존 순서를 바꾸는 문제를 발견해 callable getter로
  고쳤습니다. 최종 순서는 full/date readback의 `monotonic→timeout`, click 확인의
  `click→monotonic→timeout`입니다. 이동 전 protocol-0 pickle 3개, browser public 82개·private 29개와
  `__all__` 부재, owner/browser import order, optional Pydoll backend lazy import와 production consumer browser
  한 곳을 고정했습니다. 날짜·시간 picker orchestration·적용 click과 `#startDate` parsing은 search driver에
  유지했습니다.
- 검증: owner exact boundary·wrapper/pickle·consumer/import-order, full/date의 transient source 재시도·정확한
  timeout stage·기타 오류·취소, click 1회·exact marker·1초 상한·오류·취소를 묶은 신규 6건과 기존 search-driver
  late seam 1건, 외부 KORAIL이 아닌 로컬 Chromium의 schedule mismatch·soft-ARIA hour·날짜 변경 뒤 disabled
  hour 재조회 3건을 합친 focused 10건을 통과했습니다. API 전체 pytest와 실제 KORAIL 외부 요청은 사용자
  지침에 따라 실행하지 않았습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 32개와 strict
  mypy 158개 파일을 통과했습니다. browser는 1,345줄에서 1,351줄이 되었고 최상위 실제 정의는 4개 파일
  2,179줄입니다. wrapper 호환 때문에 물리 줄 수는 늘었지만 schedule commit 승인 책임은 concrete session에서
  독립됐습니다.
- 통합 운영·후속 범위: `scripts/ops.ps1 experimental`로 전체 profile build와 단계적 drain·강제 재생성을
  완료한 뒤, 같은 새 image의 KORAIL adapter만 GUI overlay와 `--no-deps`로 다시 생성했습니다. 최종
  migration·log-init exit 0, 장기 서비스 11/11 healthy, host proxy·내부 API·adapter health 200,
  `DISPLAY=:99`와 X 접근, container의 owner/browser static identity 3개, API 계열 단일 image, active volume
  4개 보존, Redis RDB/AOF 상태 `ok`, 최근 로그 553줄의 fatal 표식 0건을 확인했습니다. 다음에는 1,351줄
  browser의 generic visible/exact wait 경계와 concrete session 조립 root를 비교해 독립 owner가 되는 범위를 다시
  감사합니다. README·`OPERATIONS.md`·`POLICY_AND_SAFETY.md`는 사용자/API/DB/환경·운영·안전 계약이
  바뀌지 않아 수정하지 않았습니다.

### 2026-08-08 백서른두 번째 구조 슬라이스

- Pydoll generic DOM interaction owner: concrete session에 남아 있던 current-tab value/text script read,
  visible 순서의 exact text 탐색·click, value·exact·enabled·visible·dialog bounded polling과 실패 stage 열 책임을
  221줄 `korail_sidecar/pydoll/dom_interaction.py`로 옮겼습니다. 기존 `live_dom.py`는 polling·logger·실패
  stage를 모르는 one-shot visible collection·live control-state leaf로 유지하고, `_visible_elements`는 scope 또는
  현재 tab을 그 leaf에 연결하는 browser composition wrapper로 남겼습니다.
- 실패·안전·호환 경계: enabled exact 조회는 같은 label의 disabled clone을 건너 모든 visible exact control의
  live state를 읽고 첫 enabled control만 반환합니다. timeout 로그에는 code-owned stage·visible/exact 개수와
  bounded structural state만 남기며 selector·label·actual value를 기록하지 않습니다. 일반 오류와
  `CancelledError`는 기존 경로대로 전파하고, `has_exact_visible`만 detached React node의 일반 text 오류를
  건너뜁니다. browser의 기존 열 메서드는 같은 module·qualname의 wrapper로 남아 canonical hook에 current tab,
  `port=self`, timeout getter·monotonic clock·sleep·logger·source 오류 타입을 호출 시점에 주입합니다. 이동 전
  protocol-0 pickle 10개, browser public 82개·private 29개와 `__all__` 부재, owner/browser import order,
  optional Pydoll backend lazy import와 production consumer browser 한 곳을 고정했습니다.
- 검증: owner exact boundary·wrapper/pickle·consumer/import-order, current-tab JS/value/text, exact find·has·click과
  detached/cancel, value·exact·visible·dialog polling, enabled accepted label·disabled clone·live state·secret-free
  warning을 묶은 신규 6건과 기존 search-driver late seam 3건, live enabled-control·has-exact awaited text 2건,
  외부 KORAIL이 아닌 로컬 Chromium 24시간 catalog 1건을 합친 focused 12건을 통과했습니다. 기존
  Starlette/httpx 전환 경고 1건 외 실패는 없으며 API 전체 pytest와 실제 KORAIL 외부 요청은 사용자 지침에 따라
  실행하지 않았습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 32개와 strict mypy 159개
  파일을 통과했습니다. browser는 1,351줄에서 1,350줄, 최상위 실제 정의는 4개 파일 2,178줄입니다. 물리 줄
  수보다 concrete session에서 generic DOM polling·실패 정책을 제거한 책임 분리가 이번 슬라이스의 기준입니다.
- 통합 운영·후속 범위: `scripts/ops.ps1 experimental`로 전체 profile build와 단계적 drain·강제 재생성을
  완료한 뒤 같은 새 image의 KORAIL adapter만 GUI overlay와 `--no-deps`로 다시 생성했습니다. 최종
  migration·log-init exit 0, 장기 서비스 11/11 healthy, host proxy·내부 API·adapter health 200,
  `DISPLAY=:99`와 X 접근, container의 owner/browser static identity 10개, API 계열 단일 image, active volume
  4개 보존, Redis RDB/AOF 상태 `ok`, 최근 로그 614줄의 fatal 표식 0건을 확인했습니다. 다음에는 1,350줄
  browser의 concrete session composition root를 유지하면서도 분리 가능한 network evidence callback 또는 남은
  단일 DOM/lifecycle 경계가 있는지 다시 감사합니다. README·`OPERATIONS.md`·`POLICY_AND_SAFETY.md`는
  사용자/API/DB/환경·운영·안전 계약이 바뀌지 않아 수정하지 않았습니다.

### 2026-08-08 백서른세 번째 구조 슬라이스

- Pydoll concrete composition shell 동결: 1,350줄 `korail_pydoll_browser.py`의 남은 책임을 다시 감사한 결과,
  client의 인증·검색·예약 actor 조립과 session의 Chromium lifecycle·로그인·검색·예약 driver 조립, current-tab
  port와 compatibility callback 연결이 한 concrete adapter 경계로 남았습니다. 제출 latch, sanitized network
  evidence, 최초 open 여부와 HTTP capture offset 외 독립 mutable state나 새 순수 정책은 없습니다. 20줄 network
  listener callback도 URL·body·header·cookie를 저장하지 않고 `(status, resource_type)`만 최초 순서로 중복 제거하는
  lifecycle state adapter라 별도 owner로 미세 분리하지 않았습니다. 이후 새 정책·DOM 판정·상태 전이는 이 shell에
  추가하지 않고 별도 canonical owner로 둡니다.
- 의존·호환 경계: `korail_sidecar/http.py`의 예약·verify·prewarm 내부 요청이 top-level browser compatibility
  alias를 거치지 않고 canonical auth/reservation contract를 직접 import하도록 바꿨습니다. 이로써 browser
  shell의 production consumer는 engine-selected client를 지연 생성하는 runtime과 operational smoke 두 곳뿐입니다.
  shell의 local definition 9개·module assignment 20개, 다섯 class method inventory와 허용 implementation island,
  public 82개·private 29개와 `__all__` 부재, core protocol/client/session/context/factory의 구형 pickle 6개,
  optional Pydoll backend·legacy facade passive import를 구조 gate로 동결했습니다.
- 검증·운영: composition gate 신규 4건, canonical contract consumer 1건과 sidecar HTTP composition 6건,
  로그인·예약·검색 driver late seam, constructor-time policy capture와 sanitized network callback 대표 7건을 합친
  focused 18건을 통과했습니다. API 전체 pytest와 실제 KORAIL 외부 요청은 사용자 지침에 따라 실행하지
  않았습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 32개와 strict mypy 159개 파일,
  `git diff --check`도 통과했습니다.
- 통합 운영·비밀 회전: GUI override를 포함한 `experimental-rail` config와 전체 build·단계적 강제 재생성을
  완료하고 adapter를 같은 새 image의 GUI 구성으로 다시 생성했습니다. 점검 과정에서 컨테이너 환경 전체를
  출력한 명령이 내부 adapter token까지 포함한 것을 즉시 발견해 해당 token을 새 난수 값으로 회전하고,
  adapter와 이를 소비하는 API·worker·scheduler 계열 6개를 다시 생성했습니다. 5분 stop 유예에서 CLI가
  timeout되어 기존 API와 `created` 교체본이 잠시 함께 남았지만 volume 삭제 없이 idempotent `up -d`로 교체본을
  기동해 canonical 이름을 회복했습니다. 최종 migration·log-init exit 0, 장기 서비스 11/11 healthy, host HTTP·
  내부 API·adapter 200, adapter `DISPLAY=:99`·GUI/X 접근, canonical contract/browser identity 4개와 optional
  Pydoll backend 0, 회전된 token의 6/6 일치, API 계열 image identity 1개, project volume 6개 보존, Redis
  RDB/AOF `ok`, 최근 로그 665줄의 fatal 표식 0건을 확인했습니다. README·`POLICY_AND_SAFETY.md`는 사용자/API/
  DB/환경·안전 계약이 바뀌지 않아 수정하지 않았습니다.

### 2026-08-08 백서른네 번째 구조 슬라이스

- 중앙 service 완료 감사와 동결: 453줄 `services.py`의 local 함수 14개는 canonical watch·observation·reservation·
  provider application/runtime dependency를 호출 시점에 조립하고 application 오류를 기존 FastAPI
  403·404·409·422로 변환하는 compatibility wrapper입니다. production source consumer와 직접 SQL·row lock·
  commit·rollback·flush·refresh·provider transport는 모두 0건이고, PostgreSQL observation/reservation fencing
  검증 script 두 곳만 기존 이름을 사용합니다. 별도 owner로 다시 옮길 정책/UoW가 없으므로 이 표면을 frozen
  compatibility composition facade로 확정하고 새 정책·상태 전이의 재유입을 금지했습니다.
- 구조·typing gate: exact local definition 14개·assignment/class/`__all__` 부재·453줄 상한, wrapper 13개의
  canonical delegate와 설정 getter 1개, transaction/transport 금지, production consumer 0·운영 script 2곳의 exact
  심볼, canonical owner 역의존 0과 `HTTPException` 변환 개수를 신규 구조 테스트 4건으로 고정했습니다. 기존
  create·reservation-result·update wrapper의 호출 시점 seam 대표 3건을 더한 focused 7건을 사용합니다.
  `services.py`를 strict mypy의 160번째 파일로 편입했습니다.
- 범위·후속: 이번 변경은 구조 테스트·typing ratchet·문서뿐이고 production source와 runtime image 계약을
  바꾸지 않아 Compose 재빌드·재생성은 생략합니다. 같은 감사에서 `worker.py`는 의도적 Celery composition root,
  중앙 `schemas.py`는 76개 alias compatibility hub, `models.py`는 24개 alias metadata registry임을 확인했습니다.
  이 셋은 각각 별도 exact gate와 strict 편입으로 마무리하며, 실제 남은 provider UoW는 303줄
  `provider_accounts.py`의 credential CRUD·row-lock generation CAS·watch resume transaction입니다. 다음 큰 수직
  슬라이스는 이를 `provider_account_management/application.py` owner로 이동하는 작업입니다. README·
  `OPERATIONS.md`·`POLICY_AND_SAFETY.md`는 기능·API·환경·운영·안전 계약이 바뀌지 않아 수정하지 않았습니다.

### 2026-08-08 백서른다섯 번째 구조 슬라이스

- provider account application owner 이동: 303줄 top-level 구현이 직접 소유하던 credential 암복호화·redacted
  read projection, KORAIL/SRT 계정 CRUD, `FOR UPDATE` credential generation CAS, 인증 성공 뒤 watch 재개와
  계정 write의 단일 transaction을 305줄 `provider_account_management/application.py`로 행동 그대로
  이동했습니다. 최초 insert의 uniqueness/autoflush `IntegrityError`만 rollback 뒤 generation conflict로
  닫고, 취소와 다른 DB·crypto·refresh 오류는 caller session으로 전파합니다. stale credential generation은
  최신 인증 상태와 마지막 성공 시각을 강등하지 않으며 `commit=False`는 외부 예약 UoW 안에서 flush만 합니다.
- 호환·의존 경계: 76줄 top-level `provider_accounts.py`는 definition·assignment·`__all__` 없이 기존 public
  28개·private 5개 wildcard 표면을 exact re-export합니다. 이동 전 local callable/class 14개와 credential
  dataclass의 구형 pickle은 canonical 객체로 복원되며 owner/legacy import 순서에도 identity가 같습니다.
  provider-account HTTP, provider runtime, provider adapter execution, watch application, worker와 PostgreSQL
  reservation fencing script는 canonical owner를 직접 사용하고 production/script의 legacy 재진입은 0건입니다.
  facade의 dependency attribute 재할당은 canonical owner에 전달하지 않는 one-way 호환 경계입니다.
- 검증·운영: owner/facade 구조·pickle·consumer 5건, provider account API/UoW 파일 12건, reservation
  transaction adapter 1건과 변경된 model/auth-recovery/contract boundary 18건을 묶은 focused 36건을
  통과했습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 31개, strict mypy 161개 파일과
  `git diff --check`를 확인했고 API 전체 pytest는 사용자 지침에 따라 실행하지 않았습니다. GUI override를
  포함한 experimental profile config를 검증한 뒤 전체 image를 재빌드하고 단계적으로 강제 재생성했으며,
  같은 새 image의 KORAIL adapter를 GUI override로 다시 맞췄습니다. 최종 migration·log-init exit 0, 장기
  서비스 11/11 healthy, host health 200, adapter GUI=true·`DISPLAY=:99`·X 접근, container facade surface
  28/5와 identity 14개, API 계열 image identity 1개, adapter token 내부 일치 6/6, project volume 6개 보존,
  Redis RDB/AOF `ok`, 최근 로그 fatal 표식 0건을 확인했습니다. README·`OPERATIONS.md`·
  `POLICY_AND_SAFETY.md`는 기능·API·DB·환경·운영·비밀값 의미가 바뀌지 않아 수정하지 않았습니다.

### 2026-08-08 백서른여섯 번째 구조 슬라이스

- provider login verification owner 이동: top-level 211줄 구현이 함께 소유하던 provider-neutral outcome,
  KORAIL/SRT 단발 verify·prewarm dispatch와 session telemetry projection을 228줄
  `provider_account_management/login_verification.py`로 이동했습니다. SRT identifier·인증·NetFunnel·provider
  오류의 sanitized 결과 우선순위, broad ordinary exception의 `FAILED` 변환과 `CancelledError` 전파, 한 번만
  호출하는 no-retry 계약을 유지했습니다. 원격 SRT status를 우선하고 local snapshot에서는 monotonic clock을
  한 번만 읽어 age와 재사용 잔여 시간을 0 이상으로 clamp하며, 이전 status에 새 telemetry 필드가 없으면
  `None`으로 닫는 fallback도 보존했습니다.
- 호환·typing 경계: 49줄 top-level `provider_login_verification.py`는 definition·assignment·`__all__` 없이
  기존 public 21개·private 0개를 exact re-export합니다. 이동 전 local class 7개의 구형 pickle과 양방향
  import identity를 보존하고, production 5곳은 canonical owner를 직접 사용하며 legacy 재진입은 0건입니다.
  외부 SRTrain untyped import는 기존 SRT owner와 같은 좁은 경계로 표시하고, default executor와 원격/local
  snapshot을 명시적으로 좁혀 새 owner를 strict mypy의 162번째 파일로 편입했습니다. facade attribute 재할당은
  canonical owner에 전달하지 않는 one-way 호환입니다.
- 검증·운영: owner/facade 구조·pickle·consumer 6건, verifier verify/prewarm·KORAIL/원격 SRT/local SRT telemetry
  8건, provider account·runtime 대표 2건, KORAIL auth·source 대표 5건과 sidecar contract boundary 5건을 합친
  focused 26건을 통과했습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 31개, strict mypy
  162개 파일을 통과했고 API 전체 pytest는 사용자 지침에 따라 실행하지 않았습니다. GUI override를 포함한
  experimental profile config와 전체 build·단계적 강제 재생성 뒤 adapter GUI 구성을 다시 적용했습니다.
  최종 migration·log-init exit 0, 장기 서비스 11/11 healthy, host health 200, GUI=true·`DISPLAY=:99`·X 접근,
  container facade surface 21/0과 identity 7개, API image identity 1개, adapter token 내부 일치 6/6, project
  volume 6개 보존, Redis RDB/AOF `ok`, 최근 로그 fatal 표식 0건을 확인했습니다. README·`OPERATIONS.md`·
  `POLICY_AND_SAFETY.md`는 사용자/API·DB·환경·운영·안전 의미가 바뀌지 않아 수정하지 않았습니다.

### 2026-08-08 백서른일곱 번째 구조 슬라이스

- provider session runtime owner 이동: 368줄 top-level 구현이 소유하던 enabled account `FOR UPDATE` read,
  credential generation 재확인, provider I/O 전 rollback, startup prewarm과 recoverable revision별 1회 복구,
  재사용 가능한 session의 인증 상태 복원과 watch 재개 transaction을
  `provider_account_management/runtime.py`로 행동 그대로 이동했습니다. stale generation은 외부 호출이나
  최신 인증 상태를 덮지 못하고, authenticated write와 watch 재개만 같은 commit에 참여합니다. registry에는
  비밀값 없이 provider별 outcome·최신 revision·완료 여부만 남기며 `CancelledError`는 계속 전파하고 일반
  maintenance 오류만 redacted warning 뒤 다음 tick으로 격리합니다.
- 호환·의존 경계: 107줄 top-level `provider_runtime.py`는 definition·assignment·`__all__` 없이 기존 public
  29개·private 6개를 exact re-export합니다. 이동 전 local class/function 11개의 구형 pickle과 owner/legacy
  import 순서 identity를 보존하고 facade attribute 재할당은 canonical owner에 전달하지 않는 one-way 경계로
  고정했습니다. `main.py`만 canonical runtime을 직접 조립하고 production legacy 재진입은 0건입니다.
  새 owner는 account application·credential/login contract·model/schema와 호출 시점 auth-recovery runtime만
  참조하며 top-level facade·FastAPI·worker·provider transport를 역참조하지 않습니다.
- 검증·운영: runtime owner/facade·pickle·consumer 6건과 기존 session recovery/UoW 행동 11건, 인접
  provider-account/login owner와 persistence·transition·contract 경계 32건을 묶은 focused 49건을
  통과했습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 30개, strict mypy 163개 파일과
  `git diff --check`를 확인했고 API 전체 pytest는 사용자 지침에 따라 실행하지 않았습니다. GUI override를
  포함한 experimental profile config를 검증한 뒤 전체 image를 재빌드하고 단계적으로 강제 재생성했으며,
  KORAIL adapter를 GUI override로 다시 맞췄습니다. 최종 migration·log-init exit 0, 장기 서비스 11/11
  healthy, host health 200, GUI=true·`DISPLAY=:99`·X 접근, container facade surface 29/6과 identity 11개,
  API 계열 image identity 1개, adapter token 내부 일치 6/6, project volume 6개 보존, Redis RDB/AOF `ok`,
  최근 로그 443줄의 fatal 표식 0건을 확인했습니다. README·`OPERATIONS.md`·`POLICY_AND_SAFETY.md`는
  사용자/API·DB schema·환경·운영·비밀값 의미가 바뀌지 않아 수정하지 않았습니다.

### 2026-08-09 백서른여덟 번째 구조 슬라이스

- Celery composition root 동결: 476줄 `worker.py`는 top-level 함수 27개, dependency 조립 closure 3개와
  Celery task 4개만 남은 의도적 process root입니다. canonical due/group/reservation/reconciliation/notification
  application과 lifecycle runtime을 호출 시점 dependency로 조립하며 직접 SQL·transaction·FastAPI·provider
  adapter method·credential/secret 처리는 소유하지 않습니다. strict 보정은 AsyncSession 주석 2곳, 구조적 port
  cast 2곳과 외부 Celery decorator의 좁은 ignore 4곳으로 한정했습니다.
- 검증: exact definition·dependency wiring·정책 재유입 금지·task/Compose entrypoint·reverse dependency 구조
  5건과 기존 delegate·Celery 이름·single-watch·lease/cleanup·취소 대표 9건을 합친 focused 14건을
  통과했습니다. `worker.py`를 strict mypy의 164번째 파일로 편입했습니다.

### 2026-08-09 백서른아홉 번째 구조 슬라이스

- 중앙 compatibility hub 동결: 133줄 `schemas.py`는 local definition 없이 canonical schema·contract 76개를,
  38줄 `models.py`는 mapper 23개와 `utcnow`를 exact alias합니다. schema production/script consumer는 0이고
  model hub는 전체 metadata bootstrap을 위한 `main.py`와 Alembic `migrations/env.py`만 소비합니다. exact
  assignment/import/runtime surface, canonical mapper 23개·table 23개·pickle과 bootstrap consumer를 구조
  6건으로 고정하고 두 hub를 strict mypy에 편입해 대상이 166개가 됐습니다.

### 2026-08-09 백마흔 번째 구조 슬라이스

- SRT fullstack fixture owner 이동: 고정 test origin의 JSON GET과 strict train-shape projection을 90줄
  `provider_adapters/srt_fullstack_fixture.py`로 이동했습니다. top-level 15줄 facade는 기존 public 9개·private
  0개와 local class/function 3개의 구형 pickle을 canonical 객체로 복원합니다. `main.py`와 SRT source runtime은
  canonical owner를 직접 사용하고 legacy 재진입은 0이며, facade의 `urlopen` 재할당은 owner에 전달하지 않는
  one-way 경계입니다. fixture client의 structural SRT port는 source runtime에서 좁게 cast하고, 실제 source
  protocol은 소비하는 필드와 sequence 반환만 요구하도록 정리했습니다.
- 최종 검증·운영: fixture owner/facade·행동·SRT execution 15건, 중앙 hub 6건, worker 구조 5건을 묶은
  focused 26건을 통과했습니다. `uv lock --check`, 전체 Ruff `E/F/I`, format ratchet legacy 30개, strict mypy
  167개 파일과 `git diff --check`를 확인했고 API 전체 pytest와 실제 철도사 외부 호출은 사용자 지침에 따라
  실행하지 않았습니다. GUI override를 포함한 experimental profile 전체 image를 재빌드하고 단계적으로 강제
  재생성한 뒤 KORAIL adapter를 GUI로 다시 맞췄습니다. 최종 migration·log-init exit 0, 장기 서비스 11/11
  healthy, host health 200, GUI=true·`DISPLAY=:99`·X 접근, fixture facade surface 9/0과 identity 3개, API 계열
  image identity 1개, adapter token 내부 일치 6/6, project volume 6개 보존, Redis RDB/AOF `ok`, 최근 로그
  406줄의 fatal 표식 0건을 확인했습니다. 사용자/API·DB schema·환경·운영·안전 의미가 바뀌지 않아 README·
  `OPERATIONS.md`·`POLICY_AND_SAFETY.md`는 수정하지 않았습니다.

## 단계별 완료 기준과 rollback

| 단계 | 완료 기준(DoD) | rollback 기준과 방법 |
|---|---|---|
| 0 | 비밀값·생성물 제외 확인, 현재 검증 명령과 결과 기록, 되돌릴 수 있는 기준 commit/tag 확보 | 기준선이 없거나 secret 포함 가능성이 있으면 코드 이동을 시작하지 않고 staging을 해제해 분류부터 다시 수행 |
| 1 | 문서 링크 동기화, lint·format·import 규칙이 기존 코드에 적용 가능한 범위로 실행, 예외가 문서화됨 | 기존 미전환 코드 전체를 한 번에 차단하면 신규·변경 파일 우선 gate로 축소하고 예외를 ledger에 기록 |
| 2 | import만 바뀐 작은 모듈 단위, 공개 API 호환, 관련 테스트·전체 typecheck/build/pytest 통과 | compatibility export 또는 이전 import 경로로 한 슬라이스만 되돌림. 새 정책을 함께 넣었다면 이동과 정책 변경을 분리 |
| 3 | 기능별 상태·API·표시 책임이 분리되고 stale response·부분 실패·접근성 회귀 테스트 통과 | 해당 feature의 조립점을 이전 컴포넌트로 복원하되 새 테스트와 발견한 계약은 보존 |
| 4 | 순수 결정 함수가 기존 상태 전이와 동일한 결과를 내고 예약 episode·reconciliation 회귀 테스트 통과 | 호출자를 기존 service 정책으로 되돌리고 새 함수는 비활성 상태로 보존해 차이를 재분석 |
| 5 | route/task가 얇아지고 UoW 범위, lock 순서, outbox 원자성, PostgreSQL 동시성 테스트 통과 | 새 seam의 wiring만 이전 service 호출로 복원. migration이나 데이터 삭제로 되돌리지 않음 |
| 6 | 역할별 provider 계약과 capability가 기존보다 넓어지지 않고 timeout·보호·부분 실패가 fail-closed | provider registry를 기존 adapter로 복원하고 capability를 안전하게 `false`로 강등 |
| 7 | `App.jsx`·`api.js` 제거, 잔여 JS/JSX 테스트 전환과 `allowJs=false`, typecheck·Vitest·build·Sites 검증 통과, CSS 시각 회귀 확인 | TS와 CSS를 같은 rollback으로 묶지 않고 실패한 전환 단위만 이전 진입점 또는 stylesheet import로 복원 |
| 8 | browser lifecycle·DOM parser·검색·로그인·예약 경계가 분리되고 실제 외부 재호출 없이 계약 테스트 통과, 허가된 smoke에서 보호·결제 전 중단 확인 | sidecar entrypoint를 기존 facade로 되돌리고 새 내부 모듈 호출을 끔. 보호 응답을 성공으로 완화하지 않음 |

어떤 단계에서도 `down -v`, volume 삭제, migration history 재작성으로 rollback하지 않습니다. DB 계약 변경이 꼭 필요하면 forward migration과 호환 읽기 기간을 별도 설계합니다.

## 수직 슬라이스 진행 규칙

1. 한 슬라이스의 현재 행동과 보존 계약을 테스트 또는 기존 테스트 위치로 확인합니다.
2. 소유 모듈과 의존 방향을 정하고, 동작을 바꾸지 않는 이동을 먼저 수행합니다.
3. import·wiring·테스트 탐색 설정을 같은 변경에서 갱신합니다.
4. 관련 테스트와 파일 단위 검증을 모아서 실행한 뒤 전체 품질 gate를 실행합니다.
5. 공개 계약이나 운영 절차가 바뀐 경우에만 관련 문서와 체크리스트를 함께 수정합니다.
6. 변경 파일을 다시 읽고 `git diff --check`를 통과시킵니다.
7. 코드·런타임 변경이면 `docker compose config --quiet` 후 현재 Compose 프로필 전체를 재빌드·강제 재생성하고 migration·health를 확인합니다.

## 기본 품질 gate

```powershell
cd apps/web
npm run lint
npm run typecheck
npm test
npm run build
npm run test:sites

cd ../api
uv lock --check
python -m pytest
uvx --from ruff==0.12.12 ruff check --select E,F,I .
uv run --extra test python scripts/check_ruff_format_ratchet.py
uv run --frozen --extra test mypy

cd ../..
docker compose config --quiet
git diff --check
```

실제 명령은 package script와 개발 환경에 맞게 실행하되, 실패를 제외하거나 타입검사를 축소해 통과시키지 않습니다. CSS·문서만 바꾼 단계에는 Compose 재배포를 강제하지 않습니다.

## 의도적으로 하지 않는 작업

- 전면 재작성, 마이크로서비스화, 모노레포 도구 선행 도입
- 모든 SQLAlchemy model·Alembic migration의 선행 이동
- 모든 DB 접근을 generic repository로 감싸기
- Redux·React Query·Zod·React Router를 구조 정리만을 위해 도입하기
- 모든 기능을 한 번에 `App.tsx` 또는 하나의 거대한 hook으로 이동하기
- TypeScript 전환과 CSS Modules 전환을 같은 슬라이스에 섞기
- 기존 대형 테스트를 삭제하고 새 테스트 수가 적어진 상태를 완료로 보기
- 가장 위험한 KORAIL browser sidecar부터 분리하기
