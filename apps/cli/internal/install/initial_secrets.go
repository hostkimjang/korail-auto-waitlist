package install

import "fmt"

func InitialSecretNames() []string { return append([]string(nil), secretKeys...) }

// ValidateInitialSecrets limits manually supplied material to generated app
// secrets. External API credentials and arbitrary environment settings are excluded.
func ValidateInitialSecrets(secrets map[string]string) error {
	for key, value := range secrets {
		allowed := false
		for _, name := range secretKeys {
			if key == name {
				allowed = true
				break
			}
		}
		if !allowed {
			return fmt.Errorf("직접 입력할 수 없는 내부 키 이름입니다")
		}
		if len(value) < 32 || len(value) > 1024 {
			return fmt.Errorf("%s는 32~1024자의 URL-safe 문자여야 합니다", key)
		}
		for _, c := range value {
			if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_') {
				return fmt.Errorf("%s에는 영문·숫자·-·_만 사용할 수 있습니다", key)
			}
		}
	}
	return nil
}
