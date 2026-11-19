package platform

import (
	"errors"
	"time"
)

// ErrNotManagerProcess means the process a manager heartbeat names is not that manager: it
// runs another executable or was created at another time, for example after its process ID
// was reused. Such a process is never ended.
var ErrNotManagerProcess = errors.New("이 설치의 관리 프로세스로 확인되지 않았습니다")

// managerStartWindow bounds how long before its recorded start a manager process may have
// been created. The manager records its start right after taking its serve lock, so a
// process created much earlier is another process.
const managerStartWindow = 10 * time.Minute

// managerClockSlack tolerates timer granularity between a process's creation time and the
// start its heartbeat recorded.
const managerClockSlack = 2 * time.Second

// plausibleManagerStart reports whether a process created at created can be the manager
// whose heartbeat recorded startedAt. A process created after that start, such as one that
// reused the ID of a manager that has exited, cannot.
func plausibleManagerStart(created, startedAt time.Time) bool {
	return !created.After(startedAt.Add(managerClockSlack)) && startedAt.Sub(created) <= managerStartWindow
}
