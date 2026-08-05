# KORAIL 브라우저 화면 진단

KORAIL Chromium 화면이 꼭 필요할 때만 로컬 noVNC 구성을 사용합니다. 이 화면은 읽기 전용이며
`127.0.0.1`에만 열립니다. 보호 응답을 우회하거나 원격 운영 화면으로 공개하는 용도가 아닙니다.

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

진단이 끝나면 기본 headless 구성으로 되돌립니다.

```powershell
docker compose --profile experimental-rail -f compose.yml up -d --force-recreate korail-browser-adapter
Remove-Item -LiteralPath secrets/korail-novnc-password.txt
```
