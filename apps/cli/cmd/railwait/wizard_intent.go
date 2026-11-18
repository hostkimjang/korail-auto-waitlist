package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
)

const wizardRecordName = "wizard.json"

// wizardRecord remembers the access mode chosen for this folder. A plain rerun
// after a failed Tailscale step then continues remote setup instead of silently
// finishing as a local-only install.
type wizardRecord struct {
	Schema int    `json:"schema"`
	Access string `json:"access"`
}

func recordedAccess(path string) string {
	data, err := os.ReadFile(filepath.Join(path, ".railwait", wizardRecordName))
	if err != nil {
		return ""
	}
	var record wizardRecord
	if json.Unmarshal(data, &record) != nil || record.Schema != 1 || (record.Access != "local" && record.Access != "tailscale") {
		return ""
	}
	return record.Access
}

// rememberAccess writes only after the installer owns the folder. An empty new
// folder must stay empty until install.json exists.
func rememberAccess(path, mode string) error {
	if mode != "local" && mode != "tailscale" {
		return nil
	}
	if _, err := os.Lstat(filepath.Join(path, ".railwait", "install.json")); err != nil {
		return nil
	}
	encoded, err := json.MarshalIndent(wizardRecord{Schema: 1, Access: mode}, "", "  ")
	if err != nil {
		return err
	}
	return writeAdvisoryRecord(path, wizardRecordName, encoded)
}

// writeAdvisoryRecord atomically replaces a small, reconstructible wizard record
// in .railwait. It never touches operational or credential files.
func writeAdvisoryRecord(path, name string, encoded []byte) error {
	f, err := os.CreateTemp(filepath.Join(path, ".railwait"), "."+name+"-*")
	if err != nil {
		return err
	}
	temp := f.Name()
	defer os.Remove(temp)
	_, writeErr := f.Write(encoded)
	closeErr := f.Close()
	if err = errors.Join(writeErr, closeErr); err != nil {
		return err
	}
	destination := filepath.Join(path, ".railwait", name)
	if info, statErr := os.Lstat(destination); statErr == nil {
		if !info.Mode().IsRegular() {
			return errors.New("설치 기록 경로가 일반 파일이 아닙니다")
		}
		// Windows cannot rename over an existing file.
		if runtime.GOOS == "windows" {
			if err := os.Remove(destination); err != nil {
				return err
			}
		}
	} else if !os.IsNotExist(statErr) {
		return statErr
	}
	return os.Rename(temp, destination)
}
