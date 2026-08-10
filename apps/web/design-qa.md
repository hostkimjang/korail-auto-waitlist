# 모바일·태블릿 역 선택 UX 디자인 QA

## 기준 시안과 구현 캡처

- 선택한 기준 시안: `output/design-qa/selected-ux-source.png`
- 구현 비교 이미지: `output/design-qa/design-qa-comparison.png`
- 모바일 전체 선택창: `output/design-qa/design-qa-mobile-selector.png`
- 가상 키보드 높이 최종 확인: `output/design-qa/mobile-keyboard-final.png`
- 터치 태블릿: `output/design-qa/design-qa-tablet-selector.png`

## 확인 환경

- 모바일: 390×844, 가상 키보드 표시 상당 visual viewport 390×480
- 최소 폭: 320×720
- 태블릿: 1280×720 터치 포인터, 가상 키보드 표시 상당 1024×480
- 확대: 200% 확대 상당 640×720
- 브라우저: Codex 인앱 Chromium, 개발 모드 데모 데이터

실제 Android 가상 키보드를 자동으로 띄운 검증은 아니며, `visualViewport`가 줄어드는 동일한 레이아웃 조건을 브라우저 뷰포트로 재현했다. 실기기 키보드·제조사별 동작은 체크리스트의 운영 확인 항목으로 유지한다.

## 상호작용 및 접근성 확인

- 출발역 선택 후 도착역 단계로 연속 전환되고, 도착역 선택 후 선택창이 닫히며 원래 트리거로 포커스가 복원됐다.
- 검색 입력은 선택창을 열 때 자동 포커스하지 않아 불필요하게 가상 키보드를 띄우지 않는다.
- 검색을 시작한 뒤에는 검색어 지우기와 출발역→도착역 전환에서도 입력 포커스를 유지한다.
- 가상 키보드 표시 상당 높이에서도 제목, 경로 요약, 검색창은 고정되고 결과 목록만 스크롤됐다.
- IME 조합 중 Enter를 무시하고, Escape 닫기, Tab 포커스 트랩, 방향키 활성 항목 자동 스크롤을 회귀 테스트로 확인했다.
- 320px, 200% 확대 상당, 터치 태블릿에서 문서 가로 넘침이 없고 44px 이상 주요 행동 영역을 유지했다.
- 최종 새 탭에서 브라우저 console error·warning은 0건이었다.

## 시각 충실도 검토

- 타이포그래피: 기존 레일웨잇 서체 크기·굵기 위계를 유지했다.
- 간격과 구조: 기준 시안의 상단 진행 단계, 출발·도착 경로, 지역 rail, 단일 열 결과 구조를 반영했다.
- 색상: 기존 navy·teal 토큰과 선택 상태를 그대로 사용했다.
- 이미지와 아이콘: 기존 Phosphor 아이콘과 브랜드 자산을 사용했으며 임시 도형·문자 아이콘은 추가하지 않았다.
- 문구: `출발역을 선택하세요`/`도착역을 선택하세요`와 검색 결과 개수를 짧게 노출했다.
- 반응형: 모바일뿐 아니라 포인터가 거친 태블릿에서도 전용 전체 화면 선택창을 사용한다.

기준 시안보다 출발·도착 경로 카드를 가로로 더 압축한 것은 의도한 차이다. 가상 키보드가 올라온 짧은 화면에서 결과 목록 높이를 확보하는 편이 역 탐색 성공률에 더 중요하다고 판단했다. 나머지 계층, 색상, region rail, 고정 검색 구조는 기준 시안과 일치한다.

## 768px 세로 태블릿·모달 재검증

- 문제 캡처: `output/design-qa/tablet-responsive-source.jpg`
- 수정 후 캡처: `output/design-qa/tablet-768-calendar-after.png`
- 전후 결합 비교: `output/design-qa/tablet-responsive-comparison.png`
- 모바일 캘린더 열림: `output/design-qa/mobile-calendar-sheet-before-drag.png`
- 모바일 드래그 닫힘 뒤: `output/design-qa/mobile-calendar-after-drag-dismiss.png`
- 모바일 역 선택 스크롤 잠금: `output/design-qa/mobile-station-dialog-scroll-lock.png`
- 알림센터·캘린더 최종 레이어: `output/design-qa/tablet-calendar-notification-stacking-final.png`

768×800과 768×1024에서 데스크톱 사이드바가 숨겨지고 모바일 헤더·하단 내비게이션이 노출됐다. 여정의 날짜·시간 영역은 한 열로 쌓였고 문서 `scrollWidth`는 viewport와 같았다. 달력은 520px 고정 모달로 화면 안쪽에 들어왔으며 768×800 기준 아래쪽 좌표가 697px라 잘리지 않았다.

390×844에서는 달력이 하단 바텀시트로 표시됐다. 24px 짧은 아래 드래그는 `0px`으로 복귀했고, 165px 드래그는 닫혔다. 드래그 도중에는 CSS 이동값이 포인터 이동량을 따라가며, 닫힌 뒤에는 잠금 전 스크롤 위치가 복원됐다. 캘린더·역 선택·공식 인계·공식 좌석 확인 네 모달은 공통 참조 카운트 잠금을 사용한다. 열림 중 `html/body` overflow와 overscroll을 잠그고 body를 현재 위치에 고정하며, 마지막 모달이 닫힐 때만 기존 인라인 스타일과 좌표를 복원한다.

final result: passed

---

# Windows PC PWA 제목 표시줄 아이콘 디자인 QA

## 비교 기준과 구현 근거

- 기준 이미지: `output/design-qa/pc-titlebar-source.png`
- 구현 화면 캡처: `output/design-qa/pc-icon-page-after.png`
- 기준 이미지 크기: 70×65px
- 구현 화면: CSS viewport 1440×900, device scale factor 1, 캡처 1425×891px
- 상태: Windows 설치형 PWA 제목 표시줄과 데모 모드 PC 홈 화면

기준 이미지는 Windows 설치형 PWA의 네이티브 제목 표시줄을 보여 주지만, 인앱 브라우저 캡처는 웹 콘텐츠 영역만 제공한다. 따라서 두 이미지는 같은 viewport와 같은 네이티브 window chrome 상태가 아니며, 제목 표시줄의 최종 시각 일치 여부를 직접 비교하는 근거로 사용하지 않았다. 구현 화면 캡처는 PC 본문의 브랜드 아이콘이 깨지지 않고 표시되는지와 콘솔 오류가 없는지만 확인했다.

## 확인한 수정 근거

- 일반 PC/PWA 아이콘 `app-icon-192.png`, `app-icon-512.png`의 네 모서리 alpha는 모두 0이다.
- 투명 픽셀은 전체 면적의 3% 이하이고 불투명·부분 투명 픽셀은 95% 이상이라 중앙 표·체크 형태와 아이콘 면적을 유지한다.
- maskable 아이콘과 Apple Touch 아이콘의 별도 자산 계약은 변경하지 않았다.
- 1440px PC 화면에서 앱 내부 브랜드 아이콘과 레이아웃이 정상이고 브라우저 console error·warning은 0건이었다.

## 필수 시각 항목

- 타이포그래피: 앱 본문과 제목 표시줄 제목 문구를 변경하지 않았다.
- 간격과 레이아웃: PC 본문 레이아웃과 브랜드 아이콘의 52px 슬롯을 변경하지 않았다.
- 색상: navy·teal 원본 픽셀을 유지하고 외곽 미색만 투명 처리했다.
- 이미지 품질: 두 일반 아이콘의 중앙 로고를 유지하고 둥근 모서리에 부분 alpha를 남겨 축소 시 계단 현상을 줄였다.
- 문구: 변경 없음.

## 남은 확인 항목

- Windows Chrome·Edge에 설치된 기존 PWA는 운영체제·브라우저 아이콘 캐시를 사용할 수 있다. 캐시를 갱신하거나 PWA를 재설치한 뒤 네이티브 제목 표시줄을 다시 캡처해야 최종 통과로 판정할 수 있다.
- 같은 상태의 구현 캡처가 없으므로 기준 이미지와 구현 이미지의 결합 비교는 수행하지 않았다.

final result: blocked

## 브라우저 탭 favicon 후속 수정

추가 기준 이미지 `output/design-qa/pc-tab-favicon-source.png`에서 외곽 회백색 픽셀을 다시 확인했다. 현재 192·512px 자산과 개발 서버 응답의 네 모서리는 모두 투명하고 반투명 경계도 남색이므로, 화면은 같은 `/icons/app-icon-512.png` URL에 남은 Chromium favicon 캐시와 일치했다. 또한 전체 티켓·노치·점선은 16px에서 흰 블록처럼 뭉쳐 보일 수 있었다.

탭 전용 `favicon-16.png`와 `favicon-32.png`를 새 경로로 추가하고, 작은 크기에서는 네이비 둥근 타일과 흰 체크만 유지했다. 두 자산은 실제 크기, 투명한 네 모서리, 부분 alpha, 외곽 2px의 밝은 픽셀 0개, 흰 체크의 안전 여백을 회귀 테스트로 확인한다. 열린 로컬 미리보기 두 탭을 다시 불러온 뒤 DOM에서 두 새 `sizes` 경로와 console error·warning 0건을 확인했다.

Windows 브라우저 chrome 자체의 수정 후 캡처는 자동 수집하지 못했으므로, 실제 탭에서 새 체크형 favicon이 보이는지 사용자 확인이 남아 있다.

설치형 PC PWA 제목 표시줄과 앱 내부 브랜드도 같은 캐시 문제를 반복하지 않도록 manifest `any` 192·512px와 화면 브랜드를 `app-icon-any-*-v2.png` 경로로 전환했다. manifest 링크는 `?v=2`, service worker app shell은 `railwait-shell-v4`로 올려 이전 URL과 cache storage를 함께 교체했다. maskable·Apple Touch 자산은 기존 설치 계약을 유지한다.

final result: blocked
