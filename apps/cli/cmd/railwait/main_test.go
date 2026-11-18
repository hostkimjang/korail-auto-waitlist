package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestProtectedInputRejectsUnknownFieldsAndTrailingObjects(t *testing.T) {
	for _, data := range []string{`{"username":"admin","password":"do-not-print","unexpected":true}`, `{} {}`, strings.Repeat("x", 8193)} {
		path := filepath.Join(t.TempDir(), "credentials.json")
		if err := os.WriteFile(path, []byte(data), 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := readInstallInput(path); err == nil {
			t.Fatal("accepted malformed input")
		} else if strings.Contains(err.Error(), "do-not-print") {
			t.Fatal("credential leaked to error")
		}
	}
}

func TestProtectedInputAcceptsOnlyCredentialAndOptionalTAGOFields(t *testing.T) {
	path := filepath.Join(t.TempDir(), "credentials.json")
	if err := os.WriteFile(path, []byte(`{"username":"admin","password":"local-test-value","tago_service_key":"optional-test-key"}`), 0600); err != nil {
		t.Fatal(err)
	}
	value, err := readInstallInput(path)
	if err != nil {
		t.Fatal(err)
	}
	if value.Username != "admin" || value.Password != "local-test-value" || value.TAGOKey != "optional-test-key" {
		t.Fatal("input fields changed")
	}
}

func TestManagerExecutableIsPersistedAndDifferentExistingBinaryIsPreserved(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(t.TempDir(), "downloaded-cli")
	if err := os.WriteFile(source, []byte("verified-cli-fixture"), 0700); err != nil {
		t.Fatal(err)
	}
	dest, err := copyManagerExecutable(root, source)
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Dir(dest) != filepath.Join(root, ".railwait", "bin") {
		t.Fatal(dest)
	}
	if _, err = copyManagerExecutable(root, source); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(source, []byte("different-build"), 0700); err != nil {
		t.Fatal(err)
	}
	if _, err = copyManagerExecutable(root, source); err == nil {
		t.Fatal("replaced existing manager without update workflow")
	}
	data, _ := os.ReadFile(dest)
	if string(data) != "verified-cli-fixture" {
		t.Fatal("existing manager executable changed")
	}
}
