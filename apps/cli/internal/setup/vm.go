package setup

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path"
	"strings"
	"time"
)

// DockerVM describes a Docker engine that runs inside a local Lima or Colima VM.
// Containers can bind mount only host folders the VM shares at the same path.
type DockerVM struct {
	Kind    string
	Name    string
	Folders []SharedFolder
}

type SharedFolder struct {
	Path     string
	Writable bool
}

type vmLocation struct{ kind, name, limaHome string }

type limactlRunner func(ctx context.Context, env []string, args ...string) ([]byte, error)

// InspectDockerVM reads the shared folders of the Lima or Colima VM behind a
// Docker endpoint. ok is false for every other engine, such as Docker Desktop.
func InspectDockerVM(ctx context.Context, endpoint string) (DockerVM, bool, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return DockerVM{}, false, err
	}
	return inspectDockerVM(ctx, endpoint, home, runLimactl)
}

func inspectDockerVM(ctx context.Context, endpoint, home string, run limactlRunner) (DockerVM, bool, error) {
	location, ok := dockerVMLocation(endpoint, home)
	if !ok {
		return DockerVM{}, false, nil
	}
	vm := DockerVM{Kind: location.kind, Name: location.name}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	out, err := run(ctx, []string{"LIMA_HOME=" + location.limaHome}, "list", location.name, "--format", "{{json .Config.Mounts}}")
	if err != nil {
		return vm, true, err
	}
	var mounts []struct {
		Location   string  `json:"location"`
		MountPoint *string `json:"mountPoint"`
		Writable   *bool   `json:"writable"`
	}
	if err = json.Unmarshal([]byte(strings.TrimSpace(string(out))), &mounts); err != nil {
		return vm, true, errors.New("VM 공유 폴더 정보를 읽지 못했습니다")
	}
	for _, mount := range mounts {
		folder := expandHome(mount.Location, home)
		if !strings.HasPrefix(folder, "/") {
			continue
		}
		// A different guest path would make Docker resolve the host path to an
		// unrelated folder inside the VM, so such mounts cannot hold the install.
		if mount.MountPoint != nil && *mount.MountPoint != "" && path.Clean(expandHome(*mount.MountPoint, home)) != path.Clean(folder) {
			continue
		}
		vm.Folders = append(vm.Folders, SharedFolder{Path: path.Clean(folder), Writable: mount.Writable != nil && *mount.Writable})
	}
	return vm, true, nil
}

// dockerVMLocation recognizes the socket layouts that Lima
// (<LIMA_HOME>/<instance>/sock/docker.sock) and Colima
// (~/.colima/<profile>/docker.sock) create.
func dockerVMLocation(endpoint, home string) (vmLocation, bool) {
	socket, ok := strings.CutPrefix(endpoint, "unix://")
	if !ok || !strings.HasPrefix(socket, "/") || path.Base(socket) != "docker.sock" {
		return vmLocation{}, false
	}
	socket = path.Clean(socket)
	parent := path.Dir(socket)
	colimaHome := path.Join(home, ".colima")
	switch {
	case parent == colimaHome:
		return vmLocation{"colima", "colima", path.Join(colimaHome, "_lima")}, true
	case path.Dir(parent) == colimaHome:
		profile := path.Base(parent)
		name := "colima"
		if profile != "default" {
			name = "colima-" + profile
		}
		return vmLocation{"colima", name, path.Join(colimaHome, "_lima")}, true
	case path.Base(parent) == "sock":
		instance := path.Dir(parent)
		name := path.Base(instance)
		if name == "" || name == "." || name == "/" || strings.HasPrefix(name, "_") {
			return vmLocation{}, false
		}
		if _, err := os.Stat(path.Join(instance, "lima.yaml")); err != nil {
			return vmLocation{}, false
		}
		return vmLocation{"lima", name, path.Dir(instance)}, true
	}
	return vmLocation{}, false
}

func expandHome(value, home string) string {
	if value == "~" {
		return home
	}
	if rest, ok := strings.CutPrefix(value, "~/"); ok {
		return path.Join(home, rest)
	}
	return value
}

func runLimactl(ctx context.Context, env []string, args ...string) ([]byte, error) {
	executable, err := exec.LookPath("limactl")
	if err != nil {
		for _, candidate := range []string{"/opt/homebrew/bin/limactl", "/usr/local/bin/limactl"} {
			if executableFile(candidate, "darwin") {
				executable, err = candidate, nil
				break
			}
		}
	}
	if err != nil {
		return nil, errors.New("limactl을 찾지 못했습니다")
	}
	cmd := exec.CommandContext(ctx, executable, args...)
	cmd.Env = append(os.Environ(), env...)
	return cmd.Output()
}
