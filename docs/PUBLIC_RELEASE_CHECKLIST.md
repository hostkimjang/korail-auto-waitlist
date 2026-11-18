# 공개 릴리스 체크리스트

저장소 공개와 첫 공식 서명 릴리스에 필요한 확인 항목입니다. 실제로 마친 작업만 완료로 표시합니다.

## 현재 상태

저장소는 공개 상태입니다. README와 핵심 문서, 소개·데모 영상, MIT 라이선스, 공개 제외 자료의 현재 파일·Git 이력 정리는 마쳤습니다. 2026년 11월 18일 GitHub 설정을 확인했을 때 비공개 취약점 신고, 비밀값 검사와 푸시 차단, 기본 브랜치 보호가 모두 꺼져 있었으므로 이 세 가지를 먼저 켜야 합니다. 공식 서명 릴리스는 아직 게시하지 않았습니다.

## 공개 저장소에서 먼저 처리할 항목

- [x] MIT 라이선스를 루트 `LICENSE`와 패키지 메타데이터에 반영
- [x] `docs/research/`와 `docs/design/`을 현재 파일에서 제거
- [x] 공개에서 제외한 자료를 로컬·원격 브랜치와 태그의 전체 Git 이력에서 제거
- [ ] GitHub의 비공개 취약점 신고 기능을 켜고 `SECURITY.md`의 경로 확인 (2026-11-18 확인 때 꺼짐)
- [ ] GitHub 비밀값 검사와 푸시 차단 기능 활성화 (2026-11-18 확인 때 꺼짐)
- [ ] 기본 브랜치 보호 규칙 설정 (2026-11-18 확인 때 보호 없음, 저장소 ruleset은 비활성)
- [x] 새로 내려받은 저장소에서 설치와 첫 관리자 등록 절차 재현 (커밋 `fe4b364`, OCI Ubuntu 20.04 ARM64 clean clone)
- [ ] 네이티브 Ubuntu `linux/amd64` clean clone에서 데모와 설치 절차 재현
- [ ] 기본 브랜치의 전체 자동 검증 통과 확인

## 공개에서 제외한 자료

- 타사 화면과 조사 자료가 있던 `docs/research/`, `docs/design/`
- 로컬 캡처, 브라우저 인증 상태, HAR, 추적 파일, 로그와 테스트 출력

제외한 문서 경로는 현재 파일과 도달 가능한 Git 이력에서 제거했으며, 새 복제본에서도 남아 있지
않음을 확인했습니다.

## 완료한 정리

- [x] README를 프로젝트 소개, 핵심 기능, 영상, 설치, 안전 원칙 중심으로 재작성
- [x] 아키텍처·운영·안전 문서에서 날짜별 작업 기록과 중복 설명 제거
- [x] 누적 작업 일지였던 `CHECKLIST.md`를 현재 상태 중심으로 축약
- [x] 실제 계정이나 외부 요청을 쓰지 않는 소개 영상과 화면 데모 제작
- [x] `.env`, 비밀값, 백업, 로그, 브라우저 인증 상태가 Git에서 제외되는지 확인
- [x] 전체 Git 이력과 현재 공개 후보 파일을 Gitleaks로 검사하고 실제 비밀값 0건 확인
- [x] 기여 가이드와 보안 정책 작성
- [x] MIT 라이선스 추가

## 설치 확인

- [x] 공개할 파일만 골라 스테이징하고 변경 내용을 파일별로 검토
- [ ] README의 영상, 배지, 상대 링크를 GitHub 화면에서 확인
- [ ] `.env.example` 기반 설치에서 좌석 조회·감시와 `experimental-rail` 프로필이 활성화되고, 계정 기반 예매 시도는 꺼져 있는지 확인
- [x] 최초 관리자 등록 뒤 등록 기능이 닫히는지 확인 (커밋 `fe4b364`, OCI ARM64 clean clone)
- [ ] 로컬 전용 접속, HTTPS, 보안 쿠키와 허용 출처 설정 확인
- [x] 데이터베이스 준비 작업과 장기 서비스의 상태 확인 (커밋 `fe4b364`, OCI ARM64 clean clone)

## 첫 공식 서명 릴리스

공식 릴리스는 `main`에서 `Signed release publication` 워크플로를 수동 실행해 게시합니다. 워크플로가 `v<버전>` 태그와 GitHub Release를 직접 만듭니다. 설치 CLI는 최신 Release에서 서명된 `release-manifest.json`을 찾으므로, 서명 자산이 없는 Release를 손으로 만들면 설치와 업데이트 확인이 그 Release에서 멈춥니다. 태그나 Release를 손으로 만들지 않습니다.

- [ ] 서명용 Ed25519 private seed를 만들어 저장소 secret `RAILWAIT_RELEASE_SIGNING_KEY`에 등록하고 저장소 밖 안전한 곳에 따로 보관 (키를 바꾸면 기존 CLI가 새 서명을 거절함)
- [ ] 첫 실행 뒤 GHCR에 생기는 앱 이미지 패키지 4종을 공개로 설정 (비공개면 익명 조회 단계에서 멈춤)
- [ ] 첫 릴리스는 `compatible_from`을 비워 새 설치만 허용
- [ ] 정식 CLI로 새 설치를 확인한 뒤, 다음 릴리스를 `compatible_from`에 이전 버전을 넣어 게시하고 웹 **앱 업데이트** 버튼으로 업데이트 확인

## 공개 미디어

- [x] 소개 애니메이션에 비공식 프로젝트와 연출 화면임을 표시
- [x] README의 화면 데모를 브랜드 인트로, 자동 예매 선택, 좌석 발견, 예매 진행, 결제 전 중단까지 이어지는 전체 반복 GIF·MP4로 제공
- [x] 실제 화면 데모에 고정 예시 데이터만 사용
- [x] 영상에서 계정, 토큰, 쿠키, 알림 주소와 로컬 사용자 경로가 보이지 않는지 확인
- [x] README 이미지에 대체 텍스트와 MP4 링크 제공

## 확인 명령

비밀값이 출력되지 않는 명령만 사용합니다.

Linux Bash:

```bash
docker compose -f compose.yml config --quiet
bash ./scripts/ops.sh verify
git diff --check
git status --short
git ls-files
```

Windows PowerShell:

```powershell
docker compose -f compose.yml config --quiet
./scripts/ops.ps1 verify
git diff --check
git status --short
git ls-files
```

`docker compose config`의 평문 결과, `docker inspect`, 실제 서비스 추적 자료와 HAR는 공개 검증 자료에 포함하지 않습니다.
