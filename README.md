# 레일웨잇

KTX·SRT 관심 열차를 한곳에서 찾고 관리하는 개인용 웹 앱

좌석 감시를 활성화하면 관심 열차의 상태 변화를 확인해 알림을 받을 수 있습니다. 모바일과 PC에서 사용할 수 있으며, 개인 서버에 직접 설치해 운영합니다.

[![핵심 저장소 검증 상태](https://github.com/hostkimjang/korail-auto-waitlist/actions/workflows/web-journey.yml/badge.svg)](https://github.com/hostkimjang/korail-auto-waitlist/actions/workflows/web-journey.yml)
[![실험 Chromium 검증 상태](https://github.com/hostkimjang/korail-auto-waitlist/actions/workflows/experimental-browser.yml/badge.svg)](https://github.com/hostkimjang/korail-auto-waitlist/actions/workflows/experimental-browser.yml)

[![여정 등록부터 알림과 공식 채널 안내까지 보여주는 레일웨잇 소개 영상](docs/media/railwait-intro.gif)](docs/media/railwait-intro.mp4)

연출된 고정 데모입니다. 철도사 비공식 프로젝트이며 자동 결제를 제공하지 않습니다.

## 이런 분을 위해 만들었습니다

- 매진된 열차를 여러 번 다시 검색하고 있는 분
- KTX와 SRT의 관심 열차를 한곳에서 관리하고 싶은 분
- 좌석이 바뀌었을 때 알림을 받고 싶은 분
- 개인 서버에서 직접 운영할 수 있는 도구가 필요한 분

## 주요 기능

- 공공데이터 API 키를 설정한 KTX·SRT 열차 검색
- 코레일 공식 조회가 접속 대기에 들어가면 대기 상태와 앱에서 측정한 경과 시간을 표시
- 코레일 검색 공지의 내용이 바뀌거나 여러 공지가 이어져도 확인된 공지 구조의 닫기 버튼을 처리하고 열차 목록 조회를 이어가며, 좌석 조회 실패 원인을 구분해 표시
- 좌석 확인 기능을 켰을 때 일반실·특실별 대기 등록
- 좌석 상태와 확인 시각 표시
- 감시 중인 열차의 실제 열차 종류·번호와 결제 필요 항목을 한눈에 확인
- 운영사 예약 결과에서 확인된 호차·좌석번호 표시
- Chrome·Edge·모바일 등 연결한 각 기기에 함께 보내는 Web Push와 Telegram·Discord·Webhook 알림
- 공식 예약·결제 페이지를 새 브라우저 창으로 여는 안전한 인계와, 경로별 실기기 검증 뒤에만 켤 수 있는 코레일+·SRT 앱 인계
- 모바일 PWA와 데스크톱 반응형 화면

## 지금 버전 확인하기

모든 화면 아래 푸터에 `레일웨잇 v1.0.0`과 `2026-09-18 (금) 업데이트`처럼 릴리스 버전과 업데이트 날짜가 요일과 함께 표시됩니다. 재배포한 뒤 브라우저를 새로고침했을 때 이 값이 [업데이트 노트](CHANGELOG.md) 맨 위 항목과 같으면 최신 화면을 받은 것입니다. 값이 그대로면 새로고침을 한 번 더 하고, 그래도 바뀌지 않으면 웹 이미지를 다시 만들었는지 확인하세요.

## 화면 미리 보기

[![자동 예매 선택부터 좌석 발견, 예매 진행과 결제 전 중단까지 보여주는 레일웨잇 데모](docs/media/railwait-demo.gif)](docs/media/railwait-demo.mp4)

고정 예시 데이터만 사용하며 실제 좌석·외부 요청·예약 성공을 나타내지 않습니다.

## 먼저 체험해 보기

Node.js 22가 설치되어 있다면 Docker나 외부 API 없이 데모를 실행할 수 있습니다.

Linux Bash:

```bash
cd apps/web
npm ci
export VITE_DEMO_MODE=true
npm run dev
```

Windows PowerShell:

```powershell
cd apps/web
npm ci
$env:VITE_DEMO_MODE="true"
npm run dev
```

브라우저에서 `http://127.0.0.1:5173`을 여세요. 데모에 표시되는 내용은 실제 시간표·좌석·예약 정보가 아닙니다.

## 설치

Docker Compose로 실행합니다. 처음 설치하는 방법은 [시작하기](docs/GETTING_STARTED.md)에 순서대로 정리했습니다.

Linux Bash:

```bash
cp .env.example .env
chmod 600 .env
${EDITOR:-vi} .env
bash ./scripts/ops.sh experimental
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env
./scripts/ops.ps1 experimental
```

기본 설정에서는 설치한 컴퓨터에서만 접속할 수 있습니다. 다른 기기에서 안전하게 사용하려면 Tailscale 또는 HTTPS를 구성하세요. `.env.example`은 KORAIL·SRT 좌석 조회와 감시 gate, 필요한 `experimental-rail` 프로필을 켜 둡니다. 두 sidecar 내부 token과 공통 대기 저장용 `KORAIL_PROVIDER_COOLDOWN_REDIS_PASSWORD`를 채워야 하며, 자동 예매 gate는 별도로 꺼져 있습니다. 새 비밀값은 URL-safe 무작위 32바이트 이상으로 생성해 `.env`에만 저장합니다. 자세한 설정은 [설치·운영 가이드](docs/OPERATIONS.md#철도사-실험-기능)를 참고하세요.

공통 대기 저장용 비밀값은 `experimental-rail`에서 필수이며, 이 프로필을 실행하지 않는 구성에서는 선택 항목입니다. KORAIL sidecar의 준비 상태는 저장소와 설정 오류도 확인하지만 정상 준비 상태가 실제 코레일 로그인 성공을 뜻하지는 않습니다.

## Android 공식 앱 인계 QA

`.env.example`은 검증되지 않은 앱 업데이트에서 딥링크가 자동으로 활성화되지 않도록 네 경로를 모두 꺼 둡니다. 현재 기록된 코레일+·SRT 버전으로 네 경로를 함께 검증하거나, 해당 검증 결과를 승인한 배포를 만들 때는 커밋하지 않는 로컬 `.env`에 다음 값을 사용합니다.

```dotenv
# Android 공식 앱 인계 QA. 앱 버전 변경 시 먼저 끄고 경로별로 다시 검증합니다.
VITE_KORAIL_BOOKING_DEEPLINK_ENABLED=true
VITE_KORAIL_BOOKING_VALIDATED_VERSION=7.0.0+70000006
VITE_KORAIL_TICKET_DEEPLINK_ENABLED=true
VITE_KORAIL_TICKET_VALIDATED_VERSION=7.0.0+70000006
VITE_SRT_MAIN_DEEPLINK_ENABLED=true
VITE_SRT_MAIN_VALIDATED_VERSION=2.0.41+150
VITE_SRT_TICKET_DEEPLINK_ENABLED=true
VITE_SRT_TICKET_VALIDATED_VERSION=2.0.41+150
```

앱 업데이트를 확인하면 대응하는 `VITE_*_DEEPLINK_ENABLED`를 먼저 `false`로 바꾸고 웹 이미지를 다시 만드세요. 새 버전에서 resolver, 목적 화면, 설치·미설치 브라우저 동작을 경로별로 다시 확인한 뒤 검증 버전과 플래그를 갱신합니다. 전체 절차와 아직 남은 환경별 확인 항목은 [Android 공식 앱 인계 검증](docs/ANDROID_APP_HANDOFF_QA.md)을 따릅니다.

## 사용 흐름

1. 출발역, 도착역, 날짜와 시간을 고릅니다.
2. 관심 열차와 좌석 등급을 등록합니다.
3. 홈에서 좌석 상태와 최근 확인 시각을 살펴봅니다.
4. 상태가 바뀌면 설정한 채널로 알림을 받습니다.
5. 최종 확인과 결제는 철도사 공식 앱이나 홈페이지에서 직접 진행합니다.

자세한 화면 설명은 [사용 안내](docs/USAGE.md)를 참고하세요.

## 꼭 알아두세요

- 이 프로젝트는 한국철도공사(KORAIL) 또는 주식회사 에스알(SR)의 공식 서비스나 제휴 제품이 아닙니다.
- `.env.example` 기반 설치에서는 철도사 좌석 조회·감시가 활성화되고, 보호·호출 제한 응답이 확인되면 설정된 cooldown 동안 해당 운영사 요청을 중단합니다. 예매 시도 기능은 별도 gate로 꺼져 있습니다.
- 좌석 정보가 확인되지 않으면 임의로 추정하지 않고 `확인 필요`로 표시합니다.
- 좌석과 예약 상태는 언제든 바뀔 수 있으므로 공식 앱이나 홈페이지에서 다시 확인해야 합니다.
- 결제 정보는 저장하지 않으며 결제를 자동으로 진행하지 않습니다.
- 접속 제한, 보안문자, 호출 제한 등 철도사의 보호 조치를 우회하지 않습니다.

자세한 원칙은 [안전 원칙과 사용 범위](docs/POLICY_AND_SAFETY.md)에서 확인할 수 있습니다.

## 현재 확인 범위

v1.2.14는 현재 자동 로그인에서 보호 응답을 받은 같은 인증 상태의 반복 제출을 멈추고, HTTP 200으로
반환된 열차 조회 보호 JSON도 판독합니다. 10월 16일 운영 서버의 정상 검색 폼 단회 조회는
`macro_err1`과 결과 0건으로 끝났습니다. 이 판독·재시도 수정과 실제 예매 가능·매진 수집 복구는 다르며,
현재 공식 좌석 수집 복구는 미완료입니다. 세부 근거는 [운영 장애 기록](docs/INCIDENTS.md)을 참고하세요.

v1.2.12는 설정 화면의 과거 로그인 성공 이력과 현재 상주 세션을 분리합니다. 런타임 조회 실패·늦은 응답·계정 세대 불일치는 `재사용 가능`으로 표시하지 않으며, 가장 최근의 같은 세대 응답만 현재 상태로 사용합니다. 웹·API 전체 회귀와 로컬 전체 프로필 검증을 통과하고 운영 서버에도 최신 소스를 설치했습니다.

2026년 10월 16일에는 최신 작업 트리로 로컬과 Oracle을 다시 빌드·재생성하고, 운영 주소의 502를
해소했습니다. Oracle 장기 서비스 12개 healthy와 설치 소스 일치를 확인했으며 로컬은 검증 후 중지했습니다.
다만 현재 Oracle의 KORAIL 세션은 `blocked`·재사용 불가이므로 실제 인증·좌석 조회·예약 복구는 미완료입니다.

10월 10일의 제한된 운영 관찰에서는 공개 번들의 실제 로그인 제출 호출을 확인했지만, 공식 응답이 HTTP 200 JSON의 `macro_err1` 보호 상태로 끝났습니다. 서비스는 이를 `provider_access_restricted`로 분류해 공유 900초 대기를 열었고 외부 호출 경로를 즉시 중지했습니다. 보호를 우회하거나 좌석 조회·예약으로 진행하지 않았습니다. 이 표본은 새 임시 profile의 결과이며, 당시에는 자동 로그인·좌석 조회·자동 예약이 복구되지 않았습니다.

같은 서버와 headed Chrome 151에서 컨트롤러만 Playwright로 바꾼 제한 비교도 인증에 성공하지 못했습니다. 관측한 2xx POST 한 건은 DynaPath 실제 역할을 확정할 근거가 부족해 정상 로그인으로 올리지 않았고, 추가 엔진·표식 은폐 실험 없이 보호 대기를 다시 열었습니다.

Windows 호스트의 headed Chrome 154도 새 임시 profile에서는 문자별 입력과 버튼 클릭을 정상 완료한 뒤 같은 `macro_err1` 보호 응답을 받았습니다. 앞선 호스트 actionability 시간 초과는 KORAIL 화면이 아니라 별도 CDP `Debugger.enable` 계측과 Playwright의 충돌이었습니다. 사용자가 비밀번호 관리자 자동입력으로 로그인한 일반 Chrome은 현재 `로그아웃` 표시가 확인됐지만, 자격증명·쿠키·profile은 읽거나 복사하지 않았습니다. 따라서 Docker나 입력 로직을 원인으로 보지 않으며, 사용자 profile 복사나 automation 표식 은폐로 보호를 우회하지 않습니다.

무인 서버에는 `dedicated_auto`를 사용합니다. 이 모드는 일반 사용자 Chrome을 복사하지 않고 앱 전용 영속
profile에서 서버에 저장된 철도 계정으로 기존 자동 로그인 흐름을 실행합니다. Chrome 비밀번호 관리자를
읽거나 조작하지 않으며, 공식 보호·호출 제한 응답이면 다른 입력 방식으로 우회하지 않고 공유 cooldown에서
멈춥니다. 10월 11일 현재 Docker 표본에서는 noVNC를 끈 채 adapter와 API를 재생성한 뒤 저장 계정으로
자동 로그인이 완료돼 `authenticated`·`ready`·재사용 가능 상태가 됐습니다. 이어 adapter만 재시작했을 때도
약 12초 안에 공식 세션을 자동 확인해 같은 상태로 복구했습니다. 사람이 VNC에 접속하는 단계는 없습니다.
profile은 브라우저 상태의 연속성을 제공하지만 로그인 성공을 보장하는 우회 수단은 아닙니다.

이 차이를 안전하게 검증하기 위해 앱 전용 Docker Chrome에서 사용자가 공식 화면에 직접 로그인하고,
adapter가 그 브라우저를 닫지 않은 채 이어받는 `dedicated_manual` 모드를 추가했습니다. 실제 확인에서는
전용 profile 재시작 뒤 세션이 사라진 표본과 유지된 표본이 모두 있었으므로 복원을 전제로 삼지 않습니다.
일반 Chrome profile이나 쿠키를 가져오지 않으며 세션이 없으면 저장된 비밀번호를 자동 제출하지 않습니다.
10월 11일 새 이미지에서는 사용자가 직접 로그인한 뒤 수동 새로고침 없이 같은 계정 세대의 재확인이
`authenticated`·`ready`·재사용 가능 상태로 전환됐고, 두 탭 모두 조회 화면으로 정리됐습니다. 로그인 대기가
일반 30분 재사용 창을 넘으면 확인 전에 Chrome을 교체하던 경합도 이 과정에서 발견해, 같은 세대의
`auth_required` 수동 인계는 시간 제한보다 현재 live Chrome을 먼저 확인하도록 고쳤습니다. 로그인 완료가
새 탭에서 끝나 기존 조회 탭의 DOM이 낡아 있던 경우에는 읽기 전용 공식 세션 확인을 양성 근거로 사용합니다.
최초 직접 로그인 뒤에는 API의 session manager가 만료 전 공식 세션을 자동 재확인합니다. 같은 자격증명
지문이면 재사용 시간이 이미 지났거나 계정 세대 표기가 갱신돼도 live Chrome을 먼저 재검증해 이어 쓰고,
아이디·비밀번호가 달라진 경우에만 기존 세션을 폐기합니다. `dedicated_manual`의 `manual`은 최초 인증 방식을
뜻하며 이후 조회·세션 갱신을 사람이 반복한다는 뜻이 아닙니다.
이는 로그인 세션 인계만 확인한 결과이며 좌석 조회·예약 복구를 뜻하지 않습니다. 절차는
[KORAIL 브라우저 화면 진단](apps/api/KORAIL_BROWSER_DIAGNOSTICS.md)을 따릅니다.

공개된 코레일 승차권 API는 승차 후 진위 확인용으로 실시간 로그인·좌석·예약 기능을 제공하지 않습니다. 자동 연동은 운영사의 승인된 경로가 확인되기 전까지 복구된 것으로 보지 않고, 현재는 공식 홈페이지·앱 인계를 사용합니다.

운영 DB의 KORAIL `UNKNOWN` 99건은 모두 출발 시각이 지난 기록이며 진행 중 예약과 미래 대상은 0건입니다. 기존 기록은 공식 근거 없이 바꾸거나 재제출하지 않습니다. 현재 근거와 안전한 재개 조건은 [이슈 분석·개발 계획](docs/DEVELOPMENT_PLAN.md), 세부 검증은 [체크리스트](CHECKLIST.md)와 [장애 기록](docs/INCIDENTS.md)을 참고하세요.

## 문서

- [시작하기](docs/GETTING_STARTED.md)
- [사용 안내](docs/USAGE.md)
- [설치·운영 가이드](docs/OPERATIONS.md)
- [Android 공식 앱 인계 검증](docs/ANDROID_APP_HANDOFF_QA.md)
- [시스템 구조](docs/ARCHITECTURE.md)
- [문서 전체 보기](docs/README.md)

## 개발

전체 검증은 저장소 루트에서 실행합니다.

Linux Bash:

```bash
bash ./scripts/ops.sh verify
```

Windows PowerShell:

```powershell
./scripts/ops.ps1 verify
```

GitHub Actions의 핵심 검증은 Compose 설정, API, 웹, PostgreSQL 경합 계약을 확인합니다. GitHub runner 환경에 민감한 KORAIL Chromium 컨테이너 검증은 별도 실험 워크플로에서 실행하며, 로컬의 `verify` 명령은 두 범위를 모두 확인합니다.

변경 방법과 테스트 기준은 [기여 가이드](CONTRIBUTING.md)를 참고하세요. 보안 문제는 공개 이슈 대신 [보안 정책](SECURITY.md)의 비공개 신고 절차를 이용해 주세요.

## 라이선스

[MIT License](LICENSE)로 배포합니다.
