# KORAIL Browser Companion

사용자가 KORAIL 공식 승차권 검색 결과를 직접 열고 검색을 마친 뒤, popup의 **현재 결과
가져오기**를 한 번 눌렀을 때만 현재 보이는 결과 목록을 읽는 Manifest V3 확장 프로그램입니다.

이 도구는 검색·로그인·새로고침·폴링·예약을 하지 않습니다. `MutationObserver`, 네트워크
가로채기, 쿠키·storage·token·원문 HTML·숨김 DOM 수집도 사용하지 않습니다. 보호 문구
`CODE -8002`, `CODE -8003`, `macro_err1`, `CAPTCHA`, `NetFunnel`, `비정상 접근`이 보이면
즉시 전송을 중단합니다. 공식 화면에서 승객 1명을 확인할 수 없거나 날짜·경로·두 좌석 등급을
완전히 읽지 못해도 전송하지 않습니다.

## 설치와 빌드

```powershell
cd apps/korail-browser-companion
npm install
npm run typecheck
npm test
npm run build
```

Chrome 또는 Edge의 확장 프로그램 개발자 모드에서 `dist` 폴더를 압축 해제된 확장 프로그램으로
불러옵니다.

## 사용

> 이 확장은 현재 주 UI에서 제거된 레거시 호환 경로입니다. 새 설치의 기본 좌석 조회 경로가 아니며,
> 일반 사용자는 설치할 필요가 없습니다. 유지보수·호환성 검증 목적에서만 아래 절차를 사용합니다.

1. 레일웨잇 `.env`에서 `KORAIL_BROWSER_BRIDGE_ENABLED=true`로 기동합니다. `KORAIL_BROWSER_BRIDGE_TOKEN`은 더 이상 필요하지 않습니다.
2. 관리자 `설정 > 로그·진행 상태`에서 5분 동안 한 번만 쓸 수 있는 연결 코드를 만듭니다.
3. 확장 `로컬 브리지 설정`에 `http://127.0.0.1`(Caddy) 또는 개발 화면의
   `http://127.0.0.1:4173` 같은 loopback 주소와 연결 코드를 입력합니다. 연결되면 확장은 설치별 자격증명과 client ID를 로컬 저장소에만 보관합니다.
4. KORAIL에서 `https://www.korail.com/ticket/search/list`를 직접 열어 승객 1명 검색을 완료한 뒤 확장 popup의 **현재 결과 가져오기**를 누릅니다. 레일웨잇 주 UI에는 더 이상 `공식 좌석 상태 가져오기` 버튼이 없습니다.

서비스가 다른 개인 장비에 있으면 Tailscale 또는 공개 도메인의 `https://...` 주소를 저장할 수
있습니다. 저장 버튼을 누를 때 Chrome이 그 정확한 HTTPS origin에 대한 선택 권한을 요청하며,
승인된 origin 외 사이트에는 브리지 요청을 보내지 않습니다. 암호화되지 않은 원격 HTTP 주소는
허용하지 않습니다.

연결 코드는 1회 사용 뒤 폐기됩니다. 이후 명시적 가져오기는 저장된 설치별 자격증명을 자동 사용합니다. 서버는 자격증명 원문이 아니라 키드 해시만 보관하며, 정확한 `chrome-extension://` origin과 client ID가 일치할 때만 수락합니다. 각 전송은 본문 SHA-256에 결박된 30초짜리 1회 challenge를 받고, 설치별 수락 예산은 분당 6건입니다. 관리자 설정에서 해당 연결을 철회하면 이후 전송은 거부됩니다. 설정이 없을 때는 결과를 브라우저 저장소에 보관하거나 다른 곳에 전송하지 않습니다.

브리지는 `Cache-Control: no-store`, 설치 자격증명·client ID·challenge 헤더를 사용합니다. Caddy는 bridge 본문을 128KB로 제한하고 이 민감 헤더를 접근 로그에서 제거합니다. 설치와 첫 연결은 1회 사용자 행동이며, 이후 자동화되는 것은 사용자가 누른 가져오기에 대한 자격증명 사용뿐입니다.

## 개발 계약

- 파서는 DOM 접근과 분리한 순수 함수이며 `fixtures/korail-results.fixture.ts`로 단위 테스트합니다.
- 표준석/특실은 화면의 첫 번째/두 번째 `.price_box`와 `.gen`/`.spe` 표시를 함께 확인합니다.
- `.sold_out`, `.sold_out_soon`, `예약대기`, 좌석 미제공 문구를 각각 `sold_out`, `limited`,
  `waitlist_available`, `not_offered`로 정규화합니다. 그 밖의 정상 가격 상자는 `available`입니다.
- 결과는 서버가 관측 시각·신선도와 출처를 부여하는 보조 입력일 뿐, 자동 예약 또는 백그라운드
  감시의 근거가 아닙니다.
