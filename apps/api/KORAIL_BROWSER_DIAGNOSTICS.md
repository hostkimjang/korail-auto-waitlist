# KORAIL 브라우저 화면 진단

기본 KORAIL adapter는 내부 Xvfb에서 non-headless Chrome을 실행하지만 화면 listener와 host 포트를
열지 않습니다. Chromium 화면이 꼭 필요할 때만 로컬 noVNC 구성을 사용합니다. adapter 화면은 읽기
전용이며 `127.0.0.1`에만 열립니다. 보호 응답을 우회하거나 원격 운영 화면으로 공개하는 용도가 아닙니다.

PowerShell에서 저장소 루트를 기준으로 실행합니다.

```powershell
New-Item -ItemType Directory -Force secrets | Out-Null
$bytes = New-Object byte[] 6
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
$password = [Convert]::ToBase64String($bytes)
[IO.File]::WriteAllText(
  (Join-Path (Get-Location) "secrets/korail-novnc-password.txt"),
  $password,
  [Text.UTF8Encoding]::new($false)
)
docker compose --profile experimental-rail -f compose.yml -f compose.korail-gui.yml config --quiet
docker compose --profile experimental-rail -f compose.yml -f compose.korail-gui.yml up -d --build --force-recreate korail-browser-adapter
```

브라우저에서 `http://127.0.0.1:6080/vnc.html`을 열고 방금 만든 8자 비밀번호를 입력합니다.
고전 VNC 인증은 8바이트만 사용하므로 이 비밀번호를 다른 서비스와 공유하지 말고 진단할 때마다
새로 만드세요. 화면과 캡처에는 계정·세션 정보가 보일 수 있으므로 외부에 공유하지 않습니다.

Linux에서는 host 사용자와 컨테이너의 `pwuser` 숫자 UID가 다를 수 있습니다. 다음처럼 8바이트
비밀번호를 만든 뒤 adapter UID 1001만 읽을 수 있도록 소유권과 권한을 맞춥니다. 비밀번호 값은
터미널이나 채팅에 출력하지 않습니다.

```bash
mkdir -p secrets
chmod 0700 secrets
python3 - <<'PY'
from pathlib import Path
import secrets

alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
Path("secrets/korail-novnc-password.txt").write_text(
    "".join(secrets.choice(alphabet) for _ in range(8)),
    encoding="ascii",
)
PY
sudo chown 1001:1001 secrets/korail-novnc-password.txt
sudo chmod 0600 secrets/korail-novnc-password.txt
docker compose --profile experimental-rail -f compose.yml -f compose.korail-gui.yml config --quiet
docker compose --profile experimental-rail -f compose.yml -f compose.korail-gui.yml up -d --build --force-recreate --no-deps korail-browser-adapter
```

OCI 같은 원격 서버의 6080 포트를 외부에 열지 않습니다. 로컬 PC에서 `ssh -L
6080:127.0.0.1:6080 <서버>`로 전달한 뒤 `http://127.0.0.1:6080/vnc.html`을 엽니다.

진단이 끝나면 noVNC listener가 없는 기본 내부 Xvfb 구성으로 되돌립니다.

```powershell
docker compose --profile experimental-rail -f compose.yml up -d --force-recreate korail-browser-adapter
Remove-Item -LiteralPath secrets/korail-novnc-password.txt
```

Linux에서는 같은 Compose 명령을 실행한 뒤 `rm -f secrets/korail-novnc-password.txt`로 제거합니다.

## 무인 서버의 앱 전용 자동 로그인

`KORAIL_BROWSER_PROFILE_MODE=dedicated_auto`는 아래 수동 진단과 같은 전용 named volume·owner lock을
사용하지만 noVNC 입력을 요구하지 않습니다. one-shot bootstrap 뒤 기본 `compose.yml`에서 adapter를 실행하면
서버에 저장된 철도 계정으로 기존 Pydoll 로그인 driver가 동작합니다. 일반 Chrome profile·쿠키·비밀번호
저장소를 가져오지 않으며 Chrome 비밀번호 관리자를 자동화하지 않습니다. 공식 보호·호출 제한 응답이면 한 번의
닫힌 결과를 기록하고 provider cooldown으로 중단합니다. profile 연속성은 로그인 성공 보장이 아닙니다.

```dotenv
KORAIL_BROWSER_PROFILE_MODE=dedicated_auto
KORAIL_NOVNC_INPUT_ENABLED=false
```

2026년 10월 11일 검증에서는 기본 Compose만 사용해 noVNC를 시작하지 않았습니다. adapter와 API 재생성 뒤
저장 계정으로 자동 제출한 세션이 `authenticated`·`ready`·재사용 가능 상태가 됐고, adapter만 재시작한 뒤에도
12초 안에 공식 세션을 자동 확인했습니다. 따라서 이 모드의 정상 운영 절차에 VNC 접속은 없습니다. 실패 시
화면 진단으로 자동 전환하지 않으며, 닫힌 결과와 cooldown을 먼저 확인한 뒤 별도 진단 창을 엽니다.

## 앱 전용 live Chrome에서 직접 로그인하기

`KORAIL_BROWSER_PROFILE_MODE=dedicated_manual`은 일반 Chrome 프로필이나 쿠키를 복사하지 않습니다.
별도 Docker named volume에는 앱 전용 marker와 최소 브라우저 상태만 두고, adapter가 연 Chrome에서 사용자가
공식 로그인 화면을 직접 완료합니다. 첫 인증 확인이 `auth_required`가 되어도 adapter는 그 Chrome과 현재
탭을 닫지 않습니다. 공식 화면이 로그인 완료를 새 탭으로 전환해도 같은 browser context의 세션은 공유됩니다.
같은 계정 세대의 다음 확인은 adapter가 보유한 탭의 정확한 인증 header를 먼저 보고, header가 낡아 있으면
읽기 전용 공식 `loginCheck`를 한 번 확인합니다. 양성일 때만 검색 화면을 갱신해 상주 세션으로 승격하고,
비인증·확인 불가이면 `auth_required`를 유지합니다. 세션이 없거나 만료되면 저장된 아이디·비밀번호를 자동
제출하지 않습니다.

2026년 10월 10일 첫 로컬 Docker 확인에서는 전용 Chrome을 정상 종료하고 같은 profile을 다시 열었을 때
공식 화면이 `로그인` 상태였습니다. 반면 10월 11일 장시간 수동 인계 표본은 같은 전용 profile로 교체된
Chrome에서도 공식 인증이 유지됐습니다. 세션 cookie의 수명과 종료 시점에 따라 결과가 달라질 수 있으므로
profile 디렉터리나 `--restore-last-session`만으로 복원을 보장하지 않습니다. Chrome·adapter 재시작 뒤에는
항상 같은 계정 세대의 읽기 전용 세션 확인을 다시 통과해야 하며, 실패하면 사용자가 다시 직접 로그인합니다.

먼저 위 절차대로 8자 noVNC 비밀번호를 만들고 adapter를 중지합니다. bootstrap은 브라우저를 열지 않는
일회성 volume 초기화 작업입니다. marker·권한·단일 owner lock을 준비한 뒤 종료 코드 0으로 끝나야 합니다.
named volume과 다른 서비스의 volume은 삭제하지 않습니다.

```powershell
docker compose --profile experimental-rail -f compose.yml stop korail-browser-adapter
docker compose --profile korail-profile-bootstrap -f compose.yml -f compose.korail-gui.yml config --quiet
docker compose --profile korail-profile-bootstrap -f compose.yml -f compose.korail-gui.yml up --build --force-recreate --no-deps korail-browser-profile-bootstrap
docker compose --profile korail-profile-bootstrap -f compose.yml -f compose.korail-gui.yml rm -f korail-browser-profile-bootstrap
```

`.env`에서 전용 profile과 adapter noVNC 입력을 명시적으로 켭니다. 입력 포트는 GUI overlay가 고정한 host
loopback 밖으로 공개하지 않습니다.

```dotenv
KORAIL_BROWSER_PROFILE_MODE=dedicated_manual
KORAIL_NOVNC_INPUT_ENABLED=true
```

```powershell
docker compose --profile experimental-rail -f compose.yml -f compose.korail-gui.yml config --quiet
docker compose --profile experimental-rail -f compose.yml -f compose.korail-gui.yml up -d --build --force-recreate --no-deps korail-browser-adapter
```

현재 계정의 일반 인증 확인을 한 번 실행합니다. 첫 결과가 `auth_required`이면 실패로 adapter를 재시작하지
않고, Chrome 프로세스와 session state가 그대로 남아 있는지 확인합니다. 그 상태에서
`http://127.0.0.1:6080/vnc.html`을 열어 사용자가 공식 화면에 직접 로그인합니다. clipboard는 차단된 상태를
유지하고 비밀번호 저장 제안은 수락하지 않습니다. 로그인 뒤 Chrome 창을 닫지 않습니다.

같은 계정 세대로 인증 확인을 다시 한 번 실행합니다. 수동 인계 상태에서는 일반 세션 재사용 시간·횟수 제한이
지났더라도 같은 Chrome PID를 먼저 확인하며, 공식 인증과 session state `ready`·`locally_reusable=true`가
모두 확인돼야 성공입니다. 로그인 완료가 새 탭에서 일어났다는 이유만으로 실패 처리하지 않지만, adapter가
새 로그인 탭을 만들거나 저장 자격증명을 제출하지는 않습니다.
이후 전체 앱의 API session manager가 재사용 기한 전에 같은 세션을 자동 재확인하므로 사용자가 이 절차를
반복하지 않습니다. 같은 자격증명 지문이면 계정 세대 표기가 갱신돼도 fresh 공식 확인 뒤 기존 Chrome을 새
세대에 결합하고, 지문이 달라지면 폐기합니다. adapter와 Redis만 실행한 진단 상태에는 이 관리자가 없습니다.
readiness probe와 공개 읽기 검색은 전용 인증 profile을 잠그지 않는 별도 임시 profile을 사용합니다. 성공
전에는 좌석 검색이나 예약을 재개하지 않습니다.
보호·호출 제한 응답이면 기존 provider cooldown을 그대로 따릅니다.

2026년 10월 11일 수정 전 adapter에서 첫 확인은 `auth_required`였고 사용자가 같은 live Chrome에서 직접
로그인했습니다. 로그인 완료 탭은 `/ticket/main`에서 `로그아웃`을 표시했지만 adapter 보유 조회 탭의 DOM은
로그인 전 상태였습니다. 보유 탭의 공식 `loginCheck` HTTP 200 인증 양성 뒤 수동으로 탭을 갱신하면 같은
PID·계정 세대가 `authenticated`·`ready`·재사용 가능으로 전환돼 판정 순서 결함을 확정했습니다.

수정한 새 이미지의 2차 확인은 수동 탭 갱신 없이 HTTP 200 `authenticated`, session `ready`, 계정 세대 일치,
`locally_reusable=true`를 반환했고 두 page target도 `/ticket/search/general`이었습니다. 이때 최초 인계 뒤
30분이 지나 기존 PID가 확인 전에 교체된 사실을 추가로 발견했습니다. 같은 profile에서 공식 세션은 유지돼
이번 확인은 성공했지만 복원되지 않는 표본도 있으므로, 같은 세대의 `auth_required` 수동 인계는 일반 TTL과
횟수 제한보다 현재 live Chrome을 먼저 확인하도록 수정하고 회귀 테스트를 추가했습니다. 두 검증 모두
자격증명·쿠키 원문을 읽지 않았고 좌석 조회와 예약 요청도 수행하지 않았습니다. 이 보완을 포함한 이미지를
다시 빌드·재생성한 뒤에도 adapter `healthy`, 같은 세대의 `authenticated`·`ready`·재사용 가능, 조회 화면
page target 2개와 자격증명 제출 로그 0건을 확인했습니다.

원격 서버에서도 6080을 외부에 공개하지 않고 SSH loopback tunnel만 사용합니다. 전용 profile volume은
세션 비밀로 취급해 백업·복제·공유 대상에서 제외하고, 일반 Chrome 기본 profile 경로를
`KORAIL_BROWSER_PROFILE_DIR`로 지정하지 않습니다. adapter는 정확한 marker와 비공개 권한을 갖춘 앱 전용
디렉터리만 허용합니다. adapter를 재빌드·재생성하면 메모리의 live session은 끝나며 profile 복원도 보장되지
않으므로, 코드 배포와 검증을 모두 마친 뒤 세션을 다시 확인하고 필요할 때 마지막 단계에서 직접 로그인합니다.
