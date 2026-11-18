//go:build windows

package install

import (
	"errors"

	"golang.org/x/sys/windows"
)

func replaceFile(source, target string) error {
	s, err := windows.UTF16PtrFromString(source)
	if err != nil {
		return err
	}
	t, err := windows.UTF16PtrFromString(target)
	if err != nil {
		return err
	}
	return windows.MoveFileEx(s, t, windows.MOVEFILE_REPLACE_EXISTING|windows.MOVEFILE_WRITE_THROUGH)
}

// fileHeldByAnotherProcess reports the errors Windows returns while another process, such as
// an antivirus scan or the search indexer, has a file open inside a folder being renamed or
// removed.
func fileHeldByAnotherProcess(err error) bool {
	return errors.Is(err, windows.ERROR_ACCESS_DENIED) || errors.Is(err, windows.ERROR_SHARING_VIOLATION) || errors.Is(err, windows.ERROR_LOCK_VIOLATION)
}
