//go:build !windows

package install

import (
	"errors"
	"os"
	"path/filepath"
)

func replaceFile(source, target string) error {
	if err := os.Rename(source, target); err != nil {
		return err
	}
	dir, err := os.Open(filepath.Dir(target))
	if err != nil {
		return err
	}
	return errors.Join(dir.Sync(), dir.Close())
}

// fileHeldByAnotherProcess is false on POSIX systems, which rename and remove folders even
// while another process has a file inside them open.
func fileHeldByAnotherProcess(error) bool { return false }
