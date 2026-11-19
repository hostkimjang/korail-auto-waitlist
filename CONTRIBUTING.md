# 기여 가이드

레일웨잇에 관심을 가져 주셔서 감사합니다. 이 프로젝트는 개인 서버에 설치하는 서비스이며 철도사 보호 조치, 인증정보, 결제와 맞닿아 있습니다. 기능 수보다 안전한 동작과 검증 가능한 근거를 우선합니다.

## 시작하기 전에

- 버그를 신고하거나 기능을 제안하기 전에 기존 이슈를 확인해 주세요.
- 취약점, 실제 인증정보, 쿠키, 토큰, 내부 URL, 개인정보가 포함된 내용은 공개 이슈나 PR에 올리지 말고 [보안 정책](SECURITY.md)을 따르세요.
- 보안문자(CAPTCHA), 접속 대기(NetFUNNEL), 호출 제한, 비정상 접근 탐지 또는 운영사 정책을 우회하는 변경은 받지 않습니다.
- 자동 결제, 결제정보 저장, 계정·IP 회전, 공격적인 폴링을 추가하는 변경은 프로젝트 범위 밖입니다.

## 개발 환경

- Node.js 22
- Python 3.12와 `uv`
- Go 1.27 이상 (설치 CLI `apps/cli`를 바꿀 때)
- Docker Engine 또는 Docker Desktop + Compose v2
- Chromium을 사용하는 테스트에는 Playwright 브라우저 설치가 필요합니다.

웹 개발:

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

API 개발과 전체 실행 방법은 [apps/api/README.md](apps/api/README.md)와 [운영 가이드](docs/OPERATIONS.md)를 참고하세요.

## 변경 원칙

1. 확인된 사실, 설계 제안, 운영 환경에서 미검증인 항목을 구분합니다.
2. 외부 응답과 저장 JSON은 시스템 경계에서 검증하고, 근거 없는 좌석 상태는 확인하지 못한 상태로 처리합니다.
3. 행동 변경에는 사용자 상태 전이 또는 API 계약을 검증하는 회귀 테스트를 추가합니다.
4. 기능, 상태, API, 환경변수, 운영 절차 또는 안전 경계가 바뀌면 관련 문서와 `CHECKLIST.md`를 같은 PR에서 갱신합니다.
5. 실제 비밀값, 계정 식별자, 쿠키, 브라우저 저장 상태, 결제정보를 테스트 데이터·로그·스크린샷에 넣지 않습니다.

세부 규칙은 [코드 작성 규칙](docs/CODE_CONVENTIONS.md)을 따릅니다.

## 릴리스 버전 올리기

사용자가 화면에서 최신 배포를 받았는지 확인할 수 있도록, 사용자에게 보이는 변경을 배포할 때는 버전을 함께 올립니다.

1. `apps/web/src/shared/lib/appRelease.ts`의 `APP_RELEASE`에서 `version`과 KST 기준 배포 날짜 `releasedOn`을 갱신합니다.
2. `CHANGELOG.md` 맨 위에 `## 1.1.0 — 2026-09-19 (토)` 형식으로 항목을 추가하고, 무엇이 달라졌는지 사용자 관점으로 적습니다. 항목 끝에는 `적용할 때:` 줄로 `.env` 새 항목·DB migration 여부와 다시 만들어야 하는 범위를 적어, 서버를 업데이트하는 사람이 할 일을 알 수 있게 합니다. Git 설치와 로컬 패키지 설치에서 할 일이 다르면 둘 다 적습니다. 이 파일에서 `## `로 시작하는 줄은 릴리스 제목만 쓸 수 있습니다.
3. `cd apps/web` 후 `npm test`로 두 기록이 일치하는지 확인합니다. 버전·날짜·요일이 어긋나면 `tests/appRelease.test.ts`가 실패합니다.
4. 커밋한 뒤 로컬 패키지로 설치한 관리형 설치에는 `bash scripts/update-local-install.sh --path <설치 폴더>`로 반영합니다. 스크립트는 커밋한 내용만 사용합니다. Git 설치는 [Git으로 새 버전 받아 재가동하기](docs/OPERATIONS.md#git으로-새-버전-받아-재가동하기)를 따릅니다.
5. 공식 서명 릴리스는 `main`에서 `Signed release publication` 워크플로를 수동 실행해 게시합니다. 입력 버전은 `CHANGELOG.md` 맨 위와 같아야 하고, 저장소 secret `RAILWAIT_RELEASE_SIGNING_KEY`와 공개 GHCR 패키지가 필요합니다. `compatible_from`에는 게시된 manifest의 DB revision·설정 schema·PostgreSQL·Redis 이미지가 새 릴리스와 같은 버전만 넣을 수 있고, 워크플로가 이를 확인해 다르면 이미지를 빌드하거나 서명하기 전에 멈춥니다([저장소 이미지와 업데이트 출발 버전](scripts/releases/README.md#저장소-이미지와-업데이트-출발-버전)). 이 워크플로가 `v<버전>` 태그와 GitHub Release를 만들고 설치 CLI는 최신 Release의 서명 manifest를 읽으므로, 태그나 Release를 손으로 만들지 않습니다. 워크플로 자체를 바꿨다면 게시 전에 검토 브랜치에서 `dry_run=true`로 먼저 실행합니다. dry run은 저장소 서명 키를 쓰지 않고 게시하지 않습니다. 자세한 내용은 [릴리스 도구 안내](scripts/releases/README.md)에 있습니다.
6. 새 Release 자산이나 그 릴리스에서 처음 생기는 기능에 기대는 설치 안내(README, [시작하기](docs/GETTING_STARTED.md), [설치 CLI 안내](apps/cli/README.md) 등)는 게시를 확인한 뒤 별도 커밋으로 `main`에 올립니다. `releases/latest/download/<자산>` 주소는 그 시점의 최신 Release로만 연결되므로, 게시 전에 안내가 공개되면 이전 Release로 연결돼 404가 납니다. Windows PowerShell 5.1에서는 이 404가 `요청이 중단되었습니다. 예기치 않게 연결이 닫혔습니다.`로 보일 수 있습니다. "1.4.4부터 쓸 수 있습니다" 같은 문구로는 이 순서를 대신할 수 없습니다. 순서는 다음과 같습니다.
   1. 코드·테스트와 `CHANGELOG.md`·`APP_RELEASE`, 아직 공개되지 않은 자산을 안내하지 않는 문서를 커밋해 `main`에 올리고 CI를 확인합니다.
   2. 5단계의 워크플로로 게시합니다.
   3. 최신 Release가 새 버전인지, 안내할 자산이 모두 200으로 받아지는지 확인합니다.

      ```bash
      gh api repos/hostkimjang/korail-auto-waitlist/releases/latest --jq .tag_name
      for asset in install.sh install.ps1 release-manifest.json; do
        curl -sSL -o /dev/null -w "%{http_code} $asset\n" \
          "https://github.com/hostkimjang/korail-auto-waitlist/releases/latest/download/$asset"
      done
      ```

   4. 확인한 뒤에 설치 안내 커밋을 `main`에 올립니다.

버전은 `주.부.수` 형식입니다. 사용 방식이나 운영 절차가 달라지면 주, 새 기능과 눈에 띄는 개선은 부, 버그 수정과 안정화는 수를 올립니다. 문서나 내부 정리만 바꾼 변경에는 버전을 올리지 않습니다.

## 검증

전체 저장소:

Linux Bash:

```bash
bash ./scripts/ops.sh verify
```

Windows PowerShell:

```powershell
./scripts/ops.ps1 verify
```

Pull Request의 핵심 GitHub Actions 검증은 Compose 설정, API, 웹과 PostgreSQL 경합 계약을 실행합니다. KORAIL Chromium 컨테이너는 GitHub runner의 브라우저 실행 정책에 영향을 받으므로 별도 `experimental-browser-verify` 워크플로에서 검증합니다. 이 분리는 실패를 무시하지 않으며, 로컬 전체 검증은 계속 두 범위를 모두 포함합니다.

웹 변경:

```console
cd apps/web
npm run lint
npm run typecheck
npm test
npm run build
npm run test:sites
```

API 변경:

```console
cd apps/api
uv lock --check
uv run --extra test pytest
uvx --from ruff==0.12.12 ruff check --select E,F,I .
uv run --frozen --extra test mypy
```

설치 CLI 변경:

```console
cd apps/cli
gofmt -l .
go vet ./...
go test -race ./...
```

`gofmt -l`은 아무것도 출력하지 않아야 합니다. OS별 코드를 바꿨다면 `GOOS=windows go vet ./...`와 `GOOS=darwin go vet ./...`로 다른 OS의 컴파일도 확인합니다. GitHub Actions는 Ubuntu·macOS·Windows에서 같은 검사를 실행하고 6개 OS·CPU 조합을 빌드합니다.

릴리스 도구와 로컬 설치 업데이트 스크립트 변경:

```console
uv run --no-project --python 3.12 --with-requirements scripts/releases/requirements.txt python -m unittest discover -s scripts/releases -v
bash -n scripts/update-local-install.sh
```

업데이트 스크립트는 macOS 기본 bash 3.2에서도 돌아야 하므로 연관 배열과 `mapfile`을 쓰지 않습니다.

실행하지 않은 검증을 통과했다고 기록하지 마세요. 외부 철도사, 공개 도메인, 실제 알림 채널, 실기기 확인은 로컬 자동 테스트와 분리해 표시합니다.

## Pull Request

- 한 PR은 하나의 설명 가능한 변경 이유를 갖게 합니다.
- 사용자에게 보이는 변화, 안전에 미치는 영향, 데이터베이스 마이그레이션·환경변수 변경 여부, 수행한 검증을 본문에 적습니다.
- UI 변경은 데스크톱, 390px, 320px, 키보드와 200% 확대 영향을 확인합니다.
- API나 상태 규칙을 바꾸면 관련 호출 코드, 데이터 변환 코드, 테스트와 문서를 함께 갱신합니다.
- 비밀값이 포함될 수 있는 `docker compose config`, `docker inspect`, 운영 환경 추적 자료(trace), HAR 파일은 첨부하지 않습니다.

작은 수정도 환영하지만, 대규모 구현 전에는 이슈에서 범위와 안전 경계를 먼저 합의하는 편이 좋습니다.
