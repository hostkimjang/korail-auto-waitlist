# Android 공식 앱 인계 검증

이 문서는 설치형 PWA에서 코레일+와 SRT를 여는 외부 경로의 검증 결과와 재현 절차를 설명합니다. 앱 설치 여부, Android resolver 성공, 실제 도착 화면, 브라우저·PWA 동작을 서로 다른 증거로 관리합니다.

## 현재 제품 계약

| 운영사·상태 | 기본 동작 | 검증 뒤 선택 가능한 앱 인계 |
| --- | --- | --- |
| KORAIL 좌석 확인·예매 | 코레일 예매 웹을 새 창으로 열기 | `korailtalk://navigation?view=booking` |
| KORAIL 결제 필요·예약 확인 | 코레일 예약목록 웹을 새 창으로 열기 | `korailtalk://navigation?view=bookedTicket` |
| SRT 좌석 확인·예매 | SRT 예매 웹을 새 창으로 열기 | `srapp://main` |
| SRT 결제 필요·예약 확인 | SRT 발권·취소 조회 웹을 새 창으로 열기 | `srapp://main` + 고정 문자열 extra `btnNo=2` |

각 앱 경로는 독립 기능 플래그와 검증 버전이 모두 있어야 생성됩니다. 기본값은 모두 꺼져 있습니다. HTTPS와 `intent://`는 사용자 클릭 안에서 `target="_blank"` 외부 문맥으로 열어 레일웨잇 PWA 화면을 보존합니다. 앱이 없으면 intent의 고정 HTTPS fallback이 닫기 버튼이 있는 브라우저 Custom Tab에서 열립니다. 타이머로 설치 여부를 추측하거나 PWA 화면을 공식 홈페이지로 바꾸는 JavaScript fallback은 사용하지 않습니다.

고정 HTTPS 진입점:

- KORAIL 예매: `https://www.korail.com/ticket/search/general`
- KORAIL 예약목록: `https://www.korail.com/ticket/reservation/list`
- SRT 예매: `https://etk.srail.kr/hpg/hra/01/selectScheduleList.do?pageId=TK0101010000`
- SRT 발권·취소 조회: `https://etk.srail.kr/hpg/hra/02/selectReservationList.do?pageId=TK0102010000`

비로그인 요청에서 코레일 예약목록은 정상 응답하고, SRT 발권·취소 조회는 조회 경로를 `goUrl`로 보존한 공식 로그인 화면으로 이동합니다.

## 2026년 8월 7일 에뮬레이터 검증 결과

검증 환경:

- Android Studio `2026.1.3.7`
- Platform Tools `37.0.1`, Emulator `37.1.11`
- Android 16 / API 36 Google Play x86_64
- Pixel 9 Pro Fold 프로필 AVD `railwait_play_api36`, serial `emulator-5554`
- 코레일+ `7.0.0` / versionCode `70000006`
- SRT `2.0.41` / versionCode `150`

확인된 사실:

- 코레일+ base APK의 exported `DeepLinkActivity`에 `korailtalk` scheme, `navigation` host, `VIEW`·`DEFAULT`·`BROWSABLE` 필터가 있습니다.
- `view=booking`, `view=ticket`, `view=bookedTicket`은 모두 `com.korail.talk/.DeepLinkActivity`로 resolve됩니다.
- `view=booking` 콜드 실행은 출발역·도착역·날짜·인원·좌석과 `간편 예매`, `열차 조회`가 보이는 코레일+ 예매 화면으로 이동했습니다.
- `view=ticket`은 하단 `나의 티켓`의 `MyTicketListRoute`로 이동하므로 결제 직전 예약 확인 목적지로 사용하지 않습니다.
- `view=bookedTicket`은 전체메뉴의 `예약 승차권 조회 · 취소`와 같은 `MyTicketReservationRoute`로 이동합니다.
- SRT base APK의 exported `SRMainActivity`에 `srapp` scheme, `main` host, `VIEW`·`DEFAULT`·`BROWSABLE` 필터가 있습니다.
- `srapp://main`은 `kr.co.srail.newapp/.main.SRMainActivity`로 resolve되고, 콜드 실행 뒤 SRT 승차권 예매 홈으로 이동했습니다.
- 설치 APK의 `SRMainActivity`는 외부 intent의 extras를 `SRWebActivity`로 전달합니다. `SRWebActivity.X0()`는 문자열 extra `btnNo`가 `2`이면 `https://app.srail.or.kr/atc/selectListAtc14017_n.do`를 초기 URL로 선택합니다.
- `srapp://main`에 문자열 extra `btnNo=2`를 넣어 콜드 실행하자 `SRWebActivity`의 **승차권 확인** 화면으로 이동했고, 비로그인 상태에서는 `로그인이 필요합니다.` 안내가 나타났습니다. 별도 추측 scheme이나 사용자 데이터는 사용하지 않았습니다.
- SRT 앱은 `etk.srail.kr` HTTPS의 App Link handler가 아니며, 매니페스트에 승차권 확인용 별도 BROWSABLE URI는 없습니다. 검증된 `srapp://main`과 앱이 명시적으로 읽는 고정 extra만 조합합니다.
- Chrome 설치형 PWA의 실제 터치에서 `target="_blank"` 코레일 booking intent는 코레일+ 예매 입력 화면을, SRT ticket intent는 `승차권 확인`과 비로그인 안내를 열었습니다. 버튼 callback이 임시 anchor를 만들어 동기적으로 클릭하는 결제·예약목록 경로에서도 SRT ticket extra가 유지됐습니다.
- 최종 Compose 배포 뒤 레일웨잇 `내 예약`의 실제 `공식 확인 열기` 버튼을 터치해도 SRT `승차권 확인`과 비로그인 안내로 이동했습니다. 개발자 도구에 임시로 만든 QA 링크가 아니라 제품의 `ReservationsPage` → `launchOfficialOpenTarget` 경로까지 확인한 결과입니다.
- 설치되지 않은 시험용 package를 지정한 동일 intent를 PWA에서 누르자 SRT 발권·취소 조회 로그인 화면이 Chrome Custom Tab으로 열렸고, 닫기 뒤 기존 레일웨잇 PWA가 그대로 남았습니다. 같은 intent를 `target="_blank"` 없이 열면 공식 웹이 PWA를 교체하는 실패도 재현해 현재 창 탐색을 제거했습니다. 이 시험은 실제 철도 앱을 제거하지 않은 resolver 실패 모의 검증입니다.
- SRT `srapp://main`은 콜드 실행에서 예매 홈으로 이동하지만, 이미 `SRWebActivity`의 승차권 확인이 떠 있는 웜 실행에서는 기존 화면을 유지했습니다. `btnNo=1`을 추가해도 `onNewIntent`가 이를 다시 적용하지 않아 예매 홈을 보장하지 못했습니다. 반대로 예매 홈이 열린 상태에서 ticket intent의 `btnNo=2`는 승차권 확인으로 이동했습니다.
- SRT 2.0.41에서 이미 `승차권 확인`이 최상단인 웜 상태에 같은 ticket intent를 다시 전달하면 Android는 새 Activity를 시작하지 않았고(`TotalTime: 0`), SRT WebView에도 새 URL load가 발생하지 않았습니다. 하단 `승차권 확인`(`quick02`)을 다시 누른 경우에만 앱 내부 조회 navigation이 실행됐습니다. 사용자 제공 실기기 화면에서도 app link 직후 비어 있던 방금 예약이 이 재선택 뒤 표시됐습니다. 따라서 ticket intent는 목적 화면 이동까지만 보장하고 목록 갱신은 보장하지 않으며, 제품은 결제 카드에 하단 탭 재선택 안내를 표시합니다. 공개 BROWSABLE refresh 계약이 없으므로 지연된 두 번째 intent나 앱 내부 좌표 클릭을 만들지 않습니다.

아직 확인할 사실:

- 실제 결제 직전 예약이 있는 상태에서 `view=bookedTicket` 첫 화면에 해당 예약이 바로 표시되는지
- SRT 앱 업데이트마다 로그인 상태의 ticket intent 첫 표시와 하단 `승차권 확인` 재선택 뒤 방금 예약 목록 갱신 차이가 유지되는지
- Chrome 일반 탭의 실제 사용자 클릭에서 각 intent와 외부 fallback이 같은 방식으로 동작하는지
- 삼성 인터넷 일반 탭과 설치형 PWA에서 같은 동작을 하는지
- 실제 코레일+·SRT 앱을 제거한 상태에서도 오류 intent 주소 없이 공식 HTTPS가 외부 창으로 열리는지
- 갤럭시 폴드7에서 뒤로가기와 로그인 만료 흐름이 안전한지

Samsung Internet은 Google Play에서 이 x86_64 AVD에 설치를 시도했지만 호환 설치가 실패했습니다. 따라서 삼성 인터넷 행은 에뮬레이터 성공으로 대체하지 않고 갤럭시 폴드7 실기기 확인으로 남깁니다.

## 2026년 8월 8일 일반 Chrome 결제 버튼 회귀 확인

갤럭시 폴드7의 일반 Chrome 탭에서 `공식 결제 열기`를 누른 뒤 탭 수가 하나 늘고 코레일 웹 로그인으로 이동한 실패를 확인했습니다. 실패 당시 배포 번들에는 검증 버전 값이 컴파일되지 않았고, 로컬 `.env`에도 여덟 개 딥링크 설정이 없었습니다. 따라서 브라우저가 intent를 거절한 것이 아니라 `resolveOfficialOpenTarget`이 처음부터 고정 HTTPS를 선택한 경우입니다.

로컬 QA 환경의 `.env`에 코레일 booking·ticket과 SRT main·ticket의 enabled·검증 버전을 기록하고 웹 이미지를 다시 만든 뒤 다음을 확인했습니다.

- Compose의 최종 web build args 여덟 개가 모두 기대값으로 해석됩니다.
- 배포 JavaScript의 런타임 설정에 코레일+ `7.0.0+70000006`, SRT `2.0.41+150`과 네 개 enabled 값이 포함됩니다.
- Android 16/API 36 일반 Chrome 탭의 실제 SRT `공식 결제 열기` 버튼은 새 웹 탭을 만들지 않고 SRT `승차권 확인`으로 전환했습니다.
- 같은 일반 Chrome 탭에서 당시 코레일 `view=ticket` intent를 사용자 입력 이벤트로 누르면 `com.korail.talk/.MainActivity`로 전환했습니다. 이 검증은 앱 전환만 확인했으며, 이후 `나의 티켓`이 잘못된 목적지임을 확인해 `view=bookedTicket`으로 교체했습니다.

일회성 셸 환경변수로만 QA 이미지를 만들면 다음 Compose build에서 기본값 `false`로 돌아갈 수 있습니다. 현재 장비에서 계속 검증할 값은 `.env`에 기록하고, 빌드 전 effective build args와 빌드 뒤 번들의 검증 버전을 모두 확인합니다. 이미 열려 있던 브라우저 탭은 이전 JavaScript를 계속 실행하므로 재배포 뒤 새로고침하거나 닫고 다시 열어야 합니다.

## 2026년 8월 8일 코레일 예약목록 목적지 보정

결제 인계가 하단 `나의 티켓`이 아니라 `전체메뉴 → 예약 승차권 조회 · 취소`로 가야 한다는 실기기 피드백을 기준으로 코레일+ 7.0.0 APK와 두 화면을 다시 대조했습니다.

- 전체메뉴의 `예약 승차권 조회 · 취소`를 직접 누르면 `MyTicketReservationRoute`로 이동합니다.
- 코레일+의 `AppViewModel` 딥링크 분기에서 `view=ticket`은 `MyTicketListRoute`, 별도 값은 `MyTicketReservationRoute`로 매핑됩니다.
- 별도 Google APIs API 36 AVD에서 같은 코레일+ APK를 런타임 계측해 보호된 분기 문자열이 `bookedTicket`이고 Java hash가 분기 키 `-1599049100`과 일치함을 확인했습니다.
- Google Play API 36 AVD에서 `korailtalk://navigation?view=bookedTicket`을 콜드 실행하면 `DeepLinkActivity`를 거쳐 `MainActivity`로 이동하고, 화면에 `예약 승차권 조회 · 취소`, `예약된 승차권이 없어요`, `열차 조회`가 표시됐습니다.
- 같은 AVD의 일반 Chrome 탭과 설치형 WebAPK에서 제품과 동일한 `target="_blank"` intent anchor를 물리 입력으로 각각 눌렀을 때도 코레일+의 `예약 승차권 조회 · 취소` 화면으로 전환됐습니다. 이는 브라우저·PWA의 외부 앱 전환과 목적 URI를 검증한 것이며, 실제 KORAIL 결제 카드 버튼과 결제 직전 예약 표시 여부는 별도 확인 항목입니다.
- APK 리소스에 남은 `view=ticketRefund`는 같은 AVD에서 홈에 머물렀으므로 사용하지 않습니다.

따라서 제품의 KORAIL `ticket` 목적지는 외부 URI의 이름과 별개로 `view=bookedTicket`을 생성합니다. `view=ticket`과 `view=ticketRefund`로 되돌리지 않습니다.

## ADB 검증 절차

연결 상태를 확인합니다.

```powershell
adb devices -l
```

코레일+ 두 경로의 resolver를 확인합니다.

```powershell
./scripts/verify-android-rail-deeplinks.ps1 -DeviceSerial DEVICE_SERIAL
```

화면을 한 경로씩 실행합니다.

```powershell
./scripts/verify-android-rail-deeplinks.ps1 -DeviceSerial DEVICE_SERIAL -Destination booking -Launch
./scripts/verify-android-rail-deeplinks.ps1 -DeviceSerial DEVICE_SERIAL -Destination ticket -Launch
```

SRT 예매 홈과 승차권 확인 화면을 각각 확인합니다.

```powershell
./scripts/verify-android-rail-deeplinks.ps1 -DeviceSerial DEVICE_SERIAL -Provider SRT
./scripts/verify-android-rail-deeplinks.ps1 -DeviceSerial DEVICE_SERIAL -Provider SRT -Destination main -Launch
./scripts/verify-android-rail-deeplinks.ps1 -DeviceSerial DEVICE_SERIAL -Provider SRT -Destination ticket -Launch
```

`Status: ok`와 대상 package Activity만으로 목적 화면을 합격시키지 않습니다. 코레일 booking은 예매 입력 화면, ticket은 `예약 승차권 조회 · 취소` 제목과 예약목록, SRT main은 승차권 예매 홈, SRT ticket은 `승차권 확인` 제목과 로그인 또는 목록 화면을 사람이 확인해야 합니다.

## 브라우저·PWA 합격 행렬

| 환경 | 조건 | 기대 결과 | 현재 상태 |
| --- | --- | --- | --- |
| Chrome 설치형 PWA | 코레일+ 설치 | 사용자 클릭으로 코레일 예매 화면 열기 | 확인 |
| Chrome 설치형 PWA | 코레일+ 설치 | `bookedTicket` 클릭으로 `예약 승차권 조회 · 취소` 열기 | API 36 WebAPK의 동일 anchor 확인, 실제 KORAIL 결제 카드 버튼 재검증 필요 |
| Chrome 설치형 PWA | SRT 설치 | ticket 클릭으로 `승차권 확인` 열기 | 확인 |
| Chrome 설치형 PWA | SRT 설치 | booking 클릭으로 예매 홈 열기 | 콜드 확인, 웜 실행 목적 화면 미보장 |
| Chrome 설치형 PWA | 존재하지 않는 시험 package | 공식 HTTPS Custom Tab과 레일웨잇 화면 보존 | 확인 |
| Chrome 일반 탭 | 코레일+·SRT 설치 | 사용자 클릭으로 각 ticket 앱 화면 열기 | SRT 실제 버튼과 코레일 `bookedTicket` 동일 anchor 확인, 실제 KORAIL 결제 카드 버튼 재검증 필요 |
| Chrome 일반 탭 | 각 앱 미설치 | 고정 공식 HTTPS fallback | 미확인 |
| 삼성 인터넷 일반 탭·PWA | 각 앱 설치·미설치 | Chrome과 같은 목적 화면과 외부 fallback | 미확인 |
| Chrome·삼성 인터넷 | 실제 대상 앱 미설치 | 오류 intent 주소 없이 고정 공식 HTTPS 외부 창 | 미확인 |

브라우저 한 종류의 성공을 다른 브라우저의 성공으로 대신 기록하지 않습니다. 한 행이라도 현재 구현과 다르면 해당 경로의 플래그를 켜지 않습니다.

## 기능 플래그

경로별 검증을 모두 통과한 값만 `.env`에 기록합니다.

```dotenv
VITE_KORAIL_BOOKING_DEEPLINK_ENABLED=true
VITE_KORAIL_BOOKING_VALIDATED_VERSION=7.0.0+70000006
VITE_KORAIL_TICKET_DEEPLINK_ENABLED=false
VITE_KORAIL_TICKET_VALIDATED_VERSION=
VITE_SRT_MAIN_DEEPLINK_ENABLED=false
VITE_SRT_MAIN_VALIDATED_VERSION=
VITE_SRT_TICKET_DEEPLINK_ENABLED=true
VITE_SRT_TICKET_VALIDATED_VERSION=2.0.41+150
```

위 예시는 목적 화면과 외부 fallback 검증 상태를 구분한 보수적 후보입니다. 코레일 ticket은 `bookedTicket` 제품 버튼, SRT main은 웜 실행 목적 화면, 삼성 인터넷 행렬을 통과하기 전까지 문서 예시에서 꺼 둡니다. Vite 빌드 입력이므로 값을 바꾸면 전체 이미지를 다시 만듭니다.

```powershell
docker compose config --quiet
docker compose build
docker compose up -d --force-recreate
```

PWA는 설치 앱 버전을 감지할 수 없습니다. 앱 업데이트가 확인되면 해당 경로 플래그를 먼저 끄고 새 버전에서 같은 절차를 반복합니다.

Android의 웹→앱 연결 원리는 [Android 딥링크 안내](https://developer.android.com/training/app-links/deep-linking)와 [Chrome Android intent URI 안내](https://developer.chrome.com/docs/android/intents/)를 참고합니다.
