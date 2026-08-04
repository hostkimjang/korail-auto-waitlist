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

웹에는 이미 `main.tsx`, strict TypeScript 설정과 `domain/`, `api/`, `features/`, `shared/`의 기능별 경계가 존재합니다. `api.js` barrel과 확인된 `api -> feature` 역의존은 제거됐고 `auth`, `home`, `new-wait`, `official-handoff`, `reservations`, `settings`의 leaf 컴포넌트·hook·순수 함수도 점진 분리됐습니다. 다만 `App.jsx`와 전역 CSS가 계속 여러 화면 조립 책임을 소유하며, 남은 JS/JSX와 DTO·도메인·ViewModel 혼용은 후속 정리가 필요합니다.

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
| 7 | `App.jsx`·`api.js` 제거, `allowJs=false`, typecheck·Vitest·build·Sites 검증 통과, CSS 시각 회귀 확인 | TS와 CSS를 같은 rollback으로 묶지 않고 실패한 전환 단위만 이전 진입점 또는 stylesheet import로 복원 |
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
python -m pytest
uvx --from ruff==0.12.12 ruff check --select E,F,I .
uv run --extra test python scripts/check_ruff_format_ratchet.py

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
