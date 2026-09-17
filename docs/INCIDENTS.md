# 운영 장애 기록

운영 환경에서 실제로 발생한 장애와 그 해결 과정을 남깁니다. 재발했을 때 같은 진단을 다시 반복하지
않도록 증상, 근본 원인, 조치, 검증 근거, 남은 과제를 함께 적습니다. 최신 기록을 위에 둡니다.

## 2026년 9월 17일 · KORAIL HTTP replay lease 재생 불가로 인한 좌석 관측 대량 실패

### 요약

KORAIL이 좌석 조회 요청 URL을 일회성으로 바꾸면서, 브라우저에서 캡처한 HTTP replay 재료를 브라우저
밖에서 재생할 수 없게 됐습니다. 어댑터는 이 실패를 조회 전체의 실패로 승격해 30초 backoff을 열었고,
그 사이 예정된 관측이 모두 오류로 기록됐습니다. 공식 출처와 브라우저 조회 경로는 정상이었는데도
Oracle 운영 서버의 24시간 좌석 관측 오류율이 93.6%까지 올라갔습니다.

### 영향

- 좌석 관측: 24시간 기준 오류 33,437건 / 전체 35,733건(93.6%). 활성 대기 20건의 좌석 상태가 대부분
  `운행·예매 상태 관측 오류 · 재시도 예정`으로 표시됐습니다.
- 열차 조회 화면: 같은 어댑터를 쓰는 공식 시간표 조회도 backoff에 걸려 TAGO fallback으로 떨어졌고,
  좌석 등급이 `서버 좌석 조회 미설정`으로 표시됐습니다. API 로그에는
  `Official live timetable unavailable provider=korail; trying TAGO fallback` 경고가 남았습니다.
- 알림: 알림 전달 자체는 실패하지 않았습니다(최종 실패율 0.0%). 다만 좌석 관측이 대부분 오류였으므로
  실제 좌석 변화를 놓쳤을 가능성이 있습니다.

### 타임라인

- 2026년 9월 12일 01:45(UTC): 마지막 정상 replay(`event=search_succeeded lease_search_index=100`).
  직후 `lease_retired reason=search_limit`으로 콜드 재초기화가 일어나고 새 lease가 만들어집니다.
- 2026년 9월 12일 01:46(UTC) 이후: 새 lease에서 `event=search_succeeded`가 한 건도 나오지 않습니다.
- 2026년 9월 17일: 대시보드에서 좌석 관측 오류율 93.6%를 확인하고 조사를 시작했습니다.
- 2026년 9월 17일: 수정본을 Oracle과 로컬에 재배포하고 관측 회복을 확인했습니다.

### 근본 원인

Pydoll 검색은 읽기 전용 브라우저 세션에서 공식 business 요청을 한 번 캡처한 뒤, 같은 route의 다음
조회를 그 재료로 재생해 약 1초에 처리합니다. KORAIL은 이 요청 경로를 페이지마다 달라지는
`/web_s/<무작위 경로>?_qzj=<일회성 토큰>` 형태로 바꿨고, 캡처한 URL은 그것을 만든 브라우저 페이지
밖에서 더 이상 유효하지 않습니다. 브라우저 밖 HTTP client로 재생하면 KORAIL이 HTTP 500과
`코레일 승차권예매` 오류 페이지를 돌려줍니다.

이때 replay manager는 이 실패를 typed source unavailable로 승격했고, sidecar의 search coordinator는
exact query에 30초 backoff을 열었습니다. 그 30초 동안 도착한 관측 요청은 전부
`event=provider_query_skipped reason=query_backoff`로 끝나 오류 관측이 됐습니다. 결과적으로 약 52초
주기마다 브라우저 조회 성공 1건과 오류 약 29건이 쌓였습니다.

정리하면 공식 출처 장애가 아니라 임대한 replay 재료만 못 쓰게 된 상태였는데, 그 하나가 정상 동작하는
브라우저 경로까지 막아 세운 구조적 결함이었습니다.

### 진단이 늦어진 이유

replay 실패 경로가 사유를 전혀 로그에 남기지 않았습니다. 남는 것은 상위의
`outcome=source_unavailable stage=http_replay` 한 줄뿐이어서, 캡처가 잘못된 것인지 응답이 깨진
것인지 공식 출처가 죽은 것인지 구분할 수 없었습니다. 실제 원인은 로컬 브라우저 어댑터 이미지에서
계정 로그인 없이 캡처-재생 흐름을 재현하고 원본 HTTP 응답을 직접 관찰한 뒤에야 확정할 수 있었습니다.

### 조치

`apps/api/src/rail_waitlist/korail_sidecar/pydoll/http_replay.py`에서 replay 실패를 lease의 문제로만
다루도록 바꿨습니다.

- invalid capture, invalid response, lease invalid, 일반 source unavailable은 기존 session invalid와
  같이 lease를 폐기하고 `None`을 돌려, caller가 같은 호출에서 cold browser 검색으로 관측을 끝냅니다.
- 실패 사유를 `event=cold_reinit source=http_replay reason=<reason> stage=<stage>`로 남깁니다.
- 이 fallback이 연속 3회 반복되면 `event=capture_suspended reason=repeated_replay_failure`로 900초
  동안 capture를 중단하고, 이후 `event=capture_resumed`에서 다시 시도합니다. 재생할 수 없는 lease를
  반복해 만들지 않기 위한 것입니다.
- 보호(protection), rate limit, 점검 판정은 종전대로 조회를 중단하고 provider cooldown을 엽니다.
  이들은 실제 운영사 신호이므로 브라우저로 우회하지 않습니다.

관측 1건의 비용은 replay 약 1초에서 브라우저 약 21초로 늘어납니다. 같은 route·날짜의 활성 대기는
coordinator의 단일 실행으로 한 번의 조회를 공유하므로, 실제 운영사 호출량은 오히려 줄어듭니다.

### replay lease 동작의 이전과 이후

replay lease는 읽기 전용 브라우저 검색 한 번에서 캡처한 공식 요청 재료를 출발·도착 route별로 임대해
두는 프로세스 내 자원입니다. 유효기간 1800초, 최대 재사용 100회, route별 bounded LRU라는 경계는
이번 변경에서 그대로입니다. 달라진 것은 재생이 실패했을 때 그 실패를 무엇의 실패로 볼 것인가입니다.

정상이던 시기(2026년 9월 12일 이전)에는 브라우저 조회 한 번으로 lease를 만들고 같은 route의 다음
조회 약 100회를 1초 안팎에 처리했습니다. 실제로 한 lease가 `lease_search_index=100`까지 쓰인 뒤
`lease_retired reason=search_limit`으로 정상 은퇴하고 새 lease로 넘어갔습니다.

KORAIL이 일회성 URL을 도입한 뒤에는 lease를 만들자마자 첫 재생이 실패했습니다. 이 실패가 조회 전체의
실패로 승격되면서, 못 쓰는 lease 하나가 정상 동작하는 브라우저 경로까지 30초 동안 막았습니다.

수정 뒤에는 같은 실패가 lease만의 문제로 한정됩니다. lease를 폐기하고 같은 호출에서 브라우저 검색을
이어가 관측을 정상으로 끝내며, 반복되면 캡처 자체를 잠시 멈춰 왕복 비용도 없앱니다.

| 구분 | 고장 이전(정상) | 고장 이후(수정 전) | 수정 이후 |
| --- | --- | --- | --- |
| 재생 성공률 | lease당 최대 100회 성공 | 0회, 첫 재생부터 실패 | 0회, 첫 재생부터 실패 |
| 재생 실패의 의미 | 드물게 발생, 대부분 session invalid | 조회 전체의 실패로 승격 | 임대한 재료만 무효 |
| 재생 실패 후 동작 | lease 폐기 후 브라우저로 회복 | 조회 중단, exact query에 30초 backoff | lease 폐기 후 같은 호출에서 브라우저로 회복 |
| 관측 결과 | 정상 좌석 상태 | backoff 구간의 관측이 모두 오류 | 정상 좌석 상태 |
| 실패 사유 로그 | 해당 없음 | 상위 `stage=http_replay`만 남고 사유 없음 | `cold_reinit ... reason=<reason> stage=<stage>` |
| 캡처 반복 | 필요할 때만 재캡처 | 사이클마다 못 쓰는 lease를 다시 캡처 | 연속 3회 실패 시 900초 캡처 중단 |
| 조회 1건 비용 | 약 1초 | 약 21초 성공 1건 + 오류 약 29건 | 약 21초, 같은 질의는 단일 실행 공유 |
| 보호·rate limit·점검 | 조회 중단과 cooldown | 조회 중단과 cooldown | 변경 없음, 그대로 중단과 cooldown |

KORAIL이 요청 URL을 다시 재생 가능한 형태로 되돌리면 `capture_resumed` 뒤 재생이 성공하면서 예전의
빠른 경로로 자동 복귀합니다. 이 경우 표의 첫 열 동작으로 돌아가며 별도 설정 변경은 필요하지 않습니다.

### 검증

- 재현: 로컬 브라우저 어댑터 이미지에서 계정 로그인 없이 대전→서울 조회를 두 번 수행해, 첫 번째는
  브라우저로 97편을 성공하고 두 번째 replay가 HTTP 500과 오류 페이지로 실패하는 것을 확인했습니다.
- 수정 확인: 같은 재현 흐름에서 세 번의 조회가 모두 실제 공식 결과를 반환하고, 실패한 replay는
  `cold_reinit` 뒤 브라우저 조회로 회복되는 것을 확인했습니다.
- 테스트: replay 계약 테스트를 추가하고 전체 pytest 4404건 통과와 mypy를 확인했습니다. SRT 예약 확정
  테스트 1건과 ruff 포맷 ratchet 실패는 이번 변경 전 HEAD에서도 같게 재현되는 기존 문제입니다.
- Oracle 재배포: `experimental-rail` 프로필 전체를 재빌드·재생성하고 migration 정상 종료와 장기
  서비스 12개 `healthy`를 확인했습니다. 배포 직후 20분 동안 좌석 관측 644건 중 오류 0건, 어댑터 조회는
  모두 `outcome=success`, TAGO fallback 경고 0건이었습니다.
- 로컬 재배포: 같은 커밋으로 `experimental-rail` 프로필을 재빌드·재생성하고 서비스 12개 `healthy`를
  확인했습니다. sidecar `/v1/seat-snapshot` 연속 호출이 모두 200과 31편을 반환하고, lease 생성 뒤 다음
  호출이 `cold_reinit`으로 회복해 성공으로 끝나는 것을 확인했습니다.

### 검증 중 관찰한 동일 계정 이중 실행

로컬 검증을 위해 `experimental-rail` 프로필을 올리자 로컬 어댑터가 KORAIL 로그인 프리웜을 수행했고,
Oracle과 로컬이 같은 계정의 세션을 번갈아 무효화했습니다. 양쪽 어댑터에
`login prewarm completed outcome=auth_required`가 반복되고, Oracle의 예약 확정이
`stage=confirmation_session_unavailable`로 실패했습니다. 좌석 조회는 로그인이 필요 없어 관측은 계속
정상이었고 오류는 전환 시점의 1분 구간에만 몰렸습니다.

이는 기존 운영 지침대로 같은 철도사 계정에서는 단일 활성 배포만 유지해야 한다는 것을 다시 확인해
준 사례입니다. 로컬 검증을 마친 뒤 로컬의 `experimental-rail`·`korail-browser-adapter`·
`srt-provider-adapter`를 정지·제거해 Oracle을 단일 활성 배포로 되돌렸습니다. 로컬에서 좌석·시간표
기능을 다시 확인해야 할 때는 Oracle 쪽 로그인·예약 경로와 겹치지 않는 시점에만 잠시 올립니다.

### 재발을 알아채는 방법

sidecar 로그에서 다음 순서를 봅니다.

- `event=cold_reinit source=http_replay reason=source_unavailable`이 반복되면 replay 재료가 다시
  재생 불가 상태입니다. 이 자체는 관측 실패가 아니며 브라우저 조회로 회복됩니다.
- `event=capture_suspended`가 열린 뒤에는 브라우저 조회만으로 관측이 이어져야 합니다.
- 그런데도 `outcome=source_unavailable`과 `event=provider_query_skipped reason=query_backoff`가 쌓이면
  그때는 브라우저 경로까지 실패한 것이므로 공식 출처 장애로 다룹니다.

### 남은 과제

- 대시보드의 24시간 누적 좌석 관측 오류율은 과거 오류가 빠질 때까지 높게 보입니다. 실제 운영 표본에서
  정상 범위로 내려가는지 확인이 필요합니다.
- 브라우저 전용 관측으로 전환된 상태에서 같은 route·날짜 활성 대기가 coordinator 단일 실행을 공유해
  `next_check_at`이 계속 전진하는지, 관측 주기가 길어진 것이 알림 적시성을 해치지 않는지 확인이
  필요합니다.
- TAGO fallback의 좌석 미관측 사유가 `source_not_configured`("서버 설정을 확인하세요")로 표시됩니다.
  실제로는 설정 문제가 아니라 일시적인 공식 조회 실패이므로 문구가 오해를 줍니다. 실제 실패 사유가
  전달되도록 고치는 것이 남아 있습니다.
- KORAIL이 일회성 URL을 되돌리면 `capture_resumed` 뒤 replay가 다시 성공하며 자동으로 빠른 경로로
  돌아옵니다. 별도 조치는 필요하지 않습니다.

### 관련 문서

- [설치·운영 가이드](OPERATIONS.md): 로그 해석과 `운행·예매 상태 관측 안내가 보임` 문제 해결
- [시스템 구조](ARCHITECTURE.md): replay manager의 소유 범위와 fallback 계약
- [확인 목록](../CHECKLIST.md): 이번 장애의 확인 완료 항목과 남은 운영 확인 항목
