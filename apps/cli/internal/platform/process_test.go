package platform

import (
	"testing"
	"time"
)

func TestPlausibleManagerStartRejectsReusedProcessIDs(t *testing.T) {
	started := time.Date(2026, 11, 19, 12, 0, 0, 0, time.UTC)
	for _, test := range []struct {
		name    string
		created time.Time
		want    bool
	}{
		{"created just before the recorded start", started.Add(-300 * time.Millisecond), true},
		{"slow start after an interrupted job", started.Add(-2 * time.Minute), true},
		{"timer granularity", started.Add(time.Second), true},
		{"created after the manager started, as a reused ID is", started.Add(time.Minute), false},
		{"created long before the manager started", started.Add(-time.Hour), false},
	} {
		if got := plausibleManagerStart(test.created, started); got != test.want {
			t.Errorf("%s: got %v", test.name, got)
		}
	}
}
