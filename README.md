# 레일웨잇

KTX·SRT 관심 열차를 한곳에서 찾고 관리하는 개인용 웹 앱

좌석 감시를 활성화하면 관심 열차의 상태 변화를 확인해 알림을 받을 수 있습니다. 모바일과 PC에서 사용할 수 있으며, 개인 서버에 직접 설치해 운영합니다.

[![저장소 검증 상태](https://github.com/hostkimjang/korail-auto-waitlist/actions/workflows/web-journey.yml/badge.svg)](https://github.com/hostkimjang/korail-auto-waitlist/actions/workflows/web-journey.yml)

[![여정 등록부터 알림과 공식 채널 안내까지 보여주는 레일웨잇 소개 영상](docs/media/railwait-intro.gif)](docs/media/railwait-intro.mp4)

연출된 고정 데모입니다. 철도사 비공식 프로젝트이며 자동 결제를 제공하지 않습니다.

## 이런 분을 위해 만들었습니다

- 매진된 열차를 여러 번 다시 검색하고 있는 분
- KTX와 SRT의 관심 열차를 한곳에서 관리하고 싶은 분
- 좌석이 바뀌었을 때 알림을 받고 싶은 분
- 개인 서버에서 직접 운영할 수 있는 도구가 필요한 분

## 주요 기능

- 공공데이터 API 키를 설정한 KTX·SRT 열차 검색
- 좌석 확인 기능을 켰을 때 일반실·특실별 대기 등록
- 좌석 상태와 확인 시각 표시
- 감시 중인 열차와 결제 필요 항목을 한눈에 확인
- Web Push, Telegram, Discord, Webhook 알림
- 모바일 PWA와 데스크톱 반응형 화면

## 화면 미리 보기

[![레일웨잇 화면 데모](docs/media/railwait-demo.gif)](docs/media/railwait-demo.mp4)

고정 예시 데이터만 사용하며 실제 좌석·외부 요청·예약 성공을 나타내지 않습니다.

## 먼저 체험해 보기

Node.js 22가 설치되어 있다면 Docker나 외부 API 없이 데모를 실행할 수 있습니다.

```powershell
cd apps/web
npm ci
$env:VITE_DEMO_MODE="true"
npm run dev
```

브라우저에서 `http://127.0.0.1:5173`을 여세요. 데모에 표시되는 내용은 실제 시간표·좌석·예약 정보가 아닙니다.

## 설치

Docker Compose로 실행합니다. 처음 설치하는 방법은 [시작하기](docs/GETTING_STARTED.md)에 순서대로 정리했습니다.

```powershell
Copy-Item .env.example .env
docker compose -f compose.yml config --quiet
docker compose -f compose.yml up -d --build
```

기본 설정에서는 설치한 컴퓨터에서만 접속할 수 있습니다. 다른 기기에서 안전하게 사용하려면 Tailscale 또는 HTTPS를 구성하세요.

## 사용 흐름

1. 출발역, 도착역, 날짜와 시간을 고릅니다.
2. 관심 열차와 좌석 등급을 등록합니다.
3. 홈에서 좌석 상태와 최근 확인 시각을 살펴봅니다.
4. 상태가 바뀌면 설정한 채널로 알림을 받습니다.
5. 최종 확인과 결제는 철도사 공식 앱이나 홈페이지에서 직접 진행합니다.

자세한 화면 설명은 [사용 안내](docs/USAGE.md)를 참고하세요.

## 꼭 알아두세요

- 이 프로젝트는 한국철도공사(KORAIL) 또는 주식회사 에스알(SR)의 공식 서비스나 제휴 제품이 아닙니다.
- 기본 설치에서는 철도사 좌석 감시와 예매 시도 기능이 꺼져 있습니다.
- 좌석 정보가 확인되지 않으면 임의로 추정하지 않고 `확인 필요`로 표시합니다.
- 좌석과 예약 상태는 언제든 바뀔 수 있으므로 공식 앱이나 홈페이지에서 다시 확인해야 합니다.
- 결제 정보는 저장하지 않으며 결제를 자동으로 진행하지 않습니다.
- 접속 제한, 보안문자, 호출 제한 등 철도사의 보호 조치를 우회하지 않습니다.

자세한 원칙은 [안전 원칙과 사용 범위](docs/POLICY_AND_SAFETY.md)에서 확인할 수 있습니다.

## 문서

- [시작하기](docs/GETTING_STARTED.md)
- [사용 안내](docs/USAGE.md)
- [설치·운영 가이드](docs/OPERATIONS.md)
- [시스템 구조](docs/ARCHITECTURE.md)
- [문서 전체 보기](docs/README.md)

## 개발

전체 검증은 저장소 루트에서 실행합니다.

```powershell
./scripts/ops.ps1 verify
```

변경 방법과 테스트 기준은 [기여 가이드](CONTRIBUTING.md)를 참고하세요. 보안 문제는 공개 이슈 대신 [보안 정책](SECURITY.md)의 비공개 신고 절차를 이용해 주세요.

## 라이선스

[MIT License](LICENSE)로 배포합니다.
