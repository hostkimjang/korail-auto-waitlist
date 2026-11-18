//go:build !windows

package setup

import "errors"

func installerWindowsSystemDirectory() (string, error) {
	return "", errors.New("Windows 시스템 설치 프로그램은 Windows에서만 실행할 수 있습니다")
}
