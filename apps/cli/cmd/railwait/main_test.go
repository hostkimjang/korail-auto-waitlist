package main

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLocalPackageUpdateRequiresPackageAndTrustKeyTogether(t *testing.T) {
	path := t.TempDir()
	for _, args := range [][]string{
		{"--path", path, "--package", "package"},
		{"--path", path, "--trust-key", "trust-key.pub"},
		{"check", "--path", path, "--package", "package", "--trust-key", "trust-key.pub"},
	} {
		if err := updateCommand(context.Background(), args); err == nil {
			t.Fatalf("accepted %v", args)
		}
	}
}

func TestStatusJobReadsRecordsOfJobsRunByTheHostCLI(t *testing.T) {
	root := t.TempDir()
	id := strings.Repeat("d", 32)
	dir := filepath.Join(root, ".railwait", "updates")
	if err := os.MkdirAll(dir, 0700); err != nil {
		t.Fatal(err)
	}
	record := `{"job_id":"` + id + `","state":"recovery_required","current_version":"1.4.0","target_version":"1.4.1","stage":"verifying"}`
	if err := os.WriteFile(filepath.Join(dir, id+".json"), []byte(record), 0600); err != nil {
		t.Fatal(err)
	}
	if err := run(context.Background(), []string{"status", "--path", root, "--job", id, "--json"}); err != nil {
		t.Fatal(err)
	}
	if err := run(context.Background(), []string{"status", "--path", root, "--job", strings.Repeat("e", 32)}); err == nil {
		t.Fatal("reported a job without any record")
	}
}

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
	dest, err := copyManagerExecutable(root, source, false)
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Dir(dest) != filepath.Join(root, ".railwait", "bin") {
		t.Fatal(dest)
	}
	if _, err = copyManagerExecutable(root, source, false); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(source, []byte("different-build"), 0700); err != nil {
		t.Fatal(err)
	}
	if _, err = copyManagerExecutable(root, source, false); err == nil {
		t.Fatal("replaced existing manager without update workflow")
	}
	data, _ := os.ReadFile(dest)
	if string(data) != "verified-cli-fixture" {
		t.Fatal("existing manager executable changed")
	}
	// A manager whose service definition the OS rejected never ran, so this build replaces it.
	if _, err = copyManagerExecutable(root, source, true); err != nil {
		t.Fatal(err)
	}
	if data, _ = os.ReadFile(dest); string(data) != "different-build" {
		t.Fatal("never started manager executable was not replaced")
	}
}

func TestConfigureReservationNeedsAnExplicitDirection(t *testing.T) {
	root := t.TempDir()
	for _, args := range [][]string{
		{"reservation"},
		{"reservation", "maybe", "--path", root},
		{"reservation", "--path", root, "on"},
		{"reservation", "on", "--path", root, "extra"},
		{"reservation", "on", "--path", root, "--plan", "not-a-plan"},
	} {
		if err := configureCommand(context.Background(), args); err == nil {
			t.Fatalf("accepted %v", args)
		}
	}
}
