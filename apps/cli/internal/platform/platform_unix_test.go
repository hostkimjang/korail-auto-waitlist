//go:build linux || darwin

package platform

import (
	"os"
	"path/filepath"
	"testing"
)

func assertPrivate(t *testing.T, path string, directory bool) {
	t.Helper()
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	want := os.FileMode(0o600)
	if directory {
		want = 0o700
	}
	if info.Mode().Perm() != want {
		t.Fatalf("permissions: got %o want %o", info.Mode().Perm(), want)
	}
}

func TestRejectHardLinkedSecret(t *testing.T) {
	dir := t.TempDir()
	file := filepath.Join(dir, "secret")
	if err := os.WriteFile(file, []byte("fixture"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Link(file, filepath.Join(dir, "alias")); err != nil {
		t.Fatal(err)
	}
	if err := SecureFile(file); err == nil {
		t.Fatal("hardlinked secret accepted")
	}
}
