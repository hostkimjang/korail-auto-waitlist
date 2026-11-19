//go:build !windows

package platform

import (
	"errors"
	"time"
)

// EndManagerProcess is needed only on Windows; launchd and systemd stop the manager process
// itself when they stop its service.
func EndManagerProcess(int, string, time.Time, time.Duration) error {
	return errors.ErrUnsupported
}
