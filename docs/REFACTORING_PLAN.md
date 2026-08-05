# 클린 구조 리팩터링 계획

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
| 6. provider 역할 | 계획 | timetable/observe/reserve/confirm/lifecycle 계약 분리 | capability와 adapter 역할별 검증 |
| 7. 웹 전환 종료 | 진행 | `App.tsx`, `allowJs=false`, CSS 단계 분리 | strict TS와 모듈 경계 완성 |
| 8. 고위험 sidecar | 계획 | `korail_pydoll_browser.py` 내부 lifecycle·DOM·flow 분리 | 기존 보호·결제 전 중단 계약 보존 |

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
