package platform

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"golang.org/x/sys/windows"
)

// stillActive is the exit code GetExitCodeProcess reports for a running process.
const stillActive = 259

// EndManagerProcess ends the manager process pid that Task Scheduler left running: /End ends
// only the conhost that hosts the manager, and the manager keeps its serve lock. It ends
// nothing unless the process runs executable and was created just before startedAt, the start
// the manager recorded in its heartbeat, so a reused process ID is never ended. A process
// that has already exited is not an error.
func EndManagerProcess(pid int, executable string, startedAt time.Time, wait time.Duration) error {
	if pid <= 0 || pid == os.Getpid() {
		return ErrNotManagerProcess
	}
	process, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION|windows.PROCESS_TERMINATE|windows.SYNCHRONIZE, false, uint32(pid))
	if errors.Is(err, windows.ERROR_INVALID_PARAMETER) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("프로세스를 열지 못했습니다: %w", err)
	}
	defer windows.CloseHandle(process)
	var code uint32
	if err = windows.GetExitCodeProcess(process, &code); err != nil {
		return err
	}
	if code != stillActive {
		return nil
	}
	image, err := processImage(process)
	if err != nil {
		return fmt.Errorf("프로세스의 실행 파일을 확인하지 못했습니다: %w", err)
	}
	if !sameExecutable(image, executable) {
		return fmt.Errorf("%w: 실행 파일 %s", ErrNotManagerProcess, image)
	}
	var created, exited, kernel, user windows.Filetime
	if err = windows.GetProcessTimes(process, &created, &exited, &kernel, &user); err != nil {
		return fmt.Errorf("프로세스의 시작 시각을 확인하지 못했습니다: %w", err)
	}
	if !plausibleManagerStart(time.Unix(0, created.Nanoseconds()), startedAt) {
		return fmt.Errorf("%w: 프로세스 시작 시각이 heartbeat의 시작 시각과 맞지 않습니다", ErrNotManagerProcess)
	}
	if err = windows.TerminateProcess(process, 1); err != nil {
		return fmt.Errorf("프로세스를 끝내지 못했습니다: %w", err)
	}
	event, err := windows.WaitForSingleObject(process, uint32(wait.Milliseconds()))
	if err != nil {
		return err
	}
	if event != windows.WAIT_OBJECT_0 {
		return fmt.Errorf("프로세스가 %s 안에 끝나지 않았습니다", wait)
	}
	return nil
}

func processImage(process windows.Handle) (string, error) {
	buffer := make([]uint16, windows.MAX_LONG_PATH)
	size := uint32(len(buffer))
	if err := windows.QueryFullProcessImageName(process, 0, &buffer[0], &size); err != nil {
		return "", err
	}
	return windows.UTF16ToString(buffer[:size]), nil
}

// sameExecutable compares the file a process runs with the expected one: by file identity
// when both can be read, which also matches short 8.3 names, otherwise by path without case.
func sameExecutable(image, executable string) bool {
	if running, err := os.Stat(image); err == nil {
		if expected, err := os.Stat(executable); err == nil {
			return os.SameFile(running, expected)
		}
	}
	return strings.EqualFold(filepath.Clean(image), filepath.Clean(executable))
}
