# 릴리스 번들 생성

이 도구는 `compose.yml`과 참조한 infra 파일, `.env.example`만 읽어 설치용 묶음을 만든다.
운영 `.env`와 Docker 실행 상태는 읽지 않는다. YAML alias는 PyYAML의 안전한 loader로 병합한 뒤
해제하며, 개발 빌드 설정과 `test` 프로필 서비스는 배포 Compose에서 제거한다.
나머지 서비스·프로필·환경변수·health·named volume·network·보안 옵션을 보존한다.
관리형 API·worker·sidecar에는 유지보수 control·gate bind를 추가하며 API만 auth·status 읽기와
requests 쓰기 mount를 받는다. Docker socket은 넣지 않는다. 로그 bind는 `${RAILWAIT_INSTALL_DIR:?RAILWAIT_INSTALL_DIR must be set}/logs`로 바꾼다.

Python 3.12 환경에서 `requirements.txt`를 설치한 뒤 다음으로 로컬 계약을 검증한다.

```console
python -m unittest discover -s scripts/releases -v
```

테스트는 고정 fixture와 임시 디렉터리만 사용한다. Docker가 설치돼 있으면 임시 설정으로 모든
운영 프로필의 `docker compose config --quiet`도 확인한다. 컨테이너를 시작하지 않는다.

`build_bundle.py --list-images`는 pin이 필요한 원본 이미지 목록을 JSON으로 반환한다.
`--images-json`에는 원본 image reference를 키로 하고 `image@sha256:<64자리>`를 값으로 하는
객체를 전달한다. 같은 API 이미지를 쓰는 worker·migration·sidecar는 같은 pin을 공유한다.
`resolve_images.py`는 게시된 앱 이미지와 upstream registry index에 `linux/amd64`·`linux/arm64`가
모두 존재하는지 확인하고 해당 map을 만든다. 태그만 있는 값이나 누락된 이미지로는 번들을 만들지 않는다.

서명키는 base64로 인코딩한 Ed25519 32바이트 private seed다. `RAILWAIT_RELEASE_SIGNING_KEY`
환경변수 또는 접근이 제한된 파일의 경로 `--signing-key-file`로 전달한다. 키를 명령행 인자 값이나
로그에 넣지 않는다. `--public-key-output`은 CLI에 넣을 base64 공개키만 별도 파일로 저장한다.
출력 디렉터리는 새 경로여야 하며, 다음 세 파일을 생성한다.

- `railwait-bundle.tar.gz`: 결정적인 gzip/tar, 일반 파일만 포함, 각 파일 8MiB·전체 32MiB·256개 이하
- `release-manifest.json`: schema 1, stable version·source revision·양쪽 Linux CPU·최소 CLI 버전,
  번들 URL·SHA-256·크기, DB revision·설정 schema, 승인한 출발 버전, 서비스별 image pin·profile
- `release-manifest.sig`: JSON 원본 bytes를 서명한 Ed25519 signature의 base64

`.github/workflows/release.yml`은 `main`에서 수동 실행한다. 입력 버전은 CHANGELOG 최상단과
같아야 한다. 전체 API·웹·브라우저 및 3개 OS의 CLI 검증이 통과한 뒤 4개 앱 이미지를 빌드하고,
익명 registry 조회로 양쪽 CPU 지원을 검증한다. 서명한 번들과 키가 포함된 CLI 6종을 준비하고,
두 Linux CPU에서 게시한 동일 digest의 실행 smoke가 통과한 뒤 draft Release에 자산을 모두 올리고
stable로 공개한다. smoke는 import·실행파일·웹 정적 파일 검증이며 실제 운영사 인증·예약 검증은 아니다.
브라우저 이미지의 QEMU 빌드는 자원이 많이 들 수 있어 120분 제한을 둔다.

저장소 secret `RAILWAIT_RELEASE_SIGNING_KEY`와 GHCR package의 공개 접근 설정이 필요하다.
처음 만들어지는 package가 비공개이면 익명 조회 단계에서 멈추며, 공개 접근을 설정하기 전에는
안정 릴리스를 게시하지 않는다. 이 파일의 추가나 로컬 테스트는 실제 이미지 게시·릴리스 공개를 뜻하지 않는다.

현재 서명 공개키는 2026-11-18에 만든 `qeR7RDhD9a1lF0YVpMX5PbiHmrVU+nm7Fstt9UbB5nM=`이고, 개인 seed는 저장소
secret과 관리자의 오프라인 백업에만 있다. GHCR의 `api`·`web`·`browser`·`backup` 네 패키지는 첫 게시 직후부터 익명
pull 토큰으로 `tags/list` 200을 돌려줘 따로 공개로 바꿀 필요가 없었다. `gh` 토큰에 packages 권한이 없어 패키지
공개 설정 값 자체는 API로 확인하지 않았다.
verify 작업은 repository-verify와 같은 ubuntu-24.04에서 돈다. ubuntu-latest가 고르는 Ubuntu 26.04에는 API의
Playwright 1.55가 Chromium 의존성을 설치하지 못한다. 병렬 테스트 전에 Chrome을 한 번 띄워, 첫 시작이 Pydoll의
시작 대기 시간을 넘기지 않게 한다.

`--compatible-from`을 반복 지정하거나 workflow의 `compatible_from`에 쉼표로 구분한 버전을 입력한다.
비워 두면 새 설치만 허용하고 기존 설치의 자동 업데이트는 차단한다. DB revision은 소스 migration의
단일 head에서 계산한다. 현재 updater는 다른 DB revision이나 PostgreSQL·Redis digest 변경을 차단하므로,
해당 변경은 별도의 검토·이전 절차가 필요하다. 배포 검증키가 바뀌면 기존 CLI가 새 서명을 거절하며
CLI 자체 자동 교체는 제공하지 않는다.

API·browser 이미지는 `uv.lock`을 `uv sync --locked`로 설치하고 uv 도구 이미지도 digest로 고정한다.
Python base·OS apt package·Chrome `stable_current` 다운로드까지 빌드 재현성이 완성된 것은 아니다.
릴리스 실행 시에는 검증 완료한 최종 이미지 digest를 고정하며 이 둘을 구분한다.

## 로컬 오프라인 설치 패키지

`build_local_package.py`는 **이미 로컬 Docker에 존재하는 이미지**로 독립 설치용 패키지를 만든다.
공식 stable 릴리스가 아니며 registry에 게시하지 않는다. 이미지를 빌드·pull·tag하지 않고 운영
컨테이너·볼륨·기존 이미지 태그도 변경하지 않는다. `.env`를 읽거나 기존 설치의 비밀값을 복사하지 않는다.
기본 서비스뿐 아니라 `experimental-rail`, 모니터링, 알림, 백업 등 모든 운영 프로필의 이미지가
선택한 `linux/amd64` 또는 `linux/arm64`에 맞게 준비돼 있어야 한다. 누락되면 저장 전에 중단한다.

공식 릴리스 키와 별개의 일회성 Ed25519 private seed 파일을 사용한다. 파일 내용은 임의 32바이트의
base64이며 저장소 밖에 `0600` 또는 Windows의 사용자 전용 ACL로 보호한다. 비밀값을 명령행 인자나
문서에 넣지 않는다. `--public-key-output`의 base64 공개키 파일은 사용자가 신뢰 경로를 확인해
CLI의 `--trust-key`로 명시적으로 선택한다. 패키지 안에 든 공개키를 자동으로 신뢰하지 않는다.

```console
python scripts/releases/build_local_package.py \
  --docker-context <로컬-context> --platform linux/arm64 --version 1.4.0 \
  --output <새-패키지-디렉터리> --signing-key-file <보호된-로컬-private-seed-파일> \
  --public-key-output <새-공개키-파일> --images-json <이미지-선택-JSON>
```

`--images-json`은 선택 항목이다. 키는 원본 Compose 이미지이고 값은 준비한 이미지 reference다.
예를 들어 `{"rail-waitlist-api:local":"railwait-local-install-api:20261117"}`로 새 고유 태그의
API를 선택한다. 같은 원본 API를 쓰는 worker·migration·SRT sidecar도 같은 이미지를 사용한다.
새 코드를 검증하려면 기존 `:local`을 덮어쓰지 않고 작업 전용 이름으로 별도 빌드한 뒤 선택한다.
출력 경로와 공개키 경로는 기존 파일을 덮어쓰지 않는다. 실패하면 이번 실행이 만든 불완전 패키지만 정리한다.
로컬 패키지 설치를 저장소 커밋으로 업데이트할 때는 `scripts/update-local-install.sh`가 이 생성기를 호출한다.
커밋별 임시 worktree에서 작업 전용 태그로 빌드한 이미지, 일회성 서명키, 임시 출력 경로를 쓰고 적용 뒤 정리한다.
Docker 29의 containerd 이미지 저장소는 같은 플랫폼 매니페스트를 공유하는 다른 이미지가 있으면 그 이미지의 provenance
증명을 `docker image save` 결과에 referrer 최상위 항목으로 덧붙인다. 생성기는 서명할 이미지 외의 최상위 항목을
거절하므로, 레이어가 모두 캐시인 재빌드에는 실행마다 다른 라벨을 붙여 매니페스트를 고유하게 만든다. 실패하면 생성기가
예외 종류와 메시지를 출력한다. 개인키는 파일로만 읽으므로 메시지나 명령 인자에 들어가지 않는다.

패키지는 다음 네 파일로 구성된다.

- `local-manifest.json`: `schema_version: 1`, `kind: local_package`, 버전·DB revision·설정 schema,
  한 개 Linux CPU, 번들·이미지 archive의 SHA-256·크기, 서비스별 이미지 ID·config ID·프로필
- `local-manifest.sig`: manifest 원본 bytes를 서명한 Ed25519 signature의 base64
- `railwait-bundle.tar.gz`: 기존 배포 번들과 같은 파일·bind·유지보수 정책. `build` 없이
  `image: sha256:<ID>`와 `pull_policy: never`로 실행해 registry 다운로드를 막는다.
- `railwait-images.tar`: 중복 제거한 이미지 ID만 `docker image save`한 archive. 운영 태그를 담지 않는다.

`images`는 Docker가 실제 실행하는 immutable ID다. classic Docker에서는 config digest이고
containerd 저장소에서는 OCI index·manifest digest일 수 있다. `image_config_ids`는 선택한 CPU의
실제 config JSON SHA-256이다. 생성기는 Docker archive의 `manifest.json`과 OCI index가 있으면
root descriptor → 선택 CPU manifest → config digest를 대조하고, 각 metadata blob의 hash도 확인한다.
이미지 누락·추가, 다른 CPU, 이름·태그를 가져오는 annotation, 링크·위험 경로는 서명 전에 거절한다.
CLI도 같은 검증과 전체 archive checksum을 통과한 뒤 `docker image load`해야 한다.

`source_revision`은 패키징 체크아웃의 HEAD이며, `source_dirty`는 현재 수정 여부다.
`source_tree_sha256`은 추적 파일과 Git ignore 대상이 아닌 미추적 파일의 현재 bytes를 경로와 함께
계산한 지문이다. `.env*`(템플릿 `.env.example` 제외), 명시한 서명 입력·공개키 출력과 패키지 출력은
제외한다. 심볼릭 링크는 대상 문자열만 기록하고 대상 파일은 읽지 않는다. 이 지문은 **패키징 시점
소스의 식별**이며, 미리 만들어 둔 Docker 이미지가 해당 소스에서 빌드됐다는 증명이 아니다.
실제 이미지의 출처·현재 소스 일치는 별도 확인해야 하며 이미지 ID만 보고 현재 dirty 작업의 결과로
표시하면 안 된다. 로컬 패키지 설치가 공식 stable 업데이트나 다른 CPU 지원 검증을 대신하지 않는다.
