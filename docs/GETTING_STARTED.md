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

`.env`에서 다음 다섯 값을 서로 다른 무작위 값으로 채웁니다.

- `POSTGRES_PASSWORD`
- `SECRET_ENCRYPTION_KEY`
- `AUTH_SESSION_SECRET`
- `KORAIL_BROWSER_ADAPTER_TOKEN`
- `SRT_PROVIDER_ADAPTER_TOKEN`

PowerShell에서는 다음 명령으로 무작위 값을 만들 수 있습니다.

```powershell
[Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(48))
```

위 명령을 다섯 번 실행해 서로 다른 값을 사용합니다. 두 adapter token은 `.env.example`에서 기본 활성화한 좌석 감시 sidecar의 내부 인증에만 사용하며 로그나 화면에 노출하지 않습니다.

처음 관리자 계정을 만들 때만 아래 값을 `true`로 바꿉니다.

```dotenv
AUTH_INITIAL_REGISTRATION_ENABLED=true
```

실제 비밀값은 `.env`에만 보관하세요. 이슈, 로그, 스크린샷에 붙여 넣지 마세요.

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
