# 정책과 안전 경계

`PROVIDER_BLOCKED`는 보류 없음이나 좌석 소실을 증명하지 않으므로 그 결과만으로 같은 요청을 재시도하지 않습니다. 다만 제한 이후 새 성공 로그인 검증 세대가 생긴 경우에는 `AUTH_REQUIRED`와 같은 세대 fence를 적용해 한 번만 재무장합니다. `UNKNOWN`도 결과 자체로 예약을 반복하지 않고 공식 예약 내역을 최대 6회 읽기 전용으로 확인합니다. 최초 시도의 exact `NOT_FOUND`로 보류 없음이 증명된 경우에만 같은 연속 가용 구간에서 한 번 재무장하며, 그 시도도 `UNKNOWN`이면 추가 예약 호출 없이 fail-closed합니다. `FAILED`와 동일 검증 세대는 계속 fail-closed입니다.

## 확인된 사실

공개 확인 가능한 TAGO 열차 API는 시간표·역 정보용이며 잔여석·예약·결제 API는 확인되지 않았습니다.

- [TAGO 열차정보 API](https://www.data.go.kr/data/15098552/openapi.do)
- [SR 매크로 사용금지 안내](https://www.srail.or.kr/cms/archive.do?pageId=TK0409940000)
- [SR의 2026년 반복 호출 차단 정책](https://www.srail.or.kr/cms/article/view.do?pageId=KR0502000000&postNo=1588)
- [SR의 2026년 프로그램 입력 행동 탐지 발표](https://www.srail.or.kr/cms/article/view.do?pageId=KR0502000000&postNo=1557)
- [코레일 반복 접속 패턴 탐지 발표](https://info.korail.com/info/selectBbsNttView.do?bbsNo=199&integrDeptCode=&key=911&nttNo=26069&pageIndex=4&searchCnd=all&searchCtgry=&searchKrwd=)
- [SRT 공식 예약대기 안내](https://www.srail.or.kr/cms/article/view.do?pageId=TK0504000000&postNo=87)
- [KORAIL 여객운송약관·부속약관](https://www.korail.com/file/cubedata/COMMON/jfile/202504/18/2025041819647fb8f38770.pdf)

SR의 특정 차단 수치는 허용 호출량이 아닙니다. 개인용이고 호출 빈도가 낮다는 이유만으로 자동 조회·예약이 허용된다고 표현하지 않습니다.

2026년 7월 29일 자동화 브라우저에서 공식 조회를 각 1회 확인했을 때 KORAIL은 미허가 도구 사용 시 제한될 수 있다는 `CODE -8003` 안내를, SRT는 비정상 접근 등을 원인으로 한 접근 불가 안내를 표시했습니다. 같은 날 새 공식 KORAIL 화면에서 사용자 가시 동선으로 다시 1회 확인한 조회도 같은 취지의 `CODE -8002` 안내로 중단됐으며 추가 호출이나 우회를 시도하지 않았습니다. 이 증거와 공식 약관을 근거로 공식 예약 화면은 사용자 직접 열기만 제공하고 서버 폴링 대상으로 사용하지 않습니다.

## 제품 기본값

- KORAIL Chromium·SRT live source의 공식 결과를 시간표·좌석의 주 데이터 경로로 사용하고, 주 경로를 사용할 수 없을 때만 TAGO 시간표를 비차단 fallback으로 사용
- 공식 시간표 결과 카드에 조회 시각과 일반실·특실별 관측 상태를 분리하고 내부 수집원 이름은 숨김. TAGO fallback처럼 좌석 근거가 없는 결과는 두 등급 모두 `unknown/not_observed`로 유지
- 좌석별 status는 provenance와 action을 함께 검증하고, 관측 근거가 없거나 잘못된 payload는 `unknown`으로 강등
- 공식 HTTPS 링크 열기와 비밀값이 없는 여정 요약 복사로 사용자 직접 확인 지원
- SRT는 사용자 시간표 조회의 계정 없는 live source 결과, KORAIL은 선택적으로 활성화한 서버 Chromium의 공식 UI 결과 중 exact match한 열차 identity·운행시각·일반실·특실 상태만 `official_provider`로 정규화. Browser Companion snapshot은 주 UI가 사용하지 않는 레거시 호환 경로
- 한 운영사 시간표가 실패해도 다른 운영사의 성공 결과를 보존하고 실패한 운영사만 제한적으로 재조회
- 별도 자동화 모드 UI 없이 작업 생성 직후 시작 요청
- 기본 설정에서는 KORAIL·SRT `seat_monitoring=false`, `reservation_once=false`; 관측은 운영사별 3중 opt-in, 에피소드당 1회 자동 예매는 추가 운영사 플래그·활성 암호화 계정·작업별 명시 정책까지 모두 충족할 때만 허용
- 결제기한과 서비스 상태 알림
- 최종 결제는 사용자 직접 수행
- `experimental-rail`은 기본 비활성

철도 계정 연결은 회원번호·이메일·휴대전화의 비밀번호 기반 일반 로그인만 지원합니다. 저장 전에 로그인만 한 번 확인하며 실패를 자동 재시도하지 않고, 성공 전에는 새 자격증명을 DB에 쓰지 않습니다. 카카오·애플·SNS·간편인증·생체인증·모바일신분증과 비회원 예매는 상호작용·기기 인증이 필요한 별도 공식 흐름이므로 서버 자격증명으로 변환하거나 저장하지 않습니다. 로그인 확인은 시간표 조회·좌석 선택·예약·결제를 수행하지 않으며, 원문 ID·비밀번호·cookie·세션 응답을 로그나 API 응답에 남기지 않습니다.

## 상주 런타임·예약 확인·만료 안전 경계

`PROVIDER_BLOCKED`는 같은 로그인 검증 세대에서는 계속 차단합니다. 차단 시도 종료보다 새로운 성공 로그인 검증 세대가 확인된 경우에만 `AUTH_REQUIRED`와 같은 세대 fence로 그 세대에서 한 번 재무장합니다. `UNKNOWN`은 공식 확인의 exact `NOT_FOUND`가 없는 한 예약을 다시 호출하지 않으며, `FAILED`와 새 검증 세대가 없는 차단 결과도 계속 fail-closed입니다.

실험 런타임은 KORAIL과 SRT를 서로 다른 상주 sidecar로 격리하고, 각 운영사 안에서도 read-only 검색 actor와 인증·예약·공식 예약 확인 actor를 분리합니다. startup prewarm은 활성·설정된 계정을 대상으로 하며, `auth_required`나 `provider_blocked`로 남은 계정도 복구 시도에서 제외하지 않습니다. 성공은 현재 credential generation과 일치할 때만 DB에 반영하고 관련 작업을 감시 상태로 복구하며, 실패·차단·인증 필요 같은 일시 결과는 기존 성공 시각을 지우지 않습니다. 기동 뒤 새 `auth_required` 또는 `provider_blocked` revision은 상주 manager가 30초 주기로 감지합니다. 같은 generation의 actor가 이미 `ready`이면 재로그인 없이 상태만 동기화하고, 그렇지 않을 때만 provider·credential generation·DB revision별 한 번 재검증합니다. 실패한 같은 revision은 자동 반복하지 않으며 계정 변경이나 새 실패 revision이 생겨야 다시 시도합니다. 운영사 제한 뒤 감시를 복구해도 완료된 예약 attempt와 availability episode fence는 보존하여 같은 좌석을 자동 재예약하지 않습니다. 런타임 API가 공개하는 생성·검증·사용 후 경과 시간과 `locally_reusable`은 로컬 프로세스의 보수적 수명주기 관측값이며, 운영사가 보장한 고정 TTL이나 장시간 로그인 유효성 주장이 아닙니다. credential generation 이외의 ID·비밀번호·cookie·storage state·fingerprint는 응답·DB·Redis·로그·artifact에 남기지 않습니다.

`PAYMENT_REQUIRED`·`UNKNOWN` attempt의 사후 확인은 공식 보류를 읽는 reconciliation이며 그 자체가 두 번째 예약 시도는 아닙니다. 같은 credential generation의 인증 actor에서 빠른 확인을 최대 3회 수행합니다. `UNKNOWN + INCONCLUSIVE`만 5분·15분·60분 지연 확인을 이어 총 6회까지 확인하고, 이전 버전에서 빠른 3회를 이미 소모한 legacy 시도도 첫 지연 확인부터 복구합니다. `PAYMENT_REQUIRED`는 빠른 3회 계약을 유지하며, 결제기한이 경과한 경우 그 뒤 공식 예약목록의 최종 확인을 한 번 허용합니다. migration `0022_post_deadline_reconciliation`의 nullable `post_deadline_reconciled_at`과 index는 이 최종 확인이 끝났음을 표시해 재시작 뒤에도 같은 확인을 반복하지 않게 합니다. KORAIL은 동일 세션 상세 화면을 먼저 읽고 exact 근거가 없으면 공식 예약목록의 열차번호·구간·서비스일·출도착시각·인원과 유일한 미결제 행동을 대조합니다. 정상 로드된 목록에서 exact 대상이 0건일 때만 `NOT_FOUND`이고, 중복 일치·인증 실패·차단·불확실 로드는 `INCONCLUSIVE`입니다. 예약목록에 없는 좌석 등급은 확인된 근거로 만들지 않습니다. SRT 자동 예매 결과는 실제 ticket 좌석 등급과 seat count까지 요청과 일치할 때만 직접 확정하며, 불명확한 결과만 공식 예약목록과 다시 대조합니다. 어느 확인도 예매·취소·결제 동작을 호출하지 않습니다. 최초 `UNKNOWN` 시도의 exact `NOT_FOUND`는 `confirmed-absent-retry:<attempt.id>`라는 별도 근거로 같은 연속 `AVAILABLE` 구간에서 자동 예매를 딱 한 번 재무장할 수 있습니다. 재무장된 시도가 다시 `UNKNOWN`이면 추가 예약 호출을 만들지 않습니다. 결제기한 경과 `PAYMENT_REQUIRED`의 최종 exact `NOT_FOUND`, 또는 exact 미결제 행이 남아 있어도 그 공식 기한이 확인 시각 이하인 결과는 행동 가능한 보류 종료 근거입니다. `notify_only`는 `expired`, `reserve_once_before_payment`는 `watching`으로 복귀시키되 marker 뒤 확정 비가용→새 행동 가능 관측이 생긴 새 episode에서만 한 번 재시도합니다. 이전 버전이 과거 기한 exact 행에 marker만 기록한 경우에는 같은 actor의 호환성 정리 read를 한 번만 더 수행합니다. 기한 없는 exact 행·중복·인증·차단·불확실 응답은 fail-closed입니다. 미래 기한의 정확한 positive 확인만 결제 필요 인계를 복구할 수 있고, 그 경우에도 결제는 사용자가 공식 플랫폼에서 직접 수행합니다. 실제 운영 KORAIL·SRT 예약목록의 장시간 자동 검증은 완료되지 않았습니다.

작업 만료도 예정 출발시각 하나로 사실을 추정하지 않습니다. 후보는 예정·예상·실제 출발시각과 지연 분을 분리합니다. KORAIL의 정확한 지연 문구·공식 replay 지연 필드는 scheduled identity를 바꾸지 않고 estimated departure에만 투영하며, `sold_out`은 예매창 종료로 해석하지 않습니다. source·관측시각·유효기간이 모두 신선한 출발·취소·예매창 종료만 즉시 terminal 근거로 사용합니다. 신선한 지연·탑승·열린 예매창은 예정시각보다 우선합니다. 상태가 불명확하거나 terminal 근거가 stale이면 예정 출발 뒤 최대 15분만 제한 재확인하고, 그때까지 신선한 계속 운행 근거가 없으면 무기한 감시를 막기 위한 절대 horizon으로 닫습니다.

## 로컬 자동화 방어 연구

브라우저 휴리스틱의 한계는
[브라우저 자동화 방어 취약성 로컬 PoC](research/poc/browser_defense_lab/README.md)에서 합성
데이터와 loopback 서버로만 재현합니다. 이 실험은 공식 provider capability, 인계 상태 전이,
외부 철도사 호출 경로를 바꾸지 않습니다. 결과는 `클라이언트 단독 통제는 서버 권한 경계가
아니다`라는 일반적인 웹 보안 성질만 입증하며, KORAIL·SRT의 실제 내부 신호나 취약성을
확인한 것으로 기록하지 않습니다.


## 공식 확인 인계

공식 페이지는 좌석 상태를 서버가 읽는 provider가 아니라 사용자가 결과를 확정하는 외부 채널입니다. 공식 시간표·좌석 확인 인계 경로의 좌석별 CTA는 `예매 완료`가 아니라 `공식 좌석 확인`과 `대기에 추가`로 표시하고, 안정적인 공식 HTTPS 링크와 `날짜 / 출발역 → 도착역 / 열차번호 / 출발시각 / 선택 좌석 등급` 형식의 여정 요약만 제공합니다. 로그인정보, 회원번호, 쿠키, 인증 토큰은 링크·QR·클립보드에 포함하지 않습니다.

공식 페이지가 여정을 자동 입력한다고 약속하지 않으며, 페이지를 열었다는 사실을 좌석 확인·예약대기 신청·예약 완료로 전환하지 않습니다. 보호 응답별 중단 상태와 사용자 행동은 [공식 페이지 차단 시 정책 준수 대체 경로](research/OFFICIAL_HANDOFF_FALLBACKS.md)를 따릅니다. 벤치마크 앱 정적 분석은 [티캣·레일픽 구현 방식 연구](research/APP_IMPLEMENTATION_STUDY.md)의 확인·추정·미확인 구분 안에서만 참고합니다.

반복 조회·동일 패턴·IP·프로그램 입력 행동처럼 공식 자료에서 확인된 신호와 브라우저/TLS 지문처럼 미확인인 영역은 [공식 페이지 자동화 감지와 정책 준수 해결 설계](research/OFFICIAL_AUTOMATION_DETECTION.md)에 분리합니다. 공개된 임계치는 허용량이 아니라 자동 접근을 기본 경로에서 제거해야 하는 근거로만 사용합니다.

## 상태 표현 원칙

`좌석 발견`, `예약 시도`, `결제 필요`, `결제 완료`는 서로 다른 사실입니다. 알림 수신이나 임시 확보를 결제 완료 또는 승차권 발권으로 표현하지 않습니다.

`payment_required` 중 미래의 timezone-aware 실제 결제기한 또는 기한 미제공 건은 홈과 `내 예약`의 결제 필요 항목에 우선 표시하지만 결제 완료를 뜻하지 않습니다. 실제 기한이 있을 때만 카운트다운하고, 값이 없거나 timezone이 빠지면 임의의 시간을 만들지 않고 `결제기한 미제공`으로 표시합니다. 화면 시계가 기한을 넘으면 홈 긴급 집계와 결제 CTA에서 즉시 제외하고 `00:00:00`을 남기지 않습니다. 서버 정리 전인 기한 경과 기록은 `내 예약`에 `기한 경과 · 공식 확인 필요`와 실제 기한, 공식 확인 CTA로 보존하며 결제 완료나 보류 소실로 승격하지 않습니다. CTA는 저장된 공식 handoff URL을 열 뿐 결제·카드정보 입력·결제 인증을 자동화하지 않습니다. 공식 provider의 `updated_at`도 잔여석 확인 시각이나 결제기한으로 사용하지 않습니다.

예약 결과 `FAILED`·`UNKNOWN`, 이미 지난 결제기한, worker 재시작 뒤 stale `PENDING → UNKNOWN`, `PAYMENT_REQUIRED`·`RESERVED`·`PROVIDER_BLOCKED`는 그 사실만으로 같은 후보를 다시 예약할 근거가 아닙니다. 일반 `FAILED`는 attempt에 실패로 남기고 사용자에게 실패·감시 복귀를 알린 뒤 `watching`으로 되돌리며, 확인된 로그인 상태를 임의로 실패 처리하지 않습니다. `UNKNOWN`과 지난 기한은 공식 예약 내역 수동 확인 outbox도 유지합니다. 최초 `UNKNOWN`은 최대 6회의 읽기 전용 확인에서 exact `NOT_FOUND`가 저장된 경우에만 `confirmed-absent-retry:<attempt.id>`로 같은 연속 `AVAILABLE` 구간에서 한 번 재무장합니다. 이 재무장 시도가 다시 `UNKNOWN`이면 같은 규칙을 연쇄 적용하지 않습니다. `NOT_AVAILABLE`로 보류 없음이 확정된 시도와, 결제기한 후 최종 exact `NOT_FOUND`로 보류 소실이 확인된 `reserve_once_before_payment` 시도는 각각 기존 episode fence를 그대로 보존합니다. 그 marker 뒤 좌석의 확정 비가용 관측과 새 행동 가능 관측이 순서대로 생긴 새 가용성 에피소드에서만 한 번 재시도할 수 있습니다. `ReservationAttempt(candidate_id, episode_key)` 고유 제약은 같은 근거의 중복 호출을 막고, `attempt_sequence`는 후보별 시도 순서를 보존합니다. `AUTH_REQUIRED`는 이전 시도보다 새로운 성공 로그인 검증 세대에서만 한 번 재무장합니다. 사용자가 호출 중 취소한 작업과 여행이 만료된 terminal 상태, 최종 확인으로 종료된 `notify_only` 작업은 자동 복구하지 않습니다.

홈도 이 차이를 보존합니다. 최종 exact `NOT_FOUND`, 또는 exact `CONFIRMED_PAYMENT_REQUIRED`의 공식 기한이 `post_deadline_reconciled_at` marker보다 늦지 않은 과거 attempt는 더 이상 현재 결제 보류로 표시하지 않고, 보류 종료·감시 복귀·새 가용성 에피소드 대기로 안내합니다. 일반 확인의 `NOT_FOUND`, marker 없는 값, 기한 없는 exact 행, `INCONCLUSIVE`는 보류 종료로 승격하지 않으며 기존 결제 필요 또는 공식 내역 수동 확인 경계를 유지합니다.

`auth_required`는 provider가 인증 필요를 확정한 최신 작업 상태일 때만 사용자에게 표시합니다. 과거 인증 오류 이력이나 `UNKNOWN` 결과로 경고를 합성하지 않습니다. 같은 운영사의 로그인 재검증이 성공하면 재검증보다 이전의 `reservation_auth_required` 작업과, `begin_reservation_attempt` 전에 계정 preflight에서 멈춘 `provider_account_not_authenticated_before_reservation` 작업을 `scheduled`로 재개합니다. 후자는 예약 attempt가 없으므로 후보 상태와 초기 episode fence를 그대로 보존하고 다음 관측만 다시 시작합니다. 기존 `AUTH_REQUIRED` attempt는 감사 이력으로 보존하되, `last_authenticated_at`이 이전 attempt 종료보다 새로운 로그인 검증 세대에서만 해당 후보를 한 번 재무장합니다. 홈은 실제 최신 `auth_required` 행에만 컴팩트 경고와 `철도 계정` CTA를 제공하고, 재검증 뒤에는 과거 인증 실패 toast를 제거해 감시 재개 안내로 교체합니다.

로그인 확인 계정·작업의 `reserve_once_before_payment`·운영사 예약 capability가 모두 충족된 start는 응답 지연을 줄이기 위해 `process_watch_now`를 best-effort로 즉시 queue에 넣습니다. 이 깨우기 실패는 예약 권한을 넓히거나 시작 transaction을 되돌리는 근거가 아니며, 5초 beat와 5초 task expiry가 같은 DB due 상태를 backlog 없이 영속 fallback으로 처리합니다. 즉시 task와 주기 task 모두 같은 임대·circuit·unique fence를 통과합니다.

열차 카드의 압축 상태 chip은 source와 timezone이 있는 `observed_at` 등 관측 provenance 계약이 유효할 때만 세부 상태를 `예매 가능`, `매진`, `예약대기 가능`, `예매 불가`로 표시합니다. `not_observed`이거나 근거가 불완전하면 status 문자열과 관계없이 미관측 원인으로 강등합니다. TAGO fallback처럼 공개 시간표만 있는 경로에서는 일반실·특실 각각을 `status=unknown`, `provenance.kind=not_observed`로 유지합니다. 이미 운행이 끝난 시간창도 현재 재고를 추정하지 않고 `departure_window_elapsed`로 구분합니다. SRT 계정 없는 live source와 KORAIL 서버 Chromium이 출발·도착역·날짜·인원·열차번호·출도착시각을 exact match한 값만 `official_provider`로 사용합니다. 이 관측 상태와 provenance는 실행 capability와 별개이며, API는 `seat_monitoring=false`이면 `add_to_watch`와 registration evidence만 제거합니다. KORAIL·SRT는 각각의 3중 opt-in에서만 관측된 `available`·`limited`·`standing_plus_seat`를 포함한 허용 좌석에 감시 등록 행동과 evidence를 발급합니다. `official_page_browser_companion`은 레거시 snapshot 호환에만 사용하며, 어느 값도 예약 성공·다인원 좌석 보장의 근거로 재사용하지 않습니다.

서버 source는 운영사별 동시 요청 1개, 동일 조건 singleflight, 짧은 TTL cache를 강제합니다. 429와 보호·비정상 접근 응답은 서로 다른 cooldown으로 분리하고 Redis TTL로 공유하여 API 재시작·replica가 새 상류 호출을 보내지 않게 합니다. 보호 응답의 현재 기본 cooldown은 5분이며, 활성 TTL 동안은 재요청하지 않습니다. Redis 장애 때도 프로세스 메모리 fallback으로 차단을 유지합니다. timeout·상류 장애·빈 결과·parse 실패·identity 불일치는 매진으로 저장하지 않고 원인에 맞는 `unknown`으로 닫습니다. KORAIL background source는 미래 서비스일에는 병결 identity 보존을 위해 00:00부터, 당일에는 KST 현재 hour와 요청 시작 중 늦은 시각부터 조회합니다. 이미 전부 지난 당일 시간창에는 공식 browser 요청을 보내지 않습니다. 같은 구간·KST 서비스일·인원·실제 시작 시각을 singleflight로 합친 뒤 열차번호·출발시각·좌석 등급을 exact match합니다. 원문 응답, 쿠키, 세션, Authorization, CAPTCHA·대기열 정보는 로그·지표·DB에 저장하지 않습니다.

새 대기 3단계의 기본 5초 자동 동기화는 `GET /api/v1/timetable-snapshots`에서 마지막 정상 snapshot을 먼저 읽습니다. cache miss는 계속 404로 닫고 상류 요청으로 fallback하지 않습니다. cache hit이 60초 이상 지난 경우에만 API가 같은 query의 백그라운드 재검증을 최대 한 건 예약하며, 실패는 30초부터 최대 5분까지 backoff합니다. 이 수치는 안전 보장이 아니라 현재 서버 부하 제어값입니다. 브라우저 탭이나 화면 주기를 늘려도 동일 query는 singleflight로 합치고 기존 provider cooldown·접근 제한 중단을 그대로 적용합니다. 화면 표시 5~300초는 공식 철도사 조회 허가나 worker 관측 주기로 해석하지 않습니다. 별도로 저장하는 전역 좌석 관측 1~600초도 목표 cadence일 뿐 provider cache·운영사별 단일 lease·backoff·circuit·cooldown보다 우선하지 않으며, 안전 보장 수치나 호출 성공 보장으로 표현하지 않습니다. 사용자가 누르는 원형 수동 새로고침도 정상 provider 조회 한 번일 뿐 보호 정책을 우회하지 않습니다.

서비스 파일 로그도 같은 비밀값 경계를 적용합니다. JSONL formatter는 줄바꿈을 escape하고 UTC timestamp, service, level, logger, message, error type만 기록합니다. 환경에 있는 보호값, Bearer 값, `password`·`token`·`secret`·`authorization`·`cookie`·API key assignment, URL userinfo와 query string은 `[REDACTED]`로 바꾸며 traceback 본문은 파일에 넣지 않습니다. 세션·CSRF·adapter token·DB/Redis credential·TAGO key·VAPID key·webhook/Push endpoint·storage state·원문 요청/응답·HTML은 파일 로그, stdout 로그, 운영 요약 모두에 기록하지 않습니다. 파일 로그는 raw network capture 저장소가 아닙니다.

KORAIL·SRT background worker도 같은 shared source cooldown을 외부 실행 임대·fencing 안에서 확인합니다.
활성 TTL 동안 due 작업은 `next_check_at`·`cooldown_until`만 만료 시각으로 연기하고 upstream 호출과
오류 관측 행을 만들지 않습니다. preflight와 observe 사이 race는 오류 저장 전에 다시 확인합니다.
TTL 해제 뒤 stale 표시를 지우고 정상 관측을 재개하는 동작은 보호 응답 재시도나 우회가 아니라
공유 중단 상태를 모든 worker가 일관되게 지키는 fail-closed 제어입니다.

SRT의 동기 provider 호출은 asyncio timeout으로 실제 thread가 중단됐다고 가정하지 않습니다.
worker task가 source와 Redis client를 소유하고, 같은 task 내부에서만 adapter를 공유하며, 추적한
thread 호출을 provider/account 임대 해제 전에 drain합니다. task 종료 뒤에는 Redis client를 닫아
다음 event loop가 이전 loop의 연결을 재사용하지 않게 합니다. 이 수명주기 경계는 호출 간격을
위장하거나 보호 판정을 피하는 기능이 아니라 동시 호출 중복과 늦은 결과 기록을 막는 제어입니다.

관리자 인증이 필요한 `GET /api/v1/seat-status/status`는 이 좌석 조회 cooldown의
`ready|cooldown`, 허용 목록의 원인과 남은 초만 `no-store`로 공개합니다. 원문 보호 응답이나
요청 식별자는 공개하지 않으며, 이 상태를 worker의 영속 `ProviderCircuit`과 결합하거나
우회·수동 재개 신호로 사용하지 않습니다.

KORAIL 주 UI의 좌석 보강은 서버 관리형 Chromium sidecar로 이어지지만, 이 실행 경로는 기본 비활성 `experimental-rail`에서 세 가지 명시적 활성화 값이 모두 있을 때만 열립니다. sidecar의 기본 `pydoll` 엔진은 WebDriver 없이 공식 `/ticket/search/general` 화면의 보이는 역·날짜·시간·인원 컨트롤을 조작하고 조회 조건을 exact readback합니다. 성공한 official same-origin UI 요청에서만 multipart template과 해당 context의 cookie를 메모리 lease로 옮기고 Chromium을 닫습니다. 같은 출발·도착 구간의 후속 조회는 검증된 `txtGoAbrdDt`·`txtGoHour` byte span만 변경하며 다른 multipart 필드나 동적 값을 합성하지 않습니다. `KORAIL_BROWSER_ENGINE=playwright_direct_cdp`를 명시한 경우에는 기존 Chromium 직접 실행·loopback Playwright CDP·raw mouse 제출 엔진만 사용하고 HTTP replay로 전환하지 않습니다. Chromium child에는 OS 실행에 필요한 허용 목록 환경변수만 전달하며 adapter token·secret은 상속하지 않습니다.

HTTP replay는 캡처한 `https://www.korail.com` 기본 origin과 `/web_s/` path만 허용하고 redirect·userinfo·fragment를 거부합니다. 브라우저가 생성한 opaque query는 해석·재구성·변경 없이 원문 URL에 결합된 채 구간별 메모리 lease에서만 보존합니다. 요청별 timeout, 응답 2 MiB, 최대 20페이지, 단조 증가 cursor, 구간별 기본 1800초·100회 제한과 전체 최대 4개 bounded LRU 제한을 모두 적용합니다. 전역 직렬화는 유지하며 여러 lease가 동시에 상류 요청을 보내지 않습니다. TTL·횟수 만료와 선택 구간 오류는 해당 lease만, 용량 초과는 가장 오래 사용하지 않은 lease만 outbound POST 전에 폐기하고 cold UI로 시작합니다. 로그인 검증·예매·sidecar 종료는 pool 전체를 정리합니다. 401, 동일 origin 로그인 경로 redirect, 명시적인 로그인 HTML처럼 session 만료가 확인된 경우에만 선택 lease를 즉시 폐기하고 같은 read-only 요청에서 cold UI 초기화를 한 번 허용합니다. cookie 누락, capture·response schema·cursor 불일치와 그 밖의 4xx는 상태를 추정하거나 같은 요청에서 cold retry하지 않고 fail-closed합니다. 403·429·보호 marker가 확인된 경우에도 다른 엔진 전환이나 동일 검색 재제출 없이 선택 lease를 닫고 기존 cooldown을 적용합니다.

결과는 UI DOM과 replay JSON 모두에서 KTX·KTX-산천·KTX-청룡 계열만 파싱하고 출발역·도착역·날짜·성인 1명·열차번호·출발시각·일반실·특실을 exact match합니다. 혼합된 무궁화·ITX 행은 건너뛰되 선택한 KTX 행의 identity나 좌석 schema가 불완전하면 상태를 추정하지 않습니다. 수동 Chrome raw capture/import, NetFUNNEL key·ticket 계산 또는 재사용, 다른 worker·IP·계정 전환, proxy·header·User-Agent·지문 변경은 하지 않습니다. Browser Companion은 새 대기 주 UI에서 제거된 레거시 호환 경로이고 기존 snapshot도 같은 freshness·identity 검증과 fail-closed 정책을 벗어나지 않습니다.

multipart template, cookie jar, opaque path·query, header 값과 원문 응답은 lease 메모리 밖으로 내보내지 않습니다. repr·DB·Redis·파일·artifact·stdout·서비스 로그·metric label에 저장하거나 별도 secret으로 등록·회전하지 않으며 lease 만료·폐기·sidecar 종료 때 HTTP client와 함께 제거합니다. 내부 진단에는 원문 대신 허용 목록의 낮은 cardinality reason·trigger·stage만 기록합니다.

이 sidecar는 KORAIL PC UI가 지원하는 고정 desktop viewport `1440×1000`에서만 locator 계약을 적용합니다. 비동기 역 검색 결과와 출발일 dialog는 하나의 가시 대상이 짧은 안정 구간 동안 유지될 때만 선택하며, 0개·복수·불안정 대상은 `source_unavailable`으로 닫습니다. 시간 slider도 정확히 하나의 slider, 정확히 하나의 target hour, 가시·활성 navigation control을 확인한 경우에만 이동합니다. 대상이나 control이 모호·비활성·범위 밖이면 다른 selector로 추측하지 않고 클릭 0회로 중단합니다.

KORAIL 공개 `/dynaPath.do` 로더, child JS 실행 sink, 업무 요청 후보와 HTTP 200 내부 보호 결과의
단계별 관계는 [KORAIL 프론트 동적 로더 동작 플로우 보고서](research/KORAIL_FRONT_LOADER_FLOW_REPORT.md)에
별도로 기록합니다. 이 문서는 구조 이해용 산출물이며 hidden child path, 동적 업무 URL,
세션·접속 제어 값을 제품 코드에 재사용하는 근거가 아닙니다.

공개 브라우저 자동화 라이브러리는 공식 UI 조작 순서를 구현할 수 있지만, 서버가 보는 수동
사용자 세션과 완전히 같은 업무 맥락을 보장하지 않습니다. Playwright·Selenium·CDP·OS 자동화
등 접근별 가능성과 한계는 [KORAIL 수동 성공과 자동화 실패 분기점 심층 연구](research/KORAIL_MANUAL_AUTOMATION_DIVERGENCE_DEEP_DIVE.md)에
분리합니다. 이 판정은 자동화로 절대 프론트를 실행할 수 없다는 뜻이 아니라, 검증되지 않은
동일성을 좌석 상태·감시·예약 capability로 승격하지 않는다는 제품 계약입니다.

수동 Chrome CDP 캡처와 자동화 실패 캡처를 비교할 때도 같은 경계를 유지합니다. 비교 도구와 문서는
method, status, 성공/제한 분류, allowlist된 field/header 이름처럼 원문 보호값이 아닌 구조적
차이만 기록합니다. 사용자가 조작한 수동 Chrome의 동적 path, cookie, storage, NetFUNNEL 값,
request id, header 값이나 response body를 제품 코드로 가져오거나 Pydoll lease에 합치지 않습니다.
sidecar가 자기 official same-origin cold UI 성공 요청에서 직접 만든 bounded in-memory lease만 허용합니다.
확인된 차이와 접목 판정은
[KORAIL 수동 CDP 캡처와 자동화 실패 캡처 비교](research/KORAIL_MANUAL_CDP_VS_AUTOMATION_ANALYSIS.md)에 기록합니다.

시간표 provider와 worker 실행 provider도 별도 registry입니다. 사용자 요청 시 좌석 보강은 화면
표시와 등록 당시 evidence만 만들며 그 자체로 실행 capability를 열지 않습니다. KORAIL 실행
registry는 `EXPERIMENTAL_RAIL_ENABLED`, `KORAIL_BROWSER_ADAPTER_ENABLED`,
`KORAIL_SEAT_MONITORING_ENABLED`, SRT 실행 registry는 `EXPERIMENTAL_RAIL_ENABLED`,
`SRT_SEAT_STATUS_ENABLED`, `SRT_SEAT_MONITORING_ENABLED`가 모두 `true`일 때만 관측을
허용합니다. 이 경로는 provider/account DB 임대와 fencing, provider circuit, Redis
cooldown을 함께 적용합니다. 선택한 모든 운영사의 계정이 로그인 확인·활성 상태이면 새 대기 정책은 `reserve_once_before_payment`가 기본이며 사용자는 `notify_only`로 바꿀 수 있습니다. `reservation_once`는 추가 운영사 플래그가 켜진 경우에만 capability로 열리고, 활성 암호화 계정과 작업별 `reserve_once_before_payment`가 모두 있어야 호출합니다. `sold_out`은 감시를 계속하고,
관측된 예매 가능·예약대기 상태만 각각 `seat_found`·`official_waitlist`로 전이합니다. `notify_only` 작업은 두 상태를
열차 출발 전까지 계속 관측하고 같은 상태 반복 알림을 만들지 않습니다. `reserve_once_before_payment` 작업은 작업 전체 1회가 아니라 후보·가용성 에피소드별 DB 고유 fence 아래 예매 버튼을 에피소드당 한 번만 눌러 결제 전에 멈추며 결제·장바구니·취소·카드정보 입력은 실행하지 않습니다. `NOT_AVAILABLE` 뒤 좌석 소실과 새 재출현이 확인된 에피소드, `AUTH_REQUIRED` 뒤 새로운 로그인 검증 세대, 또는 결제기한 후 보류 소실 marker 뒤 확정 비가용→새 행동 가능 관측이 확인된 새 에피소드마다 제한적으로 한 번 재무장합니다. 확정 비가용 뒤 좌석이 다시 발견되면 후속 에피소드에서도 자동 예매를 계속할 수 있지만 같은 에피소드의 반복 관측은 재호출하지 않습니다. timeout·취소·만료·`FAILED`·`PROVIDER_BLOCKED`처럼 결과가 불명확하거나 보류 없음이 입증되지 않으면 같은 예매 요청을 재시도하지 않고 공식 예약 내역의 수동 확인으로 전환합니다. `UNKNOWN`도 예약을 곧바로 반복하지 않습니다. 같은 credential generation에서 빠른 3회와 5분·15분·60분 지연 확인을 합쳐 최대 6회 읽기 전용으로 확인하고, 최초 시도의 exact `NOT_FOUND`만 같은 연속 가용 구간에서 `confirmed-absent-retry:<attempt.id>`로 한 번 재무장합니다. 그 재무장 결과가 `UNKNOWN`이면 추가 예약 호출은 금지합니다. `PAYMENT_REQUIRED`의 공식 보류 확인은 기존 빠른 최대 3회와 기한 경과 뒤 marker 없는 최종 확인 1회를 유지하고, 이전 버전의 과거 기한 exact 행은 호환성 정리 확인 1회로 제한합니다. 이 확인 횟수는 예약 재시도 횟수가 아닙니다. KORAIL 현재 상세 또는 공식 예약목록과 SRT 공식 예약목록에서 미래 공식 기한을 가진 exact 미결제 보류가 확인된 경우만 handoff를 복구합니다. `NOT_FOUND` 또는 exact 행의 공식 기한이 지난 결과는 보류 종료로 처리하고, `INCONCLUSIVE`·기한 없는 exact 행·인증·차단 결과는 재예약 허가로 사용하지 않습니다. 확정적인
후속 관측에 따라 두 상태 사이를 이동하거나 `watching`으로 복귀하며 오류만으로 강등하지 않습니다. 임대 상실 뒤
도착한 결과와 미관측·보호 응답은 상태 성공 근거로 기록하지 않습니다.

KORAIL Pydoll sidecar와 SRT reservation worker의 인증·예약·공식 예약 확인 actor는 provider별 프로세스 메모리 안에서만 하나씩 유지하고 lock으로 직렬화합니다. 재사용 상태에는 credential generation과 monotonic 생성·마지막 사용 시각을 두며, KORAIL에 한해 `login_method`·`login_id`·비밀번호의 단방향 SHA-256 fingerprint도 함께 보관합니다. 원문 ID·비밀번호·cookie·storage state·token·원문 login 응답은 저장하거나 로그에 쓰지 않고, fingerprint 자체도 프로세스 메모리 밖의 로그·응답·DB·artifact에 기록하지 않습니다. 두 인증 세션은 같은 generation과 마지막 사용 기준 bounded TTL이 유효할 때만 재사용하고, KORAIL은 fingerprint까지 일치해야 합니다. credential generation 변경·TTL 만료·fingerprint 불일치·명시적인 `auth_required`에서는 해당 세션을 폐기합니다. 계정 검증은 저장될 다음 generation으로 수행하고, 검증 뒤 row-lock compare-and-swap이 같은 generation을 확인한 경우에만 암호문과 성공 상태를 저장합니다. 그 요청에서 허용된 단 한 번의 로그인 검증이 실패하거나 CAS가 충돌하면 다른 계정·엔진·재시도로 대체하지 않고 fail-closed합니다. KORAIL background·read-only 시간표 검색은 별도 검색 actor의 ephemeral browser lease와 제한된 HTTP replay lease를 사용하고, SRT 검색은 계정 없는 source를 사용합니다. 검색 actor와 인증 actor는 session material을 이전하지 않으며 검색 실패로 인증 session을 소비·폐기하지 않습니다. 공식 예약 확인도 검색 actor나 새 익명 session으로 우회하지 않고 실제 예약 시도와 같은 credential generation의 인증 actor만 사용합니다. 예약 adapter는 실제 외부 호출에 사용한 credential version을 내부 결과로 반환하며 worker는 그 version과 현재 계정이 일치할 때만 인증 상태를 반영합니다. 로그인 저장과 예약 처리 모두 provider account → watch 순서로 잠가 교착을 피하고, 늦게 도착한 과거 결과가 새 로그인 성공을 강등하지 않게 합니다. 컨테이너 재시작은 메모리 상태를 복원하지 않고 암호화 저장 credential으로 새 로그인 검증을 시작합니다.

KORAIL 좌석 클릭 판정은 관측과 같은 exact `.price_box` 경계에서 요청 좌석 등급, 명시적인 원화 가격, 공식 seat-box 상태와 live enabled control을 모두 확인합니다. `sold_out_soon`·`매진임박`은 매진과 구분해 `limited`로만 허용하고, `sold_out`·예약대기·다른 등급·가격 없는 링크는 거부합니다. 반응형 중복은 같은 정규화 좌석 등급·가격일 때만 하나로 접으며 서로 다른 가격이나 control이 남으면 클릭하지 않습니다. 이 정렬은 관측값을 예약 성공으로 승격하지 않고 예약 직전 exact DOM 재확인을 계속 요구합니다.

웹의 접속 중 알림은 결제·수동 확인·인증·좌석 발견을 우선하는 하나의 surface에서 관리하고 동일 watch의 최신 revision만 유지합니다. 같은 revision이나 더 오래된 lifecycle 결과를 다시 표시하거나 live region에서 반복 안내하지 않으며, surface를 접어도 알림을 확인·삭제하거나 정보 알림의 자동 닫힘 시간을 연장하지 않습니다. 진행 중 예매는 timed dismissal 대상이 아니고 최종 result revision이나 사용자 닫기로만 종료합니다. provider가 timezone-aware 시각으로 증명한 세션 확인·대상 재확인·좌석 선택·예약 요청 단계만 결과 카드에 완료로 표시하며 근거 없는 중간 완료를 합성하지 않습니다. 홈 활동 행은 실제 목록 컨테이너 폭으로 reflow하고 `좌석 재발견마다 자동 예매` 문구와 상태·근거·행동을 겹치지 않게 분리합니다. 정책 스위치와 pause·cancel은 44px 영역을 유지하고, 같은 열차의 일반실·특실 스위치는 읽을 수 있는 좌석 등급 이름으로 구분합니다. 이 UI 표현은 실행 capability나 예약 성공 근거를 추가하지 않습니다.

full-stack E2E는 실제 철도사와 분리된 내부 fixture만 사용합니다. test 전용 URL override는 고정
container URL과 정확히 일치할 때만 허용하고, API·worker·KORAIL sidecar와 page fixture는 외부
egress가 없는 network에 둡니다. KORAIL fixture가 좌석 snapshot API 응답을 직접 흉내 내는
경로는 사용하지 않습니다. 실제 sidecar의 기본 Pydoll 엔진이 내부 HTML page의 보이는 컨트롤을
WebDriver 없이 CDP로 조작해 렌더된 DOM을 정규화하고, API가 exact match한 결과만
`official_provider` 상태와 공식 인계 CTA로 사용합니다. 이어 SRT worker 상태 전이·실행 임대·예약 시도
0건·알림 outbox 재시도까지 검증합니다. sidecar와 page는 `browser-egress`에 연결하지 않으며 실제
KORAIL·SRT provider 호출은 0건입니다. 이 fixture 결과는 제품의 provenance·evidence·worker·
outbox 계약을 검증하는 합성 증거일 뿐 실제 좌석 상태, 승인 transport, 실제 알림 전달 성공이나
운영 capability 증거로 기록하지 않습니다.

이 full-stack 시나리오는 KORAIL·SRT 실행 `seat_monitoring`을 내부 fixture에서만 3중 opt-in으로
열고, Chromium DOM의 매진 특실 evidence·대기 등록·worker 관측과 SRT 좌석 등급 3개의 상태 전이를
함께 검증합니다. 실제 철도사 호출이나 예약 시도는 0건이어야 합니다.

선택 실행형 live smoke의 로컬 관리자 인증은 저장소 밖 storage state 또는 실행 프로세스 전용
ID·비밀번호 쌍만 허용합니다. 유효 storage session을 우선하고 만료 시 자격증명 fallback은 정확히
한 번만 수행하며, 초기 관리자 계정을 자동 생성하지 않습니다. 명시한 storage-state 파일이
유효하지 않으면 자격증명으로 조용히 대체하지 않고 provider 요청 전에 중단합니다. 자격증명은
Compose `.env`, 새 storage-state 파일, trace·video·screenshot·artifact·로그에 저장하지 않으며
live 실행에서 Playwright API debug logging을 켜지 않습니다.

2026-07-31 동일한 대전→서울, 2026-08-01, 03:00–08:00 단발 비교에서 Windows PoC의 Pydoll은 전체 10행(KTX 계열 8행, ITX 1행, 무궁화 1행)을 읽었습니다. Linux sidecar의 기본 Pydoll 엔진은 HTTP 200과 KTX 계열 8행을 반환했고 `available`, `limited`, `sold_out` 좌석 상태를 정규화했습니다. 같은 조건의 기존 `playwright_direct_cdp` 엔진은 결과 단계 `marker_code_8003`에서 내부 HTTP 423 `provider_access_restricted`로 닫혔습니다. 당시 인증된 새 대기 웹 UI에서는 KTX 계열 8행이 exact overlay되고 2행은 미관측으로 유지됐습니다. `더보기` 확장 적용 후 최신 재검증에서는 시간표 10행의 일반실·특실 20개 상태가 모두 공식 관측으로 표시됐습니다. 이는 요청 시점 단발 조회의 증거이지 공식 허가, background 감시, 예약 capability 또는 장기 안정성 증거는 아닙니다. 보호 응답을 받은 엔진은 재시도·우회 없이 Redis cooldown으로 전환하고 cooldown 동안 상류를 다시 호출하지 않습니다.

시간표 응답이 발급하는 `registration_evidence`는 등록 당시 UI가 실제로 받은 관측 상태·provenance, 실행 `seat_monitoring=true`, `add_to_watch` 허용 여부를 보존하는 감사 근거입니다. 관측값이 있어도 실행 capability가 꺼져 있거나 `unknown`·`not_observed`·mock·비허용 행동이면 ID를 발급하지 않으며, 이전 버전에서 남은 비허용 evidence도 생성 시 서버가 거부합니다. 등록 토큰이나 snapshot 자체를 실시간 좌석 관측·좌석 발견·예약대기·예약 성공의 근거로 승격하지 않습니다. worker·알림·예약은 이 snapshot을 입력으로 사용하지 않으며 공식 provider capability도 계속 별도입니다. 생성 시 근거 만료가 확인된 경우에도 보호장치 우회나 반복 폴링으로 전환하지 않습니다. 기존 좌석 refresh 경로를 한 번만 사용하고 동일 identity·상태의 새롭고 신선한 공식 근거가 없으면 등록을 중단합니다.

`provenance.kind=mock`인 상태는 벤치마크 UI와 계약 테스트에만 사용합니다. 화면에 `벤치마크 데모 상태`임을 명시하며 실제 KORAIL·SRT 관측값, 마지막 좌석 확인 시각 또는 예약 근거로 사용하지 않습니다. `official_check`·`official_waitlist`는 HTTPS와 운영사 소유 도메인을 API·웹 양쪽에서 확인하고, `add_to_watch`·`retry_provider`에는 외부 URL을 넣지 않습니다. KORAIL 조건 선입력 `official_search_url`은 일반 진입·결제용 `official_booking_url`과 별도 필드이며, 공식 host의 `/ticket/search/list`와 정확한 25개 단일 키·고정값·실제 날짜·시각·4자리 코드가 모두 맞을 때만 사용합니다. `mutMrkVrfCd`, `srtJob`, `selectedTrainList`, 로그인·cookie·token·결제값과 임의 추가·중복 query는 거부합니다. provider가 actions를 주지 않거나 URL이 검증에서 제거되면 UI도 공식 확인·대기 행동을 임의로 추가하지 않습니다.

사용자가 일반실과 특실을 각각 선택할 수 있으므로 대기 작업도 `provider + seat_class` 단위로 분리합니다. 한 좌석 등급의 상태나 성공을 다른 등급에 전파하지 않고, 열차 단위 호환 `availability`를 좌석별 판정 근거로 사용하지 않습니다.

역 카탈로그 스냅샷은 TAGO 원본 역 식별자와 KORAIL 공개 역 안내의 화면용 교집합을 분리해 보존합니다. 원본은 node ID·역명 검증에만 사용하고 화면에는 교집합만 반환하며, 교집합 생성에 실패해도 원본 목록을 fallback으로 노출하지 않습니다. 이 필터는 검색·발견 편의를 위한 것으로 KORAIL/SRT별 역 소속이나 특정 날짜의 정차를 증명하지 않으므로 `provider_membership=not_verified_by_source`를 유지합니다. 서울역·수서역을 운영사에 고정 배정하지 않고 실제 운행 여부는 선택 날짜·구간의 KTX/SRT 시간표 결과로만 판단합니다.

## 자동 예매 경쟁 소실과 접속 중 알림

`NOT_AVAILABLE`은 예약 시점에 보류가 없고 좌석도 확보되지 않았다는 확정 근거입니다. 그 뒤 처음 관측된 행동 가능 좌석에만 `not-available-retry:<attempt-id>` 경쟁 소실 보정 시도를 한 번 허용합니다. 그 보정도 `NOT_AVAILABLE`이면 동일 연속 관측으로 더 호출하지 않으며 이후에는 확정 비가용→새 행동 가능 관측의 순서가 다시 필요합니다. `UNKNOWN`·`FAILED`·`PROVIDER_BLOCKED`에는 이 예외를 적용하지 않습니다.

웹은 자동 예매 작업의 실제 `watch.reservation_attempted`가 생성된 뒤에만 진행 단계를 표시하고, 결과·수동 확인 SSE로 같은 카드를 즉시 교체합니다. 진행 카드는 자동 닫힘 대상이 아니며 최종 결과 이벤트로 교체되거나 사용자가 직접 닫을 때까지 유지합니다. 각 카드는 해당 서버 단계의 KST `HH:mm:ss`를 표시하되 유효한 시각 근거가 없으면 허위 시각을 만들지 않습니다. 따라서 빠른 `reserving`을 REST 재조회 사이에서 잃거나, 오래 걸리는 시도가 타이머 때문에 완료 전에 사라지거나, 재시도 fence 때문에 attempt가 없는 행동 가능 관측을 예약 시작으로 오인하지 않습니다.

## Official Handoff UI 안전 계약

웹 인계는 KORAIL·SRT allowlist의 안정적인 공식 HTTPS 진입점만 새 탭으로 열고, 안내 panel에서 여정 요약을 먼저 확인·복사하게 합니다. 공식 화면이 조건을 자동 입력한다고 보장하지 않으며 URL, 클립보드, 화면 요약 어디에도 계정·쿠키·토큰·결제정보를 넣지 않습니다. `mock`은 반드시 벤치마크 데모임을, `official_provider`는 허가 관측값일 뿐 최종 결과가 아님을, `not_observed`는 공개 API로 좌석을 확인하지 못했음을 표시합니다.

외부 페이지를 열어도 좌석 확보, 예약대기 신청, 예약, 결제 완료를 기록하거나 추정하지 않습니다. CAPTCHA·NetFunnel·`-1405`·`-8002`·`-8003`·403·접근 제한이 나타나면 자동 재시도, 다른 worker·IP·계정으로의 대체, 자동화 회피를 제공하지 않습니다. 사용자는 안내에 복사된 여정으로 공식 앱·홈페이지에서 직접 확인합니다.

접근성 안전 경계도 인계 계약의 일부입니다. modal/sheet가 열리면 배경을 `inert`와 `aria-hidden`으로 비활성화하고, modal은 제목·설명과 `aria-modal`을 제공하며 Escape와 Tab/Shift+Tab 포커스 순환을 지원합니다. 320px 및 200% 확대 reflow, 키보드 포커스와 새 탭 고지는 실제 브라우저 QA 전까지 완료 주장하지 않습니다.
