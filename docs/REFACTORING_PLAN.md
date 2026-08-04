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

웹에는 이미 `main.tsx`, strict TypeScript 설정과 `domain/`, `api/`, `features/`, `shared/`의 일부 수직 슬라이스가 존재합니다. `auth`, `home`, `new-wait`, `official-handoff`, `reservations`, `settings`의 leaf 컴포넌트·hook·순수 함수도 일부 분리되어 있습니다. 다만 `App.jsx`, `api.js`, 전역 CSS가 계속 주요 조립과 공용 경계를 소유하며, 일부 `api` 모듈이 feature 표현 타입을 참조하는 경계 역전은 후속 정리가 필요합니다.

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
| 1. 규칙 고정 | 진행 | 컨벤션·계획 문서, formatter/lint/import-boundary 기준 | 이 문서와 자동 품질 gate |
| 2. 기계적 분리 | 진행 | demo fixture, 공용 API client, 작은 router/schema 이동 | 행동 변화 없는 작은 모듈 |
| 3. 웹 수직 슬라이스 | 계획 | `NewWait` form·station·timetable·registration 흐름 | feature별 TS/TSX와 회귀 테스트 |
| 4. 백엔드 정책 | 진행 | watch transition, reservation episode, reconciliation 결정 함수 | 프레임워크 비의존 domain 정책 |
| 5. 실행 경계 | 계획 | 최소 UoW/repository seam, worker pipeline 분리 | 얇은 route/task와 트랜잭션 테스트 |
| 6. provider 역할 | 계획 | timetable/observe/reserve/confirm/lifecycle 계약 분리 | capability와 adapter 역할별 검증 |
| 7. 웹 전환 종료 | 계획 | `App.tsx`, `api.js` 제거, `allowJs=false`, CSS 단계 분리 | strict TS와 모듈 경계 완성 |
| 8. 고위험 sidecar | 계획 | `korail_pydoll_browser.py` 내부 lifecycle·DOM·flow 분리 | 기존 보호·결제 전 중단 계약 보존 |

단계 상태는 코드·검증 결과와 함께 `CHECKLIST.md`에서 갱신합니다. 문서 작성만으로 구현 단계를 완료 처리하지 않습니다.

### 2026-08-04 첫 구조 슬라이스

- 기준선 완료: 루트 `output/`, Playwright 도구 상태, API pytest 임시 디렉터리와 cache를 Git 제외 대상으로 고정하고 `docker compose config --quiet`를 통과했습니다. 전체 통합 검증 뒤 `codex/clean-architecture`의 최초 commit `a5ab434`와 tag `clean-architecture-phase-1-baseline-20260804`를 만들었습니다.
- 웹 기계적 분리: `App.jsx`의 demo 데이터 책임을 `fixtures/demoData.ts`로, `api.js`의 공용 HTTP transport를 `api/client.ts`로 이동했습니다. settings API mapper의 `api -> feature` 역방향 의존을 제거하고, 알림 전용 UI와 결제기한 정책·hook·표시 UI를 실제 소유 경계로 옮겼습니다.
- 웹 경계 gate: `api`, `domain`, `shared`, feature 사이의 새 역방향 의존을 차단하는 ratchet 테스트를 추가했습니다. 착수 때 있던 허용 예외 11개를 위 슬라이스에서 모두 제거해 현재 allowlist는 비어 있습니다.
- API transport 분리: 운영 요약, UI preferences, 철도 계정·runtime 라우트와 Pydantic schema를 기능 패키지로 이동하고 중앙 `schemas.py`에는 객체 identity가 같은 compatibility re-export를 유지했습니다.
- API domain 분리: 예약 결과의 재시도·수동 확인 투영 정책을 `reservations/domain.py`로 이동하고 모든 `ReservationOutcome`을 표 기반 테스트로 고정했습니다. 모든 `domain.py`의 프레임워크·provider SDK import와 향후 application 모듈의 FastAPI import를 차단하는 gate도 추가했습니다.
- 확인된 검증: 웹 strict typecheck, Vitest 51개 파일·347건, production build와 Sites 4건을 통과했고 API 전체 pytest 949건과 Ruff 핵심 규칙·module boundary를 통과했습니다. `experimental-rail` 프로필 전체 이미지를 재빌드·강제 재생성한 뒤 migration·log-init exit 0, 장기 서비스 11개 healthy, 재생성 뒤 새 runtime 오류 표식 0건을 확인했습니다.

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
npm run typecheck
npm test
npm run build
npm run test:sites

cd ../api
python -m pytest

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
