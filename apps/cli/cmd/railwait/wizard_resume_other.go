//go:build !windows

package main

import "errors"

func newWizardResumeRegistry() (wizardResumeRegistry, error) {
	return nil, errors.New("로그인 후 설치 자동 재개는 Windows에서만 지원합니다")
}
