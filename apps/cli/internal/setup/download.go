package setup

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"time"

	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
)

func dependencyURLAllowed(tool, address string) bool {
	u, err := url.Parse(address)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Port() != "" || u.Fragment != "" {
		return false
	}
	host := map[string]string{"docker": "desktop.docker.com", "tailscale": "pkgs.tailscale.com"}[tool]
	return host != "" && u.Host == host
}

func downloadClient(p InstallPlan) *http.Client {
	return &http.Client{
		Timeout:   45 * time.Minute,
		Transport: &http.Transport{Proxy: http.ProxyFromEnvironment, DialContext: (&net.Dialer{Timeout: 30 * time.Second, KeepAlive: 30 * time.Second}).DialContext, TLSHandshakeTimeout: 15 * time.Second, ResponseHeaderTimeout: 30 * time.Second},
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= 5 || !dependencyURLAllowed(p.Tool, req.URL.String()) {
				return errors.New("공식 다운로드 서버 외부로 연결하려 하여 중단했습니다")
			}
			return nil
		},
	}
}

func downloadDependency(ctx context.Context, p InstallPlan, destination string, progress func(string)) error {
	client := downloadClient(p)
	defer client.CloseIdleConnections()
	return downloadWithClient(ctx, client, p, destination, progress)
}

func downloadWithClient(ctx context.Context, client *http.Client, p InstallPlan, destination string, progress func(string)) (err error) {
	if !dependencyURLAllowed(p.Tool, p.URL) || p.MaxBytes <= 0 {
		return errors.New("공식 설치 파일 주소를 확인하지 못했습니다")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, p.URL, nil)
	if err != nil {
		return errors.New("설치 파일 요청을 만들지 못했습니다")
	}
	req.Header.Set("User-Agent", "Railwait-Installer")
	progress("공식 서버에서 " + p.Filename + " 다운로드 중…")
	res, err := client.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return errors.New("공식 설치 파일을 다운로드하지 못했습니다. 네트워크 연결 후 다시 실행해 주세요")
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK || res.ContentLength == 0 || res.ContentLength > p.MaxBytes {
		return errors.New("설치 파일 응답 또는 크기가 올바르지 않습니다")
	}
	f, err := os.OpenFile(destination, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return errors.New("설치 파일을 안전하게 저장하지 못했습니다")
	}
	defer func() {
		if f != nil {
			_ = f.Close()
		}
		if err != nil {
			_ = os.Remove(destination)
		}
	}()
	if err = platform.SecureFile(destination); err != nil {
		return err
	}
	w := &downloadWriter{writer: f, progress: progress, expected: res.ContentLength, last: time.Now()}
	n, err := io.Copy(w, io.LimitReader(res.Body, p.MaxBytes+1))
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return errors.New("설치 파일 다운로드가 끊겼습니다. 다시 실행해 주세요")
	}
	if n == 0 || n > p.MaxBytes || (res.ContentLength >= 0 && n != res.ContentLength) {
		return errors.New("설치 파일이 완전하지 않거나 허용 크기를 넘었습니다")
	}
	if err = f.Sync(); err != nil {
		return errors.New("설치 파일을 저장하지 못했습니다")
	}
	if err = f.Close(); err != nil {
		return errors.New("설치 파일 저장을 완료하지 못했습니다")
	}
	f = nil
	progress("다운로드 완료 · 배포자 서명을 확인합니다.")
	return nil
}

type downloadWriter struct {
	writer             io.Writer
	progress           func(string)
	expected, received int64
	last               time.Time
}

func (w *downloadWriter) Write(p []byte) (int, error) {
	n, err := w.writer.Write(p)
	w.received += int64(n)
	if time.Since(w.last) >= 3*time.Second {
		if w.expected > 0 {
			w.progress(fmt.Sprintf("다운로드 중 · %d%% (%d MB)", w.received*100/w.expected, w.received>>20))
		} else {
			w.progress(fmt.Sprintf("다운로드 중 · %d MB", w.received>>20))
		}
		w.last = time.Now()
	}
	return n, err
}
