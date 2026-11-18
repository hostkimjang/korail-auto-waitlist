package main

import (
	"errors"
	"golang.org/x/sys/windows/registry"
)

const wizardResumeRegistryPath = `Software\Microsoft\Windows\CurrentVersion\Run`

type windowsWizardResumeRegistry struct{}

func newWizardResumeRegistry() (wizardResumeRegistry, error) {
	return windowsWizardResumeRegistry{}, nil
}
func (windowsWizardResumeRegistry) Set(name, command string) error {
	key, _, err := registry.CreateKey(registry.CURRENT_USER, wizardResumeRegistryPath, registry.SET_VALUE)
	if err != nil {
		return err
	}
	defer key.Close()
	return key.SetStringValue(name, command)
}
func (windowsWizardResumeRegistry) Delete(name string) error {
	key, err := registry.OpenKey(registry.CURRENT_USER, wizardResumeRegistryPath, registry.SET_VALUE)
	if errors.Is(err, registry.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	defer key.Close()
	err = key.DeleteValue(name)
	if errors.Is(err, registry.ErrNotExist) {
		return nil
	}
	return err
}

func (windowsWizardResumeRegistry) Get(name string) (string, error) {
	key, err := registry.OpenKey(registry.CURRENT_USER, wizardResumeRegistryPath, registry.QUERY_VALUE)
	if errors.Is(err, registry.ErrNotExist) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	defer key.Close()
	value, _, err := key.GetStringValue(name)
	if errors.Is(err, registry.ErrNotExist) {
		return "", nil
	}
	return value, err
}
