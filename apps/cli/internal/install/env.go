package install

import (
	"bufio"
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"sort"
	"strconv"
	"strings"
)

var secretKeys = []string{"POSTGRES_PASSWORD", "SECRET_ENCRYPTION_KEY", "AUTH_SESSION_SECRET", "KORAIL_BROWSER_ADAPTER_TOKEN", "SRT_PROVIDER_ADAPTER_TOKEN", "KORAIL_PROVIDER_COOLDOWN_REDIS_PASSWORD"}

// ParseEnv accepts a deliberately small dotenv grammar without executing or expanding values.
func ParseEnv(data string) (map[string]string, error) {
	values := map[string]string{}
	scanner := bufio.NewScanner(strings.NewReader(data))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		key = strings.TrimSpace(key)
		value = strings.TrimSpace(value)
		if !ok || key == "" {
			return nil, fmt.Errorf("invalid environment entry")
		}
		for n, c := range key {
			if !(c == '_' || c >= 'A' && c <= 'Z' || c >= 'a' && c <= 'z' || n > 0 && c >= '0' && c <= '9') {
				return nil, fmt.Errorf("invalid environment key")
			}
		}
		if _, exists := values[key]; exists {
			return nil, fmt.Errorf("duplicate environment key: %s", key)
		}
		if strings.HasPrefix(value, "'") {
			if len(value) < 2 || !strings.HasSuffix(value, "'") {
				return nil, fmt.Errorf("invalid quoted value for %s", key)
			}
			value = value[1 : len(value)-1]
			if strings.Contains(value, "'") {
				return nil, fmt.Errorf("unsupported quote in %s", key)
			}
		} else if strings.ContainsAny(value, "\"'\r\n") {
			return nil, fmt.Errorf("unsupported quoting for %s", key)
		}
		if strings.ContainsRune(value, 0) {
			return nil, fmt.Errorf("invalid value for %s", key)
		}
		values[key] = value
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("read environment: %w", err)
	}
	return values, nil
}

func InitialEnv(template string, existing map[string]string, project string, port int, tago string) ([]byte, error) {
	return InitialEnvWithPush(template, existing, project, port, tago, PushOptions{})
}

func InitialEnvWithPush(template string, existing map[string]string, project string, port int, tago string, push PushOptions) ([]byte, error) {
	return InitialEnvWithSetup(template, existing, project, port, tago, nil, push)
}

func InitialEnvWithSetup(template string, existing map[string]string, project string, port int, tago string, secrets map[string]string, push PushOptions) ([]byte, error) {
	if err := ValidateInitialSecrets(secrets); err != nil {
		return nil, err
	}
	values, err := ParseEnv(template)
	if err != nil {
		return nil, err
	}
	for key, value := range existing {
		values[key] = value
	}
	delete(values, "RAILWAIT_INSTALL_DIR")
	for _, key := range secretKeys {
		if supplied := secrets[key]; supplied != "" {
			if existing[key] != "" && existing[key] != supplied {
				return nil, fmt.Errorf("기존 %s를 교체하지 않습니다", key)
			}
			values[key] = supplied
		} else if existing[key] == "" {
			raw := make([]byte, 48)
			if _, err := rand.Read(raw); err != nil {
				return nil, err
			}
			values[key] = base64.RawURLEncoding.EncodeToString(raw)
		}
	}
	values["COMPOSE_PROJECT_NAME"] = project
	values["CADDY_HTTP_BIND"] = "127.0.0.1:" + strconv.Itoa(port)
	values["CADDY_HTTPS_BIND"] = "127.0.0.1:0"
	values["AUTH_ALLOWED_ORIGINS"] = "http://127.0.0.1:" + strconv.Itoa(port) + ",http://localhost:" + strconv.Itoa(port)
	values["CORS_ORIGINS"] = values["AUTH_ALLOWED_ORIGINS"]
	values["AUTH_COOKIE_SECURE"] = "false"
	values["AUTH_INITIAL_REGISTRATION_ENABLED"] = "false"
	values["SITE_ADDRESS"] = ":80"
	values["TAGO_SERVICE_KEY"] = tago
	for _, key := range []string{"KORAIL_RESERVATION_ONCE_ENABLED", "SRT_RESERVATION_ONCE_ENABLED"} {
		values[key] = "false"
	}
	if err := configurePushValues(values, existing, push); err != nil {
		return nil, err
	}
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	var out strings.Builder
	for _, key := range keys {
		value := values[key]
		if strings.ContainsAny(value, "'\r\n") {
			return nil, fmt.Errorf("unsupported value for %s", key)
		}
		fmt.Fprintf(&out, "%s='%s'\n", key, value)
	}
	return []byte(out.String()), nil
}
