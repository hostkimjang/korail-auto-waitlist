package setup

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"testing"
)

func TestDockerVMLocationRecognizesLimaAndColimaSockets(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Lima와 Colima는 Unix 소켓 경로만 사용합니다")
	}
	home := t.TempDir()
	instance := filepath.Join(home, ".lima", "homeserver")
	if err := os.MkdirAll(filepath.Join(instance, "sock"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(instance, "lima.yaml"), []byte("mounts: []\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		endpoint string
		want     vmLocation
		ok       bool
	}{
		{"unix://" + filepath.ToSlash(filepath.Join(instance, "sock", "docker.sock")), vmLocation{"lima", "homeserver", filepath.ToSlash(filepath.Join(home, ".lima"))}, true},
		{"unix://" + filepath.ToSlash(filepath.Join(home, ".colima", "default", "docker.sock")), vmLocation{"colima", "colima", filepath.ToSlash(filepath.Join(home, ".colima", "_lima"))}, true},
		{"unix://" + filepath.ToSlash(filepath.Join(home, ".colima", "work", "docker.sock")), vmLocation{"colima", "colima-work", filepath.ToSlash(filepath.Join(home, ".colima", "_lima"))}, true},
		{"unix://" + filepath.ToSlash(filepath.Join(home, ".colima", "docker.sock")), vmLocation{"colima", "colima", filepath.ToSlash(filepath.Join(home, ".colima", "_lima"))}, true},
		{"unix:///var/run/docker.sock", vmLocation{}, false},
		{"unix://" + filepath.ToSlash(filepath.Join(home, ".docker", "run", "docker.sock")), vmLocation{}, false},
		{"unix://" + filepath.ToSlash(filepath.Join(home, ".lima", "missing", "sock", "docker.sock")), vmLocation{}, false},
		{"npipe:////./pipe/docker_engine", vmLocation{}, false},
	} {
		got, ok := dockerVMLocation(tc.endpoint, filepath.ToSlash(home))
		if ok != tc.ok || got != tc.want {
			t.Fatalf("%s: got %+v %t", tc.endpoint, got, ok)
		}
	}
}

func TestInspectDockerVMKeepsOnlyFoldersSharedAtTheSamePath(t *testing.T) {
	home := "/Users/example"
	var gotEnv, gotArgs []string
	run := func(_ context.Context, env []string, args ...string) ([]byte, error) {
		gotEnv, gotArgs = env, args
		return []byte(`[
			{"location":"~","mountPoint":"~","writable":false},
			{"location":"/Users/example/Library/Application Support/Server/services","mountPoint":"/Users/example/Library/Application Support/Server/services","writable":true},
			{"location":"/Volumes/data","mountPoint":"/mnt/data","writable":true},
			{"location":"/tmp/lima","writable":true}
		]`), nil
	}
	vm, ok, err := inspectDockerVM(context.Background(), "unix:///Users/example/.colima/default/docker.sock", home, run)
	if err != nil || !ok {
		t.Fatalf("inspect failed: %v %t", err, ok)
	}
	if !reflect.DeepEqual(gotEnv, []string{"LIMA_HOME=/Users/example/.colima/_lima"}) || !reflect.DeepEqual(gotArgs, []string{"list", "colima", "--format", "{{json .Config.Mounts}}"}) {
		t.Fatalf("wrong limactl call %v %v", gotEnv, gotArgs)
	}
	want := []SharedFolder{{"/Users/example", false}, {"/Users/example/Library/Application Support/Server/services", true}, {"/tmp/lima", true}}
	if vm.Kind != "colima" || vm.Name != "colima" || !reflect.DeepEqual(vm.Folders, want) {
		t.Fatalf("unexpected VM %+v", vm)
	}
}

func TestInspectDockerVMReportsUnreadableVMWithoutGuessing(t *testing.T) {
	failing := func(context.Context, []string, ...string) ([]byte, error) { return nil, errors.New("limactl failed") }
	if _, ok, err := inspectDockerVM(context.Background(), "unix:///Users/example/.colima/default/docker.sock", "/Users/example", failing); !ok || err == nil {
		t.Fatal("a VM whose folders cannot be read must not look like a plain engine")
	}
	broken := func(context.Context, []string, ...string) ([]byte, error) { return []byte("not json"), nil }
	if _, _, err := inspectDockerVM(context.Background(), "unix:///Users/example/.colima/default/docker.sock", "/Users/example", broken); err == nil {
		t.Fatal("invalid mount output accepted")
	}
	if _, ok, err := inspectDockerVM(context.Background(), "unix:///var/run/docker.sock", "/Users/example", failing); ok || err != nil {
		t.Fatal("plain Docker must not query limactl")
	}
}
