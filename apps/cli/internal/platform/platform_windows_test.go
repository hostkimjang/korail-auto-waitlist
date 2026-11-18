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
	want := "(A;" + flags + ";FA;;;" + user.User.Sid.String() + ")"
	// Windows can retain automatic-inheritance bookkeeping flags. Verify the
	// protected bit above, and require exactly one allow ACE for this user.
	actual := sd.String()
	if index := strings.Index(actual, "("); index >= 0 {
		actual = actual[index:]
	}
	if actual != want {
		t.Fatalf("private DACL mismatch: got %s want %s", actual, want)
	}
}
