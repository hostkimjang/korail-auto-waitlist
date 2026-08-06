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

1. 홈의 결제 필요·활동 중 대기 정보 구조
2. `새 대기`에서 KORAIL·SRT, 서울→부산 여정 선택
3. 좌석 발견 후 행동을 `알림만 받기`로 유지
4. 데모 시간표와 일반실·특실 provenance 확인
5. 공식 채널 인계 안내의 비제휴·최종 확인 경계
6. KTX 일반실 대기 등록 후 홈 반영

촬영은 실제 좌석, 예약 성공 또는 운영사 응답을 재현하지 않습니다.

## 원본 녹화

```powershell
cd apps/web
npm ci
npm run demo:capture
```

원본 WebM은 Git에서 제외된 `output/readme-demo-video/railwait-demo.webm`에 생성됩니다. 스크립트가 자체 Vite 서버를 `127.0.0.1:4175`에 열고 종료하며, `VITE_DEMO_MODE=true`를 강제합니다. 브라우저 시계는 fixture 날짜와 맞는 `2026-07-30 14:32 KST`로 고정하고 localhost 밖의 요청은 차단합니다.

## 공개 파일 변환

저장소 루트에서 ffmpeg 7.x로 실행합니다.

```powershell
ffmpeg -y -i output/readme-demo-video/railwait-demo.webm -an -c:v libx264 -preset slow -crf 24 -pix_fmt yuv420p -movflags +faststart docs/media/railwait-demo.mp4
ffmpeg -y -ss 00:00:11.5 -i docs/media/railwait-demo.mp4 -frames:v 1 -vf "scale=1280:-2" docs/media/railwait-demo-poster.png
ffmpeg -y -i docs/media/railwait-demo.mp4 -vf "fps=8,scale=800:-2:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle" -loop 0 docs/media/railwait-demo.gif
```

## 공개 전 확인

- 영상에 실제 계정, 알림 endpoint, token, cookie, 주소 query, 로컬 사용자 경로가 없어야 합니다.
- `데모 시간표`, `데모 좌석 상태` 표기가 읽혀야 합니다.
- 공식 페이지 안내가 예약 성공이나 제휴로 오해되지 않아야 합니다.
- README의 GIF 대체 텍스트와 MP4 링크가 GitHub에서 열려야 합니다.
