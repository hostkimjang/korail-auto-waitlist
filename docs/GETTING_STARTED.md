# 시작하기

레일웨잇을 처음 실행하는 방법을 안내합니다. 화면만 둘러보려면 데모를, 실제로 운영하려면 Docker Compose 설치를 따라가세요.

## 데모 실행

데모는 저장소에 포함된 예시 데이터만 사용합니다. 철도사 계정이나 외부 API가 필요하지 않습니다.

```powershell
cd apps/web
npm ci
$env:VITE_DEMO_MODE="true"
npm run dev
```

브라우저에서 `http://127.0.0.1:5173`을 엽니다.

## 설치 전 준비

다음 도구가 필요합니다.

- Git
- Docker Desktop 또는 Docker Engine
- Docker Compose v2
- 운영 스크립트를 사용할 경우 PowerShell 7

## 1. 저장소 내려받기

```powershell
git clone https://github.com/hostkimjang/korail-auto-waitlist.git
cd korail-auto-waitlist
Copy-Item .env.example .env
```

## 2. 필수 값 설정하기

새로 설치할 때 `.env`의 다음 다섯 값을 각각 다른 무작위 값으로 채웁니다. 아래 명령은 48바이트 난수를 Base64로 바꿔 각 값에 필요한 충분한 길이를 만듭니다.

| 이름 | 용도와 변경 시 주의사항 |
| --- | --- |
| `POSTGRES_PASSWORD` | PostgreSQL 접속 비밀번호입니다. 기존 DB 볼륨을 유지하면서 바꾸면 애플리케이션이 DB에 연결하지 못할 수 있습니다. |
| `SECRET_ENCRYPTION_KEY` | 알림 채널과 외부 credential을 암호화합니다. 잃어버리거나 바꾸면 기존 암호문을 복구할 수 없습니다. |
| `AUTH_SESSION_SECRET` | 관리자 세션을 서명합니다. 바꾸면 기존 로그인 세션이 무효화됩니다. |
| `KORAIL_BROWSER_ADAPTER_TOKEN` | API와 KORAIL 좌석 감시 sidecar 사이의 내부 인증값입니다. 철도사 계정 비밀번호가 아닙니다. |
| `SRT_PROVIDER_ADAPTER_TOKEN` | API와 SRT 좌석 감시 sidecar 사이의 내부 인증값입니다. 철도사 계정 비밀번호가 아닙니다. |

기존 설치를 업데이트하는 중이라면 빈 값만 새로 채우고, 이미 사용 중인 값은 그대로 유지하세요. DB 볼륨이나 암호화된 데이터가 남아 있는 상태에서 `POSTGRES_PASSWORD` 또는 `SECRET_ENCRYPTION_KEY`를 다시 생성하면 접속 또는 복호화에 문제가 생길 수 있습니다.

Linux Bash에서는 다음 블록을 한 번 실행합니다.

```bash
for name in \
  POSTGRES_PASSWORD \
  SECRET_ENCRYPTION_KEY \
  AUTH_SESSION_SECRET \
  KORAIL_BROWSER_ADAPTER_TOKEN \
  SRT_PROVIDER_ADAPTER_TOKEN
do
  printf '%s=%s\n' "$name" "$(openssl rand -base64 48)"
done
```

Windows PowerShell 5.1 또는 PowerShell 7에서는 다음 블록을 한 번 실행합니다.

```powershell
$names = @(
    'POSTGRES_PASSWORD'
    'SECRET_ENCRYPTION_KEY'
    'AUTH_SESSION_SECRET'
    'KORAIL_BROWSER_ADAPTER_TOKEN'
    'SRT_PROVIDER_ADAPTER_TOKEN'
)
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    foreach ($name in $names) {
        $bytes = New-Object byte[] 48
        $rng.GetBytes($bytes)
        '{0}={1}' -f $name, [Convert]::ToBase64String($bytes)
    }
}
finally {
    $rng.Dispose()
}
```

Windows PowerShell 5.1에는 `RandomNumberGenerator.GetBytes(48)` 정적 메서드가 없으므로, 위 명령은 호환되는 인스턴스 메서드를 사용합니다. 실행 결과로 나오는 `이름=값` 다섯 줄을 복사해 `.env`의 같은 이름을 가진 빈 줄을 통째로 교체하세요. 파일 끝에 같은 이름을 다시 추가하면 어떤 값이 적용되는지 혼동할 수 있습니다.

처음 관리자 계정을 만들 때만 아래 값을 `true`로 바꿉니다.

```dotenv
AUTH_INITIAL_REGISTRATION_ENABLED=true
```

명령이 출력한 값은 모두 비밀정보입니다. `.env`에만 보관하고 이슈, 로그, 채팅, 스크린샷에 붙여 넣지 마세요. 저장소에도 커밋하지 않습니다.

실제 KTX·SRT 시간표를 검색하려면 공공데이터포털에서 발급받은 일반 인증키를 `TAGO_SERVICE_KEY`에 설정해야 합니다. 키가 없어도 데모는 실행할 수 있지만 실제 시간표 검색은 사용할 수 없습니다.

## 3. 구성 확인하기

```powershell
docker compose -f compose.yml config --quiet
```

명령이 아무 내용 없이 끝나면 형식 검사가 통과한 것입니다. 비밀값이 출력될 수 있으므로 `--quiet`를 빼지 마세요. `.env.example`의 `COMPOSE_PROFILES=experimental-rail`이 KORAIL·SRT sidecar와 실험 worker를 기본 구성에 포함합니다.

## 4. 서비스 시작하기

```powershell
docker compose -f compose.yml up -d --build
docker compose -f compose.yml ps
```

브라우저에서 `http://127.0.0.1`을 엽니다. API·worker·scheduler와 KORAIL·SRT sidecar를 포함한 장기 실행 서비스가 모두 `healthy`로 바뀐 뒤 사용하세요. 좌석 조회·감시는 켜져 있지만 계정 기반 예매 시도 gate는 기본적으로 꺼져 있습니다.

## 5. 첫 관리자 계정 만들기

화면에서 관리자 계정을 만든 뒤 `.env`를 다시 열어 등록 기능을 닫습니다.

```dotenv
AUTH_INITIAL_REGISTRATION_ENABLED=false
```

API 서비스를 다시 만듭니다.

```powershell
docker compose -f compose.yml up -d --force-recreate api
```

레일웨잇은 한 명의 관리자만 사용하도록 설계되었습니다. 공개 회원가입 기능은 없습니다.

## 6. 다음 단계

- 화면 사용법: [사용 안내](USAGE.md)
- 다른 기기에서 접속하기: [설치·운영 가이드](OPERATIONS.md#다른-기기에서-접속하기)
- 알림 설정: [설치·운영 가이드](OPERATIONS.md#알림-채널)
- 백업과 복원: [설치·운영 가이드](OPERATIONS.md#백업과-복원)
- 실험 기능: [안전 원칙과 사용 범위](POLICY_AND_SAFETY.md#실험-기능)
