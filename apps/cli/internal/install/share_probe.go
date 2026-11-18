package install

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// checkFileSharing proves with a small pinned image that containers can read and
// write the install folder before multi-gigabyte image downloads start. A folder
// that Docker Desktop, Lima or Colima does not share otherwise fails only after
// the pull, because Docker silently binds an empty folder inside the VM.
func (i *Installer) checkFileSharing(ctx context.Context, s State, image string) error {
	probe, err := os.MkdirTemp(filepath.Join(s.Path, ".railwait"), "share-probe-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(probe)
	// The probe lives inside the private .railwait folder. Open modes only let a
	// remapped container user answer; they do not widen access to install data.
	if err = os.Chmod(probe, 0o777); err != nil {
		return err
	}
	hostToken, err := probeToken()
	if err != nil {
		return err
	}
	containerToken, err := probeToken()
	if err != nil {
		return err
	}
	if err = os.WriteFile(filepath.Join(probe, "host"), []byte(hostToken), 0o644); err != nil {
		return err
	}
	out, runErr := i.docker(ctx, s, nil, "run", "--rm", "--network", "none", "--entrypoint", "sh",
		"-e", "RAILWAIT_PROBE_TOKEN="+containerToken, "-v", probe+":/probe", image,
		"-c", "cat /probe/host && printf %s \"$RAILWAIT_PROBE_TOKEN\" > /probe/container")
	written, readErr := os.ReadFile(filepath.Join(probe, "container"))
	if runErr != nil || strings.TrimSpace(string(out)) != hostToken || readErr != nil || string(written) != containerToken {
		return fmt.Errorf("Docker 컨테이너가 설치 폴더 %s 를 읽고 쓰지 못했습니다. Docker Desktop은 설정의 File sharing에, Lima·Colima는 VM의 쓰기 가능한 공유 폴더에 이 경로가 있어야 합니다. 공유된 폴더를 --path로 지정해 다시 실행하세요", s.Path)
	}
	return nil
}

func probeToken() (string, error) {
	raw := make([]byte, 16)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	return hex.EncodeToString(raw), nil
}
