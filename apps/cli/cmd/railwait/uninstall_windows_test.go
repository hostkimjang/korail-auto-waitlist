package main

import "testing"

func TestDeleteDataRefusesADriveRoot(t *testing.T) {
	for _, path := range []string{`C:\`, `C:\Users`} {
		if err := deletableInstallation(path, `C:\Users\a`, "windows", ""); err == nil {
			t.Fatalf("%s accepted", path)
		}
	}
	if !containsPath(`C:\Railwait`, `c:\railwait\.railwait\bin\railwait.exe`, "windows") {
		t.Fatal("Windows paths compare case-insensitively")
	}
}
