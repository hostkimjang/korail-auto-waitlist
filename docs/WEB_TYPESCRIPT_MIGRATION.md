# 웹 TypeScript·모듈 분리 계획

## 목적

프런트엔드를 strict TypeScript로 전환하면서 현재의 모바일·PC UX, 접근성, 공식 채널 인계, 시간표·좌석 provenance 계약을 그대로 보존합니다. 확장자만 일괄 변경하거나 하나의 거대 `App.tsx`에 타입 표기를 덧붙이는 방식은 사용하지 않습니다.

2026-08-04 구조 진단 착수 기준 주요 구조 부채는 `App.jsx` 약 2,100줄, `api.js` 1,185줄, `styles.css` 약 6,670줄입니다. 이후 `NewWait`의 폼 계약과 KST 날짜·요일·역 교환·provider 예약 정책 보정은 strict TypeScript 순수 모델로 분리했지만, 역 카탈로그·시간표 요청·stale response 차단·열차 선택·단계 렌더링의 최종 조립은 아직 `App.jsx`에 남아 있습니다. `App`도 SSE·작업 CRUD·알림·화면 전환을 계속 함께 담당합니다. 줄 수는 분리 목표가 아니라 서로 다른 변경 이유가 집중된 위치를 찾는 지표로만 사용합니다.

현재 `main.tsx`, strict TypeScript와 typecheck gate는 적용되어 있습니다. `domain/`, `api/`, `features/`, `shared/` 아래에도 auth, home, new-wait, official-handoff, reservations, settings의 leaf 컴포넌트·hook·순수 함수가 일부 분리되어 있습니다. 이는 기반과 몇 개 수직 슬라이스가 진행됐다는 뜻이며 `App.jsx`·`api.js` 제거, API DTO/mapper 경계 완성, feature 간 역방향 import 제거, 전체 JS/JSX 전환이 끝났다는 뜻은 아닙니다.

## 목표 구조

```text
src/
  app/                 App shell, navigation, top-level providers
  domain/              provider, station, timetable, seat, watch, notification types
  api/                 client, DTO validators, mappers, feature endpoints
  features/
    auth/
    home/
    new-wait/
    reservations/
    settings/
    official-handoff/
  shared/
    ui/                접근성 계약이 있는 공용 표현 컴포넌트
    lib/               날짜·시간·공식 URL 등 순수 함수
  fixtures/            명시적인 demo/mock 데이터
  main.tsx
```

FastAPI의 snake_case DTO와 웹 도메인 모델, 표시용 ViewModel을 동일 타입으로 취급하지 않습니다. API 경계는 JSON을 `unknown`으로 받고 validator와 mapper를 거쳐 도메인 타입을 만듭니다.

## 단계

1. TypeScript 기반
   - 완료: `typescript`, React type package, strict `tsconfig.json`, `typecheck` script
   - 완료: 진입점을 `main.tsx`로 전환하고 Vite·Vitest의 TS/TSX 경로 확장
   - 완료: 새 역방향 import와 feature 간 내부 의존 증가를 막는 module-boundary ratchet test
   - 완료: ESLint를 `src`·`tests`·`e2e`·`scripts`·`worker`에 연결하고 런타임 전역을 분리해 새
     오류를 차단. 전환 전 effect/ref 경고 27건만 위치·소스 행 해시 지문으로 고정해 신규·변경·stale
     경고를 모두 실패 처리
   - 전환 기간에만 `allowJs=true`, `checkJs=false` 유지
2. 도메인 타입과 순수 함수
   - 완료: 결제기한 순수 정책을 `domain/paymentDeadline.ts`로 이동
   - provider 표시 코드와 API provider 값을 별도 union으로 정의
   - station, timetable DTO, seat status/action/provenance, watch status, notification kind 정의
   - 날짜·KST 시간창·공식 URL·payload builder를 `.ts` 순수 함수로 이동
3. API 분리
   - 완료: 공용 credentials·CSRF·JSON/error transport를 `api/client.ts`로 이동하고 `api.js` 호환 export 유지
   - 완료: operations summary와 seat-status source mapper를 API 소유 모듈로 이동해 `api -> features/settings` 역방향 import 제거
   - 완료: 관리자 인증 상태·최초 등록·로그인·로그아웃 endpoint를 `api/auth.ts`로 이동하고
     `api.js`에는 함수 객체 identity가 같은 compatibility re-export 유지
   - `client`, `auth`, `stations`, `timetables`, `watches`, `notifications`, `events`로 분리
   - DTO validator와 mapper를 endpoint 호출과 분리해 단위 테스트
4. leaf UI 전환
   - 완료: 공용 결제기한 표시 UI를 `shared/ui`, 공유 clock hook을 `hooks/`로 이동
   - 완료: App 전용 알림 center를 `features/app` 소유로 이동해 `shared -> feature` 역방향 import 제거
   - StationCombobox, CalendarPicker, TimeRangePicker
   - SeatClassPanel, TrainResultCard, PriorityList
   - OfficialHandoff portal·focus·clipboard 흐름
5. 기능 상태 분리
   - 완료: `App.jsx`의 demo 계정·runtime·watch·시간표·역 카탈로그를 `fixtures/demoData.ts`로 이동
   - 완료: `NewWait`의 폼·KST 초기 날짜·날짜/요일 동기화·과거 날짜 보정·역명과 node ID의 원자적
     교환·provider 토글과 예약 정책 fail-closed 보정을 `newWaitForm.ts` 순수 모델로 이동
   - 남음: `NewWait`의 station catalog, timetable search, stale query 차단, selection priority와 등록 상태 hook
   - Home, Reservations, Settings, Auth page와 feature hook
6. shell과 테스트
   - 마지막에 `App.tsx`로 전환
   - 기존 대형 테스트를 feature별 `.test.tsx`·`.test.ts`로 분리
7. CSS와 JavaScript 제거
   - class name과 시각 결과를 유지한 채 tokens/base/shell/feature/responsive 순서로 분리
   - 모든 소스·테스트 전환 후 `allowJs` 제거, Vitest JS/JSX include 제거

각 단계에서 컴포넌트 이동과 동작 변경을 섞지 않습니다. 먼저 import만 바뀌는 이동을 완료하고 테스트한 뒤 타입·정책 개선을 별도 단계로 적용합니다.

## 반드시 보존할 회귀 계약

- 한 운영사 조회 실패 시 다른 운영사의 시간표와 선택 보존
- 조건 변경 뒤 늦게 도착한 자동 조회·수동 재조회 응답 폐기
- 여정·날짜·시간·운영사 변경 시 오래된 열차·좌석 선택 무효화
- KST 서비스 날짜, 시간창 양끝 포함, 요일 빠른 선택 계약
- 근거 없는 좌석 상태의 `unknown/not_observed` fail-closed
- KORAIL·SRT 공식 URL allowlist와 고정 진입점 정규화
- OfficialHandoff의 portal, `inert`, `aria-hidden`, focus trap, body scroll·실행 버튼 초점 복원
- 관리자 ID·비밀번호 등록/로그인 DTO 검증과 Push/SSE 브라우저 API 타입
- `null`과 `undefined` 의미가 다른 결제기한·관측 시각·optional API 필드
- 320px·200% 확대·44px 행동 영역·색상 외 상태 표현

## 각 단계의 완료 기준

```powershell
cd apps/web
npm run lint
npm run typecheck
npm test
npm run build
npm run test:sites
```

`dist/client/index.html`, `dist/server/index.js`, `dist/.openai/hosting.json`을 유지합니다. 현재 ESLint의
기존 경고는 새 오류를 허용하는 기준이 아니며, 지문 baseline을 늘리지 않고 해당 경고를 정리할 때마다
줄입니다. `any`, `@ts-ignore`, 타입검사 제외 범위 증가, 테스트 수 감소로 전환을 통과시키지 않습니다.
