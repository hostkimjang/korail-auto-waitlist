package install

import "strings"

// DockerCause names a Docker failure the installer recognizes and can explain.
type DockerCause string

const (
	// DockerAddressPoolsExhausted: Docker has no address range left for a new network.
	DockerAddressPoolsExhausted DockerCause = "address_pools_exhausted"
	// DockerPortInUse: another program already uses a port the app publishes.
	DockerPortInUse DockerCause = "port_in_use"
	// DockerDiskFull: Docker's storage is full.
	DockerDiskFull DockerCause = "disk_full"
	// DockerUnreachable: the Docker daemon does not answer.
	DockerUnreachable DockerCause = "docker_unreachable"
)

// DockerFailure is a failed Docker command whose cause was recognized. It carries only the
// cause and a fixed explanation: Docker's own output can contain expanded configuration.
type DockerFailure struct {
	Cause DockerCause
	err   error
}

func (f *DockerFailure) Error() string {
	return dockerCauseText[f.Cause] + " (" + f.err.Error() + ")"
}

func (f *DockerFailure) Unwrap() error { return f.err }

// newDockerFailure attaches a recognized cause to err; without one err is returned as is.
func newDockerFailure(cause DockerCause, err error) error {
	if cause == "" {
		return err
	}
	return &DockerFailure{Cause: cause, err: err}
}

var dockerCauseText = map[DockerCause]string{
	DockerAddressPoolsExhausted: "Docker가 새 네트워크에 줄 주소 범위를 모두 써서 앱 네트워크를 만들지 못했습니다. " +
		"docker network ls로 지금 쓰지 않는 프로젝트의 네트워크를 찾아 지우거나(컨테이너가 연결되지 않은 네트워크는 " +
		"docker network prune으로 한꺼번에 지웁니다), Docker 엔진 설정(Docker Desktop은 설정의 Docker Engine)의 " +
		"default-address-pools를 넓힌 뒤 다시 실행하세요",
	DockerPortInUse: "다른 프로그램이 이미 쓰는 포트라 앱 컨테이너를 시작하지 못했습니다. 그 포트를 쓰는 프로그램을 끄거나, " +
		"새 설치라면 --port로 다른 포트를 지정해 다시 실행하세요",
	DockerDiskFull: "Docker의 저장 공간이 부족합니다. docker system df로 사용량을 확인해 쓰지 않는 이미지와 빌드 캐시를 " +
		"지우거나 Docker Desktop의 디스크 크기를 늘린 뒤 다시 실행하세요",
	DockerUnreachable: "Docker에 연결하지 못했습니다. Docker Desktop(또는 Lima·Colima)이 실행 중인지 확인한 뒤 다시 실행하세요",
}

// dockerCauseMarkers are lowercase fragments of Docker and Compose error messages. They are
// fixed texts from Docker itself, never configuration values.
var dockerCauseMarkers = []struct {
	cause   DockerCause
	markers []string
}{
	{DockerAddressPoolsExhausted, []string{"all predefined address pools have been fully subnetted", "could not find an available, non-overlapping ipv4 address pool"}},
	{DockerPortInUse, []string{"port is already allocated", "address already in use", "only one usage of each socket address", "ports are not available"}},
	{DockerDiskFull, []string{"no space left on device"}},
	{DockerUnreachable, []string{"cannot connect to the docker daemon", "error during connect", "is the docker daemon running"}},
}

// dockerFailureCause recognizes a failure in the end of a Docker command's error output.
func dockerFailureCause(output []byte) DockerCause {
	text := strings.ToLower(string(output))
	for _, entry := range dockerCauseMarkers {
		for _, marker := range entry.markers {
			if strings.Contains(text, marker) {
				return entry.cause
			}
		}
	}
	return ""
}

// dockerErrorTail bounds how much of a Docker command's error output is kept in memory.
// Docker prints the failure last, after progress lines such as image pulls.
const dockerErrorTail = 64 << 10

// tailBuffer keeps only the last limit bytes written to it.
type tailBuffer struct {
	data  []byte
	limit int
}

func (b *tailBuffer) Write(p []byte) (int, error) {
	b.data = append(b.data, p...)
	if extra := len(b.data) - b.limit; extra > 0 {
		kept := copy(b.data, b.data[extra:])
		clear(b.data[kept:])
		b.data = b.data[:kept]
	}
	return len(p), nil
}

// wipe clears the kept output, which may hold configuration values.
func (b *tailBuffer) wipe() {
	clear(b.data[:cap(b.data)])
	b.data = b.data[:0]
}
