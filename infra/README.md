# 컨테이너 운영 안내

## 최초 설정

1. `.env.example`을 `.env`로 복사하고 형식 안내에 따라 필수 비밀값과 두 sidecar 내부 token을 채웁니다. 실제 `.env`는 커밋하지 않습니다.
2. `docker compose -f compose.yml config --quiet`으로 설정을 검증합니다.
3. `docker compose -f compose.yml up -d --build`로 기본 서비스를 시작합니다.

기본 구성은 Caddy의 80/443만 호스트에 게시합니다. 앱·데이터·관제 서비스는 Compose 내부 네트워크에서만 통신합니다. 마이그레이션이 성공한 뒤 API, scheduler, worker가 시작되며 scheduler는 1개, worker는 concurrency 1로 고정합니다.

## Tailscale 우선 접속

서버 호스트에 Tailscale을 설치하고 `tailscale serve --bg http://127.0.0.1:80`으로 Caddy를 Tailnet에만 공개하는 구성을 권장합니다. JSON 정책이 필요한 환경은 `infra/tailscale/serve.json.example`을 참고하되 `${TS_CERT_DOMAIN}`을 실제 MagicDNS 이름으로 바꿉니다. Funnel은 활성화하지 않습니다.

공개 도메인을 사용할 때는 `.env`의 `SITE_ADDRESS`를 실제 FQDN으로 바꾸고 DNS, 방화벽, 포트 전달을 설정합니다. Caddy가 인증서 발급을 위해 80/443에 접근할 수 있어야 합니다.

## 선택 프로필

- `--profile experimental-rail`: KORAIL Chromium sidecar, SRT provider sidecar와 별도 experimental worker를 시작합니다. `.env.example`은 `COMPOSE_PROFILES=experimental-rail`과 운영사별 좌석 감시 gate를 기본 활성화합니다. 실제 due 감시 작업은 `rail` queue의 기본 worker가 소비하므로 Linux에서는 `bash scripts/ops.sh experimental`, Windows에서는 `scripts/ops.ps1 experimental`로 API·sidecar·기본 worker·experimental worker를 함께 재생성합니다. 예매 시도는 기본적으로 꺼진 별도 운영사 gate, 로그인 확인된 활성 계정과 작업별 `reserve_once_before_payment` 정책까지 모두 만족할 때만 가용성 에피소드당 최대 한 번 실행하고 결제 전에 멈춥니다. Chromium 이미지는 `linux/amd64`와 `linux/arm64` 빌드 대상을 지원합니다. ARM64 네이티브 호스트의 전체 서비스 health와 실제 KORAIL 읽기 조회는 별도 운영 검증 항목입니다.
- `--profile monitoring`: Prometheus와 Grafana를 실행합니다. Grafana는 `/ops/grafana/`에서 접근하며 자체 관리자 로그인이 필요합니다.
- `--profile ntfy`: 내부 알림용 ntfy를 실행합니다. 기본 접근 정책은 `deny-all`입니다.
- `--profile backup`: 암호화 백업 daemon을 실행합니다.

OCI ARM64 네이티브 검증에서는 KORAIL 이미지 빌드와 151개 고정 fixture, 전체 프로필 재배포, migration·log-init, 11개 장기 서비스 health와 sidecar `/readyz`를 통과했습니다. adapter에만 Playwright v1.55.0 seccomp 프로필과 `SYS_CHROOT` 하나를 적용하고 `pwuser`, 읽기 전용 루트, `cap_drop: ALL`, `no-new-privileges`를 유지합니다. `--no-sandbox`, `seccomp=unconfined`, `SYS_ADMIN`은 허용하지 않습니다. headless 조회 3회가 모두 HTTP 423이었던 반면 같은 이미지의 Pydoll non-headless 조회는 성공했습니다. 이에 기본 adapter를 host 포트나 noVNC listener가 없는 내부 Xvfb non-headless 모드로 배포했고, 실제 배포 sidecar HTTP 경계에서 서울→부산 열차 13개와 좌석 상태 판독을 확인했습니다.

운영 예시는 Linux의 `scripts/ops.sh`, Windows의 `scripts/ops.ps1` 또는 Linux `Makefile`을 사용합니다. Make 운영 target은 Bash 스크립트로 위임해 같은 drain·복원 안전 계약을 사용합니다. `docker compose up --scale scheduler=2`처럼 scheduler를 확장하면 중복 주기 작업이 발생하므로 금지합니다.

## 백업과 복원

`age-keygen`으로 키를 만들고 공개 수신자 키를 `.env`의 `BACKUP_AGE_RECIPIENT`에, 한 줄짜리 비밀키를 `BACKUP_AGE_IDENTITY`에 둡니다. 백업은 PostgreSQL custom dump를 만든 뒤 age로 암호화하며 평문 dump를 볼륨에 남기지 않습니다. 비밀 identity와 암호화 dump는 서로 다른 저장소에 보관합니다.

Linux 수동 백업은 `bash scripts/ops.sh backup`, 복원은 `bash scripts/ops.sh restore /backups/<파일>.dump.age`를 사용합니다. Windows에서는 같은 위치에서 `scripts/ops.ps1`을 사용합니다. 복원은 기존 데이터베이스 내용을 교체할 수 있으므로 `RESTORE_CONFIRM=RESTORE`가 없으면 실행되지 않습니다. 정기적으로 별도 테스트 인스턴스에서 실제 복원을 확인해야 합니다.
