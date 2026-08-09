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

실행하지 않은 검증을 통과했다고 기록하지 마세요. 외부 철도사, 공개 도메인, 실제 알림 채널, 실기기 확인은 로컬 자동 테스트와 분리해 표시합니다.

## Pull Request

- 한 PR은 하나의 설명 가능한 변경 이유를 갖게 합니다.
- 사용자에게 보이는 변화, 안전에 미치는 영향, 데이터베이스 마이그레이션·환경변수 변경 여부, 수행한 검증을 본문에 적습니다.
- UI 변경은 데스크톱, 390px, 320px, 키보드와 200% 확대 영향을 확인합니다.
- API나 상태 규칙을 바꾸면 관련 호출 코드, 데이터 변환 코드, 테스트와 문서를 함께 갱신합니다.
- 비밀값이 포함될 수 있는 `docker compose config`, `docker inspect`, 운영 환경 추적 자료(trace), HAR 파일은 첨부하지 않습니다.

작은 수정도 환영하지만, 대규모 구현 전에는 이슈에서 범위와 안전 경계를 먼저 합의하는 편이 좋습니다.
