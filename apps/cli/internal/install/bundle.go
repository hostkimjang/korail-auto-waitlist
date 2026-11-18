package install

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
)

func bundleHashes(dir string) (map[string]string, error) {
	hashes := map[string]string{}
	err := filepath.WalkDir(dir, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("릴리스의 symbolic link는 사용할 수 없습니다")
		}
		if entry.IsDir() {
			return nil
		}
		if !entry.Type().IsRegular() {
			return fmt.Errorf("릴리스에 지원하지 않는 파일이 있습니다")
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		sum := sha256.Sum256(data)
		relative, err := filepath.Rel(dir, path)
		if err != nil {
			return err
		}
		hashes[filepath.ToSlash(relative)] = hex.EncodeToString(sum[:])
		return nil
	})
	if err != nil {
		return nil, err
	}
	if hashes["compose.release.yml"] == "" || hashes[".env.example"] == "" {
		return nil, fmt.Errorf("릴리스 필수 파일이 없습니다")
	}
	return hashes, nil
}

func verifyBundle(dir string, expected map[string]string) error {
	if len(expected) == 0 {
		return fmt.Errorf("검증된 릴리스 지문이 없습니다")
	}
	actual, err := bundleHashes(dir)
	if err != nil {
		return err
	}
	if !reflect.DeepEqual(actual, expected) {
		return fmt.Errorf("검증 후 릴리스 파일이 변경되었습니다; 실행을 중지합니다")
	}
	return nil
}

func onlyUnfinishedLock(entries []os.DirEntry, path string) bool {
	if len(entries) != 1 || entries[0].Name() != ".railwait" || !entries[0].IsDir() {
		return false
	}
	children, err := os.ReadDir(filepath.Join(path, ".railwait"))
	if err != nil {
		return false
	}
	if len(children) == 0 {
		return true
	}
	return len(children) == 1 && children[0].Name() == "install.lock" && children[0].Type().IsRegular()
}
