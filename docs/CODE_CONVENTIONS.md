# 코드 컨벤션

이 문서는 레일웨잇의 현재 코드 작성 규칙입니다. 저장소 전체에는 루트 `AGENTS.md`가 우선 적용되고,
작업 경로에 더 구체적인 `AGENTS.md`가 있으면 그 지침도 함께 적용합니다.

## 공통 원칙

- 파일과 모듈은 하나의 변경 이유를 갖습니다. 화면 구성, 외부 I/O, DTO 변환, 도메인 정책, 상태 전이를 한 파일에 함께 추가하지 않습니다.
- 이름은 기술보다 업무 의도를 드러냅니다. `handleData`, `utils`, `manager`처럼 범위가 불분명한 이름보다 `reconcileWatchSnapshot`, `registrationEvidence`처럼 책임을 표현합니다.
- 줄 수를 맞추기 위한 분리는 하지 않습니다. 독립적으로 설명·테스트할 수 있는 정책, 별도 상태 흐름, 외부 경계가 생길 때 추출합니다.
- 두 사용처의 계약이 실제로 같을 때만 공용화합니다. 우연히 모양이 비슷한 코드를 성급하게 `shared`나 범용 helper로 올리지 않습니다.
- 여러 boolean 조합으로 상태를 암시하지 않습니다. 문자열 union, enum, 판별 union과 명시적인 전이 함수로 유효한 상태를 제한합니다.
- 주석에는 코드가 하는 일을 되풀이하지 않고 정책의 이유, 외부 계약, 실패 시 안전한 동작을 기록합니다.
- 이동과 행동 변경을 한 단계에 섞지 않습니다. 먼저 동작을 그대로 옮기고 회귀 검증한 뒤 타입·정책을 별도 변경합니다.
- 실제로 검증하지 않은 기능은 구현 완료나 운영 검증 완료로 표현하지 않습니다.

## 웹 TypeScript와 React

### 파일과 이름

- 신규·수정 React 컴포넌트는 `.tsx`, JSX가 없는 타입·도메인·API·hook·utility는 `.ts`로 작성합니다. 런타임 경계가 요구하는 service worker와 빌드 스크립트만 `.js`·`.mjs`를 유지할 수 있습니다.
- 컴포넌트는 `PascalCase`, hook은 `use...`, 일반 함수·변수는 `camelCase`를 사용합니다.
- props 타입은 `ComponentNameProps`, 사용자 행동 callback은 `on...`으로 이름을 정합니다.
- export 함수와 hook은 명시적인 반환 타입을 사용해 공개 계약을 고정합니다.
- 기능 전용 코드는 `features/<feature>/`에 함께 둡니다. 두 기능 이상이 같은 의미와 변경 주기로 사용할 때만 `shared/`로 이동합니다.

### 타입과 데이터 경계

- strict TypeScript를 기준으로 하며 `any`, `@ts-ignore`, 무근거 type assertion, non-null assertion으로 오류를 숨기지 않습니다.
- 네트워크 응답과 저장된 JSON은 `unknown`으로 받고 API 경계에서 검증·정규화합니다. `response.json() as SomeDto`는 사용하지 않습니다.
- API의 snake_case DTO, 정규화된 도메인 모델, 화면 ViewModel을 구분합니다. mapper가 필드 변환과 fail-closed 기본값을 명시합니다.
- provider, watch status, seat class/status/action/provenance, notification kind는 문자열 union 또는 판별 union으로 제한합니다.
- 좌석 상태의 출처나 관측 시각이 계약을 만족하지 않으면 `unknown/not_observed`로 강등합니다. TAGO 시간표를 좌석 재고 근거로 변환하지 않습니다.
- `null`과 `undefined`의 의미가 다른 결제기한·관측 시각·선택 API 필드는 임의로 합치지 않습니다.

### 컴포넌트와 상태

- page·shell 컴포넌트는 화면 조립과 feature 사이의 흐름만 담당합니다. fetch, 재시도, DTO 검증, 도메인 계산은 컴포넌트 밖의 API·hook·domain 경계에 둡니다.
- 표시 컴포넌트는 typed props와 접근성 표현에 집중하고 전역 상태나 네트워크 모듈을 직접 읽지 않습니다.
- server state를 복제한 boolean 묶음보다 요청 식별자, 상태 union, reducer를 사용합니다. 늦게 도착한 응답을 버리는 query key 계약을 유지합니다.
- hook은 상태 흐름이나 생명주기를 캡슐화할 때 사용합니다. 거대한 controller hook으로 화면·네트워크·정책을 다시 합치지 않습니다.
- 접근성 이름, `aria-pressed`, focus 복원, `inert`, 44px 행동 영역, 320px와 200% 확대 계약은 구조 이동 중에도 회귀 테스트 대상으로 유지합니다.

## import와 모듈 의존성

웹의 기본 의존 방향은 다음과 같습니다.

```text
app -> features -> api/domain/shared
api -> domain/shared
domain -> shared/lib
shared/ui -> shared/lib
```

- `domain`과 `shared/lib`는 React, feature, 네트워크 호출에 의존하지 않습니다.
- `api`는 feature를 import하지 않습니다. API DTO가 feature의 표시 타입을 요구하면 도메인 계약이나 mapper 위치를 다시 정합니다.
- 한 feature가 다른 feature의 내부 파일을 직접 import하지 않습니다. 실제 공용 계약이면 `domain` 또는 `shared`로 올리고, 화면 조정이면 `app`에서 조립합니다.
- 거대한 barrel export는 만들지 않고 기본적으로 실제 소유 모듈을 직접 import합니다.
- 순환 의존이 생기면 type-only import로 숨기지 말고 소유권과 의존 방향을 바로잡습니다.
- 경로 alias는 모듈 경계를 읽기 쉽게 할 때만 사용하고, 상대 경로와 alias가 같은 경계를 중복 표현하지 않게 합니다.

백엔드의 목표 의존 방향은 다음과 같습니다.

```text
FastAPI/Celery/bootstrap -> application -> domain
persistence/provider/notification 구현 -> application이 정의한 Protocol
```

- domain은 FastAPI, Celery, SQLAlchemy, Pydantic, provider SDK를 import하지 않습니다.
- application은 유스케이스와 트랜잭션 흐름을 조정하며 HTTP status나 Celery retry 정책을 반환하지 않습니다.
- bootstrap만 구체적인 DB·provider·알림 구현을 조립합니다.
- 전환 중인 기존 평면 모듈은 호환 진입점으로 남길 수 있지만 새 정책을 그 파일에 추가하지 않습니다.

## 백엔드 Python

### 경계와 오류

- Python 3.12와 Ruff `line-length=100`을 기준으로 합니다.
- Ruff의 `E/F/I` 검사는 전체 Python 경로에 적용합니다. formatter 전환 전의 파일은 경로와
  개행을 LF로 정규화한 SHA-256이 함께 기록된 `apps/api/ruff-format-legacy.txt`에만 한시적으로
  둘 수 있습니다.
  목록에 없는 새 미포맷 파일, 수정됐지만 아직 미포맷인 기존 파일, 이미 포맷돼 불필요해진 목록
  항목은 모두 format ratchet 실패로 처리합니다. 기존 파일을 수정할 때는 목록의 해시를 갱신하지
  않고 해당 파일을 포맷한 뒤 항목을 제거합니다.
- mypy는 `strict=true`와 Python 3.12로 실행하며, 현재 오류 0인 `admin_auth/models.py`·
  `admin_auth/schemas.py`, `browser_companion/models.py`·`browser_companion/schemas.py`·
  `browser_companion/http.py`·`browser_companion/snapshot_overlay.py`와 top-level browser bridge exact facade,
  `event_stream/schemas.py`, `idempotency/models.py`, `notification_management/models.py`,
  `health/schemas.py`,
  `outbox_management/models.py`,
  `official_rail_identity.py`,
  `official_page_confirmation/application.py`·`official_page_confirmation/models.py`·
  `official_page_confirmation/schemas.py`, `provider_account_management/models.py`,
  `provider_account_management/reservation_runtime.py`,
  `provider_schema_base.py`, `provider_circuit/models.py`, provider execution contracts·models·lease
  application·lifecycle runtime, top-level `station_catalog_cache.py`·`station_visibility.py` compatibility facade,
  `timetable_management/application.py`·`timetable_management/catalog_application.py`·
  `timetable_management/catalog_http.py`·`timetable_management/contracts.py`·
  `timetable_management/http.py`·`timetable_management/korail_browser_projection.py`·
  `timetable_management/models.py`·
  `timetable_management/schemas.py`·`timetable_management/srt_live_timetable.py`·
  `timetable_management/station_visibility.py`·`timetable_management/tago_timetable_projection.py`,
  `seat_status_operations/schemas.py`,
  `provider_contracts.py`,
  provider base·execution·experimental·KORAIL browser query runtime·browser reservation policy·execution·accountless seat source·reservation
  control·search bootstrap·
  SRT identity·seat source·source runtime·station roster·
  TAGO response parser·timetable adapter와 registry
  application
  및 observation contracts·operational projection·recording·reservation payment-hold·watch transition notification
  application, observation status policy·due provider policy·due runtime·cycle·group runtime·idempotency application·watch
  create·lookup·arming·command runtime·schemas(등록 충돌·생성·부분 수정 요청)·transition
  policy/application/command/runtime·provider failure policy·watch
  update application·provider auth recovery application/runtime·provider circuit application·reservation attempt
  policy/claim/result application·reservation contracts·execution runtime·KORAIL/SRT provider confirmation·reservation
  reconciliation policy/state
  application/runtime·stale attempt recovery
  application·worker task runtime·KORAIL browser stateful provider composition shell·auth·KST 시간창·관측·예약
  순수 provider policy·KORAIL sidecar
  adapter deployment composition root·browser contracts/page contracts/protection·검색 coordinator·검색 결과 policy·
  Playwright client/search form/result
  reader·core HTTP
  replay·contracts/client/runtime/HTTP/direct CDP/Chromium test launch·Pydoll canonical
  page/auth/reservation contracts·page-safety·search snapshot/hour policy·live DOM control reader·DOM interaction·hour carousel input·
  hour carousel observation·schedule commit readback·search driver·read-only search actor·auth actor·reservation actor·
  login driver·reservation driver·Chromium lifecycle·confirmation reader·HTTP replay manager
  policy·browser·
  provider account credential leaf·persistence application·login verification·session runtime·SRT fullstack fixture·SRT sidecar
  session/wire/client/port/application/reservation/runtime/HTTP,
  `provider_registry/contracts.py`, `provider_registry/korail_search_contracts.py`·
  `provider_registry/korail_search_url_policy.py`, `provider_registry/official_url_policy.py`,
  `reservations/contracts.py`,
  `reservations/attempt_runtime.py`,
  `reservations/provider_confirmation/contracts.py`·`reservations/provider_confirmation/korail_sidecar_runtime.py`
  및 중앙 service·worker composition root, 중앙 schema/model compatibility hub,
  `watch_management/models.py`의 167개 파일을
  명시적 ratchet으로 검사합니다.
  `ignore_missing_imports`, 전역 오류 코드 비활성화,
  광범위한 `type: ignore`로 통과시키지 않습니다. 새 owner는 오류 0을 만든 뒤 대상 목록에
  추가하고 전체 legacy package가 이미 strict라고 표현하지 않습니다.
- top-level 배포/CLI composition root를 정의 파일 수만 줄이기 위해 억지로 facade와 owner로 다시 나누지 않습니다.
  route·정책·상태가 canonical owner에 있고 남은 파일이 dependency 조립과 process entrypoint만 제공한다면 exact
  import·함수·`app`/`main` 경계를 테스트로 고정한 뒤 의도적인 root로 유지합니다.
- 동결한 stateful provider/browser composition shell에는 새 순수 정책·DOM 판정·독립 상태 전이를 다시
  추가하지 않습니다. 남은 코드는 canonical actor·driver·lifecycle의 조립과 compatibility seam으로 제한하고,
  exact local definition·method inventory·production consumer·passive import 계약을 구조 테스트로 고정합니다.
- API 검증은 첫 단계에서 `uv lock --check`를 실행해 pyproject와 커밋된 lock 불일치를 테스트 전에
  차단합니다. 전체 pytest와 mypy는 `uv run --python 3.12 --frozen --extra test --extra browser`를
  공통 기반으로 사용해 Python 3.12, 커밋된 lock, 테스트·브라우저 의존성을 동일하게 고정합니다.
- FastAPI route는 인증, 요청·응답 검증, 트랜잭션 진입, application 오류의 HTTP 변환만 담당합니다.
- Pydantic schema는 transport 계약이고 도메인 객체를 대신하지 않습니다. 외부 provider 응답도 경계에서 검증한 뒤 내부 결과로 변환합니다.
- 외부 provider의 JSON row는 `object`에서 시작해 문자열 키 object인지 각 원소를 검증합니다. pagination
  aggregate에서 비정상 row를 조용히 제외하면 `totalCount`와 완전성을 증명할 수 없으므로 page 전체를
  fail-closed로 거절하고, 검증 실패 결과를 cache하지 않습니다.
- 기능 owner로 옮긴 Pydantic schema와 ORM mapper는 중앙 `schemas.py`·`models.py`에 다시 선언하거나
  subclass로 호환하지 않습니다. 기존 import가 필요하면 canonical class와 동일한 객체를 alias로 재노출하고,
  양쪽 import 순서에서 mapper·table이 한 번만 등록되는지 검사합니다. 전체 metadata를 쓰는 main·migration
  bootstrap은 모든 mapper를 등록하는 중앙 진입점을 먼저 import해야 합니다.
- PostgreSQL fencing·복구 같은 운영 검증 script는 중앙 `models.py`에서 mapper를 가져오지 않고 각 canonical
  model owner를 직접 사용합니다. 중앙 model hub 재유입 검사는 production `src`뿐 아니라 `apps/api/scripts`의
  direct·module·dynamic import 형태도 포함합니다.
- production 코드와 운영 검증 script는 중앙 `schemas.py`에서 transport·provider 계약을 가져오지 않고 각
  bounded context의 `schemas.py` 또는 leaf `contracts.py`를 직접 사용합니다. 중앙 schema hub 재유입 검사도
  `src`와 `apps/api/scripts`의 direct·wildcard·module/package attribute·alias·dynamic import를 함께 다룹니다.
- 한 transport가 다른 schema의 `default_factory`·중첩 annotation·validator 정책을 직접 캡처하면 개별 class만
  떼어내지 않습니다. 같은 변경 이유를 가진 aggregate를 한 owner로 함께 옮기고 exact identity, JSON schema,
  legacy pickle, 양방향 import 순서와 production-wide 중앙 hub 재유입 차단을 한 슬라이스에서 검증합니다.
- 양방향 relationship, self-reference, 문자열·lambda 정렬식과 외래 키로 결합된 mapper graph는 임의로 class를
  하나씩 분리하지 않습니다. 독립 lifecycle이 아닌 persistence aggregate는 같은 owner로 함께 옮기고,
  외부 aggregate 관계는 canonical mapper를 직접 import합니다. 여러 legacy/canonical import 순서에서
  `configure_mappers()`와 metadata identity를 검증한 뒤 중앙 호환 hub에는 exact alias만 남깁니다.
- persistence·순수 policy가 enum 같은 공통 결과 계약만 필요하면 Pydantic schema hub나 provider runtime을
  거치지 않는 side-effect 없는 leaf contract를 직접 사용합니다. leaf contract는 schema·ORM·FastAPI·provider
  구현을 import하지 않으며, 기존 평면 모듈은 같은 객체의 exact alias만 제공합니다. target·result처럼 더 큰
  계약을 옮길 때는 먼저 공통 URL·host 정책의 하위 의존을 분리해 feature owner가 중앙 schema hub를
  역참조하지 않게 합니다. legacy facade의 우발적 wildcard 표면이나 pickle 경로를 보존한다면 exact identity를
  테스트하고, facade attribute 재할당이 canonical 구현의 dependency injection으로 전달되는지는 명시적으로
  지원하거나 fail-closed 비지원 계약으로 고정합니다.
- 기능을 종료하더라도 이미 migration으로 만든 table과 보존 데이터는 forward migration 없이 ORM
  metadata에서 제거하지 않습니다. 보존 기간에는 feature owner의 비활성 mapper와 중앙 exact alias로
  Alembic metadata 계약을 유지합니다.
- application service는 도메인 오류를 반환하거나 발생시키며 `HTTPException`에 의존하지 않습니다.
- feature runtime은 production dependency를 canonical policy·application에서 직접 조립하고 commit·rollback·
  refresh를 호출하지 않습니다. 중앙 compatibility wrapper가 남아 있더라도 worker·route·read model의 production
  dependency로 다시 사용하지 않으며, 도메인 충돌의 HTTP status 변환은 route 경계에서 수행합니다.
- provider capability는 실제 구현, 설정 gate, 승인 근거의 교집합으로 계산합니다. 구현이나 승인이 없는데 `true`로 노출하지 않습니다.
- provider 계약은 필요에 따라 timetable, observe, reserve, confirm, lifecycle 역할로 나눕니다. 모든 구현에 넓은 단일 interface를 강제하지 않습니다.
- 비밀번호, cookie, token, 카드정보, credential fingerprint 원문을 예외, 로그, metric label, fixture, outbox에 넣지 않습니다.

### 트랜잭션과 동시성

- 트랜잭션 경계는 application 유스케이스가 소유하고 route와 worker entrypoint는 이를 호출합니다. repository 메서드마다 임의 commit하지 않습니다.
- 상태 변경과 해당 outbox 이벤트는 같은 트랜잭션에 기록합니다. 외부 알림 전송은 commit 이후 outbox 소비자가 담당합니다.
- 외부 provider 호출 동안 불필요한 DB 트랜잭션이나 row lock을 유지하지 않습니다. 다만 호출 전·결과 기록 전 lease와 fencing token 소유권은 다시 확인합니다.
- reconciliation의 retry 간격·상한은 순수 policy, confirmation 결과를 DB 상태·outbox로 반영하는 일은
  state application에 둡니다. 상위 application은 due 선택·lease·인증 actor lifecycle만 조립합니다.
- 계정과 watch를 함께 잠글 때는 `provider account -> watch` 순서를 유지합니다.
- 멱등 키, 후보 unique, availability episode fence, credential generation CAS, provider/account lease를 우회하지 않습니다.
- timezone-aware datetime과 KST 서비스 날짜를 구분합니다. naive datetime을 새로 만들거나 UTC와 서비스 날짜를 암묵적으로 변환하지 않습니다.
- commit 이후 enqueue 실패를 상태 변경 실패로 되돌리는 등 이미 확정된 DB 사실을 왜곡하지 않습니다. 재시도 가능한 영속 fallback을 유지합니다.
- generic repository를 목표로 삼지 않습니다. 테스트 가능한 경계나 다른 구현이 필요한 유스케이스에 최소한의 Protocol을 정의합니다.
- task-local wrapper가 여러 외부 resource를 소유하면 의존 source를 먼저 닫고, 앞선 종료가 실패해도 뒤쪽
  client 종료가 실행되도록 `finally`를 사용합니다. 호출자가 주입한 borrowed resource는 wrapper가 닫지
  않습니다.
- 동기 worker shell이 `asyncio.run`으로 task-local event loop를 만들면 DB engine 정리도 loop가 닫히기 전에
  같은 비동기 경계에서 끝냅니다. cleanup owner는 전역 engine이나 Celery를 직접 import하지 않고 disposer를
  callback으로 받으며, operation·cleanup이 함께 실패할 때의 예외 우선순위와 취소 전파를 계약 테스트로
  고정합니다.

## 테스트 규칙

- 행동 변경에는 실패를 재현하는 테스트 또는 계약 회귀 테스트를 추가합니다. 함수 내부 호출 순서보다 사용자 행동, 상태 전이, API 경계, DB 불변식을 검증합니다.
- 순수 domain 정책은 빠른 단위 테스트로, DTO·route·repository는 경계 테스트로, PostgreSQL 잠금·unique·CAS는 실제 DB 통합 테스트로 검증합니다.
- provider 테스트는 외부 호출을 기본으로 하지 않습니다. fixture와 fake transport로 timeout, 보호 응답, 부분 실패, 불명확 결과의 fail-closed 동작을 검증합니다.
- 테스트를 통과시키기 위해 타입검사 범위를 줄이거나 실패 테스트를 삭제하지 않습니다. 테스트 수가 줄면 삭제 근거를 문서와 변경 설명에 남깁니다.
- 웹 수직 슬라이스는 기본적으로 `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`를 실행합니다. Sites 경계를 바꾸면 `npm run test:sites`도 실행합니다.
- 웹 ESLint는 `src`·`tests`·`e2e`·`scripts`·`worker`의 JS/JSX/MJS/TS/TSX를 검사하고, 브라우저·
  테스트·Node script·Worker 전역을 분리합니다. 전환 전에 존재하던 effect/ref 경고 27건만
  `eslint-warning-baseline.json`의 파일·규칙·위치·소스 행 해시로 고정합니다. 새 경고, 바뀐 경고,
  해결돼 불필요해진 stale 항목은 모두 실패하며 baseline을 늘려 통과시키지 않습니다.
- API 변경은 관련 pytest를 먼저 실행하고 가능한 경우 전체 pytest를 실행합니다. Compose 경계 변경은 `docker compose config --quiet`, build, migration, health를 확인합니다.
- 접근성·반응형 변경은 키보드, 스크린리더 이름, 44px 행동 영역, 320px, 200% 확대, 가로 넘침을 관련 범위에서 확인합니다.

## 문서와 변경 관리

- 기능, 상태, API, 환경변수, 운영 절차, 안전 경계를 바꾸면 같은 작업에서 관련 문서와 `CHECKLIST.md`를 갱신합니다.
- 확인된 사실, 설계 목표, 운영 환경에서 미검증인 항목을 구분합니다. 코드가 존재하는 것과 운영에서 검증된 것을 같은 완료 상태로 기록하지 않습니다.
- `.env.example`에는 값이 아니라 형식, 필수 여부, 생성 방법만 기록합니다.
- 파일 이동은 import, 테스트 탐색, Vite/Vitest 또는 Python packaging 설정까지 한 수직 슬라이스에서 함께 갱신합니다.
- CSS 분리와 TypeScript 전환, 파일 이동과 정책 변경처럼 원인이 다른 위험을 같은 변경에 섞지 않습니다.
- 기존 사용자 변경과 관계없는 dirty worktree를 보존합니다. 단계 종료 전 변경 파일을 다시 읽고 `git diff --check`를 실행합니다.
- 코드·런타임 계약을 바꾼 뒤에는 프로젝트 운영 지침에 따라 현재 Compose 프로필 전체를 재빌드·강제 재생성하고 migration·health를 확인합니다. 문서나 CSS만 바꾼 경우에는 재배포를 강제하지 않습니다.
