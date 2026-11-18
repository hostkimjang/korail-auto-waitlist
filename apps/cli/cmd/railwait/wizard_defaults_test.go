package main

import "testing"

func TestSimpleInstallationDefaultsDoNotChangeExistingOrAutomatedSetups(t *testing.T) {
	for _, tc := range []struct {
		name        string
		args        []string
		interactive bool
		existing    bool
		want        bool
	}{
		{name: "new interactive install", interactive: true, want: true},
		{name: "yes keeps all-in-one defaults", args: []string{"--yes"}, interactive: true, want: true},
		{name: "custom retains manual setup", args: []string{"--custom"}, interactive: true},
		{name: "existing local installation", interactive: true, existing: true},
		{name: "noninteractive automation", args: []string{"--input", "-"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			o, err := parseWizardOptions(tc.args)
			if err != nil {
				t.Fatal(err)
			}
			if got := useAllInOneDefaults(o, tc.interactive, tc.existing); got != tc.want {
				t.Fatalf("all-in-one defaults = %t, want %t", got, tc.want)
			}
		})
	}
}
