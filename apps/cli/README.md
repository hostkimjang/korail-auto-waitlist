# railwait CLI

Windows, macOS, Linux의 `amd64`·`arm64`용 설치 CLI입니다. Docker의 Linux containers와
Compose 2.24 이상이 필요합니다. Docker Desktop/VM에서 설치 경로의 파일 공유도 허용해야 합니다.
사용자는 Go를 설치할 필요가 없습니다. `railwait install` 또는 `railwait setup`은 실행 환경 → 설치 계획 →
앱·계정 → 자동 시작 → 접속 확인의 다섯 단계로 진행합니다. 대화형 터미널에서 인자 없이 실행해도 시작합니다.

Windows·macOS의 대화형 설치에서는 없는 Docker Desktop·Tailscale을 공식 서버에서 내려받고 배포자
서명을 검증한 뒤 OS 설치 창으로 이어갑니다. Docker Desktop의 약관, OS 권한, VPN 허용과 SSO는 열린
창에서 직접 처리합니다. 설치 후에는 실행·로그인 상태를 다시 확인합니다. 이미 설치된 프로그램은 덮어쓰지
않고, 기존 Lima·Colima 등 로컬 Docker가 정상이면 그대로 재사용합니다. Docker의 전역 context를 바꾸지
않으며 PATH 밖의 OS별 설치 경로도 탐색합니다. Linux와 비대화형 실행에서는 필수 도구를 먼저 준비합니다.

새 대화형 설치는 간편 모드로 진행합니다. Tailscale HTTPS와 앱 보안키 6종·브라우저 알림용 VAPID 자동
생성을 기본으로 하고, 계획 확인·키 선택·TAGO 입력 질문을 생략합니다. 관리자 계정은 사용자가 설정하며
완료 후 앱을 브라우저로 엽니다. 로컬 전용은 `--access local`, 접속 방식·보유 키·TAGO 키를 직접
선택하려면 `--custom`을 사용합니다. TAGO 발급은 자동화하지 않으며 시간표 검색은 별도 키 설정 뒤
사용할 수 있습니다. 기존 설치의 접속 방식과 비대화형 설치의 로컬 기본값은 유지합니다.
숨김 입력 중에는 내용을 표시하지 않으며 비밀번호는 API와 같은 12~128자 기준을 사용합니다. 완료된 설치에 같은 경로로
실행하면 관리자·API 키를 다시 묻지 않고 상태 확인과 접속 설정을 이어갑니다. 중단된 설치는 기존 설치
기록과 설정을 보존하고 필요한 관리자 입력만 요청합니다. 누락된 브라우저 알림 키는 별도 설정 작업으로
준비하며 이미 저장된 키를 교체하지 않습니다. DB migration 결과 불명 상태는 자동 재개하지 않습니다.

진행 표시는 실제 작업명과 경과 시간이며 전체 설치의 추정 퍼센트를 표시하지 않습니다. 다운로드에서는
실제로 받은 용량과 확인된 전체 크기 기준 진행률을 표시합니다. `--plain`, `NO_COLOR`,
`TERM=dumb`, 비TTY 출력에서는 색·커서 제어·애니메이션 없이 출력합니다. `railwait preview`는 설치나
설정을 바꾸지 않는 화면 예시입니다. `--yes`는 계획 확인을 생략하고 별도 키 입력이 없으면 자동 생성
기본값을 사용합니다. 관리자 입력·OS 승인·로그인을 대신하지 않습니다.

중단·재부팅 후 표시되는 재개 명령에는 실제 실행 파일 경로와 설치 폴더, 패키지·신뢰키, 접속 방식,
Docker context, 포트, 입력 파일 및 선택한 실행 옵션이 유지됩니다. 파일 경로는 절대경로로 보존하고,
Windows 명령은 PowerShell용으로 표시합니다. Windows 준비 중 재부팅이 필요하면 현재 사용자 로그인 시
설치 창을 한 번 재개하도록 등록합니다. 원본 실행 파일·패키지·입력 파일은 이동하거나 삭제하지 않아야 합니다.
표준입력 입력과 Windows 자동 실행 명령의 길이 제한을 넘는 경로는 자동 재개 대신 수동 재개를 안내합니다.
자동 실행 명령은 UTF-16 260단위, 보호된 옵션 JSON은 32KiB까지 허용합니다. 옵션 파일에는 입력 파일의
경로만 저장하며 비밀번호·키 내용은 복사하지 않습니다. 다른 로그인 등록과 기존 상태는 덮어쓰지 않고,
일치하는 로그인 등록은 실행 시 한 번 제거합니다. 손상된 상태 파일은 복구를 위해 보존합니다.
앱 설치 기록이 생기기 전에는 상태 조회 명령을 표시하지
않습니다. `--input -`를 썼다면 보호된 JSON을 표준 입력으로 다시 전달해야 하며 그 내용은 명령에 넣지 않습니다.

현재 구현한 명령은 다음과 같습니다.

```console
railwait install --path <새 설치 경로> --port 8080
railwait install --path <설치 경로> --access tailscale --open
railwait setup --path <설치 경로> --plain
railwait install --path <새 설치 경로> --package <로컬 패키지 디렉터리> --trust-key <직접 신뢰할 공개키 파일>
railwait status --path <설치 경로> --json
railwait status --path <설치 경로> --job <작업 ID> --json
railwait doctor --path <설치 경로> --json
railwait update check --json
railwait update --path <설치 경로> --dry-run
railwait update --path <설치 경로> --target <확인한 버전> --plan <계획 ID>
railwait update --path <설치 경로> --package <새 로컬 패키지> --trust-key <직접 신뢰할 공개키 파일> --dry-run
railwait update --path <설치 경로> --package <새 로컬 패키지> --trust-key <직접 신뢰할 공개키 파일> --target <확인한 버전> --plan <계획 ID>
railwait configure --path <설치 경로>
railwait configure --path <설치 경로> --plan <계획 ID>
railwait manager status --path <설치 경로>
railwait version
railwait preview --plain
```

## 필수 프로그램 설치

Docker는 `desktop.docker.com`, Tailscale은 `pkgs.tailscale.com`의 HTTPS 주소에서만 받으며 다른
호스트로의 리디렉션을 거부합니다. 파일은 현재 사용자 전용 임시 폴더에 저장합니다. 이 검증은 아래의
Railwait 릴리스·로컬 패키지 서명 검증과 별개입니다.

| 환경 | 자동으로 이어가는 준비 | 사용자가 처리하는 단계 |
| --- | --- | --- |
| macOS | Docker DMG의 앱·설치 프로그램 서명과 Tailscale PKG의 배포자·Gatekeeper 확인, OS 권한 창을 통한 공식 설치, 앱 실행 | 관리자 권한 승인, Docker 약관, Tailscale VPN·확장 기능 허용과 로그인 |
| Windows | EXE·MSI의 Authenticode·공식 배포자·실행 직전 파일 해시 확인, Docker의 사용자별 WSL 2 설치, Tailscale 설치 | WSL·Tailscale의 UAC 승인, 약관, 로그인, 필요한 재부팅 |

Windows에서는 WSL 2.1.5 이상과 실행 상태를 확인하고, 필요하면 `wsl --install --no-distribution` 또는
`wsl --update`를 시스템 권한 창으로 실행합니다. 재부팅이 필요하면 설치를 멈추고 다음 로그인에서 설치를
자동 재개합니다. 등록 실패 시에는 같은 명령으로 이어가는 방법을 표시합니다. CLI가 재부팅을 실행하지는 않습니다. Windows ARM용 Docker Desktop은 공식 Early
Access 배포판입니다. 설치되어 있으나 준비되지 않은 프로그램은 재설치하지 않고 시작·복구를 안내합니다.
Docker Desktop을 시작한 뒤에는 최대 5분간 준비를 자동 확인합니다. 약관·초기 설정을 마치면 Enter 없이
이어갑니다. Tailscale도 초기 실행·연결·기기 승인을 최대 5분 확인하며, 첫 로그인은 검증한 공식 페이지를
한 번 자동으로 엽니다. 인증 주소 원문은 터미널·오류·설치 기록에 남기지 않습니다. 조직에서 기기 승인을
요구하면 해당 관리자 승인이 필요합니다. 시간 초과나 취소를 설치 성공으로 처리하지 않습니다.

다운로드·검증 중 취소하면 임시 파일을 정리합니다. **OS 설치 창이 이미 열렸다면 그 창에서 설치를
완료하거나 취소해 주세요.** CLI는 시스템 설치가 끝날 때까지 기다린 뒤 임시 파일을 지웁니다. macOS 설치
디스크의 연결 해제를 확인하지 못한 경우에는 해당 폴더를 보존하고 경로를 알립니다. 설치 후 오류나 중단이
있어도 같은 명령으로 다시 실행하면 기존 프로그램과 준비 상태를 먼저 확인합니다.

공식 요구사항과 수동 준비: [Docker macOS](https://docs.docker.com/desktop/setup/install/mac-install/),
[Docker Windows](https://docs.docker.com/desktop/setup/install/windows-install/),
[Tailscale 다운로드](https://tailscale.com/download),
[Tailscale macOS](https://tailscale.com/docs/install/mac).

## Railwait 설치 패키지

공식 릴리스 CLI는 GitHub Release의 `railwait-<OS>-<CPU>` 실행 파일이며, 릴리스 서명 공개키
`qeR7RDhD9a1lF0YVpMX5PbiHmrVU+nm7Fstt9UbB5nM=`를 포함합니다. 1.4.3부터 게시하며, 1.4.2 Release의 CLI는
GitHub API 조회가 HTTP 415로 실패해 공식 설치에 쓸 수 없습니다. 저장소에서 직접 빌드한 개발용 실행 파일에는
이 공개키가 없어 공식 릴리스 설치와 조회가 중단됩니다. 개발용 실행 파일은 Go 1.27 이상으로 `apps/cli`에서
`go build ./cmd/railwait`를 실행해 만들고 아래 로컬 패키지 설치에만 씁니다. Docker daemon과 같은 CPU용으로 빌드해야 합니다.

공식 릴리스가 없는 경우 사용자가 명시적으로 신뢰한 **로컬 설치 패키지**를 선택할 수 있습니다.
`--package`와 `--trust-key`를 함께 지정하며 공개키는 base64 Ed25519 32바이트입니다. 패키지는
`local-manifest.json`, `local-manifest.sig`, `railwait-bundle.tar.gz`, `railwait-images.tar`로 구성합니다.
서명과 모든 파일의 SHA-256, CPU, 이미지 archive의 config ID·OCI index 연결을 검증한 뒤
Docker에 불러옵니다. image tag가 있는 archive는 기존 운영 이미지 태그를 바꿀 수 있어 거절합니다.
Compose는 immutable image ID와 `pull_policy: never`를 사용하고 네트워크 pull을 실행하지 않습니다.
공식 릴리스 resolver는 이 로컬 manifest를 받아들이지 않습니다.
로컬 패키지의 CPU는 실행 중인 CLI의 CPU와 비교하므로 Docker daemon과 같은 CPU의 native CLI를
사용해야 합니다. arm64 Docker에서 Rosetta로 실행한 amd64 CLI는 현재 지원하지 않습니다.

검증한 로컬 패키지와 공개키는 `.railwait/local-package`에 보존합니다. 최초 입력 경로가 없어져도
같은 설치 경로로 재개할 수 있으며 기존 secret·관리자를 유지합니다. 원본 패키지와 보존본을 위한
디스크 공간이 각각 필요하고 보존본은 설치 성공 뒤에도 유지합니다. 소스 revision·dirty 여부·소스
트리 지문과 실제 이미지 identity는 별도 기록이며, 소스 지문만으로 이미지의 빌드 출처를 입증하지
않습니다. 로컬 설치도 실제 Docker 서비스를 실행하지만 공개 안정 릴리스 설치와 구분합니다.
공식 릴리스 업데이트는 차단하며 현재 설치의 상태 조회, TAGO·HTTPS 접속 설정과 누락된 알림 키 준비는 지원합니다.

로컬 패키지 설치의 새 버전은 새 로컬 패키지로 업데이트합니다. 호스트에서 `--package`와 `--trust-key`를 붙여
`railwait update --dry-run`으로 계획을 만들고, 출력된 `--target`·`--plan`을 붙여 같은 패키지·공개키로 다시
실행합니다. 계획은 서명 manifest 전체와 공개키에 묶이므로 둘 중 하나라도 바뀌면 새 계획이 필요합니다. 패키지 안의
공개키는 자동으로 신뢰하지 않으며 이전 패키지의 공개키와 같을 필요는 없습니다. 대상은 더 높은 버전이어야 하고
DB revision·설정 schema·PostgreSQL·Redis 이미지·서비스 구성이 현재 설치와 같아야 합니다. 로컬 manifest에는
공식 릴리스의 `compatible_from`이 없으므로 출발 버전 허용은 운영자가 패키지와 공개키를 직접 고른 것과 이
조건들로 대신합니다. 기존 관리 서비스가 새 보존 패키지를 계속 읽을 수 있도록 manifest 형식은 그대로 둡니다.

적용은 웹이나 호스트 관리 서비스를 거치지 않고 이 CLI 프로세스가 설치 잠금을 잡고 실행합니다. 패키지를
`.railwait` 아래로 복사해 다시 검증하고 이미지를 불러온 뒤(`loading_package`), 보존 패키지 교체(`replacing_package`)
→ 새 작업 차단 → 기존 작업 종료 확인 → 암호화 백업 검증 → 앱 교체 → health·이미지 확인 → 작업 재개 순서로
진행합니다. 보존 패키지 `.railwait/local-package`는 새 작업을 막기 전에 교체하므로, 교체가 거부되면 서비스와
유지보수 상태를 바꾸지 않은 `failed`로 끝납니다. 그 뒤 단계에서 실패하면 새 설치 기록을 쓰기 전까지 이전 패키지로
되돌립니다. 되돌리는 이동까지 거부돼 보존 패키지가 비거나 일부만 남으면 작업 종료 대기가 연기된 경우를 포함해 단계와
관계없이 `recovery_required`로 남깁니다. 교체부터 새 설치 기록까지는 보존 패키지가 새 버전이라, 그사이 `railwait status`는 버전이 다르다고
알리면서 진행 중인 작업을 안내합니다. 교체 전에 이전 패키지의 임시 위치를 작업 기록에 남기므로 강제 종료된
작업도 `railwait status --path <설치 경로> --job <작업 ID>`로 확인할 수 있습니다. 적용 중 터미널 연결이 끊겨도
작업은 끝까지 진행하고 결과를 작업 기록에 남깁니다. Ctrl-C나 종료 신호는 실패 경로로 중단하며, 새 작업 차단 뒤라면
`recovery_required`로 남습니다. 이전 이미지는 Docker에 남지만 자동 rollback은 하지 않습니다. 관리 서비스 실행 파일은
바꾸지 않으므로 이 기능이 들어간 CLI를 따로 실행해야 합니다. 이미지만 바꾸는 임시 적용은 다음 설정·업데이트 작업이
설치 기록의 이미지로 서비스를 다시 만들면서 사라지므로 이 경로로 대체합니다. 그런 임시 적용이 실행 중이면 현재
서비스가 기록과 달라 계획이 차단되므로, 먼저 기록된 이미지로 되돌려 `railwait status`가 healthy인지 확인합니다.
Windows는 다른 프로세스가 폴더 안 파일을 열고 있으면 공유 모드와 관계없이 그 폴더의 이름 변경을 거부합니다. 백신 검사나
검색 색인이 파일을 잠깐 잡는 경우에 대비해 패키지 교체·되돌리기·복원·정리는 거부 오류(5·32·33)일 때만 최대 30초 동안 다시
시도합니다. 교체가 그래도 거부되면 서비스를 바꾸기 전이라 작업은 `failed`로 끝나고, 보존 패키지와 서비스는 그대로이며
옮기지 않은 이전 패키지 경로는 작업 기록에 남기지 않습니다. 파일을 잡고 있는 프로그램을 확인한 뒤 같은 패키지와
공개키로 `--dry-run`부터 다시 실행합니다. 되돌리기가 거부되면 작업은 `recovery_required`로 남고 이전 패키지는
`.local-package-previous-<작업 ID>`에 있으며 오류와 작업 기록이 그 경로를 알려 줍니다. 새 설치 기록을 쓴 뒤의 정리만
거부되면 업데이트는 성공하고 경고가 같은 경로를 알려 줍니다. 복구는 [운영 가이드](../../docs/OPERATIONS.md#로컬-패키지-업데이트가-멈췄을-때)를 따릅니다.

저장소의 커밋을 로컬 패키지 설치에 반영할 때는 macOS·Linux에서
`bash scripts/update-local-install.sh --path <설치 경로>`로 위 과정을 한 번에 실행합니다. 스크립트는 지정한
커밋(`--ref`, 기본 `HEAD`)을 임시 worktree로 꺼내 화면 버전과 `CHANGELOG.md` 맨 위가 같고 설치된 버전보다 높은지,
DB revision이 같은지 먼저 확인합니다. 그다음 Compose의 build 정의대로 앱 이미지를
`railwait-local-install-<이름>:<버전>` 태그로 빌드하고(실행마다 다른 build-id 라벨을 붙여 캐시 재빌드도 고유한
이미지가 되게 합니다), 빌드하지 않는 이미지가 Docker에 있는지 확인한 뒤 일회성
키로 서명한 로컬 패키지를 만듭니다. 개인키는 서명 직후 지웁니다. 같은 커밋의 CLI를 Docker 안의 Go로 빌드해
계획을 확인하고 적용한 다음, 새 CLI의 `status`, `/healthz`·`/readyz`·`/` 응답, web 컨테이너 화면 파일의 버전을
확인합니다. 적용은 기존 서비스를 멈춘 뒤 새 migration부터 실행하고 자동으로 되돌리지 않으므로, 빌드한 이미지는
패키지를 만들기 전에 각 이미지의 비root 기본 사용자로 한 번씩 실행해 봅니다. API는 import와 DB 없이 계산한
`alembic heads`가 설치 DB revision과 같은지, 웹은 `nginx -t`와 화면 파일 읽기, KORAIL 어댑터는 import와 Chrome
버전, 백업은 도구와 스크립트 실행 권한을 확인하고 실패하면 설치를 건드리지 않고 멈춥니다. worktree는 umask 022로
만들어 사용자의 umask가 이미지 파일 권한에 섞이지 않게 합니다.
`--dry-run`은 계획 확인까지만 하고, `--korail-smoke`는 적용 뒤 설치된 KORAIL 어댑터로 로그인·예약 없는
공개 좌석 조회를 한 번 실행합니다. 끝나면 임시 worktree와 작업 폴더를 지우고, 적용이 실패하거나 중단되면 작업
기록을 확인할 새 CLI와 패키지를 남겨 경로를 알려 줍니다. 그 뒤 복구는
[운영 가이드](../../docs/OPERATIONS.md#로컬-패키지-업데이트가-멈췄을-때)를 따릅니다. 실패한 작업이 남긴
`releases/<버전>` 폴더가 있으면 빌드 전에 알리고 멈춥니다. uv(Python 3.12 실행)와 Docker가 필요하며 uv 경로는
`UV` 환경변수로 지정할 수 있습니다. 웹 빌드 인자는 `.env`를 읽지 않고 Dockerfile 기본값을 씁니다. 이 값은
Compose 기본값과 같습니다. 커밋하지 않은 변경은 들어가지 않고, 이전 버전 이미지는 되돌리기용으로 남겨 자동으로
지우지 않습니다.

설치는 지정한 로컬 Docker context를 검사하고 실제 socket/pipe 주소를 고정합니다. 원격 context와
기존 설치 파일·동일 프로젝트 자원은 차단합니다. `--docker-context`를 생략한 대화형 새 설치는 현재
로컬 context를 확인해 사용합니다. 비대화형 새 설치는 기존과 같이 `default`를 사용합니다.
중단 뒤 같은 경로로 다시 실행하면 기록한 context와 포트를 유지합니다.
Docker Desktop이나 로컬 VM에서는 설치 경로를 파일 공유 대상으로 허용해야 합니다. 이미지 다운로드
뒤 실제 API 컨테이너에서 관리 파일과 잠금 파일을 읽는지 확인하고, 실패하면 DB를 만들기 전에
중지합니다. 파일 공유의 짧은 전파 지연은 최대 세 차례 확인합니다.

접속은 loopback 전용이며 관리자 공개 등록은 꺼 둡니다. 검증한 배포 묶음의 좌석 감시·sidecar
기본값과 `experimental-rail` 프로필을 유지하고 자동 예약은 꺼 둡니다. TAGO 키와 관리자 비밀번호는
숨김 입력으로 받습니다. TAGO는 외부 서비스에서 직접 발급받아야 하며 CLI가 대신 발급하지 않습니다.
TAGO는 생략할 수 있으며, 철도사 계정은 설치 후 웹 설정에서 연결합니다.
일반 설치가 공식 철도사 인증·좌석 조회의 성공을 보장하지는 않습니다.

## 보안 키와 브라우저 알림 키

새 설치의 **자동 생성**은 내부 보안키 6종을 각각 별도 난수로 만들고 VAPID P-256 키 쌍도 생성합니다.
**직접 입력**을 선택하면 내부 키를 숨김 입력할 수 있고 빈 항목은 자동 생성합니다. 직접 지정하는 내부
키는 영문·숫자·`-`·`_`로만 구성된 32~1024자여야 합니다. 허용되는 이름은 다음과 같습니다.

| `app_secrets` 키 | 용도 |
| --- | --- |
| `POSTGRES_PASSWORD` | 데이터베이스 비밀번호 |
| `SECRET_ENCRYPTION_KEY` | 저장한 비밀정보 암호화 |
| `AUTH_SESSION_SECRET` | 로그인 세션 |
| `KORAIL_BROWSER_ADAPTER_TOKEN` | KORAIL 내부 연결 |
| `SRT_PROVIDER_ADAPTER_TOKEN` | SRT 내부 연결 |
| `KORAIL_PROVIDER_COOLDOWN_REDIS_PASSWORD` | 내부 상태 저장소 |

VAPID를 직접 입력할 때는 패딩 없는 base64url로 표현한 P-256 공개키·비밀키를 함께 입력합니다.
비밀키의 원본은 32바이트이며 공개키는 그 비밀키에 대응하는 65바이트 비압축 공개점이어야 합니다.
키 쌍의 유효성과 일치를 검사하며 한쪽만 입력한 값은 거절합니다. 두 키를 모두 비우면 자동 생성합니다.
`subject`는 포트·경로 없는 `https://호스트명` 또는 `mailto:` 주소입니다. 자동 선택은 계획한 Tailscale
주소에서 호스트명만 사용하며 앱의 실제 접속 포트는 바꾸지 않습니다. 로컬 전용 기본값 `https://github.com`은
프로젝트 호스팅 도메인으로, 운영자의 구체적인 연락처를 뜻하지는 않습니다. 실제 연락처를 지정하려면
수동 이메일 주소를 사용하세요. 이는 현재 발송 라이브러리의 HTTPS subject 처리 범위에 맞춘 정책입니다.

재실행은 기존 내부 키와 VAPID 키를 유지하며 다른 키로의 교체를 거절합니다. 이전 CLI 설치에 VAPID 키
쌍이나 subject가 없으면 설치 마법사가 `configure_push` 계획을 만들고 작업 종료 대기·암호화 백업·서비스
재생성·health 확인을 거쳐 보완합니다. 이때 잠시 접속이 중단될 수 있습니다. 한쪽만 남았거나 손상된
VAPID 키는 자동 교체하지 않고 중단하며, 이 절차로 기존 내부 암호화키를 재발급하지 않습니다.
기존 PEM·DER 형식의 유효한 P-256 키도 표현을 그대로 보존합니다. 포트·경로가 포함돼 발송에 사용할 수
없는 subject는 키를 유지한 채 지원되는 주소로 보완합니다.
이미 유효한 VAPID 키가 있으면 재사용하며 보호된 JSON으로 subject만 변경할 때도 같은 설정 절차를 거칩니다.

키 준비와 브라우저 알림 수신 허용은 별개입니다. 설치 후 웹 알림 설정에서 사용자가 브라우저·OS 알림
권한을 직접 허용해야 합니다. 키 생성이나 서비스 health만으로 실제 기기 알림 전달까지 검증한 것은 아닙니다.

## Tailscale 원격 접속

`--access tailscale`을 지정하거나 대화형 접속 선택에서 Tailscale을 선택합니다. Windows·macOS에서
미설치라면 위의 검증된 패키지 설치를 이어갑니다. 상태가 `NeedsLogin`일 때만 옵션 없는 `tailscale up`으로
로그인을 시작합니다. VPN 허용과 SSO는 사용자가 직접 처리하며 앱에서 로그인한 뒤 재확인할 수도 있습니다.
기존 프로필을 전환하는 `tailscale login`이나 설정 초기화 옵션을 사용하지 않습니다. 초기화 중인 `NoState`는
잠시 기다려 다시 확인하고, 연결된 노드가 오프라인인 경우에는 연결 복구를 안내합니다. 이미 연결된 노드에는
`tailscale up`을 실행하지 않으며 기존 서버의 CLI 연결과 노드를 재사용합니다.

기존 Serve·TCP 경로를 확인해 빈 HTTPS 포트(443, 8443, 9443, 10443)를 선택하거나 `--https-port`로 지정합니다.
대상을 이미 연결한 경로와 기존 설치의 HTTPS 포트는 재사용합니다. 적용 직전·직후 설정을 대조하고 다른
연결이 바뀌면 중단합니다. Funnel이 켜진 포트는 거부하며 전역 reset·Funnel 활성화·Tailnet ACL 변경은 없습니다.
처음 HTTPS를 사용할 때 공식 CLI가 승인을 요구하면, 대화형 마법사가 검증한 Tailscale 승인 페이지를
한 번 열고 최대 5분 동안 승인을 기다립니다. 브라우저에서 HTTPS 사용을 승인하고 공개 Funnel은 켜지 마세요.
승인 후에는 같은 계획의 노드·전체 경로를 다시 검사하며 진행합니다. 일회용 승인 URL은 오류·로그·설치
기록에 넣지 않습니다. 비대화형 실행, 승인 거부나 시간 초과는 설정 후 같은 명령으로 재개하도록 안내합니다.

연결 후 앱의 허용 출처를 해당 HTTPS 주소로 제한하고 보안 쿠키를 켭니다. 기존 암호화 DB·설정 백업,
작업 종료 대기, 서비스 재시작과 health 확인 절차를 사용하므로 잠시 접속이 중단됩니다. **로그인은 완료
화면의 HTTPS 주소에서 진행하세요.** localhost HTTP는 로그인용 주소가 아닙니다. 실제 HTTPS 인증서 검증과
관리자 계정이 준비된 API 응답까지 확인한 뒤 완료를 표시합니다. 원격 연결 실패를 전체 완료로 표시하지 않습니다.

`.railwait/access.json`은 확인한 주소·포트·시각만 담는 보조 기록입니다. 재실행은 실제 Serve와 앱 설정을
다시 확인하며 이 파일만으로 기존 경로를 변경하지 않습니다. 실패 시 앱 설정이 아직 적용되지 않았고
유지보수 gate가 열려 있다고 확인되는 경우에만 이번에 추가한 경로를 제거합니다. 적용 여부가 불명확하거나
gate가 닫혔으면 복구를 위해 보존하며 안내합니다. 자동 DB rollback·HTTPS 해제 기능은 제공하지 않습니다.

동일 Tailscale 호스트에 두 Railwait 설치를 서로 다른 포트로 병행하면 브라우저 쿠키는 포트를 구분하지
않습니다. 두 설치에 동시에 로그인하려면 브라우저 프로필을 분리하거나 별도 Tailscale 노드를 사용하세요.

공식 절차: [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve),
[HTTPS 설정](https://tailscale.com/docs/how-to/set-up-https-certificates),
[macOS CLI](https://tailscale.com/docs/reference/tailscale-cli?tab=macos).

비대화형 설치는 명령행 비밀번호 대신 권한을 제한한 JSON 파일이나 stdin을 사용합니다.

```console
railwait install --path <새 설치 경로> --input <보호된 JSON 파일>
```

새 설치 JSON은 `username`, `password`와 선택적인 `tago_service_key`, `app_secrets`, `webpush`를 받습니다.
`app_secrets`는 위 6개 이름만 허용하는 문자열 객체이며 생략한 키는 자동 생성합니다. JSON에서 빈 문자열을
지정하면 내부 키의 길이 검증에 실패하므로 자동 생성할 항목은 생략합니다. `webpush` 객체는
`public_key`, `private_key`, `subject`만 받으며 키 쌍 생략은 자동 생성입니다. 임의 환경변수·알 수 없는
필드·서로 맞지 않는 VAPID 키는 거절하고 입력 전체는 8 KiB로 제한합니다. Unix 파일 권한은
`0600`이어야 하며 Windows에서는 현재 사용자만 접근하도록 ACL을 제한합니다. 입력 파일은
사용자가 관리하는 자원이므로 CLI가 삭제하지 않습니다. `--input -`는 stdin을 사용합니다.

설치 기록은 `.railwait/install.json`, 설정은 `.env`, 검증한 배포 묶음은 `releases/<version>`에
저장합니다. 자동 생성하거나 직접 지정한 내부 키 6종과 VAPID 키는 재실행 때 보존합니다. 다운로드와 파일
교체 경계, 관리자 생성 중 중단은 기록을 바탕으로 재개하며 이미 생성한 관리자를 변경하지 않습니다.
검증한 배포 파일이 바뀌거나 암호화키가 없으면 적용하지 않습니다. migration 실행 중 종료되어
결과를 확정할 수 없으면 자동 재실행하지 않고 DB 상태 확인을 요구합니다.

설치가 끝나면 실행 파일을 `.railwait/bin`에 보존하고 사용자 로그인 세션에서 호스트 관리 서비스를
시작합니다. Linux는 user systemd, macOS는 LaunchAgent, Windows는 현재 사용자의 Task Scheduler를
사용합니다. 실제 부팅·로그아웃 조건은 OS별로 다르며 Linux의 lingering을 자동으로 설정하지 않습니다.
등록 실패는 앱 설치 완료와 구분해 보고하며 `railwait manager install --path <설치 경로>`로 재개합니다.

업데이트는 15분간 유효한 계획에서 확인한 버전만 적용합니다. CLI와 웹은 같은 호스트 관리 작업을
사용하므로 진행 화면을 닫아도 제출한 작업은 유지됩니다. 현재 DB revision과 설정 schema가 같고,
출발 버전을 명시적으로 허용하며 PostgreSQL·Redis 이미지와 서비스 구성이 같은 릴리스만 적용합니다.
새 작업을 차단하고 진행 작업 종료를 확인한 뒤 DB와 설정을 age로 암호화·복호화 검증하고 앱을
교체합니다. 실제 실행 이미지와 health를 확인한 뒤에만 재개합니다. 실패·중단으로 결과가 불명확하면
유지보수 gate를 보존하며 자동 DB 복원이나 무검증 rollback을 수행하지 않습니다.

첫 업데이트의 로컬 복구키는 `.railwait/backup.env`에 현재 사용자 전용 권한으로 보존합니다. 이 파일은
비밀이며 별도의 안전한 장소에도 보관해야 합니다. 암호화 백업은 기존 Compose `backups` 볼륨에
작업 ID로 남고 자동 회전하지 않습니다. 같은 호스트의 키와 백업만으로 디스크 손실에 대비할 수는 없습니다.

`configure`는 TAGO 키만 변경합니다. 숨김 입력 또는 `--input`의 `tago_service_key`를 받아 계획을
만들고, 별도 `--plan` 실행 시 업데이트와 같은 종료 대기·암호화 백업·앱 재시작을 수행합니다. 입력값은
공개 계획·관리 요청에 포함하지 않습니다. 적용 전 후보는 `.railwait/plans`의 보호된 파일로 보존하고,
실행 종료 시 삭제합니다. 적용하지 않은 만료 후보는 다음 계획 생성 때 정리합니다.

설치 파일시스템에는 최소 4 GiB 여유 공간을 요구합니다. Docker 저장소·Docker Desktop VM 내부의
여유 공간과 이미지 전체의 압축 해제 크기는 별도로 확인해야 합니다. DB schema·저장소 이미지 변경,
기존 소스 설치의 `adopt`, 자동 복원과 관리 서비스 자체 업데이트는 아직 제공하지 않습니다.

개발 검증에는 Go 1.27 이상을 사용합니다.

```console
go test -race ./...
go vet ./...
go build ./cmd/railwait
```

단위 테스트는 임시 디렉터리와 가짜 Docker runner를 사용합니다. 여섯 대상의 컴파일과 각 OS의
CI 테스트를 실제 Docker 설치·재부팅 복구 검증과 구분합니다. macOS ARM의 기존 Lima·Tailscale을 재사용한
로컬 패키지 설치와 HTTPS 연결은 확인했습니다. 2026-11-17에는 Windows의 기존 Docker에서 별도 소스
스냅샷으로 Linux amd64 앱 이미지 4종을 빌드하고 네트워크 없는 실행 확인, 패키지 계약 27건과 최신
Windows CLI의 서명·전체 archive 검증을 통과했습니다. 이 패키지는 19개 서비스·11개 고유 이미지를
포함하며 기존 ARM 패키지와 별개입니다. Windows·macOS의 필수 프로그램 최초 설치, OS 승인·SSO,
재부팅 후 복구는 별도 실기기 검증이 남아 있습니다. Windows 검증 VM은 관리자 실행 취소로 시작하지
못했으며 패키지 제작 성공을 실제 신규 설치 성공으로 기록하지 않습니다.
