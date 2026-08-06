# README 데모 영상 제작

README에는 소개용 마케팅 애니메이션과 실제 UI 데모가 있습니다. 두 영상 모두 외부 요청 없이 로컬 자산과 고정 데모 데이터만 사용합니다.

## 소개 애니메이션

```powershell
cd apps/web
npm ci
npm run marketing:capture
```

원본은 Git에서 제외된 `output/marketing-video/railwait-intro.webm`에 생성됩니다. 기존 앱 아이콘과 디자인 토큰을 사용하며, 문구와 화면 요소는 `apps/web/scripts/railwait-marketing.html`에서 결정적으로 렌더링합니다.

저장소 루트에서 공개 파일을 만듭니다.

```powershell
ffmpeg -y -ss 00:00:00.20 -i output/marketing-video/railwait-intro.webm -an -c:v libx264 -preset slow -crf 20 -pix_fmt yuv420p -movflags +faststart docs/media/railwait-intro.mp4
ffmpeg -y -ss 00:00:02.0 -i docs/media/railwait-intro.mp4 -frames:v 1 -update 1 docs/media/railwait-intro-poster.png
ffmpeg -y -i docs/media/railwait-intro.mp4 -vf "fps=8,scale=800:-2:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=96:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" -loop 0 docs/media/railwait-intro.gif
```

## 실제 UI 데모 장면

1. 홈의 활동 중 대기 정보 구조
2. `새 대기`에서 KORAIL·SRT, 서울→부산 여정 선택
3. 좌석 발견 후 행동을 `좌석 재발견마다 자동 예매`로 선택
4. 데모 시간표와 일반실·특실의 정보 출처 확인
5. KTX 일반실 대기 등록 후 홈에서 같은 열차 확인
6. 같은 열차의 좌석 발견과 예매 진행 상태 확인
7. 결제 직전 중단과 공식 플랫폼 직접 결제 안내 확인

후반부 상태는 촬영 전용 고정 fixture가 같은 대기 ID와 여정을 유지하며 순서대로 전환합니다. 모든 좌석 근거는 `mock`으로 표시하고 결제기한은 만들지 않습니다. 촬영은 실제 좌석, 예약 성공 또는 운영사 응답을 재현하지 않습니다.

## 원본 녹화

```powershell
cd apps/web
npm ci
npm run demo:capture
```

원본 WebM은 Git에서 제외된 `output/readme-demo-video/railwait-demo.webm`에 생성됩니다. 스크립트가 자체 Vite 서버를 `127.0.0.1:4175`에 열고 종료하며, `VITE_DEMO_MODE=true`와 촬영 전용 예약 진행 시나리오를 강제합니다. 이 드라이버는 개발 모드의 명시적 촬영 시나리오에서만 열립니다. 브라우저 시계는 고정 예시 날짜인 `2026-07-30 14:32 KST`에 맞추고 localhost 밖의 요청은 차단합니다.

`demo-capture-motion.mjs`는 접근성 선택자로 찾은 실제 위치를 기준으로 커서 이동과 클릭 링을 그립니다. 단계 화면과 예약 상태는 짧게 교차 전환하고, 화면 밖의 다음 버튼과 열차 카드는 실제 페이지를 부드럽게 스크롤해 찾습니다. 화면 확대는 상태 변경이 끝난 결과 장면에만 적용하므로 앱 화면 구조와 스크롤 동작에는 영향을 주지 않습니다. UI 장면에는 `연출 데모 · 실제 예약 아님` 표시를 고정합니다.

## 브랜드 인트로와 화면 데모 합성

저장소 루트에서 ffmpeg 7.x로 실행합니다.

```powershell
ffmpeg -y -i output/readme-demo-video/railwait-demo.webm -an -c:v libx264 -preset slow -crf 24 -pix_fmt yuv420p output/readme-demo-video/railwait-demo-ui.mp4
ffmpeg -y -i docs/media/railwait-intro.mp4 -i output/readme-demo-video/railwait-demo-ui.mp4 -filter_complex "[0:v]trim=start=0:end=3.0,setpts=PTS-STARTPTS,scale=1280:720:flags=lanczos,pad=1280:800:0:40:color=0x082f52,fps=25[brand];[1:v]trim=start=2.36,setpts=PTS-STARTPTS,scale=1280:800:flags=lanczos,fps=25[ui];[brand][ui]xfade=transition=wipeleft:duration=0.40:offset=2.60,format=yuv420p[out]" -map "[out]" -an -c:v libx264 -preset slow -crf 22 -movflags +faststart docs/media/railwait-demo.mp4
ffmpeg -y -ss 00:00:30.80 -i docs/media/railwait-demo.mp4 -frames:v 1 -vf "scale=1280:-2:flags=lanczos" docs/media/railwait-demo-poster.png
ffmpeg -y -i docs/media/railwait-demo.mp4 -vf "fps=8,scale=800:-2:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=96:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" -loop 0 docs/media/railwait-demo.gif
```

공개 영상은 3초짜리 브랜드 장면에서 실제 UI로 이어지는 33.20초 구성입니다. 원본 녹화 앞의 브라우저 준비 화면 2.36초는 합성할 때 제외합니다.

## 공개 전 확인

- 영상에 실제 계정, 알림 주소, 토큰, 쿠키, URL 쿼리와 로컬 사용자 경로가 없어야 합니다.
- `데모 시간표`, `데모 좌석 상태` 표기가 읽혀야 합니다.
- 공식 페이지 안내가 예약 성공이나 제휴로 오해되지 않아야 합니다.
- 첫 프레임부터 브랜드 장면이 보여야 하며 2.6~3.0초 전환 구간에 흰 화면이 없어야 합니다.
- 커서가 클릭 대상에 도착한 뒤 링이 표시되고, 확대 장면에 빈 가장자리나 잘린 핵심 정보가 없어야 합니다.
- 단계 이동, 열차 카드 탐색과 홈 복귀가 한 프레임 안에 바뀌지 않고 중간 이동 프레임을 보여야 합니다.
- README의 GIF 대체 텍스트와 MP4 링크가 GitHub에서 열려야 합니다.
