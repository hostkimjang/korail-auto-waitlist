package platform

import (
	"strings"
	"testing"

	"golang.org/x/sys/windows"
)

func assertPrivate(t *testing.T, path string, directory bool) {
	t.Helper()
	sd, err := windows.GetNamedSecurityInfo(path, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION)
	if err != nil {
		t.Fatal(err)
	}
	control, _, err := sd.Control()
	if err != nil || control&windows.SE_DACL_PROTECTED == 0 {
		t.Fatalf("DACL must block inherited permissions: %v", err)
	}
	user, err := windows.GetCurrentProcessToken().GetTokenUser()
	if err != nil {
		t.Fatal(err)
	}
	flags := ""
	if directory {
		flags = "OICI"
	}
	// SDDL writes well-known accounts as aliases, for example LA for the built-in
	// administrator that CI runners use. Render the expected ACE through the same
	// conversion so the comparison does not depend on which account runs the test.
	expected, err := windows.SecurityDescriptorFromString("D:P(A;" + flags + ";FA;;;" + user.User.Sid.String() + ")")
	if err != nil {
		t.Fatal(err)
	}
	want := daclEntries(expected)
	// Windows can retain automatic-inheritance bookkeeping flags. Verify the
	// protected bit above, and require exactly one allow ACE for this user.
	actual := daclEntries(sd)
	if actual != want {
		t.Fatalf("private DACL mismatch: got %s want %s", actual, want)
	}
}

// daclEntries keeps only the ACE list of an SDDL string, dropping DACL control flags.
func daclEntries(sd *windows.SECURITY_DESCRIPTOR) string {
	text := sd.String()
	if index := strings.Index(text, "("); index >= 0 {
		return text[index:]
	}
	return text
}
