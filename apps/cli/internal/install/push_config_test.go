package install

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"encoding/base64"
	"encoding/pem"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestInitialPushGenerationAndPreservation(t *testing.T) {
	data, err := InitialEnv("WEBPUSH_VAPID_SUBJECT=mailto:admin@example.invalid\n", nil, "railwait-fixture", 8080, "")
	if err != nil {
		t.Fatal(err)
	}
	values, _ := ParseEnv(string(data))
	o := PushOptions{values[pushKeys[0]], values[pushKeys[1]], values[pushKeys[2]]}
	if err := ValidatePushOptions(o); err != nil {
		t.Fatal(err)
	}
	if o.PublicKey == "" || o.PrivateKey == "" || strings.Contains(o.Subject, "example.invalid") {
		t.Fatal("push defaults missing")
	}
	again, err := InitialEnv("", values, "railwait-fixture", 8080, "")
	if err != nil {
		t.Fatal(err)
	}
	next, _ := ParseEnv(string(again))
	for _, key := range pushKeys {
		if next[key] != values[key] {
			t.Fatal("key rotated on resume")
		}
	}
	manual, err := InitialEnvWithPush("", nil, "railwait-fixture", 8080, "", o)
	if err != nil {
		t.Fatal(err)
	}
	supplied, _ := ParseEnv(string(manual))
	if supplied[pushKeys[0]] != o.PublicKey {
		t.Fatal("manual key ignored")
	}
}
func TestPushValidationRejectsPartialMismatchedAndInvalidSubjects(t *testing.T) {
	data, _ := PushEnvironment(nil, PushOptions{})
	values, _ := ParseEnv(string(data))
	for _, o := range []PushOptions{{PublicKey: values[pushKeys[0]]}, {PrivateKey: values[pushKeys[1]]}, {PublicKey: values[pushKeys[0]], PrivateKey: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}, {Subject: "http://example.com"}, {Subject: "mailto:not-an-address"}, {Subject: "https://user:secret@example.com"}, {Subject: "https://example.com\nINJECT=x"}} {
		if ValidatePushOptions(o) == nil {
			t.Fatal("invalid options accepted")
		}
	}
}
func TestPushCandidatePreservesExistingKeysAndSettingBoundary(t *testing.T) {
	before := []byte("POSTGRES_PASSWORD='fixture-secret'\nTAGO_SERVICE_KEY='external-key'\n")
	after, err := PushEnvironment(before, PushOptions{Subject: "https://fixture.tail12345.ts.net"})
	if err != nil {
		t.Fatal(err)
	}
	if err := validatePushCandidate(before, after); err != nil {
		t.Fatal(err)
	}
	if validatePushCandidate(before, []byte(strings.Replace(string(after), "fixture-secret", "changed", 1))) == nil {
		t.Fatal("expanded boundary")
	}
	next, err := PushEnvironment(after, PushOptions{Subject: "mailto:owner@example.org"})
	if err != nil {
		t.Fatal(err)
	}
	if err := validatePushCandidate(after, next); err != nil {
		t.Fatal(err)
	}
	other, _ := PushEnvironment(nil, PushOptions{})
	o, _ := ParseEnv(string(other))
	if _, err := PushEnvironment(after, PushOptions{PublicKey: o[pushKeys[0]], PrivateKey: o[pushKeys[1]]}); err == nil {
		t.Fatal("installed keys rotated")
	}
	partial, _ := ParseEnv(string(after))
	delete(partial, pushKeys[1])
	if err := configurePushValues(map[string]string{}, partial, PushOptions{}); err == nil {
		t.Fatal("partial existing pair regenerated")
	}
}
func TestPushConfigureBacksUpDrainsAndCleansCandidate(t *testing.T) {
	i, runner, _, state := updateFixture(t)
	before, err := os.ReadFile(filepath.Join(state.Path, ".env"))
	if err != nil {
		t.Fatal(err)
	}
	plan, err := i.PlanConfigurePush(context.Background(), state.Path, PushOptions{Subject: "https://fixture.tail12345.ts.net"})
	if err != nil || !plan.Allowed {
		t.Fatal(plan, err)
	}
	result, err := i.Update(context.Background(), UpdateOptions{Path: state.Path, TargetReleaseID: state.Release, PlanID: plan.PlanID})
	if err != nil || result.State != "succeeded" {
		t.Fatal(result, err)
	}
	after, err := os.ReadFile(filepath.Join(state.Path, ".env"))
	if err != nil {
		t.Fatal(err)
	}
	if err := validatePushCandidate(before, after); err != nil {
		t.Fatal(err)
	}
	if len(runner.inputs) != 1 || runner.inputs[0] != string(before) {
		t.Fatal("original settings not backed up")
	}
	if _, err := os.Stat(filepath.Join(state.Path, ".railwait", "plans", plan.PlanID+".candidate.env")); !os.IsNotExist(err) {
		t.Fatal("candidate retained")
	}
	if _, err := i.PlanConfigurePush(context.Background(), state.Path, PushOptions{Subject: "https://fixture.tail12345.ts.net"}); err == nil {
		t.Fatal("unnecessary restart planned")
	}
}

func TestInitialSecretsManualValidationAndPreservation(t *testing.T) {
	supplied := map[string]string{}
	for _, key := range InitialSecretNames() {
		supplied[key] = strings.Repeat("A", 32)
	}
	data, err := InitialEnvWithSetup("", nil, "railwait-fixture", 8080, "", supplied, PushOptions{})
	if err != nil {
		t.Fatal(err)
	}
	values, _ := ParseEnv(string(data))
	for key, value := range supplied {
		if values[key] != value {
			t.Fatal("manual secret ignored")
		}
	}
	if _, err := InitialEnvWithSetup("", values, "railwait-fixture", 8080, "", map[string]string{"AUTH_SESSION_SECRET": strings.Repeat("B", 32)}, PushOptions{}); err == nil {
		t.Fatal("existing secret rotated")
	}
	for _, invalid := range []map[string]string{{"TAGO_SERVICE_KEY": strings.Repeat("A", 32)}, {"AUTH_SESSION_SECRET": "short"}, {"POSTGRES_PASSWORD": strings.Repeat("A", 32) + "'"}} {
		if ValidateInitialSecrets(invalid) == nil {
			t.Fatal("invalid secret accepted")
		}
	}
	names := InitialSecretNames()
	names[0] = "modified"
	if InitialSecretNames()[0] == "modified" {
		t.Fatal("secret whitelist mutated")
	}
}
func TestPushConfiguredStates(t *testing.T) {
	if ok, err := PushConfigured(nil); ok || err != nil {
		t.Fatal(ok, err)
	}
	if _, err := PushConfigured([]byte("WEBPUSH_VAPID_PUBLIC_KEY='partial'\n")); err == nil {
		t.Fatal("partial pair accepted")
	}
	data, err := PushEnvironment(nil, PushOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if ok, err := PushConfigured(data); !ok || err != nil {
		t.Fatal(ok, err)
	}
}

func TestExistingPushDERAndPEMPreserveRepresentation(t *testing.T) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	publicKey, err := key.ECDH()
	if err != nil {
		t.Fatal(err)
	}
	public := base64.RawURLEncoding.EncodeToString(publicKey.PublicKey().Bytes())
	pkcs8, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	sec1, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	for _, der := range [][]byte{pkcs8, sec1} {
		for _, private := range []string{base64.RawURLEncoding.EncodeToString(der), strings.ReplaceAll(string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})), "\n", `\n`)} {
			before := []byte("WEBPUSH_VAPID_PUBLIC_KEY='" + public + "'\nWEBPUSH_VAPID_PRIVATE_KEY='" + private + "'\nWEBPUSH_VAPID_SUBJECT='" + "https://fixture.tail12345.ts.net" + "'\n")
			if ok, err := PushConfigured(before); !ok || err != nil {
				t.Fatal("existing key rejected", err)
			}
			after, err := PushEnvironment(before, PushOptions{Subject: "https://example.org"})
			if err != nil {
				t.Fatal(err)
			}
			values, _ := ParseEnv(string(after))
			if values[pushKeys[1]] != private {
				t.Fatal("existing key representation changed")
			}
			if err := validatePushCandidate(before, after); err != nil {
				t.Fatal(err)
			}
			initial, err := InitialEnv("", values, "railwait-existing", 8080, "")
			if err != nil {
				t.Fatal(err)
			}
			reused, _ := ParseEnv(string(initial))
			if reused[pushKeys[1]] != private {
				t.Fatal("initial environment rotated existing key")
			}
			if ValidatePushOptions(PushOptions{PublicKey: public, PrivateKey: private}) == nil {
				t.Fatal("manual raw contract changed")
			}
			mismatch := strings.Replace(string(before), public, base64.RawURLEncoding.EncodeToString(make([]byte, 65)), 1)
			if _, err := PushConfigured([]byte(mismatch)); err == nil {
				t.Fatal("mismatched existing public key accepted")
			}
		}
	}
	other, err := ecdsa.GenerateKey(elliptic.P384(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	der, err := x509.MarshalPKCS8PrivateKey(other)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := pushPublicKey(base64.RawURLEncoding.EncodeToString(der), true); err == nil {
		t.Fatal("non P-256 existing key accepted")
	}
}

func TestPushSubjectCanonicalizationAndRepairPreservesKeys(t *testing.T) {
	for _, tc := range []struct{ origin, want string }{{"", "https://github.com"}, {"https://macmini-server.tail374278.ts.net:8443/path?q=x", "https://macmini-server.tail374278.ts.net"}, {"https://github.com/hostkimjang/korail-auto-waitlist", "https://github.com"}} {
		got, err := AutoPushSubject(tc.origin)
		if err != nil || got != tc.want {
			t.Fatal("automatic subject rejected", err)
		}
	}
	for _, subject := range []string{"https://macmini-server.tail374278.ts.net:8443", "https://github.com/hostkimjang/korail-auto-waitlist", "https://localhost", "https://github.com/", "https://github.com?x=y", "https://github.com#"} {
		if ValidatePushOptions(PushOptions{Subject: subject}) == nil {
			t.Fatal("unsupported subject accepted")
		}
	}
	for _, origin := range []string{"http://example.com", "https://user:password@example.com", "https://localhost"} {
		if _, err := AutoPushSubject(origin); err == nil {
			t.Fatal("invalid automatic origin accepted")
		}
	}
	data, err := PushEnvironment(nil, PushOptions{})
	if err != nil {
		t.Fatal(err)
	}
	values, _ := ParseEnv(string(data))
	invalid := []byte(strings.Replace(string(data), "WEBPUSH_VAPID_SUBJECT='https://github.com'", "WEBPUSH_VAPID_SUBJECT='https://macmini-server.tail374278.ts.net:8443'", 1))
	if configured, err := PushConfigured(invalid); configured || err != nil {
		t.Fatal("invalid subject not repairable", err)
	}
	if _, err := PushEnvironment(invalid, PushOptions{}); err == nil {
		t.Fatal("invalid subject silently replaced")
	}
	repaired, err := PushEnvironment(invalid, PushOptions{Subject: "https://macmini-server.tail374278.ts.net"})
	if err != nil {
		t.Fatal(err)
	}
	if err := validatePushCandidate(invalid, repaired); err != nil {
		t.Fatal(err)
	}
	next, _ := ParseEnv(string(repaired))
	for _, key := range pushKeys[:2] {
		if values[key] != next[key] {
			t.Fatal("subject repair rotated key")
		}
	}
	if configured, err := PushConfigured(repaired); !configured || err != nil {
		t.Fatal("repair failed", err)
	}
}
