package install

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReservationConfigurationChangesOnlyTheTwoSwitches(t *testing.T) {
	i, runner, _, state := updateFixture(t)
	before, err := os.ReadFile(filepath.Join(state.Path, ".env"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = i.PlanConfigureReservation(context.Background(), state.Path, false); err == nil {
		t.Fatal("planned a change for the default that is already off")
	}
	plan, err := i.PlanConfigureReservation(context.Background(), state.Path, true)
	if err != nil {
		t.Fatal(err)
	}
	if !plan.Allowed {
		t.Fatal(plan.BlockedReasons)
	}
	result, err := i.Update(context.Background(), UpdateOptions{Path: state.Path, TargetReleaseID: state.Release, PlanID: plan.PlanID})
	if err != nil || result.State != "succeeded" {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	after, _ := os.ReadFile(filepath.Join(state.Path, ".env"))
	oldValues, _ := ParseEnv(string(before))
	newValues, _ := ParseEnv(string(after))
	for name, value := range oldValues {
		want := value
		if name == "KORAIL_RESERVATION_ONCE_ENABLED" || name == "SRT_RESERVATION_ONCE_ENABLED" {
			want = "true"
		}
		if newValues[name] != want {
			t.Fatalf("%s=%q, want %q", name, newValues[name], want)
		}
	}
	if len(newValues) != len(oldValues) {
		t.Fatal("added or removed settings")
	}
	if len(runner.inputs) != 1 || runner.inputs[0] != string(before) {
		t.Fatal("old settings were not backed up before replacement")
	}
	if _, err = os.Stat(filepath.Join(state.Path, ".railwait", "plans", plan.PlanID+".candidate.env")); !os.IsNotExist(err) {
		t.Fatal("settings candidate was retained")
	}
	if updated, _ := ReadState(state.Path); updated.Release != state.Release {
		t.Fatal("configuration changed release")
	}
	if _, err = i.PlanConfigureReservation(context.Background(), state.Path, true); err == nil || !strings.Contains(err.Error(), "이미 켜져") {
		t.Fatalf("second enable: %v", err)
	}
}

func TestReservationPlanEnablesNamesThePlannedDirectionOnly(t *testing.T) {
	i, _, _, state := updateFixture(t)
	plan, err := i.PlanConfigureReservation(context.Background(), state.Path, true)
	if err != nil {
		t.Fatal(err)
	}
	if enabled, err := ReservationPlanEnables(state.Path, plan.PlanID); err != nil || !enabled {
		t.Fatalf("enabled=%v err=%v", enabled, err)
	}
	tago, err := i.PlanConfigureTAGO(context.Background(), state.Path, "other-tago-fixture")
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{tago.PlanID, strings.Repeat("0", 64), "../" + plan.PlanID} {
		if _, err = ReservationPlanEnables(state.Path, id); err == nil {
			t.Fatalf("accepted plan %q", id)
		}
	}
}

func TestReservationCandidateRejectsAnyOtherChange(t *testing.T) {
	original := []byte("A='1'\nKORAIL_RESERVATION_ONCE_ENABLED='false'\nSRT_RESERVATION_ONCE_ENABLED='false'\n")
	enabled, err := ReservationEnvironment(original, true)
	if err != nil {
		t.Fatal(err)
	}
	if err = validateReservationCandidate(original, enabled); err != nil {
		t.Fatal(err)
	}
	for _, candidate := range []string{
		"A='2'\nKORAIL_RESERVATION_ONCE_ENABLED='true'\nSRT_RESERVATION_ONCE_ENABLED='true'\n",
		"A='1'\nKORAIL_RESERVATION_ONCE_ENABLED='true'\nSRT_RESERVATION_ONCE_ENABLED='false'\n",
		"A='1'\nKORAIL_RESERVATION_ONCE_ENABLED='yes'\nSRT_RESERVATION_ONCE_ENABLED='yes'\n",
		"A='1'\nB='new'\nKORAIL_RESERVATION_ONCE_ENABLED='true'\nSRT_RESERVATION_ONCE_ENABLED='true'\n",
	} {
		if validateReservationCandidate(original, []byte(candidate)) == nil {
			t.Fatalf("accepted %q", candidate)
		}
	}
}

func TestReservationEnvironmentKeepsOtherLinesAndAddsMissingSwitches(t *testing.T) {
	got, err := ReservationEnvironment([]byte("# note\nA='1'\nKORAIL_RESERVATION_ONCE_ENABLED='false'\n"), true)
	if err != nil {
		t.Fatal(err)
	}
	want := "# note\nA='1'\nKORAIL_RESERVATION_ONCE_ENABLED='true'\nSRT_RESERVATION_ONCE_ENABLED='true'\n"
	if string(got) != want {
		t.Fatalf("got %q", got)
	}
	if _, err = ReservationEnvironment([]byte("A='1'\nA='2'\n"), true); err == nil {
		t.Fatal("accepted a duplicate key")
	}
}

func TestNewInstallTurnsOnAutomaticReservationOnlyWhenChosen(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		root := filepath.Join(t.TempDir(), "installation")
		installer := testInstaller(&fakeRunner{}, &fakeReleases{})
		if _, err := installer.Install(context.Background(), Options{Path: root, AutomaticReservation: enabled, Credentials: func() (Credentials, error) {
			return Credentials{Username: "admin", Password: "fixture-password-value"}, nil
		}}); err != nil {
			t.Fatal(err)
		}
		data, err := os.ReadFile(filepath.Join(root, ".env"))
		if err != nil {
			t.Fatal(err)
		}
		values, err := ParseEnv(string(data))
		if err != nil {
			t.Fatal(err)
		}
		want := reservationSwitchValue(enabled)
		if values["KORAIL_RESERVATION_ONCE_ENABLED"] != want || values["SRT_RESERVATION_ONCE_ENABLED"] != want {
			t.Fatalf("enabled=%v: KORAIL=%q SRT=%q", enabled, values["KORAIL_RESERVATION_ONCE_ENABLED"], values["SRT_RESERVATION_ONCE_ENABLED"])
		}
		if values["KORAIL_RESERVATION_DIALOG_AUTO_ACTION_ENABLED"] == "true" {
			t.Fatal("reservation choice also accepted the delay consent dialog")
		}
	}
}
