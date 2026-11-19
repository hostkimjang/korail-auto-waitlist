package install

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"testing"
)

func TestDockerFailureCauseRecognizesFixableFailures(t *testing.T) {
	for _, test := range []struct {
		output string
		want   DockerCause
	}{
		{" Network railwait-78794274_data  Creating\nfailed to create network railwait-78794274_data: Error response from daemon: all predefined address pools have been fully subnetted\n", DockerAddressPoolsExhausted},
		{"Error response from daemon: could not find an available, non-overlapping IPv4 address pool among the defaults to assign to the network", DockerAddressPoolsExhausted},
		{"Error response from daemon: driver failed programming external connectivity on endpoint x: Bind for 127.0.0.1:8080 failed: port is already allocated", DockerPortInUse},
		{"Error response from daemon: Ports are not available: exposing port TCP 127.0.0.1:8080 -> 127.0.0.1:0: listen tcp 127.0.0.1:8080: bind: Only one usage of each socket address (protocol/network address/port) is normally permitted.", DockerPortInUse},
		{"listen tcp 127.0.0.1:8080: bind: address already in use", DockerPortInUse},
		{"failed to register layer: write /usr/lib/x: no space left on device", DockerDiskFull},
		{"Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?", DockerUnreachable},
		{"error during connect: Get \"http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.47/containers/json\": open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.", DockerUnreachable},
		{"service \"api\" refers to undefined volume data: invalid compose project", ""},
		{"", ""},
	} {
		if got := dockerFailureCause([]byte(test.output)); got != test.want {
			t.Errorf("%q: got %q, want %q", test.output, got, test.want)
		}
	}
}

func TestTailBufferKeepsTheEndOfLongOutputAndWipes(t *testing.T) {
	tail := &tailBuffer{limit: 80}
	for range 20 {
		fmt.Fprintf(tail, "Pulling %s\n", strings.Repeat("x", 30))
	}
	fmt.Fprint(tail, "all predefined address pools have been fully subnetted\n")
	if len(tail.data) != 80 || dockerFailureCause(tail.data) != DockerAddressPoolsExhausted {
		t.Fatalf("kept %d bytes: %q", len(tail.data), tail.data)
	}
	kept := tail.data[:cap(tail.data)]
	tail.wipe()
	if len(tail.data) != 0 || strings.Trim(string(kept), "\x00") != "" {
		t.Fatal("wipe left Docker output in memory")
	}
}

// dockerStderr is what TestDockerHelperProcess prints before failing like Docker.
const dockerStderr = "RAILWAIT_TEST_DOCKER_STDERR"

// TestDockerHelperProcess is not a test of its own. Started by
// TestDockerCommandFailureKeepsOnlyTheRecognizedCause, it fails the way a Docker command does.
func TestDockerHelperProcess(t *testing.T) {
	message := os.Getenv(dockerStderr)
	if message == "" {
		return
	}
	fmt.Fprintln(os.Stderr, message)
	os.Exit(1)
}

func TestDockerCommandFailureKeepsOnlyTheRecognizedCause(t *testing.T) {
	const secret = "postgresql://railwait:hunter2-not-for-output@postgres/railwait"
	failing := func(message string) *exec.Cmd {
		cmd := exec.Command(os.Args[0], "-test.run=^TestDockerHelperProcess$", "-test.count=1")
		cmd.Env = append(os.Environ(), dockerStderr+"="+message)
		return cmd
	}
	_, recognized := runDockerCommand(failing("DATABASE_URL=" + secret + "\nfailed to create network railwait-1_data: all predefined address pools have been fully subnetted"))
	var failure *DockerFailure
	if !errors.As(recognized, &failure) || failure.Cause != DockerAddressPoolsExhausted {
		t.Fatalf("not recognized: %v", recognized)
	}
	if !strings.Contains(recognized.Error(), "default-address-pools") || !strings.Contains(recognized.Error(), "Docker command failed") {
		t.Fatalf("explanation: %v", recognized)
	}
	_, unknown := runDockerCommand(failing("unexpected failure with DATABASE_URL=" + secret))
	if unknown == nil || errors.As(unknown, &failure) {
		t.Fatalf("an unknown failure must stay unclassified: %v", unknown)
	}
	for _, err := range []error{recognized, unknown} {
		for _, raw := range []string{"hunter2", "DATABASE_URL", "subnetted"} {
			if strings.Contains(err.Error(), raw) {
				t.Fatalf("raw Docker output reached the error: %v", err)
			}
		}
	}
}
