# 기존 secret 파일에서 `.env`로 이전

Docker secret 파일 방식은 더 이상 사용하지 않습니다. 새 설치는 저장소 루트의 `.env.example`을 `.env`로 복사하고 그 안의 형식·생성 안내를 따릅니다. 기존 파일이 모두 남아 있으면 저장소 루트에서 `./scripts/migrate-secrets-to-env.ps1`을 실행해 값 노출 없이 이전할 수 있습니다.

기존 설치는 파일 내용을 로그에 출력하지 말고 다음 대응 관계로 한 번만 이전합니다.

- `postgres_password.txt` → `POSTGRES_PASSWORD`
- `rail_credential_key.txt` → `SECRET_ENCRYPTION_KEY`
- `auth_session_key.txt` → `AUTH_SESSION_SECRET`
- `auth_bootstrap_token.txt` → 현재 런타임에서 사용하지 않음. 값을 `.env`로 옮기지 말고 기존 secret 폐기 절차를 따름
- `tago_service_key.txt` → `TAGO_SERVICE_KEY`
- `webpush_vapid_private_key.txt` → `WEBPUSH_VAPID_PRIVATE_KEY`
- `webpush_vapid_public_key.txt` → `WEBPUSH_VAPID_PUBLIC_KEY`
- `grafana_admin_password.txt` → `GRAFANA_ADMIN_PASSWORD`
- `backup_age_identity.txt` → `BACKUP_AGE_IDENTITY`

`app_secret_key.txt`는 런타임에서 사용되지 않았으므로 이전하지 않습니다. multiline PEM 형식의 기존 VAPID private key는 `.env`의 double-quoted 값 안에서 줄바꿈을 `\n`으로 바꾸거나, 한 줄짜리 URL-safe Base64 VAPID key pair를 새로 생성합니다. 이전 확인 후 기존 `secrets/`는 안전하게 별도 보관한 다음 삭제할 수 있습니다.
