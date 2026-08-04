# 백엔드 서비스 로그

Compose 실행 시 다음 서비스의 JSON Lines 로그가 서비스별 하위 디렉터리에 저장됩니다.

- `api/current.log`
- `worker/current.log`
- `scheduler/current.log`
- `experimental-rail/current.log`
- `korail-browser-adapter/current.log`

각 파일은 기본 5 MiB에서 회전하며 `.1`부터 `.4`까지 보존합니다. 컨테이너 stdout/stderr도
계속 유지되므로 실시간 확인은 `docker compose logs -f <service>`를 사용할 수 있습니다.
로그 파일과 회전 파일은 운영 데이터이며 Git에 포함되지 않습니다. 외부로 전달하기 전에는
관리자 ID, 역·열차 정보와 예상하지 못한 외부 라이브러리 메시지가 포함됐는지 다시 확인합니다.
