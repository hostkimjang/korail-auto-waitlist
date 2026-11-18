package install

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const accessOrigin = "https://fixture.tail12345.ts.net:8443"

func TestAccessEnvironmentPreservesSecretsAndOnlyEnablesExactHTTPSOrigin(t *testing.T) {
	before := []byte("POSTGRES_PASSWORD='private-fixture'\nTAGO_SERVICE_KEY='key-fixture'\nAUTH_ALLOWED_ORIGINS='http://localhost:8080'\nCORS_ORIGINS='http://localhost:8080'\nAUTH_COOKIE_SECURE='false'\n")
	after, err := AccessEnvironment(before, accessOrigin)
	if err != nil {
		t.Fatal(err)
	}
	if err = validateAccessCandidate(before, after); err != nil {
		t.Fatal(err)
	}
	configured, err := AccessConfigured(after, accessOrigin)
	if err != nil || !configured {
		t.Fatal("not configured", err)
	}
	values, _ := ParseEnv(string(after))
	if values["POSTGRES_PASSWORD"] != "private-fixture" || values["TAGO_SERVICE_KEY"] != "key-fixture" {
		t.Fatal("secrets modified")
	}
	if values["AUTH_ALLOWED_ORIGINS"] != accessOrigin || values["CORS_ORIGINS"] != accessOrigin || values["AUTH_COOKIE_SECURE"] != "true" {
		t.Fatal("HTTPS settings missing")
	}
	for _, mutated := range []string{strings.Replace(string(after), "private-fixture", "changed", 1), strings.Replace(string(after), "AUTH_COOKIE_SECURE='true'", "AUTH_COOKIE_SECURE='false'", 1), strings.Replace(string(after), "CORS_ORIGINS='"+accessOrigin+"'", "CORS_ORIGINS='https://other.tail12345.ts.net'", 1)} {
		if validateAccessCandidate(before, []byte(mutated)) == nil {
			t.Fatal("candidate expanded setting boundary")
		}
	}
}

func TestAccessConfigurationUsesBackupDrainAndPreservesDataSettings(t *testing.T) {
	i, runner, source, state := updateFixture(t)
	before, err := os.ReadFile(filepath.Join(state.Path, ".env"))
	if err != nil {
		t.Fatal(err)
	}
	source.target.Version = "not-used-for-configure"
	plan, err := i.PlanConfigureAccess(context.Background(), state.Path, accessOrigin)
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
	if err = validateAccessCandidate(before, after); err != nil {
		t.Fatal(err)
	}
	if len(runner.inputs) != 1 || runner.inputs[0] != string(before) {
		t.Fatal("old settings not backed up")
	}
	if _, err = os.Stat(filepath.Join(state.Path, ".railwait", "plans", plan.PlanID+".candidate.env")); !os.IsNotExist(err) {
		t.Fatal("temporary candidate retained")
	}
	if _, err = i.PlanConfigureAccess(context.Background(), state.Path, accessOrigin); err == nil {
		t.Fatal("unnecessary restart planned")
	}
}
