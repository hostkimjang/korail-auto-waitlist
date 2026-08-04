# 웹 TypeScript·모듈 분리 계획

## 목적

프런트엔드를 strict TypeScript로 전환하면서 현재의 모바일·PC UX, 접근성, 공식 채널 인계, 시간표·좌석 provenance 계약을 그대로 보존합니다. 확장자만 일괄 변경하거나 하나의 거대 `App.tsx`에 타입 표기를 덧붙이는 방식은 사용하지 않습니다.

2026-08-04 구조 진단 착수 기준 주요 구조 부채는 `App.jsx` 약 2,100줄, `api.js` 1,185줄, `styles.css` 약 6,670줄이었습니다. 열 번째 수직 슬라이스를 마친 현재 `api.js`는 제거됐고 `App.jsx`는 1,010줄입니다. watch REST/SSE 동기화, watch payload·DTO·ViewModel, pause·resume·cancel·delete와 예약정책 mutation, `NewWait`의 좌석별 등록·evidence 갱신·열차 결과 카드와 좌석 표현, 알림 채널 표시·편집 상태, 설정 페이지 section 조립은 strict TypeScript 경계로 이동했습니다. 초기 demo fixture와 마법사 완료 결과는 typed factory가 canonical `MappedWatch`로 만들고, demo 시간표도 production 응답과 같은 canonical mapper를 통과합니다. `NewWait`의 나머지 단계 조립과 App의 화면 전환 shell은 아직 남아 있습니다. 줄 수는 분리 목표가 아니라 서로 다른 변경 이유가 집중된 위치를 찾는 지표로만 사용합니다.

현재 `main.tsx`, strict TypeScript와 typecheck gate는 적용되어 있습니다. `domain/`, `api/`, `features/`, `shared/` 아래에도 auth, home, new-wait, official-handoff, reservations, settings의 leaf 컴포넌트·hook·순수 함수가 일부 분리되어 있습니다. `api.js` barrel과 확인된 feature 간 역방향 import는 제거됐지만, 이는 `App.jsx` 제거, 모든 DTO/mapper 경계 완성, 전체 JS/JSX 전환이 끝났다는 뜻은 아닙니다.

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
     경고를 모두 실패 처리. 구조 슬라이스에서 실제 부채를 제거할 때마다 줄여 현재 18건만 격리
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
   - 완료: 알림 채널 CRUD·시험 전송·Web Push 수명주기를 `api/notifications.ts`, SSE 연결·history
     cutoff·정리를 `api/events.ts`로 이동하고 `App.jsx` 직접 import와 compatibility re-export 유지
   - 완료: 알림 응답을 secret-free camel ViewModel로 투영하고 kind·boolean·UTC timestamp를 검증하며,
     시험 전송의 `queued=true + event_id`와 65바이트 P-256 Web Push 공개키를 fail-closed로 확인
   - 완료: `stations.ts`의 외부 DTO·metadata·identity 검증, `timetables.ts`의 query·부분 실패·mapper,
     `seatClasses.ts`의 provenance·`unknown/not_observed` fail-closed 정규화
   - 완료: `timetables.ts`의 canonical DTO mapper가 provider·열차번호·구간과 timezone-aware 출도착
     시각을 필수로 검증하고, 선택 운임·출처·조회 시각·공식 URL은 fail-closed 정규화. demo 시간표도
     같은 `mapTimetable` 경계를 통과
   - 완료: watch payload builder·DTO 검증·provenance·공식 URL fail-closed mapping과 CRUD를
     `api/watches.ts`로 이동하고 `api.js`에는 동일 함수 객체 compatibility re-export 유지
   - 완료: 좌석 재조회는 `api/timetables.ts`, demo runtime gate는 `shared/lib/runtimeConfig.ts`로
     이동하고 모든 production·test caller를 실제 owner import로 전환한 뒤 `api.js` barrel 제거
   - 완료: production graph에서 접근할 수 없던 Browser Companion 패널과 dead snapshot/provider
     frontend API를 제거하고 module-boundary 테스트로 중앙 API barrel 재도입 차단
   - 남음: 잔여 API DTO·도메인·ViewModel 경계와 legacy JS 테스트의 strict TypeScript 전환
   - DTO validator와 mapper를 endpoint 호출과 분리해 단위 테스트
4. leaf UI 전환
   - 완료: 공용 결제기한 표시 UI를 `shared/ui`, 공유 clock hook을 `hooks/`로 이동
   - 완료: App 전용 알림 center를 `features/app` 소유로 이동해 `shared -> feature` 역방향 import 제거
   - StationCombobox, CalendarPicker, TimeRangePicker
   - 완료: strict `TrainResultCard.tsx`가 카드·좌석 표현, provenance/freshness, 좌석별 등록 상태 union과
     typed `OfficialHandoff` component 주입 경계를 소유
   - 완료: strict `NotificationChannelSettings.tsx`가 종류별 상태·비밀 입력 editor·동시 pending key,
     Web Push 기기 상태, 44px switch와 editor focus/ARIA 계약을 소유
   - 완료: strict `SettingsPage.tsx`가 설정 section union과 철도 계정·알림·화면 동작·보안·시스템
     조립을 소유하고, 공용 제목 DOM은 `shared/ui/PageHeader.tsx`로 이동. class·순서·접근성 이름과
     mount-only `initialSection`, 사용자 선택 callback, secret-free 읽기 계약을 보존
   - PriorityList
   - OfficialHandoff portal·focus·clipboard 흐름
5. 기능 상태 분리
   - 완료: `App.jsx`의 demo 계정·runtime·watch·시간표·역 카탈로그를 `fixtures/demoData.ts`로 이동
   - 완료: `NewWait`의 폼·KST 초기 날짜·날짜/요일 동기화·과거 날짜 보정·역명과 node ID의 원자적
     교환·provider 토글과 예약 정책 fail-closed 보정을 `newWaitForm.ts` 순수 모델로 이동
   - 완료: `NewWait`의 station catalog 요청·demo/공식 source·재시도·provider 변경 stale 응답 차단과
     역명/node ID fail-closed 정합성을 `useStationCatalog.ts`로 이동
   - 완료: `NewWait`의 자동 timetable search, provider 재시도, 수동 전체·cache-only 조회와 stale query
     차단을 `useTimetableSearch.ts`로 이동
   - 완료: App의 canonical watch snapshot·SSE burst·polling·상태 전이 알림·인증 만료와 stale GET
     차단을 `features/app/useWatchCollection.ts`로 이동하고 구독 lifecycle 세대를 격리
   - 완료: App의 pause·resume·cancel·delete와 예약정책 변경을 strict
     `features/app/useWatchMutations.ts`로 이동. `api/watches.ts`의 canonical `MappedWatch`를 그대로
     사용하고 demo/live snapshot 교체, 오류 toast와 cancel 재전파, 예약정책 mutation guard·정리 뒤
     refresh 순서를 계약 테스트로 고정
   - 완료: `NewWait`의 좌석별 즉시 등록·DB hydration·정확한 watch ID 취소·만료 evidence 재조회와
     1회 재시도를 `useSeatWatchRegistration.ts`로 이동
   - 남음: `NewWait`의 나머지 단계 렌더링과 선택 우선순위 leaf UI 경계
   - 남음: App의 알림·화면 전환 조립과 Home, Reservations, Auth page의 최종 feature 경계
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
