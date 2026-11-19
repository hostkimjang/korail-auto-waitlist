package install

import (
	"context"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

// dockerName matches the volume and image references Docker reports for this project, so a
// name printed in a removal command is never a shell fragment.
var dockerName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}$`)

// RemoveContainers runs docker compose down for this installation's project with every
// profile enabled: its containers and networks, and with volumes also its named volumes.
// Without every profile, Compose skips the services that only run on demand and their
// volumes, such as the backups volume that updates write encrypted dumps to. Any volume of
// the project that Compose still left, for example one an earlier release declared, is
// removed by name, so deleting data never reports success while data remains. Images stay.
// The project name is unique to the installation, so --remove-orphans and the label filter
// reach only what this installation created.
func (i *Installer) RemoveContainers(ctx context.Context, s State, volumes bool) error {
	args := []string{"--profile", "*", "down", "--remove-orphans", "--timeout", "120"}
	if volumes {
		args = append(args, "--volumes")
	}
	if _, err := i.compose(ctx, s, nil, args...); err != nil {
		return err
	}
	if !volumes {
		return nil
	}
	left, err := i.ProjectVolumes(ctx, s)
	if err != nil || len(left) == 0 {
		return err
	}
	_, err = i.docker(ctx, s, nil, append([]string{"volume", "rm"}, left...)...)
	return err
}

// RemoveContainerLogs deletes the log folders that containers wrote under each release
// folder. log-init gives them to the containers' users, so on Linux the host user cannot
// delete them; the installation's own log-init image, run as root with only the releases
// folder mounted and no network, removes them before the installation folder is deleted.
func (i *Installer) RemoveContainerLogs(ctx context.Context, s State) error {
	image := s.ExpectedImages["log-init"]
	if image == "" {
		return nil
	}
	releases := filepath.Join(s.Path, "releases")
	_, err := i.docker(ctx, s, nil, "run", "--rm", "--pull", "never", "--network", "none", "--user", "0:0", "--entrypoint", "sh",
		"-v", releases+":/releases", image, "-c", `for logs in /releases/*/logs; do if [ -d "$logs" ]; then rm -rf "$logs"; fi; done`)
	return err
}

// ProjectVolumes lists the Docker volumes of this installation's Compose project.
func (i *Installer) ProjectVolumes(ctx context.Context, s State) ([]string, error) {
	out, err := i.docker(ctx, s, nil, "volume", "ls", "--quiet", "--filter", "label=com.docker.compose.project="+s.Project)
	if err != nil {
		return nil, err
	}
	return dockerNames(strings.Fields(string(out))), nil
}

// ReleaseImages lists the images of the installed release, including the backup tool, so
// they can be removed by hand after uninstall. It falls back to the recorded images when
// Compose cannot read the release.
func (i *Installer) ReleaseImages(ctx context.Context, s State) []string {
	images := []string{}
	if s.ComposeFile != "" {
		if out, err := i.compose(ctx, s, nil, "--profile", "backup", "config", "--images"); err == nil {
			images = strings.Fields(string(out))
		}
	}
	if len(images) == 0 {
		for _, image := range s.ExpectedImages {
			images = append(images, image)
		}
	}
	return dockerNames(images)
}

func dockerNames(values []string) []string {
	seen := map[string]bool{}
	names := []string{}
	for _, value := range values {
		if dockerName.MatchString(value) && !seen[value] {
			seen[value] = true
			names = append(names, value)
		}
	}
	sort.Strings(names)
	return names
}
