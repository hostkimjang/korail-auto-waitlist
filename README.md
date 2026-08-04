# 레일웨잇

KORAIL·SRT 여정을 모바일과 PC에서 관리하는 1인용 self-hosted 예약 대기 도우미입니다. 기본 모드는 공식 시간표와 공식 예매·예약대기 화면을 연결하며, 결제는 반드시 철도사 공식 플랫폼에서 사용자가 직접 완료합니다.

자동 예매는 작업 전체에서 한 번으로 끝나지 않고, 확정적인 `unavailable → available` 좌석 재발견으로 시작한 새 가용성 에피소드마다 최대 한 번 실행됩니다. 같은 에피소드의 반복 관측·중복 worker 실행은 추가 호출을 만들지 않습니다. `UNKNOWN` 결과는 예약을 다시 호출하지 않은 채 공식 예약 내역을 읽기 전용으로 빠르게 3회 확인하고, 계속 `INCONCLUSIVE`이면 5분·15분·60분 뒤까지 총 6회 확인합니다. exact `NOT_FOUND`로 보류 없음이 확인된 경우에만 같은 연속 `AVAILABLE` 구간에서 `confirmed-absent-retry:<attempt-id>` 근거로 딱 한 번 재무장하며, 그 재무장 시도도 `UNKNOWN`이면 추가 재호출하지 않습니다. `FAILED`·`PROVIDER_BLOCKED`처럼 보류 없음이 입증되지 않은 다른 결과는 자동 반복하지 않습니다. `AUTH_REQUIRED` 또는 `PROVIDER_BLOCKED` 뒤에는 시도 종료보다 새로운 성공 로그인 검증 세대가 확인된 경우에만 보존한 attempt fence를 삭제하지 않고 그 검증 세대에서 정확히 한 번 재무장합니다.

![선택된 반응형 디자인](docs/design/selected-responsive-ui.png)

## 현재 구현 범위

- `설정 > 화면 동작`은 화면 표시 갱신과 백엔드 철도사 좌석 관측을 서로 다른 값으로 저장합니다. 화면 표시 갱신은 기본 5초·허용 5~300초이며 저장된 snapshot과 홈 목록만 다시 읽습니다. 전역 `좌석 관측 간격`은 최초 설치 기본 5초·허용 1~600초이고 모든 활성 작업에 같은 목표값을 적용합니다. 관측 설정을 저장하면 아직 실행 시각이 도래하지 않고 실행 lease·cooldown이 없는 활성 작업만 새 값으로 `next_check_at`을 다시 계산하며, 이미 실행 중인 작업은 완료 뒤 새 값을 사용합니다. 실제 요청에는 provider cache·운영사별 단일 실행 lease·backoff·circuit·rate-limit·보호 cooldown이 항상 우선하므로 설정값은 호출 성공이나 정확한 실행 간격을 보장하지 않습니다.
- 결제기한 경과 뒤 같은 인증 actor의 최종 공식 확인에서 보류가 더 이상 결제 가능하지 않음이 확정되면 `watch.payment_hold_ended_*` terminal 이벤트가 기존 결제 알림 subject를 교체합니다. 알림은 `결제기한 안에 결제되지 않아 예매가 취소되었습니다`와 초 단위 발생 시각을 표시하고 모든 진행 step을 `completed|failed`로 닫아 spinner를 남기지 않습니다. 자동 예매 작업은 기존 episode fence를 보존한 채 감시로 복귀하고, `notify_only`는 종료합니다. 단순히 브라우저 시계가 기한을 지났다는 이유만으로 이 terminal 알림을 합성하지 않습니다.
- 모바일 PWA와 PC 반응형 웹: 홈, 3단계 새 대기, 내 예약, 알림 설정, `설정 > 로그·진행 상태`
- App 전역 알림은 하나의 `실시간 알림` surface에서 관리합니다. 결제·수동 확인·인증·좌석 발견을 진행·복구·일반 알림보다 먼저 정렬하고 종류별 건수, 펼치기, 그룹 닫기를 제공합니다. 같은 작업의 좌석 발견·예매 진행·결과는 `subjectKey`의 최신 단계 한 장으로 교체하며 동일 revision과 더 오래된 `revisionAt`은 다시 표시하거나 live region에서 재안내하지 않습니다. 일반 알림은 30초, 완료된 단계형 복구 알림은 60초 뒤 닫히고 진행 중 예매·결제·수동 확인·인증·좌석 발견은 최종 결과로 교체되거나 사용자가 닫기 전까지 유지합니다. surface를 접어도 확인·삭제로 처리하지 않고 카드와 타이머를 유지하므로 접힌 동안에도 원래 자동 닫힘 시각이 연장되지 않습니다. 예약 알림에는 운영사·열차번호·날짜·요일·구간·실제 후보 출도착 시각·좌석 등급과 확인 가능한 진행 단계만 표시합니다.
- 홈의 각 활동 중 티켓은 `감시만`과 `좌석 재발견마다 자동 예매`를 독립적으로 전환할 수 있음. 활성 작업에서는 여정 조건을 잠근 채 `reservation_policy`만 DB에 즉시 반영하고, 자동 예매를 켜려면 해당 운영사의 로그인 확인 계정이 필요함. 이 정책은 작업 전체 1회가 아니라 확정적인 새 가용성 에피소드마다 1회이며 결제 직전에 멈춥니다. 정책 전환과 겹쳐 도착한 오래된 목록 응답은 화면에 반영하지 않으며, 예약 직전에도 DB의 최신 계정 인증 상태를 다시 확인함. 정책 전환은 기존 예약 시도 이력을 삭제하거나 임의로 재무장하지 않습니다. 재시도는 확정적인 새 가용성 에피소드, 로그인 재검증 세대 또는 최초 `UNKNOWN`의 공식 확인에서 얻은 exact `NOT_FOUND`라는 서버 근거가 생긴 경우에만 허용하며, 마지막 경우에는 같은 연속 가용 구간에서 한 번만 허용합니다. 활동 행은 viewport가 아니라 실제 `.watch-list` 컨테이너 폭에 따라 운영사·시간, 상태·근거, 정책·행동을 독립 행으로 재배치합니다. 좁은 화면과 200% 확대에서도 정책 문구는 줄바꿈하고 스위치·일시정지·취소의 44px 영역은 줄이지 않으며, 정책 스위치의 접근 가능한 이름에 열차와 좌석 등급을 함께 포함합니다. 결제는 수행하지 않음
- 홈은 후보별 최신 예약 시도의 결과·종료 시각·재시도 조건을 목록 재조회 뒤에도 유지합니다. 좌석 확보 실패는 감시 계속과 새 가용성 에피소드 조건을, 불명확·일반 실패는 공식 예약 내역 수동 확인 필요를 표시하므로 `좌석 발견`과 `예매 진행 중`을 혼동하지 않습니다. 유효기간이 지난 운행·예매창 관측은 `예매창 열림` 같은 현재형 문구를 숨기고 `운행·예매 상태 관측 만료 · 다시 확인 중`으로 강등합니다.
- 철도 계정 검증은 DB에 저장될 다음 credential generation으로 실행하고, 검증이 끝난 뒤 계정 행을 다시 잠가 현재 generation의 바로 다음 값일 때만 암호문과 `authenticated` 상태를 저장합니다. 검증 중 다른 계정 변경이 먼저 반영되면 충돌로 닫고 최신 계정을 덮지 않습니다. KORAIL Chromium sidecar와 SRT provider sidecar는 운영사별 프로세스 내 인증 세션을 직렬화해 같은 credential generation과 마지막 사용 기준의 로컬 재사용 한도가 모두 유효할 때만 재사용합니다. KORAIL은 여기에 `login_method`·`login_id`·비밀번호로 계산한 프로세스 메모리 SHA-256 fingerprint까지 같아야 기존 인증 browser session을 같은 generation의 예약에 이어서 사용합니다. fingerprint 자체도 비밀 파생값으로 취급해 로그·응답·DB·artifact에 기록하지 않습니다. background·read-only 시간표 검색은 별도 ephemeral browser lease와 분리된 HTTP replay lease를 사용해 인증 세션을 소비하거나 폐기하지 않습니다. credential 변경·인증 만료·`auth_required`에서는 해당 인증 세션을 폐기하고 허용된 한 번의 새 로그인을 시도하며, 비밀번호·cookie·storage state는 메모리 밖·로그·DB에 저장하지 않습니다. 예약 provider adapter는 실제 외부 호출에 사용한 credential version을 내부 `ReservationResult`에 붙여 반환하고 worker는 이 값으로 인증 상태를 CAS하므로, 늦게 도착한 과거 결과가 최신 계정 상태를 덮지 않습니다. 로그인 저장과 예약 worker는 모두 provider account를 먼저, watch를 나중에 잠가 PostgreSQL 교착을 피합니다.
- 실험 프로필은 KORAIL Chromium sidecar와 SRT provider sidecar를 별도 상주 프로세스로 실행합니다. 각 sidecar 안에서도 읽기 전용 검색 actor와 인증·예약·공식 예약 확인 actor를 분리해 시간표·좌석 조회가 인증 세션을 소비하거나 교체하지 않게 합니다. KORAIL 검색은 별도 ephemeral browser/HTTP replay lease를, SRT 검색은 계정 없는 source를 사용하며 예약과 확인은 같은 credential generation의 인증 actor에서만 수행합니다. API 시작 시 저장된 모든 활성 계정을 한 번 prewarm하고, 성공한 현재 credential generation만 DB의 `authenticated` 상태와 검증 시각에 반영해 관련 작업을 재개합니다. 기동 뒤 새 `auth_required` 또는 `provider_blocked`가 저장되면 상주 manager가 30초 주기로 새 revision을 감지합니다. 같은 credential generation의 상주 actor가 이미 `ready`이고 로컬 재사용 가능하면 외부 로그인 없이 DB 상태와 감시를 복구하고, 그렇지 않을 때만 `(provider, credential_version, updated_at)`별 딱 한 번 재검증합니다. 실패한 같은 revision은 반복하지 않고 새 실패 revision이나 계정 변경이 생겨야 다시 시도합니다. `GET /api/v1/provider-runtime-status`는 credential generation, `created/last_verified/last_used` 경과 시간, 현재 프로세스의 남은 로컬 재사용 시간과 prewarm 결과만 `no-store`로 반환합니다. 이는 운영사가 공개한 고정 세션 TTL이나 공식 로그인 유효기간이 아니라 현재 프로세스의 보수적인 로컬 관측값입니다. 원문 credential·cookie·storage state와 credential fingerprint는 프로세스 메모리 밖에 저장하지 않습니다.
- strict TypeScript·TSX 진입점과 typecheck gate. 기존 대형 JS/JSX는 기능별 수직 슬라이스로 점진 전환
- KTX(KORAIL)·SRT 복수 선택, 역명 문자열 일치 우선 공식 역 검색·교환, 커스텀 달력, 요일 빠른 선택, 30분 단위 출발 시간 범위. 서비스일의 마지막 종료 선택은 `다음 날 00:00`으로 표시하되 같은 날짜의 역전 범위를 보내지 않고 `23:59` 경계로 정규화해 23:30 이후 출발·자정 이후 도착 열차를 포함합니다. 다음 날 00시 이후 출발 열차는 출발일을 다음 날로 바꿔 조회합니다.
- 출발역·도착역의 공식 `node_id`·역명·도시를 함께 선택하고 같은 식별자를 시간표 조회까지 전달해 동명이역과 불필요한 재탐색 방지
- KORAIL은 서버 Chromium 공식 결과, SRT는 운영사 live source를 시간표·좌석의 주 데이터 경로로 사용합니다. 각 운영사 결과를 `departure_from`부터 `departure_to`까지 양 끝을 포함해 병합·정렬·중복 제거하고, 한 운영사가 실패해도 다른 운영사의 결과는 보존합니다. 운영사 live 조회가 정상적으로 끝나면 TAGO를 호출하지 않으며, live source를 사용할 수 없을 때만 TAGO 시간표를 비차단 fallback으로 사용합니다. 이 fallback에는 좌석 재고 근거가 없으므로 일반실·특실을 `unknown/not_observed`로 유지합니다.
- 공식 live 시간표는 TAGO `origin_node_id·destination_node_id`가 없어도 역명·구간 조건으로 조회할 수 있습니다. 다만 node ID가 없는 응답에는 node-bound confirmation overlay와 `registration_evidence_id`를 발급하지 않으므로 시간표·좌석 상태는 표시하되 대기 등록은 fail-closed합니다.
- 열차 선택 단계에서도 같은 달력으로 실제 출발일을 바꾸며, 날짜가 바뀌면 기존 열차·좌석 선택을 비우고 해당 날짜 시간표를 다시 조회
- 시간표 조회 시각·운임·소요시간과 일반실·특실별 `SeatClass` 상태를 분리한 열차 결과 카드
- 좌석 상태의 출처·관측 시각과 허용 행동을 함께 검증하는 provenance/action 계약. 관측 근거가 없으면 두 좌석 등급 모두 `unknown/not_observed`로 fail-closed하고, 미관측 사유를 출처 미설정·접근 제한·미지원 구간·다인원 미지원·출발 시간 경과·일치 열차 없음·상류 장애로 구분. 이미 운행이 끝난 시간창만 미관측이면 3단계 요약에 `선택한 출발 시간대가 지났습니다`를 표시하고 불필요한 서버 재조회 행동을 숨깁니다. 지난 시간창과 실제 공급원 오류가 함께 있으면 오류가 남은 운영사만 재조회합니다. KORAIL sidecar가 보호 신호를 내부 HTTP 423 `provider_access_restricted`로 정규화하면 3단계의 해당 좌석에는 예매·대기·재조회 행동을 표시하지 않음
- SRT는 사용자의 시간표 조회에서 계정 없는 live source를 한 번 실행하고, KORAIL은 서버 관리형 Chromium 어댑터의 공식 결과에서 열차번호·종류·구간·날짜·출도착시각·운임과 일반실·특실 상태를 정규화합니다. KORAIL sidecar의 기본 엔진은 WebDriver 계층 없이 CDP로 통신하는 Pydoll이며, `KORAIL_BROWSER_ENGINE=playwright_direct_cdp`를 명시한 경우에만 기존 Chromium 직접 실행·loopback CDP 경로를 사용합니다. 두 엔진 모두 기본 비활성 `experimental-rail`에서만 실행하며 미활성·오류·보호 응답은 TAGO 시간표 fallback 또는 `unknown/not_observed`로 닫습니다.
- KORAIL Chromium 어댑터는 업무 document의 403과 공식 화면 보호 marker는 계속 즉시 차단하되 font·analytics 같은 비업무 subresource 403만으로 보호 응답을 추정하지 않음. 공식 결과 DOM에 무궁화·ITX가 섞여도 KTX·KTX-산천·KTX-청룡 행만 엄격히 파싱해 비-KTX 행 구조가 KTX snapshot을 깨뜨리지 않음. 공식 검색 시작 시각에 따라 병결 보조편이 누락되는 결과 경계를 피하기 위해 미래 서비스일의 좌석 조회는 00:00부터 읽습니다. 당일은 KST 현재 hour와 사용자가 고른 시작 중 늦은 시각부터 읽어 이미 지난 picker 시각을 선택하지 않고, 화면의 요청 시간창과 열차번호·출발시각 exact key는 합성 단계에서 다시 적용합니다. Pydoll은 공식 입력에 이미 선택된 당일이 picker 링크에서 생략돼도 그 날짜를 유지한 채 시각만 바꿉니다. 최초 검색은 한 번만 제출하고 결과 화면의 `더보기`만 최대 19회 확장하며, 새 열차가 없거나 버튼이 사라지거나 403·429·보호 신호가 관측되면 즉시 멈추고 열차 종류·번호·구간 기준으로 누적 중복을 제거
- KORAIL Pydoll cold 조회와 인증된 에피소드당 1회 자동 예매 조회는 공식 4자리 `stn_cd ↔ stn_nm` map을 정상적으로 읽은 경우 서버가 만든 25키 KTX 일반검색 URL로 결과 화면을 한 번 직접 엽니다. 이 경로는 역 선택 2회·날짜/시간 picker·조회 버튼 입력만 생략하며 기존 미래일 00:00 시작, 결과 exact match, HTTP replay capture, 인증 actor 분리와 결제 전 중단을 유지합니다. 역 map을 받지 못한 경우 업무 조회 전에만 기존 가시 UI 입력으로 돌아가고, 직접 이동이 업무 요청을 시작한 뒤에는 보호·timeout·불명확 결과를 UI 재제출로 반복하지 않습니다.
- 1440px에서 182px 높이를 기준으로 한 고밀도 열차 카드. 430px 이하 모바일은 운영사·열차번호, 출도착 시각, 등록 상태, 일반실·특실 순서의 티켓형 1열 정보 구조로 전환하며 320px 가로 넘침을 막고 44px 행동 영역을 유지
- 일반실·특실 독립 패널과 `열차 + 좌석 등급`별 즉시 대기 등록. 같은 열차의 일반실·특실을 모두 등록할 수 있고 등록 결과는 홈 활동 목록에 바로 반영. 활성 카드는 `대기 등록 N건`, 좌석은 `대기 등록됨`·`좌석 변화를 감시 중`을 표시하며 취소는 진한 위험색 `일반실/특실 대기 취소` 버튼으로 구분합니다. 상태·행동은 이름과 의미 색상으로 함께 전달하고 `aria-pressed`와 44px 이상 행동 영역을 유지합니다.
- 열차 등록 단계의 컴팩트 자동 동기화 헤더와 원형 수동 새로고침, 기본 5초 부분 동기화. 화면 주기는 서버의 마지막 정상 동일 조건 snapshot을 즉시 읽고, snapshot이 60초 이상 지난 경우에만 서버가 백그라운드 재검증을 한 번 예약합니다. 동일 조건은 singleflight로 합치고 실패는 30초부터 최대 5분까지 backoff합니다. KORAIL 공식 응답이 성공이면서 열차가 0개이면 심야 서비스일의 정상 빈 결과로 보존하고 상류 장애로 승격하지 않습니다. 일반 source·DOM 실패 backoff는 API와 Chromium sidecar 두 계층 모두에서 출발·도착·서비스일·시간창·승객 수가 같은 exact query에만 적용하고, 403·423·429처럼 명시적인 접근 제한·rate-limit 신호만 provider-wide cooldown으로 공유합니다. 따라서 background worker의 한 날짜 제어 실패가 다른 서비스일의 사용자 조회를 막지 않습니다. 원형 아이콘은 빠른 응답에도 최소 한 바퀴(800ms)를 회전하고 `최근 갱신 HH:mm:ss`는 고정 폭으로 유지합니다. 화면은 값이 달라진 열차 카드만 갱신하고, 주기는 `설정 > 화면 동작`에서 5~300초로 저장할 수 있습니다.
- 좌석 대기 버튼은 별도 `등록 완료` 단계 없이 작업을 즉시 생성·시작하고, 등록된 같은 버튼을 다시 누르면 저장된 watch ID로 즉시 취소합니다. Home에서 새 대기로 다시 들어와도 활성 DB 작업의 `provider + train_number + departure_at` instant + `seat_class`를 같은 좌석 버튼에 hydrate해 실제 watch ID와 작업별 `reservation_policy`를 복원합니다. 자동 예매 작업은 `좌석 재발견마다 자동 예매 · 에피소드당 1회 · 결제 전 중단`으로 다시 표시합니다. 등록 중·취소 중에는 해당 좌석 버튼만 잠깁니다.
- 출발·도착 TAGO node ID와 후보 열차별 출도착 시각·좌석 등급·우선순위를 독립 행으로 영속화. `available`·`limited`·`standing_plus_seat`를 포함해 공식 시간표에서 관측되고 실행 provider의 `seat_monitoring=true`여서 `add_to_watch`가 허용된 선택 좌석만 서버가 짧게 발급한 불변 `registration_evidence`와 exact match할 때 등록합니다. 관측 상태·provenance는 유지하되 실행 capability가 없으면 API가 `add_to_watch`와 evidence ID를 제거합니다. KORAIL은 Chromium background 3중 opt-in, SRT는 계정 없는 source 3중 opt-in을 모두 만족할 때만 대기 행동을 발급합니다. `unknown`·`not_observed`·비허용 좌석에는 ID를 발급하지 않으며, 과거 비허용 evidence도 서버가 수락하지 않습니다. 생성 전에 근거가 만료되면 해당 운영사의 정상 좌석 refresh를 한 번만 수행하고, identity·좌석 상태가 같으며 새롭고 신선한 공식 관측 근거가 발급된 경우에만 생성·시작을 한 번 재시도합니다. evidence-bound 생성 키와 watch-bound 시작 키를 재사용해 시작 응답 유실 뒤의 같은 좌석 재시도도 기존 작업으로 복구합니다. 홈에는 등록 당시 상태·출처·관측 시각을 감사 정보로 보존하고, 후보별 현재 상태는 별도의 최신 `SeatObservation`으로 표시합니다.
- 좌석 관측·상태 전이·예약 시도를 서로 다른 이력으로 저장하고, migration `0018_reservation_episodes`가 `ReservationAttempt.attempt_sequence`와 후보 범위의 고유 `episode_key`로 같은 근거의 중복 호출을 막습니다. `NOT_AVAILABLE`은 보류가 없다는 예약 시점의 확정 비가용 근거이므로 그 뒤 처음 들어온 새 행동 가능 관측에는 `not-available-retry:<attempt-id>`로 경쟁 소실 보정 시도를 한 번 허용합니다. 이 보정 시도도 `NOT_AVAILABLE`이면 연속 행동 가능 관측으로 재호출하지 않고, 이후 확정 비가용 observation과 새 행동 가능 관측이 순서대로 생긴 다음 에피소드에서만 다시 재무장합니다. `AUTH_REQUIRED`는 계정이 다시 `authenticated`가 되고 `last_authenticated_at`이 이전 시도 종료보다 새로운 로그인 검증 세대에서만 한 번 재무장합니다. 최초 `UNKNOWN` 시도는 공식 확인의 exact `NOT_FOUND`가 저장된 경우에만 `confirmed-absent-retry:<attempt-id>`라는 별도 identity로 같은 연속 `AVAILABLE` 구간에서 한 번 재무장합니다. 예약 결과가 계정 인증 상태를 갱신할 때는 adapter가 실제 호출에 사용해 결과와 함께 반환한 credential version과 현재 version이 같은 경우에만 반영하므로, 이후 완료된 새 로그인 검증을 과거 호출 결과가 강등하지 않습니다. `FAILED`·`UNKNOWN`·`PROVIDER_BLOCKED`, worker 재시작 뒤 stale `PENDING → UNKNOWN`, `PAYMENT_REQUIRED`·`RESERVED`는 그 결과만으로 자동 재시도하지 않고 기존 시도 이력과 수동 확인 근거를 보존합니다. 재무장 시도까지 `UNKNOWN`이면 추가 예약 호출을 만들지 않습니다. 결제기한 후 보류 소실이 확정된 자동 예매 작업도 같은 episode에서는 다시 호출하지 않으며, 최종 확인 marker 뒤 확정 비가용→새 행동 가능 관측으로 새 episode가 생겨야 한 번 재무장합니다.
- 예약 결과가 `PAYMENT_REQUIRED` 또는 `UNKNOWN`이면 공개 시간표나 계정 없는 좌석 source가 아니라 실제 예매에 사용한 것과 같은 credential generation의 로그인된 인증 actor에서 공식 예약 보류를 읽기 전용으로 확인합니다. `PAYMENT_REQUIRED`는 빠른 확인을 최대 3회 수행하고, 정확한 결제기한이 경과한 경우 공식 예약목록을 한 번 최종 확인합니다. `UNKNOWN + INCONCLUSIVE`는 빠른 3회 뒤 5분·15분·60분 지연 확인을 이어 총 6회까지만 수행하며, 이미 빠른 3회를 소모한 legacy 시도도 첫 지연 확인부터 복구합니다. migration `0022_post_deadline_reconciliation`의 nullable `post_deadline_reconciled_at`과 해당 index는 `PAYMENT_REQUIRED`의 최종 확인 완료를 표시하고, migration `0023_extend_unknown_reconcile`은 `UNKNOWN` 지연 확인을 위한 DB 횟수 상한을 6회로 확장합니다. KORAIL은 현재 상세 화면을 먼저 판독하고 근거가 없으면 공식 예약목록에서 열차번호·구간·서비스일·출도착시각·인원과 유일한 미결제 행동을 exact match합니다. 목록에 좌석 등급이 없으면 이를 좌석 등급 확인 근거로 승격하지 않습니다. SRT의 1회 예매 호출이 반환한 미결제 예약은 열차·구간·서비스일·출발시각·ticket 좌석 등급·seat count가 요청과 모두 일치할 때만 즉시 보존하고, 불명확한 결과만 공식 예약목록과 다시 대조합니다. 어느 확인도 예매·취소·결제·결제정보 입력을 실행하지 않습니다. 최초 `UNKNOWN` 시도의 공식 확인이 exact `NOT_FOUND`이면 `confirmed-absent-retry:<attempt-id>` 근거로 같은 연속 `AVAILABLE` 구간에서 자동 예매를 딱 한 번 재무장합니다. 재무장된 시도도 `UNKNOWN`이면 추가 예약 호출을 만들지 않습니다. 결제기한 경과 `PAYMENT_REQUIRED`의 최종 확인이 exact `NOT_FOUND`이거나, exact 미결제 행이 남아 있어도 운영사가 반환한 그 행의 결제기한이 이미 지난 경우에는 더 이상 행동 가능한 결제 보류가 아닌 것으로 종료합니다. `notify_only`는 `expired`, `reserve_once_before_payment`는 `watching`으로 옮기되 기존 episode fence를 유지하고, 최종 확인 marker 뒤 확정 비가용→새 행동 가능 관측이 생긴 새 episode에서만 다시 시도합니다. 이전 버전이 과거 기한의 exact 행에 marker만 남긴 레거시 건은 같은 인증 actor에서 호환성 정리 확인을 한 번만 더 수행합니다. 인증 필요·운영사 차단·중복 일치·기한 없는 행·불확실 응답은 fail-closed로 남깁니다. 실제 운영 KORAIL·SRT 예약목록의 장시간 자동 검증은 운영 확인 항목입니다.
- 후보별 최신 예약 시도 API는 원래 `PAYMENT_REQUIRED` outcome을 감사 이력으로 유지하면서 `confirmation_outcome`과 `post_deadline_reconciled_at`을 함께 반환합니다. 최종 exact `NOT_FOUND`, 또는 marker 시각보다 늦지 않은 공식 결제기한을 가진 exact `CONFIRMED_PAYMENT_REQUIRED`만 `new_availability_episode` 재시도 조건으로 투영하고, 홈은 과거 보류를 현재 `결제 필요`로 남기지 않고 `결제 보류 종료 확인 · 감시 계속`과 매진 후 좌석 재발견 대기를 표시합니다. marker 누락·기한 없는 exact 행·일반 확인의 `NOT_FOUND`·`INCONCLUSIVE`는 보류 종료로 표현하지 않습니다.
- 시간표 조회용 provider registry와 worker 실행용 provider registry를 분리. KORAIL은 `EXPERIMENTAL_RAIL_ENABLED`, `KORAIL_BROWSER_ADAPTER_ENABLED`, `KORAIL_SEAT_MONITORING_ENABLED`, SRT는 `EXPERIMENTAL_RAIL_ENABLED`, `SRT_SEAT_STATUS_ENABLED`, `SRT_SEAT_MONITORING_ENABLED`가 모두 `true`일 때만 background 좌석 관측을 연결. 로그인 확인된 선택 운영사 철도 계정이 있으면 새 대기의 기본 정책은 `reserve_once_before_payment`이며 사용자는 언제든 `notify_only`로 바꿀 수 있습니다. 운영사별 `*_RESERVATION_ONCE_ENABLED=true`, 암호화 저장된 활성 철도 계정, 작업별 `reserve_once_before_payment`가 모두 충족될 때만 좌석 발견 뒤 DB episode fence로 해당 가용성 에피소드에서 한 번 예매를 진행하며 결제 직전에 멈춥니다. 예매가 확정적으로 실패하거나 보류 없음이 확인되고 좌석이 사라진 뒤 다시 행동 가능해지면 새 episode를 생성해 다시 한 번 시도하므로, 이 정책은 작업 전체 1회가 아닙니다.
- SRT 외부 관측은 PostgreSQL의 `provider + account_scope` 실행 임대를 먼저 획득하고, 단조 증가 fencing token을 호출 전과 결과 기록 전에 다시 확인. 임대가 만료되거나 소유권을 잃은 worker의 늦은 결과는 기록하지 않음
- SRT shared source cooldown이 활성화돼 있으면 worker가 같은 실행 임대·fencing 안에서 due 작업의 `next_check_at`·`cooldown_until`을 TTL 만료 시각으로 미루고 상류 호출과 오류 `SeatObservation` 기록을 모두 생략. preflight와 실제 관측 사이에 cooldown이 열린 race도 재확인해 같은 방식으로 연기하며, TTL 해제 뒤에는 오래된 cooldown 표시를 지우고 정상 관측을 재개
- 외부 요청이 없는 mock worker에서 동일 조건 조회 병합, 변화 없는 상태 backoff, 우선순위 예약과 하위 후보 억제, `payment_required` 인계까지 검증
- provider-wide circuit 상태를 영속화하고 `open`·`manual_hold`에서는 adapter 호출 전에 작업을 중단하는 fail-closed 경계
- 관리자 한 명을 위한 ID·비밀번호 인증과 안전한 서버 세션
- 대기 작업 생성 직후 시작 요청, CRUD, 일시정지·취소, 상태 전이, SSE 실시간 갱신. 로그인 확인된 계정·`reserve_once_before_payment` 정책·운영사 자동 예매 capability가 모두 있는 새 작업은 start commit 뒤 `process_watch_now`를 best-effort로 즉시 `rail` queue에 넣고, broker enqueue가 실패해도 시작 응답을 되돌리지 않으며 5초 Celery beat와 5초 expiry가 오래된 sweep을 쌓지 않고 영속 fallback으로 처리
- 홈의 활동 중 대기는 고정 개수 제한 없이 모두 표시합니다. 웹이 보이는 동안 같은 화면 동작 주기로 내부 작업 목록만 갱신하고, 헤더의 원형 새로고침과 고정폭 `최근 갱신 HH:mm:ss`로 이 `/watches` 요청 상태를 표시합니다. 데이터는 응답 즉시 반영하지만 회전 아이콘은 빠른 응답에도 최소 한 바퀴(800ms)를 보여 주고, 느린 응답은 다음 회전 경계에서 멈춥니다. 수동 새로고침·SSE·`watch.seat_observed`·예약 시도·예약 결과·주기 요청은 같은 coordinator에서 합쳐지며 별도의 provider 조회를 만들지 않습니다. 후보별 최신 `SeatObservation`의 상태·`observed_at`·`fresh_until`을 현재 표시와 행동의 근거로 사용하고, 불변 `registration_evidence`는 `등록 당시` 감사 정보로만 구분합니다. `예매` CTA는 현재 후보에 신선한 `available`·`limited`·`standing_plus_seat` 관측이 있을 때만 표시합니다. 접속 중 새 좌석이 발견되면 상세 여정 alertdialog를 명시적으로 닫을 때까지 유지합니다. 예약이 시작되면 `좌석 발견 → 계정·조건 확인 → 공식 예매 요청 → 결과 확인` 중 서버 상태로 확인 가능한 단계만 상단 toast에 표시하고, 결과가 실패·불명확·좌석 소실이면 그 사실과 감시 복귀를 한 번 안내합니다. 확정적인 `NOT_AVAILABLE`은 좌석 소실 뒤 새 재출현 에피소드에서 다시 한 번 시도할 수 있음을 안내합니다. `UNKNOWN`은 공식 확인 중 수동 확인 상태를 유지하고, 최초 시도의 exact `NOT_FOUND`가 저장된 경우에만 같은 연속 가용 구간의 1회 재무장 상태를 서버 근거대로 표시합니다. `FAILED`·`PROVIDER_BLOCKED`는 자동 재시도를 약속하지 않고 수동 확인을 유지합니다. 로그인 재검증 성공 뒤에는 과거 `AUTH_REQUIRED` toast를 제거하고 새 검증 세대에서 감시·예약 준비가 재개됐음을 표시합니다. CTA는 좌석 등급과 최근 관측 시각을 포함한 공식 예매 안내를 열고, 여정 복사와 운영사 공식 검색 진입점을 제공합니다. 공개된 열차별 딥링크가 없으므로 공식 결과가 자동 입력되거나 해당 열차가 예약된다고 표현하지 않습니다. 현재 API snapshot의 상태가 실제 `auth_required`인 작업에만 컴팩트 경고와 `철도 계정` CTA를 표시하며, 과거 인증 오류 이력만으로 경고를 유지하지 않습니다. 완료·만료와 과거 terminal 실패 기록은 `전체 내역 보기`로 `내 예약`에서 확인합니다.
- 자동 예매의 접속 중 알림은 `watch.reservation_attempted` SSE가 생성된 경우에만 `좌석 발견 → 예매 시작 → 로그인 세션 확인 → 열차·좌석 재확인 → 좌석 선택 → 예약 요청 → 공식 결과 확인`을 표시합니다. 수 초 안에 끝난 `reserving`도 REST snapshot 사이에서 잃지 않으며, 진행 카드는 최종 결과 이벤트로 교체되거나 사용자가 닫기 전에는 자동으로 사라지지 않습니다. 동기 sidecar 실행 중에는 세부 단계를 대기로 표시하고, 결과 SSE가 도착하면 sidecar가 실제 도달한 단계와 timezone-aware 시각만 완료·실패로 바꿉니다. 카드 상단과 각 단계 아래에는 KST `HH:mm:ss`를 표시하고 발견→시작 대기, 시작→세션·재확인·선택·요청·결과의 구간별 처리시간을 계산합니다. 결과 카드가 시작 카드를 교체해도 앞 단계 시각을 보존하며, SRT나 이전 결과처럼 세부 근거가 없는 경로에는 단계를 추정하지 않고 기존 시작→결과 총 처리시간만 표시합니다. 자동 예매 attempt가 생성되지 않은 fence 상태의 행동 가능 관측은 일반 `좌석을 찾았습니다` 알림으로 승격하지 않습니다.
- `payment_required` 작업 중 timezone을 포함한 유효한 실제 `payment_deadline`이 남은 건과 기한 미제공 건만 홈의 긴급 `결제 대기`에 표시합니다. 기한이 있는 항목은 빠른 순서로 정렬해 남은 분·초를 카운트다운하고, 기한이 없거나 timezone이 빠진 값은 임의 시간을 만들지 않고 `결제기한 미제공`으로 안내합니다. 화면 시계가 기한 경계를 넘으면 `00:00:00`과 결제 CTA를 계속 남기지 않고 긴급 집계에서 즉시 제외합니다. 서버의 공식 정리가 끝나기 전인 기한 경과 기록은 `내 예약`에 `기한 경과 · 공식 확인 필요`와 실제 기한, `공식 확인 열기`로 보존하며 결제 완료나 보류 소실로 추정하지 않습니다. 공식 CTA는 저장된 handoff URL만 열며 결제 자체는 자동화하지 않습니다.
- KORAIL 예약목록의 `YYYY.MM.DD.HH:mm`·공백이 섞인 점 구분 형식과 SRT 예약 객체의 결제일·결제시각을 KST timezone-aware 기한으로 정규화합니다. 신규 좌석 관측과 에피소드당 1회 자동 예매를 과거 불명확 시도의 읽기 전용 reconciliation보다 먼저 처리하며, KORAIL 최초 결과에 exact 대상 열차가 하나 있으면 불필요한 결과 확장을 생략합니다.
- 후보는 등록 identity인 `scheduled_departure_at`과 지연 시 계산되는 `estimated_departure_at`, 공식 출발 관측 시각인 `actual_departure_at`, `delay_minutes`를 분리해 보존합니다. KORAIL 화면의 정확한 `N분 지연 예상` 문구와 HTTP replay의 `h_expn_dpt_dlay_tnum`을 지연 분으로 정규화하되 scheduled identity는 바꾸지 않고 estimated departure만 갱신합니다. `sold_out`은 좌석 재고 관측일 뿐 예매창 종료로 해석하지 않습니다. 신선한 provenance가 있는 `departed_origin`·`cancelled` 또는 닫힌 예매창만 즉시 만료 근거로 사용합니다. 신선한 `delayed`·`boarding`·열린 예매창은 예정시각이 지났더라도 감시를 유지합니다. 운행 상태가 `unknown`이거나 terminal 근거가 stale이면 예정 출발 뒤 최대 15분 동안 제한적으로 다시 확인하고, 그 시점에도 신선한 계속 운행 근거가 없으면 만료합니다. `seat_found`·`official_waitlist`도 같은 규칙을 따르며 KST 자정이나 도착시각을 임의의 만료 근거로 사용하지 않습니다.
- Web Push, Telegram, Discord, 범용 HTTPS Webhook과 PostgreSQL outbox 재시도. 128자를 넘는
  중복 방지 키는 읽을 수 있는 접두부와 원문 SHA-256을 결합해 같은 논리 이벤트가 안정적으로
  같은 영속 키를 사용하도록 정규화. 단일 관리자 전역에서 현재 `enabled=true`인 채널은 채널 생성
  이후 시작한 작업뿐 아니라 이미 활동 중인 대기의 다음 알림부터 동일하게 적용합니다. 설정 화면에서
  미설정 채널의 스위치를 켜면 해당 채널 설정을 시작하고, 설정된 채널은 같은 스위치로 활성·비활성을
  바꿉니다. 설정의 `OS 알림`은 이 기기의 Web Push 권한·구독과 서버 채널이 모두 준비된 경우에만
  켜진 상태로 표시하며, 접속 중 웹 화면의 `실시간 알림` surface와 독립적으로 동작합니다. HTTPS 또는
  loopback secure context가 필요하고, iOS는 16.4 이상에서 홈 화면에 설치한 PWA로 연결해야 합니다.
  브라우저나 운영체제가 background push를 중지한 상태의 전달까지 보장하지는 않으므로 `시험` 알림의
  실제 OS 알림 영역 수신을 운영 환경별로 확인합니다.
- 인증된 관리자 관제 화면에서 24시간 처리량, 좌석 관측 오류율, 알림 최종 실패율, 작업 상태, provider circuit, 식별정보를 제거한 최근 진행 기록과 별도 `좌석 조회 제공원 상태` 확인
- FastAPI, PostgreSQL, Redis, Celery worker·scheduler, Caddy 기반 Docker Compose
- Tailscale 우선 접속과 선택적 공개 도메인, 암호화 PostgreSQL 백업·복원
- 공식 시간표 provider, 좌석별 벤치마크 상태를 보여주는 mock provider, 기본 비활성 `experimental-rail` 프로필


## 빠른 시작

요구사항은 Docker Desktop 또는 Docker Engine과 Compose v2입니다.

1. `.env.example`을 `.env`로 복사합니다.
2. `.env.example`의 형식 안내를 따라 `.env`의 필수 비밀값을 채웁니다. 실제 `.env`는 Git과 Docker build context에서 제외됩니다.
3. `AUTH_ALLOWED_ORIGINS`와 `CORS_ORIGINS`를 실제 접속 주소에 맞춥니다.
4. 다음 명령으로 검증하고 시작합니다.

```powershell
./scripts/ops.ps1 config
./scripts/ops.ps1 up
```

Tailscale 접속은 호스트에서 다음과 같이 Caddy의 로컬 포트를 tailnet에 제공합니다.

```powershell
tailscale serve --bg http://127.0.0.1:80
```

서버 관리형 KORAIL·SRT 경로를 사용할 때는 `./scripts/ops.ps1 experimental`을 실행합니다. 이 명령은 `.env`의 요청 시점·background 활성화 값과 두 API-sidecar 내부 token을 자동으로 생성·보존한 뒤 현재 `experimental-rail` 프로필의 migration, API, 웹, proxy, 두 sidecar, 기본·실험 worker와 scheduler를 모두 같은 revision으로 빌드·재생성합니다. token을 화면이나 확장 프로그램에 직접 입력할 필요가 없습니다. `docker compose`를 직접 실행하는 운영 환경에서는 `.env.example` 형식대로 같은 값을 수동 설정해야 합니다.

Compose 기본 포트는 `127.0.0.1`에만 bind되어 LAN 평문 HTTP로 직접 노출되지 않습니다. 공개 도메인을 사용할 때만 `.env`의 bind 주소를 `0.0.0.0`으로 바꾸고 Caddy HTTPS와 secure cookie를 함께 활성화합니다. 비밀값은 모두 `.env`에서 컨테이너 환경변수로 주입되므로 `docker compose config` 결과나 `docker inspect`를 공유하지 말고, 설정 검증은 `--quiet`로 실행합니다.

기능·코드 또는 Dockerfile·Compose·런타임 이미지 계약을 바꾼 배포는 이전 이미지를 남긴 채 일부 서비스만 갱신하지 않습니다. `docker compose -f compose.yml config --quiet`, `docker compose -f compose.yml build`, `docker compose -f compose.yml up -d --force-recreate` 순서로 현재 사용하는 프로필의 서비스를 일관되게 적용한 뒤 migration 성공 종료와 서비스 health를 확인합니다. 단순 CSS·문서 변경에는 이 절차를 강제하지 않으며, 데이터 보존을 위해 `down -v`와 volume 삭제는 사용하지 않습니다. 자세한 확인 명령은 [운영 가이드](docs/OPERATIONS.md)를 따릅니다.

### 서비스 로그

Compose는 API, rail 전용 worker, notifications 전용 worker, scheduler, experimental worker, KORAIL browser sidecar의 애플리케이션 로그를 저장소 루트의 `logs/<service>/current.log`에 JSON Lines로 기록합니다. rail과 notifications 큐는 concurrency 1인 별도 worker가 소비하므로 느린 알림 전송·재시도가 좌석 관측과 자동 예매 실행을 점유하지 않습니다. 기본값은 파일 하나 5 MiB(`APP_LOG_MAX_BYTES=5242880`)와 archive 4개(`APP_LOG_BACKUP_COUNT=4`)이므로 서비스별 최대는 약 25 MiB입니다. 컨테이너 표준 출력은 끄지 않으므로 실시간 점검에는 계속 `docker compose logs -f <service>`를 사용합니다. Docker의 `local` log driver 보존 설정도 파일 회전과 별도로 적용됩니다.

`logs/`는 Git에 포함하지 않으며, 로그 파일을 전달하기 전에는 민감정보가 없는지 다시 확인합니다. 크기·archive 개수·level은 `.env`의 `APP_LOG_MAX_BYTES`, `APP_LOG_BACKUP_COUNT`, `APP_LOG_LEVEL`로 바꾼 뒤 컨테이너를 recreate하여 반영합니다. 상세 경로·권한·복구 절차는 [운영 가이드](docs/OPERATIONS.md)를 따릅니다.

최초 접속에서는 기본 loopback 또는 Tailscale 안에서 `AUTH_INITIAL_REGISTRATION_ENABLED=true`로 잠시 기동한 뒤 관리자 ID와 12자 이상의 비밀번호를 등록합니다. ID와 Argon2id 비밀번호 해시가 PostgreSQL에 저장되면 추가 등록은 닫히고, 등록한 브라우저에는 로그인 세션이 발급됩니다. 계정 생성 뒤에는 값을 `false`로 되돌려 재시작합니다. 이후 로그아웃하거나 세션이 만료되면 같은 ID와 비밀번호로 로그인합니다. 최초 등록 전에는 서비스를 공개 인터넷에 노출하지 않습니다.

`.env.example`의 `AUTH_COOKIE_SECURE=false`는 `http://localhost` 최초 검증용입니다. Tailscale Serve나 공개 도메인처럼 HTTPS로 접속할 때는 `AUTH_COOKIE_SECURE=true`로 바꿔야 합니다.

## 개발과 검증

```powershell
cd apps/web
npm install
npx playwright install chromium
npm run typecheck
npm test
npm run test:e2e
npm run build

cd ../api
python -m pip install -e ".[test]"
pytest
```

기능을 바꾼 뒤에는 저장소 루트에서 `./scripts/ops.ps1 verify`를 실행합니다. 이 명령은 Compose
구성, API 전체 pytest·Ruff, 웹 strict typecheck·컴포넌트 계약·Playwright 데스크톱·모바일
실제 사용자 여정·production build를 한 번에 확인합니다. API 검증에는 Python 3.12와 `uv`가
필요합니다. E2E는 로컬 고정 API로 `예매 가능`,
`매진`, `예약대기 가능`, `조회 제한`을 재현하므로 철도사 서버를 호출하지 않습니다. 여정·
달력·시간 범위 선택부터 만료된 등록 근거의 단 1회 refresh·새 ID 재등록, 일반실·특실
독립 즉시 등록과 홈 전체 반영, KORAIL 조회 제한 때의 좌석 미관측·행동 및 재조회 숨김을
실제 Chromium 클릭으로 검증합니다. 여정·조회 제한 두 시나리오와 관리자 자격증명 로그인·초기
등록 차단 두 인증 시나리오를 데스크톱·모바일에서 각각 실행한 E2E 8건이 통과하며 GitHub
Actions의 `repository-verify`도 같은 결정적 경로를 실행합니다.

운영사 단발 스모크는 기본 검증과 분리되어 있습니다. 인증된 로컬 서비스와 함께 저장소 밖의
Playwright storage state 또는 실행 프로세스에만 넣은 관리자 ID·비밀번호 쌍 중 하나를 준비한
뒤 명시적으로 실행합니다. storage state에는 세션 쿠키가 포함될 수 있으므로 저장소에 커밋하거나
진단 자료에 첨부하지 않습니다.

```powershell
$env:RUN_LIVE_PROVIDER_SMOKE='1'
$env:E2E_BASE_URL='http://127.0.0.1:4173'
$env:E2E_STORAGE_STATE='C:\private\railwait.storage-state.json'
cd apps/web
npm run test:e2e:live:srt
```

storage-state 파일을 만들지 않으려면 같은 셸 프로세스에서만 다음 두 값을 전달할 수 있습니다.
스모크는 로그인 화면을 정확히 한 번 제출하고 계정을 자동 등록하거나 자격증명을 파일로 저장하지
않습니다. 실행 뒤에는 셸 환경에서도 즉시 제거합니다.

```powershell
$env:E2E_ADMIN_USERNAME='<등록한 관리자 ID>'
$env:E2E_ADMIN_PASSWORD='<등록한 관리자 비밀번호>'
try {
  npm run test:e2e:live:srt
} finally {
  Remove-Item Env:E2E_ADMIN_USERNAME,Env:E2E_ADMIN_PASSWORD -ErrorAction SilentlyContinue
}
```

유효한 storage state와 자격증명 쌍을 함께 주면 storage session을 먼저 사용하고, 만료돼 로그인
화면이 보일 때만 ID·비밀번호로 한 번 fallback합니다. 명시한 storage-state 경로가 상대경로,
저장소 내부, 누락 또는 손상된 JSON이면 자격증명으로 우회하지 않고 구성 오류로 중단합니다.
비밀번호 공백은 그대로 보존하며 trace·video·screenshot·artifact·로그에 기록하지 않습니다.
live credential 실행에서는 입력값을 남길 수 있는 `DEBUG=pw:api`를 사용하지 않습니다. 이 두
변수는 Playwright 프로세스 전용이므로 Compose `.env`나 `.env.example`에 넣지 않습니다.

SRT 스모크는 관리자 인증을 먼저 완료한 뒤 SRT만 선택해 내일 수서→부산 12:00–14:00 시간표를 한 번 조회합니다. 열차
카드에 `official_provider` 근거가 있는 좌석 상태가 하나 이상 보이고, 그 상태에 맞는 공식
예매·취소표 대기·예약대기 CTA가 있는지 확인합니다. 세 환경변수 중 하나라도 빠지면
실행하지 않으며 반복 실행용 CI에는 포함하지 않습니다. 보호 응답이나 미관측 결과가 나오면
재시도하거나 우회하지 않고 원문 응답·URL·header·storage state 없이 정규화한 원인만
텍스트 artifact로 남깁니다. live SRT smoke에서는 trace·video·screenshot도 저장하지 않습니다.

구형 KORAIL 계정 없는 source의 live spec은 보호 응답 진단용으로만 남기며 기본 운영에서는
실행하지 않습니다. KORAIL 주 UI는 시간표 조회와 미관측 운영사 재조회에서 서버 관리형
Chromium 어댑터를 사용하지만, 어댑터는 `experimental-rail` 프로필과 명시적 환경변수가
모두 있을 때만 활성화됩니다. 기본 Compose에서는 비활성이므로 KORAIL 좌석은 근거 없이
추정하지 않고 `unknown`으로 표시합니다. sidecar의 기본 `pydoll` 엔진은 안정판 Google Chrome과
WebDriver 없이 CDP로 통신해 공식 화면의 보이는 컨트롤을 조작하고 결과 DOM을 판독합니다.
시간 carousel 이동에는 화면 안의 viewport를 실제 pointer drag하며, 기존 최종 조회 버튼 raw mouse 제출 구현은
`KORAIL_BROWSER_ENGINE=playwright_direct_cdp`를 명시한 진단용 fallback으로만 남습니다.

2026-07-31 동일한 대전→서울, 2026-08-01, 03:00–08:00 단발 비교에서 Windows PoC의 Pydoll은
전체 10행(KTX 계열 8행, ITX 1행, 무궁화 1행)을 읽었고 Linux sidecar의 기본 Pydoll 엔진은
HTTP 200과 KTX 계열 8행을 반환했습니다. 좌석 상태에는 `available`, `limited`, `sold_out`이
포함됐습니다. 같은 조건의 `playwright_direct_cdp` 엔진은 결과 단계의
`marker_code_8003`을 감지해 내부 HTTP 423으로 닫혔습니다. 이 비교는 sidecar 단발 조회와
KTX 전용 파서 성공 증거입니다. 당시 같은 03:00–08:00 조건의 새 대기 웹 조회는 KTX 계열
8행의 실제 상태·CTA를 표시했고, exact match되지 않은 2행은 미관측으로 유지했습니다. 이후
`더보기` 결과 확장을 적용한 최신 재검증에서는 시간표 10행 모두의 일반실·특실 20개 상태가
공식 관측으로 overlay되어 `예매 가능`·`매진 임박`·`매진`과 상태별 CTA로 표시됐습니다.
같은 날 새 대기 화면의 대전→서울, 2026-08-01, 05:00–09:00 조건도 서버 재조회로 검증했습니다.
공식 시간 picker가 월 이동 화살표와 시간 slider를 별도 DOM으로 렌더링하고 다음 5개 시간 링크에
`aria-disabled=true`를 부여하는 구조를 구분한 뒤, 시간 링크 자체를 실제 pointer click하고
`#startDate`를 exact readback하도록 수정했습니다. sidecar `POST /v1/seat-snapshot`은 HTTP 200을
반환했고 KTX 계열 16행의 일반실·특실 32개 상태가 모두 공식 관측으로 overlay되어 `예매 가능`,
`매진 임박`, `입석+좌석`, `매진`과 상태별 CTA로 표시됐습니다.

같은 날 12:00–18:00 조건의 58개 미확인 좌석은 API 변환 오류가 아니라 sidecar 시간 선택의
`departure_hour_navigate` 실패였습니다. `00:00` PoC는 첫 시간 창이라 carousel 이동을 시험하지
않았고, 12시는 화면 밖 링크를 숨은 DOM에서 직접 누르는 방식이 공식 결과 단계에서 거부됐습니다.
숨은 24시간 catalog는 구조 검증에만 사용하고, 가시 `.slideWrap .slick-list`를 실제 CDP pointer로
드래그한 뒤 Slick 전환이 끝난 활성 12시 링크를 눌러 `#startDate`를 exact readback하도록 수정했습니다.
안정판 Chrome으로 재빌드한 sidecar의 동일 대전→서울, 2026-08-01, 12:00–18:00 단발 요청은
HTTP 200과 KTX 계열 28행·일반실/특실 56개 상태를 반환했습니다. API와 sidecar를 재생성한 뒤
최종 재검증한 실시간 분포는 `limited` 7개, `sold_out` 30개, `standing_plus_seat` 19개였습니다.
이 수치는 sidecar 응답 검증 결과이며 이번
작업에서는 로그인된 웹 화면의 overlay를 다시 완료했다고 기록하지 않습니다.
background 장시간 안정성은 아직 완료로 간주하지 않습니다.
Browser Companion과 `official_page_browser_companion` snapshot은 기존 설치·데이터를 위한 레거시
호환 경로이며 새 대기 주 UI에는 확장 설치나 `공식 좌석 상태 가져오기` 흐름이 없습니다.
기본 `설정 > 로그·진행 상태`에도 연결 코드·pairing UI를 표시하지 않고 서버 Chromium과 SRT
live source의 정규화된 `ready|cooldown` 상태만 표시합니다.

전체 컨테이너 계약은 다음으로 확인합니다.

```powershell
docker compose -f compose.yml config --quiet
docker compose -f compose.yml build
```

`npm run verify`에는 화면 route mock과 별도로 격리 Compose full-stack E2E가 포함됩니다. 이
시나리오는 매 실행마다 임시 project·포트·PostgreSQL·Redis·Caddy volume과 무작위 테스트
비밀값을 만들고 실제 FastAPI·PostgreSQL·Redis·Caddy·웹·Celery worker·scheduler·KORAIL
Chromium sidecar를 함께 기동합니다. KORAIL 좌석 응답을 직접 흉내 내는 fake stub 대신 실제
sidecar의 기본 Pydoll 엔진이 `app` 내부망의 고정 HTML fixture에서 보이는 역·날짜·시간·조회
컨트롤을 WebDriver 없이 CDP로 조작하고 결과 DOM을 정규화합니다. 같은 테스트 이미지에는
시간 carousel의 현재 `slick-current` 페이지와 인접 사전 렌더링 페이지가 함께 보이는 경우도
포함합니다. 인접 페이지의 비활성 시각은 선택값으로 오인하지 않고 한 페이지씩 이동한 뒤,
적용된 날짜·시각을 모두 정확히 다시 읽습니다. 같은 테스트 이미지에는
명시적 `playwright_direct_cdp` 엔진의 Chromium lifecycle·raw mouse 제출 회귀도 포함됩니다. API는 그 결과를
구간·날짜·인원·열차번호·출발시각과 exact match해 KORAIL의 `official_provider` 상태와 상태별
공식 예매·대기 CTA로 반영합니다. 격리 환경은 KORAIL 좌석 감시 3중 opt-in을 켜 매진 특실
대기 1건을 evidence-bound로 등록하고, 같은 사용자 흐름에서 SRT 좌석 등급 3건을 더해 총 4건을
검증합니다. worker의 KORAIL `watching`과 SRT `watching`·`seat_found`·`official_waitlist` 전이,
운영사별 실행 임대 획득·해제, 예약 시도 0건, 알림 outbox 생성·
재시도를 검증합니다. sidecar와 fixture page는 `app` 내부망만 사용하고 `browser-egress`에 연결하지
않으므로 실제 KORAIL·SRT endpoint 호출은 0건입니다. 이 합성 결과는 실제 KORAIL 공식 좌석
snapshot 성공 증거가 아니며, 성공·실패 뒤 컨테이너와 volume을 삭제합니다.

브라우저 자동화 휴리스틱을 연구할 때는 실제 철도사 endpoint 대신 로컬 합성 PoC를
사용합니다.

```powershell
python -m unittest discover docs/research/poc/browser_defense_lab -p "test_*.py"
python docs/research/poc/browser_defense_lab/lab.py
```

이 실험은 클라이언트 전용 게이트의 한계와 서버 측 replay·요청 예산 집행을 비교하며,
KORAIL·SRT의 실제 감지 신호나 우회 가능성을 증명하지 않습니다. 상세 범위와 예상 결과는
[브라우저 자동화 방어 취약성 로컬 PoC](docs/research/poc/browser_defense_lab/README.md)를
참고하세요.

운영과 복구 절차는 [운영 가이드](docs/OPERATIONS.md), 내부 구조는 [아키텍처](docs/ARCHITECTURE.md), 자동화 경계는 [정책과 안전](docs/POLICY_AND_SAFETY.md)을 참고하세요. 모듈형 모놀리스 전환의 의존 규칙은 [코드 컨벤션](docs/CODE_CONVENTIONS.md), 단계·완료 기준·rollback은 [클린 구조 리팩터링 계획](docs/REFACTORING_PLAN.md)에 기록합니다. 티캣·레일픽의 공개 구현 단서와 레일웨잇에 적용한 결정은 [벤치마크 구현 연구](docs/research/APP_IMPLEMENTATION_STUDY.md), 설치 split 무결성·도구별 산출물·주요 locator는 [정적 분석 근거 명세](docs/research/benchmark-audit/2026-07-29/EVIDENCE_MANIFEST.md), 현재 코드에 연결할 데이터 모델·provider·worker 순서는 [리버싱 결과 접목 계획](docs/research/APP_REVERSE_ENGINEERING_INTEGRATION_PLAN.md), 공식 페이지의 공개 감지 신호는 [공식 페이지 자동화 감지 연구](docs/research/OFFICIAL_AUTOMATION_DETECTION.md), 계층별 동작과 PoC 상태 전이는 [브라우저 자동화 방어 동작 로직 기술 보고서](docs/research/OFFICIAL_BROWSER_DEFENSE_TECHNICAL_REPORT.md), `/dynaPath.do`·NetFUNNEL 함수군은 [공개 JavaScript 정적 분석](docs/research/OFFICIAL_BROWSER_JAVASCRIPT_CODE_ANALYSIS.md), KORAIL 프론트 로더와 업무 응답의 단계별 흐름은 [KORAIL 프론트 동적 로더 동작 플로우 보고서](docs/research/KORAIL_FRONT_LOADER_FLOW_REPORT.md), 수동 성공과 자동화 실패 분기점의 공개 라이브러리별 구현 가능성은 [KORAIL 수동·자동 분기점 심층 연구](docs/research/KORAIL_MANUAL_AUTOMATION_DIVERGENCE_DEEP_DIVE.md), CDP 기반 UI 단발 조회의 method·path·status 분류는 [브라우저 네트워크 캡처 보고서](docs/research/BROWSER_NETWORK_CAPTURE_REPORT.md), 구현·검증 상태는 [CHECKLIST.md](CHECKLIST.md)에 보수적으로 기록합니다.

2026년 7월 30일 수동 Chrome CDP 성공 캡처와 자동화 실패 캡처의 차이, 서버 측 보호 응답 판정,
제품에 접목 가능한 안전 경계는 [KORAIL 수동 CDP 캡처와 자동화 실패 캡처 비교](docs/research/KORAIL_MANUAL_CDP_VS_AUTOMATION_ANALYSIS.md)에 별도로 기록합니다.
같은 분석의 동작 구조와 시각화는 [KORAIL 프론트 동적 로더 동작 플로우 보고서](docs/research/KORAIL_FRONT_LOADER_FLOW_REPORT.md)에 정리합니다.
공개 자동화 라이브러리로 UI 흐름은 구현할 수 있지만 서버 수락 동일성을 보장할 수 없다는 심층 판정은
[KORAIL 수동·자동 분기점 심층 연구](docs/research/KORAIL_MANUAL_AUTOMATION_DIVERGENCE_DEEP_DIVE.md)에 기록합니다.

2026년 7월 30일 SRT 공식 동일 세션 UI와 현재 `SrtLiveSeatSource`가 좌석 상태까지 도달한
단발 결과, KORAIL cooldown 중단과 미검증 범위는
[실제 좌석 조회 경로 검증](docs/research/LIVE_SEAT_ROUTE_VALIDATION.md)에 기록합니다.

## 프로젝트 상태

현재는 개인 장비에서 검증하는 v1입니다. 외부 철도사 계정이나 결제정보 없이 mock provider의 관측·예약 1회·결제 필요 상태 머신과 공식 시간표 경계를 검증할 수 있습니다. 이 mock 실행은 실제 잔여석이나 예약을 뜻하지 않습니다. 실제 TAGO 시간표 호출은 발급받은 서비스 키가 있을 때만 활성화되며, 실제 알림 채널 전송과 배포 origin별 관리자 등록·로그인은 운영자가 자신의 도메인으로 별도 smoke test해야 합니다.

`설정 > 철도 계정`에서는 KORAIL·SRT 각각 `회원번호 / 이메일 / 휴대전화` 로그인 방식을 명시해 연결합니다. 저장 요청은 시간표 조회나 예약 없이 해당 운영사 로그인을 한 번만 확인하며, 성공한 경우에만 ID·비밀번호·로그인 방식을 암호화해 DB에 저장하고 `로그인 확인됨`으로 표시합니다. 로그인 확인된 계정의 운영사를 선택한 새 대기는 `좌석 재발견마다 자동 예매(에피소드당 1회·결제 전 중단)`를 기본으로 선택하고, 사용자는 `알림만 받기`로 명시적으로 바꿀 수 있습니다. 선택 운영사 중 계정이 연결되지 않았거나 비활성·미인증이면 안전하게 `notify_only`가 기본값이며 자동 예매 선택은 비활성화합니다. KORAIL은 기존 검색·예약 인증 세션을 폐기한 새 Pydoll CDP context에서 입력한 계정을 다시 증명하므로 이전 로그인 상태를 새 계정의 성공으로 오인하지 않습니다. 실패·접근 제한·응답 지연이면 새 값은 저장하지 않고 기존 연결도 교체하지 않습니다. 입력란은 관리자 로그인 비밀번호의 브라우저 자동 채움을 차단하고, validation 오류도 원문 비밀번호를 응답하지 않습니다. 카카오·애플·간편인증·모바일신분증·비회원 예매는 사용자 상호작용이 필요한 별도 흐름이므로 서버 상주 계정 연결에 사용하지 않습니다. 실제 비밀번호를 채팅·로그·스크린샷에 넣지 말고 설정 화면에서만 입력합니다.

새 대기의 역 목록은 TAGO 열차정보의 원본 식별자와 [KORAIL 공개 역 안내](https://www.korail.com/public/st_info/station_data.json)의 역명 교집합으로 구성합니다. migration `0007`이 원본 식별자 목록과 화면 표시 목록을 PostgreSQL 스냅샷으로 함께 보존하며, DB lease와 fencing으로 한 수집자만 갱신 결과를 기록합니다. 신선한 스냅샷으로 재시작하면 상류 호출이 없고, 24시간이 지난 스냅샷은 즉시 제공하면서 백그라운드에서 갱신합니다. 갱신 실패·빈 결과·손상 응답은 마지막 정상 스냅샷을 덮지 않으며 정상 스냅샷이 없으면 `503`으로 닫습니다.

원본 TAGO 식별자는 시간표의 node ID·역명 검증에 사용하지만 화면에는 교집합만 반환합니다. 광운대·노량진·신도림·서빙고·왕십리·옥수처럼 통근 성격의 역은 선택 목록에서 제외하고, 서울·수서·대전·부산 sentinel이 없는 KORAIL 역 안내는 거부합니다. 교집합을 만들 수 없을 때 원본 목록으로 조용히 되돌아가지 않습니다. 이 필터는 역을 찾기 쉽게 하는 장치일 뿐 KORAIL/SRT 소속이나 특정 날짜의 실제 운행을 증명하지 않으며, 그 여부는 선택 날짜·구간의 시간표 결과로 판단합니다.

새 대기 화면에는 별도의 자동화 모드 카드·토글이 없습니다. SRT는 `SRT_SEAT_STATUS_ENABLED=true`일 때 사용자 시간표 요청에서 SRTrain 2.6.7의 계정 없는 검색을 한 번 수행합니다. KORAIL의 주 UI 좌석 보강은 시간표 조회와 미관측 운영사 재조회가 서버 관리형 Chromium 어댑터로 이어지는 경로입니다. 이 어댑터는 선택적인 `experimental-rail` 프로필에서만 실행됩니다. 기본 Pydoll 엔진은 WebDriver 없이 CDP로 공식 `/ticket/search/general` 화면의 보이는 역·날짜·시간·인원·조회 컨트롤을 조작하고 exact readback 뒤 결과 DOM을 판독합니다. 첫 정상 조회가 만든 공식 same-origin POST template과 KORAIL 쿠키는 sidecar 프로세스 메모리의 구간별 lease로만 넘기고 Chromium을 닫습니다. 최대 4개인 bounded LRU pool이 서로 다른 활성 구간을 보존하므로 A→B→A 전환에서도 A lease를 다시 사용합니다. 같은 출발·도착 구간의 후속 조회는 multipart의 검증된 `txtGoAbrdDt`와 `txtGoHour` byte span만 바꿔 해당 `httpx.AsyncClient`로 실행합니다. opaque path·query·header 값·body·cookie는 직렬화·파일·DB·로그로 내보내지 않으며 query는 캡처 URL에서 해석·재구성·변경하지 않습니다. redirect와 다른 origin·port·userinfo·fragment가 있는 target은 거부합니다. 구간별 수명·횟수 만료와 LRU 용량 초과는 outbound POST 전에 해당 lease만 버리고 cold init부터 시작합니다. 401, 동일 origin 로그인 경로 redirect, 명시적인 로그인 HTML처럼 세션 만료가 확인된 경우에는 선택한 lease를 버리고 같은 읽기 조회를 cold init으로 한 번만 복구합니다. 로그인 검증·예매·sidecar 종료 전에는 모든 read-only lease를 폐기해 인증 browser context와 섞이지 않게 합니다. cookie·capture·응답 schema·cursor 불일치와 그 밖의 4xx는 상태를 추정하거나 같은 요청에서 재시도하지 않고 fail-closed하며, 403·429·`-1405`·`-8002`·`-8003`·`macro_err1`·CAPTCHA·NetFUNNEL은 cooldown으로 중단합니다. 기존 Chromium 직접 실행·loopback Playwright CDP·raw mouse 제출 엔진은 `KORAIL_BROWSER_ENGINE=playwright_direct_cdp`를 명시한 경우에만 선택되며 요청 실패 뒤 자동 engine fallback은 없습니다. `EXPERIMENTAL_RAIL_ENABLED=true`, `KORAIL_BROWSER_ADAPTER_ENABLED=true`, 32바이트 이상 내부 token이 있어야 요청 시점 조회가 열리고, `KORAIL_SEAT_MONITORING_ENABLED=true`까지 있어야 background 감시가 열립니다. 기본 Compose에서는 모두 실행되지 않습니다. 비활성·실패·보호 응답에서는 좌석을 추정하지 않고 `unknown`으로 닫습니다. Browser Companion은 주 UI에서 제거된 레거시 호환 경로입니다. 읽기 경로 자체는 로그인·예약·결제를 수행하지 않으며, 별도 자동 예매 gate와 활성 계정·작업 정책이 모두 있는 경우에만 관리형 로그인과 예매 버튼을 가용성 에피소드당 1회까지 진행합니다. 결제는 항상 수행하지 않습니다.

같은 운영사의 요청은 한 번에 하나만 실행하고 동일 조건은 singleflight와 기본 20초 TTL cache로 병합합니다. SRT background 관측은 같은 출발역·도착역·KST 서비스일·인원 요청을 `00:00–23:59` 하루 조회 하나로 합쳐, 서로 다른 관심 열차도 같은 상류 결과를 재사용합니다. 각 후보에 반영할 때는 열차번호·서비스일·출발시각·좌석 등급을 다시 exact match합니다. Chromium sidecar와 API 사이는 별도 Bearer token으로 인증하고 proxy 환경변수를 신뢰하지 않습니다. sidecar의 CDP port는 `127.0.0.1`에만 임시로 열고 외부에 노출하지 않습니다. Pydoll cold init과 그 결과에서 만든 메모리 전용 HTTP lease는 구간별 기본 최대 1800초·100회 검색, 전체 최대 4개 LRU로 제한됩니다. 한도·선택 구간 오류·LRU 축출에서는 해당 cookie jar와 template만 제거하고, 인증 전환·취소·종료에서는 pool 전체를 제거합니다. 전역 직렬화는 유지하므로 여러 lease가 동시에 상류 요청을 보내지 않습니다. 응답의 구간·날짜·인원·열차번호·출발시각이 요청과 모두 일치할 때만 상태를 합칩니다. 429는 설정된 rate-limit cooldown 동안, `CODE -1405`, `CODE -8002`, `CODE -8003`, `macro_err1`, CAPTCHA, NetFUNNEL 또는 동등한 보호 응답은 기본 5분의 별도 protection cooldown 동안 해당 운영사 조회를 중단합니다. KORAIL의 일반 장애는 provider 전체가 아니라 exact query별로 30초부터 최대 300초까지 backoff하여 다른 서비스일·시간창 조회를 계속 허용합니다. API가 sidecar 전체 결과를 기다리는 제한시간은 기본 90초, 브라우저 내부의 개별 UI 대기는 기본 25초로 분리합니다. provider-wide cooldown 중에는 상류 요청을 보내지 않고 최근의 신선한 cache만 재사용하며, 근거가 없으면 `provider_access_restricted` 또는 `source_unavailable`인 `unknown`으로 fail-closed합니다.

보호 중단의 내부 진단은 원문 body·URL·header 없이 `http_403_main`,
`http_403_subresource`, `marker_code_1405`, `marker_code_8002`, `marker_code_8003`, `marker_macro_err1`,
`marker_captcha`, `marker_netfunnel`, `marker_abnormal_access` 중 하나와 실행 stage만
sidecar 로그에 남깁니다. API와 사용자 화면에는 계속 `provider_access_restricted`만 반환합니다.

선택 실행형 KORAIL live smoke는 먼저 인증된 `GET /api/v1/seat-status/status`를 확인합니다.
cooldown이면 시간표를 한 번도 호출하지 않고 허용된 원인·남은 시간만 sanitized artifact로 남긴
뒤 skip합니다. `ready`일 때만 `GET /api/v1/providers`의 실행 capability와 KORAIL 시간표 한 건을
조회합니다. 저장소 밖의 읽을 수 있는 storage-state와 실제 로그인 세션을 먼저 확인하고,
KORAIL만 선택한 서울(`NAT010000`)→부산(`NAT014445`)·내일 KST·12:00–18:00·성인 1명 요청이
정확히 한 번 전달되며 SRT 시간표 요청은 0건이어야 합니다. 같은 열차의 일반실·특실은 정확한
source, 신선하고 timezone이 있는 `observed_at`, 상태별 capability CTA를 가져야 하고 최소 한
좌석은 실제 행동 가능한 관측 상태여야 합니다. 이 읽기 전용 smoke는 `/providers`의 KORAIL
`seat_monitoring=true`, `reservation_once=false`를 필수로 검증합니다. 이 범위에서는 매진·예약대기
좌석의 exact-match evidence와 대기 행동만 확인하고, 예매 가능 좌석의 감시 등록 또는 예약 시도는
만들지 않습니다. 명시적 live gate는 cooldown이나 미관측을 skip 성공으로 처리하지 않습니다.

인증된 `GET /api/v1/seat-status/status`는 KORAIL browser와 SRT live 좌석 조회 제공원의 현재
`ready|cooldown`, 허용 목록의 원인, 남은 초만 `Cache-Control: no-store`로 반환합니다. 이는
Redis 좌석 조회 cooldown의 현재 상태이며 PostgreSQL에 영속되는 worker `ProviderCircuit`과는
별도입니다. 설정의 `로그·진행 상태 > 좌석 조회 제공원 상태`에서 같은 구분을 확인합니다.

선택한 작업을 만든 직후 웹이 `startWatch`를 호출하는 것과 요청 시점 좌석 보강은 별개입니다. 기본 설정에서는 KORAIL·SRT background 감시와 자동 예매가 모두 꺼져 있습니다. KORAIL은 `EXPERIMENTAL_RAIL_ENABLED=true`, `KORAIL_BROWSER_ADAPTER_ENABLED=true`, `KORAIL_SEAT_MONITORING_ENABLED=true`, SRT는 `EXPERIMENTAL_RAIL_ENABLED=true`, `SRT_SEAT_STATUS_ENABLED=true`, `SRT_SEAT_MONITORING_ENABLED=true`를 모두 명시한 경우에만 worker 관측을 수행합니다. 유효한 evidence와 실행 capability가 있으면 `available`·`limited`·`standing_plus_seat`를 포함한 선택 좌석도 감시 작업으로 등록할 수 있습니다. `sold_out`은 `watching`을 유지하며 다음 관측을 예약하고, `available`·`limited`·`standing_plus_seat`는 `seat_found`, `waitlist_available`은 `official_waitlist`로 전이합니다. `seat_found`와 `official_waitlist`도 마지막 선택 열차의 출발시각과 사용자가 지정한 감시 종료시각 중 이른 시점까지 계속 관측합니다. 후속 관측에 따라 두 상태 사이를 이동하거나, 모든 좌석이 다시 비행동 상태가 되면 `watching`으로 복귀합니다. 같은 상태의 반복 관측은 상태 전이와 알림 outbox를 다시 만들지 않습니다. 출발 2~24시간 전은 unchanged backoff 없이 평균 60초, 2시간 이내는 평균 30초에 ±20% jitter로 다음 관측을 예약하고, 5초 beat가 due 시각을 깨웁니다. 24시간보다 먼 작업은 기존 5분·10분 base와 최대 3배 unchanged backoff를 유지합니다. 로그인 확인된 계정으로 만든 새 작업은 기본 `reserve_once_before_payment`이며, 운영사별 `*_RESERVATION_ONCE_ENABLED=true`, 활성 철도 계정, 작업별 정책까지 모두 설정된 작업만 `seat_found → reserving → payment_required`를 같은 가용성 에피소드당 한 번 수행합니다. 이 조건을 갖춘 작업의 start는 commit 뒤 `process_watch_now`를 best-effort로 즉시 enqueue하며, enqueue 오류에도 시작 자체는 성공하고 5초 beat가 due 작업을 다시 찾습니다. 한 due sweep에서는 KORAIL과 SRT 파이프라인을 서로 병렬 실행하되, 같은 운영사·계정의 관측과 예약은 120초 실행 lease와 단일 인증 actor 안에서 직렬 처리해 중복 임시예약을 막습니다. `NOT_AVAILABLE`은 예약 시점의 확정 비가용 근거이므로 그 뒤 첫 행동 가능 관측에 경쟁 소실 보정 시도를 한 번 허용합니다. 이 보정도 `NOT_AVAILABLE`이면 연속 관측으로 반복하지 않고 이후 확정 비가용 관측과 새 재출현이 차례로 생긴 에피소드에서만 다시 시도합니다. `AUTH_REQUIRED`는 해당 시도보다 새로운 성공 로그인 검증 세대에서만 한 번 재무장합니다. 일반 `FAILED`·`UNKNOWN`·`PROVIDER_BLOCKED`, 이미 만료된 결제기한, worker 재시작 뒤 stale `PENDING → UNKNOWN`, `PAYMENT_REQUIRED`·`RESERVED`는 결과 자체만으로 자동 재시도하지 않습니다. 최초 `UNKNOWN` 시도의 공식 확인이 exact `NOT_FOUND`로 끝난 경우에만 같은 연속 `AVAILABLE` 구간에서 한 번 재무장하며, 이 시도도 `UNKNOWN`이면 추가 호출하지 않습니다. 결제와 결제정보 입력·저장은 하지 않습니다. KORAIL 보호 응답 cooldown 동안에는 새 sidecar 요청을 보내거나 다른 세션·IP로 전환하지 않습니다. 시간표 stale response 차단용 query key에는 승객 수도 포함합니다.

기한이 없거나 이미 지난 기존 `payment_required` 행도 방치하지 않습니다. worker는 같은 credential generation의 인증 actor에서 공식 예약목록을 30초 간격·최대 3회 읽기 전용으로 다시 확인하고, 결제기한이 경과한 뒤 `post_deadline_reconciled_at` marker가 없을 때 최종 확인을 한 번 추가합니다. exact 미결제 보류가 미래 기한을 제공하면 공식 기한과 handoff를 보정합니다. 정상 로드된 공식 예약목록에서 exact 대상 0건인 `NOT_FOUND`뿐 아니라 exact 행의 공식 기한 자체가 최종 확인 시각 이하인 경우도 행동 가능한 보류 종료로 처리합니다. `notify_only`는 이력을 보존한 `expired`, `reserve_once_before_payment`는 `watching`으로 복귀합니다. 이전 버전이 과거 기한 exact 행에 marker만 남긴 경우 같은 actor의 호환성 정리 확인을 한 번 더 수행합니다. 기한 없는 exact 행·중복 일치·인증 실패·차단·목록 로드 불확실은 fail-closed로 유지합니다. 감시 복귀만으로 기존 `PAYMENT_REQUIRED` episode fence를 해제하지 않으며, marker 뒤 확정 비가용 관측과 새 행동 가능 관측이 차례로 생긴 경우에만 새 episode에서 한 번 다시 시도합니다. 과거 버전에서 결제기한과 marker가 모두 누락됐지만 공식 exact `NOT_FOUND` 확인 시각이 남은 행은 그 확인 뒤의 행동 가능 관측을 근거로 `confirmed-absent-retry:<attempt>` 한 번만 허용하고, 같은 근거로 재연쇄하지 않습니다.

Celery는 매 작업마다 새 asyncio event loop를 사용하므로 KORAIL·SRT source와 async Redis client도
프로세스 전역으로 캐시하지 않고 작업 단위로 소유합니다. 같은 작업 안에서는 provider별 adapter
하나를 여러 dedupe 그룹이 공유해 singleflight·cache를 유지합니다. timeout 뒤에도 남아 있을 수
있는 실제 `to_thread` 호출은 그룹의 DB 실행 임대를 해제하기 전에 drain하고, 모든 그룹이 끝난
뒤 Redis와 adapter를 닫습니다. 이 순서는 다음 작업에서 닫힌 loop의 client를 재사용하거나,
임대 밖에서 늦은 상류 호출이 계속되는 것을 막습니다.

migration `0010`은 시간표 응답의 좌석 등급별 상태와 provenance를 서버 발급 `timetable_seat_evidence`로 보존하고 `watch_candidates`에 선택 근거를 연결합니다. 등록 유효창이 지난 토큰, 다른 역·열차·출발시각·인원·좌석 등급의 토큰은 거부합니다. 이 값은 등록 당시 화면을 설명하는 감사 snapshot일 뿐 `seat_observations`를 만들거나 worker 조회·알림·예약을 시작하지 않습니다. 홈은 작업의 `updated_at`을 좌석 확인 시각으로 표시하지 않고 이 snapshot의 실제 관측 시각 또는 미관측 사유를 사용하며, migration 이전 작업은 `등록 근거 없음`으로 구분합니다.

mock provider와 개발 데모는 선택한 출발역·도착역과 시간창 전체를 반영한 `데모 시간표`를 생성하고, 일반실·특실별 `예약 가능`, `매진 임박`, `입석+좌석`, `매진`, `예약대기 가능`, `지연`, `오류` 예시로 카드와 CTA 계약을 검증합니다. 고정된 소수 열차 fixture나 화면 표시 개수 제한은 사용하지 않습니다. 이 값은 시간표와 좌석 모두 명시적인 mock 벤치마크 데이터이며 실제 TAGO 시간표나 KORAIL·SRT 잔여석으로 표시하거나 저장하지 않습니다. 좌석 행동을 누르면 별도 확인 단계 없이 `train + seat_class`별 작업 하나를 생성하고 즉시 시작합니다. 따라서 같은 열차의 일반실과 특실, 여러 열차를 각각 독립 작업으로 등록할 수 있습니다.

열차별 실시간 좌석 재고를 제공하는 일반 공개 API는 확인되지 않았습니다. 현재 SRT는 사용자 시간표 조회와 명시적으로 활성화한 background worker에서 계정 없는 라이브러리 결과를 사용합니다. 이 실험 관측 경로는 예약·결제를 수행하지 않으며 운영 허가를 대신하지 않습니다. KORAIL은 `experimental-rail`을 명시적으로 활성화한 경우에만 서버 Chromium이 공식 화면의 보이는 컨트롤을 단발 조작하고 렌더된 DOM을 읽으며, 기본 배포와 보호 cooldown에서는 미관측으로 닫습니다. Browser Companion snapshot은 기존 데이터 호환을 위해서만 읽습니다. 공식 예약, 장기 운영, 재배포가 필요한 좌석 연동에는 KORAIL·SR 또는 정부 디지털서비스 개방의 정식 제휴 규격과 목적별 호출 권한이 필요합니다. 공개·제휴 경로별 조사 결과와 필요한 계약 조건은 [공식 좌석 데이터 연동 경로](docs/research/OFFICIAL_SEAT_DATA_PATHS.md), 명세 수령 전후의 코드 seam과 완료 조건은 [승인 Provider 연동 준비 상태 감사](docs/research/APPROVED_PROVIDER_INTEGRATION_READINESS.md)에 기록합니다. 공개 anti-bot 저장소를 우회 코드가 아닌 정상 브라우저 신뢰성 관점에서 선별한 결과는 [브라우저 자동화 저장소 선별 기록](docs/research/ANTI_BOT_REPOSITORY_SCREENING.md)에 분리했습니다.

티캣·레일픽의 정식 설치 앱은 로그인·예약 없이 사용자 화면을 관찰했고, 별도로 식별 가능한 APK와 JADX 산출물에서 Android background component, 좌석 상태 리소스·모델, 저장소·WebView·SDK 구성을 정적으로 확인했습니다. 레일픽 parser 필드와 공개 소스 비교 자료는 공통 상태 모델을 교차 확인하는 데 사용했으며 비공개 철도 endpoint를 제품 명세로 복제하지 않았습니다. 계정 없는 SRT 관측은 명시적 3중 opt-in과 DB 실행 임대 아래의 실험 기능이며 예약 capability로 승격하지 않습니다. KORAIL 제한 응답에는 추가 요청이나 우회를 수행하지 않습니다. 확인·추정·미확인 범위는 [벤치마크 구현 연구](docs/research/APP_IMPLEMENTATION_STUDY.md)에 기록합니다.

새 대기의 요일 버튼은 반복 감시 요일을 만드는 기능이 아닙니다. 누른 요일과 가장 가까운 실제 출발일로 날짜를 이동하는 빠른 선택이며, 달력에서 날짜를 고르면 요일 표시도 함께 갱신됩니다. PC에서는 역·달력을 팝오버로, 모바일에서는 화면 하단 선택 시트로 표시합니다.

3단계 열차 등록에서도 같은 커스텀 달력으로 실제 출발일을 고르고 시간 범위를 바꾸어 다시 조회할 수 있습니다. 달력 날짜를 선택하면 날짜·요일이 함께 바뀌고 즉시 해당 날짜의 시간표를 다시 가져옵니다. 변경 뒤 새 결과에서 누르는 행동에는 새 조건만 사용하며, 이미 생성·시작된 대기는 좌석 버튼의 등록 상태와 홈 활동 목록에서 유지합니다. 화면의 역 선택과 열차 결과에는 내부 수집원 이름인 `TAGO`를 노출하지 않습니다.

새 대기 3단계는 시간표를 조회하거나 조건을 다시 적용할 때 서버가 좌석 상태도 함께 보강합니다. 별도의 `공식 좌석 상태 가져오기` 버튼이나 브라우저 확장 설치는 필요하지 않습니다.

### Official Handoff UX

열차 카드의 `공식 좌석 확인`은 외부 페이지를 즉시 열지 않고 인계 안내를 먼저 표시합니다. 좌석이 미관측인 카드에서는 `관심 열차에 추가`가 주 행동이고 공식 확인은 보조 행동입니다. PC에서는 modal, 모바일에서는 하단 sheet로 표시하며, KORAIL·SRT allowlist에 등록된 공식 HTTPS 주소만 새 탭(`noopener,noreferrer`)으로 엽니다. 날짜·구간·출도착 시각·열차 번호·선택 좌석 등급을 요약하고 `여정 복사`를 제공합니다. URL·클립보드에는 로그인 정보, 쿠키, 토큰, 결제 정보가 들어가지 않습니다.

2026-08-03 재검증에서 SRT가 KORAIL로 넘기는 `/ticket/search/list?...` query는 새 비로그인 세션에서도 출발역·도착역·날짜·조회 시작시각·인원을 복원했고, SRT 연계 확인값과 빈 `reqTime`을 제거해도 실제 열차 목록까지 도달한 단발 표본이 있었습니다. 현재 구현은 이 결과를 그대로 전달하지 않고 공식 역 map에서 같은 레코드의 코드·역명을 확인한 뒤, 편도·직통·성인 1명·일반석·KTX·KORAIL-only로 고정한 25키 `official_search_url`만 별도 생성합니다. 웹도 host·path·단일 키·고정값·날짜·시각·4자리 코드를 다시 검사하며 실패하면 고정 공식 진입점으로 강등합니다. `official_booking_url`은 결제·일반 진입 의미로 계속 분리합니다. 현재 KORAIL SPA는 `txtGoTrnNo`와 구형 `selGoTrain`을 읽지 않으므로 특정 열차 자동 선택을 약속하지 않으며, 별도 격리 단발의 `CODE -8003`처럼 보호 응답이 나오면 재제출하지 않습니다. 전체 역·29개 파라미터 조사와 구현 경계는 [KORAIL 검색 딥링크·역 코드·파라미터 조사](docs/research/KORAIL_DEEPLINK_STATION_PARAMETER_AUDIT.md)에 기록합니다.

좌석 근거는 `벤치마크 데모 상태(mock)`, SRT source 또는 KORAIL 서버 Chromium의 관측값(`official_provider`), 레거시 Browser Companion 단발 값(`official_page_browser_companion`), 기존 확인 기록(`user_confirmed_official_page`), `미관측(not_observed)`으로 구분합니다. 만료·identity 불일치·손상된 provenance는 즉시 미관측으로 강등합니다. 이미 등록된 홈 행은 현재 좌석 주장 대신 `등록 당시 snapshot`임을 명시한 채 관측 시각과 근거를 감사 정보로 보존합니다. demo는 실시간 잔여석이 아니고, 모든 관측값도 공식 플랫폼에서 최종 확인해야 합니다. `not_observed`에서는 대기 등록을 막고 재조회나 공식 확인만 안내합니다. 관측된 `sold_out`과 `waitlist_available`도 실행 `seat_monitoring` capability가 있을 때만 각각 `취소표 대기`, `예약대기` 등록 행동을 표시합니다. capability가 없으면 상태와 공식 인계 CTA만 유지합니다. 새 탭을 연 사실은 좌석 확보·예약대기·예약·결제 성공으로 처리되지 않습니다. CAPTCHA, NetFunnel, `-8002`·`-8003`, 접근 제한에는 전송을 중단하고 공식 앱·홈페이지에서 직접 확인하도록 안내합니다.

인계 안내는 `role=dialog`, `aria-modal`, 제목·설명 연결, Escape 닫기, Tab/Shift+Tab 포커스 트랩을 사용합니다. 열려 있는 동안 앱 본문은 `inert`와 `aria-hidden`으로 비활성화하고, 닫으면 실행 버튼으로 초점을 되돌립니다. 로컬 브라우저에서 1440×1000, 390×844, 320×844, 200% 확대에 해당하는 720×500 reflow를 확인했으며 실제 iOS Safari·Android PWA는 운영 기기 확인 항목으로 남겨 둡니다.
