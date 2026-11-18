package platform

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestPrivateStorageClearsMacOSACL(t *testing.T) {
	dir := t.TempDir()
	file := filepath.Join(dir, "secret")
	if err := os.WriteFile(file, []byte("fixture"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{dir, file} {
		if data, err := exec.Command("/bin/chmod", "+a", "everyone allow read", path).CombinedOutput(); err != nil {
			t.Fatalf("set fixture ACL: %v %s", err, data)
		}
	}
	if err := SecureDir(dir); err != nil {
		t.Fatal(err)
	}
	if err := SecureFile(file); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{dir, file} {
		data, err := exec.Command("/bin/ls", "-lde", path).Output()
		if err != nil {
			t.Fatal(err)
		}
		if bytes.Contains(data, []byte("everyone allow")) {
			t.Fatal("extended allow ACL remained after private storage protection")
		}
	}
}
