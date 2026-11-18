package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/hostkimjang/korail-auto-waitlist/apps/cli/internal/platform"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"unicode/utf16"
)

type wizardResumeState struct {
	Schema  int           `json:"schema"`
	Options wizardOptions `json:"options"`
}
type wizardResumeRegistry interface {
	Get(string) (string, error)
	Set(string, string) error
	Delete(string) error
}

func resumeDirectory() (string, error) {
	root, err := os.UserCacheDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(root, "RailwaitSetupResume"), nil
}
func resumeName(path string) string {
	sum := sha256.Sum256([]byte(path))
	return "RailwaitSetup-" + hex.EncodeToString(sum[:8])
}

func resumeCommand(executable, statePath string) (string, error) {
	for _, path := range []string{executable, statePath} {
		if !filepath.IsAbs(path) || strings.ContainsAny(path, "\"\r\n\x00") || strings.HasSuffix(path, `\`) {
			return "", errors.New("자동 재개 경로 형식이 잘못되었습니다")
		}
	}
	// Direct process invocation has no shell expansion. Each path is quoted and
	// ends in a filename, so trailing-backslash quote escaping cannot occur.
	command := "\"" + executable + "\" --resume-setup \"" + statePath + "\""
	if len(utf16.Encode([]rune(command))) > 260 {
		return "", errors.New("자동 재개 경로가 너무 깁니다. 재부팅 후 설치 프로그램을 다시 실행해 주세요")
	}
	return command, nil
}
func scheduleWizardResume(ctx context.Context, o wizardOptions) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	registry, err := newWizardResumeRegistry()
	if err != nil {
		return err
	}
	dir, err := resumeDirectory()
	if err != nil {
		return err
	}
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	return scheduleWizardResumeWith(ctx, o, executable, dir, registry)
}
func scheduleWizardResumeWith(ctx context.Context, o wizardOptions, executable, dir string, registry wizardResumeRegistry) (err error) {
	if err := ctx.Err(); err != nil {
		return err
	}
	if o.Input == "-" {
		return errors.New("표준입력의 비밀정보는 저장하지 않습니다. 재부팅 후 보호된 JSON 파일 또는 대화형 설치로 다시 실행해 주세요")
	}
	for _, path := range []*string{&o.Path, &o.Package, &o.TrustKey, &o.Input} {
		if *path != "" {
			absolute, e := filepath.Abs(*path)
			if e != nil {
				return errors.New("자동 재개 경로를 확인하지 못했습니다")
			}
			*path = absolute
		}
	}
	if o.Input != "" {
		if _, err := readInstallInput(o.Input); err != nil {
			return err
		}
	}
	info, err := os.Lstat(executable)
	if err != nil || !info.Mode().IsRegular() {
		return errors.New("자동 재개할 설치 실행 파일을 확인하지 못했습니다")
	}
	if err = platform.SecureDir(dir); err != nil {
		return err
	}

	lock, err := platform.AcquireLock(filepath.Join(dir, "resume.lock"))
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, lock.Close()) }()
	if err = ctx.Err(); err != nil {
		return err
	}
	statePath := filepath.Join(dir, resumeName(o.Path)+".json")
	command, err := resumeCommand(executable, statePath)
	if err != nil {
		return err
	}
	name := resumeName(o.Path)
	priorCommand, err := registry.Get(name)
	if err != nil {
		return errors.New("기존 자동 재개 등록을 확인하지 못했습니다")
	}
	if priorCommand != "" && priorCommand != command {
		return errors.New("다른 자동 재개 등록이 있어 변경하지 않습니다")
	}
	stateExists := false
	if _, statErr := os.Lstat(statePath); statErr == nil {
		prior, readErr := readWizardResumeState(statePath, name)
		if readErr != nil || !reflect.DeepEqual(prior.Options, o) {
			return errors.New("기존 자동 재개 상태가 다르거나 유효하지 않아 보존합니다")
		}
		stateExists = true
	} else if !os.IsNotExist(statErr) {
		return statErr
	}
	if priorCommand == command {
		if !stateExists {
			return errors.New("기존 자동 재개 등록의 상태 파일이 없어 변경하지 않습니다")
		}
		return ctx.Err()
	}
	stateCreated, registrationAttempted := false, false
	defer func() {
		err = errors.Join(err, ctx.Err())
		if err == nil {
			return
		}
		if registrationAttempted {
			current, readErr := registry.Get(name)
			err = errors.Join(err, readErr)
			if readErr == nil && current == command {
				err = errors.Join(err, registry.Delete(name))
			}
		}
		if stateCreated {
			if removeErr := os.Remove(statePath); removeErr != nil && !os.IsNotExist(removeErr) {
				err = errors.Join(err, removeErr)
			}
		}
	}()
	if !stateExists {
		payload, marshalErr := json.Marshal(wizardResumeState{Schema: 1, Options: o})
		if marshalErr != nil {
			return marshalErr
		}
		if len(payload) > 32768 {
			return errors.New("자동 재개할 설치 옵션이 너무 큽니다. 재부팅 후 설치 프로그램을 다시 실행해 주세요")
		}
		file, openErr := os.OpenFile(statePath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if openErr != nil {
			return openErr
		}
		stateCreated = true
		writeErr := platform.SecureFile(statePath)
		if writeErr == nil {
			_, writeErr = file.Write(payload)
		}
		if writeErr == nil {
			writeErr = file.Sync()
		}
		if closeErr := file.Close(); writeErr != nil || closeErr != nil {
			return errors.Join(writeErr, closeErr)
		}
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	registrationAttempted = true
	if err = registry.Set(name, command); err != nil {
		return errors.New("로그인 후 자동 재개를 등록하지 못했습니다. 재부팅 후 설치 프로그램을 다시 실행해 주세요")
	}
	return ctx.Err()
}

func consumeWizardResume(args []string) ([]string, bool, error) {
	if len(args) == 0 || args[0] != "--resume-setup" {
		return nil, false, nil
	}
	if len(args) != 2 {
		return nil, true, errors.New("자동 재개 명령이 잘못되었습니다")
	}
	registry, err := newWizardResumeRegistry()
	if err != nil {
		return nil, true, err
	}
	dir, err := resumeDirectory()
	if err != nil {
		return nil, true, err
	}
	next, err := consumeWizardResumeWith(args[1], dir, registry)
	return next, true, err
}
func consumeWizardResumeWith(statePath, dir string, registry wizardResumeRegistry) (args []string, err error) {
	if filepath.Dir(statePath) != dir || !strings.HasPrefix(filepath.Base(statePath), "RailwaitSetup-") || filepath.Ext(statePath) != ".json" {
		return nil, errors.New("자동 재개 상태 경로가 잘못되었습니다")
	}
	name := strings.TrimSuffix(filepath.Base(statePath), ".json")
	suffix := strings.TrimPrefix(name, "RailwaitSetup-")
	decoded, decodeErr := hex.DecodeString(suffix)
	if decodeErr != nil || len(decoded) != 8 {
		return nil, errors.New("자동 재개 식별자가 잘못되었습니다")
	}
	info, statErr := os.Lstat(dir)
	if statErr != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("자동 재개 폴더가 올바르지 않습니다")
	}
	lock, err := platform.AcquireLock(filepath.Join(dir, "resume.lock"))
	if err != nil {
		return nil, err
	}
	defer func() { err = errors.Join(err, lock.Close()) }()
	executable, err := os.Executable()
	if err != nil {
		return nil, err
	}
	expected, err := resumeCommand(executable, statePath)
	if err != nil {
		return nil, err
	}
	registered, err := registry.Get(name)
	if err != nil {
		return nil, errors.New("자동 재개 등록을 확인하지 못했습니다")
	}
	if registered != "" && registered != expected {
		return nil, errors.New("다른 자동 재개 등록을 변경하지 않습니다")
	}
	// Consume only the exact action that invoked this executable. A corrupt
	// state file stays intact for recovery without creating a login error loop.
	if registered == expected {
		if err = registry.Delete(name); err != nil {
			return nil, errors.New("자동 재개 등록을 제거하지 못했습니다")
		}
	}
	state, err := readWizardResumeState(statePath, name)
	if err != nil {
		return nil, err
	}
	if err = os.Remove(statePath); err != nil {
		return nil, err
	}
	if state.Options.Input != "" {
		if _, err = readInstallInput(state.Options.Input); err != nil {
			return nil, err
		}
	}
	return wizardResumeArgs(state.Options), nil
}

func readWizardResumeState(path, name string) (wizardResumeState, error) {
	var state wizardResumeState
	info, err := os.Lstat(path)
	if err != nil {
		return state, err
	}
	if !info.Mode().IsRegular() || info.Size() > 32768 {
		return state, errors.New("자동 재개 상태 파일이 잘못되었습니다")
	}
	file, err := os.Open(path)
	if err != nil {
		return state, err
	}
	decoder := json.NewDecoder(io.LimitReader(file, 32769))
	decoder.DisallowUnknownFields()
	parseErr := decoder.Decode(&state)
	if parseErr == nil && decoder.Decode(new(any)) != io.EOF {
		parseErr = errors.New("trailing data")
	}
	if err = file.Close(); err != nil {
		return state, err
	}
	if parseErr != nil || state.Schema != 1 || resumeName(state.Options.Path) != name || state.Options.Input == "-" {
		return state, errors.New("자동 재개 상태가 유효하지 않습니다")
	}
	normalized, err := parseWizardOptions(wizardResumeArgs(state.Options)[1:])
	if err != nil || !reflect.DeepEqual(normalized, state.Options) {
		return state, fmt.Errorf("저장된 설치 옵션이 잘못되었습니다")
	}
	return state, nil
}
