# 레일웨잇 문서

필요한 내용부터 바로 찾을 수 있도록 독자별로 나누었습니다. 설치 방법별 업데이트 방법은 [README의 업데이트](../README.md#업데이트)에 요약했습니다.

## 처음 사용하는 분

- [시작하기](GETTING_STARTED.md): Linux Bash·Windows PowerShell 데모 실행, Git·Docker Compose 설치, 첫 관리자 계정 만들기
- [사용 안내](USAGE.md): 열차 검색부터 알림, 공식 앱·홈페이지에서의 최종 확인까지의 흐름
- [안전 원칙과 사용 범위](POLICY_AND_SAFETY.md): 제공하는 기능과 제공하지 않는 기능
- [업데이트 노트](../CHANGELOG.md): 릴리스별 변경 내용, 버전마다 적용할 때 할 일, 화면 푸터에서 현재 버전을 확인하는 방법

## 직접 운영하는 분

- [설치·운영 가이드](OPERATIONS.md): 설치 방법별 업데이트, 안전한 재배포, 접속, 알림, 백업, 문제 해결
- [설치 CLI 안내](../apps/cli/README.md): `railwait` 명령, 로컬 패키지 설치와 업데이트, 보안·알림 키, Tailscale HTTPS
- [운영 장애 기록](INCIDENTS.md): 실제 발생한 장애의 원인, 조치, 검증 근거와 재발 감지 방법
- [공지 팝업 대응 검증 기록](NOTICE_ADAPTATION_VERIFICATION.md): 코레일 공지 구조 변경에 대응하는 판정 기준과 검증 범위
- [Android 공식 앱 인계 검증](ANDROID_APP_HANDOFF_QA.md): 코레일+ 딥링크와 SRT 웹 인계의 ADB·실기기 QA 절차
- [KORAIL 브라우저 화면 진단](../apps/api/KORAIL_BROWSER_DIAGNOSTICS.md): 짧은 화면 진단과 앱 전용 Chrome의 직접 로그인 인계
- [환경 설정 예시](../.env.example): 필수 값과 선택 기능의 설정 형식
- [컨테이너 구성 안내](../infra/README.md): Compose 프로필과 인프라 개요

## 개발에 참여하는 분

- [기여 가이드](../CONTRIBUTING.md): 개발 환경, 검증, 릴리스 버전 올리기와 Pull Request 절차
- [코드 작성 규칙](CODE_CONVENTIONS.md): TypeScript·Python·테스트 기준
- [시스템 구조](ARCHITECTURE.md): 서비스 구성과 데이터 흐름
- [CLI 설치와 웹 업데이트 설계](INSTALL_UPDATE_DESIGN.md): 설치 CLI, 서명 릴리스, 업데이트 작업과 실패 복구의 설계·구현 상태와 남은 검증
- [릴리스 도구 안내](../scripts/releases/README.md): 서명 배포 번들과 로컬 설치 패키지 생성, 공식 릴리스 워크플로
- [API 개발 안내](../apps/api/README.md): FastAPI 백엔드의 구성과 개발 방법
- [최근 이슈 분석과 개발 계획](DEVELOPMENT_PLAN.md): 채팅 인계, 수정·배포 결과, 현재 보호 경계와 안전한 재개 조건
- [공개 릴리스 체크리스트](PUBLIC_RELEASE_CHECKLIST.md): 공개 저장소 설정과 첫 공식 서명 릴리스 전에 확인할 항목
- [보안 정책](../SECURITY.md): 취약점 비공개 신고 방법

## 보관 문서

- [클린 구조 리팩터링 계획](REFACTORING_PLAN.md): 리팩터링 과정과 단계별 검증 기록
- [웹 TypeScript·모듈 분리 계획](WEB_TYPESCRIPT_MIGRATION.md): TypeScript 전환 과정의 작업 기록
