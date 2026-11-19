# 레일웨잇

KTX·SRT 관심 열차를 한곳에서 찾고 관리하는 개인용 웹 앱

좌석 감시를 활성화하면 관심 열차의 상태 변화를 확인해 알림을 받을 수 있습니다. 모바일과 PC에서 사용할 수 있으며, 개인 서버에 직접 설치해 운영합니다.

[![핵심 저장소 검증 상태](https://github.com/hostkimjang/korail-auto-waitlist/actions/workflows/web-journey.yml/badge.svg)](https://github.com/hostkimjang/korail-auto-waitlist/actions/workflows/web-journey.yml)
[![실험 Chromium 검증 상태](https://github.com/hostkimjang/korail-auto-waitlist/actions/workflows/experimental-browser.yml/badge.svg)](https://github.com/hostkimjang/korail-auto-waitlist/actions/workflows/experimental-browser.yml)
[![설치 CLI 검증 상태](https://github.com/hostkimjang/korail-auto-waitlist/actions/workflows/cli.yml/badge.svg)](https://github.com/hostkimjang/korail-auto-waitlist/actions/workflows/cli.yml)

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
- 코레일 공지 팝업의 문구·클래스·닫기 아이콘이 바뀌어도 화면에 그려진 창과 보이는 라벨에서 공지 근거를 찾아 닫기 단추만 누르고, 결제·동의·입력이 섞인 창은 닫지 않은 채 설정의 `닫지 못한 팝업` 카드에 이유를 표시
- 좌석 확인 기능을 켰을 때 일반실·특실별 대기 등록
- 좌석 상태와 확인 시각 표시
- 감시 중인 열차의 실제 열차 종류·번호와 결제 필요 항목을 한눈에 확인
- 운영사 예약 결과에서 확인된 호차·좌석번호 표시
- Chrome·Edge·모바일 등 연결한 각 기기에 함께 보내는 Web Push와 Telegram·Discord·Webhook 알림
- 공식 예약·결제 페이지를 새 브라우저 창으로 여는 안전한 인계와, 경로별 실기기 검증 뒤에만 켤 수 있는 코레일+·SRT 앱 인계
- 모바일 PWA와 데스크톱 반응형 화면
- Windows·macOS·Linux용 설치 CLI와, 작업 종료 대기·암호화 백업·상태 확인을 거치는 업데이트

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

설치 방법은 세 가지입니다. 서버를 운영하기만 한다면 GitHub Release의 서명된 설치 CLI 하나로 설치하는 방법이 가장 간단합니다. 저장소를 직접 운영하거나 개발한다면 Git 설치를, 직접 빌드한 이미지를 시험한다면 로컬 패키지 설치를 씁니다.

| 설치 방법 | 알맞은 경우 | 상태 | 업데이트 |
| --- | --- | --- | --- |
| 설치 CLI + 공식 서명 릴리스 | Git·Python·Node.js 없이 한 줄로 설치 | 사용 가능(1.4.3부터, 한 줄 설치는 1.4.4부터) | 웹 **앱 업데이트** 버튼 또는 `railwait update` |
| Git + Docker Compose | 저장소를 받아 직접 운영 | 사용 가능 | `git pull` 뒤 운영 스크립트로 재빌드 |
| 설치 CLI + 로컬 패키지 | 직접 빌드·서명한 패키지로 관리형 설치 | 사용 가능(macOS에서 확인) | `scripts/update-local-install.sh` |

### 공식 릴리스로 설치하기

macOS·Linux에서는 터미널에 한 줄을 붙여 넣습니다.

```bash
curl -fsSL https://github.com/hostkimjang/korail-auto-waitlist/releases/latest/download/install.sh | sh
```

Windows에서는 PowerShell에서 실행합니다.

```powershell
irm https://github.com/hostkimjang/korail-auto-waitlist/releases/latest/download/install.ps1 | iex
```

Windows PowerShell 5.1에서 `요청이 중단되었습니다. 예기치 않게 연결이 닫혔습니다.`(영문 Windows에서는 `The request was aborted: The connection was closed unexpectedly.`)가 나오면, 받으려는 파일이 그 Release에 없어서(404) 생긴 오류일 수 있습니다. 같은 문구는 프록시나 네트워크 문제에서도 나옵니다. [최신 Release](https://github.com/hostkimjang/korail-auto-waitlist/releases/latest)에 `install.ps1`이 있는지 확인하거나, 같은 창에서 `[Net.ServicePointManager]::SecurityProtocol = 'Tls12'`을 실행한 뒤 다시 시도하면 실제 상태 코드(예: 404)를 볼 수 있습니다.

스크립트는 운영체제와 CPU에 맞는 설치 CLI를 같은 Release에서 받아 SHA-256을 확인하고, `~/.local/bin/railwait`(Windows는 `%LOCALAPPDATA%\Programs\Railwait\railwait.exe`)에 둔 뒤 설치 마법사를 바로 시작합니다. sudo나 관리자 권한을 쓰지 않고, PATH와 셸 설정 파일은 바꾸지 않습니다. PATH에 없으면 추가할 명령만 안내합니다. 마법사 옵션은 `sh -s --` 뒤에 붙입니다. 예를 들어 설치 폴더와 포트를 직접 고르려면 `curl -fsSL …/install.sh | sh -s -- --custom`을 실행하고, Windows에서는 `$env:RAILWAIT_INSTALL_ARGS = '--custom'`을 먼저 지정합니다. `RAILWAIT_VERSION=1.4.4`처럼 지정하면 그 버전의 설치 CLI를 받지만, 설치되는 앱은 항상 최신 공식 릴리스입니다. 한 줄 설치는 1.4.4부터 쓸 수 있습니다.

설치 마법사는 실행 환경 확인, 설치 계획, 앱·계정 설정, 자동 시작, 접속 확인의 다섯 단계로 진행합니다. Windows·macOS에서는 없는 Docker Desktop·Tailscale을 공식 서버에서 받아 배포자 서명을 확인한 뒤 OS 설치 창으로 이어 줍니다. 간편 설치는 Tailscale HTTPS 연결, 보안·알림 키 생성, 자동 예매 켜기를 기본으로 하므로 선택 항목인 TAGO 키와 관리자 계정만 물어봅니다. 실행 파일에 들어 있는 공식 공개키로 서명을 확인한 배포 묶음과 이미지만 설치합니다.

- **설치 폴더**: 운영체제의 사용자 앱 데이터 폴더(macOS `~/Library/Application Support/Railwait`)가 기본입니다. Docker가 Lima·Colima VM에서 돌고 그 VM이 기본 폴더를 쓸 수 없으면, VM이 쓰기 가능으로 공유한 폴더 안의 `railwait` 폴더로 자동으로 정합니다. 수 GB의 이미지를 받기 전에 Docker 컨테이너가 설치 폴더를 읽고 쓸 수 있는지 먼저 확인합니다. 직접 정하려면 `--path`를 씁니다.
- **포트**: 이 컴퓨터의 접속 주소는 8080부터 비어 있는 포트를 자동으로 고릅니다. 직접 정하려면 `--port 9090`처럼 지정하고, `--custom`을 쓰면 설치 폴더와 포트를 물어봅니다. Enter를 누르면 표시된 기본값을 씁니다.
- **원격 접속**: 로컬 전용은 `--access local`입니다. Tailscale 단계에서 멈췄다면 같은 명령을 다시 실행하면 원격 접속 설정을 이어갑니다. 로컬 전용으로 설치했다가 나중에 켜려면 `--access tailscale`을 붙여 다시 실행합니다. 원격 접속을 켜면 다른 기기는 완료 화면의 HTTPS 주소로 접속하고, 이 컴퓨터에서는 로컬 주소(`http://127.0.0.1:<포트>`)도 계속 쓸 수 있습니다(1.4.5부터).
- **TAGO 키**: 없어도 됩니다. 새 대기 등록의 역 목록은 앱에 포함된 공개 역 목록을 쓰고, 열차 시간표는 코레일·SRT 공식 조회를 먼저 씁니다. 공공데이터포털 TAGO 일반 인증키(Decoding)를 설치 중에 넣거나 나중에 `railwait configure`로 넣으면 최신 역 목록을 받아 쓰고, 공식 조회가 안 될 때 TAGO 시간표로 대신합니다. 설치 중에는 Enter를 누르면 건너뜁니다.
- **자동 예매**: 새 설치는 켜 둡니다. 새 대기에서 **좌석 재발견마다 자동 예매**를 고른 대기만 좌석을 찾으면 저장한 철도 계정으로 결제 직전까지 예매하고, 결제는 하지 않습니다. 설치할 때 끄려면 `--custom`을 쓰고, 설치한 뒤에는 `railwait configure reservation off`·`on`으로 바꿉니다. 지연승낙 안내의 자동 선택은 따로 꺼 둡니다.

스크립트 없이 직접 받으려면 [최신 Release](https://github.com/hostkimjang/korail-auto-waitlist/releases/latest)에서 `railwait-<OS>-<CPU>` 실행 파일과 같은 이름의 `.sha256` 파일을 받아 `shasum -a 256 -c`(Linux는 `sha256sum -c`, Windows는 `Get-FileHash`)로 확인한 뒤 `install`을 실행합니다. 이때 받은 실행 파일은 설치가 끝날 때까지 옮기거나 지우지 마세요. 중단된 설치를 이어갈 때 같은 파일을 씁니다. 명령 전체는 [설치 CLI 안내](apps/cli/README.md)에 있습니다. 기존 Git 설치를 CLI 설치로 옮기는 기능은 아직 없으므로 다른 폴더와 포트에 설치하세요.

### Git으로 설치하기

Docker Compose로 실행합니다. 처음 설치하는 방법은 [시작하기](docs/GETTING_STARTED.md)에 순서대로 정리했습니다.

macOS·Linux Bash:

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

### 로컬 패키지로 설치하기

직접 빌드한 이미지를 시험할 때는 저장소에서 빌드한 CLI로, 직접 만든 로컬 패키지와 그 공개키를 명시적으로 신뢰해 설치합니다. 이렇게 설치하면 공식 릴리스 업데이트는 받지 않습니다.

```console
railwait install --path <새 설치 폴더> --package <로컬 패키지 폴더> --trust-key <공개키 파일>
```

CLI를 빌드하는 방법은 [설치 CLI 안내](apps/cli/README.md#railwait-설치-패키지), 로컬 패키지를 만드는 방법은 [릴리스 도구 안내](scripts/releases/README.md#로컬-오프라인-설치-패키지)에 있습니다.

## 업데이트

설치한 방법에 따라 업데이트 방법이 다릅니다. 어느 방법이든 DB와 `.env`의 비밀값은 그대로 유지합니다. 올리기 전에 [업데이트 노트](CHANGELOG.md)에서 새 버전 항목 끝의 `적용할 때`를 읽고 새 설정이나 DB migration이 있는지 확인하세요.

### Git 설치

새 커밋을 fast-forward로만 받은 뒤 사용 중인 프로필 전체를 다시 빌드하고 재가동합니다.

macOS·Linux Bash:

```bash
git pull --ff-only origin main
bash ./scripts/ops.sh experimental
```

`ops.sh`는 1.5.0부터 macOS 기본 bash(3.2)에서도 동작합니다. 그 전 버전에서 올릴 때도 `git pull`로 받은 새 스크립트가 실행되므로 같은 명령을 씁니다.

Windows PowerShell:

```powershell
git pull --ff-only origin main
./scripts/ops.ps1 experimental
```

운영 스크립트는 새 외부 요청과 주기 작업을 먼저 막고 진행 중인 작업이 끝나기를 기다린 뒤 서비스를 다시 만듭니다. 받기 전에 확인할 것과 되돌리는 방법은 [설치·운영 가이드](docs/OPERATIONS.md#git으로-새-버전-받아-재가동하기)에 있습니다.

### 로컬 패키지로 설치한 CLI 설치

반영할 커밋을 저장소에서 받은 뒤 macOS·Linux에서 스크립트 하나로 올립니다. 먼저 `--dry-run`으로 계획만 확인할 수 있습니다.

```bash
bash scripts/update-local-install.sh --path <설치 폴더> --dry-run
bash scripts/update-local-install.sh --path <설치 폴더>
```

스크립트는 앱 이미지 빌드, 일회성 키로 서명한 패키지와 같은 커밋의 CLI 생성, 업데이트 계획 확인과 적용, 설치 상태·HTTP·화면 버전 확인을 이어서 실행합니다. 적용할 때는 새 작업을 막고 진행 중인 작업이 끝나기를 기다린 뒤 DB와 설정을 암호화 백업하고 앱을 교체합니다. 커밋하지 않은 변경은 들어가지 않으며, DB 구조가 바뀌는 버전은 이 방법으로 올릴 수 없습니다. uv와 Docker가 필요합니다. macOS에서 확인했고 Linux 호스트 실행은 아직 확인하지 않았습니다. Windows에서는 새 로컬 패키지를 만든 뒤 `railwait update --package … --trust-key …`로 계획 확인과 적용을 직접 실행합니다. 자세한 동작은 [설치 CLI 안내](apps/cli/README.md#railwait-설치-패키지)에 있습니다.

### 공식 서명 릴리스로 설치한 CLI 설치

웹의 **설정 → 로그·진행 상태 → 앱 업데이트**에서 새 버전을 확인하고 적용합니다. 같은 작업을 `railwait update`로도 실행할 수 있고, 진행 화면을 닫아도 작업은 계속됩니다. 새 Release가 지금 설치한 버전에서 올라오는 것을 허용할 때만 적용할 수 있고, DB 구조가 바뀌는 버전은 아직 이 방법으로 올릴 수 없습니다. 로컬 패키지 설치에서는 이 버튼이 의도적으로 막혀 있습니다.

### 지금 버전 확인하기

모든 화면 아래 푸터에 지금 실행 중인 릴리스 버전과 업데이트 날짜가 요일과 함께 표시됩니다. 업데이트한 뒤 새로고침한 화면의 값이 [업데이트 노트](CHANGELOG.md) 맨 위 항목과 같으면 최신 화면을 받은 것입니다. 값이 그대로면 새로고침을 한 번 더 하고, 그래도 바뀌지 않으면 웹 이미지를 다시 만들었는지 확인하세요.

## 설치한 서버 관리하기

설치 CLI로 설치한 서버는 웹의 **설정 → 서버 설정**과 `railwait` 명령으로 관리합니다. `railwait help`는 용도별 명령 목록을, `railwait help <명령>`은 명령별 사용법을 보여 줍니다. 기본 위치가 아닌 폴더에 설치했다면 명령마다 `--path <설치 폴더>`를 붙입니다.

| 하고 싶은 일 | 웹 화면 | 명령 |
| --- | --- | --- |
| 자동 예매 켜기·끄기, TAGO 키 바꾸기 | **설정 → 서버 설정** | `railwait configure reservation on\|off`, `railwait configure` |
| 알림 발신자 연락처, Android 공식 앱 연결 바꾸기 | **설정 → 서버 설정** | 설정 파일을 고친 뒤 `railwait restart` |
| 설정 파일(`.env`) 위치 보기·열기 | **서버 설정 → 설정 파일 위치** | `railwait open` |
| 멈춘 것 같은 앱 서비스 다시 시작 | **서버 설정 → 서비스 다시 시작** | `railwait restart` |
| 서비스 상태와 작업 결과 확인 | **설정 → 로그·진행 상태** | `railwait status` |
| 제거(데이터는 기본으로 남김) | | `railwait uninstall`, 데이터까지 지우려면 `--delete-data` |

화면의 **설정 파일 위치**는 1.4.8 이상, **서비스 다시 시작**과 `restart`·`open`·`uninstall` 명령은 1.5.0 이상 관리 서비스·CLI가 필요합니다. 그 전 CLI로 설치했다면 한 줄 설치 명령을 다시 실행하거나 새 CLI로 `railwait manager upgrade`를 한 번 실행합니다. 데이터와 설정은 그대로입니다. 다시 시작하면 KORAIL에 새로 로그인하므로, KORAIL이 이 로그인을 막으면 좌석 관측이 30분 이상 멈출 수 있습니다. Git 설치는 저장소 폴더의 `.env`를 고친 뒤 운영 스크립트(`ops.sh experimental`, `ops.ps1 experimental`)로 다시 만듭니다.

## Android 공식 앱 인계

`.env.example`은 코레일+·SRT 앱을 바로 여는 네 경로를 모두 꺼 둡니다. 검증하지 않은 앱 업데이트에서 딥링크가 저절로 켜지지 않게 하기 위해서입니다. 직접 확인하지 않았다면 웹의 **설정 → 서버 설정 → Android 공식 앱 연결**에서 **개발자 확인 값 채우기**를 눌러 개발자가 실기기에서 확인한 경로만 켜고 적용하면 됩니다(1.5.0부터). 경로별 검증 절차, QA용 설정값, 앱이 업데이트됐을 때 다시 끄는 순서는 [Android 공식 앱 인계 검증](docs/ANDROID_APP_HANDOFF_QA.md#기능-플래그)을 따르세요.

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

## 현재 상태

2026년 11월 18일, v1.4.3 기준입니다. 확인한 범위와 남은 일을 나눠 적었습니다.

- **좌석 조회와 감시**: 11월 18일 관리형 설치에서 공지를 닫은 뒤 KORAIL 공개 좌석 조회가 성공하는 것을 확인했습니다. 코레일이 보호 응답(`macro_err1` 등)을 보내면 자동 로그인과 조회를 정해진 시간 동안 멈추며 우회하지 않으므로, 그동안 감시 관측도 멈출 수 있습니다.
- **공지 팝업**: v1.4.1부터 화면 기준으로 판정합니다. 실제 공개 화면의 세 가지 화면 폭에서 인식과 닫기를 확인했고, 예약 도중 늦게 뜨는 공지는 아직 실제로 마주쳐 확인하지 못했습니다. 범위는 [공개 검증 기록](docs/NOTICE_ADAPTATION_VERIFICATION.md)에 있습니다.
- **예약**: 계정 기반 예매 시도는 별도 gate로 꺼져 있습니다. 출발 전 열차로 좌석 관측부터 결제 전 예약까지 다시 확인하는 일이 남아 있습니다.
- **설치 CLI**: 1.4.3부터 GitHub Release에 서명된 CLI 6종과 배포 묶음을 게시합니다. 게시한 이미지는 amd64·arm64 runner에서 실행을 확인했고, 받은 macOS CLI로 공식 릴리스 조회와 서명 확인을 마쳤습니다. 1.4.2 Release의 CLI는 릴리스 조회 결함 때문에 쓸 수 없습니다. 웹 **앱 업데이트** 버튼으로 실제 새 버전을 받는 확인은 다음 릴리스에서 하고, Windows·macOS Docker Desktop 새 설치와 재부팅 뒤 복구는 아직 확인하지 않았습니다.

지난 장애의 원인과 조치는 [운영 장애 기록](docs/INCIDENTS.md), 보호 경계와 안전한 재개 조건은 [이슈 분석·개발 계획](docs/DEVELOPMENT_PLAN.md), 남은 검증 전체는 [체크리스트](CHECKLIST.md)에 있습니다.

## 문서

- 처음 쓰는 분: [시작하기](docs/GETTING_STARTED.md), [사용 안내](docs/USAGE.md), [안전 원칙과 사용 범위](docs/POLICY_AND_SAFETY.md), [업데이트 노트](CHANGELOG.md)
- 직접 운영하는 분: [설치·운영 가이드](docs/OPERATIONS.md), [설치 CLI 안내](apps/cli/README.md), [운영 장애 기록](docs/INCIDENTS.md), [Android 공식 앱 인계 검증](docs/ANDROID_APP_HANDOFF_QA.md)
- 개발에 참여하는 분: [시스템 구조](docs/ARCHITECTURE.md), [CLI 설치·업데이트 설계](docs/INSTALL_UPDATE_DESIGN.md), [릴리스 도구 안내](scripts/releases/README.md), [기여 가이드](CONTRIBUTING.md)
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

GitHub Actions의 핵심 검증은 Compose 설정, API, 웹, PostgreSQL 경합 계약을 확인합니다. GitHub runner 환경에 민감한 KORAIL Chromium 컨테이너 검증은 별도 실험 워크플로에서 실행하며, 로컬의 `verify` 명령은 두 범위를 모두 확인합니다. 설치 CLI는 또 다른 워크플로가 Ubuntu·macOS·Windows에서 `go test -race`를 실행하고 6개 OS·CPU 조합의 빌드를 확인합니다.

변경 방법, 테스트 기준, 릴리스 버전을 올리는 방법은 [기여 가이드](CONTRIBUTING.md)를 참고하세요. 보안 문제는 공개 이슈 대신 [보안 정책](SECURITY.md)의 비공개 신고 절차를 이용해 주세요.

## 라이선스

[MIT License](LICENSE)로 배포합니다.
