package install

import (
	"context"
	"crypto/ecdh"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"encoding/base64"
	"encoding/pem"
	"fmt"
	"net/mail"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
)

// PushOptions supplies an optional raw P-256 base64url key pair. Empty keys
// generate a pair only when none exists; installed keys are never rotated here.
type PushOptions struct {
	PublicKey  string `json:"public_key,omitempty"`
	PrivateKey string `json:"private_key,omitempty"`
	Subject    string `json:"subject,omitempty"`
}

var pushKeys = []string{"WEBPUSH_VAPID_PUBLIC_KEY", "WEBPUSH_VAPID_PRIVATE_KEY", "WEBPUSH_VAPID_SUBJECT"}

func ValidatePushOptions(o PushOptions) error { return validatePushOptions(o, false) }

func validatePushOptions(o PushOptions, existing bool) error {
	if (o.PublicKey == "") != (o.PrivateKey == "") {
		return fmt.Errorf("VAPID 공개키와 비밀키를 함께 입력하세요")
	}
	if o.PublicKey != "" {
		expected, err := pushPublicKey(o.PrivateKey, existing)
		if err != nil {
			return fmt.Errorf("VAPID 비밀키는 유효한 P-256 키여야 합니다")
		}

		public, err := base64.RawURLEncoding.DecodeString(o.PublicKey)
		if err != nil && existing {
			public, err = base64.URLEncoding.DecodeString(o.PublicKey)
		}
		if err != nil || !reflect.DeepEqual(public, expected) {
			return fmt.Errorf("VAPID 공개키와 비밀키가 일치하지 않습니다")
		}
	}
	if o.Subject == "" {
		return nil
	}
	if strings.ContainsAny(o.Subject, "'\r\n\x00") {
		return fmt.Errorf("VAPID subject 형식이 잘못되었습니다")
	}
	parsed, err := url.Parse(o.Subject)
	if err != nil {
		return fmt.Errorf("VAPID subject는 포트·경로 없는 HTTPS 호스트 주소 또는 mailto 주소여야 합니다")
	}
	if parsed.Scheme == "https" && validPushHostname(parsed.Hostname()) && parsed.Host == parsed.Hostname() && parsed.User == nil && parsed.Path == "" && parsed.RawQuery == "" && !parsed.ForceQuery && parsed.Fragment == "" && !strings.Contains(o.Subject, "#") {
		return nil
	}
	if parsed.Scheme == "mailto" && parsed.RawQuery == "" && parsed.Fragment == "" {
		address, err := mail.ParseAddress(parsed.Opaque)
		if err == nil && address.Address == parsed.Opaque {
			return nil
		}
	}
	return fmt.Errorf("VAPID subject는 포트·경로 없는 HTTPS 호스트 주소 또는 mailto 주소여야 합니다")
}

func configurePushValues(values, existing map[string]string, o PushOptions) error {
	if err := ValidatePushOptions(o); err != nil {
		return err
	}
	prior := PushOptions{existing[pushKeys[0]], existing[pushKeys[1]], existing[pushKeys[2]]}
	if prior.PublicKey != "" || prior.PrivateKey != "" {
		if err := validatePushOptions(PushOptions{PublicKey: prior.PublicKey, PrivateKey: prior.PrivateKey}, true); err != nil {
			return err
		}
		if o.PublicKey != "" && (o.PublicKey != prior.PublicKey || o.PrivateKey != prior.PrivateKey) {
			return fmt.Errorf("기존 VAPID 키를 교체하지 않습니다")
		}
		o.PublicKey, o.PrivateKey = prior.PublicKey, prior.PrivateKey
	}
	if o.PublicKey == "" {
		key, err := ecdh.P256().GenerateKey(rand.Reader)
		if err != nil {
			return err
		}
		o.PublicKey = base64.RawURLEncoding.EncodeToString(key.PublicKey().Bytes())
		o.PrivateKey = base64.RawURLEncoding.EncodeToString(key.Bytes())
	}
	if o.Subject == "" {
		o.Subject = prior.Subject
	}
	// The push library accepts host-only HTTPS subjects. Remote setup supplies
	// the real hostname; local-only setup uses a public project hosting domain.
	if o.Subject == "" {
		o.Subject = "https://github.com"
	}
	if err := validatePushOptions(o, true); err != nil {
		return err
	}
	values[pushKeys[0]], values[pushKeys[1]], values[pushKeys[2]] = o.PublicKey, o.PrivateKey, o.Subject
	return nil
}

func PushEnvironment(data []byte, o PushOptions) ([]byte, error) {
	values, err := ParseEnv(string(data))
	if err != nil {
		return nil, err
	}
	changed := map[string]string{}
	if err := configurePushValues(changed, values, o); err != nil {
		return nil, err
	}
	lines := strings.Split(string(data), "\n")
	for index, line := range lines {
		key, _, ok := strings.Cut(line, "=")
		key = strings.TrimSpace(key)
		if value, exists := changed[key]; ok && exists {
			lines[index] = key + "='" + value + "'"
			delete(changed, key)
		}
	}
	for _, key := range pushKeys {
		if value, exists := changed[key]; exists {
			lines = append(lines, key+"='"+value+"'")
		}
	}
	return []byte(strings.Join(lines, "\n")), nil
}

func validatePushCandidate(original, candidate []byte) error {
	before, err := ParseEnv(string(original))
	if err != nil {
		return err
	}
	after, err := ParseEnv(string(candidate))
	if err != nil {
		return err
	}
	if after[pushKeys[0]] == "" || after[pushKeys[1]] == "" || after[pushKeys[2]] == "" {
		return fmt.Errorf("VAPID 설정 후보가 누락되었습니다")
	}
	if err := validatePushOptions(PushOptions{after[pushKeys[0]], after[pushKeys[1]], after[pushKeys[2]]}, true); err != nil {
		return err
	}
	for _, key := range pushKeys[:2] {
		if before[key] != "" && before[key] != after[key] {
			return fmt.Errorf("기존 VAPID 키를 교체하지 않습니다")
		}
	}
	for _, key := range pushKeys {
		delete(before, key)
		delete(after, key)
	}
	if !reflect.DeepEqual(before, after) {
		return fmt.Errorf("VAPID 외 설정이 변경된 계획은 적용하지 않습니다")
	}
	return nil
}

func (i *Installer) PlanConfigurePush(ctx context.Context, path string, o PushOptions) (UpdatePlan, error) {
	s, err := ReadState(path)
	if err != nil {
		return UpdatePlan{}, err
	}
	data, err := os.ReadFile(filepath.Join(s.Path, ".env"))
	if err != nil {
		return UpdatePlan{}, err
	}
	candidate, err := PushEnvironment(data, o)
	if err != nil {
		return UpdatePlan{}, err
	}
	if string(candidate) == string(data) {
		return UpdatePlan{}, fmt.Errorf("이미 해당 Web Push 설정을 사용합니다")
	}
	if err := validatePushCandidate(data, candidate); err != nil {
		return UpdatePlan{}, err
	}
	r := installedRelease(s)
	plan, err := i.makePlanMode(ctx, s, r, planConfigure)
	if err != nil {
		return plan, err
	}
	plan.Steps = []string{"checking", "preparing_config", "draining", "backing_up", "applying", "verifying", "resuming"}
	return savePlan(s, plan, r.SHA256, "configure_push", candidate)
}

func PushConfigured(data []byte) (bool, error) {
	values, err := ParseEnv(string(data))
	if err != nil {
		return false, err
	}
	keys := PushOptions{PublicKey: values[pushKeys[0]], PrivateKey: values[pushKeys[1]]}
	if keys.PublicKey == "" && keys.PrivateKey == "" {
		return false, nil
	}
	if err := validatePushOptions(keys, true); err != nil {
		return false, err
	}
	subject := values[pushKeys[2]]
	if subject == "" || ValidatePushOptions(PushOptions{Subject: subject}) != nil {
		return false, nil
	}
	return true, nil
}

var pushHostname = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$`)

func validPushHostname(host string) bool { return len(host) <= 253 && pushHostname.MatchString(host) }

// AutoPushSubject selects a library-compatible server identifier. Unlike the
// browser origin, VAPID's contact subject does not include a listening port.
func AutoPushSubject(origin string) (string, error) {
	if origin == "" {
		return "https://github.com", nil
	}
	parsed, err := url.Parse(origin)
	if err != nil || parsed.Scheme != "https" || parsed.User != nil || !validPushHostname(parsed.Hostname()) || strings.ContainsAny(origin, "'\r\n\x00") {
		return "", fmt.Errorf("알림 발신자 주소에 사용할 HTTPS 호스트가 필요합니다")
	}
	subject := "https://" + strings.ToLower(parsed.Hostname())
	if err := ValidatePushOptions(PushOptions{Subject: subject}); err != nil {
		return "", err
	}
	return subject, nil
}

// Existing installations may store any in-memory P-256 format supported by the
// API. New manual inputs retain the documented single-line raw-key contract.
func pushPublicKey(value string, existing bool) ([]byte, error) {
	decoded, err := base64.RawURLEncoding.DecodeString(value)
	if err != nil && existing {
		decoded, err = base64.URLEncoding.DecodeString(value)
	}
	if err == nil && len(decoded) == 32 {
		key, err := ecdh.P256().NewPrivateKey(decoded)
		if err != nil {
			return nil, err
		}
		return key.PublicKey().Bytes(), nil
	}
	if !existing {
		return nil, fmt.Errorf("raw P-256 key required")
	}
	if strings.HasPrefix(strings.TrimSpace(value), "-----BEGIN") {
		block, rest := pem.Decode([]byte(strings.ReplaceAll(value, `\n`, "\n")))
		if block == nil || len(strings.TrimSpace(string(rest))) != 0 {
			return nil, fmt.Errorf("invalid PEM")
		}
		decoded = block.Bytes
	} else if err != nil {
		return nil, err
	}
	var key *ecdsa.PrivateKey
	parsed, pkcsErr := x509.ParsePKCS8PrivateKey(decoded)
	if pkcsErr == nil {
		key, _ = parsed.(*ecdsa.PrivateKey)
	} else {
		key, err = x509.ParseECPrivateKey(decoded)
		if err != nil {
			return nil, err
		}
	}
	if key == nil || key.Curve != elliptic.P256() {
		return nil, fmt.Errorf("P-256 key required")
	}
	ecdhKey, err := key.ECDH()
	if err != nil {
		return nil, err
	}
	return ecdhKey.PublicKey().Bytes(), nil
}
