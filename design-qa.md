# 디자인 QA

## 2026-08-02 예매 진행·실패 후 감시 복구 알림

- 사용자 원본: `C:/Users/kimjang/AppData/Local/Temp/codex-clipboard-38b708ea-6025-4410-93c5-587b81190283.png` (840×460px). 열차번호만 담긴 네이비 단일행 toast와 명시적 닫기 행동을 기준으로 삼았습니다.
- 동일 크기 구현 비교: `output/design-qa/reservation-recovery/failure-toast-840-final.png` (840×460px). 실제 `AppToast` 컴포넌트와 제품 CSS를 사용해 실패·감시 복구 상태를 렌더링했습니다.
- 실제 서비스 화면: `output/design-qa/reservation-recovery/home-desktop-1440.png`, `output/design-qa/reservation-recovery/home-mobile-390.png`. Compose 재생성 뒤 관리자 홈의 활동 작업 7건과 5초 부분 갱신 상태를 확인했습니다.

원본은 `00038 일반실 1회 예매를 진행하고 있습니다`만 보여 어느 날짜·구간·시각의 열차인지, 현재 무엇을 하는지 알기 어려웠습니다. 구현은 같은 상단 고정 위치와 닫기 행동을 유지하면서 `KORAIL · KTX 038 · 일반실`, `8월 3일 (월) · 대전 → 서울 · 14:35 → 15:39`, `좌석 발견 → 1회 예매 처리 → 감시 재개`를 분리했습니다. 실패 알림은 붉은 계열, 일반 진행은 네이비, 결제 필요는 성공 계열을 사용하되 모든 상태를 텍스트와 아이콘으로 함께 전달합니다.

진행 단계는 provider 내부 동작을 추정하지 않습니다. 서버가 증명할 수 있는 `좌석 발견`, `1회 예매 처리`, `공식 결과 확인`, `감시 재개`만 표시하며, `watch.reservation_result.candidate_id`와 해당 후보 문맥을 결합해 우선순위 첫 열차가 아니라 실제 시도한 열차를 안내합니다. 일반 toast는 30초, 단계형 진행·실패 toast는 60초, 결제·인증처럼 즉시 행동이 필요한 알림은 수동 닫기입니다.

실패 결과는 같은 toast key를 갱신해 성공처럼 남지 않습니다. `NOT_AVAILABLE`이면 `예매에 실패해 다시 감시 중입니다`와 `좌석이 다시 확인되면 예매를 다시 시도합니다`를 함께 표시하고, 좌석이 사라진 뒤 다시 확인되는 다음 관측 회차에서만 새 예매를 허용합니다. 결과가 불명확한 `UNKNOWN`은 중복 예매 위험 때문에 공식 예약 내역 확인을 요구하며 자동 재시도하지 않습니다. `reserving → watching` 상태 변화만으로 실패를 추정하지 않아 실제 결과가 도착하기 전에 잘못된 실패 문구가 보이지 않습니다. 모바일 390px 측정은 `clientWidth=390`, `scrollWidth=390`, toast 폭 362px이며 가로 넘침이 없습니다. 닫기 버튼은 데스크톱·모바일 모두 44×44px입니다.

원본과 구현을 같은 비교 입력에서 열어 위치·폭·모서리·색 대비·닫기 행동을 대조했습니다. 상세 정보와 단계가 추가되어 높이는 늘었지만 620px 상한과 모바일 양쪽 14px 여백을 유지해 카드 내용을 과도하게 가리지 않습니다. 실제 서비스 홈의 데스크톱·모바일 화면에서 가로 넘침이 없고, 브라우저 console warning/error는 0건입니다. 남은 P0·P1·P2 시각 또는 핵심 상호작용 결함은 없습니다.

final result: passed

## 2026-08-01 철도 계정·작업별 1회 예매·결제 대기

- 시각 원본: `docs/design/selected-responsive-ui.png` (1635×965px). 네이비·틸 소비자 앱 위계, 데스크톱 결제 카드와 모바일 티켓형 1열 구조를 기준으로 사용했습니다.
- 데스크톱 구현: `output/web-reservation-desktop.png` (1440×1000px, CSS viewport 1440×1000, DPR 1).
- 모바일 구현: `output/web-reservation-mobile.png` (375×812px), `output/web-provider-accounts-mobile.png` (375×812px), `output/web-reservation-policy-mobile.png` (375×812px). 추가 접근성 측정은 CSS viewport 320×844에서 수행했습니다.
- 상태: 데모의 `payment_required` 작업 1건은 provider 결제기한이 없는 상태, KORAIL·SRT 계정은 마스킹 아이디만 보이는 연결 상태, 새 대기 2단계는 기본 `notify_only` 상태입니다.

첫 비교에서 결제 대기 여러 건을 기존 단일 hero 하나로만 표시하면 긴급 작업이 누락될 수 있고, 결제기한이 없는데도 데모 18분 카운트다운을 만들어 실제 기한처럼 보이는 P1이 있었습니다. 모든 `payment_required` 작업을 실제 provider 기한 오름차순으로 표시하고, 기한이 없는 카드에는 숫자 countdown 없이 `공식 결제기한 확인 필요`를 표시하도록 수정했습니다. 결제 CTA는 카드마다 독립적으로 유지됩니다.

두 번째 비교에서는 철도 계정과 좌석 발견 후 행동이 기존 디자인 시스템 안에 없었습니다. 설정의 가로 메뉴·14px 패널·네이비/틸 상태색을 재사용해 KORAIL·SRT 계정 카드를 만들고, 모바일에서는 입력과 행동을 1열로 재배치했습니다. 새 대기 2단계에는 일반 선택과 1회 예매 선택을 같은 높이의 명시적 상태 카드로 배치했습니다. 선택 운영사 중 계정이 하나라도 없으면 1회 예매가 비활성화되고 기본 알림 정책으로 되돌아갑니다.

필수 표면 검토:

- 글꼴·타이포그래피: 기존 앱의 네이비 굵기, 작은 muted 보조문구, tabular countdown을 유지했습니다. 모바일 결제 제목은 25–32px, 시각은 29–36px 범위로 줄여 줄바꿈과 위계를 함께 보존했습니다.
- 간격·레이아웃: 결제 목록은 14px 간격의 독립 카드이며 모바일은 여정과 결제 행동을 세로로 분리합니다. 320px에서 `clientWidth=305`, `scrollWidth=305`로 가로 넘침이 없었습니다.
- 색상·토큰: 결제는 기존 orange, 안전한 선택은 teal, 연결 해제는 danger outline을 사용합니다. 재고 상태나 계정 인증 상태를 색만으로 전달하지 않고 텍스트와 아이콘을 함께 제공합니다.
- 이미지·아이콘: 새 raster 자산은 없고 기존 앱 아이콘과 Phosphor 아이콘만 사용했습니다. 원본의 브랜드 이미지와 아이콘 품질을 대체하는 임의 CSS 자산은 추가하지 않았습니다.
- 문구·콘텐츠: `결제 직전까지 1회 예매`와 `자동 결제는 하지 않습니다`를 같은 화면에 명시했습니다. 마스킹 아이디만 표시하고 비밀번호는 저장 후 다시 렌더링하지 않습니다.

모바일에서 보이는 계정·정책·결제 버튼은 44px 이상이며 320px 정책 카드 두 개는 각각 92px, 이전·다음은 48px, 하단 내비게이션은 57px입니다. 브라우저에서 홈 결제 CTA, 설정 철도 계정 탭, 새 대기 1→2단계 이동을 확인했고 console warning/error는 0건이었습니다. 원본에 없는 계정 설정 화면은 동일 디자인 토큰과 모바일 정보 구조를 기준으로 검토했으며 남은 P0·P1·P2 시각 또는 핵심 상호작용 결함은 없습니다.

final result: passed

## 2026-07-31 새 대기 3단계 자동 동기화 컴팩트 헤더

- 사용자 지적 원본: `C:/Users/kimjang/AppData/Local/Temp/codex-clipboard-f0d1750b-dea5-4739-ae60-22452ffc7a56.png` (1555×323px). 날짜·시간 카드 위에 같은 크기의 회색 자동 동기화 카드가 한 층 더 쌓인 상태입니다.
- 선택한 시각 기준: `output/design-qa/reference-home-current-refresh.png` (1265×712px). 홈의 `활동 중인 대기 · 전체 건수 · 원형 새로고침 · 최근 갱신` 한 줄 헤더를 동일 제품 안의 기준으로 사용했습니다.
- 데스크톱 구현: `output/design-qa/step3-compact-desktop-full.png` (1384×1361px, CSS viewport 1384×1376, 브라우저 기본 density).
- 모바일 구현: `output/design-qa/step3-compact-mobile-320-final.png`과 동기화 영역 집중 캡처 `output/design-qa/step3-compact-mobile-320-scrolled.png` (각 320×900px, CSS viewport 320×900, 콘텐츠 client width 305px, density 1).
- 동일 비교 입력: `output/design-qa/refresh-header-comparison.png` (970×170px). 위쪽은 홈 기준 헤더의 950×75px 원본 crop, 아래쪽은 새 대기 구현의 970×85px crop이며 별도 밀도 변환 없이 한 이미지에 배치했습니다.
- 검증 상태: 대전→서울, 2026-08-01, 새 대기 3단계, 09:00–13:00 결과 19편과 일반실·특실 좌석 상태가 반영된 상태입니다.

첫 비교에서 확인한 P2는 자동 동기화가 날짜·시간 입력과 같은 회색 카드 위계를 가져 결과 목록 시작을 불필요하게 아래로 밀고, 홈의 갱신 패턴과 다른 테두리 버튼·`마지막 동기화` 문구를 사용한다는 점이었습니다. 배경·테두리·큰 패딩을 제거하고 높이 44px의 인라인 헤더로 바꿨습니다. 왼쪽은 `열차 정보 자동 동기화 · 서버 snapshot · 5초`, 오른쪽은 홈과 같은 원형 아이콘과 고정 폭 `최근 갱신 HH:mm:ss`입니다.

비교 이력:

- P2 위계·밀도: 68px 회색 카드와 별도 테두리 버튼을 제거해 입력 카드보다 낮은 위계의 48px 데스크톱 상태 행으로 축소했습니다. 수정 후 날짜 카드가 바로 이어지고 홈 헤더와 같은 좌우 정렬을 사용합니다.
- P2 모바일 줄바꿈: 320px에서 한 줄 강제를 제거하고 제목·주기와 갱신 행동을 두 줄로 재배치했습니다. 측정값은 `clientWidth=305`, `scrollWidth=305`, 자동 동기화 행 폭 245px·높이 약 77px로 가로 넘침이 없습니다.
- P2 모션 일관성: 빠른 응답에서 500ms 만에 중간 회전으로 끊기던 표시를 공용 800ms 회전 계약으로 통합했습니다. 느린 응답은 다음 회전 경계에서 멈춥니다. 실제 버튼은 클릭 직후와 300ms 후 `aria-busy=true`·회전 animation, 950ms 후 `aria-busy=false`·animation 없음으로 확인했습니다.

필수 표면 검토:

- 글꼴·타이포그래피: 기존 앱 글꼴·네이비 굵기를 그대로 사용하고, 14px 제목과 12px 보조 문구로 입력 레이블보다 과도하게 커지지 않게 했습니다. 시간은 tabular 숫자와 17ch 최소 폭으로 갱신 시 흔들리지 않습니다.
- 간격·레이아웃: 데스크톱에서 홈과 같은 좌우 정렬, 모바일에서 두 줄 wrap을 사용합니다. 날짜·시간 카드의 radius·padding은 변경하지 않았고 자동 동기화만 카드 계층에서 제거했습니다.
- 색상·토큰: 새 색을 추가하지 않고 기존 네이비·틸·muted 토큰을 사용했습니다. 원형 아이콘의 44px 행동 영역과 focus 스타일을 유지합니다.
- 이미지·아이콘: 새 raster 자산은 없고 기존 Phosphor `ArrowsClockwise`와 앱 로고만 사용해 품질·브랜드 자산 회귀가 없습니다.
- 문구·콘텐츠: `최근 갱신 --:--:--/HH:mm:ss`로 홈과 통일하되 `서버 snapshot · 5초`를 남겨 자동 조회의 cache-only 성격을 숨기지 않았습니다.

수동 정상 시간표 조회와 5초 cache-only 자동 snapshot 조회는 기존의 서로 다른 callback을 그대로 사용합니다. 실제 관리자 브라우저에서 3단계 진입, 결과 렌더링, 자동 갱신 시각 변화, 수동 새로고침 회전·정지, 320px 재배치, console warning/error 0건을 확인했습니다. 현재 비교에서 남은 P0·P1·P2 시각 또는 핵심 상호작용 결함은 없습니다.

final result: passed

## 2026-07-31 대기 등록 상태·모바일 티켓 카드 최종 비교

- 사용자 원본: `C:/Users/kimjang/AppData/Local/Temp/codex-clipboard-eaa772f5-bad4-4d8d-8107-9093b7ca8447.png`, `C:/Users/kimjang/AppData/Local/Temp/codex-clipboard-7dc4a369-4521-4de1-8a56-cef99bbf8545.png`
- PC 구현: `output/registered-waits-after.png`
- 320px 모바일 구현: `output/registered-waits-mobile-320.png`

원본과 구현을 같은 비교 입력에서 확인했습니다. 원본의 P1 문제는 `매진` 재고 패널의 붉은색과 등록 상태의 틸색 테두리가 겹치고, 연한 취소 버튼과 상위 보조 텍스트 색상 규칙 때문에 `대기 등록 1건` 문구가 읽히지 않는 점이었습니다. 열차 카드의 선택 테두리는 왼쪽 틸 상태선으로 줄이고, 열차에는 흰색 전경의 `대기 등록 N건`, 실제 등록 좌석에는 `대기 등록됨`·`좌석 변화를 감시 중`, 행동에는 흰 글자와 휴지통 아이콘을 사용한 진한 위험색 `일반실/특실 대기 취소`를 적용했습니다. 재고의 `매진` 붉은색과 등록 처리 상태의 틸색은 서로 다른 정보로 유지했습니다.

모바일은 데스크톱 2열을 축소하지 않고 430px 이하에서 티켓 카드 전용 1열 구조로 전환했습니다. 운영사·열차번호와 등록 건수, 출도착 시각, 일반실, 특실 순서로 읽히며 등록된 좌석의 상태와 취소 행동을 같은 패널 안에 둡니다. 320·360·390·430px에서 `scrollWidth === clientWidth`, 두 좌석 패널 유지, 모든 카드 행동 최소 높이 44px을 실브라우저에서 확인했습니다. 320px의 활성 카드 폭은 약 261px, 좌석 패널 폭은 약 237px이며 출발·도착 시각과 전체 `대기 등록 1건` 문구가 잘리지 않습니다.

활성 DB 작업이 hydrate된 SRT 312·314·382 카드에서 등록 건수, 특실 등록 상태, 취소 행동을 확인했습니다. 320px DOM 회귀 테스트는 일반실·특실 region과 상태 문구, 한 좌석 등록 뒤의 취소 행동, 반대 좌석의 미등록 행동을 함께 검증합니다. 브라우저 warning/error는 0건이며 TypeScript 검사, 웹 단위 테스트 215건, production build가 통과했습니다.

현재 비교에서 남은 P0·P1·P2 시각 또는 핵심 상호작용 결함은 없습니다.

final result: passed

## 2026-07-30 서버 자동 좌석 조회 전환

- 비교 원본: `C:/Users/kimjang/AppData/Local/Temp/codex-clipboard-72c8223e-3f97-434a-8c38-141d81452722.png`, `C:/Users/kimjang/AppData/Local/Temp/codex-clipboard-2e2662d6-2fe3-4d53-b0c0-1acccce9b85f.png`
- 구현 화면: 새 대기 3단계의 `ServerSeatStatusPanel`과 일반실·특실 상태 카드
- 핵심 변경: 브라우저 확장 설치·페어링·`공식 좌석 상태 가져오기` 버튼을 제거하고 최초 시간표 요청이 서버 좌석 source를 함께 실행하도록 변경했습니다.

실제 관리자 세션에서 서울→부산 KORAIL과 수서→부산 SRT를 각각 2026-07-31 12:00–18:00 조건으로 확인했습니다. 조회 중에는 하나의 `좌석 상태 자동 조회 중` 상태만 표시되고 사용자 브라우저 설치나 별도 입력을 요구하지 않습니다. KORAIL 상류 보호 응답은 모든 카드에 `조회 제한`으로 명확히 표시하고 좌석 행동을 열지 않았습니다. SRT는 14개 열차·28개 좌석 등급을 서버에서 관측해 `좌석 상태 자동 반영 완료`, `매진`, `일반실/특실 취소표 대기`까지 자동 반영했습니다.

원본에서 가장 큰 P1은 사용자가 확장을 직접 설치해야만 상태를 가져올 수 있다는 점이었습니다. 활성 UI에서 확장·연결 설정·수동 가져오기 문구를 모두 제거했고, 레거시 코드는 기존 데이터 호환 범위에만 남겼습니다. 브라우저 DOM에서 `확장`과 `공식 좌석 상태 가져오기` 활성 문구가 각각 0건임을 확인했습니다. 실제 흐름의 console warning/error도 0건입니다.

상태 기반 행동은 그대로 유지합니다. `available`, `limited`, `standing_plus_seat`는 공식 예매, `sold_out`은 취소표 대기, `waitlist_available`은 공식 예약대기와 대기 등록, `not_offered`는 비활성, 미관측·접근 제한은 행동 없음입니다. 일반실과 특실은 독립적으로 표시·등록됩니다.

현재 비교에서 남은 P0·P1·P2 시각·핵심 상호작용 결함은 없습니다. KORAIL 양성 좌석 관측은 상류 접근 제한 때문에 아직 운영 검증되지 않았으며 실제 상태를 추정하지 않는 것이 의도된 실패 폐쇄 동작입니다.

final result: passed

## 2026-07-30 KORAIL 좌석 상태·CTA 최종 비교

- 시각 원본: `C:/Users/kimjang/AppData/Local/Temp/codex-clipboard-f5901193-4b70-4c58-bb6a-85585956ea9a.png` (1394×1206px)
- PC 구현: `docs/design/qa/seat-import-desktop-1362-results.png` (1347×1193px, CSS viewport 1362×1206, scale 1)
- 모바일 구현: `docs/design/qa/seat-import-mobile-390-viewport.png` (390×844px, CSS viewport 390×844, scale 1), 가져오기·첫 카드 집중 캡처 `docs/design/qa/seat-import-mobile-390-detail.png`, `docs/design/qa/seat-import-mobile-320-viewport.png` (320×844px, CSS viewport 320×844, scale 1)
- 상태: 대전→서울, 2026-07-31, 12:00–18:00, KORAIL 28개 시간표. 좌석 snapshot을 가져오기 전의 실패 폐쇄 상태와 확장 미설치 안내를 각각 확인했습니다.

원본과 PC 구현을 같은 비교 입력에서 열어 단계 위계, 날짜·시간 도구, 결과 요약, 카드 밀도, 일반실·특실 2열과 네이비·틸 토큰을 대조했습니다. 원본의 미관측 카드마다 있던 대기 버튼은 최신 제품 결정과 충돌하므로 의도적으로 제거했고, 대신 높이 74px의 `KORAIL 좌석 상태 가져오기` 바를 결과 바로 위에 추가했습니다. 첫 카드 높이는 182px로 유지되어 원본보다 한 화면에서 더 많은 열차를 비교할 수 있습니다.

집중 영역은 새 가져오기 바와 첫 열차 카드입니다. 버튼 높이는 PC·390px·320px에서 모두 44px이고, 1440px 카드 높이는 182px입니다. 390px과 320px에서 문구·행동은 한 열로 재배치되며 측정된 수평 overflow는 모두 0px입니다. 폰트·굵기·행간, 네이비·틸·회색 상태 토큰, 14px 모서리와 얇은 경계는 기존 선택 시안과 일치합니다. 새 이미지 자산은 없고 기존 앱 아이콘과 Phosphor 아이콘만 사용하므로 이미지 품질 회귀는 없습니다. 사용자 문구는 내부 수집원 이름이나 자동 예약 성공을 노출하지 않습니다.

비교 이력:

- P1: 실제 KORAIL DOM의 좌석명 없는 `매진`과 `-`가 전체 snapshot을 실패시키던 문제를 `.gen/.spe` 식별과 제한적인 두 매진 셀 순서 fallback으로 수정했습니다. 알 수 없는 문구는 예매 가능으로 추정하지 않습니다.
- P1: 미관측 좌석에도 대기 추가가 보여 사용자가 상태를 확인하지 않고 등록할 수 있던 문제를 수정했습니다. 예매 가능·매진임박·입석+좌석은 `공식 예매`, 매진은 `취소표 대기`, 예약대기는 `공식 예약대기`, 미운영은 비활성으로 분리했습니다.
- P1: 첫 실DB 적용에서 Alembic revision이 32자 DB 컬럼을 넘는 문제를 발견했습니다. revision을 `0013_browser_standing_status`로 줄이고 길이 회귀 테스트를 추가한 뒤 실제 PostgreSQL head 적용을 확인했습니다.
- P2: 가져오기 행동이 popup에만 있어 등록 문맥이 끊기던 문제를 화면 내 단발 가져오기로 수정했습니다. 연결 origin, 공식 결과 탭 1개, 구간·날짜 불일치는 모두 실패 폐쇄합니다.

브라우저에서 여정 선택, 3단계 진입, 28개 결과 렌더링, 가져오기 버튼의 확장 미설치 오류, PC·390px·320px reflow를 확인했습니다. console warning/error는 0건입니다. 실제 Chrome unpacked extension 설치와 연결 코드 교환은 운영 확인 항목이며 이번 인앱 브라우저 QA에서는 실행하지 않았습니다.

현재 비교에서 남은 P0·P1·P2 시각 문제는 없습니다. 원본 대비 가져오기 바 추가와 미관측 대기 버튼 제거는 최신 UX 계약에 따른 의도적 차이입니다.

final result: passed

## 이번 변경의 기준

- 사용자 제공 원본: `docs/design/source/new-wait-native-controls.png`, `docs/design/source/native-date-picker.png`
- PC 구현: `docs/design/qa/journey/desktop-original-window.png`
- 모바일 구현: `docs/design/qa/journey/mobile-390x844.png`
- 모바일 달력 최종본: `docs/design/qa/journey/mobile-390x844-final.png`
- 열차 결과 PC 구현: `docs/design/qa/journey/train-results-desktop-1440x1000.png`
- 열차 결과 모바일 구현: `docs/design/qa/journey/train-results-mobile-390x844.png`
- 열차 카드·우선순위 원본: `docs/research/benchmark-audit/2026-07-29/official-screenshots/railpick-01.png`, `railpick-05.png`
- 정식 설치 앱 실화면: `docs/research/benchmark-audit/2026-07-29/live-installed-app/ticat-korail-loaded.png`, `ticat-results-visible.png`, `railpick-korail-tab.png`, `railpick-srt-tab.png`, `railpick-korail-scroll.png`
- 좌석 상태 최종 구현: `docs/design/qa/journey/seat-results-mobile-detail-320x844.png`, `seat-results-mobile-detail-390x844.png`, `seat-results-srt-waitlist-mobile-390x844.png`, `seat-results-desktop-detail-1440x1000.png`
- 나란히 비교: `docs/design/qa/journey/desktop-before-after.png`, `docs/design/qa/journey/calendar-before-after.png`
- Official Handoff 구현: `docs/design/qa/official-handoff-audit/2026-07-29/05-handoff-desktop-1440x1000.png`, `06-handoff-mobile-390x844.png`, `07-handoff-mobile-320x844.png`, `08-handoff-200pct-equivalent-720x500.png`, `11-final-deliverable.png`
- 공식 역 카탈로그 구현: `docs/design/qa/journey/station-catalog-desktop-1440x1000.png`, `station-catalog-mobile-390x844.png`, `station-catalog-mobile-320x844.png`
- 열차·좌석 즉시 등록 반응형 구현: `docs/design/qa/new-wait-immediate/2026-07-29/01-desktop-1440x1000.png`, `02-mobile-390x844.png`, `03-mobile-320x844.png`, `04-mobile-320x844-train-card.png`
- 동일 비교 입력: `docs/design/qa/official-handoff-audit/2026-07-29/09-source-vs-results-comparison.png`, `10-handoff-responsive-comparison.png`

원본 화면의 네이비·틸 위계는 유지하되 생성 흐름은 `여정 → 조건 → 열차 등록` 3단계로 줄였고 브라우저 기본 `select`·날짜·시간 picker는 사용하지 않는 방향으로 비교했습니다. 원본과 구현 캡처의 역·날짜는 각각 캡처 시점의 테스트 데이터이므로 값 자체가 아니라 컨트롤 구조, 정보 위계, 반응형 배치를 검토했습니다.

## 시각 검토

- KTX(KORAIL)와 SRT를 텍스트 배지와 운영사 카드로 분리해 색상만으로 구분하지 않습니다.
- 출발역·도착역 검색, 가운데 교환 버튼, 역-운영사 주의 문구가 하나의 여정 블록으로 읽힙니다.
- 날짜는 요일과 ISO 날짜를 함께 표시하고, 커스텀 달력은 오늘·내일·이번 주말 빠른 선택과 지난 날짜 비활성 상태를 제공합니다.
- 출발 시간은 시작·종료 값을 동시에 보이는 이중 범위와 새벽·오전·오후·저녁 프리셋으로 바꿨습니다.
- PC에서는 2열 일정 구성이며 모바일에서는 운영사·역·날짜·시간을 한 열로 재배치합니다.
- 390px 모바일 달력은 배경 scrim과 하단 시트로 표시되고 하단 내비게이션과 겹치지 않습니다.
- 원본과 구현을 한 비교 이미지에서 확인했으며 잘린 CTA, 가로 넘침, 겹친 레이블, 기본 브라우저 달력 노출은 없습니다.
- RailPick 정식 설치 화면의 `운영사·열차번호 → 출도착 시각 → 일반실·특실 독립 상태·행동` 정보 위계와 최종 390px·1440px 구현을 같은 비교 입력에서 대조했습니다. 티캣의 `매진 임박`, `매진`, `입석+좌석`과 레일픽의 `예약 가능`, `특실 없음`, `취소표 감시`를 상태 계약에 반영하되, 실제 관측값이 아닌 화면 예시는 `벤치마크 데모 상태`로 표시했습니다.
- 좌석 패널은 provider의 `official_check`, `official_waitlist`, `add_to_watch`, `retry_provider`를 각각 `공식 예매 확인`, `공식 예약대기 확인`, `일반실/특실로 대기`, `좌석 상태 다시 조회`로 연결합니다. CTA는 상태 텍스트와 분리해 예약·대기·재조회를 같은 행동처럼 보이게 하지 않습니다.
- 첫 모바일 결과 검토에서 선택 트레이와 단계 CTA의 sticky 배치가 제목·카드를 가리는 P1 겹침을 발견했습니다. 두 요소를 문서 흐름형 배치로 수정한 뒤 390×844에서 다시 캡처했으며 겹침과 가로 스크롤이 사라졌습니다.
- 320px 최종 검토에서 루트 `min-width: 320px`와 세로 스크롤바가 합쳐져 가로 스크롤을 만드는 P1을 발견했습니다. 루트 최소 너비를 제거하고 340px 이하에서 좌석 패널을 1열로 전환했습니다. 최종 측정은 `clientWidth=305`, `scrollWidth=305`, 모든 좌석 행동 높이 44px입니다.
- 즉시 등록 개편 뒤 결과 카드의 wide layout 기준을 viewport가 아닌 결과 컨테이너 920px로 변경했습니다. 1440px에서는 카드 높이 182px, 일반실·특실 2열과 44px 행동을 유지합니다. 390px은 `clientWidth=375`, `scrollWidth=375`, 320px은 `clientWidth=305`, `scrollWidth=305`로 가로 overflow가 없고 320px 좌석 패널·행동은 모두 1열입니다.
- 데스크톱 출발일 달력은 시간 필터와 첫 결과를 덮지 않도록 중앙 dialog와 scrim으로 띄우고, 980px 이하는 날짜·시간 도구를 한 열로 배치했습니다. 760px 이하는 시간 시작·구분·종료 3열과 전체 폭 적용 버튼을 사용합니다.

## 상호작용과 접근성

- 실제 브라우저에서 KORAIL·SRT 동시 선택, 역 검색·교환, 캘린더 열기, 1단계와 3단계의 요일 날짜 이동, 시간대 프리셋 변경을 확인했습니다.
- 여정 → 조건 → 열차 → 확인을 진행해 KORAIL·SRT 열차를 함께 선택하고 운영사별 대기가 생성되는 흐름을 확인했습니다.
- 열차의 일반실·특실을 각각 누르면 해당 `열차 + 좌석 등급` 작업이 즉시 생성·시작되고, 같은 열차의 두 등급을 모두 등록할 수 있음을 단위 테스트로 검증했습니다.
- 시간 범위를 다시 적용한 뒤에도 이미 성공한 등록 snapshot과 등록 완료 상태가 유지되고, 홈은 생성된 활동 작업을 고정 개수 제한 없이 모두 표시합니다.
- 실제 관리자 세션의 대전→서울 12:00–18:00 결과 24편에서 동일 00026 열차의 일반실과 특실을 각각 눌렀고, 등록 트레이 2행과 홈 활동 대기 2건 증가를 확인했습니다. 버튼 pending·success 상태 동안 중복 생성은 차단됩니다.
- migration `0010` 적용 뒤 실제 대전→수서 SRT 330의 관측된 `매진` 일반실·특실을 각각 눌러 등록 트레이 2행과 홈 활동 6건 전체 표시를 확인했습니다. 홈의 두 행은 `일반실/특실 · 매진 · 공식 관측 01:03`으로 구분되고, 이전 작업은 `등록 근거 없음`으로 보수적으로 표시됩니다.
- 등록 근거 홈 화면은 1440×1000에서 `clientWidth=1425`, `scrollWidth=1425`, 390×844에서 `375/375`, 320×844에서 `305/305`였고 가로 overflow가 없었습니다. 세 크기 모두 보이는 버튼 최소 높이는 44px, 320px의 모든 활동 행은 viewport 안에 있었으며 browser warning/error는 0건이었습니다.
- 320·390·1440px에서 일반실·특실 상태, 공식 예매/예약대기 CTA, 좌석별 대기 CTA와 선택 상태를 확인했습니다. 390px 이상은 2열, 320px는 1열이며 모두 가로 넘침이 없습니다.
- 달력 이동·빠른 날짜·1단계와 3단계의 요일 버튼은 모바일에서 최소 44px이며 열차 선택은 `aria-pressed`로 상태를 전달합니다.
- 시간 범위 슬라이더는 숫자 인덱스 외에 `12:00부터`, `18:00까지` 형식의 `aria-valuetext`를 제공합니다.
- native `input[type=date]`와 `input[type=time]`은 없습니다.
- 달력은 `aria-modal=true`이며 열릴 때 선택 날짜로 포커스를 옮기고 Tab·Shift+Tab을 내부에 가둡니다. Escape로 닫으면 날짜 버튼으로 포커스가 돌아오는 동작을 실제 브라우저와 단위 테스트에서 확인했습니다.
- 브라우저 console에 앱 런타임 오류나 경고가 없습니다.
- TAGO 역 계약에 맞춘 combobox는 역명·도시를 함께 보여주고 동명이역은 `node_id`로 보존합니다. 텍스트를 직접 바꾼 직후에는 다음 버튼과 교환을 비활성화하고, 목록 항목을 선택해야 다시 진행할 수 있습니다. 역 옵션은 Tab 순서에서 제외하고 input 포커스와 `aria-activedescendant` 키보드 탐색 모델을 유지합니다.
- 실제 브라우저에서 서울을 수서로 직접 입력했을 때 다음 단계가 차단되는 것, ArrowDown·Enter로 수서(서울)를 선택하면 다시 활성화되는 것, 출발·도착 교환 시 부산·수서가 함께 바뀌는 것, KTX·SRT 동시 선택 뒤에도 역 조합을 정적으로 차단하지 않는 것을 확인했습니다.
- 데모 시간표는 선택한 출발역·도착역을 카드에 그대로 반영하고 출처를 `데모 시간표`로 표시합니다. 합성 열차가 `TAGO 공식 시간표`로 보이거나 서울 출발 선택에 수서 출발 열차가 섞이던 회귀를 브라우저에서 발견해 수정했습니다.
- 열차 결과는 중첩 카드의 과도한 높이를 줄여 데스크톱에서 여정 요약과 두 좌석 등급을 좌우로 배치합니다. 1440×1000에서 카드 높이 190px·폭 942px, 390×844에서 높이 386px·폭 322px이며 페이지와 카드의 가로 넘침, 버튼 텍스트 넘침이 없고 모든 CTA는 최소 44px를 유지합니다.
- 좌석 CTA는 근거와 상태에 따라 달라집니다. 공개 API로 미관측이면 `관심 열차에 추가`, 관측된 매진이면 `취소표 대기`, 관측된 예약대기 가능이면 `예약대기`로 표시해 같은 행동처럼 보이지 않게 했습니다.

## Official Handoff 최종 비교

- 시각 원본은 사용자 제공 `docs/design/source/new-wait-native-controls.png`와 티캣·레일픽 정식 설치 앱 캡처다. 구현은 원본의 네이비·틸, 얇은 경계, 단계형 여정 구조를 이어받되 공식 인계는 별도 안전 계층이므로 원본 화면을 픽셀 복제하지 않았다.
- PC 캡처는 1440×1000 CSS px, DPR 1, 열차 KTX 033 일반실 선택·인계 dialog 열린 상태다. 모바일 캡처는 390×844와 320×844 CSS px, DPR 1, 같은 열차·같은 상태다. 720×500은 데스크톱 1440×1000을 브라우저 200%로 확대했을 때의 CSS viewport 등가 상태다.
- `09-source-vs-results-comparison.png`에서 사용자 원본과 현재 열차·좌석 결과를 한 이미지에 놓고 브랜드 색, 모서리, 입력·카드 밀도, 단계 위계를 비교했다. `10-handoff-responsive-comparison.png`에서는 같은 인계 상태의 PC modal과 모바일 bottom sheet를 한 이미지에서 비교했다.
- 첫 비교의 P1은 공식 페이지에 여정 문맥이 이어진다고 보장할 수 없는데 복사 수단이 없다는 점이었다. 여정·날짜·출도착 시각·열차·좌석 등급 요약과 복사 CTA를 dialog 안에 추가했다.
- 두 번째 비교의 P1은 모바일 긴 본문에서 CTA가 화면 아래로 밀리는 문제와 배경이 실제로 비활성화되지 않는 문제였다. 모바일 행동 footer를 sticky로 바꾸고 `.app-shell`에 `inert`·`aria-hidden`을 적용했다.
- P2로 지적된 새 탭 미표기, 약한 포커스 윤곽선, 무동작 drag handle, 기술적인 보호 문구 위계, 복사 실패 피드백을 각각 보이는 `(새 탭)` 텍스트, 불투명 3px focus ring, handle 제거, 행동 우선 문장, dialog 내부 status/alert로 수정했다.
- 독립 코드 재검토에서 발견한 임의 action URL, 다른 좌석 등급의 provenance 혼용, 복사 promise 예외, 320px 시간 행 최소폭 문제를 고정 공식 진입점, 선택 좌석 기준 설명, `true` 전용 성공 판정과 예외 처리, 380px 이하 시간 그리드 축소로 수정했다.
- 후속 재검토에서 발견한 느린 복사 결과의 close/reopen 경합은 요청 토큰과 `pending` 상태로 차단했다. 처리 중 버튼은 비활성화되고 `aria-busy`와 `복사 중…`을 표시하며, 닫힌 이전 dialog의 완료 결과는 새 dialog에 반영하지 않는다. dialog 밖 배경 scrim은 포커스 불가능한 장식 요소로 바꾸고 키보드 닫기는 dialog 내부 닫기 버튼과 Escape로만 제공한다.
- 실제 브라우저에서 dialog 열기, 초기 포커스, Escape 닫기, 트리거 포커스 복귀, 배경 `inert`·`aria-hidden` 적용·해제, 1440·390·320·720 CSS px의 수평 overflow 부재를 확인했다. 공식 새 탭 URL은 브라우저에서 반복 접근하지 않고 단위 테스트의 `window.open` 인자로 검증했다.
- 남은 P0·P1·P2는 없다. 실제 iOS Safari·Android PWA safe-area, OS별 새 탭 복귀 초점, 스크린리더 조합은 운영 기기 QA이며 현재 로컬 승인 범위를 막지 않는다.

## 검증 결과

- Web Vitest: 65 passed
- Sites worker: 4 passed
- Backend pytest: 82 passed
- Production dependency audit: 0 vulnerabilities
- Production build, Compose config, `git diff --check`: 통과

P0·P1·P2 시각·핵심 상호작용·상태 계약·달력 접근성 결함은 남아 있지 않습니다. 공식 좌석 상태를 직접 가져오지 않는 차이는 정책·데이터 경계에 따른 의도적 차이입니다. 40개 초과 결과의 목록 가상화는 실제 성능 측정 뒤 판단할 후속 항목이며 현재 핵심 여정과 반응형 사용을 막지 않습니다.

final result: passed
