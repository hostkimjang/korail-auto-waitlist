package access

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"
)

const testDNS = "fixture.tail12345.ts.net"

type fakeRunner struct {
	config             map[string]any
	status             Status
	commands           [][]string
	changeAfterApply   func(map[string]any)
	serveErr           error
	serveErrAfterApply error
	configErr          error
}

func newRunner() *fakeRunner {
	return &fakeRunner{config: map[string]any{}, status: Status{BackendState: "Running", DNSName: testDNS, Online: true}}
}

func (r *fakeRunner) Run(_ context.Context, args ...string) ([]byte, error) {
	r.commands = append(r.commands, append([]string{}, args...))
	if reflect.DeepEqual(args, []string{"status", "--json"}) {
		return json.Marshal(map[string]any{"BackendState": r.status.BackendState, "Self": map[string]any{"DNSName": r.status.DNSName + ".", "Online": r.status.Online}})
	}
	if reflect.DeepEqual(args, []string{"serve", "status", "--json"}) {
		if r.configErr != nil {
			return nil, r.configErr
		}
		return json.Marshal(r.config)
	}
	if len(args) == 4 && args[0] == "serve" && args[1] == "--bg" && strings.HasPrefix(args[2], "--https=") {
		if r.serveErr != nil {
			return nil, r.serveErr
		}
		port := strings.TrimPrefix(args[2], "--https=")
		if args[3] == "off" {
			delete(object(r.config, "TCP"), port)
			delete(object(r.config, "Web"), r.status.DNSName+":"+port)
		} else {
			if r.config["TCP"] == nil {
				r.config["TCP"] = map[string]any{}
			}
			if r.config["Web"] == nil {
				r.config["Web"] = map[string]any{}
			}
			object(r.config, "TCP")[port] = map[string]any{"HTTPS": true}
			object(r.config, "Web")[r.status.DNSName+":"+port] = map[string]any{"Handlers": map[string]any{"/": map[string]any{"Proxy": args[3]}}}
			if r.changeAfterApply != nil {
				r.changeAfterApply(r.config)
			}
			if r.serveErrAfterApply != nil {
				return nil, r.serveErrAfterApply
			}
		}
		return nil, nil
	}
	return nil, errors.New("unexpected command")
}

func productionConfig() map[string]any {
	return map[string]any{"TCP": map[string]any{"22": map[string]any{"TCPForward": "192.0.2.1:22"}, "443": map[string]any{"HTTPS": true}}, "Web": map[string]any{testDNS + ":443": map[string]any{"Handlers": map[string]any{"/": map[string]any{"Proxy": "http://127.0.0.1:8080"}}}}}
}

func mutations(r *fakeRunner) int {
	n := 0
	for _, args := range r.commands {
		if len(args) > 1 && args[0] == "serve" && args[1] != "status" {
			n++
		}
	}
	return n
}

func TestPlanApplyAndRollbackPreserveExistingHTTPSAndSSH(t *testing.T) {
	r := newRunner()
	r.config = productionConfig()
	c := Client{Runner: r}
	p, err := c.Plan(context.Background(), 18080, 0)
	if err != nil || p.HTTPSPort != 8443 || p.Existing || p.URL != "https://"+testDNS+":8443" {
		t.Fatalf("plan=%+v err=%v", p, err)
	}
	if mutations(r) != 0 {
		t.Fatal("planning mutated tailnet")
	}
	result, err := c.Apply(context.Background(), p)
	if err != nil || !result.Created {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	if !reflect.DeepEqual(withoutRoute(r.config, p), productionConfig()) {
		t.Fatal("existing services changed")
	}
	if err = c.Rollback(context.Background(), result); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(normalized(r.config), productionConfig()) {
		t.Fatal("rollback did not preserve existing services")
	}
	for _, args := range r.commands {
		joined := strings.Join(args, " ")
		if strings.Contains(joined, "reset") || strings.Contains(joined, "funnel") {
			t.Fatal("unsafe operation")
		}
	}
}

func TestExistingMatchingRouteIsReusedAndNeverRemoved(t *testing.T) {
	r := newRunner()
	r.config = productionConfig()
	c := Client{Runner: r}
	p, err := c.Plan(context.Background(), 8080, 443)
	if err != nil || !p.Existing {
		t.Fatal(p, err)
	}
	result, err := c.Apply(context.Background(), p)
	if err != nil || result.Created {
		t.Fatal(result, err)
	}
	if err = c.Rollback(context.Background(), result); err != nil {
		t.Fatal(err)
	}
	if mutations(r) != 0 {
		t.Fatal("existing route was mutated")
	}
}

func TestExplicitCollisionAndFunnelAreRejectedWithoutMutation(t *testing.T) {
	for _, test := range []string{"foreign", "funnel", "nested_funnel", "nested_service"} {
		t.Run(test, func(t *testing.T) {
			r := newRunner()
			r.config = productionConfig()
			port := 443
			switch test {
			case "funnel":
				r.config["AllowFunnel"] = map[string]any{testDNS + ":443": true}
			case "nested_funnel":
				port = 8443
				r.config["Foreground"] = map[string]any{"session": map[string]any{"AllowFunnel": map[string]any{testDNS + ":8443": true}}}
			case "nested_service":
				port = 8443
				r.config["Foreground"] = map[string]any{"session": map[string]any{"TCP": map[string]any{"8443": map[string]any{"HTTPS": true}}}}
			}
			_, err := (Client{Runner: r}).Plan(context.Background(), 18080, port)
			if err == nil || mutations(r) != 0 {
				t.Fatal("unsafe plan accepted")
			}
		})
	}
}

func TestChangedConfigurationOrIdentityBlocksApply(t *testing.T) {
	for _, test := range []string{"config", "dns", "offline", "funnel"} {
		t.Run(test, func(t *testing.T) {
			r := newRunner()
			c := Client{Runner: r}
			p, err := c.Plan(context.Background(), 18080, 0)
			if err != nil {
				t.Fatal(err)
			}
			switch test {
			case "config":
				r.config["TCP"] = map[string]any{"22": map[string]any{"TCPForward": "192.0.2.1:22"}}
			case "dns":
				r.status.DNSName = "other.tail12345.ts.net"
			case "offline":
				r.status.Online = false
			case "funnel":
				r.config["AllowFunnel"] = map[string]any{testDNS + ":443": true}
			}
			if _, err = c.Apply(context.Background(), p); err == nil || mutations(r) != 0 {
				t.Fatal("stale plan applied")
			}
		})
	}
}

func TestConcurrentOutsideChangeIsDetectedAndNotRestored(t *testing.T) {
	r := newRunner()
	r.config = productionConfig()
	c := Client{Runner: r}
	p, err := c.Plan(context.Background(), 18080, 0)
	if err != nil {
		t.Fatal(err)
	}
	r.changeAfterApply = func(config map[string]any) {
		object(config, "TCP")["22"] = map[string]any{"TCPForward": "192.0.2.2:22"}
	}
	result, err := c.Apply(context.Background(), p)
	if err == nil || !result.Created {
		t.Fatal("concurrent change not reported")
	}
	if object(object(r.config, "TCP"), "22")["TCPForward"] != "192.0.2.2:22" {
		t.Fatal("outside change reverted")
	}
	if err = c.Rollback(context.Background(), result); err != nil {
		t.Fatal(err)
	}
	if object(object(r.config, "TCP"), "22")["TCPForward"] != "192.0.2.2:22" {
		t.Fatal("rollback reverted outside change")
	}
}

func TestChangedOwnedRouteCannotBeRemoved(t *testing.T) {
	r := newRunner()
	c := Client{Runner: r}
	p, _ := c.Plan(context.Background(), 18080, 0)
	result, err := c.Apply(context.Background(), p)
	if err != nil {
		t.Fatal(err)
	}
	object(object(object(r.config, "Web"), routeKey(p)), "Handlers")["/other"] = map[string]any{"Proxy": "http://127.0.0.1:9999"}
	before := mutations(r)
	if err = c.Rollback(context.Background(), result); err == nil || mutations(r) != before {
		t.Fatal("shared port removed")
	}
}

func TestInvalidOrNonHTTPSOriginsAreRejected(t *testing.T) {
	for _, origin := range []string{"http://fixture.tail12345.ts.net", "https://example.com", "https://user@fixture.tail12345.ts.net", "https://fixture.tail12345.ts.net/path", "https://fixture.tail12345.ts.net?x=1", "https://fixture.tail12345.ts.net#x", "https://fixture.tail12345.ts.net:", "https://fixture.tail12345.ts.net:99999", "https://fixture.tail12345.ts.net:0443", "https://UPPER.tail12345.ts.net", "https://.ts.net", "https://fixture.tail12345.ts.net\n"} {
		if ValidateOrigin(origin) == nil {
			t.Errorf("accepted %q", origin)
		}
	}
	for _, origin := range []string{"https://fixture.tail12345.ts.net", "https://fixture.tail12345.ts.net:8443"} {
		if err := ValidateOrigin(origin); err != nil {
			t.Error(err)
		}
	}
}

func TestServeFailureDoesNotClaimCreation(t *testing.T) {
	r := newRunner()
	c := Client{Runner: r}
	p, _ := c.Plan(context.Background(), 18080, 0)
	r.serveErr = errors.New("requires HTTPS permission")
	result, err := c.Apply(context.Background(), p)
	if err == nil || result.Created {
		t.Fatal(result, err)
	}
}

func TestInterruptedClientCanReportVerifiedCreationForScopedRollback(t *testing.T) {
	r := newRunner()
	c := Client{Runner: r}
	p, _ := c.Plan(context.Background(), 18080, 0)
	r.serveErrAfterApply = errors.New("client interrupted after daemon commit")
	result, err := c.Apply(context.Background(), p)
	if err == nil || !result.Created || result.RequiresReview {
		t.Fatal("committed route was not identified", result, err)
	}
	if err = c.Rollback(context.Background(), result); err != nil {
		t.Fatal(err)
	}
	if portOccupied(r.config, p.HTTPSPort) {
		t.Fatal("owned route remained")
	}
}

func TestUnobservableInterruptedCreationRequiresReview(t *testing.T) {
	r := newRunner()
	c := Client{Runner: r}
	p, _ := c.Plan(context.Background(), 18080, 0)
	r.serveErrAfterApply = errors.New("client interrupted")
	r.changeAfterApply = func(map[string]any) { r.configErr = errors.New("daemon unavailable") }
	result, err := c.Apply(context.Background(), p)
	if err == nil || result.Created || !result.RequiresReview {
		t.Fatal("unknown result was treated as no change", result, err)
	}
}

func TestMalformedKnownRoutingMapsCannotBeTreatedAsFreePorts(t *testing.T) {
	for _, config := range []map[string]any{
		{"TCP": []any{"443"}},
		{"Web": map[string]any{testDNS + ":443": false}},
		{"AllowFunnel": map[string]any{testDNS + ":443": "false"}},
	} {
		r := newRunner()
		r.config = config
		if _, err := (Client{Runner: r}).Plan(context.Background(), 18080, 0); err == nil || mutations(r) != 0 {
			t.Fatal("malformed routing data was accepted")
		}
	}
}
