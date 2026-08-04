# 프로젝트 작업 지침

## 적용 범위와 프로젝트 기억

- 이 파일은 저장소 전체에 적용한다. 하위 디렉터리의 `AGENTS.md`가 더 구체적인 규칙을 제공하면 두 지침을 함께 적용하고, 충돌 시 하위 지침을 우선한다.
- 작업 전 `README.md`, `docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`, `docs/POLICY_AND_SAFETY.md`, `CHECKLIST.md`와 작업 경로의 지침을 확인한다.
- `apps/web/AGENTS.md`에는 확정된 반응형 디자인, 접근성, 공식 채널 인계, 좌석 provenance, 시간표·좌석 상태 분리 등 제품 기억이 있다. 리팩터링 중에도 이 계약을 보존한다.
- 확인된 사실, 설계 제안, 운영 환경에서 미검증인 항목을 구분한다. 테스트하지 않은 기능을 완료로 기록하지 않는다.

## 클린 코드 원칙

- 이름으로 의도를 드러내고 함수·컴포넌트·모듈은 하나의 변경 이유를 갖게 한다.
- 한 파일에 화면 구성, 네트워크 I/O, DTO 변환, 도메인 정책, 상태 전이를 함께 쌓지 않는다.
- 임의 줄 수를 맞추기 위한 분리는 하지 않는다. 독립적으로 설명·테스트 가능한 책임, 재사용되는 정책, 별도 상태 흐름이 생기면 추출한다.
- 중복 제거보다 잘못된 추상화를 피하는 것이 우선이다. 두 사용처의 계약이 실제로 같을 때만 공용화한다.
- boolean 조합으로 상태를 숨기지 말고 명시적인 enum·문자열 union·판별 union과 상태 전이를 사용한다.
- 주석은 코드가 무엇을 하는지 반복하지 않고 정책의 이유, 외부 계약, 실패 시 안전한 동작을 설명한다.
- 공개 함수·API payload·상태 계약을 바꾸면 호출자, 회귀 테스트, 문서를 같은 작업에서 갱신한다.

## 프런트엔드 TypeScript 규칙

- `apps/web/src`의 신규·수정 React 컴포넌트는 `.tsx`, JSX가 없는 타입·도메인·API·hook·utility는 `.ts`로 작성한다. 신규 `.js`·`.jsx` 기능 파일은 만들지 않는다.
- 배포 경계의 service worker, Sites worker, 빌드 스크립트는 해당 런타임 요구가 있을 때 `.js`·`.mjs`를 유지할 수 있다.
- 전환된 TypeScript에는 `strict`를 적용한다. `any`, `@ts-ignore`, 무근거 type assertion, non-null assertion으로 오류를 숨기지 않는다.
- 외부 응답과 저장된 JSON은 `unknown`에서 시작해 경계에서 검증·정규화한 뒤 도메인 타입으로 바꾼다. `response.json() as SomeDto`를 사용하지 않는다.
- API의 snake_case DTO, 도메인 모델, 화면 ViewModel을 구분한다. mapper가 명시적으로 필드와 fail-closed 기본값을 결정한다.
- provider, watch status, seat class/status/action/provenance, notification kind는 문자열 union 또는 판별 union으로 고정한다. 근거 없는 좌석 상태는 `unknown/not_observed`로 강등하는 계약을 타입에도 반영한다.
- props는 `ComponentNameProps`, 사용자 행동 callback은 `on...` 이름을 사용한다. export 함수와 hook의 반환 타입은 안정적인 공개 계약으로 관리한다.
- 리팩터링은 가능한 한 `행동을 그대로 이동`하는 단계와 `타입·정책을 변경`하는 단계를 분리한다.

## 프런트엔드 모듈 경계

- 목표 구조는 `app/`, `features/`, `domain/`, `api/`, `shared/ui/`, `shared/lib/`, `hooks/`, `fixtures/`이다.
- 기능 전용 코드는 해당 `features/<feature>/`에 배치하고, 두 기능 이상에서 같은 계약으로 쓰일 때만 `shared/`로 이동한다.
- 화면 컴포넌트는 orchestration을 맡고, 표시 컴포넌트는 typed props와 접근성 표현에 집중한다. fetch·재시도·DTO 검증은 컴포넌트 밖 API/hook 경계에 둔다.
- `NewWait`는 여정·조건·열차·확인 단계, 역 카탈로그, 시간표 검색, 선택 우선순위 상태를 각각 분리한다. stale response 차단용 query key 계약은 hook으로 옮겨도 유지한다.
- 거대한 barrel export를 만들지 않고 직접 import를 기본으로 하여 순환 의존을 피한다.
- CSS는 토큰·기본 요소·shell·feature·반응형 경계로 점진 분리한다. TypeScript 전환과 CSS Modules 전환을 한 작업에 섞지 않는다.

## 백엔드 규칙

- FastAPI route는 인증·입출력 검증·트랜잭션 경계를 담당하고, 도메인 정책·provider 연동·알림 전달은 service/provider 모듈에 둔다.
- Pydantic schema와 provider capability를 명시적 계약으로 유지한다. 실제 구현과 승인 근거 없이 capability를 `true`로 표시하지 않는다.
- timezone-aware datetime, KST 서비스 날짜, idempotency, outbox, provider circuit, 예약 1회 제약과 fail-closed 동작을 보존한다.
- Python은 3.12와 Ruff `line-length=100` 기준을 따른다. 민감정보 원문을 예외·로그·테스트 fixture에 넣지 않는다.

## 테스트와 완료 기준

- 행동 변경에는 실패를 재현하는 테스트 또는 계약 회귀 테스트를 추가한다. 구현 세부보다 사용자 행동·상태 전이·API 경계를 검증한다.
- 웹 변경은 기본적으로 `npm run typecheck`, `npm test`, `npm run build`를 통과해야 한다. Sites 경계를 바꾸면 `npm run test:sites`와 필수 산출물도 확인한다.
- 접근성·반응형 변경은 키보드, 스크린리더 이름, 44px 행동 영역, 320px, 200% 확대, 가로 넘침을 관련 범위에서 확인한다.
- API 변경은 관련 pytest와 가능한 경우 전체 pytest를 실행한다. Compose·환경변수·컨테이너 변경은 `docker compose config --quiet`, build, health를 확인한다.
- 기능·코드 또는 Dockerfile·Compose·런타임 이미지 계약을 바꾼 작업을 마치면, 이전 이미지를 섞어 실행하지 않도록 현재 사용하는 Compose 프로필의 전체 서비스를 `docker compose build`와 `docker compose up -d --force-recreate`로 재빌드·재생성한다. 먼저 `docker compose config --quiet`를 통과시키고, migration의 성공 종료, 장기 서비스의 `healthy` 상태, 비밀값을 출력하지 않는 범위의 관련 로그를 확인한다. CSS·문서만 바꾼 경우에는 이 재배포를 강제하지 않으며, 어떤 경우에도 `down -v`나 volume 삭제로 데이터를 초기화하지 않는다.
- 타입검사를 제외하거나 오류를 무시해 통과시키지 않는다. 기존 테스트 수가 줄면 의도적인 삭제 근거를 남긴다.

## 문서와 체크리스트

- 기능, 상태, API, 환경변수, 운영 절차, 안전 경계를 바꾸면 관련 문서와 `CHECKLIST.md`를 같은 작업에서 동기화한다.
- `.env.example`에는 값이 아니라 형식·필수 여부·생성 방법만 기록한다. 자연스러운 한국어로 현재 구현과 미완료 항목을 구분한다.
- 완료한 검증만 `[x]`로 표시하고 실제 키·외부 채널·실기기·공개 도메인 검증은 수행 전까지 운영 확인 항목으로 남긴다.

## 비밀값과 철도 연동 안전 경계

- 실제 secret은 `.env`로 관리하며 출력·커밋·스크린샷·진단 첨부·테스트 fixture에 포함하지 않는다. `docker compose config`는 `--quiet`로만 검증한다.
- 비밀번호, 쿠키, 인증 token, 카드정보, CVC, 결제 인증정보는 URL·로그·metric label·SSE·outbox에 넣지 않는다.
- TAGO 시간표와 좌석 재고를 혼동하지 않는다. mock, `official_provider`, `not_observed` provenance를 화면과 데이터에서 보존한다.

## 변경 방식

- 기존 사용자 변경과 unrelated dirty worktree를 보존하고 작업 범위만 수정한다.
- 대규모 전환은 동작 가능한 수직 슬라이스로 나눈다. 파일 이동 시 import, 테스트, Vite/Vitest 설정, 문서를 원자적으로 갱신한다.
- 작업 종료 전 변경 파일을 다시 읽고 관련 테스트, 빌드, `git diff --check`를 확인한다.
