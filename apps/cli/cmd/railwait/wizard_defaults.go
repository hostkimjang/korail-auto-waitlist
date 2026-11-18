package main

// Preserve existing installations and explicit automation. Only a new,
// interactive simple installation opts into the all-in-one defaults.
func useAllInOneDefaults(o wizardOptions, interactive, existing bool) bool {
	return interactive && !existing && !o.Custom
}

type prerequisiteRebootRequired struct{}

func (*prerequisiteRebootRequired) Error() string {
	return "Windows 준비를 마치려면 재부팅이 필요합니다"
}
