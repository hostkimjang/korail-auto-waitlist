package platform

import (
	"fmt"
	"os/exec"
	"path/filepath"
)

func clearExtendedACL(path string) error {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return err
	}
	// macOS extended allow ACLs can grant access even after chmod 0600/0700.
	// Use the fixed system tool without a shell, before writing any secret.
	if err := exec.Command("/bin/chmod", "-N", absolute).Run(); err != nil {
		return fmt.Errorf("cannot clear inherited macOS ACL: %w", err)
	}
	return nil
}
